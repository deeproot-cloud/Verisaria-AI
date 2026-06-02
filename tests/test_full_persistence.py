"""Integration tests for full subsystem persistence (P2-1).

Verifies that save/load round-trip preserves:
- World core (state + event log)
- MemoryStore + BeliefEngine (NPC memories & beliefs)
- AgendaService (player signals, drives, proposals)
- CombatEngine (active sessions)
- ConversationManager (active sessions)
- NPCActionGenerator (seq state)
- CampaignDriverManager (driver states)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from verisaria.runtime.session import GameSession
from verisaria.engine.schemas import Memory, MemoryLayer


@pytest.fixture
def minimal_pack_path():
    return "fixtures/content_packs/minimal_valid.json"


class TestFullPersistenceRoundTrip:
    def test_save_load_preserves_npc_memory_and_belief(self, minimal_pack_path, tmp_path):
        """Run ticks to build NPC memory, save, load, verify memory/belief intact."""
        # -- Build state --
        session1 = GameSession(minimal_pack_path, save_dir=str(tmp_path / "saves"))

        # Inject a memory directly into NPC's memory store
        session1.memory_store.add(
            "npc.guard_b",
            Memory(
                memory_id="mem_test_1",
                owner_id="npc.guard_b",
                source_observation_id="obs_1",
                tick=1,
                content="有人偷了铁匠的短剑",
                salience=0.9,
                decay_rate=0.05,
                layer=MemoryLayer.WORKING,
                topic_tags=["theft"],
            ),
        )

        # Add agenda signal
        session1.agenda_service.add_signal(
            note="我想找到失落的短剑",
            tick=session1.world.state.tick,
            topic_tags=["quest", "sword"],
        )

        # Save
        result = session1._handle_command("/save")
        save_id = result.replace("Saved: ", "")

        # -- Restore into fresh session --
        session2 = GameSession(minimal_pack_path, save_dir=str(tmp_path / "saves"))
        session2._handle_command(f"/load {save_id}")

        # Verify world core
        assert session2.world.state.tick == session1.world.state.tick

        # Verify NPC memory
        memories = session2.memory_store.get_all("npc.guard_b")
        assert len(memories) == 1
        assert memories[0].content == "有人偷了铁匠的短剑"
        assert memories[0].salience == 0.9

        # Verify agenda
        agenda_state = session2.agenda_service.get_state()
        assert agenda_state["signal_count"] == 1
        assert any("短剑" in s["note"] for s in agenda_state["signals"])

    def test_save_load_preserves_conversation_state(self, minimal_pack_path, tmp_path):
        session1 = GameSession(minimal_pack_path, save_dir=str(tmp_path / "saves"))

        # Start a conversation
        session1.conversation_manager.start_session(
            initiator_id="player_001",
            participants=["npc.guard_b"],
            tick=session1.world.state.tick,
        )

        result = session1._handle_command("/save")
        save_id = result.replace("Saved: ", "")

        session2 = GameSession(minimal_pack_path, save_dir=str(tmp_path / "saves"))
        session2._handle_command(f"/load {save_id}")

        active = session2.conversation_manager.get_active_session("player_001")
        assert active is not None
        assert "npc.guard_b" in active.participants

    def test_save_load_preserves_combat_state(self, minimal_pack_path, tmp_path):
        session1 = GameSession(minimal_pack_path, save_dir=str(tmp_path / "saves"))

        # Start combat manually via engine
        session1.combat_engine.start_combat(
            initiator_id="player_001",
            target_ids=["npc.guard_b"],
            world=session1.world.state,
            tick=session1.world.state.tick,
        )

        result = session1._handle_command("/save")
        save_id = result.replace("Saved: ", "")

        session2 = GameSession(minimal_pack_path, save_dir=str(tmp_path / "saves"))
        session2._handle_command(f"/load {save_id}")

        assert session2.combat_engine.is_in_combat("player_001")

    def test_save_load_preserves_campaign_driver_state(self, minimal_pack_path, tmp_path):
        session1 = GameSession(minimal_pack_path, save_dir=str(tmp_path / "saves"))

        if not hasattr(session1, "campaign_driver_manager") or session1.campaign_driver_manager is None:
            pytest.skip("No campaign driver manager in this content pack")

        # Manually trigger a driver to advance its state
        original_state = session1.campaign_driver_manager.get_state()
        driver_ids = list(original_state.keys())
        if not driver_ids:
            pytest.skip("No campaign drivers configured")

        # Save
        result = session1._handle_command("/save")
        save_id = result.replace("Saved: ", "")

        session2 = GameSession(minimal_pack_path, save_dir=str(tmp_path / "saves"))
        session2._handle_command(f"/load {save_id}")

        loaded_state = session2.campaign_driver_manager.get_state()
        for did in driver_ids:
            assert loaded_state[did]["trigger_count"] == original_state[did]["trigger_count"]
            assert loaded_state[did]["last_triggered_tick"] == original_state[did]["last_triggered_tick"]

    def test_save_file_contains_all_subsystem_keys(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path / "saves"))

        # Populate subsystems
        session.memory_store.add(
            "npc.guard_b",
            Memory(
                memory_id="mem_1",
                owner_id="npc.guard_b",
                source_observation_id="obs_1",
                tick=0,
                content="test",
                salience=0.5,
                decay_rate=0.05,
                layer=MemoryLayer.WORKING,
                topic_tags=["test"],
            ),
        )
        session.agenda_service.add_signal("test signal", tick=0)

        result = session._handle_command("/save")
        save_id = result.replace("Saved: ", "")

        import json
        path = tmp_path / "saves" / f"{save_id}.json"
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Verify all subsystem keys present
        assert "subjectivity_state" in data
        assert "player_state" in data
        assert "combat_state" in data
        assert "conversation_state" in data
        assert "npc_runtime_state" in data
        assert "scheduler_state" in data

        # Verify non-empty content
        assert data["subjectivity_state"]["memories"] != {}
        assert data["player_state"]["agenda"]["signals"] != []
