"""Intent Parser: raw input → ParsedIntent or ClarificationRequest.

Also includes:
- Input Normalizer: cleans and standardizes raw input from any source
- Prompt Loader: loads versioned prompts from content/prompts/
- Coherence Checker: validates intent against world state

Responsibilities:
- Load versioned prompts from content/prompts/
- Call LLM via Orchestrator
- Validate output against ParsedIntent schema
- Run Coherence Check against world state
- Handle ambiguous input by returning ClarificationRequest
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from verisaria.engine.coherence import CoherenceChecker, CoherenceIssue
from verisaria.engine.llm import LLMCallRequest, LLMOrchestrator
from verisaria.engine.schemas import ParsedIntent
from verisaria.engine.world import WorldState


# Diagnostics for parse failures (the opaque "我没理解" otherwise hides the cause).
# Silent unless a handler is attached (CLI/TUI --log).
_ilog = logging.getLogger("verisaria.intent")


# ---------------------------------------------------------------------------
# Raw Input (normalized)
# ---------------------------------------------------------------------------

@dataclass
class RawInput:
    source: str  # "natural_language" | "quick_command" | "ui_interaction" | "mixed"
    raw_text: str
    context: dict[str, Any]
    command: str | None = None
    args: list[str] | None = None
    target: str | None = None
    action: str | None = None
    content: str | None = None


# ---------------------------------------------------------------------------
# Input Normalizer
# ---------------------------------------------------------------------------

class InputNormalizer:
    """Normalize raw input from any source into a standard RawInput structure.

    Supports:
    - Natural language (pass-through)
    - Quick commands (/look, /move, /whisper, /say, /examine, /attack)
    - UI interactions (structured data)
    """

    QUICK_COMMANDS: dict[str, dict[str, Any]] = {
        "/look": {"intent_type": "look", "params": {}},
        "/examine": {"intent_type": "look", "params": {}},
        "/move": {"intent_type": "movement", "params_key": "destination"},
        "/go": {"intent_type": "movement", "params_key": "destination"},
        "/whisper": {
            "intent_type": "speech",
            "params_keys": ["target", "content"],
            "modifiers": {"volume": "low"},
        },
        "/say": {
            "intent_type": "speech",
            "params_keys": ["target", "content"],
            "modifiers": {"volume": "normal"},
        },
        "/shout": {
            "intent_type": "speech",
            "params_keys": ["target", "content"],
            "modifiers": {"volume": "loud"},
        },
        "/attack": {"intent_type": "combat", "params_keys": ["target"]},
        "/steal": {"intent_type": "physical", "params_keys": ["target"]},
        "/persuade": {"intent_type": "social", "params_keys": ["target", "content"]},
    }

    def normalize(
        self,
        raw_text: str,
        source: str = "natural_language",
        actor_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> RawInput:
        """Normalize any raw input into RawInput."""
        ctx = context or {}
        ctx["actor_id"] = actor_id

        if source == "quick_command" or raw_text.strip().startswith("/"):
            return self._parse_quick_command(raw_text, ctx)

        if source == "ui_interaction":
            return RawInput(
                source="ui_interaction",
                raw_text=raw_text,
                context=ctx,
            )

        return RawInput(
            source="natural_language",
            raw_text=raw_text.strip(),
            context=ctx,
        )

    def _parse_quick_command(self, raw_text: str, context: dict[str, Any]) -> RawInput:
        parts = raw_text.strip().split()
        command = parts[0].lower() if parts else ""
        args = parts[1:] if len(parts) > 1 else []

        cmd_def = self.QUICK_COMMANDS.get(command, {})

        # Build normalized raw_text from template
        intent_type = cmd_def.get("intent_type", "unknown")
        modifiers = dict(cmd_def.get("modifiers", {}))

        # Extract target/content from args based on params_keys
        params_keys = cmd_def.get("params_keys", [])
        target = args[0] if len(args) > 0 and "target" in params_keys else None
        content = " ".join(args[1:]) if len(args) > 1 and "content" in params_keys else None
        if "content" in params_keys and len(args) == 1:
            content = args[0]  # single arg is content (e.g. /examine sword)
            target = args[0]

        # Reconstruct natural language-like raw_text for parser
        reconstructed = self._reconstruct_text(intent_type, target, content, modifiers)

        return RawInput(
            source="quick_command",
            raw_text=reconstructed,
            context={
                **context,
                "command": command,
                "args": args,
                "intent_type": intent_type,
                "modifiers": modifiers,
            },
            command=command,
            args=args,
            target=target,
            content=content,
        )

    def _reconstruct_text(
        self,
        intent_type: str,
        target: str | None,
        content: str | None,
        modifiers: dict[str, Any],
    ) -> str:
        """Reconstruct a natural language-like text from quick command parts."""
        volume = modifiers.get("volume", "")
        vol_prefix = {"low": "低声", "normal": "", "loud": "大声"}.get(volume, "")

        if intent_type == "look":
            return f"看看{target or '周围'}"
        if intent_type == "movement":
            return f"去{target or ''}"
        if intent_type == "speech":
            return f"{vol_prefix}对{target or ''}说{content or ''}"
        if intent_type == "combat":
            return f"攻击{target or ''}"
        if intent_type == "physical":
            return f"偷{target or ''}"
        if intent_type == "social":
            return f"说服{target or ''}{content or ''}"
        return ""


# ---------------------------------------------------------------------------
# Clarification Request
# ---------------------------------------------------------------------------

@dataclass
class ClarificationRequest:
    request_id: str
    original_input: str
    clarifying_question: str
    options: list[str] | None = None
    ambiguity_type: str = ""  # e.g. "target", "action", "pronoun"


# ---------------------------------------------------------------------------
# Prompt Loader
# ---------------------------------------------------------------------------

class PromptLoader:
    """Load versioned prompt files from content/prompts/."""

    def __init__(self, prompts_root: str | Path = "content/prompts") -> None:
        self.prompts_root = Path(prompts_root)

    def load(self, task_type: str, version: str = "v1.0.0") -> str:
        path = self.prompts_root / task_type / f"{version}.md"
        if not path.exists():
            raise FileNotFoundError(f"Prompt not found: {path}")
        return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Intent Parser
# ---------------------------------------------------------------------------

class IntentParser:
    """Parse raw player/NPC input into structured intents."""

    def __init__(
        self,
        llm_orchestrator: LLMOrchestrator,
        prompt_loader: PromptLoader | None = None,
        coherence_checker: CoherenceChecker | None = None,
    ) -> None:
        self.llm = llm_orchestrator
        self.prompts = prompt_loader or PromptLoader()
        self.coherence = coherence_checker or CoherenceChecker()
        self._seq = 0

    def parse(
        self,
        raw_text: str,
        actor_id: str,
        tick: int,
        world: WorldState,
        context: dict[str, Any] | None = None,
        skip_ambiguity_check: bool = False,
    ) -> ParsedIntent | ClarificationRequest:
        """Parse a raw input string into a ParsedIntent.

        If the input is ambiguous, returns a ClarificationRequest instead.
        When skip_ambiguity_check=True, ambiguities are ignored (used after
        player explicitly chooses to proceed with a clarification).
        """
        self._seq += 1
        request_id = f"req_parse_{tick}_{self._seq}"

        # Load prompt template
        try:
            prompt_template = self.prompts.load("parse_player_intent", "v1.0.0")
        except FileNotFoundError:
            # Fallback: use a minimal built-in prompt for testing
            prompt_template = self._fallback_prompt()

        # Enrich context with entity + location lists from world state. The
        # location list stops the LLM inventing destinations (P1.5).
        enriched_context = dict(context or {})
        if "entities" not in enriched_context and world is not None:
            enriched_context["entities"] = [
                {"entity_id": eid, "name": eid.replace("npc.", "").replace("_", " ")}
                for eid in world.entities
            ]
        if "locations" not in enriched_context and world is not None:
            enriched_context["locations"] = [
                {"location_id": lid} for lid in world.locations
            ]

        # Build prompt with context
        prompt = self._build_prompt(prompt_template, raw_text, actor_id, enriched_context)

        # Call LLM
        result = self.llm.call(
            LLMCallRequest(
                task_type="parse_player_intent",
                prompt=prompt,
                schema_model=ParsedIntent,
                model_preference="ollama",
            )
        )

        if not result.success:
            # The opaque "我没理解" is a dead end for diagnosis — trace the REAL
            # cause (budget / connection / json / schema / empty) so a --log run
            # shows WHY parsing failed instead of just that it did.
            _ilog.warning(
                "parse failed for %r → %s [%s]", raw_text, result.error,
                getattr(result.error_category, "value", result.error_category),
            )
            # If LLM fails, return a clarification asking to rephrase
            return ClarificationRequest(
                request_id=request_id,
                original_input=raw_text,
                clarifying_question="我没理解你的意思，能再描述一下吗？",
                ambiguity_type="parse_failed",
            )

        intent = ParsedIntent.model_validate(result.data)

        # Extract active_conversation from context for pronoun resolution
        active_conversation = enriched_context.get("active_conversation")

        # Resolve target_ref to target_id if missing
        intent = self._resolve_target_ref(intent, world, active_conversation)

        # Resolve a movement destination from a natural-language ref (P0.3),
        # e.g. "广场" → town_square, clearing any ambiguity it raised.
        intent = self._resolve_movement_location(intent, world)

        # A SPEECH whose target the LLM bound to an ABSENT NPC because the sentence
        # mentioned that NPC's *domain* ("对在场的祭主说，提到'定罪'（征瓷使的职掌）"
        # → target → absent 征瓷使) — retarget to the present addressee instead of
        # dead-ending the whole turn with a spatial-mismatch (grand-integration).
        intent = self._retarget_absent_speech_to_present_partner(
            intent, world, active_conversation, raw_text)

        # If target_id is set and target_ref exists, remove ambiguities
        # that correspond to the resolved target (whether resolved by LLM
        # or by _resolve_target_ref).
        if intent.target_id is not None and intent.target_ref:
            ref_lower = intent.target_ref.lower()
            intent.ambiguities = [
                a for a in intent.ambiguities
                if a.lower() not in ref_lower and ref_lower not in a.lower()
            ]

        # A clearly-named entity flagged as an "ambiguity" isn't actually ambiguous —
        # e.g. "去栈桥找莉拉" (the NPC mention shouldn't pop a destination menu) or
        # "对莉拉说…当着梅档案官的面…" (the third party shouldn't hijack the turn).
        # Drop ambiguities that uniquely name a known entity; pronouns/deictics stay.
        if intent.ambiguities and world is not None:
            intent.ambiguities = [
                a for a in intent.ambiguities
                if not self._uniquely_names_entity(a, world)
            ]

        # P1.5: a movement that is still uncertain — either its destination can't
        # be resolved to a real location (the LLM invented "市场"), or a leftover
        # ambiguity remains (the LLM substituted a valid id but flagged the named
        # place, e.g. 去铁匠铺) — gets a clarification that lists the actual
        # reachable locations, not the useless generic "尝试执行 / 取消动作".
        if (
            not skip_ambiguity_check
            and intent.intent_type.value == "movement"
            and world is not None
        ):
            dest = (intent.modifiers or {}).get("to_location")
            if dest not in world.locations:
                # Last-ditch: a location whose display name sits verbatim in the raw
                # input the LLM didn't extract cleanly ("我返回征船听证台找林槐" →
                # pump_gate). Go there instead of bouncing to a menu (audit F3).
                scanned = self._scan_raw_for_location(raw_text, world)
                if scanned is not None:
                    mods = dict(intent.modifiers or {})
                    mods["to_location"] = scanned
                    intent.modifiers = mods
                    intent.ambiguities = []
                    dest = scanned
            if dest not in world.locations or intent.ambiguities:
                return ClarificationRequest(
                    request_id=request_id,
                    original_input=raw_text,
                    clarifying_question="去不了那里。你想去哪个地方？",
                    # Display NAMES, not raw ids (audit #4). A chosen name resolves
                    # back through _match_location (which now matches names), so the
                    # round-trip stays deterministic.
                    options=[*[world.location_label(lid)
                               for lid in sorted(world.locations.keys())], "取消动作"],
                    ambiguity_type="movement_destination",
                )

        # P0.2: auto-resolve a personal-pronoun ambiguity to the single
        # obvious candidate (conversation partner or the only nearby actor),
        # so we execute directly instead of asking a needless question.
        if not skip_ambiguity_check:
            intent = self._auto_resolve_single_candidate(intent, world, active_conversation)

        # Check for ambiguities in the parsed intent
        if intent.ambiguities and not skip_ambiguity_check:
            return self._build_clarification(request_id, raw_text, intent, world, active_conversation)

        # Coherence check
        issues = self.coherence.check(intent, world)
        errors = [i for i in issues if i.severity == "error"]
        if errors:
            return ClarificationRequest(
                request_id=request_id,
                original_input=raw_text,
                clarifying_question=f"输入存在矛盾: {'; '.join(e.message for e in errors)}",
                ambiguity_type="coherence_error",
            )

        return intent

    @staticmethod
    def _uniquely_names_entity(ref: str, world: WorldState) -> bool:
        """Whether ``ref`` clearly names exactly one known entity OR location (a
        proper name like '梅档案官' / '旧检修梯', or a location id), as opposed to a
        pronoun/deictic (他/她/你/这/那), which stays genuinely ambiguous."""
        r = (ref or "").strip()
        if not r or any(p in r for p in ("他", "她", "它", "你", "我", "这", "那", "TA")):
            return False
        matches: set[str] = {
            eid for eid, e in world.entities.items()
            if (getattr(e, "name", "") or "") and
            ((e.name == r) or (r in e.name) or (e.name in r))
        }
        for lid, loc in world.locations.items():
            name = getattr(loc, "name", "") or ""
            if lid == r or (name and ((name == r) or (r in name) or (name in r))):
                matches.add(lid)
        return len(matches) == 1

    @staticmethod
    def _resolve_target_ref(
        intent: ParsedIntent,
        world: WorldState,
        active_conversation: dict[str, Any] | None = None,
    ) -> ParsedIntent:
        """If target_id is missing but target_ref is present, try to resolve it."""
        if intent.target_id is not None or not intent.target_ref:
            return intent

        ref = intent.target_ref.strip().lower()

        # Pronoun resolution via active_conversation
        if active_conversation and ref in ("她", "他", "你"):
            participants = active_conversation.get("participants", [])
            other_participants = [p for p in participants if p != intent.actor_id]
            if len(other_participants) == 1:
                intent.target_id = other_participants[0]
                return intent

        # Exact match on entity_id
        for eid in world.entities:
            if eid.lower() == ref:
                intent.target_id = eid
                return intent

        # Match without npc. prefix
        for eid in world.entities:
            bare = eid.lower().replace("npc.", "")
            if bare == ref:
                intent.target_id = eid
                return intent

        # Exact match on the entity's DISPLAY name (e.g. "奥罗医师" → npc.clinician_oro).
        # Without this a display-name address the LLM left unresolved stays targetless,
        # and the uniquely-names-entity ambiguity filter would then silently drop it.
        for eid, e in world.entities.items():
            name = (getattr(e, "name", "") or "").lower()
            if name and name == ref:
                intent.target_id = eid
                return intent

        # Substring match on the display name
        for eid, e in world.entities.items():
            name = (getattr(e, "name", "") or "").lower()
            if name and (ref in name or name in ref):
                intent.target_id = eid
                return intent

        # Substring match
        for eid in world.entities:
            bare = eid.lower().replace("npc.", "")
            if ref in bare or bare in ref:
                intent.target_id = eid
                return intent

        # Fuzzy match via Levenshtein distance (allow 1 typo for short names)
        def _levenshtein(a: str, b: str) -> int:
            m, n = len(a), len(b)
            if m < n:
                return _levenshtein(b, a)
            if n == 0:
                return m
            prev = list(range(n + 1))
            for i in range(1, m + 1):
                curr = [i]
                for j in range(1, n + 1):
                    cost = 0 if a[i - 1] == b[j - 1] else 1
                    curr.append(min(curr[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
                prev = curr
            return prev[n]

        best_match = None
        best_dist = float("inf")
        for eid in world.entities:
            bare = eid.lower().replace("npc.", "")
            dist = _levenshtein(ref, bare)
            if dist < best_dist:
                best_dist = dist
                best_match = eid

        # Allow up to 2 edits for names >= 3 chars, 1 edit for shorter
        threshold = 2 if len(ref) >= 3 else 1
        if best_match and best_dist <= threshold:
            intent.target_id = best_match

        return intent

    @staticmethod
    def _retarget_absent_speech_to_present_partner(
        intent: ParsedIntent,
        world: WorldState,
        active_conversation: dict[str, Any] | None,
        raw_text: str,
    ) -> ParsedIntent:
        """A SPEECH the LLM bound to an ABSENT NPC — almost always because the
        sentence mentioned that NPC's domain ("提到'定罪'" → 征瓷使) rather than
        addressing them — is retargeted to the present addressee (the co-located
        conversation partner, or the only co-located NPC). Skipped when the absent
        target is named verbatim, so an explicit "let X (elsewhere) know…" is kept.
        Without this, _check_spatial hard-rejects the turn and dead-ends the line."""
        if intent.intent_type.value != "speech" or not intent.target_id:
            return intent
        if not intent.target_id.startswith("npc."):
            return intent
        actor = world.get_entity(intent.actor_id)
        target = world.get_entity(intent.target_id)
        if actor is None or target is None or target.location_id == actor.location_id:
            return intent  # targetless / target already present → nothing to fix

        rt = raw_text or ""
        bare = intent.target_id.replace("npc.", "")
        tname = getattr(target, "name", "") or ""
        if intent.target_id in rt or bare in rt or (tname and tname in rt):
            return intent  # explicitly addressed by name → respect the cross-location intent

        present = [
            eid for eid, e in world.entities.items()
            if eid != intent.actor_id and getattr(e, "entity_type", "") == "npc"
            and e.location_id == actor.location_id
        ]
        chosen = None
        if active_conversation:
            parts = [p for p in active_conversation.get("participants", []) if p in present]
            if len(parts) == 1:
                chosen = parts[0]
        if chosen is None and len(present) == 1:
            chosen = present[0]
        if chosen is not None:
            intent.target_id = chosen
        return intent

    def _build_prompt(
        self,
        template: str,
        raw_text: str,
        actor_id: str,
        context: dict[str, Any],
    ) -> str:
        # Format entities list for the prompt template
        entities_list = ""
        entities = context.get("entities", [])
        if entities:
            lines = []
            for ent in entities:
                eid = ent.get("entity_id", "")
                name = ent.get("name", eid)
                lines.append(f"  - {name} → entity_id: `{eid}`")
            entities_list = "\n".join(lines)
        else:
            entities_list = "  (none)"

        # Format locations list (P1.5: keep the LLM from inventing destinations).
        locations = context.get("locations", [])
        if locations:
            locations_list = "\n".join(
                f"  - `{loc.get('location_id', '')}`" for loc in locations
            )
        else:
            locations_list = "  (none)"

        prompt = template.replace("{entities_list}", entities_list)
        prompt = prompt.replace("{locations_list}", locations_list)

        # Inject active_conversation context for pronoun resolution
        active_conversation = context.get("active_conversation")
        if active_conversation:
            participants = active_conversation.get("participants", [])
            other_participants = [p for p in participants if p != actor_id]
            if other_participants:
                other_str = ", ".join(other_participants)
                prompt += f"\n\nYou are currently in a conversation with: {other_str}."
                last_speaker = active_conversation.get("last_speaker", "")
                last_content = active_conversation.get("last_content", "")
                if last_speaker and last_content:
                    prompt += f" Recent context: {last_speaker} said '{last_content}'."

        # Remove the entities from context to avoid duplication
        context_copy = {k: v for k, v in context.items() if k != "entities"}
        ctx_json = json.dumps(context_copy, ensure_ascii=False, indent=2)

        return f"""{prompt}

