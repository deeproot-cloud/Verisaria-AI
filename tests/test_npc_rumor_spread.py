"""Tests for NPC rumor spreading via memory-driven speech (P0-4)."""

from __future__ import annotations

import pytest

from verisaria.engine.memory import MemoryStore
from verisaria.engine.npc_runtime import NPCActionGenerator
from verisaria.engine.schemas import ActionType, Memory, MemoryLayer
from verisaria.engine.world import EntityState, LocationState, WorldState


@pytest.fixture
def world() -> WorldState:
    ws = WorldState()
    ws.entities["npc_alice"] = EntityState(
        entity_id="npc_alice", entity_type="npc",
        location_id="loc_tavern", hp=100, max_hp=100,
    )
    ws.entities["npc_bob"] = EntityState(
        entity_id="npc_bob", entity_type="npc",
        location_id="loc_tavern", hp=100, max_hp=100,
    )
    ws.locations["loc_tavern"] = LocationState(location_id="loc_tavern")
    return ws


@pytest.fixture
def generator() -> NPCActionGenerator:
    return NPCActionGenerator(seed=42)


class TestRumorSpread:
    def test_npc_with_memory_uses_it_for_speech(self, generator, world):
        store = MemoryStore()
        store.add("npc_alice", Memory(
            memory_id="mem_1",
            owner_id="npc_alice",
            source_observation_id="obs_1",
            tick=0,
            content="有人偷了铁匠的短剑",
            salience=0.9,
            decay_rate=0.05,
            layer=MemoryLayer.WORKING,
            topic_tags=["theft", "blacksmith"],
        ))

        # Force speech by using a seed that produces speech for alice
        for seed in range(50):
            gen = NPCActionGenerator(seed=seed)
            actions = gen.generate_actions(
                world, tick=1, memory_store=store
            )
            alice = next((a for a in actions if a.actor_id == "npc_alice"), None)
            if alice and alice.action_type == ActionType.SPEECH:
                assert "短剑" in alice.params.get("content", "")
                return

        pytest.skip("No speech generated in 50 seeds")

    def test_npc_without_memory_uses_random_chatter(self, generator, world):
        store = MemoryStore()  # empty
        actions = generator.generate_actions(world, tick=1, memory_store=store)
        for a in actions:
            if a.action_type == ActionType.SPEECH:
                content = a.params.get("content", "")
                # Should be one of the random chatter lines
                assert content in generator.CHATTER_LINES
                return
        pytest.skip("No speech generated")

    def test_long_memory_truncated(self, generator, world):
        store = MemoryStore()
        store.add("npc_alice", Memory(
            memory_id="mem_1",
            owner_id="npc_alice",
            source_observation_id="obs_1",
            tick=0,
            content="这是一个非常长的记忆内容，包含了大量关于偷窃事件的详细描述和目击者的证词，还有更多细节需要补充说明",
            salience=0.9,
            decay_rate=0.05,
            layer=MemoryLayer.WORKING,
            topic_tags=["theft"],
        ))

        for seed in range(50):
            gen = NPCActionGenerator(seed=seed)
            actions = gen.generate_actions(world, tick=1, memory_store=store)
            alice = next((a for a in actions if a.actor_id == "npc_alice"), None)
            if alice and alice.action_type == ActionType.SPEECH:
                content = alice.params.get("content", "")
                assert len(content) <= 40
                assert content.endswith("...")
                return

        pytest.skip("No speech generated in 50 seeds")

    def test_memory_store_none_fallback(self, generator, world):
        actions = generator.generate_actions(world, tick=1, memory_store=None)
        for a in actions:
            if a.action_type == ActionType.SPEECH:
                content = a.params.get("content", "")
                assert content in generator.CHATTER_LINES
                return
        pytest.skip("No speech generated")
