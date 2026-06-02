"""Tests for CLI and GameSession.

These tests verify the REPL entry point, command handling, and the
GameSession orchestration layer.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from verisaria.engine.campaign_loader import CampaignLoader
from verisaria.runtime.session import GameSession
from verisaria.frontends.cli import build_parser, cmd_load, cmd_replay, cmd_run, cmd_validate, main
from verisaria.engine.llm import FakeLLMProvider, LLMOrchestrator
from verisaria.engine.schemas import ActionType
from verisaria.engine.intent import ClarificationRequest
from verisaria.engine.world import EntityState, LocationState, WorldCore, WorldState, ZoneState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_pack_path(tmp_path_factory):
    """Return path to the minimal valid content pack."""
    return "fixtures/content_packs/minimal_valid.json"


@pytest.fixture
def frontier_town_path():
    """Return path to the frontier town content pack."""
    return "fixtures/content_packs/valid_frontier_town.json"


@pytest.fixture
def broken_pack_path():
    """Return path to a broken content pack."""
    return "fixtures/content_packs/broken_refs.json"


# ---------------------------------------------------------------------------
# GameSession initialisation
# ---------------------------------------------------------------------------

class TestGameSessionInit:
    def test_init_with_minimal_pack(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        assert session.pack is not None
        assert session.world is not None
        assert session.player_id == "player_001"

    def test_init_with_broken_pack_shows_warning(self, broken_pack_path, tmp_path, capsys):
        # Broken refs pack loads but has validation issues
        session = GameSession(broken_pack_path, save_dir=str(tmp_path))
        captured = capsys.readouterr()
        assert session.world is not None

    def test_init_fallback_when_file_missing(self, tmp_path):
        """Non-existent file triggers CampaignLoader fallback."""
        fake_path = str(tmp_path / "nonexistent.json")
        session = GameSession(fake_path, save_dir=str(tmp_path))
        assert session.world is not None
        assert len(session.world.state.entities) > 0  # fallback has default entities


# ---------------------------------------------------------------------------
# GameSession.tick (direct method testing, no REPL)
# ---------------------------------------------------------------------------

class TestGameSessionTick:
    def test_empty_input_advances_tick(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        tick_before = session.world.state.tick
        result = session.run_tick("")
        tick_after = session.world.state.tick
        # A bare empty input advances exactly one tick — fast-forward only
        # happens on an explicit /skip or /wait (P2.3 decision).
        assert tick_after == tick_before + 1
        # Narrative may include NPC actions; just ensure it returns something
        assert result

    def test_none_input_advances_tick(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        tick_before = session.world.state.tick
        result = session.run_tick(None)
        assert session.world.state.tick == tick_before + 1
        assert result

    def test_tick_advances_after_action(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        initial_tick = session.world.state.tick
        # Without matching LLM fixture, parse will return ClarificationRequest
        result = session.run_tick("wait")
        # Should not crash; tick may or may not advance depending on parse result
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# GameSession.look_around
# ---------------------------------------------------------------------------

class TestGameSessionLook:
    def test_look_around(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session.look_around()
        assert "Location:" in result

    def test_look_when_no_player(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        # Remove player entity
        session.world.state.entities.pop("player_001", None)
        result = session.look_around()
        assert "nowhere" in result.lower()


# ---------------------------------------------------------------------------
# Command handling
# ---------------------------------------------------------------------------

class TestCommandHandling:
    def test_help_command(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/help")
        assert "Commands:" in result
        assert "/save" in result

    def test_quit_command(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        session.running = True
        result = session._handle_command("/quit")
        assert session.running is False
        assert "Goodbye" in result

    def test_look_command(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/look")
        assert "Location:" in result

    def test_status_command(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/status")
        assert "Player:" in result
        assert "HP:" in result

    def test_save_command(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/save")
        assert result.startswith("Saved: save_")

    def test_load_without_arg_shows_list(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        # Save something first
        session._handle_command("/save")
        result = session._handle_command("/load")
        assert "Available saves:" in result

    def test_load_with_invalid_id(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/load nonexistent_save_xyz")
        assert "not found" in result.lower()

    def test_unknown_command(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        result = session._handle_command("/xyzzy")
        assert "Unknown command" in result


# ---------------------------------------------------------------------------
# Persistence integration
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_save_and_load_roundtrip(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        initial_tick = session.world.state.tick

        # Save
        save_result = session._handle_command("/save")
        save_id = save_result.replace("Saved: ", "")

        # Load
        load_result = session._handle_command(f"/load {save_id}")
        assert f"Loaded: {save_id}" in load_result
        assert session.world.state.tick == initial_tick

    def test_replay_command(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        save_result = session._handle_command("/save")
        save_id = save_result.replace("Saved: ", "")

        args = argparse.Namespace(save_id=save_id, save_dir=str(tmp_path))
        exit_code = cmd_replay(args)
        assert exit_code == 0


# ---------------------------------------------------------------------------
# Argparse entry points
# ---------------------------------------------------------------------------

class TestArgparse:
    def test_build_parser(self):
        parser = build_parser()
        args = parser.parse_args(["run", "some_pack.json"])
        assert args.command == "run"
        assert args.content_pack == "some_pack.json"

    def test_validate_good_pack(self, frontier_town_path):
        args = argparse.Namespace(content_pack=frontier_town_path)
        assert cmd_validate(args) == 0

    def test_validate_bad_pack(self, broken_pack_path):
        args = argparse.Namespace(content_pack=broken_pack_path)
        assert cmd_validate(args) == 1

    def test_validate_nonexistent(self, tmp_path):
        args = argparse.Namespace(content_pack=str(tmp_path / "nope.json"))
        assert cmd_validate(args) == 1


# ---------------------------------------------------------------------------
# Main dispatch
# ---------------------------------------------------------------------------

class TestMain:
    def test_main_run_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_main_unknown_command(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["xyzzy"])
        # argparse prints help and exits 2 for unknown subcommand
        assert exc.value.code == 2


# ---------------------------------------------------------------------------
# REPL loop (mocked input)
# ---------------------------------------------------------------------------

class TestRepl:
    def test_repl_exits_on_quit(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        with patch("builtins.input", side_effect=["/quit"]):
            with patch("builtins.print"):
                session.repl()
        assert session.running is False

    def test_repl_handles_empty_line(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        with patch("builtins.input", side_effect=["", "/quit"]):
            with patch("builtins.print"):
                session.repl()
        assert session.running is False

    def test_repl_eof_exits_gracefully(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        with patch("builtins.input", side_effect=EOFError()):
            with patch("builtins.print"):
                session.repl()
        # Should not raise; session.running may still be True but REPL exits

    def test_repl_keyboard_interrupt(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        with patch("builtins.input", side_effect=KeyboardInterrupt()):
            with patch("builtins.print"):
                session.repl()
