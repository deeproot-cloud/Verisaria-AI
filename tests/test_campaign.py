"""Tests for Campaign Driver: signal evaluation, event seed generation, cooldown."""

from __future__ import annotations

import pytest

from verisaria.engine.campaign import (
    CampaignDriver,
    CampaignDriverManager,
    PossibleEvent,
    Signal,
    SignalEvaluator,
)


class TestSignalEvaluator:
    def test_simple_comparison(self) -> None:
        ev = SignalEvaluator({"food_price": 2.0})
        assert ev.evaluate("food_price > 1.5") is True
        assert ev.evaluate("food_price < 1.5") is False

    def test_bool_equals_true_false(self) -> None:
        """A bool metric must compare against the `true`/`false` literals — so a
        pack can write `world.refugees_admitted == false` (e.g. to gate a driver
        on a world fact NOT being set)."""
        on = SignalEvaluator({"world": {"refugees_admitted": True}})
        assert on.evaluate("world.refugees_admitted == true") is True
        assert on.evaluate("world.refugees_admitted == false") is False
        assert on.evaluate("world.refugees_admitted != false") is True

        off = SignalEvaluator({"world": {"refugees_admitted": False}})
        assert off.evaluate("world.refugees_admitted == false") is True
        assert off.evaluate("world.refugees_admitted == true") is False
        # Bare truthy form keeps working: an unset flag is falsey.
        assert off.evaluate("world.refugees_admitted") is False


class TestBorderClosureGating:
    """The refugee-denial driver must stop firing once refugees are admitted —
    otherwise the world contradicts itself (Channel C content coherence)."""

    DRIVER = {
        "driver_id": "border_closure",
        "type": "social_pressure",
        "signals": [
            {"condition": "world.refugees_admitted == false", "weight": 0.5},
            {"condition": "tick >= 3", "weight": 0.3},
            {"condition": "recent_event_count >= 6", "weight": 0.2},
        ],
        "possible_events": [{"event_type": "refugee_denied_entry", "probability": 1.0}],
        "severity": 0.6,
        "cooldown_ticks": 5,
    }

    def _ctx(self, admitted: bool) -> dict:
        return {"tick": 5, "recent_event_count": 8, "world": {"refugees_admitted": admitted}}

    def test_fires_when_not_admitted(self) -> None:
        mgr = CampaignDriverManager.from_dicts([self.DRIVER])
        assert mgr.check_all(self._ctx(admitted=False), tick=5) != []

    def test_silent_once_admitted(self) -> None:
        mgr = CampaignDriverManager.from_dicts([self.DRIVER])
        assert mgr.check_all(self._ctx(admitted=True), tick=5) == []

    def test_equals_comparison(self) -> None:
        ev = SignalEvaluator({"status": "active"})
        assert ev.evaluate("status == 'active'") is True
        assert ev.evaluate("status == 'inactive'") is False

    def test_and_logic(self) -> None:
        ev = SignalEvaluator({"food_price": 2.0, "refugee_count": 10})
        assert ev.evaluate("food_price > 1.5 AND refugee_count >= 5") is True
        assert ev.evaluate("food_price > 1.5 AND refugee_count < 5") is False

    def test_or_logic(self) -> None:
        ev = SignalEvaluator({"food_price": 2.0, "refugee_count": 1})
        assert ev.evaluate("food_price > 1.5 OR refugee_count >= 5") is True
        assert ev.evaluate("food_price < 1.5 OR refugee_count >= 5") is False

    def test_parentheses(self) -> None:
        ev = SignalEvaluator({"a": 1, "b": 2, "c": 3})
        assert ev.evaluate("(a > 0 AND b > 0) OR c < 0") is True

    def test_missing_metric_returns_false(self) -> None:
        ev = SignalEvaluator({})
        assert ev.evaluate("food_price > 1.5") is False

    def test_boolean_literal(self) -> None:
        ev = SignalEvaluator({})
        assert ev.evaluate("true") is True
        assert ev.evaluate("false") is False

    def test_direct_truthy_lookup(self) -> None:
        ev = SignalEvaluator({"recent_monster_attack": True})
        assert ev.evaluate("recent_monster_attack") is True

    def test_dotted_path(self) -> None:
        ev = SignalEvaluator({"world": {"food_price": 2.5}})
        assert ev.evaluate("world.food_price > 2.0") is True


