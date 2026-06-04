"""Coherence Checker: validates intent against world state.

P1-7: extracted into an independent module with enhanced checks.

Responsibilities:
- Spatial coherence (same location for non-movement actions)
- Target existence and type compatibility
- Movement reachability
- Combat state consistency
- Commitment / action type sanity
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from verisaria.engine.schemas import ParsedIntent
from verisaria.engine.world import WorldState


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CoherenceIssue:
    issue_type: str
    severity: str  # "error" | "warning"
    message: str
    field: str = ""


# ---------------------------------------------------------------------------
# Coherence Checker
# ---------------------------------------------------------------------------

class CoherenceChecker:
    """Check if an intent is coherent with the current world state.

    Checks are grouped by domain and can be run selectively.
    """

    def check(self, intent: ParsedIntent, world: WorldState) -> list[CoherenceIssue]:
        """Run all coherence checks and return issues."""
        issues: list[CoherenceIssue] = []
        issues.extend(self._check_actor(intent, world))
        issues.extend(self._check_target(intent, world))
        issues.extend(self._check_spatial(intent, world))
        issues.extend(self._check_movement(intent, world))
        issues.extend(self._check_combat(intent, world))
        issues.extend(self._check_commitment(intent, world))
        return issues

    # -- Actor checks --

    def _check_actor(self, intent: ParsedIntent, world: WorldState) -> list[CoherenceIssue]:
        issues: list[CoherenceIssue] = []
        actor = world.get_entity(intent.actor_id)
        if actor is None:
            issues.append(
                CoherenceIssue(
                    issue_type="actor_not_found",
                    severity="error",
                    message=f"Actor '{intent.actor_id}' not found in world",
                    field="actor_id",
                )
            )
            return issues

        # Actor state: HP <= 0 means incapacitated
        if actor.hp <= 0:
            issues.append(
                CoherenceIssue(
                    issue_type="actor_incapacitated",
                    severity="error",
                    message=f"Actor '{intent.actor_id}' is incapacitated (HP {actor.hp})",
                    field="actor_id",
                )
            )
        return issues

    # -- Target checks --

    @staticmethod
    def _resolve_target_id(target_id: str, world: WorldState) -> tuple[str, Any | None, Any | None]:
        """Try multiple ways to resolve a target_id."""
        # Direct lookup
        entity = world.get_entity(target_id)
        location = world.locations.get(target_id)
        if entity or location:
            return target_id, entity, location

        # Strip common LLM prefixes
        cleaned = target_id
        for prefix in ("location.", "loc.", "entity.", "npc."):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix):]
                entity = world.get_entity(cleaned)
                location = world.locations.get(cleaned)
                if entity or location:
                    return cleaned, entity, location

        # Try last segment after any dot
        if "." in target_id:
            cleaned = target_id.rsplit(".", 1)[-1]
            entity = world.get_entity(cleaned)
            location = world.locations.get(cleaned)
            if entity or location:
                return cleaned, entity, location

        # Substring match against location ids (handles LLM returning a partial
        # name, e.g. "square" → "town_square", "street" → "loc_street"). Only
        # accept an unambiguous single match to avoid silently picking wrong.
        ref = target_id.strip().lower()
        if ref:
            loc_matches = [
                lid for lid in world.locations
                if ref in lid.lower() or lid.lower() in ref
            ]
            if len(loc_matches) == 1:
                lid = loc_matches[0]
                return lid, world.get_entity(lid), world.locations.get(lid)

        return target_id, None, None

    def _check_target(self, intent: ParsedIntent, world: WorldState) -> list[CoherenceIssue]:
        issues: list[CoherenceIssue] = []
        if intent.target_id is None:
            return issues

        target_id, target_entity, target_location = self._resolve_target_id(intent.target_id, world)

        # Target must exist as either entity or location
        if target_entity is None and target_location is None:
            issues.append(
                CoherenceIssue(
                    issue_type="target_not_found",
                    severity="error",
                    message=f"Target '{intent.target_id}' not found in world",
                    field="target_id",
                )
            )
            return issues

        # Type compatibility: combat must target an entity
        if intent.intent_type.value == "combat" and target_entity is None:
            issues.append(
                CoherenceIssue(
                    issue_type="combat_target_invalid",
                    severity="error",
                    message=f"Combat action must target an entity, not a location",
                    field="target_id",
                )
            )

        # Type compatibility: speech should target an entity (warn if location)
        if intent.intent_type.value == "speech" and target_location is not None:
            issues.append(
                CoherenceIssue(
                    issue_type="speech_target_location",
                    severity="warning",
                    message=f"Speech target '{intent.target_id}' is a location, not an entity",
                    field="target_id",
                )
            )

        return issues

    # -- Spatial checks --

    def _check_spatial(self, intent: ParsedIntent, world: WorldState) -> list[CoherenceIssue]:
        """Non-movement actions require actor and target to be in the same location."""
        issues: list[CoherenceIssue] = []
        if intent.intent_type.value == "movement":
            return issues

        actor = world.get_entity(intent.actor_id)
        if actor is None or intent.target_id is None:
            return issues

        _, target, _ = self._resolve_target_id(intent.target_id, world)
        if target is None:
            return issues  # location target handled elsewhere

        if actor.location_id != target.location_id:
            target_name = world.display_name(intent.target_id)
            target_loc = world.location_label(target.location_id)
            issues.append(
                CoherenceIssue(
                    issue_type="spatial_mismatch",
                    severity="error",
                    message=(
                        f"{target_name}不在这儿，TA在{target_loc}那边，"
                        "你得先过去才能搭话。"
                    ),
                    field="target_id",
                )
            )
        return issues

    # -- Movement checks --

    def _check_movement(self, intent: ParsedIntent, world: WorldState) -> list[CoherenceIssue]:
        """Movement target must be a reachable location."""
        issues: list[CoherenceIssue] = []
        if intent.intent_type.value != "movement":
            return issues

        actor = world.get_entity(intent.actor_id)
        if actor is None:
            return issues

        to_location = intent.target_id
        if to_location is None:
            # target_id may be in params for movement
            to_location = intent.modifiers.get("to_location") or intent.modifiers.get("destination")

        if to_location is None:
            issues.append(
                CoherenceIssue(
                    issue_type="movement_no_destination",
                    severity="error",
                    message="Movement action has no destination",
                    field="target_id",
                )
            )
            return issues

        # Resolve location ID (handle LLM prefixes)
        resolved_id, resolved_entity, resolved_loc = self._resolve_target_id(to_location, world)

        # If target is an entity, use its location as the destination
        if resolved_loc is None and resolved_entity is not None:
            resolved_loc = world.locations.get(resolved_entity.location_id)
            if resolved_loc is not None:
                resolved_id = resolved_entity.location_id

        if resolved_loc is None:
            issues.append(
                CoherenceIssue(
                    issue_type="movement_unknown_location",
                    severity="error",
                    message=f"Cannot move to unknown location '{to_location}'",
                    field="target_id",
                )
            )
            return issues

        # Must be reachable via the connection graph — TRANSITIVELY, not just a direct
        # neighbour. The player says "go to X"; if a path exists we let them, instead
        # of forcing a manual hop-by-hop slog (the playtest pain). Travel-time / en-route
        # events over multiple ticks is a future refinement.
        current_loc = world.locations.get(actor.location_id)
        if current_loc is not None and current_loc.connections:
            if resolved_id != actor.location_id and not self._reachable(
                world, actor.location_id, resolved_id
            ):
                issues.append(
                    CoherenceIssue(
                        issue_type="movement_unreachable",
                        severity="error",
                        message=(
                            f"Location '{to_location}' is not reachable "
                            f"from '{actor.location_id}'"
                        ),
                        field="target_id",
                    )
                )

        return issues

    @staticmethod
    def _reachable(world: WorldState, start: str, goal: str) -> bool:
        """Whether ``goal`` is reachable from ``start`` over location connections
        (graph traversal; any-path reachability, not shortest)."""
        seen = {start}
        frontier = [start]
        while frontier:
            loc = world.locations.get(frontier.pop())
            for c in (loc.connections if loc else []) or []:
                nxt = c.to_location
                if nxt == goal:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        return False

    # -- Combat checks --

    def _check_combat(self, intent: ParsedIntent, world: WorldState) -> list[CoherenceIssue]:
        """Combat actions require target to be alive and combat-ready."""
        issues: list[CoherenceIssue] = []
        if intent.intent_type.value != "combat":
            return issues

        verb = (intent.modifiers or {}).get("verb", "")
        raw = intent.raw_text.lower()
        is_self_combat = (
            verb in ("defend", "dodge", "flee")
            or "防御" in raw or "defend" in raw
            or "闪避" in raw or "dodge" in raw
            or "逃跑" in raw or "flee" in raw or "逃离" in raw
        )
        if is_self_combat and intent.target_id is None:
            return issues  # self-targeted actions don't need an explicit target

        if intent.target_id is None:
            issues.append(
                CoherenceIssue(
                    issue_type="combat_no_target",
                    severity="error",
                    message="Combat action requires a target",
                    field="target_id",
                )
            )
            return issues

        target = world.get_entity(intent.target_id)
        if target is None:
            return issues  # already caught by target check

        if target.hp <= 0:
            issues.append(
                CoherenceIssue(
                    issue_type="combat_target_incapacitated",
                    severity="warning",
                    message=f"Target '{intent.target_id}' is already incapacitated",
                    field="target_id",
                )
            )

        return issues

    # -- Commitment checks --

    def _check_commitment(self, intent: ParsedIntent, world: WorldState) -> list[CoherenceIssue]:
        """Low-commitment intents should not have significant world effects."""
        issues: list[CoherenceIssue] = []
        if intent.commitment.value in ("considering", "preparing"):
            if intent.intent_type.value not in ("wait", "look"):
                issues.append(
                    CoherenceIssue(
                        issue_type="low_commitment_with_action",
                        severity="warning",
                        message=(
                            f"Commitment '{intent.commitment.value}' with action type "
                            f"'{intent.intent_type.value}' — verify this is a preview, not execution"
                        ),
                        field="commitment",
                    )
                )
        return issues
