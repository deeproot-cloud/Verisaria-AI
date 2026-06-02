"""Tests for Memory Service: store, compression, belief engine, relationships."""

from __future__ import annotations

import pytest

from verisaria.engine.schemas import (
    Belief,
    BeliefChange,
    Conviction,
    Memory,
    MemoryLayer,
    Perception,
)
from verisaria.engine.memory import (
    BeliefEngine,
    MemoryCompressor,
    MemoryStore,
    RelationshipCalculator,
    retrieve_memories,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store() -> MemoryStore:
    s = MemoryStore()
    s.clear()
    return s


@pytest.fixture
def belief_engine() -> BeliefEngine:
    be = BeliefEngine()
    be.clear()
    return be


@pytest.fixture
def compressor(store: MemoryStore) -> MemoryCompressor:
    return MemoryCompressor(store)


def make_memory(
    owner_id: str = "npc.guard",
    tick: int = 1,
    layer: MemoryLayer = MemoryLayer.WORKING,
    content: str = "test memory",
    salience: float = 0.5,
    topic_tags: list[str] | None = None,
    memory_id: str = "mem_001",
) -> Memory:
    return Memory(
        memory_id=memory_id,
        owner_id=owner_id,
        source_observation_id=None,
        tick=tick,
        content=content,
        salience=salience,
        decay_rate=0.05,
        layer=layer,
        topic_tags=topic_tags or ["general"],
        last_recalled_tick=None,
        compression_of=None,
    )


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------

class TestMemoryStore:
    def test_add_and_get(self, store: MemoryStore) -> None:
        mem = make_memory()
        store.add("npc.guard", mem)
        assert len(store.get("npc.guard")) == 1
        assert store.get("npc.guard")[0].memory_id == "mem_001"

    def test_get_by_layer(self, store: MemoryStore) -> None:
        store.add("npc.guard", make_memory(layer=MemoryLayer.WORKING, memory_id="m1"))
        store.add("npc.guard", make_memory(layer=MemoryLayer.SHORT_TERM, memory_id="m2"))
        assert len(store.get("npc.guard", MemoryLayer.WORKING)) == 1
        assert store.get("npc.guard", MemoryLayer.WORKING)[0].memory_id == "m1"
        assert len(store.get("npc.guard", MemoryLayer.SHORT_TERM)) == 1

    def test_count(self, store: MemoryStore) -> None:
        store.add("npc.guard", make_memory(memory_id="m1"))
        store.add("npc.guard", make_memory(memory_id="m2"))
        assert store.count("npc.guard") == 2
        assert store.count("npc.other") == 0

    def test_remove(self, store: MemoryStore) -> None:
        store.add("npc.guard", make_memory(memory_id="m1"))
        store.add("npc.guard", make_memory(memory_id="m2"))
        removed = store.remove("npc.guard", {"m1"})
        assert removed == 1
        assert store.count("npc.guard") == 1

    def test_update_last_recalled(self, store: MemoryStore) -> None:
        store.add("npc.guard", make_memory(memory_id="m1"))
        store.update_last_recalled("npc.guard", ["m1"], tick=42)
        assert store.get("npc.guard")[0].last_recalled_tick == 42

    def test_oldest_tick(self, store: MemoryStore) -> None:
        store.add("npc.guard", make_memory(tick=5, memory_id="m1"))
        store.add("npc.guard", make_memory(tick=10, memory_id="m2"))
        assert store.oldest_tick("npc.guard", MemoryLayer.WORKING) == 5

    def test_clear(self, store: MemoryStore) -> None:
        store.add("npc.guard", make_memory(memory_id="m1"))
        store.clear("npc.guard")
        assert store.count("npc.guard") == 0


# ---------------------------------------------------------------------------
# Memory Compression
# ---------------------------------------------------------------------------

class TestMemoryCompression:
    def test_compress_working_to_short(self, compressor: MemoryCompressor) -> None:
        memories = [
            make_memory(
                tick=1, content="看见玩家走向铁匠铺", salience=0.6,
                topic_tags=["player", "movement"], memory_id="m1"
            ),
            make_memory(
                tick=2, content="听见玩家和铁匠交谈", salience=0.5,
                topic_tags=["player", "speech"], memory_id="m2"
            ),
        ]
        result = compressor.compress_working_to_short(memories, current_tick=15)
        assert result.layer == MemoryLayer.SHORT_TERM
        assert result.owner_id == "npc.guard"
        assert result.compression_of == ["m1", "m2"]
        assert result.salience == 0.6
        assert len(result.content) > 0

    def test_compress_short_to_long(self, compressor: MemoryCompressor) -> None:
        memories = [
            make_memory(
                layer=MemoryLayer.SHORT_TERM, tick=50,
                content="玩家多次在夜间前往教堂方向，行为可疑", salience=0.6,
                topic_tags=["player", "suspicious"], memory_id="m3"
            ),
        ]
        result = compressor.compress_short_to_long(memories, current_tick=100)
        assert result.layer == MemoryLayer.LONG_TERM
        assert result.salience == pytest.approx(0.48)  # 0.6 * 0.8

    def test_maybe_compress_working_not_triggered(
        self, store: MemoryStore, compressor: MemoryCompressor
    ) -> None:
        # Only 5 memories, threshold is 10
        for i in range(5):
            store.add("npc.guard", make_memory(tick=i, memory_id=f"m{i}"))
        assert compressor.maybe_compress_working("npc.guard", current_tick=20) is None

    def test_maybe_compress_working_triggered(
        self, store: MemoryStore, compressor: MemoryCompressor
    ) -> None:
        for i in range(12):
            store.add("npc.guard", make_memory(tick=1, memory_id=f"m{i}"))
        result = compressor.maybe_compress_working("npc.guard", current_tick=20)
        assert result is not None
        assert result.layer == MemoryLayer.SHORT_TERM


# ---------------------------------------------------------------------------
# Memory Retrieval
# ---------------------------------------------------------------------------

class TestMemoryRetrieval:
    def test_retrieve_by_tags(self, store: MemoryStore) -> None:
        store.add("npc.guard", make_memory(
            tick=5, topic_tags=["player", "combat"], memory_id="m1", salience=0.8
        ))
        store.add("npc.guard", make_memory(
            tick=6, topic_tags=["social"], memory_id="m2", salience=0.4
        ))
        results = retrieve_memories(
            store, "npc.guard", context_tags=["player"], limit=10, current_tick=10
        )
        assert len(results) == 1
        assert results[0].memory_id == "m1"

    def test_retrieve_long_term_fallback(self, store: MemoryStore) -> None:
        store.add("npc.guard", make_memory(
            layer=MemoryLayer.LONG_TERM, tick=100,
            topic_tags=["old_event"], memory_id="m_old", salience=0.3
        ))
        # Query with unrelated tags → should fallback to recent 3
        results = retrieve_memories(
            store, "npc.guard", context_tags=["unrelated"], limit=10, current_tick=200
        )
        assert len(results) == 1
        assert results[0].memory_id == "m_old"

    def test_retrieve_updates_last_recalled(self, store: MemoryStore) -> None:
        store.add("npc.guard", make_memory(tick=12, memory_id="m1"))
        retrieve_memories(store, "npc.guard", context_tags=["general"], current_tick=20)
        assert store.get("npc.guard")[0].last_recalled_tick == 20


# ---------------------------------------------------------------------------
# Belief Engine
# ---------------------------------------------------------------------------

class TestBeliefEngine:
    def test_create_new_belief(self, belief_engine: BeliefEngine) -> None:
        mem = make_memory(content="玩家给了守卫一笔钱", salience=0.8, memory_id="m1")
        change = belief_engine.update("npc.guard", mem, tick=10)
        assert change.change_type == "created"
        assert change.delta_confidence == pytest.approx(0.4)  # 0.8 * 0.5
        beliefs = belief_engine.get_beliefs("npc.guard")
        assert len(beliefs) == 1
        assert beliefs[0].claim == "玩家给了守卫一笔钱"

    def test_strengthen_existing_belief(self, belief_engine: BeliefEngine) -> None:
        mem1 = make_memory(content="玩家给了守卫一笔钱", salience=0.8, memory_id="m1")
        belief_engine.update("npc.guard", mem1, tick=10)
        mem2 = make_memory(content="玩家再次给守卫钱", salience=0.6, memory_id="m2")
        change = belief_engine.update("npc.guard", mem2, tick=11)
        assert change.change_type == "strengthened"
        assert change.delta_confidence == pytest.approx(0.06)  # 0.6 * 0.1
        beliefs = belief_engine.get_beliefs("npc.guard")
        assert beliefs[0].confidence == pytest.approx(0.46)

    def test_challenge_low_conviction(self, belief_engine: BeliefEngine) -> None:
        mem1 = make_memory(content="玩家是可信的", salience=0.8, memory_id="m1")
        belief_engine.update("npc.guard", mem1, tick=10)
        # Contradicting memory
        mem2 = make_memory(content="玩家不是可信的", salience=0.8, memory_id="m2")
        change = belief_engine.update("npc.guard", mem2, tick=11)
        assert change.change_type == "weakened"
        assert change.delta_confidence < 0
        beliefs = belief_engine.get_beliefs("npc.guard")
        # Low conviction: confidence - (salience * 0.2) = 0.4 - 0.16 = 0.24
        assert beliefs[0].confidence == pytest.approx(0.24)

    def test_challenge_medium_conviction(self, belief_engine: BeliefEngine) -> None:
        mem1 = make_memory(content="玩家是可信的", salience=0.8, memory_id="m1")
        belief_engine.update("npc.guard", mem1, tick=10)
        # Set conviction to medium
        beliefs = belief_engine.get_beliefs("npc.guard")
        beliefs[0].conviction = Conviction.MEDIUM
        mem2 = make_memory(content="玩家不是可信的", salience=0.8, memory_id="m2")
        change = belief_engine.update("npc.guard", mem2, tick=11)
        assert change.change_type == "weakened"
        # Medium: confidence - (salience * 0.1) = 0.4 - 0.08 = 0.32
        assert beliefs[0].confidence == pytest.approx(0.32)
        assert "m2" in beliefs[0].challenged_by

    def test_challenge_high_conviction_no_immediate_change(
        self, belief_engine: BeliefEngine
    ) -> None:
        mem1 = make_memory(content="玩家是可信的", salience=0.8, memory_id="m1")
        belief_engine.update("npc.guard", mem1, tick=10)
        beliefs = belief_engine.get_beliefs("npc.guard")
        beliefs[0].conviction = Conviction.HIGH
        original_conf = beliefs[0].confidence
        mem2 = make_memory(content="玩家不是可信的", salience=0.8, memory_id="m2")
        change = belief_engine.update("npc.guard", mem2, tick=11)
        assert change.change_type == "challenged"
        assert beliefs[0].confidence == original_conf  # No change
        assert "m2" in beliefs[0].challenged_by

    def test_challenge_dogmatic_ignored(self, belief_engine: BeliefEngine) -> None:
        mem1 = make_memory(content="玩家是可信的", salience=0.8, memory_id="m1")
        belief_engine.update("npc.guard", mem1, tick=10)
        beliefs = belief_engine.get_beliefs("npc.guard")
        beliefs[0].conviction = Conviction.DOGMATIC
        original_conf = beliefs[0].confidence
        mem2 = make_memory(content="玩家不是可信的", salience=0.5, memory_id="m2")
        change = belief_engine.update("npc.guard", mem2, tick=11)
        # Salience 0.5 <= 0.9, so ignored
        assert beliefs[0].confidence == original_conf
        assert "m2" not in beliefs[0].challenged_by

    def test_challenge_dogmatic_extreme_salience(
        self, belief_engine: BeliefEngine
    ) -> None:
        mem1 = make_memory(content="玩家是可信的", salience=0.8, memory_id="m1")
        belief_engine.update("npc.guard", mem1, tick=10)
        beliefs = belief_engine.get_beliefs("npc.guard")
        beliefs[0].conviction = Conviction.DOGMATIC
        mem2 = make_memory(content="玩家不是可信的", salience=0.95, memory_id="m2")
        change = belief_engine.update("npc.guard", mem2, tick=11)
        # Salience > 0.9, so challenged but no confidence change
        assert change.change_type == "challenged"
        assert "m2" in beliefs[0].challenged_by

    def test_belief_revoked(self, belief_engine: BeliefEngine) -> None:
        mem1 = make_memory(content="玩家是可信的", salience=0.3, memory_id="m1")
        belief_engine.update("npc.guard", mem1, tick=10)
        mem2 = make_memory(content="玩家不是可信的", salience=1.0, memory_id="m2")
        change = belief_engine.update("npc.guard", mem2, tick=11)
        assert change.change_type == "revoked"
        beliefs = belief_engine.get_beliefs("npc.guard")
        assert beliefs[0].confidence == 0.0


# ---------------------------------------------------------------------------
# Relationship Calculator
# ---------------------------------------------------------------------------

class TestRelationshipCalculator:
    def test_calculate_from_beliefs(self) -> None:
        beliefs = [
            Belief(
                belief_id="b1", owner_id="npc.guard", claim="玩家很可靠",
                confidence=0.8, conviction=Conviction.MEDIUM,
                source_evidence=[], challenged_by=[], would_revise_if=[],
                formed_at_tick=1, last_updated_tick=10,
            ),
            Belief(
                belief_id="b2", owner_id="npc.guard", claim="玩家让人害怕",
                confidence=0.6, conviction=Conviction.LOW,
                source_evidence=[], challenged_by=[], would_revise_if=[],
                formed_at_tick=2, last_updated_tick=10,
            ),
        ]
        snapshot = RelationshipCalculator.calculate(
            "npc.guard", "玩家", beliefs, tick=10
        )
        assert snapshot.npc_id == "npc.guard"
        assert snapshot.target_id == "玩家"
        assert snapshot.dimensions["trust"] > 0
        assert snapshot.dimensions["fear"] > 0
        assert len(snapshot.dominant_beliefs) > 0

    def test_should_recalculate_by_confidence_delta(self) -> None:
        change = BeliefChange(
            owner_id="npc.guard", belief_id="b1", change_type="strengthened",
            delta_confidence=0.3, reason="test", triggering_memory_id="m1", tick=10,
        )
        assert RelationshipCalculator.should_recalculate(change, "玩家") is True

    def test_should_not_recalculate_small_delta(self) -> None:
        change = BeliefChange(
            owner_id="npc.guard", belief_id="b1", change_type="strengthened",
            delta_confidence=0.1, reason="test", triggering_memory_id="m1", tick=10,
        )
        assert RelationshipCalculator.should_recalculate(change, "玩家") is False