class TestCampaignDriverManager:
    def _make_driver(self, **kwargs: Any) -> CampaignDriver:
        defaults = {
            "driver_id": "test_driver",
            "driver_type": "social_pressure",
            "description": "Test driver",
            "signals": [Signal(condition="food_price > 1.5", weight=0.5)],
            "possible_events": [PossibleEvent(event_type="market_argument", probability=1.0)],
            "severity": 0.3,
            "cooldown_ticks": 5,
        }
        defaults.update(kwargs)
        return CampaignDriver(**defaults)

    def test_driver_triggers_when_signal_exceeds_severity(self) -> None:
        driver = self._make_driver()
        mgr = CampaignDriverManager([driver], seed=42)
        seeds = mgr.check_all({"food_price": 2.0}, tick=1)
        assert len(seeds) == 1
        assert seeds[0]["event_type"] == "market_argument"
        assert seeds[0]["driver_id"] == "test_driver"

    def test_driver_does_not_trigger_below_severity(self) -> None:
        driver = self._make_driver(severity=0.9)
        mgr = CampaignDriverManager([driver], seed=42)
        seeds = mgr.check_all({"food_price": 2.0}, tick=1)
        assert len(seeds) == 0

    def test_multiple_signals_summed(self) -> None:
        driver = CampaignDriver(
            driver_id="multi_signal",
            driver_type="pressure",
            description="",
            signals=[
                Signal(condition="food_price > 1.5", weight=0.3),
                Signal(condition="refugee_count > 5", weight=0.3),
            ],
            possible_events=[PossibleEvent(event_type="riot", probability=1.0)],
            severity=0.5,
            cooldown_ticks=5,
        )
        mgr = CampaignDriverManager([driver], seed=42)
        seeds = mgr.check_all({"food_price": 2.0, "refugee_count": 10}, tick=1)
        assert len(seeds) == 1

    def test_cooldown_prevents_retrigger(self) -> None:
        driver = self._make_driver(cooldown_ticks=10)
        mgr = CampaignDriverManager([driver], seed=42)
        # Trigger at tick 1
        seeds = mgr.check_all({"food_price": 2.0}, tick=1)
        assert len(seeds) == 1
        # Should not trigger again before cooldown
        seeds = mgr.check_all({"food_price": 2.0}, tick=5)
        assert len(seeds) == 0
        # Cooldown expired at tick 11
        seeds = mgr.check_all({"food_price": 2.0}, tick=11)
        assert len(seeds) == 1

    def test_weighted_event_selection(self) -> None:
        driver = CampaignDriver(
            driver_id="weighted",
            driver_type="test",
            description="",
            signals=[Signal(condition="true", weight=1.0)],
            possible_events=[
                PossibleEvent(event_type="event_a", probability=0.7),
                PossibleEvent(event_type="event_b", probability=0.3),
            ],
            severity=0.1,
            cooldown_ticks=0,
        )
        mgr = CampaignDriverManager([driver], seed=42)
        # Run many times and verify distribution
        counts = {"event_a": 0, "event_b": 0}
        for tick in range(100):
            seeds = mgr.check_all({}, tick=tick)
            if seeds:
                counts[seeds[0]["event_type"]] += 1
        # With seed=42, we should get a mix but mostly event_a
        assert counts["event_a"] > counts["event_b"]

    def test_no_events_when_no_possible_events(self) -> None:
        driver = CampaignDriver(
            driver_id="no_events",
            driver_type="test",
            description="",
            signals=[Signal(condition="true", weight=1.0)],
            possible_events=[],
            severity=0.1,
            cooldown_ticks=0,
        )
        mgr = CampaignDriverManager([driver], seed=42)
        seeds = mgr.check_all({}, tick=1)
        assert len(seeds) == 0

    def test_from_dicts(self) -> None:
        raw = [
            {
                "driver_id": "border_scarcity",
                "type": "social_pressure",
                "signals": [
                    {"condition": "food_price > 1.5", "weight": 0.5},
                ],
                "possible_events": [
                    {"event_type": "market_argument", "probability": 0.3},
                ],
                "severity": 0.45,
                "cooldown_ticks": 10,
            }
        ]
        mgr = CampaignDriverManager.from_dicts(raw, seed=42)
        assert "border_scarcity" in mgr.drivers
        seeds = mgr.check_all({"food_price": 2.0}, tick=1)
        assert len(seeds) == 1

    def test_get_state(self) -> None:
        driver = self._make_driver()
        mgr = CampaignDriverManager([driver], seed=42)
        mgr.check_all({"food_price": 2.0}, tick=1)
        state = mgr.get_state()
        assert state["test_driver"]["last_triggered_tick"] == 1
        assert state["test_driver"]["trigger_count"] == 1

    def test_reset(self) -> None:
        driver = self._make_driver()
        mgr = CampaignDriverManager([driver], seed=42)
        mgr.check_all({"food_price": 2.0}, tick=1)
        mgr.reset()
        state = mgr.get_state()
        assert state["test_driver"]["last_triggered_tick"] is None
        assert state["test_driver"]["trigger_count"] == 0


class TestCampaignDriverDeterminism:
    def test_same_seed_same_result(self) -> None:
        driver = CampaignDriver(
            driver_id="det",
            driver_type="test",
            description="",
            signals=[Signal(condition="true", weight=1.0)],
            possible_events=[
                PossibleEvent(event_type="a", probability=0.5),
                PossibleEvent(event_type="b", probability=0.5),
            ],
            severity=0.1,
            cooldown_ticks=0,
        )
        mgr1 = CampaignDriverManager([driver], seed=123)
        mgr2 = CampaignDriverManager([driver], seed=123)

        seeds1 = mgr1.check_all({}, tick=1)
        seeds2 = mgr2.check_all({}, tick=1)
        assert seeds1 == seeds2
