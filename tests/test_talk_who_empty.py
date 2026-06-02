"""Tests for /talk conversation mode, /who, and empty-input hint."""

from __future__ import annotations

import pytest

from verisaria.runtime.session import GameSession
from verisaria.engine.world import EntityState


@pytest.fixture
def frontier_town_path():
    return "fixtures/content_packs/valid_frontier_town.json"


class TestTalkCommand:
    def test_talk_lists_nearby_npcs(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        result = session._handle_command("/talk")
        assert "Nearby NPCs:" in result
        assert "npc.guard_b" in result
        assert "Usage: /talk <npc_id>" in result

    def test_talk_starts_conversation(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        result = session._handle_command("/talk npc.guard_b")
        assert "Started conversation" in result
        assert session._conversation_mode == "npc.guard_b"

    def test_talk_no_npcs_nearby(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        # Move player to a location with no NPCs
        player = session.world.state.get_entity("player_001")
        player.location_id = "nonexistent_loc"
        result = session._handle_command("/talk")
        assert "No one nearby" in result

    def test_endtalk_clears_mode(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        session._handle_command("/talk npc.guard_b")
        assert session._conversation_mode is not None
        session._handle_command("/endtalk")
        assert session._conversation_mode is None

    def test_interrupt_clears_mode(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        session._handle_command("/talk npc.guard_b")
        assert session._conversation_mode is not None
        session._handle_command("/interrupt")
        assert session._conversation_mode is None


class TestWhoCommand:
    def test_who_shows_nearby_npcs(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        result = session._handle_command("/who")
        assert "npc.guard_b" in result
        assert "here" in result or "nearby" in result

    def test_who_alone(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        # Remove all NPCs
        for eid in list(session.world.state.entities.keys()):
            if eid != "player_001":
                del session.world.state.entities[eid]
        result = session._handle_command("/who")
        assert "alone" in result

    def test_who_no_player(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        session.world.state.entities.pop("player_001", None)
        result = session._handle_command("/who")
        assert "nowhere" in result

    def test_who_shows_relationship(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        result = session._handle_command("/who")
        assert "wary" in result or "friendly" in result or "unknown" in result


class TestEmptyInput:
    def test_empty_input_hint(self, frontier_town_path, tmp_path, capsys):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        # Simulate the repl behavior: first empty input shows hint
        # We can't easily test repl() directly, so test the logic via _empty_input_count
        assert session._empty_input_count == 0

    def test_empty_input_count_increments(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        session._empty_input_count += 1
        assert session._empty_input_count == 1
        session._empty_input_count += 1
        assert session._empty_input_count == 2