---

## Current Context

Actor: {actor_id}
Context: {ctx_json}

## Input to Parse

"{raw_text}"

## Output

Return ONLY a JSON object matching the ParsedIntent schema."""

    def _fallback_prompt(self) -> str:
        """Minimal fallback prompt when file-based prompts are unavailable."""
        return """Parse the player's input into JSON with these fields:
- intent_type: speech | movement | physical | social | combat | look | wait
- commitment: considering | preparing | attempting | committed
- performed_content: what NPCs can observe
- player_intent_note: hidden motive (or null)
- ambiguities: list of unclear references"""

    def _build_clarification(
        self,
        request_id: str,
        raw_text: str,
        intent: ParsedIntent,
        world: WorldState | None = None,
        active_conversation: dict[str, Any] | None = None,
    ) -> ClarificationRequest:
        ambiguity = intent.ambiguities[0] if intent.ambiguities else "输入"
        question = f"'{ambiguity}' 指代不明，请确认："
        options: list[str] = []
        ambiguity_type = "pronoun_resolution"

        # Try to resolve ambiguity using world state
        resolved_option = None
        if world is not None:
            resolved_option = self._try_resolve_ambiguity(ambiguity, intent, world, active_conversation)

        # Personal-pronoun clarification: build options from real candidates
        # (conversation partner + actors sharing the location), never generic
        # placeholders like "附近的人" the player cannot act on. (P0.2)
        if any(p in ambiguity for p in ("他", "她", "你")):
            candidates = self._candidate_person_targets(intent, world, active_conversation)
            if len(candidates) == 1:
                options = [candidates[0], "取消动作"]
                question = f"'{ambiguity}' 是指 {candidates[0]} 吗？"
            elif len(candidates) >= 2:
                options = [*candidates, "取消动作"]
                question = f"你提到的'{ambiguity}'是指谁？"
            else:
                options = ["取消动作"]
                question = f"附近没有可指代的对象，'{ambiguity}' 是指谁？请换一种说法。"
        elif resolved_option:
            options = [resolved_option, "取消动作"]
            question = f"'{ambiguity}' 是指 {resolved_option} 吗？"
        elif "这里" in ambiguity or "那里" in ambiguity:
            question = f"'{ambiguity}' 具体指哪个位置？"
            options = ["当前位置", "之前提到的位置", "取消动作"]
        elif "它" in ambiguity:
            question = f"'{ambiguity}' 指什么物品或对象？"
            options = ["最近的物品", "取消动作"]
        else:
            # Generic ambiguity — provide catch-all options
            options = ["尝试执行", "取消动作"]
            ambiguity_type = "generic"

        return ClarificationRequest(
            request_id=request_id,
            original_input=raw_text,
            clarifying_question=question,
            options=options,
            ambiguity_type=ambiguity_type,
        )

    # Hard-coded Chinese name mappings for MVP baseline.
    # In production this should come from entity attributes or content pack.
    _CN_NAME_MAP: dict[str, str] = {
        "卫兵": "npc.guard_b",
        "守卫": "npc.guard_b",
        "艾蕾": "npc.ele",
    }

    # Chinese aliases for locations (MVP baseline; production should derive these
    # from the content pack). Used to resolve movement targets the LLM returns in
    # natural language (e.g. "广场" → "town_square"). (P0.3)
    _CN_LOCATION_MAP: dict[str, str] = {
        "广场": "town_square",
        "镇广场": "town_square",
        "酒馆": "tavern",
        "铁匠铺": "blacksmith",
        "铁匠": "blacksmith",
    }

    @classmethod
    def _resolve_movement_location(
        cls, intent: ParsedIntent, world: WorldState
    ) -> ParsedIntent:
        """Resolve a movement intent's destination from a natural-language ref.

        The LLM often puts a location name in ``target_ref`` (sometimes also
        flagging it ambiguous) instead of a clean ``modifiers.to_location``.
        Resolve it against world locations (CN alias → substring → exact) and
        clear the matching ambiguity so the player is not bounced with a needless
        clarification. (P0.3)
        """
        if intent.intent_type.value != "movement" or world is None:
            return intent

        mods = dict(intent.modifiers or {})
        # Already have a valid destination → just clear any stale ambiguity the
        # LLM raised about it (P1.5: 去铁匠铺 → to_location=blacksmith yet still
        # flagged ambiguous). The valid destination wins.
        if mods.get("to_location") in world.locations:
            intent.ambiguities = cls._clear_destination_ambiguity(
                intent.ambiguities, intent.target_ref, mods.get("to_location")
            )
            return intent

        ref = (mods.get("to_location") or intent.target_ref or "").strip()
        if not ref:
            return intent

        resolved = cls._match_location(ref, world)
        if resolved is not None:
            mods["to_location"] = resolved
            intent.modifiers = mods
            intent.ambiguities = cls._clear_destination_ambiguity(
                intent.ambiguities, ref, resolved
            )
        return intent

    @staticmethod
    def _clear_destination_ambiguity(
        ambiguities: list[str], *refs: str | None
    ) -> list[str]:
        """Drop ambiguities that merely name the (now resolved) destination."""
        keys = [r.strip().lower() for r in refs if r]
        return [
            a for a in ambiguities
            if not any(a.lower() in k or k in a.lower() for k in keys if k)
        ]

    @classmethod
    def _match_location(cls, ref: str, world: WorldState) -> str | None:
        """Map a location reference to a real location id, or None. Matches the
        pack-declared DISPLAY NAME as well as the id — the player types "听证台",
        not the internal "pump_gate" (playability audit #3)."""
        r = ref.strip()
        rl = r.lower()

        def _name(lid: str) -> str:
            loc = world.locations.get(lid)
            return (getattr(loc, "name", "") or "").lower()

        # CN alias
        if r in cls._CN_LOCATION_MAP and cls._CN_LOCATION_MAP[r] in world.locations:
            return cls._CN_LOCATION_MAP[r]
        # Exact id or display name
        for lid in world.locations:
            if lid.lower() == rl or _name(lid) == rl:
                return lid
        # Unambiguous substring against id OR display name (mirrors coherence)
        matches = [
            lid for lid in world.locations
            if rl in lid.lower() or lid.lower() in rl
            or (_name(lid) and (rl in _name(lid) or _name(lid) in rl))
        ]
        if len(matches) == 1:
            return matches[0]
        return None

    @staticmethod
    def _scan_raw_for_location(raw_text: str, world: WorldState) -> str | None:
        """A location whose display name appears verbatim in the raw input — a
        last resort for when the LLM didn't extract it into to_location. Returns a
        match only when exactly one location name is present, to stay unambiguous
        (audit F3)."""
        rl = raw_text or ""
        hits = [
            lid for lid, loc in world.locations.items()
            if (getattr(loc, "name", "") or "") and loc.name in rl
        ]
        return hits[0] if len(hits) == 1 else None

    # Personal pronouns that refer to a single nearby actor.
    _PERSON_PRONOUNS: tuple[str, ...] = ("她", "他", "你", "ta", "TA", "Ta")

    @staticmethod
    def _same_location_actors(world: WorldState | None, actor_id: str) -> list[str]:
        """Return entity_ids of other entities sharing the actor's location."""
        if world is None:
            return []
        actor = world.get_entity(actor_id)
        if actor is None:
            return []
        return [
            eid
            for eid, ent in world.entities.items()
            if eid != actor_id and ent.location_id == actor.location_id
        ]

    @classmethod
    def _candidate_person_targets(
        cls,
        intent: ParsedIntent,
        world: WorldState | None,
        active_conversation: dict[str, Any] | None,
    ) -> list[str]:
        """Plausible targets for an ambiguous personal reference.

        Conversation partners come first (most likely referent), followed by
        other actors in the same location. Order-stable and de-duplicated so
        clarification options are deterministic (replay-safe).
        """
        candidates: list[str] = []
        if active_conversation:
            for p in active_conversation.get("participants", []):
                if p != intent.actor_id and p not in candidates:
                    candidates.append(p)
        for eid in cls._same_location_actors(world, intent.actor_id):
            if eid not in candidates:
                candidates.append(eid)
        return candidates

    @classmethod
    def _auto_resolve_single_candidate(
        cls,
        intent: ParsedIntent,
        world: WorldState | None,
        active_conversation: dict[str, Any] | None,
    ) -> ParsedIntent:
        """Bind a personal-pronoun ambiguity to the only plausible target.

        When the ambiguous reference is a personal pronoun and exactly one
        candidate exists (the conversation partner, or the lone actor sharing
        the location), resolve it directly so the action executes instead of
        asking a needless clarifying question. (P0.2)
        """
        if intent.target_id is not None or not intent.ambiguities:
            return intent
        has_pronoun = any(a.strip() in cls._PERSON_PRONOUNS for a in intent.ambiguities)
        if not has_pronoun:
            return intent
        candidates = cls._candidate_person_targets(intent, world, active_conversation)
        if len(candidates) == 1:
            intent.target_id = candidates[0]
            intent.ambiguities = [
                a for a in intent.ambiguities if a.strip() not in cls._PERSON_PRONOUNS
            ]
        return intent

    @classmethod
    def _try_resolve_ambiguity(
        cls,
        ambiguity: str,
        intent: ParsedIntent,
        world: WorldState,
        active_conversation: dict[str, Any] | None = None,
    ) -> str | None:
        """Try to resolve an ambiguous reference using world state.

        Returns a specific entity_id or location_id if found, else None.
        """
        amb_lower = ambiguity.lower().strip()

        # Pronoun resolution via active_conversation context
        if active_conversation:
            participants = active_conversation.get("participants", [])
            other_participants = [p for p in participants if p != intent.actor_id]

            # 她/他: if exactly one other participant, resolve to them
            if amb_lower in ("她", "他") and len(other_participants) == 1:
                return other_participants[0]

            # 你: resolve to the other participant(s)
            if amb_lower == "你" and other_participants:
                return other_participants[0]

        # Pronoun "你" in conversation context -> conversation partner
        if amb_lower == "你" and intent.conversation_session_id:
            # conversation_session_id is not enough; we need the actual
            # partner. Since we don't have ConversationManager here, we
            # fallback to the most likely partner: the last speech target.
            if intent.target_id and intent.target_id != intent.actor_id:
                return intent.target_id

        # Hard-coded Chinese name map (MVP baseline)
        if amb_lower in cls._CN_NAME_MAP:
            mapped = cls._CN_NAME_MAP[amb_lower]
            if world.get_entity(mapped) or mapped in world.locations:
                return mapped

        # Substring match against entity IDs and names
        best_match = None
        best_score = 0
        for eid, entity in world.entities.items():
            bare = eid.lower().replace("npc.", "").replace("_", " ")
            # Check entity.attributes["name"] if present (future content packs)
            name = entity.attributes.get("name", bare) if hasattr(entity, "attributes") else bare
            candidates = [eid.lower(), bare, str(name).lower()]
            for cand in candidates:
                if amb_lower in cand or cand in amb_lower:
                    score = len(amb_lower) / max(len(cand), 1)
                    if score > best_score:
                        best_score = score
                        best_match = eid

        # Also check locations
        for lid in world.locations:
            lid_lower = lid.lower()
            if amb_lower in lid_lower or lid_lower in amb_lower:
                if len(lid_lower) > best_score:
                    best_score = len(lid_lower)
                    best_match = lid

        return best_match
