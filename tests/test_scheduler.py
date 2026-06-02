"""Tests for Tick Scheduler: pacing policy, tick advancement, context evaluation."""

from __future__ import annotations

import pytest

from verisaria.engine.scheduler import PacingContext, PacingPolicy, PacingRule, TickScheduler
from verisaria.engine.schemas import PacingSpeed


class TestPacingContext:
    def test_boolean_flag_true(self) -> None:
        ctx = PacingContext(player_in_conversation=True)
        assert ctx.evaluate_condition("player_in_conversation") is True

    def test_boolean_flag_false(self) -> None:
        ctx = PacingContext(player_in_conversation=False)
        assert ctx.evaluate_condition("player_in_conversation") is False

    def test_and_condition(self) -> None:
        ctx = PacingContext(player_in_safe_area=True, no_pending_events=True)
        assert ctx.evaluate_condition("player_in_safe_area AND no_pending_events") is True

    def test_and_condition_partial(self) -> None:
        ctx = PacingContext(player_in_safe_area=True, no_pending_events=False)
        assert ctx.evaluate_condition("player_in_safe_area AND no_pending_events") is False

    def test_or_condition(self) -> None:
        ctx = PacingContext(player_in_conversation=True, combat_active=False)
        assert ctx.evaluate_condition("player_in_conversation OR combat_active") is True

    def test_or_condition_both_false(self) -> None:
        ctx = PacingContext(player_in_conversation=False, combat_active=False)
        assert ctx.evaluate_condition("player_in_conversation OR combat_active") is False

    def test_comparison_greater_than(self) -> None:
        ctx = PacingContext(campaign_pressure=0.9)
        assert ctx.evaluate_condition("campaign_pressure >= 0.8") is True
        assert ctx.evaluate_condition("campaign_pressure > 0.95") is False

    def test_parentheses(self) -> None:
        ctx = PacingContext(
            recent_major_event=True, no_reflection_completed=True, combat_active=False
        )
        assert ctx.evaluate_condition("(recent_major_event AND no_reflection_completed)") is True

    def test_complex_nested(self) -> None:
        ctx = PacingContext(
            player_in_safe_area=True,
            no_pending_events=True,
            combat_active=False,
        )
        assert ctx.evaluate_condition(
            "(player_in_safe_area AND no_pending_events) OR combat_active"
        ) is True


class TestTickScheduler:
    def test_default_pacing_slow(self) -> None:
        sched = TickScheduler()
        # Default context → no rule matches → SLOW
        assert sched.evaluate_pacing() == PacingSpeed.SLOW

    def test_conversation_pacing(self) -> None:
        sched = TickScheduler()
        sched.update_context(player_in_conversation=True)
        assert sched.evaluate_pacing() == PacingSpeed.SLOW

    def test_combat_pacing(self) -> None:
        sched = TickScheduler()
        sched.update_context(combat_active=True)
        assert sched.evaluate_pacing() == PacingSpeed.SLOW

    def test_fast_pacing_safe_area(self) -> None:
        sched = TickScheduler()
        sched.update_context(player_in_safe_area=True, no_pending_events=True)
        assert sched.evaluate_pacing() == PacingSpeed.FAST

    def test_pause_after_major_event(self) -> None:
        sched = TickScheduler()
        sched.update_context(recent_major_event=True, no_reflection_completed=True)
        assert sched.evaluate_pacing() == PacingSpeed.PAUSE

    def test_force_pacing_high_pressure(self) -> None:
        sched = TickScheduler()
        sched.update_context(campaign_pressure=0.9)
        assert sched.evaluate_pacing() == PacingSpeed.FORCE

    def test_pause_prevents_advance(self) -> None:
        sched = TickScheduler(initial_tick=5)
        sched.update_context(recent_major_event=True, no_reflection_completed=True)
        advanced = sched.advance()
        assert advanced == 0
        assert sched.tick == 5

    def test_fast_accelerates_advance(self) -> None:
        sched = TickScheduler(initial_tick=5)
        sched.update_context(player_in_safe_area=True, no_pending_events=True)
        advanced = sched.advance()
        assert advanced >= 2
        assert sched.tick > 5

    def test_force_accelerates_advance(self) -> None:
        sched = TickScheduler(initial_tick=5)
        sched.update_context(campaign_pressure=0.9)
        advanced = sched.advance()
        assert advanced >= 3
        assert sched.tick > 5

    def test_normal_advance(self) -> None:
        sched = TickScheduler(initial_tick=5)
        advanced = sched.advance()
        assert advanced == 1
        assert sched.tick == 6

    def test_is_paused(self) -> None:
        sched = TickScheduler()
        assert sched.is_paused() is False
        sched.update_context(recent_major_event=True, no_reflection_completed=True)
        assert sched.is_paused() is True

    def test_should_auto_advance(self) -> None:
        sched = TickScheduler()
        sched.update_context(player_in_safe_area=True, no_pending_events=True)
        assert sched.should_auto_advance() is True

    def test_should_not_auto_advance_when_paused(self) -> None:
        sched = TickScheduler()
        sched.update_context(recent_major_event=True, no_reflection_completed=True)
        assert sched.should_auto_advance() is False

    def test_custom_policy(self) -> None:
        policy = PacingPolicy(
            policy_id="test_policy",
            rules=[
                PacingRule(
                    condition="player_in_conversation",
                    tick_speed=PacingSpeed.FORCE,
                ),
            ],
        )
        sched = TickScheduler(policy=policy)
        sched.update_context(player_in_conversation=True)
        assert sched.evaluate_pacing() == PacingSpeed.FORCE

    def test_get_state(self) -> None:
        sched = TickScheduler(initial_tick=10)
        state = sched.get_state()
        assert state["tick"] == 10
        assert state["policy_id"] == "default_pacing"
        assert state["pacing"] == "slow"
