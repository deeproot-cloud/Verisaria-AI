"""Campaign Driver: signal detection, event seed generation, idle detection.

Phase-5 minimal version: rule-based signal evaluation, no LLM.
All randomness is seed-controlled for replayability.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from verisaria.engine.world import WorldState


# ---------------------------------------------------------------------------
# Campaign Driver model
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    condition: str
    weight: float


@dataclass
class PossibleEvent:
    event_type: str
    probability: float


@dataclass
class CampaignDriver:
    driver_id: str
    driver_type: str
    description: str
    signals: list[Signal]
    possible_events: list[PossibleEvent]
    severity: float
    cooldown_ticks: int


# ---------------------------------------------------------------------------
# Signal Evaluator
# ---------------------------------------------------------------------------

class SignalEvaluator:
    """Evaluate signal condition expressions against a context dict."""

    def __init__(self, context: dict[str, Any]) -> None:
        self.context = context

    def evaluate(self, condition: str) -> bool:
        """Evaluate a condition string."""
        cond = condition.strip()
        cond_lower = cond.lower()

        # Strip outer parens first so AND/OR inside them can be split
        while cond_lower.startswith("(") and cond_lower.endswith(")"):
            cond_lower = cond_lower[1:-1].strip()
            cond = cond[1:-1].strip()

        # OR has lowest precedence
        if " or " in cond_lower:
            parts = self._split_top_level(cond_lower, " or ")
            return any(self.evaluate(p.strip()) for p in parts)

        # AND
        if " and " in cond_lower:
            parts = self._split_top_level(cond_lower, " and ")
            return all(self.evaluate(p.strip()) for p in parts)

        # Comparison
        for op in (">=", "<=", "==", "!=", ">", "<"):
            if op in cond:
                left, right = cond.split(op, 1)
                metric_name = left.strip()
                raw_value = right.strip()
                metric_val = self._resolve_metric(metric_name)
                if metric_val is None:
                    return False
                try:
                    target_val = float(raw_value)
                except ValueError:
                    target_val = raw_value.strip("'\"")
                    # Bool metric vs `true`/`false` literal (e.g. a pack gating a
                    # driver on `world.flag == false`). Check bool before str —
                    # bool is a subclass of int, and "true"/"false" never parse
                    # as float, so they land here.
                    if isinstance(metric_val, bool):
                        low = target_val.lower()
                        if low in ("true", "false"):
                            return self._compare(metric_val, op, low == "true")
                        return False
                    if isinstance(metric_val, str):
                        return self._compare(metric_val, op, target_val)
                    return False
                return self._compare(metric_val, op, target_val)

        # Boolean literal
        if cond_lower == "true":
            return True
        if cond_lower == "false":
            return False

        # Direct context lookup (truthy check)
        val = self._resolve_metric(cond_lower)
        return bool(val)

    def _resolve_metric(self, name: str) -> Any:
        """Resolve a metric name from context."""
        # Direct key lookup
        if name in self.context:
            return self.context[name]
        # Support dotted paths like "world.food_price" (flattened)
        if "." in name:
            parts = name.split(".")
            current = self.context
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    return None
            return current
        return None

    @staticmethod
    def _compare(left: Any, op: str, right: Any) -> bool:
        try:
            return {
                ">": left > right,
                "<": left < right,
                ">=": left >= right,
                "<=": left <= right,
                "==": left == right,
                "!=": left != right,
            }[op]
        except TypeError:
            return False

    @staticmethod
    def _split_top_level(text: str, separator: str) -> list[str]:
        """Split by separator respecting parentheses."""
        parts: list[str] = []
        depth = 0
        current = ""
        i = 0
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1

            if depth == 0 and text[i : i + len(separator)].lower() == separator:
                parts.append(current.strip())
                current = ""
                i += len(separator)
                continue

            current += text[i]
            i += 1

        if current.strip():
            parts.append(current.strip())
        return parts


# ---------------------------------------------------------------------------
# Driver State (cooldown tracking)
# ---------------------------------------------------------------------------

@dataclass
class DriverState:
    driver_id: str
    last_triggered_tick: int | None = None
    trigger_count: int = 0


# ---------------------------------------------------------------------------
# Campaign Driver Manager
# ---------------------------------------------------------------------------

class CampaignDriverManager:
    """Manage active campaign drivers, evaluate signals, generate event seeds."""

    def __init__(
        self,
        drivers: list[CampaignDriver],
        seed: int = 42,
    ) -> None:
        self.drivers = {d.driver_id: d for d in drivers}
        self.states: dict[str, DriverState] = {
            d.driver_id: DriverState(d.driver_id) for d in drivers
        }
        self._rng = random.Random(seed)

    @classmethod
    def from_dicts(cls, driver_dicts: list[dict[str, Any]], seed: int = 42) -> CampaignDriverManager:
        """Construct from raw dicts (as loaded from ContentPack)."""
        drivers: list[CampaignDriver] = []
        for d in driver_dicts:
            signals = [
                Signal(condition=s["condition"], weight=s.get("weight", 0.0))
                for s in d.get("signals", [])
            ]
            events = [
                PossibleEvent(event_type=e["event_type"], probability=e.get("probability", 0.0))
                for e in d.get("possible_events", [])
            ]
            drivers.append(
                CampaignDriver(
                    driver_id=d["driver_id"],
                    driver_type=d.get("type", "unknown"),
                    description=d.get("description", ""),
                    signals=signals,
                    possible_events=events,
                    severity=d.get("severity", 0.5),
                    cooldown_ticks=d.get("cooldown_ticks", 10),
                )
            )
        return cls(drivers, seed=seed)

    def check_all(
        self,
        world_context: dict[str, Any],
        tick: int,
    ) -> list[dict[str, Any]]:
        """Evaluate all drivers and return triggered event seeds.

        Returns a list of event seed dicts.
        """
        seeds: list[dict[str, Any]] = []
        for driver in self.drivers.values():
            if not self._is_active(driver, tick):
                continue

            triggered = self._evaluate_driver(driver, world_context)
            if triggered:
                event = self._select_event(driver)
                if event:
                    self.states[driver.driver_id].last_triggered_tick = tick
                    self.states[driver.driver_id].trigger_count += 1
                    seeds.append({
                        "source": "campaign_driver",
                        "driver_id": driver.driver_id,
                        "event_type": event.event_type,
                        "tick": tick,
                    })
        return seeds

    def _is_active(self, driver: CampaignDriver, tick: int) -> bool:
        state = self.states[driver.driver_id]
        if state.last_triggered_tick is None:
            return True
        return (tick - state.last_triggered_tick) >= driver.cooldown_ticks

    def _evaluate_driver(
        self,
        driver: CampaignDriver,
        world_context: dict[str, Any],
    ) -> bool:
        """Evaluate all signals for a driver."""
        evaluator = SignalEvaluator(world_context)
        total_weight = 0.0
        for signal in driver.signals:
            if evaluator.evaluate(signal.condition):
                total_weight += signal.weight
        return total_weight >= driver.severity

    def _select_event(self, driver: CampaignDriver) -> PossibleEvent | None:
        """Weighted random selection from possible events."""
        if not driver.possible_events:
            return None
        total_prob = sum(e.probability for e in driver.possible_events)
        if total_prob <= 0:
            return None
        pick = self._rng.uniform(0, total_prob)
        cumulative = 0.0
        for event in driver.possible_events:
            cumulative += event.probability
            if pick <= cumulative:
                return event
        return driver.possible_events[-1]

    def get_state(self) -> dict[str, Any]:
        """Return serializable manager state."""
        return {
            driver_id: {
                "last_triggered_tick": s.last_triggered_tick,
                "trigger_count": s.trigger_count,
            }
            for driver_id, s in self.states.items()
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore driver states from serialized state."""
        for driver_id, sdata in state.items():
            if driver_id in self.states:
                self.states[driver_id].last_triggered_tick = sdata.get("last_triggered_tick")
                self.states[driver_id].trigger_count = sdata.get("trigger_count", 0)

    def reset(self) -> None:
        """Reset all driver states."""
        for s in self.states.values():
            s.last_triggered_tick = None
            s.trigger_count = 0
