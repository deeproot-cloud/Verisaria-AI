"""Tests for P2.3: wiring the TickScheduler / PacingPolicy into GameSession.

The world should advance faster when the player idles in a safe area, hold to a
single step during conversation/combat (slow), and force-advance under critical
campaign pressure — instead of always doing a flat tick+=1.

Invariant: a player-driven tick always advances the world by at least 1 (pausing
the player's own turn would stall the game); pacing only accelerates or holds.
"""

from __future__ import annotations

import pytest

from verisaria.engine.schemas import PacingSpeed
from verisaria.engine.scheduler import TickScheduler


@pytest.fixture
def session(tmp_path):
    from verisaria.runtime.session import GameSession
    return GameSession(
        "fixtures/content_packs/valid_frontier_town.json",
        save_dir=str(tmp_path),
        llm_backend="fake",
    )


class TestSchedulerWired:
    def test_session_has_tick_scheduler(self, session):
        assert isinstance(session.tick_scheduler, TickScheduler)

    def test_scheduler_state_in_save(self, session, tmp_path):
        session._handle_command("/save")
        saves = session.persistence.list_saves()
        latest = sorted(saves, key=lambda s: s["save_id"])[-1]["save_id"]
        data = session.persistence.load(latest)
        # scheduler_state carries pacing/tick info
        assert data.scheduler_state is not None


class TestPacingContextDerivation:
    def test_context_reflects_combat(self, session):
        # Force the player into combat and rebuild context.
        w = session.world.state
        session.combat_engine.start_combat(
            session.player_id, ["npc.guard_b"], w, tick=w.tick
        )
        ctx = session._build_pacing_context()
        assert ctx.combat_active is True

    def test_context_reflects_conversation(self, session):
        session.conversation_manager.start_session(
            initiator_id=session.player_id, participants=["npc.ele"], tick=0
        )
        ctx = session._build_pacing_context()
        assert ctx.player_in_conversation is True

    def test_context_idle_is_not_in_conversation_or_combat(self, session):
        ctx = session._build_pacing_context()
        assert ctx.player_in_conversation is False
        assert ctx.combat_active is False


class TestPacingAffectsAdvance:
    def _advance_returns(self, session, *, allow_ff=True, **ctx_flags) -> int:
        """Drive the pacing-aware advance helper directly and return steps."""
        session.tick_scheduler.update_context(**ctx_flags)
        before = session.world.state.tick
        steps = session._advance_tick_with_pacing(
            player_driven=True, allow_fast_forward=allow_ff
        )
        after = session.world.state.tick
        assert after - before == steps
        return steps

    def test_slow_advances_one(self, session):
        # Conversation → SLOW → exactly one step on a player tick.
        steps = self._advance_returns(
            session, player_in_conversation=True, combat_active=False,
            player_in_safe_area=False, no_pending_events=False,
        )
        assert steps == 1

    def test_fast_accelerates_when_allowed(self, session):
        # Safe + idle → FAST → world fast-forwards (>1) only when FF is allowed.
        steps = self._advance_returns(
            session, allow_ff=True,
            player_in_conversation=False, combat_active=False,
            player_in_safe_area=True, no_pending_events=True,
        )
        assert steps >= 2

    def test_fast_collapses_to_one_when_not_allowed(self, session):
        # A normal player tick must NOT fast-forward even if the area is safe.
        steps = self._advance_returns(
            session, allow_ff=False,
            player_in_conversation=False, combat_active=False,
            player_in_safe_area=True, no_pending_events=True,
        )
        assert steps == 1

    def test_force_under_pressure_when_allowed(self, session):
        steps = self._advance_returns(
            session, allow_ff=True, campaign_pressure=0.9,
            player_in_conversation=False, combat_active=False,
        )
        assert steps >= 3

    def test_player_tick_never_pauses_to_zero(self, session):
        # Even a PAUSE verdict must still advance the player's own tick by >=1.
        session.tick_scheduler.update_context(
            recent_major_event=True, no_reflection_completed=True,
        )
        assert session.tick_scheduler.evaluate_pacing() == PacingSpeed.PAUSE
        before = session.world.state.tick
        steps = session._advance_tick_with_pacing(player_driven=True)
        assert steps >= 1
        assert session.world.state.tick > before


class TestNormalTickNoFastForward:
    """A normal/empty player input always advances exactly one tick — the world
    only fast-forwards on an explicit /skip or /wait."""

    def test_empty_input_advances_exactly_one(self, session):
        # Move NPCs away so the player is alone in a safe area (FAST verdict),
        # yet a plain empty input must still advance only one tick.
        for nid in ("npc.guard_b", "npc.ele"):
            session.world.state.entities[nid].location_id = "far_away"
        before = session.world.state.tick
        session.run_tick("")
        assert session.world.state.tick - before == 1

    def test_skip_command_fast_forwards(self, session):
        for nid in ("npc.guard_b", "npc.ele"):
            session.world.state.entities[nid].location_id = "far_away"
        before = session.world.state.tick
        out = session._handle_command("/skip")
        assert session.world.state.tick - before >= 2  # FAST honoured
        assert out  # returns a narrative/summary

    def test_wait_n_advances_n_ticks(self, session):
        before = session.world.state.tick
        session._handle_command("/wait 5")
        assert session.world.state.tick - before == 5

    def test_wait_without_arg_advances_one(self, session):
        before = session.world.state.tick
        session._handle_command("/wait")
        assert session.world.state.tick - before == 1


class TestRunTickStillWorks:
    def test_run_tick_advances_world(self, session):
        before = session.world.state.tick
        session.run_tick("看看周围")
        assert session.world.state.tick > before

    def test_conversation_keeps_tick_to_one(self, session):
        # While in a conversation, a parseable player turn advances exactly one
        # tick (SLOW) — the world does not fast-forward away from the dialogue.
        session.conversation_manager.start_session(
            initiator_id=session.player_id, participants=["npc.ele"],
            tick=session.world.state.tick,
        )
        before = session.world.state.tick
        session.run_tick("看看周围")  # has a FakeLLM fixture → parses to a look
        assert session.world.state.tick - before == 1
