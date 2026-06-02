"""Tests for debug/inspection CLI commands (/history, /inspect, /belief, /memory)."""

from __future__ import annotations

import pytest

from verisaria.runtime.session import GameSession
from verisaria.engine.schemas import (
    Action,
    ActionType,
    Event,
    EventType,
    Memory,
    MemoryLayer,
    Conviction,
)


@pytest.fixture
def minimal_pack_path(tmp_path_factory):
    return "fixtures/content_packs/minimal_valid.json"


class TestHistoryCommand:
    def test_history_empty(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/history")
        assert "No events recorded yet" in result

    def test_history_shows_recent_events(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        # Add some events directly
        session.world.event_log.append(
            Event(
                event_id="e1",
                event_type=EventType.PHYSICAL,
                actor_id="player_001",
                tick=0,
                location_id="void",
                summary="player looked around",
            )
        )
        session.world.event_log.append(
            Event(
                event_id="e2",
                event_type=EventType.SPEECH,
                actor_id="npc_001",
                tick=1,
                location_id="void",
                summary="npc said hello",
            )
        )
        result = session._handle_command("/history")
        assert "looked around" in result
        assert "said hello" in result
        assert "Tick" in result

    def test_history_limit(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        for i in range(15):
            session.world.event_log.append(
                Event(
                    event_id=f"e{i}",
                    event_type=EventType.PHYSICAL,
                    actor_id="player_001",
                    tick=i,
                    location_id="void",
                    summary=f"action {i}",
                )
            )
        result = session._handle_command("/history 3")
        assert "action 12" in result
        assert "action 13" in result
        assert "action 14" in result
        assert "action 11" not in result

    def test_history_invalid_arg(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/history abc")
        assert "Usage" in result

    def test_history_zero_arg(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/history 0")
        assert "Usage" in result


class TestInspectCommand:
    def test_inspect_default_player(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/inspect")
        assert "player_001" in result
        assert "Type:" in result
        assert "HP:" in result

    def test_inspect_specific_entity(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        # Add an NPC to the world
        from verisaria.engine.world import EntityState
        session.world.state.entities["npc_001"] = EntityState(
            entity_id="npc_001",
            entity_type="npc",
            location_id="void",
            hp=80,
            max_hp=100,
            traits=["friendly"],
        )
        result = session._handle_command("/inspect npc_001")
        assert "npc_001" in result
        assert "Type:" in result

    def test_inspect_not_found(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/inspect nonexistent")
        assert "not found" in result


class TestBeliefCommand:
    def test_belief_empty(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/belief player_001")
        assert "No beliefs recorded" in result

    def test_belief_with_data(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        from verisaria.engine.schemas import Belief

        session.belief_engine._beliefs["npc_001"] = [
            Belief(
                belief_id="b1",
                owner_id="npc_001",
                claim="The tavern is safe",
                confidence=0.8,
                conviction=Conviction.HIGH,
                formed_at_tick=5,
                last_updated_tick=5,
            )
        ]
        result = session._handle_command("/belief npc_001")
        assert "tavern is safe" in result
        assert "high" in result
        assert "tick: 5" in result

    def test_belief_default_player(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/belief")
        assert "No beliefs recorded" in result


class TestMemoryCommand:
    def test_memory_empty(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/memory player_001")
        assert "No memories recorded" in result

    def test_memory_with_data(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        session.memory_store.add(
            "npc_001",
            Memory(
                memory_id="m1",
                owner_id="npc_001",
                tick=3,
                content="Saw player enter tavern",
                salience=0.7,
                decay_rate=0.1,
                layer=MemoryLayer.SHORT_TERM,
            ),
        )
        result = session._handle_command("/memory npc_001")
        assert "Saw player enter tavern" in result
        assert "short_term" in result
        assert "salience" in result.lower() or "70" in result

    def test_memory_default_player(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/memory")
        assert "No memories recorded" in result
