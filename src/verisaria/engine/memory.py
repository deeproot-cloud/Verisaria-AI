"""Memory Service: CRUD, layered compression, Belief update, Relationship snapshot.

Phase-3 minimal version: all rule-based, no LLM, in-memory storage.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from verisaria.engine.schemas import (
    Belief,
    BeliefChange,
    Conviction,
    Memory,
    MemoryLayer,
    RelationshipSnapshot,
)


# ---------------------------------------------------------------------------
# Memory Store (in-memory, per-owner partition)
# ---------------------------------------------------------------------------

class MemoryStore:
    """Partitioned in-memory store for Memories."""

    def __init__(self) -> None:
        self._data: dict[str, list[Memory]] = {}

    # -- CRUD --

    def add(self, owner_id: str, memory: Memory) -> None:
        """Append a Memory to the owner's partition."""
        if owner_id not in self._data:
            self._data[owner_id] = []
        self._data[owner_id].append(memory)

    def get(self, owner_id: str, layer: MemoryLayer | None = None) -> list[Memory]:
        """Retrieve memories for an owner, optionally filtered by layer."""
        memories = self._data.get(owner_id, [])
        if layer is None:
            return list(memories)
        return [m for m in memories if m.layer == layer]

    def get_all(self, owner_id: str) -> list[Memory]:
        """Retrieve all memories for an owner."""
        return list(self._data.get(owner_id, []))

    def count(self, owner_id: str, layer: MemoryLayer | None = None) -> int:
        """Count memories for an owner."""
        return len(self.get(owner_id, layer))

    def remove(self, owner_id: str, memory_ids: set[str]) -> int:
        """Remove specific memories by id. Returns number removed."""
        if owner_id not in self._data:
            return 0
        before = len(self._data[owner_id])
        self._data[owner_id] = [m for m in self._data[owner_id] if m.memory_id not in memory_ids]
        return before - len(self._data[owner_id])

    def update_last_recalled(
        self, owner_id: str, memory_ids: list[str], tick: int
    ) -> None:
        """Update last_recalled_tick for given memories."""
        mem_map = {m.memory_id: m for m in self.get_all(owner_id)}
        for mid in memory_ids:
            if mid in mem_map:
                mem = mem_map[mid]
                mem.last_recalled_tick = tick

    def oldest_tick(self, owner_id: str, layer: MemoryLayer) -> int | None:
        """Return the oldest tick in a layer, or None if empty."""
        memories = self.get(owner_id, layer)
        if not memories:
            return None
        return min(m.tick for m in memories)

    def clear(self, owner_id: str | None = None) -> None:
        """Clear all memories or just one owner's."""
        if owner_id is None:
            self._data.clear()
        else:
            self._data.pop(owner_id, None)

    def get_state(self) -> dict[str, Any]:
        """Return serializable state."""
        return {
            owner_id: [m.model_dump() for m in memories]
            for owner_id, memories in self._data.items()
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore from serialized state."""
        self._data = {
            owner_id: [Memory(**m) for m in memories]
            for owner_id, memories in state.items()
        }


# ---------------------------------------------------------------------------
# Memory Compressor (rule-based, no LLM)
# ---------------------------------------------------------------------------

class MemoryCompressor:
    """Compress memories between layers using rule-based summarisation."""

    WORKING_THRESHOLD = 10
    WORKING_AGE_THRESHOLD = 10
    SHORT_TERM_THRESHOLD = 20

    def __init__(self, memory_store: MemoryStore) -> None:
        self._store = memory_store
        self._seq = 0

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}_{self._seq}"

    def maybe_compress_working(self, owner_id: str, current_tick: int) -> Memory | None:
        """Compress working memories if thresholds are met."""
        working = self._store.get(owner_id, MemoryLayer.WORKING)
        if len(working) <= self.WORKING_THRESHOLD:
            return None
        oldest = self._store.oldest_tick(owner_id, MemoryLayer.WORKING)
        if oldest is None or (current_tick - oldest) <= self.WORKING_AGE_THRESHOLD:
            return None
        return self.compress_working_to_short(working, current_tick)

    def maybe_compress_short(self, owner_id: str, current_tick: int) -> Memory | None:
        """Compress short-term memories if threshold is met."""
        short = self._store.get(owner_id, MemoryLayer.SHORT_TERM)
        if len(short) <= self.SHORT_TERM_THRESHOLD:
            return None
        return self.compress_short_to_long(short, current_tick)

    def compress_working_to_short(
        self, memories: list[Memory], current_tick: int
    ) -> Memory:
        """Aggregate working memories into a short-term summary."""
        # 1. Cluster by topic_tags
        clusters: dict[str, list[Memory]] = defaultdict(list)
        for m in memories:
            key_tag = m.topic_tags[0] if m.topic_tags else "general"
            clusters[key_tag].append(m)

        # 2. Pick top-3 salience per cluster
        selected: list[Memory] = []
        for cluster in clusters.values():
            cluster.sort(key=lambda m: m.salience, reverse=True)
            selected.extend(cluster[:3])

        # 3. Generate a rule-based summary
        topics = sorted(set(t for m in selected for t in m.topic_tags))
        summary_parts: list[str] = []
        for m in selected[:5]:
            summary_parts.append(m.content)
        summary = "；".join(summary_parts)
        if len(summary) > 200:
            summary = summary[:197] + "..."

        # 4. Remove compressed memories from store (caller handles actual removal)
        compressed_ids = [m.memory_id for m in memories]
        max_salience = max(m.salience for m in memories)

        return Memory(
            memory_id=self._next_id("mem_short"),
            owner_id=memories[0].owner_id,
            source_observation_id=None,
            tick=current_tick,
            content=summary,
            salience=max_salience,
            decay_rate=0.02,
            layer=MemoryLayer.SHORT_TERM,
            topic_tags=topics,
            last_recalled_tick=None,
            compression_of=compressed_ids,
        )

    def compress_short_to_long(
        self, memories: list[Memory], current_tick: int
    ) -> Memory:
        """Aggregate short-term memories into a long-term summary."""
        topics = sorted(set(t for m in memories for t in m.topic_tags))
        # Keep only value/stereotype-level info: extract first clause of each
        summary_parts: list[str] = []
        for m in memories[:5]:
            first_clause = m.content.split("。")[0].split("；")[0]
            summary_parts.append(first_clause)
        summary = "。".join(summary_parts)
        if len(summary) > 80:
            summary = summary[:77] + "..."

        compressed_ids = [m.memory_id for m in memories]
        max_salience = max(m.salience for m in memories) * 0.8

        return Memory(
            memory_id=self._next_id("mem_long"),
            owner_id=memories[0].owner_id,
            source_observation_id=None,
            tick=current_tick,
            content=summary,
            salience=max_salience,
            decay_rate=0.01,
            layer=MemoryLayer.LONG_TERM,
            topic_tags=topics,
            last_recalled_tick=None,
            compression_of=compressed_ids,
        )


# ---------------------------------------------------------------------------
# Belief Engine
# ---------------------------------------------------------------------------

class BeliefEngine:
    """Manage beliefs: create, update, challenge based on memories."""

    def __init__(self) -> None:
        self._beliefs: dict[str, list[Belief]] = {}
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"belief_{self._seq}"

    def get_beliefs(self, owner_id: str) -> list[Belief]:
        return list(self._beliefs.get(owner_id, []))

    def find_related(self, owner_id: str, memory: Memory) -> list[Belief]:
        """Find beliefs related to the given memory (by tags or content overlap)."""
        beliefs = self.get_beliefs(owner_id)
        tag_set = set(memory.topic_tags)
        content_keywords = set(self._extract_keywords(memory.content))
        related = []
        for b in beliefs:
            # Check 1: topic_tags overlap with would_revise_if
            evidence_tags = set(b.would_revise_if)
            if tag_set & evidence_tags:
                related.append(b)
                continue

            # Check 2: content keyword overlap between belief claim and memory content
            claim_keywords = set(self._extract_keywords(b.claim))
            if claim_keywords & content_keywords:
                related.append(b)
                continue

            # Check 3: topic tags appear in claim
            for tag in memory.topic_tags:
                if tag in b.claim:
                    related.append(b)
                    break
        return related

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """Extract simple keywords from text for matching.

        Supports both space-delimited (English) and character-ngram (Chinese).
        """
        keywords: set[str] = set()
        # Split by common delimiters for space-separated text
        cleaned = (
            text.replace("，", " ")
            .replace("。", " ")
            .replace("；", " ")
            .replace("、", " ")
            .replace("！", " ")
            .replace("？", " ")
        )
        for part in cleaned.split():
            if len(part) >= 2:
                keywords.add(part)
        # Add 2-character n-grams for Chinese/mixed text
        for i in range(len(text) - 1):
            bigram = text[i : i + 2]
            if any(c in " ，。；、！？ \t\n" for c in bigram):
                continue
            keywords.add(bigram)
        return list(keywords)

    def update(self, owner_id: str, memory: Memory, tick: int) -> BeliefChange:
        """Ingest a memory and update/create beliefs."""
        related = self.find_related(owner_id, memory)

        if related:
            # Pick the most confident related belief
            existing = max(related, key=lambda b: b.confidence)
            if self._is_supporting(existing, memory):
                return self._strengthen(existing, memory, tick)
            else:
                return self._challenge(existing, memory, tick)
        else:
            return self._create_new(owner_id, memory, tick)

    def _is_supporting(self, belief: Belief, memory: Memory) -> bool:
        """Heuristic: memory supports belief if content aligns and no contradiction."""
        claim_lower = belief.claim.lower()
        content_lower = memory.content.lower()

        # Check for negation in memory that contradicts the claim
        negation_words = {"不", "没", "无", "非", "没有", "not", "no", "never", "false"}
        has_negation = any(w in content_lower for w in negation_words)

        # Extract shared keywords (character-level for Chinese)
        claim_keywords = set(self._extract_keywords(claim_lower))
        content_keywords = set(self._extract_keywords(content_lower))
        shared_keywords = claim_keywords & content_keywords

        if has_negation and shared_keywords:
            # Negation + shared subject = likely contradiction
            return False

        # Supporting if significant keyword overlap or high salience with same subject
        if len(shared_keywords) >= 2:
            return True
        if memory.salience > 0.5 and any(kw in content_lower for kw in claim_keywords):
            return True
        return False

    def _strengthen(self, belief: Belief, memory: Memory, tick: int) -> BeliefChange:
        delta = memory.salience * 0.1
        old_conf = belief.confidence
        belief.confidence = min(1.0, belief.confidence + delta)
        belief.last_updated_tick = tick
        if memory.memory_id not in belief.source_evidence:
            belief.source_evidence.append(memory.memory_id)
        return BeliefChange(
            owner_id=belief.owner_id,
            belief_id=belief.belief_id,
            change_type="strengthened",
            delta_confidence=belief.confidence - old_conf,
            reason="New memory supports existing belief",
            triggering_memory_id=memory.memory_id,
            tick=tick,
        )

    def _challenge(self, belief: Belief, memory: Memory, tick: int) -> BeliefChange:
        old_conf = belief.confidence
        if belief.conviction == Conviction.LOW:
            delta = memory.salience * 0.2
            belief.confidence = max(0.0, belief.confidence - delta)
        elif belief.conviction == Conviction.MEDIUM:
            delta = memory.salience * 0.1
            belief.confidence = max(0.0, belief.confidence - delta)
            if memory.memory_id not in belief.challenged_by:
                belief.challenged_by.append(memory.memory_id)
        elif belief.conviction == Conviction.HIGH:
            if memory.memory_id not in belief.challenged_by:
                belief.challenged_by.append(memory.memory_id)
            # Confidence does not change immediately
        elif belief.conviction == Conviction.DOGMATIC:
            if memory.salience > 0.9:
                if memory.memory_id not in belief.challenged_by:
                    belief.challenged_by.append(memory.memory_id)
            # Otherwise ignored

        if belief.confidence <= 0:
            belief.confidence = 0.0
            change_type = "revoked"
        elif belief.confidence < old_conf:
            change_type = "weakened"
        else:
            change_type = "challenged"

        belief.last_updated_tick = tick
        return BeliefChange(
            owner_id=belief.owner_id,
            belief_id=belief.belief_id,
            change_type=change_type,
            delta_confidence=belief.confidence - old_conf,
            reason="New memory contradicts existing belief",
            triggering_memory_id=memory.memory_id,
            tick=tick,
        )

    def _create_new(self, owner_id: str, memory: Memory, tick: int) -> BeliefChange:
        belief_id = self._next_id()
        belief = Belief(
            belief_id=belief_id,
            owner_id=owner_id,
            claim=memory.content,
            confidence=min(1.0, memory.salience * 0.5),
            conviction=Conviction.LOW,
            source_evidence=[memory.memory_id],
            challenged_by=[],
            would_revise_if=[],
            formed_at_tick=tick,
            last_updated_tick=tick,
        )
        if owner_id not in self._beliefs:
            self._beliefs[owner_id] = []
        self._beliefs[owner_id].append(belief)
        return BeliefChange(
            owner_id=owner_id,
            belief_id=belief_id,
            change_type="created",
            delta_confidence=belief.confidence,
            reason="New belief formed from memory",
            triggering_memory_id=memory.memory_id,
            tick=tick,
        )

    def clear(self, owner_id: str | None = None) -> None:
        if owner_id is None:
            self._beliefs.clear()
        else:
            self._beliefs.pop(owner_id, None)

    def get_state(self) -> dict[str, Any]:
        """Return serializable state."""
        return {
            owner_id: [b.model_dump() for b in beliefs]
            for owner_id, beliefs in self._beliefs.items()
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore from serialized state."""
        self._beliefs = {
            owner_id: [Belief(**b) for b in beliefs]
            for owner_id, beliefs in state.items()
        }


# ---------------------------------------------------------------------------
# Relationship Calculator
# ---------------------------------------------------------------------------

class RelationshipCalculator:
    """Calculate relationship snapshots from beliefs."""

    DIMENSIONS = ["trust", "suspicion", "fear", "affection", "respect", "familiarity"]

    @classmethod
    def should_recalculate(
        cls, belief_change: BeliefChange, target_id: str
    ) -> bool:
        """Check if a belief change should trigger relationship recalculation."""
        # Condition 1: target_id mentioned in reason or belief-related context
        if target_id in belief_change.reason:
            return True
        # Condition 2: confidence delta > 0.2
        if abs(belief_change.delta_confidence) > 0.2:
            return True
        return False

    @classmethod
    def calculate(
        cls,
        npc_id: str,
        target_id: str,
        beliefs: list[Belief],
        tick: int = 0,
    ) -> RelationshipSnapshot:
        """Compute relationship dimensions from beliefs about a target."""
        # Filter beliefs that mention the target
        target_beliefs = [b for b in beliefs if target_id in b.claim]

        dimensions: dict[str, float] = {}
        for dim in cls.DIMENSIONS:
            dimensions[dim] = 0.0

        # Simple keyword-based dimension extraction
        for b in target_beliefs:
            claim_lower = b.claim.lower()
            conf = b.confidence
            if any(w in claim_lower for w in ("信任", "可靠", "诚实", "trust", "reliable", "honest")):
                dimensions["trust"] = min(1.0, dimensions["trust"] + conf * 0.3)
            if any(w in claim_lower for w in ("可疑", "怀疑", "suspicious", "doubt")):
                dimensions["suspicion"] = min(1.0, dimensions["suspicion"] + conf * 0.3)
            if any(w in claim_lower for w in ("害怕", "恐惧", "威胁", "fear", "threat", "danger")):
                dimensions["fear"] = min(1.0, dimensions["fear"] + conf * 0.3)
            if any(w in claim_lower for w in ("喜欢", "好感", "affection", "like", "love")):
                dimensions["affection"] = min(1.0, dimensions["affection"] + conf * 0.3)
            if any(w in claim_lower for w in ("尊敬", "佩服", "respect", "admire")):
                dimensions["respect"] = min(1.0, dimensions["respect"] + conf * 0.3)
            # Familiarity accumulates with any belief about target
            dimensions["familiarity"] = min(1.0, dimensions["familiarity"] + conf * 0.1)

        dominant = [
            {"dimension": d, "value": v}
            for d, v in sorted(dimensions.items(), key=lambda x: x[1], reverse=True)
            if v > 0
        ][:3]

        return RelationshipSnapshot(
            snapshot_id=f"rel_{npc_id}_{target_id}_{tick}",
            npc_id=npc_id,
            target_id=target_id,
            tick=tick,
            dimensions=dimensions,
            last_interaction_summary=None,
            dominant_beliefs=dominant,
            updated_at_tick=tick,
        )


# ---------------------------------------------------------------------------
# Convenience: retrieve memories with context tags
# ---------------------------------------------------------------------------

def retrieve_memories(
    store: MemoryStore,
    owner_id: str,
    context_tags: list[str],
    layer: MemoryLayer | Literal["all"] = "all",
    limit: int = 10,
    current_tick: int = 0,
) -> list[Memory]:
    """Retrieve memories by layer and context tag overlap.

    - working: tick range (last 10 ticks) + tag intersection
    - short_term: tag intersection + salience sort
    - long_term: tag intersection + salience sort, fallback to recent 3
    """
    tag_set = set(context_tags)
    results: list[Memory] = []

    if layer in (MemoryLayer.WORKING, "all"):
        working = store.get(owner_id, MemoryLayer.WORKING)
        # Filter by tick range AND tag overlap
        filtered = [
            m for m in working
            if (current_tick - m.tick <= 10) and (tag_set & set(m.topic_tags))
        ]
        results.extend(filtered)

    if layer in (MemoryLayer.SHORT_TERM, "all"):
        short = store.get(owner_id, MemoryLayer.SHORT_TERM)
        tagged = [m for m in short if tag_set & set(m.topic_tags)]
        tagged.sort(key=lambda m: m.salience, reverse=True)
        results.extend(tagged)

    if layer in (MemoryLayer.LONG_TERM, "all"):
        long_mem = store.get(owner_id, MemoryLayer.LONG_TERM)
        tagged = [m for m in long_mem if tag_set & set(m.topic_tags)]
        if not tagged:
            # Fallback: recent 3 long-term memories
            tagged = sorted(long_mem, key=lambda m: m.tick, reverse=True)[:3]
        tagged.sort(key=lambda m: m.salience, reverse=True)
        results.extend(tagged)

    # Update last_recalled_tick
    for m in results[:limit]:
        m.last_recalled_tick = current_tick

    return results[:limit]
