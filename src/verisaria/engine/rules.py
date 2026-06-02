"""Rules Engine: hard rules for Action → Event resolution.

The Rules Engine handles deterministic, non-LLM resolution:
- Precondition checks (can the action be performed?)
- Spatial rules (zone connectivity, capacity)
- Numerical checks (attribute vs threshold for physical actions)
- Direct Event generation for simple actions (speech, movement, look)

Actions that require subjective interpretation (social, complex physical)
are passed to the LLM Arbiter. The Rules Engine decides which path to take.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from verisaria.engine.schemas import Action, ActionType, EventType
from verisaria.engine.world import WorldState


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------

@dataclass
class ResolutionResult:
    can_execute: bool
    event_type: EventType
    summary: str
    canonical_facts: dict[str, Any]
    state_changes: dict[str, Any]
    reason: str = ""  # human-readable explanation
    requires_arbiter: bool = False


# ---------------------------------------------------------------------------
# Rules Engine
# ---------------------------------------------------------------------------

class RulesEngine:
    """Deterministic rule resolver."""

    def resolve(self, action: Action, world: WorldState) -> ResolutionResult:
        """Resolve an Action using hard rules.

        Returns a ResolutionResult. If requires_arbiter=True, the caller
        should pass the action to LLM Arbiter instead of using the result directly.
        """
        resolver = self._get_resolver(action.action_type)
        return resolver(action, world)

    def _get_resolver(self, action_type: ActionType):
        mapping = {
            ActionType.SPEECH: self._resolve_speech,
            ActionType.MOVEMENT: self._resolve_movement,
            ActionType.PHYSICAL: self._resolve_physical,
            ActionType.SOCIAL: self._resolve_social,
            ActionType.COMBAT: self._resolve_combat,
            ActionType.LOOK: self._resolve_physical,
            ActionType.WAIT: self._resolve_wait,
        }
        return mapping.get(action_type, self._resolve_unknown)

    # ------------------------------------------------------------------
    # Speech
    # ------------------------------------------------------------------

    def _resolve_speech(self, action: Action, world: WorldState) -> ResolutionResult:
        content = action.params.get("content", "")
        volume = action.params.get("volume", "normal")
        target = f" 对 {action.target_id}" if action.target_id else ""
        # Neutral, objective summary — the verbatim line lives in canonical_facts
        # (the narrator reads it from there). Keeping content OUT of the summary also
        # stops the Event "no subjective motive" validator from tripping on
        # 因为/为了/意图/计划 that legitimately appear inside dialogue (a real bug: a
        # whole tick aborted mid-conversation when an NPC's reply contained "因为").
        spoke = {"whisper": "低声说话", "shout": "大声说话"}.get(volume, "说话")
        summary = f"{action.actor_id}{target} {spoke}"
        return ResolutionResult(
            can_execute=True,
            event_type=EventType.SPEECH,
            summary=summary,
            canonical_facts={"content": content, "volume": volume},
            state_changes={},
        )

    # ------------------------------------------------------------------
    # Movement
    # ------------------------------------------------------------------

    def _resolve_movement(self, action: Action, world: WorldState) -> ResolutionResult:
        to_location = action.params.get("to_location")
        to_zone = action.params.get("to_zone")

        if not to_location:
            return ResolutionResult(
                can_execute=False,
                event_type=EventType.MOVEMENT,
                summary="",
                canonical_facts={},
                state_changes={},
                reason="movement requires to_location",
            )

        entity = world.get_entity(action.actor_id)
        if entity is None:
            return ResolutionResult(
                can_execute=False,
                event_type=EventType.MOVEMENT,
                summary="",
                canonical_facts={},
                state_changes={},
                reason="actor not found in world",
            )

        # Check zone capacity (only if to_zone is specified)
        if to_zone:
            zone = world.get_zone(to_location, to_zone)
            if zone is not None and len(zone.occupant_ids) >= zone.capacity:
                return ResolutionResult(
                    can_execute=False,
                    event_type=EventType.MOVEMENT,
                    summary="",
                    canonical_facts={},
                    state_changes={},
                    reason=f"zone {to_zone} is at capacity ({zone.capacity})",
                )

        if to_zone:
            summary = f"{action.actor_id} 移动到 {to_location}.{to_zone}"
        else:
            summary = f"{action.actor_id} 移动到 {to_location}"
        return ResolutionResult(
            can_execute=True,
            event_type=EventType.MOVEMENT,
            summary=summary,
            canonical_facts={"to_location": to_location, "to_zone": to_zone},
            state_changes={
                "actor_id": action.actor_id,
                "new_location": to_location,
                "new_zone": to_zone,
            },
        )

    # ------------------------------------------------------------------
    # Physical
    # ------------------------------------------------------------------

    def _resolve_physical(self, action: Action, world: WorldState) -> ResolutionResult:
        verb = action.params.get("verb", "act")
        target = f" 对 {action.target_id}" if action.target_id else ""
        summary = f"{action.actor_id}{target} {verb}"

        # Simple physical actions go directly without skill check
        if verb in ("look", "carry", "wait", "defend", "dodge"):
            return ResolutionResult(
                can_execute=True,
                event_type=EventType.PHYSICAL,
                summary=summary,
                canonical_facts={"verb": verb},
                state_changes={},
            )

        # Skill-based physical actions: steal, sneak, climb
        if verb in ("steal", "sneak", "climb"):
            return self._resolve_skill_physical(action, world, verb, summary)

        # Other physical actions need Arbiter
        return ResolutionResult(
            can_execute=True,
            event_type=EventType.PHYSICAL,
            summary=summary,
            canonical_facts={"verb": verb},
            state_changes={},
            requires_arbiter=True,
            reason=f"{verb} requires Arbiter for skill check",
        )

    def _resolve_skill_physical(
        self, action: Action, world: WorldState, verb: str, summary: str
    ) -> ResolutionResult:
        """Resolve skill-based physical actions (steal, sneak, climb)."""
        actor = world.get_entity(action.actor_id)
        if actor is None:
            return ResolutionResult(
                can_execute=False,
                event_type=EventType.PHYSICAL,
                summary="",
                canonical_facts={"verb": verb},
                state_changes={},
                reason="actor not found in world",
            )

        # Base score from actor dexterity
        score = actor.attributes.get("dexterity", 0.5)

        # Environment modifiers
        env_mod = 0.0
        zone = world.get_zone(actor.location_id, actor.zone_id) if actor.zone_id else None
        if zone is not None:
            zone_id = zone.zone_id
            if "noisy" in zone_id:
                env_mod += 0.2
            if "dark" in zone_id:
                env_mod += 0.15
            if "crowded" in zone_id:
                env_mod += 0.1
            if "quiet" in zone_id:
                env_mod -= 0.1

        score += env_mod

        # Compute DC
        if verb in ("steal", "sneak"):
            # Need a target entity for steal/sneak
            target_id = action.target_id or action.params.get("target")
            if target_id:
                target_entity = world.get_entity(target_id)
                if target_entity is not None:
                    dc = 0.5 + (
                        target_entity.attributes.get("perception", 0.5)
                        - actor.attributes.get("dexterity", 0.5)
                    ) / 2
                else:
                    dc = 0.5
            else:
                dc = 0.5
        else:  # climb
            dc = 0.5

        margin = score - dc

        if margin > 0.2:
            outcome = "success"
            stamina_delta = -5
        elif margin >= -0.2:
            outcome = "partial"
            stamina_delta = -10
        else:
            outcome = "failure"
            stamina_delta = -15

        return ResolutionResult(
            can_execute=True,
            event_type=EventType.PHYSICAL,
            summary=f"{summary} ({outcome})",
            canonical_facts={"verb": verb, "outcome": outcome, "margin": margin},
            state_changes={"stamina_delta": stamina_delta},
        )

    # ------------------------------------------------------------------
    # Social / Combat
    # ------------------------------------------------------------------

    # Social verbs that are an outcome contest (need the LLM Arbiter). Friendly
    # verbs like greet/chat resolve directly so the arbiter's meta-hint never
    # leaks to the player. (P0.4)
    CONTEST_SOCIAL_VERBS = {"persuade", "deceive", "bribe", "intimidate", "threaten"}

    def _resolve_social(self, action: Action, world: WorldState) -> ResolutionResult:
        verb = action.params.get("verb", "act")
        target = f" 对 {action.target_id}" if action.target_id else ""

        if verb not in self.CONTEST_SOCIAL_VERBS:
            # Greeting / chitchat: a plain social exchange, no skill contest.
            return ResolutionResult(
                can_execute=True,
                event_type=EventType.SOCIAL,
                summary=f"{action.actor_id}{target} {verb}",
                canonical_facts={"verb": verb},
                state_changes={},
                requires_arbiter=False,
                reason="friendly social action resolved directly",
            )

        return ResolutionResult(
            can_execute=True,
            event_type=EventType.SOCIAL,
            summary=f"{action.actor_id}{target} 尝试 {verb}",
            canonical_facts={"verb": verb},
            state_changes={},
            requires_arbiter=True,
            reason="contest social action requires Arbiter",
        )

    def _resolve_combat(self, action: Action, world: WorldState) -> ResolutionResult:
        verb = action.params.get("verb", "act")
        target = f" {action.target_id}" if action.target_id else ""
        summary = f"{action.actor_id}{target} {verb}"
        return ResolutionResult(
            can_execute=True,
            event_type=EventType.COMBAT,
            summary=summary,
            canonical_facts={"verb": verb},
            state_changes={},
            requires_arbiter=False,  # Combat goes to Combat Subsystem, not Arbiter
            reason="combat actions go to Combat Subsystem",
        )

    def _resolve_wait(self, action: Action, world: WorldState) -> ResolutionResult:
        return ResolutionResult(
            can_execute=True,
            event_type=EventType.SYSTEM,
            summary=f"{action.actor_id} 等待",
            canonical_facts={},
            state_changes={},
        )

    def _resolve_unknown(self, action: Action, world: WorldState) -> ResolutionResult:
        return ResolutionResult(
            can_execute=False,
            event_type=EventType.SYSTEM,
            summary="",
            canonical_facts={},
            state_changes={},
            reason=f"unknown action type: {action.action_type}",
        )
