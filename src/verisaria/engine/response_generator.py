"""Response Generator: turn Events into player-facing narrative text.

Rule-driven templates (no LLM), styled by Content Pack's style_guide.
Phase-12 minimal version: one narrative sentence per Event, plus
state-change summaries.
"""

from __future__ import annotations

from typing import Any

from verisaria.engine.schemas import Event, EventType
from verisaria.engine.world import WorldState


# ---------------------------------------------------------------------------
# Default templates
# ---------------------------------------------------------------------------

_DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    "physical": {
        "look": "{actor}环顾四周。",
        "wait": "{actor}静观其变。",
        "steal": "{actor}试图从{target}身上偷走什么。",
        "climb": "{actor}开始攀爬。",
        "carry": "{actor}搬起了某样东西。",
        "default": "{actor}做出了一个动作。",
    },
    "movement": {
        "move": "{actor}移动到了{to_location}。",
        "enter": "{actor}进入了{to_location}。",
        "leave": "{actor}离开了。",
        "default": "{actor}改变了位置。",
    },
    "speech": {
        "talk": "{actor}{target_prefix}说：「{content}」",
        "shout": "{actor}{target_prefix}大声喊道：「{content}」",
        "whisper": "{actor}{target_prefix}说：「{content}」",
        "default": "{actor}{target_prefix}开口道：「{content}」",
    },
    "social": {
        "persuade": "{actor}试图说服{target}。",
        "intimidate": "{actor}试图恐吓{target}。",
        "deceive": "{actor}试图欺骗{target}。",
        "bribe": "{actor}试图贿赂{target}。",
        "default": "{actor}与{target}进行社交互动。",
    },
    "combat": {
        "attack": "{actor}向{target}发起攻击，造成{damage}点伤害。",
        "defend": "{actor}采取防御姿态，严阵以待。",
        "dodge": "{actor}身形一闪，准备躲避来袭。",
        "dodge_stance": "{actor}绷紧神经，准备闪避。",
        "flee": "{actor}转身试图脱离战斗！",
        "flee_fail": "{actor}试图逃跑，但没能成功。",
        "combat_hit": "{attacker}击中{defender}，造成{damage}点伤害。",
        "combat_defend": "{actor}摆出防御架势。",
        "combat_dodge": "{defender}灵巧地闪过了{attacker}的攻击！",
        "combat_dodge_fail": "{defender}试图闪避，但没能完全躲开。",
        "combat_dodge_stance": "{actor}绷紧神经，准备闪避。",
        "combat_fail": "{actor}试图行动，但体力不足。",
        "combat_flee": "{actor}成功脱离战斗，消失在视线中。",
        "combat_flee_fail": "{actor}试图逃离，但被拦住了。",
        "incapacitated": "{entity}支撑不住，倒在了地上。",
        "combat_end": "战斗结束了。{reason}",
        "default": "{actor}在战斗中行动。",
    },
    "system": {
        "default": "",
    },
}

