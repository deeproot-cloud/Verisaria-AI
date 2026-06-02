"""Tests for TUI/CLI Phase B debug commands: /inject, /log filter, /time."""

from __future__ import annotations

import pytest

from verisaria.runtime.session import GameSession
from verisaria.engine.schemas import Event, EventType


@pytest.fixture
def session(tmp_path):
    return GameSession("fixtures/content_packs/minimal_valid.json", save_dir=str(tmp_path))


def _seed_events(session):
    log = session.world.event_log
    log.append(Event(event_id="e1", event_type=EventType.PHYSICAL, actor_id="player_001",
                     tick=0, location_id="void", summary="player looked around"))
    log.append(Event(event_id="e2", event_type=EventType.COMBAT, actor_id="npc.bandit",
                     tick=1, location_id="void", summary="bandit attacks"))
    log.append(Event(event_id="e3", event_type=EventType.SPEECH, actor_id="player_001",
                     tick=2, location_id="void", summary="player says hi"))
    log.append(Event(event_id="e4", event_type=EventType.COMBAT, actor_id="player_001",
                     tick=3, location_id="void", summary="player defends"))


# --------------------------------------------------------------------------- #
# B-1  /inject
# --------------------------------------------------------------------------- #

class TestInject:
    def test_inject_runs_action_and_advances_tick(self, session):
        before = session.world.state.tick
        out = session._handle_command('/inject {"verb": "look"}')
        assert session.world.state.tick > before          # tick advanced
        assert "[inject]" in out.lower() or out            # returns narrative
        # An event for the injected action exists.
        evs = session.world.event_log.get_events(0)
        assert any("look" in (e.summary or "") or e.event_type == EventType.PHYSICAL
                   for e in evs)

    def test_inject_explicit_action_type(self, session):
        out = session._handle_command(
            '/inject {"action_type": "speech", "content": "测试注入"}'
        )
        assert out
        evs = session.world.event_log.get_events(0)
        assert any("测试注入" in (e.canonical_facts or {}).get("content", "") for e in evs)

    def test_inject_invalid_json(self, session):
        out = session._handle_command("/inject not-json")
        assert "json" in out.lower() or "用法" in out

    def test_inject_missing_required_param(self, session):
        # movement requires to_location → builder reports the schema error.
        out = session._handle_command('/inject {"action_type": "movement"}')
        assert "error" in out.lower() or "requires" in out.lower() or "用法" in out


# --------------------------------------------------------------------------- #
# B-2  /log filter
# --------------------------------------------------------------------------- #

class TestLogFilter:
    def test_filter_by_event_type(self, session):
        _seed_events(session)
        out = session._handle_command("/log filter combat")
        assert "bandit attacks" in out
        assert "player defends" in out
        assert "looked around" not in out   # physical filtered out

    def test_filter_by_actor(self, session):
        _seed_events(session)
        out = session._handle_command("/log filter actor=player_001")
        assert "looked around" in out
        assert "player defends" in out
        assert "bandit attacks" not in out  # npc.bandit filtered out

    def test_filter_no_match(self, session):
        _seed_events(session)
        out = session._handle_command("/log filter movement")
        assert "no" in out.lower() or "没有" in out

    def test_filter_usage_when_empty(self, session):
        out = session._handle_command("/log")
        assert "filter" in out.lower() or "用法" in out.lower() or "usage" in out.lower()


# --------------------------------------------------------------------------- #
# B-3  /time
# --------------------------------------------------------------------------- #

class TestTime:
    def test_time_shows_current_tick(self, session):
        out = session._handle_command("/time")
        assert "tick" in out.lower()
        assert str(session.world.state.tick) in out

    def test_time_skip_advances_world(self, session):
        before = session.world.state.tick
        out = session._handle_command("/time skip 5")
        assert session.world.state.tick == before + 5
        assert out

    def test_time_skip_invalid(self, session):
        out = session._handle_command("/time skip abc")
        assert "用法" in out or "usage" in out.lower()


# --------------------------------------------------------------------------- #
# Phase C: readline setup (C-2 Tab completion / C-3 history)
# --------------------------------------------------------------------------- #

class TestReadlineSetup:
    def test_setup_readline_does_not_crash(self, session):
        # Should be a no-op-safe call whether or not readline is present.
        session._setup_readline()

    def test_completer_completes_slash_prefix(self, session):
        import readline
        session._setup_readline()
        completer = readline.get_completer()
        assert completer is not None
        # "/sa" → "/save" (unique prefix)
        assert completer("/sa", 0) == "/save"
        assert completer("/sa", 1) is None
        # "/in" matches several commands (inspect/inject/interrupt); state walks them.
        in_matches = {c for c in session._COMPLETION_COMMANDS if c.startswith("/in")}
        walked = set()
        i = 0
        while (m := completer("/in", i)) is not None:
            walked.add(m); i += 1
        assert walked == in_matches and len(in_matches) >= 2
        # Non-slash text is not completed as a command.
        assert completer("hello", 0) is None
        # Out-of-range state returns None.
        assert completer("/save", 5) is None

    def test_all_real_commands_are_completable(self, session):
        # Sanity: the completion list references commands the dispatcher knows.
        for cmd in ("/inject", "/log", "/time", "/wait", "/skip", "/map", "/who"):
            assert cmd in session._COMPLETION_COMMANDS
