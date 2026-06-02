"""Subjectivity Service: Perception → Interpretation → Memory → Belief.

Phase-3 minimal version: rule-based Interpretation generation, no LLM.
NPCs do not get god-view; all interpretations are derived from their own
perception channels and traits.
"""

from __future__ import annotations

from typing import Any

from verisaria.engine.schemas import (
    BeliefChange,
    Interpretation,
    Memory,
    MemoryLayer,
    Observation,
    Perception,
    RelationshipSnapshot,
)
from verisaria.engine.memory import (
    BeliefEngine,
    MemoryStore,
    RelationshipCalculator,
)


# ---------------------------------------------------------------------------
# Interpretation Generator (rule-based)
# ---------------------------------------------------------------------------

class InterpretationGenerator:
    """Generate rule-based Interpretations from Perception.

    No LLM involved — all heuristics based on perception quality and observer traits.
    """

    def generate(
        self,
        perception: Perception,
        observer_traits: list[str] | None = None,
        event_summary: str | None = None,
    ) -> Interpretation:
        """Build an Interpretation from raw Perception."""
        traits = observer_traits or []
        traits_lower = [t.lower() for t in traits]

        # --- Confidence scoring ---
        confidence = 0.5
        if "sight" in perception.channels and "hearing" in perception.channels:
            confidence += 0.3
        elif "sight" in perception.channels:
            confidence += 0.2
        elif "hearing" in perception.channels:
            confidence += 0.1

        if perception.heard_full_content:
            confidence += 0.1
        if perception.attention_level == "focused":
            confidence += 0.1
        if perception.distance == "far":
            confidence -= 0.15

        # Trait modifiers
        if any(t in traits_lower for t in ("paranoid", "多疑", "suspicious")):
            confidence -= 0.1  # More alternatives, less sure
        if any(t in traits_lower for t in ("perceptive", "敏锐", "observant")):
            confidence += 0.1
        if any(t in traits_lower for t in ("gullible", "轻信", "naive")):
            confidence += 0.1  # Overconfident

        confidence = max(0.0, min(1.0, confidence))

        # --- Claim construction ---
        claim = self._build_claim(perception, event_summary)

        # --- Alternatives (uncertainty) ---
        alternatives: list[str] = []
        if confidence < 0.4:
            alternatives = self._generate_alternatives(perception, event_summary)
        elif confidence < 0.7:
            alternatives = self._generate_alternatives(perception, event_summary)[:1]

        # --- Emotional tone ---
        emotional_tone = self._detect_emotional_tone(perception, traits_lower)

        return Interpretation(
            claim=claim,
            confidence=round(confidence, 2),
            alternatives=alternatives,
            emotional_tone=emotional_tone,
        )

    def _build_claim(
        self, perception: Perception, event_summary: str | None
    ) -> str:
        """Construct a factual claim from perception.

        Avoids the redundant "看见：X；听到：X" when sight and hearing carry the
        same summary (P1.3 — memory text should read naturally, not duplicate).
        """
        saw = perception.saw
        heard_full = (
            event_summary if (perception.heard_full_content and event_summary) else None
        )

        parts: list[str] = []
        if saw and heard_full and saw == heard_full:
            # Same content via both channels → state it once.
            parts.append(f"看见并听到：{saw}")
        else:
            if saw:
                parts.append(f"看见：{saw}")
            if heard_full:
                parts.append(f"听到：{heard_full}")
            elif perception.heard_keywords:
                parts.append(f"听到关键词：{', '.join(perception.heard_keywords)}")

        if not parts:
            parts.append("察觉到附近发生了一些事情")

        return "；".join(parts)

    def _generate_alternatives(
        self, perception: Perception, event_summary: str | None
    ) -> list[str]:
        """Generate alternative interpretations when confidence is low."""
        alts = ["可能是普通的日常互动", "也许只是误会"]
        if perception.heard_keywords:
            alts.append(f"可能与{'、'.join(perception.heard_keywords)}有关")
        if not perception.saw:
            alts.append("因为没有看到，无法确定具体情况")
        return alts[:3]

    def _detect_emotional_tone(
        self, perception: Perception, traits_lower: list[str]
    ) -> str | None:
        """Infer emotional tone from perception and traits."""
        if any(t in traits_lower for t in ("paranoid", "多疑", "anxious", "焦虑")):
            return "unease"
        if any(t in traits_lower for t in ("aggressive", "好斗", "hostile")):
            return "hostility_detected"
        if perception.attention_level == "distracted":
            return "indifferent"
        if perception.heard_keywords and any(
            w in " ".join(perception.heard_keywords) for w in ("帮助", "救", "危险", "help", "danger")
        ):
            return "urgency_detected"
        return None


# ---------------------------------------------------------------------------
# Observation → Memory converter
# ---------------------------------------------------------------------------

