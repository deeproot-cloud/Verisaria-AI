"""Action Queue: collect, merge, conflict-detect, and execute Actions per tick.

Phase-13 minimal version:
- Collect player + NPC + campaign actions
- Sort by priority (combat > physical > social > movement > speech)
- Detect simple conflicts (same-actor, same-target exclusive)
- Execute in order, skipping actions invalidated by earlier results
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verisaria.engine.schemas import Action, ActionType, Event
from verisaria.engine.world import WorldCore


# ---------------------------------------------------------------------------
# Conflict record
# ---------------------------------------------------------------------------

@dataclass
class ActionConflict:
    action_a_id: str
    action_b_id: str
    conflict_type: str  # "same_actor" | "same_target" | "zone_capacity"
    resolution: str = "pending"  # "a_wins" | "b_wins" | "both_cancelled"


# ---------------------------------------------------------------------------
# Action Queue
# ---------------------------------------------------------------------------

class ActionQueue:
    """Collect and resolve multiple Actions within a single tick.

    Priority order (design doc §7.2.12):
        combat > physical > social > movement > speech > system
    Tie-breaker:
        player > NPC > campaign event > other
    Further tie:
        action_id lexical order (deterministic)
    """

    _TYPE_PRIORITY: dict[str, int] = {
        "combat": 0,
        "physical": 1,
        "social": 2,
        "movement": 3,
        "speech": 4,
        "system": 5,
    }

    def __init__(self) -> None:
        self._pending: list[Action] = []
        self._conflicts: list[ActionConflict] = []
        self._executed_ids: set[str] = set()
        self._cancelled_ids: set[str] = set()
        self._resolutions: dict[str, Any] = {}  # action_id -> ResolutionResult

    def submit(self, action: Action, resolution: Any | None = None) -> None:
        """Add an action to the queue for this tick.

        Optionally pass a ``ResolutionResult`` (from RulesEngine) so that
        its ``summary`` and ``canonical_facts`` are used when the action
        is committed, eliminating dual-track event generation.
        """
        self._pending.append(action)
        if resolution is not None:
            self._resolutions[action.action_id] = resolution

    def clear(self) -> None:
        """Reset the queue for the next tick."""
        self._pending.clear()
        self._conflicts.clear()
        self._executed_ids.clear()
        self._cancelled_ids.clear()
        self._resolutions.clear()

    def resolve(
        self,
        world: WorldCore,
    ) -> list[Event]:
        """Sort, detect conflicts, execute, and return Events."""
        events, _ = self.resolve_with_combat(world)
        return events

    def resolve_with_combat(
        self,
        world: WorldCore,
    ) -> tuple[list[Event], list[Action]]:
        """Sort, detect conflicts, execute, and return Events + combat actions.

        Combat actions are excluded from Events and returned separately so that
        the Combat Engine can resolve them with its own state machine.
        """
        if not self._pending:
            return [], []

        # 1. Sort by priority
        sorted_actions = self._sort_actions(self._pending)

        # 2. Detect conflicts
        self._conflicts = self._detect_conflicts(sorted_actions)
        self._apply_conflict_resolution()

        # 3. Execute in order
        events: list[Event] = []
        combat_actions: list[Action] = []
        for action in sorted_actions:
            if action.action_id in self._cancelled_ids:
                continue
            if self._is_invalidated(action, world):
                continue

            if action.action_type.value == "combat":
                self._executed_ids.add(action.action_id)
                combat_actions.append(action)
                continue

            res = self._resolutions.get(action.action_id)
            event = world.commit_action(
                action,
                summary=getattr(res, "summary", None) if res is not None else None,
                canonical_facts=getattr(res, "canonical_facts", None) if res is not None else None,
            )
            self._executed_ids.add(action.action_id)
            events.append(event)

        return events, combat_actions

    # ------------------------------------------------------------------
    # Sorting
    # ------------------------------------------------------------------

    def _sort_actions(self, actions: list[Action]) -> list[Action]:
        """Return actions sorted by priority rules."""
        return sorted(actions, key=self._priority_key)

    def _priority_key(self, action: Action) -> tuple:
        type_pri = self._TYPE_PRIORITY.get(action.action_type.value, 99)
        actor_pri = self._actor_priority(action.actor_id)
        return (type_pri, actor_pri, action.action_id)

    @staticmethod
    def _actor_priority(actor_id: str) -> int:
        if actor_id.startswith("player"):
            return 0
        if actor_id.startswith("npc"):
            return 1
        if actor_id.startswith("campaign"):
            return 2
        return 3

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def _detect_conflicts(self, actions: list[Action]) -> list[ActionConflict]:
        """Find conflicting action pairs."""
        conflicts: list[ActionConflict] = []
        seen_actors: dict[str, Action] = {}
        seen_targets: dict[str, list[Action]] = {}

        for action in actions:
            # Same-actor conflict: multiple actions by one actor
            if action.actor_id in seen_actors:
                conflicts.append(
                    ActionConflict(
                        action_a_id=seen_actors[action.actor_id].action_id,
                        action_b_id=action.action_id,
                        conflict_type="same_actor",
                    )
                )
            else:
                seen_actors[action.actor_id] = action

            # Same-target conflict: exclusive actions on same target
            if action.target_id:
                seen_targets.setdefault(action.target_id, []).append(action)

        for target_id, target_actions in seen_targets.items():
            if len(target_actions) > 1:
                # Check if any pair is mutually exclusive
                for i in range(len(target_actions)):
                    for j in range(i + 1, len(target_actions)):
                        a, b = target_actions[i], target_actions[j]
                        if self._is_mutually_exclusive(a, b):
                            conflicts.append(
                                ActionConflict(
                                    action_a_id=a.action_id,
                                    action_b_id=b.action_id,
                                    conflict_type="same_target",
                                )
                            )

        return conflicts

    @staticmethod
    def _is_mutually_exclusive(a: Action, b: Action) -> bool:
        """Check if two actions on the same target cannot both succeed."""
        # Combat actions are generally exclusive with each other
        if a.action_type == ActionType.COMBAT and b.action_type == ActionType.COMBAT:
            return True
        # Movement to same zone may be exclusive if capacity limited
        if a.action_type == ActionType.MOVEMENT and b.action_type == ActionType.MOVEMENT:
            return True
        # Kill vs Heal / Steal vs Guard — simplified: physical vs physical on same target
        if a.action_type == ActionType.PHYSICAL and b.action_type == ActionType.PHYSICAL:
            verb_a = a.params.get("verb", "")
            verb_b = b.params.get("verb", "")
            if verb_a in ("steal", "attack") and verb_b in ("steal", "attack"):
                return True
        return False

    def _apply_conflict_resolution(self) -> None:
        """Mark lower-priority actions as cancelled for each conflict."""
        for conflict in self._conflicts:
            a = self._find_action(conflict.action_a_id)
            b = self._find_action(conflict.action_b_id)
            if a is None or b is None:
                continue

            if conflict.conflict_type == "same_actor":
                # Keep higher-priority action, cancel the other
                winner = a if self._priority_key(a) <= self._priority_key(b) else b
                loser = b if winner is a else a
                self._cancelled_ids.add(loser.action_id)
                conflict.resolution = f"{winner.action_id}_wins"
            elif conflict.conflict_type == "same_target":
                # Both physical combat actions: higher priority wins
                winner = a if self._priority_key(a) <= self._priority_key(b) else b
                loser = b if winner is a else a
                self._cancelled_ids.add(loser.action_id)
                conflict.resolution = f"{winner.action_id}_wins"

    def _find_action(self, action_id: str) -> Action | None:
        for a in self._pending:
            if a.action_id == action_id:
                return a
        return None

    # ------------------------------------------------------------------
    # Invalidation
    # ------------------------------------------------------------------

    def _is_invalidated(self, action: Action, world: WorldCore) -> bool:
        """Check if action is no longer valid due to world state changes."""
        # Actor no longer exists
        if world.state.get_entity(action.actor_id) is None:
            return True
        # Target no longer exists (for targeted actions)
        if action.target_id:
            entity = world.state.get_entity(action.target_id)
            location = world.state.locations.get(action.target_id)
            if entity is None and location is None:
                return True
        # Movement: zone capacity exceeded by earlier actions
        if action.action_type == ActionType.MOVEMENT:
            to_loc = action.params.get("to_location")
            to_zone = action.params.get("to_zone")
            zone = world.state.get_zone(to_loc, to_zone) if to_zone else None
            if zone is not None and len(zone.occupant_ids) >= zone.capacity:
                return True
        return False

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_pending(self) -> list[Action]:
        return list(self._pending)

    def get_conflicts(self) -> list[ActionConflict]:
        return list(self._conflicts)

    def get_executed(self) -> list[str]:
        return list(self._executed_ids)

    def get_cancelled(self) -> list[str]:
        return list(self._cancelled_ids)
