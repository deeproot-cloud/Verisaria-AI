"""Tick Scheduler: tick advancement with Pacing Policy control.

Phase-5 minimal version: rule-based pacing evaluation, no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verisaria.engine.schemas import PacingSpeed


# ---------------------------------------------------------------------------
# Pacing Policy
# ---------------------------------------------------------------------------

@dataclass
class PacingRule:
    condition: str
    tick_speed: PacingSpeed
    description: str = ""


@dataclass
class PacingPolicy:
    policy_id: str
    rules: list[PacingRule] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pacing Context
# ---------------------------------------------------------------------------

@dataclass
class PacingContext:
    """Mutable context for pacing rule evaluation."""

    player_in_conversation: bool = False
    player_in_safe_area: bool = False
    no_pending_events: bool = True
    recent_major_event: bool = False
    no_reflection_completed: bool = False
    campaign_pressure: float = 0.0
    combat_active: bool = False

    def evaluate_condition(self, condition: str) -> bool:
        """Evaluate a simple pacing condition string."""
        cond = condition.strip()
        # Normalise AND/OR to lowercase for parsing
        cond_lower = cond.lower()

        # Strip outer parens first
        while cond_lower.startswith("(") and cond_lower.endswith(")"):
            cond_lower = cond_lower[1:-1].strip()
            cond = cond[1:-1].strip()

        # Handle parenthetical expressions simply: split by AND/OR
        # For MVP we only support flat AND/OR without nested parens
        if " or " in cond_lower:
            parts = cond_lower.split(" or ")
            return any(self.evaluate_condition(p.strip()) for p in parts)

        if " and " in cond_lower:
            parts = cond_lower.split(" and ")
            return all(self.evaluate_condition(p.strip()) for p in parts)

        # Direct boolean flags
        flag_map = {
            "player_in_conversation": self.player_in_conversation,
            "player_in_safe_area": self.player_in_safe_area,
            "no_pending_events": self.no_pending_events,
            "recent_major_event": self.recent_major_event,
            "no_reflection_completed": self.no_reflection_completed,
            "combat_active": self.combat_active,
        }
        if cond_lower in flag_map:
            return flag_map[cond_lower]

        # Comparison: metric operator value
        for op in (">=", "<=", "==", "!=", ">", "<"):
            if op in cond:
                left, right = cond.split(op, 1)
                metric_name = left.strip().lower()
                try:
                    value = float(right.strip())
                except ValueError:
                    continue
                metric_val = self._get_metric(metric_name)
                if metric_val is None:
                    return False
                return self._compare(metric_val, op, value)

        return False

    def _get_metric(self, name: str) -> float | None:
        if name == "campaign_pressure":
            return self.campaign_pressure
        return None

    @staticmethod
    def _compare(left: float, op: str, right: float) -> bool:
        return {
            ">": left > right,
            "<": left < right,
            ">=": left >= right,
            "<=": left <= right,
            "==": left == right,
            "!=": left != right,
        }[op]


# ---------------------------------------------------------------------------
# Tick Scheduler
# ---------------------------------------------------------------------------

class TickScheduler:
    """Manage tick advancement with pacing control."""

    DEFAULT_POLICY = PacingPolicy(
        policy_id="default_pacing",
        rules=[
            PacingRule(
                condition="combat_active",
                tick_speed=PacingSpeed.SLOW,
                description="Combat active → slow ticks for player thinking time",
            ),
            PacingRule(
                condition="player_in_conversation",
                tick_speed=PacingSpeed.SLOW,
                description="Player in conversation → slow, no interruption",
            ),
            PacingRule(
                condition="recent_major_event AND no_reflection_completed",
                tick_speed=PacingSpeed.PAUSE,
                description="Major event without reflection → pause",
            ),
            PacingRule(
                condition="player_in_safe_area AND no_pending_events",
                tick_speed=PacingSpeed.FAST,
                description="Safe area, no events → fast forward",
            ),
            PacingRule(
                condition="campaign_pressure >= 0.8",
                tick_speed=PacingSpeed.FORCE,
                description="Critical campaign pressure → force advance",
            ),
        ],
    )

    def __init__(
        self,
        initial_tick: int = 0,
        policy: PacingPolicy | None = None,
    ) -> None:
        self.tick = initial_tick
        self.policy = policy or self.DEFAULT_POLICY
        self._pacing_context = PacingContext()

    def update_context(self, **kwargs: Any) -> None:
        """Update pacing context flags."""
        for key, value in kwargs.items():
            if hasattr(self._pacing_context, key):
                setattr(self._pacing_context, key, value)

    def evaluate_pacing(self) -> PacingSpeed:
        """Evaluate pacing rules in order and return the first match.

        Rules are evaluated top-to-bottom; the first matching rule wins.
        If no rule matches, defaults to SLOW (conservative).
        """
        for rule in self.policy.rules:
            if self._pacing_context.evaluate_condition(rule.condition):
                return rule.tick_speed
        return PacingSpeed.SLOW

    def advance(self, steps: int = 1) -> int:
        """Advance tick by given steps, respecting pause.

        Returns the number of ticks actually advanced.
        """
        pacing = self.evaluate_pacing()
        if pacing == PacingSpeed.PAUSE:
            return 0
        if pacing == PacingSpeed.FAST:
            steps = max(steps, 2)
        if pacing == PacingSpeed.FORCE:
            steps = max(steps, 3)
        self.tick += steps
        return steps

    def is_paused(self) -> bool:
        return self.evaluate_pacing() == PacingSpeed.PAUSE

    def should_auto_advance(self) -> bool:
        """Check if the world should auto-advance (player idle)."""
        pacing = self.evaluate_pacing()
        return pacing in (PacingSpeed.FAST, PacingSpeed.FORCE)

    def get_state(self) -> dict[str, Any]:
        """Return serializable scheduler state."""
        return {
            "tick": self.tick,
            "policy_id": self.policy.policy_id,
            "pacing": self.evaluate_pacing().value,
        }