class MemoryConverter:
    """Convert an Observation (with Interpretation) into a Memory."""

    def __init__(self) -> None:
        self._seq = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"mem_{self._seq}"

    def convert(self, observation: Observation) -> Memory:
        """Turn an Observation into a Working Memory."""
        # Content: use interpretation claim if available, else perception summary
        if observation.interpretation:
            content = observation.interpretation.claim
            salience = observation.interpretation.confidence
        else:
            content = self._perception_to_content(observation.perception)
            salience = 0.3  # Default salience for uninterpreted perception

        # Topic tags derived from content keywords
        topic_tags = self._extract_topic_tags(content)

        return Memory(
            memory_id=self._next_id(),
            owner_id=observation.observer_id,
            source_observation_id=observation.observation_id,
            tick=observation.tick,
            content=content,
            salience=salience,
            decay_rate=0.05,
            layer=MemoryLayer.WORKING,
            topic_tags=topic_tags,
            last_recalled_tick=None,
            compression_of=None,
        )

    def _perception_to_content(self, perception: Perception) -> str:
        parts: list[str] = []
        if perception.saw:
            parts.append(f"看见 {perception.saw}")
        if perception.heard_keywords:
            parts.append(f"听到关键词 {'、'.join(perception.heard_keywords)}")
        if perception.heard_full_content:
            parts.append("听到了完整内容")
        return "；".join(parts) if parts else "察觉到附近发生了一些事"

    def _extract_topic_tags(self, content: str) -> list[str]:
        """Extract topic tags from content (simple keyword matching)."""
        tags: list[str] = []
        content_lower = content.lower()
        keyword_map = {
            "player": ["player", "玩家"],
            "speech": ["说", "说话", "speech", "talk"],
            "movement": ["走", "移动", "move", "离开", "前往"],
            "combat": ["攻击", "战斗", "打", "fight", "attack"],
            "suspicious": ["可疑", "怀疑", "suspicious", "偷偷"],
            "social": ["贿赂", "威胁", "请求", "bribe", "threaten", "ask"],
        }
        for tag, keywords in keyword_map.items():
            if any(kw in content_lower for kw in keywords):
                tags.append(tag)
        if not tags:
            tags.append("general")
        return tags


# ---------------------------------------------------------------------------
# Subjectivity Service (orchestrator)
# ---------------------------------------------------------------------------

class SubjectivityService:
    """Orchestrate the full subjectivity pipeline.

    Observation → Interpretation → Memory → Belief → Relationship Snapshot
    """

    def __init__(
        self,
        memory_store: MemoryStore,
        belief_engine: BeliefEngine,
        relationship_calculator: type[RelationshipCalculator] = RelationshipCalculator,
    ) -> None:
        self.memory_store = memory_store
        self.belief_engine = belief_engine
        self.relationship_calculator = relationship_calculator
        self.interpretation_generator = InterpretationGenerator()
        self.memory_converter = MemoryConverter()

    def process_observation(
        self,
        observation: Observation,
        observer_traits: list[str] | None = None,
    ) -> Observation:
        """Attach an Interpretation to an Observation.

        Only generates interpretation for observers with sufficient perception quality.
        """
        # Skip interpretation for minimal perception
        if observation.detail_level == "minimal":
            return observation

        interpretation = self.interpretation_generator.generate(
            perception=observation.perception,
            observer_traits=observer_traits,
            event_summary=observation.perception.saw,
        )
        # Replace the observation with one that has interpretation
        return Observation(
            observation_id=observation.observation_id,
            observer_id=observation.observer_id,
            source_event_id=observation.source_event_id,
            tick=observation.tick,
            perception=observation.perception,
            interpretation=interpretation,
            detail_level=observation.detail_level,
        )

    def ingest(
        self,
        observation: Observation,
        observer_traits: list[str] | None = None,
    ) -> dict[str, Any]:
        """Full pipeline: Observation → Interpretation → Memory → Belief.

        Returns a dict with the results of each stage.
        """
        # Step 1: Generate interpretation
        obs_with_interp = self.process_observation(observation, observer_traits)

        # Step 2: Convert to Memory
        memory = self.memory_converter.convert(obs_with_interp)
        self.memory_store.add(observation.observer_id, memory)

        # Step 3: Update Beliefs
        belief_change = self.belief_engine.update(
            observation.observer_id, memory, observation.tick
        )

        # Step 4: Check if relationship recalculation is needed
        # We don't know the target here; caller handles relationship updates
        return {
            "observation": obs_with_interp,
            "memory": memory,
            "belief_change": belief_change,
        }

    def update_relationship(
        self,
        npc_id: str,
        target_id: str,
        tick: int,
    ) -> RelationshipSnapshot | None:
        """Recalculate relationship snapshot for a pair."""
        beliefs = self.belief_engine.get_beliefs(npc_id)
        if not beliefs:
            return None
        return self.relationship_calculator.calculate(npc_id, target_id, beliefs, tick)

    def get_state(self) -> dict[str, Any]:
        """Return serializable state from memory and belief engines."""
        return {
            "memory_store": self.memory_store.get_state(),
            "belief_engine": self.belief_engine.get_state(),
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore memory and belief engines from serialized state."""
        self.memory_store.load_state(state.get("memory_store", {}))
        self.belief_engine.load_state(state.get("belief_engine", {}))

    def should_generate_interpretation(self, observation: Observation) -> bool:
        """Determine if this observation merits interpretation generation.

        Rules:
        - Full detail level → yes
        - Partial with sight → yes
        - Partial without sight, only hearing → yes (but low confidence)
        - Minimal → no
        """
        if observation.detail_level == "full":
            return True
        if observation.detail_level == "partial":
            return True
        return False