# English fallbacks
_ENGLISH_TEMPLATES: dict[str, dict[str, str]] = {
    "physical": {
        "look": "{actor} looks around.",
        "wait": "{actor} waits.",
        "steal": "{actor} attempts to steal from {target}.",
        "climb": "{actor} starts climbing.",
        "carry": "{actor} picks something up.",
        "default": "{actor} makes a move.",
    },
    "movement": {
        "move": "{actor} moves to {to_location}.",
        "enter": "{actor} enters {to_location}.",
        "leave": "{actor} leaves.",
        "default": "{actor} changes position.",
    },
    "speech": {
        "talk": '{actor}{target_prefix} says: "{content}"',
        "shout": '{actor}{target_prefix} shouts: "{content}"',
        "whisper": '{actor}{target_prefix} whispers: "{content}"',
        "default": '{actor}{target_prefix} speaks: "{content}"',
    },
    "social": {
        "persuade": "{actor} tries to persuade {target}.",
        "intimidate": "{actor} tries to intimidate {target}.",
        "deceive": "{actor} tries to deceive {target}.",
        "bribe": "{actor} tries to bribe {target}.",
        "default": "{actor} socially interacts with {target}.",
    },
    "combat": {
        "attack": "{actor} attacks {target} for {damage} damage.",
        "defend": "{actor} takes a defensive stance.",
        "dodge": "{actor} dodges nimbly.",
        "dodge_stance": "{actor} prepares to dodge.",
        "flee": "{actor} tries to flee!",
        "flee_fail": "{actor} tries to flee but fails.",
        "combat_hit": "{attacker} hits {defender} for {damage} damage.",
        "combat_defend": "{actor} raises their guard.",
        "combat_dodge": "{defender} dodges {attacker}'s attack!",
        "combat_dodge_fail": "{defender} fails to dodge completely.",
        "combat_dodge_stance": "{actor} prepares to dodge.",
        "combat_fail": "{actor} tries to act but lacks stamina.",
        "combat_flee": "{actor} flees successfully.",
        "combat_flee_fail": "{actor} is blocked from fleeing.",
        "incapacitated": "{entity} collapses, incapacitated.",
        "combat_end": "The battle ends. {reason}",
        "default": "{actor} acts in combat.",
    },
    "system": {
        "default": "",
    },
}


# ---------------------------------------------------------------------------
# Response Generator
# ---------------------------------------------------------------------------

class ResponseGenerator:
    """Generate player-facing narrative from Events.

    Rule-driven templates keyed by (event_type, verb/sub_type).
    Style guide from Content Pack can tweak tone/voice.
    """

    def __init__(self, style_guide: dict[str, Any] | None = None) -> None:
        self.style_guide = style_guide or {}
        self._templates = self._select_templates()

    def _select_templates(self) -> dict[str, dict[str, str]]:
        """Pick Chinese or English templates based on style guide."""
        voice = self.style_guide.get("narrative_voice", "")
        if voice and ("中文" in voice or "中文" in voice or "第三人称" in voice):
            return _DEFAULT_TEMPLATES
        # Default to Chinese for this project; English available as fallback
        return _DEFAULT_TEMPLATES

    # NPC idle actions that are noise and should never be narrated to the player —
    # an idle "look" (every bystander "环顾四周" each tick) drowns the scene in filler
    # and, stacked on persuasion that didn't land, makes the pacing read dead (audit 5 #3).
    _NPC_NOISE_VERBS = {"wait", "look"}

    def generate(
        self,
        events: list[Event],
        world: WorldState,
        player_id: str,
        viewer_location: str | None = None,
        skip_speech_actors: set[str] | None = None,
    ) -> str:
        """Produce a narrative paragraph from a list of Events.

        The player sees their own events always, plus other actors' events in
        their location. ``viewer_location`` is the player's location used for
        that filter (defaults to their current location).

        Movement tick rule (P1.4 / P1.6): on a tick where the player moves, a
        mid-transit player is neither at the origin nor the destination, so
        *no other actor's* events are narrated this tick — only the player's own
        movement. Whatever an NPC said is heard on the next tick once the player
        has actually arrived. This is the one model that avoids both pre-seeing
        the destination and re-hearing the origin you just left.
        """
        if not events:
            return "时间悄然流逝……"

        # Speech already rendered elsewhere this tick (streamed live to the player,
        # and the player's own line they just typed) is dropped from the assembled
        # narrative to avoid double-printing (PLAY-1 streaming).
        if skip_speech_actors:
            events = [
                e for e in events
                if not (e.event_type == EventType.SPEECH and e.actor_id in skip_speech_actors)
            ]
            if not events:
                return ""

        if viewer_location is None:
            player = world.get_entity(player_id)
            viewer_location = player.location_id if player else None

        # Is the player moving this tick? If so, suppress other actors entirely.
        player_is_moving = any(
            e.actor_id == player_id and e.event_type == EventType.MOVEMENT
            for e in events
        )

        paragraphs: list[str] = []
        for event in events:
            if event.actor_id != player_id:
                if player_is_moving:
                    continue  # mid-transit: hear no one else this tick
                # Others only visible if in the player's location.
                if viewer_location is None or event.location_id != viewer_location:
                    continue
                # An NPC's idle wait is noise — never surface it to the player.
                verb = (event.canonical_facts or {}).get("verb", "")
                if verb in self._NPC_NOISE_VERBS:
                    continue
            text = self._format_event(event, world, player_id)
            if text:
                paragraphs.append(text)

        if not paragraphs:
            return "什么也没发生。"

        return "\n".join(paragraphs)

    def _format_event(
        self,
        event: Event,
        world: WorldState,
        player_id: str,
    ) -> str:
        """Format a single Event into narrative text."""
        etype = event.event_type.value
        facts = event.canonical_facts or {}
        verb = facts.get("verb", "")
        sub_type = facts.get("sub_type", "")

        # Combat events use sub_type as the verb key
        lookup_key = sub_type if etype == "combat" and sub_type else verb

        templates = self._templates.get(etype, self._templates.get("system", {}))
        template = templates.get(lookup_key, templates.get("default", ""))

        if not template:
            # Fallback to event summary if no template matches
            return event.summary

        # Build substitution dict
        # Use canonical_facts actor_id if present (combat events store real actor there)
        actor_id = facts.get("actor_id", event.actor_id)
        ctx: dict[str, str] = {
            "actor": self._display_name(actor_id, world, player_id),
            "target": self._display_name(event.target_id, world, player_id) if event.target_id else "",
            "attacker": self._display_name(facts.get("attacker", ""), world, player_id),
            "defender": self._display_name(facts.get("defender", ""), world, player_id),
            "entity": self._display_name(facts.get("entity_id", ""), world, player_id),
            "to_location": facts.get("to_location", ""),
            "content": facts.get("content", ""),
            "damage": str(facts.get("damage", "")),
            "reason": facts.get("reason", ""),
        }

        # Target prefix for speech
        if etype == "speech" and event.target_id:
            ctx["target_prefix"] = f"对{ctx['target']}"
        else:
            ctx["target_prefix"] = ""

        # Volume prefix for speech
        volume = facts.get("volume", "")
        if volume == "low":
            ctx["target_prefix"] = "低声" + ctx["target_prefix"]
        elif volume == "loud":
            ctx["target_prefix"] = "大声" + ctx["target_prefix"]

        try:
            return template.format(**ctx)
        except KeyError:
            # Missing template variable — fall back to summary
            return event.summary

    @staticmethod
    def _display_name(entity_id: str, world: WorldState, player_id: str) -> str:
        """Return a display name for an entity."""
        if not entity_id:
            return ""
        if entity_id == player_id:
            return "你"
        # Prefer the pack-declared display name; else id-derived.
        ent = world.get_entity(entity_id) if world is not None else None
        if ent is not None and getattr(ent, "name", ""):
            return ent.name
        return entity_id.replace("npc.", "").replace("_", " ")

    # ------------------------------------------------------------------
    # State change summary
    # ------------------------------------------------------------------

    def generate_status_delta(
        self,
        before: dict[str, Any],
        after: dict[str, Any],
    ) -> str | None:
        """Compare two player state snapshots and describe changes."""
        changes: list[str] = []

        hp_before = before.get("hp")
        hp_after = after.get("hp")
        if hp_before is not None and hp_after is not None and hp_before != hp_after:
            delta = hp_after - hp_before
            if delta < 0:
                changes.append(f"HP -{abs(delta)}")
            else:
                changes.append(f"HP +{delta}")

        loc_before = before.get("location_id")
        loc_after = after.get("location_id")
        if loc_before and loc_after and loc_before != loc_after:
            changes.append(f"位置变更: {loc_before} → {loc_after}")

        if not changes:
            return None
        return "状态变化: " + ", ".join(changes)
