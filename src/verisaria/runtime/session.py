"""Engine runtime facade.

GameSession wires the subsystems together and orchestrates one tick from player
input to narrative (plus the post-tick passes). This is the engine's public API
that any frontend — the CLI REPL, a TUI, a future Godot client — drives.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from verisaria.engine.action_queue import ActionQueue
from verisaria.engine.agenda import AgendaService
from verisaria.engine.arbiter import LLMArbiter
from verisaria.engine.campaign import CampaignDriverManager
from verisaria.engine.campaign_loader import CampaignLoader
from verisaria.engine.combat import CombatEngine, PendingCombatAction
from verisaria.engine.conversation import ConversationManager
from verisaria.engine.hint_system import HintContext, HintSystem
from verisaria.engine.interaction import ActionComposer, InteractionResult, InteractionService
from verisaria.engine.intent import CoherenceChecker, IntentParser, PromptLoader
from verisaria.engine.llm import (
    FakeLLMProvider, LLMCallRequest, LLMOrchestrator, OllamaProvider,
    OpenAICompatibleProvider,
)
from verisaria.engine.memory import BeliefEngine, MemoryCompressor, MemoryStore
from verisaria.engine.npc_runtime import NPCActionGenerator, NPCInteractionScheduler
from verisaria.engine.appraisal import RelationshipAppraiser, RelationshipStore
from verisaria.engine.npc_dialogue import NPCDialogueGenerator
from verisaria.engine.observation import ObservationDispatcher
from verisaria.engine.persistence import PersistenceLayer
from verisaria.engine.response_generator import ResponseGenerator
from verisaria.engine.rules import RulesEngine
from verisaria.engine.scheduler import TickScheduler
from verisaria.engine.subjectivity import SubjectivityService
from verisaria.engine.intent import ClarificationRequest
from verisaria.engine.schemas import (
    Action, ActionType, Event, EventType, Memory, MemoryLayer, PacingSpeed,
)
from verisaria.engine.world_book_filter import WorldBookFilter
from verisaria.engine.validator import ValidatedOutcome
from verisaria.engine.formatter import OutputFormatter
from verisaria.engine.pack_editor import PackEditor
from verisaria.engine.world import WorldCore
from verisaria import protocol


MAX_CLARIFICATION_DEPTH = 3


@dataclass
class ClarificationContext:
    """Tracks an in-flight clarification exchange between player and parser."""

    original_input: str
    options: list[str]
    round: int
    max_rounds: int = MAX_CLARIFICATION_DEPTH


# ---------------------------------------------------------------------------
# Game Session
# ---------------------------------------------------------------------------

class GameSession:
    """Orchestrate all engine modules into a single playable session."""

    def __init__(self, content_pack_path: str, save_dir: str = "saves", **kwargs) -> None:
        # Load content pack
        self.pack, self.world_state, validation = CampaignLoader.load_or_fallback(
            content_pack_path
        )
        if not validation.valid:
            print("[Warning] Content pack validation issues:")
            for issue in validation.issues:
                print(f"  - [{issue.severity}] {issue.rule}: {issue.message}")

        # Core world
        self.world = WorldCore(initial_state=self.world_state)

        # LLM pipeline
        llm_backend = kwargs.get("llm_backend", "fake")
        if llm_backend == "ollama":
            provider = OllamaProvider(model="gpt-oss:latest")
        elif llm_backend in self._OPENAI_COMPAT_PRESETS:
            provider = self._build_openai_compatible_provider(llm_backend)
        else:
            provider = FakeLLMProvider()
        orchestrator = LLMOrchestrator(primary_provider=provider)
        self.llm_provider = provider
        self.llm_orchestrator = orchestrator
        prompt_loader = PromptLoader()
        coherence = CoherenceChecker()
        self.intent_parser = IntentParser(orchestrator, prompt_loader, coherence)

        # Response, Combat, Conversation & Interaction
        self.response_generator = ResponseGenerator(
            style_guide=self.pack.style_guide if hasattr(self.pack, "style_guide") else None
        )
        self.combat_engine = CombatEngine(seed=42)
        self.conversation_manager = ConversationManager(timeout_ticks=10)
        self.interaction_service = InteractionService(
            ActionComposer(),
            conversation_manager=self.conversation_manager,
        )
        self.rules_engine = RulesEngine()
        self.arbiter = LLMArbiter(llm_orchestrator=orchestrator)
        self.observation_dispatcher = ObservationDispatcher()
        self.action_queue = ActionQueue()
        self.hint_system = HintSystem()
        self.npc_dialogue_generator = NPCDialogueGenerator(
            self.llm_orchestrator, world_book=self.pack.world_book
        )
        self.npc_action_generator = NPCActionGenerator(
            seed=42, dialogue_generator=self.npc_dialogue_generator
        )
        self.npc_interaction_scheduler = NPCInteractionScheduler(seed=42)
        self.tick_scheduler = TickScheduler(initial_tick=self.world.state.tick)
        # Most recent aggregate campaign pressure, fed into pacing (FORCE rule).
        self._latest_campaign_pressure: float = 0.0

        # Subjectivity (Belief system)
        self.memory_store = MemoryStore()
        self.belief_engine = BeliefEngine()
        self.subjectivity_service = SubjectivityService(
            memory_store=self.memory_store,
            belief_engine=self.belief_engine,
        )
        # Rule-driven layered memory compaction (working→short→long), run each
        # tick so long-running NPCs do not accumulate unbounded working memory.
        self.memory_compressor = MemoryCompressor(self.memory_store)

        # Relationship appraisal (PLAY-3 Channel A): perceived player actions are
        # appraised by each observing NPC (through the LLM seam) into cumulative,
        # clamped relationship deltas, so player choices have consequence.
        self.relationship_appraiser = RelationshipAppraiser(self.llm_orchestrator)
        self.relationship_store = RelationshipStore()
        # Appraisal runs off the player's critical path for real providers
        # (PLAY-1): the tick returns immediately and the appraisal completes in
        # the background (hidden behind human think-time), flushed before any read
        # or the next tick. Under FakeLLM it stays inline/serial (A10).
        from concurrent.futures import ThreadPoolExecutor
        self._appraisal_executor = ThreadPoolExecutor(max_workers=1)
        self._pending_appraisal: tuple | None = None
        # Optional sink for token-by-token NPC reply streaming (set by the REPL /
        # a UI). When set, the addressed NPC's reply streams here live; that line
        # is then omitted from the assembled narrative to avoid double-printing.
        self._stream_sink = None
        self._streamed_npc: str | None = None
        # Optional sink for coarse progress messages (e.g. "理解中…") during the
        # slow phases, so the screen isn't frozen. Only fired for real providers.
        self._progress_sink = None
        # Optional structured-event sink (Step 2 protocol). When set, run_tick also
        # pushes typed protocol.Event DTOs here — the path a TUI/Godot frontend
        # consumes. The legacy string return + text sinks stay, so the CLI is
        # unaffected. See docs/design/protocol-design.md.
        self._event_sink = None

        # Mutable world facts the player's choices can change (PLAY-3 Channel C).
        self._world_var_specs: dict[str, dict[str, Any]] = {}
        self._init_world_vars()

        # Persistence
        self.persistence = PersistenceLayer(save_dir)

        # State tracking
        self.running = True
        self.player_id = "player_001"

        # Player Agenda (after player_id is set). Pack-declared stance topics let
        # repeated player intents cluster into stable, world-readable stances.
        self.agenda_service = AgendaService(
            player_id=self.player_id,
            stance_topics=self._stance_topics_from_pack(),
            stance_labels=self._stance_labels_from_pack(),
        )

        # Campaign Drivers
        self.campaign_driver_manager = CampaignDriverManager.from_dicts(
            self.pack.campaign_drivers if hasattr(self.pack, "campaign_drivers") else [],
            seed=42,
        )

        # Clarification state (multi-turn ambiguity resolution)
        self._active_clarification: ClarificationContext | None = None

        # Conversation mode (changes REPL prompt)
        self._conversation_mode: str | None = None

        # Empty input tracking
        self._empty_input_count = 0

        # Output formatter (ANSI colors, status bar, boxes)
        self.formatter = OutputFormatter()

    # -- Tick execution --

    # -- Tick execution --

    def run_tick(self, raw_input: str | None) -> str:
        """Execute one full tick from player input to narrative output."""
        if raw_input is None:
            raw_input = ""
        raw_input = raw_input.strip()

        # Barrier: apply the prior tick's backgrounded appraisal before this tick
        # reads/updates any NPC cognition (PLAY-1).
        self._flush_appraisals()

        # Fresh LLM budget for this tick. Without this the per-tick cap is never
        # cleared and every LLM call permanently fails after the first few ticks
        # (NPC dialogue then degrades to templates forever).
        self.llm_orchestrator.reset_tick_budget()

        # Expire stale conversations so an NPC the player engaged long ago stops
        # counting as "in conversation" (and interjecting). Engine-level so EVERY
        # frontend gets it — previously only the CLI repl called this, which is why
        # the TUI's NPCs never stopped talking.
        self.conversation_manager.check_timeouts(self.world.state.tick)

        # Handle active clarification session
        if self._active_clarification is not None:
            return self._handle_clarification_response(raw_input)

        return self._execute_tick(raw_input)

    def _execute_tick(
        self,
        raw_input: str,
        _clarification_depth: int = 0,
        skip_ambiguity_check: bool = False,
    ) -> str:
        """Core tick logic after any clarification dispatching."""
        # Extract conversation context for intent parsing
        session = self.conversation_manager.get_active_session(self.player_id)
        context: dict[str, Any] | None = None
        if session is not None:
            context = {
                "active_conversation": {
                    "session_id": session.session_id,
                    "participants": session.participants,
                    "other_participants": [p for p in session.participants if p != self.player_id],
                    "last_speaker": session.shared_context.get("last_speaker"),
                    "last_content": session.shared_context.get("last_content"),
                }
            }

        if raw_input:
            self._emit_progress("（正在领会你的意思…）")
            intent_result = self.intent_parser.parse(
                raw_text=raw_input,
                actor_id=self.player_id,
                tick=self.world.state.tick,
                world=self.world.state,
                skip_ambiguity_check=skip_ambiguity_check,
                context=context,
            )

            if isinstance(intent_result, ClarificationRequest):
                if intent_result.ambiguity_type in ("coherence_error", "parse_failed"):
                    # Not resolvable by clarification — a coherence contradiction
                    # or a parse the LLM couldn't make sense of. Don't enter the
                    # stateful merge loop (which would glue the next, possibly
                    # unrelated, input onto the unparseable original and send it
                    # to the wrong target — PLAY-2). Just ask for a rephrase and
                    # treat whatever comes next as a fresh attempt.
                    return intent_result.clarifying_question + "\n请重新输入。"
                if _clarification_depth >= MAX_CLARIFICATION_DEPTH:
                    self._active_clarification = None
                    return "多次澄清后仍无法理解，请换一种方式描述。"
                self._active_clarification = ClarificationContext(
                    original_input=raw_input,
                    options=intent_result.options or [],
                    round=_clarification_depth + 1,
                )
                self._emit(protocol.ClarificationNeeded(
                    tick=self.world.state.tick,
                    question=intent_result.clarifying_question,
                    options=list(intent_result.options or []),
                ))
                return self._format_clarification_question(intent_result)

            # "走到X面前问他：Y" — the LLM sometimes classifies an approach-and-
            # speak as a bare MOVEMENT, which drops the question AND relocates the
            # player (stranding them away from the NPC). A movement that carries
            # speech content aimed at an NPC is really a speech act. (playtest bug)
            if (intent_result.intent_type == ActionType.MOVEMENT
                    and (intent_result.content or "").strip()
                    and (intent_result.target_id or "").startswith("npc.")):
                intent_result.intent_type = ActionType.SPEECH
                intent_result.modifiers = {
                    k: v for k, v in (intent_result.modifiers or {}).items()
                    if k not in ("to_location", "to_zone")
                }

            # Compose action
            interaction_result = self.interaction_service.process_intent(
                intent=intent_result,
                tick=self.world.state.tick,
                world=self.world.state,
            )

            if interaction_result.preview_only:
                action = interaction_result.action
                return f"[Preview] {action.action_type.value}: {action.params}"

            if interaction_result.action is None:
                return "你想了想，没有采取任何行动。"

            action = interaction_result.action

            # Structured event: the player's spoken line (clean content from the
            # intent, not the raw "对X说：…" the action stores in params).
            if action.action_type == ActionType.SPEECH and (intent_result.content or "").strip():
                self._emit(protocol.PlayerSpoke(
                    tick=self.world.state.tick, line=intent_result.content))

            # Feed intent signal to agenda service
            self.agenda_service.add_signal(
                note=raw_input,
                tick=self.world.state.tick,
                source_id=action.source_intent_id,
            )

            # Channel C trigger: a request to the AUTHORIZED NPC to change a world
            # fact is adjudicated — does that NPC, given their stance toward the
            # player (Channel A) + their duty, comply? Persuading the right person
            # is the path to changing the world (PLAY-3 Channel C).
            wc = self._world_change_request(action)
            if wc is not None:
                return self._handle_world_change_request(action, *wc)

            # Rules Engine
            resolution = self.rules_engine.resolve(action, self.world.state)

            if not resolution.can_execute:
                return f"你没法那么做：{resolution.reason}"

            if resolution.requires_arbiter:
                return self._handle_arbiter_action(action)

        else:
            # Empty input = player waits
            action = Action(
                action_id=f"act_{self.world.state.tick}_1",
                source_intent_id=None,
                actor_id=self.player_id,
                action_type=ActionType.PHYSICAL,
                target_id=None,
                params={"verb": "wait"},
                zone_id=None,
                conversation_session_id=None,
                tick=self.world.state.tick,
            )
            resolution = self.rules_engine.resolve(action, self.world.state)

        return self._dispatch_player_action(action, resolution)

    def _dispatch_player_action(self, action, resolution=None) -> str:
        """Run a committed player Action through the world: submit it, collect
        NPC actions, resolve the queue, narrate, advance the tick, and run the
        post-tick passes. Shared by normal play and `/inject`.
        """
        # Barrier: apply the previous tick's backgrounded appraisal before this
        # tick's NPCs read their (possibly updated) memory/relationships.
        self._flush_appraisals()

        if resolution is None:
            resolution = self.rules_engine.resolve(action, self.world.state)
            if not resolution.can_execute:
                return f"你没法那么做：{resolution.reason}"
            if resolution.requires_arbiter:
                return self._handle_arbiter_action(action)

        # Submit player action to Action Queue (pass RulesEngine resolution
        # so that its summary/canonical_facts are used instead of regenerating)
        self.action_queue.submit(action, resolution)

        # If the player addressed a specific NPC, hush the bystanders in that
        # location so the chosen conversation isn't drowned out (P1.9).
        self._streamed_npc = None
        suppress_at = None
        addressed_npc = None
        if (action.action_type == ActionType.SPEECH and action.target_id
                and action.target_id.startswith("npc.")):
            player = self.world.state.get_entity(self.player_id)
            suppress_at = player.location_id if player else None
            addressed_npc = action.target_id

        # Collect NPC actions (the addressed NPC's reply may stream live).
        npc_actions = self._collect_npc_actions(
            suppress_idle_speech_at=suppress_at, addressed_npc=addressed_npc
        )
        for a in npc_actions:
            npc_resolution = self.rules_engine.resolve(a, self.world.state)
            if npc_resolution.can_execute:
                self.action_queue.submit(a, npc_resolution)

        # Player location BEFORE the queue resolves any movement — the player
        # narrates from where they were when this tick's events fired, so a move
        # doesn't pre-show the destination's NPC activity (P1.4).
        player_before = self.world.state.get_entity(self.player_id)
        viewer_location = player_before.location_id if player_before else None

        # Resolve queue → events + combat actions
        events, combat_actions = self.action_queue.resolve_with_combat(self.world)

        # Structured protocol events (Step 2): NPC lines the player can perceive
        # (A5: only actors co-located with the player) + the player's own move.
        for a in npc_actions:
            if a.action_type != ActionType.SPEECH:
                continue
            content = (a.params or {}).get("content")
            actor = self.world.state.get_entity(a.actor_id)
            if content and actor and actor.location_id == viewer_location:
                self._emit(protocol.NpcSpoke(
                    tick=self.world.state.tick, npc_id=a.actor_id,
                    name=self.world.state.display_name(a.actor_id), line=content))
        # NB: player_before is a LIVE entity reference that resolve() mutates in
        # place, so compare against viewer_location (the string captured pre-resolve).
        player_after = self.world.state.get_entity(self.player_id)
        if (player_after and viewer_location
                and player_after.location_id != viewer_location):
            self._emit(protocol.PlayerMoved(
                tick=self.world.state.tick,
                from_loc=self.world.state.location_label(viewer_location),
                to_loc=self.world.state.location_label(player_after.location_id)))

        # Observation dispatch + Subjectivity pipeline for non-combat events
        dispatched = self._process_events_for_subjectivity(events)

        # Relationship appraisal (PLAY-3 Channel A): each NPC that perceived a
        # socially-weighted *player* action appraises how it changes their stance
        # toward the player. Deltas accumulate into the relationship store, so
        # the player's choices have visible consequence.
        self._appraise_player_actions(dispatched)

        # Build narrative. If the addressed NPC's reply was streamed live, drop it
        # (and the player's own just-typed line) from the assembled narrative so
        # they aren't printed twice.
        skip_speech = {self.player_id, self._streamed_npc} if self._streamed_npc else None
        narrative = self.response_generator.generate(
            events, self.world.state, self.player_id, viewer_location=viewer_location,
            skip_speech_actors=skip_speech,
        )
        # For the structured Narration EVENT, strip ALL speech — granular Player/Npc
        # Spoke events already carry the dialogue, so this leaves only movement /
        # look / ambient prose (a TUI renders it without double-printing speech).
        _speakers = {e.actor_id for e in events if e.event_type == EventType.SPEECH}
        ambient_narration = self.response_generator.generate(
            events, self.world.state, self.player_id, viewer_location=viewer_location,
            skip_speech_actors=(_speakers or None),
        )

        # Handle combat actions via Combat Engine
        if combat_actions:
            # Typically only one player combat action per tick
            combat_narrative = self._handle_combat_action(combat_actions[0])
            if combat_narrative:
                if narrative and narrative not in ("什么也没发生。", "时间悄然流逝……"):
                    narrative += "\n" + combat_narrative
                else:
                    narrative = combat_narrative

        # Clean up and advance tick under the Pacing Policy. Conversation/combat
        # hold the world to one step; a quiet area fast-forwards; critical
        # campaign pressure forces extra advancement (design §6.5).
        self.action_queue.clear()
        self._build_pacing_context()
        self._advance_tick_with_pacing(player_driven=True)
        self._emit(protocol.TickAdvanced(
            tick=self.world.state.tick, new_tick=self.world.state.tick))

        # Layered memory compaction so working memory stays bounded (P3.3).
        self._compress_memories_for_all(current_tick=self.world.state.tick)

        # Aggregate the player's repeated intents into confirmed stances (PLAY-3
        # Channel B). Runs every player tick to break the old deadlock where
        # aggregation only happened inside a reflection scene; a sustained intent
        # now becomes a confirmed goal the player sees and the world can read.
        new_stances = self.agenda_service.aggregate_and_autoconfirm(
            self.world.state.tick
        )
        if new_stances:
            labels = "、".join(d.label for d in new_stances)
            narrative += f"\n\n（你逐渐确信了自己的目标：{labels}）"
            for d in new_stances:
                self._emit(protocol.StanceConfirmed(
                    tick=self.world.state.tick,
                    topic_id=getattr(d, "topic_id", getattr(d, "id", "")),
                    label=d.label))

        # Check for Reflection Scene triggers
        should_reflect, trigger = self.agenda_service.should_trigger_reflection(
            tick=self.world.state.tick,
        )
        if should_reflect:
            scene = self.agenda_service.generate_reflection_scene(
                current_tick=self.world.state.tick,
                trigger=trigger,
            )
            if scene and scene.narration_hint:
                narrative += f"\n\n{scene.narration_hint}"

        # Check Campaign Drivers for pressure events
        pressure_narrative = self._check_campaign_drivers()
        if pressure_narrative:
            narrative += f"\n\n{pressure_narrative}"

        if (ambient_narration and ambient_narration.strip()
                and ambient_narration not in ("什么也没发生。", "时间悄然流逝……")):
            self._emit(protocol.Narration(
                tick=self.world.state.tick, text=ambient_narration))
        return narrative

    def _handle_clarification_response(self, response: str) -> str:
        """Process the player's reply during an active clarification exchange."""
        ctx = self._active_clarification
        assert ctx is not None

        if response == "/cancel":
            self._active_clarification = None
            return "动作已取消。"

        if not response:
            return self._format_clarification_question(
                ClarificationRequest(
                    request_id="",
                    original_input=ctx.original_input,
                    clarifying_question="请提供更多信息：",
                    options=ctx.options,
                )
            )

        # If the player has moved on and typed a self-contained NEW command instead
        # of answering, run it fresh — don't glue it onto the abandoned referent
        # (real-play bug B1: a new "求你开城门…" got merged with a stale "传令兵"
        # clarification, corrupting the target). Numeric/cancel answers are still
        # handled below as genuine clarification responses.
        is_numeric_option = bool(ctx.options) and response.strip().isdigit()
        is_cancel_text = response.strip().lower() in ("取消", "cancel", "q")
        if (not is_numeric_option and not is_cancel_text
                and self._looks_like_fresh_command(response)):
            self._active_clarification = None
            return self._execute_tick(response)

        explicit_input, skip_ambiguity = self._resolve_clarification(ctx, response)
        if explicit_input == "/cancel":
            self._active_clarification = None
            return "动作已取消。"

        depth = ctx.round
        self._active_clarification = None
        return self._execute_tick(
            explicit_input, _clarification_depth=depth, skip_ambiguity_check=skip_ambiguity
        )

    @staticmethod
    def _looks_like_fresh_command(text: str) -> bool:
        """True when an input is a self-contained NEW command rather than a referent
        disambiguation. A clarification answer is a short noun-phrase ('门口那个人',
        '卡泽'); a fresh command is a full utterance — a slash command, something
        ending in sentence-final punctuation, or carrying its own addressing verb."""
        t = text.strip()
        if not t:
            return False
        if t.startswith("/"):
            return True
        if t[-1] in "。！？!?":
            return True
        # Its own addressing/speech structure (verb + delimiter) means a new act,
        # not a bare referent.
        speech_markers = ("说：", "说:", "问：", "问:", "告诉", "喊道", "喊：", "喊:")
        return any(m in t for m in speech_markers)

    def _resolve_clarification(
        self, ctx: ClarificationContext, response: str
    ) -> tuple[str, bool]:
        """Merge the player's clarification response with the original input.

        Returns (merged_input, skip_ambiguity_check).
        """
        stripped = response.strip()

        # Numeric option selection
        if ctx.options and stripped.isdigit():
            idx = int(stripped) - 1
            if 0 <= idx < len(ctx.options):
                chosen = ctx.options[idx]
                if chosen == "取消动作":
                    return "/cancel", False
                if chosen == "尝试执行":
                    # Player explicitly wants to proceed despite ambiguity.
                    # Skip ambiguity check on retry.
                    return ctx.original_input, True
                # Specific entity/location chosen — merge into original input
                # Skip ambiguity check on retry because the player has
                # explicitly disambiguated.
                return f"{ctx.original_input}（指{chosen}）", True

        # Textual cancellation
        if stripped.lower() in ("取消", "cancel", "q"):
            return "/cancel", False

        # Fallback: append as explicit clarification
        # Skip ambiguity check because the player has supplied explicit text.
        return f"{ctx.original_input}，具体来说是：{stripped}", True

    @staticmethod
    def _format_clarification_question(req: ClarificationRequest) -> str:
        """Render a ClarificationRequest into a player-facing prompt."""
        lines = [req.clarifying_question]
        if req.options:
            for i, opt in enumerate(req.options, 1):
                lines.append(f"  {i}. {opt}")
            lines.append("输入选项编号或补充说明，/cancel 取消动作。")
        else:
            lines.append("请补充说明，或输入 /cancel 取消动作。")
        return "\n".join(lines)

    def _collect_npc_actions(
        self, suppress_idle_speech_at: str | None = None,
        addressed_npc: str | None = None,
    ) -> list[Action]:
        """Generate NPC actions for this tick: autonomous NPC-NPC interactions
        first, then one idle action for every NPC not already engaged.

        ``suppress_idle_speech_at`` (set when the player addressed an NPC) keeps
        bystander NPCs there quiet so the player's conversation has the spotlight
        — both their idle chatter and NPC-NPC interactions are held back at that
        location this tick (P1.9).
        """
        # "Busy" = participants of ALL active sessions — used to not double-book an
        # NPC into a new NPC-NPC interaction this tick.
        busy_convs: set[str] = set()
        for session in self.conversation_manager._sessions.values():
            if session.status in ("active", "resumed"):
                busy_convs.update(session.participants)
        if addressed_npc:
            busy_convs.add(addressed_npc)

        # "Speaking" = the conversation the player is engaging THIS tick (the
        # addressed NPC + that NPC's session partners). Only these auto-reply every
        # tick. A lingering / NPC-NPC session no longer forces its members to
        # interject (the over-chatter bug: an NPC addressed turns ago kept talking).
        speaking_convs: set[str] = set()
        if addressed_npc:
            speaking_convs.add(addressed_npc)
            asess = self.conversation_manager.get_active_session(addressed_npc)
            if asess is not None and asess.status in ("active", "resumed"):
                speaking_convs.update(
                    p for p in asess.participants if p != self.player_id
                )

        # Precompute every candidate speaker's line concurrently (real providers)
        # so the serial generation below reads cached lines instead of blocking
        # on N sequential LLM calls — on- AND off-screen dialogue then costs ~1
        # call of latency (PLAY-1). The generation loops are unchanged, so rng/seq
        # stay deterministic; FakeLLM skips this and calls live (A10).
        if getattr(self.llm_provider, "supports_concurrency", False):
            self._precompute_npc_lines(busy_convs, stream_npc=addressed_npc)
        try:
            return self._collect_npc_actions_inner(
                busy_convs, speaking_convs, suppress_idle_speech_at
            )
        finally:
            self.npc_action_generator._line_cache = None

    def _precompute_npc_lines(
        self, active_convs: set[str], stream_npc: str | None = None
    ) -> None:
        """Concurrently generate dialogue for every NPC that could speak this tick
        (in conversation, or co-located with someone) into the action generator's
        line cache. Over-approximates speakers — a precomputed line for an NPC
        that ends up silent is simply unused (speed over call-count, per design).

        ``stream_npc`` (the addressed NPC) is streamed token-by-token in the
        FOREGROUND to ``self._stream_sink`` while the others precompute in the
        background, so the player watches the reply they're waiting on appear and
        the off-screen/bystander lines overlap it (≈1 call of latency)."""
        world = self.world.state
        candidates: list[str] = []
        for eid, e in world.entities.items():
            if e.entity_type != "npc":
                continue
            has_neighbor = any(
                oid != eid and oe.location_id == e.location_id
                for oid, oe in world.entities.items()
            )
            if eid in active_convs or has_neighbor:
                candidates.append(eid)
        if not candidates:
            return

        def _gen(npc_id: str):
            return self.npc_dialogue_generator.generate_line(
                npc_id=npc_id,
                entity=world.get_entity(npc_id),
                world=world,
                memory_store=self.memory_store,
                conversation_session=self.conversation_manager.get_active_session(npc_id),
            )

        do_stream = (
            stream_npc is not None and stream_npc in candidates
            and (self._stream_sink is not None or self._event_sink is not None)
            and getattr(self.llm_provider, "supports_streaming", False)
        )
        cache: dict[str, str | None] = {}
        others = [c for c in candidates if not (do_stream and c == stream_npc)]

        # Kick the other NPCs' lines off concurrently in the background so they
        # overlap the foreground stream.
        from concurrent.futures import ThreadPoolExecutor
        pool = futures = None
        if others:
            if getattr(self.llm_provider, "supports_concurrency", False):
                pool = ThreadPoolExecutor(max_workers=min(len(others), 8))
                futures = {c: pool.submit(_gen, c) for c in others}
            else:
                cache.update({c: _gen(c) for c in others})

        # Foreground: stream the addressed NPC's reply live.
        if do_stream:
            if self._stream_sink is not None:
                name = stream_npc.replace("npc.", "")
                self._stream_sink(f"\n{name}：")
            line = self.npc_dialogue_generator.generate_line_stream(
                npc_id=stream_npc, entity=world.get_entity(stream_npc), world=world,
                memory_store=self.memory_store,
                conversation_session=self.conversation_manager.get_active_session(stream_npc),
                on_delta=self._stream_delta_sink(stream_npc),
            )
            if self._stream_sink is not None:
                self._stream_sink("\n")
            if line:
                cache[stream_npc] = line
                self._streamed_npc = stream_npc
            else:
                # Streaming failed — fall back to a normal (cached) line.
                cache[stream_npc] = _gen(stream_npc)

        if futures is not None:
            for c, f in futures.items():
                cache[c] = f.result()
            pool.shutdown()

        self.npc_action_generator._line_cache = cache

    def _collect_npc_actions_inner(
        self, busy_convs: set[str], speaking_convs: set[str],
        suppress_idle_speech_at: str | None,
    ) -> list[Action]:
        # NPC-NPC autonomous interactions (P1.2). NPCs talking to the player are
        # busy and excluded; an interacting NPC is then excluded from idle
        # generation so it never acts twice in one tick. While the player holds a
        # conversation, suppress NPC-NPC interactions entirely so they don't bury
        # the player's exchange (P1.9). ``busy_convs`` keeps anyone in any active
        # session from being double-booked; ``speaking_convs`` (the player's current
        # exchange) is who auto-replies this tick.
        interaction_actions: list[Action] = []
        if suppress_idle_speech_at is None:
            interaction_actions = self.npc_action_generator.generate_interaction_actions(
                scheduler=self.npc_interaction_scheduler,
                world=self.world.state,
                tick=self.world.state.tick,
                belief_engine=self.belief_engine,
                memory_store=self.memory_store,
                busy_ids=busy_convs,
                max_interactions=1,
            )
        engaged = {a.actor_id for a in interaction_actions}

        idle_actions = self.npc_action_generator.generate_actions(
            world=self.world.state,
            tick=self.world.state.tick,
            active_conversation_entity_ids=speaking_convs,
            memory_store=self.memory_store,
            conversation_manager=self.conversation_manager,
            exclude_ids=engaged,
            suppress_idle_speech_at=suppress_idle_speech_at,
        )
        return interaction_actions + idle_actions

    def _handle_arbiter_action(self, action: Action) -> str:
        """Handle an action that requires LLM arbitration.

        Calls the arbiter, applies validated state changes through the Event Log,
        and returns a narrative string.
        """
        # Tell the arbiter which world facts are mutable (+ current value, who may
        # change them, and that authority's stance toward the player) so it can
        # legitimately propose `world.<var>` changes (PLAY-3 Channel C).
        self.world.mutable_world_vars = self._world_vars_for_arbiter()

        outcome = self.arbiter.arbitrate(action, self.world)

        # Map action type to event type
        event_type_map = {
            ActionType.PHYSICAL: EventType.PHYSICAL,
            ActionType.SOCIAL: EventType.SOCIAL,
        }
        event_type = event_type_map.get(action.action_type, EventType.SYSTEM)

        # Build summary from arbiter output
        arb_outcome = outcome.arbiter_output
        verb = action.params.get("verb", action.action_type.value)
        target = f" 对 {action.target_id}" if action.target_id else ""
        outcome_label = {"success": "成功", "partial_success": "部分成功", "failure": "失败"}.get(
            arb_outcome.outcome, arb_outcome.outcome
        )
        summary = f"{action.actor_id}{target} {verb} — {outcome_label}"

        # Create event and append to event log
        actor = self.world.state.get_entity(action.actor_id)
        event = Event(
            event_id=self.world.next_event_id(),
            event_type=event_type,
            actor_id=action.actor_id,
            target_id=action.target_id,
            tick=self.world.state.tick,
            location_id=actor.location_id if actor else "unknown",
            zone_id=actor.zone_id if actor else None,
            summary=summary,
            canonical_facts={
                "verb": verb,
                "arbiter_outcome": arb_outcome.outcome,
                "arbiter_reason": arb_outcome.reason,
                "confidence": arb_outcome.confidence,
            },
            source_action_id=action.action_id,
        )
        self.world.event_log.append(event)

        # Apply accepted state changes
        self._apply_state_changes(outcome)

        # Feed event through observation/subjectivity pipeline
        self._process_events_for_subjectivity([event])

        # Build narrative. The arbiter's narration_hint is a *meta* note for the
        # narrator and must not be shown raw — internal phrasings (默认裁决,
        # 玩家可继续尝试, 换策略 …) would break immersion. Prefer the rule-driven
        # narrative; only surface the hint if it reads like in-world prose. (P0.4)
        narrative = self.response_generator.generate(
            [event], self.world.state, self.player_id
        )
        hint = (arb_outcome.narration_hint or "").strip()
        if hint and not self._is_meta_hint(hint):
            narrative = f"{narrative}\n{hint}" if narrative else hint

        # Structured event: the arbiter path's prose IS the only output (no granular
        # speech event), so a TUI/Godot frontend needs it as a Narration event.
        if narrative and narrative.strip():
            self._emit(protocol.Narration(
                tick=self.world.state.tick, text=narrative))

        # Advance tick (arbiter path bypasses the normal queue resolution)
        self.world.tick_advance()
        self._emit(protocol.TickAdvanced(
            tick=self.world.state.tick, new_tick=self.world.state.tick))

        return narrative

    # Phrases that mark an arbiter hint as internal meta (never shown to player).
    _META_HINT_MARKERS = (
        "裁决", "玩家可", "换策略", "继续尝试", "NPC", "系统", "default",
        "fallback", "arbiter",
    )

    @classmethod
    def _is_meta_hint(cls, hint: str) -> bool:
        """True if an arbiter narration_hint looks like internal meta rather than
        player-facing prose."""
        low = hint.lower()
        return any(m.lower() in low for m in cls._META_HINT_MARKERS)

    def _apply_state_changes(self, outcome: ValidatedOutcome) -> None:
        """Apply accepted state changes from an arbiter outcome to world state."""
        for change in outcome.accepted_state_changes:
            parts = change.field.split(".")
            if len(parts) < 2:
                continue

            # Pattern: world.<var_id> — a mutable world fact (PLAY-3 Channel C).
            # Routed through the gated setter so only declared vars can change.
            if parts[0] == "world":
                self.set_world_var(".".join(parts[1:]), change.delta)
                continue

            # Pattern: entity_id.attribute_name  (e.g. "npc.guard_b.alertness")
            entity_id = parts[0]
            if len(parts) == 3:
                entity_id = f"{parts[0]}.{parts[1]}"
                attr_name = parts[2]
            else:
                attr_name = parts[1]

            entity = self.world.state.get_entity(entity_id)
            if entity is None:
                continue

            # Top-level fields on EntityState (not attributes dict)
            top_level_fields = {"location_id", "zone_id", "hp", "max_hp"}
            if attr_name in top_level_fields:
                if isinstance(change.delta, (int, float)) and attr_name in ("hp", "max_hp"):
                    current = getattr(entity, attr_name, 0)
                    setattr(entity, attr_name, current + change.delta)
                else:
                    setattr(entity, attr_name, change.delta)
                continue

            # Apply delta to entity attribute
            current = entity.attributes.get(attr_name, 0.0)
            if isinstance(change.delta, (int, float)):
                entity.attributes[attr_name] = current + change.delta
            elif isinstance(change.delta, bool):
                entity.attributes[attr_name] = change.delta
            else:
                entity.attributes[attr_name] = change.delta

    def _process_events_for_subjectivity(self, events: list[Event]) -> list:
        """Feed events through Observation → Subjectivity pipeline for all NPCs.

        Returns the (event, observation) pairs it dispatched so callers can run
        further per-observation passes (e.g. relationship appraisal) without
        re-dispatching — which would double-count the dispatcher's skip log.
        """
        dispatched: list = []
        for event in events:
            observations = self.observation_dispatcher.dispatch(
                event, self.world.state
            )
            for obs in observations:
                observer = self.world.state.get_entity(obs.observer_id)
                traits = observer.traits if observer else []
                self.subjectivity_service.ingest(obs, observer_traits=traits)
                dispatched.append((event, obs))
        return dispatched

    # Player action types that carry relational weight — movement/system noise
    # is not appraised (cheap deterministic gate; generous within social acts).
    _APPRAISABLE_EVENT_TYPES = frozenset({
        EventType.SPEECH, EventType.SOCIAL, EventType.COMBAT, EventType.PHYSICAL,
    })

    def _appraise_player_actions(self, dispatched: list) -> None:
        """For each NPC that perceived a socially-weighted player action this
        tick, run an appraisal and accumulate the resulting relationship deltas.

        The appraisal goes through the LLM seam (deterministic under FakeLLM);
        when it returns ``None`` (LLM unavailable / invalid output) we simply
        record no change — a player action never crashes a tick (P5).

        Each perceiving NPC's appraisal is an independent LLM call, so they run
        concurrently for real providers (PLAY-1) — the (read-only) appraisals are
        dispatched in parallel, then their mutations are applied serially in a
        deterministic order. Under FakeLLM this stays serial (A10)."""
        # Phase 1 (serial, deterministic): pick the perceiving NPCs + their
        # grounding facts. No LLM, no shared-state mutation.
        jobs: list[tuple[str, str]] = []  # (observer_id, event_id)
        seen: set[tuple[str, str]] = set()
        ctx: dict[tuple[str, str], dict] = {}
        for event, obs in dispatched:
            if event.actor_id != self.player_id:
                continue
            if event.event_type not in self._APPRAISABLE_EVENT_TYPES:
                continue
            observer_id = obs.observer_id
            if not observer_id.startswith("npc."):
                continue
            key = (observer_id, event.event_id)
            if key in seen:
                continue
            seen.add(key)
            entity = self.world.state.get_entity(observer_id)
            # Ground the appraisal in what this NPC actually knows/believes (its
            # accessible world-book), so the stance reflects the NPC's worldview
            # (A5) rather than its trait labels alone.
            known_facts = [
                e.content for e in WorldBookFilter.filter_for_entity(
                    self.pack.world_book, entity
                )
            ]
            ctx[key] = {"entity": entity, "event": event, "known_facts": known_facts}
            jobs.append(key)

        if not jobs:
            return

        tick = self.world.state.tick

        # Phase 2: run the appraisals (each an independent LLM call).
        def _appraise(key: tuple[str, str]):
            observer_id, _ = key
            c = ctx[key]
            return self.relationship_appraiser.appraise(
                observer_id, c["entity"], c["event"], self.world.state,
                self.memory_store, known_facts=c["known_facts"],
            )

        if getattr(self.llm_provider, "supports_concurrency", False):
            # Off the critical path: tick returns now; appraisal runs in the
            # background and is flushed before the next read/tick.
            future = self._appraisal_executor.submit(self._run_llm_jobs, jobs, _appraise)
            self._pending_appraisal = (jobs, future, tick)
        else:
            # Inline + serial for FakeLLM / replay (A10).
            results = self._run_llm_jobs(jobs, _appraise)
            self._apply_appraisal_results(jobs, results, tick)

    def _emit(self, event: Any) -> None:
        """Push a structured protocol Event to the frontend sink, if attached.
        A frontend bug must never crash the engine, so failures are swallowed."""
        sink = self._event_sink
        if sink is None:
            return
        try:
            sink(event)
        except Exception:
            pass

    def _stream_delta_sink(self, npc_id: str):
        """Build the ``on_delta`` callback for a streamed NPC line. Forwards each
        token to the raw CLI sink (if attached) AND emits a structured
        ``SpeechToken`` event to the protocol sink — so a TUI/Godot frontend can
        render the reply token-by-token (the §2 'latency feel' point) without the
        CLI's stdout coupling."""
        def on_delta(token: str) -> None:
            if self._stream_sink is not None:
                self._stream_sink(token)
            if self._event_sink is not None:
                self._emit(protocol.SpeechToken(
                    tick=self.world.state.tick, npc_id=npc_id, token=token))
        return on_delta

    def _emit_progress(self, message: str) -> None:
        """Send a coarse progress message to the sink — only for real (slow)
        providers, so FakeLLM's instant calls don't flicker status lines."""
        self._emit(protocol.Progress(tick=self.world.state.tick, message=message))
        if self._progress_sink is not None and getattr(
            self.llm_provider, "supports_concurrency", False
        ):
            self._progress_sink(message)

    def _flush_appraisals(self) -> None:
        """Block until any backgrounded appraisal completes, then apply it. The
        barrier every relationship read / save / next tick passes through, so the
        player never observes stale state."""
        if self._pending_appraisal is None:
            return
        jobs, future, tick = self._pending_appraisal
        self._pending_appraisal = None
        self._apply_appraisal_results(jobs, future.result(), tick)

    def _apply_appraisal_results(self, jobs: list, results: list, tick: int) -> None:
        """Apply appraisal deltas + remember beliefs, in deterministic job order
        (runs on the main thread, so no store races)."""
        for key, result in zip(jobs, results):
            if result is None:
                continue
            observer_id, _ = key
            self.relationship_store.apply(
                observer_id, self.player_id, result.deltas, tick
            )
            self._remember_appraisal_belief(observer_id, result.belief)
            self._emit_relationship_shifts(observer_id, result.deltas, tick)

    def _emit_relationship_shifts(
        self, observer_id: str, deltas: dict, tick: int
    ) -> None:
        """Surface Channel-A consequences as structured events (so a frontend can
        show 'X 怀疑 +0.2' inline). The descriptor carries the NPC's stance AFTER
        the shift; only player-perceivable (it's how the NPC feels toward the player)."""
        if self._event_sink is None or not deltas:
            return
        dims: dict = {}
        for snap in self.relationship_store.relationships_toward(self.player_id):
            if snap.npc_id == observer_id:
                dims = snap.dimensions
                break
        name = self.world.state.display_name(observer_id)
        for dim, delta in deltas.items():
            if not delta or abs(delta) < 0.01:
                continue
            self._emit(protocol.RelationshipShifted(
                tick=tick, npc_id=observer_id, name=name,
                descriptor=protocol.relationship_descriptor(dim, dims.get(dim, 0.0)),
                delta=float(delta),
            ))

    def _run_llm_jobs(self, jobs: list, fn) -> list:
        """Run independent LLM-bound jobs, concurrently for real network
        providers (PLAY-1) and serially for FakeLLM/replay (A10). Results are
        returned in the same order as ``jobs`` so callers stay deterministic."""
        if not jobs:
            return []
        if len(jobs) > 1 and getattr(self.llm_provider, "supports_concurrency", False):
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(len(jobs), 8)) as pool:
                return list(pool.map(fn, jobs))
        return [fn(job) for job in jobs]

    def _remember_appraisal_belief(self, npc_id: str, belief: str) -> None:
        """Write the appraisal's belief into the NPC's memory so the stance
        change is remembered and surfaces in later dialogue."""
        belief = (belief or "").strip()
        if not belief:
            return
        tick = self.world.state.tick
        self.memory_store.add(npc_id, Memory(
            memory_id=f"mem_appraisal_{npc_id}_{tick}",
            owner_id=npc_id,
            source_observation_id=None,
            tick=tick,
            content=belief,
            salience=0.7,
            decay_rate=0.05,
            layer=MemoryLayer.WORKING,
            topic_tags=["relationship", self.player_id],
        ))

    def get_relationship(self, npc_id: str, target_id: str):
        """Return the appraisal-accumulated relationship snapshot for a pair, or
        ``None`` if no appraised interaction has occurred yet (PLAY-3)."""
        self._flush_appraisals()  # never return stale data (PLAY-1 barrier)
        return self.relationship_store.get(npc_id, target_id)

    def _compress_memories_for_all(self, current_tick: int) -> None:
        """Run layered memory compaction for every memory owner (P3.3).

        For each owner whose working/short-term layer has crossed its threshold,
        the compressor returns a summary Memory; we then add the summary and
        remove the originals it folded in. Working→short is attempted first so a
        freshly produced short-term summary can feed short→long the same tick.
        """
        for owner_id in list(self.memory_store._data.keys()):
            for maybe in (
                self.memory_compressor.maybe_compress_working,
                self.memory_compressor.maybe_compress_short,
            ):
                summary = maybe(owner_id, current_tick)
                if summary is not None:
                    self.memory_store.add(owner_id, summary)
                    self.memory_store.remove(owner_id, set(summary.compression_of or []))

    def _build_pacing_context(self):
        """Derive the TickScheduler's pacing context from current session state.

        Returns the scheduler's internal PacingContext after updating it, so the
        next pacing evaluation reflects whether the player is in conversation /
        combat, whether the area is quiet, and the current campaign pressure.
        """
        in_combat = self.combat_engine.is_in_combat(self.player_id)
        in_conversation = (
            self.conversation_manager.get_active_session(self.player_id) is not None
        )

        # Co-located NPCs make the area "active"; an empty location is "safe".
        player_loc = self._get_player_location_id()
        others_here = any(
            e.entity_id != self.player_id and e.location_id == player_loc
            for e in self.world.state.entities.values()
        )

        self.tick_scheduler.update_context(
            player_in_conversation=in_conversation,
            combat_active=in_combat,
            player_in_safe_area=not others_here and not in_combat,
            no_pending_events=not others_here,
            campaign_pressure=self._latest_campaign_pressure,
        )
        return self.tick_scheduler._pacing_context

    def _advance_tick_with_pacing(
        self,
        player_driven: bool = True,
        allow_fast_forward: bool = False,
    ) -> int:
        """Advance world time honouring the current Pacing verdict.

        Pacing decides how many ticks the world moves this turn. A player-driven
        turn always advances at least one tick — pausing the player's own turn
        would stall the game, so PAUSE only suppresses *extra* autonomous
        advancement, not the base step.

        ``allow_fast_forward`` gates the FAST/FORCE acceleration: a normal/empty
        player input keeps it ``False`` so every ordinary turn advances exactly
        one tick (predictable). Only an explicit ``/skip`` / ``/wait`` opts in,
        letting a quiet area fast-forward. SLOW/PAUSE behaviour (conversation /
        combat hold to one step) is unaffected.

        The pacing context is expected to already be refreshed (the tick flow
        calls ``_build_pacing_context`` before advancing); this keeps the helper
        a pure "evaluate + advance" step so callers/tests can drive it directly.
        """
        speed = self.tick_scheduler.evaluate_pacing()
        steps = {
            PacingSpeed.PAUSE: 0,
            PacingSpeed.SLOW: 1,
            PacingSpeed.FAST: 2,
            PacingSpeed.FORCE: 3,
        }.get(speed, 1)
        if not allow_fast_forward:
            steps = min(steps, 1)  # collapse FAST/FORCE on ordinary turns
        if player_driven:
            steps = max(1, steps)
        for _ in range(steps):
            self.world.tick_advance()
        # Keep the scheduler's own counter mirrored to world time (it stays the
        # source of truth for arbiter/combat single-step paths too).
        self.tick_scheduler.tick = self.world.state.tick
        return steps

    def _check_campaign_drivers(self) -> str | None:
        """Check campaign drivers and generate pressure events if triggered.

        Returns a narrative string if any pressure events were generated, else None.
        """
        context = self._build_campaign_context()
        tick = self.world.state.tick
        seeds = self.campaign_driver_manager.check_all(context, tick)

        # A fired pressure event signals high world pressure; surface it to the
        # pacing policy so the FORCE rule can advance the world (design §6.5).
        self._latest_campaign_pressure = 0.9 if seeds else 0.0

        if not seeds:
            return None

        pressure_events: list[Event] = []
        for seed in seeds:
            event = Event(
                event_id=self.world.next_event_id(),
                event_type=EventType.SYSTEM,
                actor_id="system",
                tick=tick,
                location_id=self._get_player_location_id(),
                summary=f"[压力事件] {seed['event_type']}（来源: {seed['driver_id']}）",
                canonical_facts={
                    "source": "campaign_driver",
                    "driver_id": seed["driver_id"],
                    "event_type": seed["event_type"],
                },
                tags=["campaign_pressure"],
            )
            self.world.event_log.append(event)
            pressure_events.append(event)
            self._emit(protocol.PressureEvent(
                tick=tick, event_type=seed["event_type"],
                source=seed["driver_id"], summary=seed["event_type"],
            ))

        # Feed pressure events through observation/subjectivity
        self._process_events_for_subjectivity(pressure_events)

        # Build narrative
        return self.response_generator.generate(
            pressure_events, self.world.state, self.player_id
        )

    # OpenAI-compatible backends. Each vendor is pure config (base_url + model +
    # api_key from env) on the SAME generic provider — no per-vendor subclass.
    _OPENAI_COMPAT_PRESETS: dict[str, dict[str, Any]] = {
        "openai": {
            "base_url": "https://api.openai.com/v1", "base_url_env": "OPENAI_BASE_URL",
            "model": "gpt-4o-mini", "model_env": "OPENAI_MODEL", "key_env": "OPENAI_API_KEY",
            "extra_params": {},
        },
        "minimax": {
            "base_url": "https://api.minimaxi.com/v1", "base_url_env": "MINIMAX_BASE_URL",
            # MiniMax-M3 chosen after a head-to-head on real dialogue scenarios: it
            # honours thinking:disabled natively (clean content, no <think>), has the
            # fastest latency floor, keeps A5 secrets tighter, and actually engages
            # nuanced prompts where the M2.x line just deflected. The "-highspeed"
            # variants ramble on reasoning-heavy short tasks and run far slower here.
            # Override with MINIMAX_MODEL env.
            "model": "MiniMax-M3", "model_env": "MINIMAX_MODEL", "key_env": "MINIMAX_API_KEY",
            # Short structured tasks: disable thinking, and reasoning_split routes any
            # reasoning the model still emits into a separate field so `content`
            # streams clean (verified zero <think>/JSON leak). Cap output.
            "extra_params": {
                "thinking": {"type": "disabled"}, "reasoning_split": True,
                "max_completion_tokens": 2048,
            },
        },
    }

    def _build_openai_compatible_provider(self, preset_name: str) -> OpenAICompatibleProvider:
        """Build the generic OpenAI-compatible provider from a vendor preset,
        overridable via env vars. ``--llm minimax`` is just a preset, not a class."""
        import os
        p = self._OPENAI_COMPAT_PRESETS[preset_name]
        return OpenAICompatibleProvider(
            model=os.environ.get(p["model_env"], p["model"]),
            api_key=os.environ.get(p["key_env"], ""),
            base_url=os.environ.get(p["base_url_env"], p["base_url"]),
            extra_params=dict(p.get("extra_params", {})),
        )

    def _init_world_vars(self) -> None:
        """Load pack-declared world-state vars: record their specs (for the
        mutability gate) and seed any not already present in world state (so a
        loaded save keeps its values)."""
        self._world_var_specs = {}
        for spec in getattr(self.pack, "world_state_vars", []) or []:
            var_id = spec.get("var_id")
            if not var_id:
                continue
            self._world_var_specs[var_id] = spec
            if var_id not in self.world.state.world_vars:
                self.world.state.world_vars[var_id] = spec.get("initial")

    def get_world_var(self, var_id: str, default: Any = None) -> Any:
        """Read a world fact (PLAY-3 Channel C)."""
        return self.world.state.world_vars.get(var_id, default)

    def set_world_var(self, var_id: str, value: Any) -> bool:
        """Set a world fact, gated by pack declaration + mutability. Returns
        whether the change was applied (an undeclared or immutable var is a
        no-op, so neither the arbiter nor anything else can invent world facts)."""
        spec = self._world_var_specs.get(var_id)
        if spec is None or spec.get("mutable", True) is False:
            return False
        self.world.state.world_vars[var_id] = value
        self._emit(protocol.WorldVarChanged(
            tick=self.world.state.tick, var_id=var_id,
            label=spec.get("label", var_id), value=value,
        ))
        return True

    def _authority_npc_for(self, set_by: list) -> str | None:
        """The NPC whose `authority` attribute holds one of the `set_by` roles."""
        if not set_by:
            return None
        for eid, e in self.world.state.entities.items():
            if e.entity_type == "npc" and (e.attributes or {}).get("authority") in set_by:
                return eid
        return None

    def _world_vars_for_arbiter(self) -> list[dict[str, Any]]:
        """Mutable world vars enriched with the authority NPC and that NPC's
        stance toward the player, so the arbiter can adjudicate whether the
        authority would comply (Channel C)."""
        out: list[dict[str, Any]] = []
        for var_id, spec in self._world_var_specs.items():
            if spec.get("mutable", True) is False:
                continue
            set_by = spec.get("set_by") or []
            entry: dict[str, Any] = {
                "var_id": var_id,
                "label": spec.get("label", var_id),
                "current": self.world.state.world_vars.get(var_id, spec.get("initial")),
                "set_by": set_by,
            }
            auth = self._authority_npc_for(set_by)
            if auth:
                entry["authority_npc"] = auth.replace("npc.", "")
                snap = self.relationship_store.get(auth, self.player_id)
                if snap:
                    entry["authority_relationship"] = {
                        k: round(v, 2) for k, v in snap.dimensions.items() if v > 0
                    }
            out.append(entry)
        return out

    _QUESTION_MARKERS = ("？", "?", "吗", "呢", "为什么", "为何", "卡在哪", "顾虑", "是不是")

    @classmethod
    def _looks_like_question(cls, content: str) -> bool:
        c = (content or "").strip()
        return c.endswith(("？", "?")) or any(m in c for m in cls._QUESTION_MARKERS)

    def _world_change_request(self, action) -> tuple[str, str] | None:
        """If this is a SPEECH request to an NPC who *holds authority* over a
        mutable world var, and the content matches that var's request keywords,
        return (var_id, authority_npc_id); else None (Channel C trigger)."""
        if action.action_type != ActionType.SPEECH:
            return None
        target = action.target_id
        if not target or not target.startswith("npc."):
            return None
        entity = self.world.state.get_entity(target)
        if entity is None:
            return None
        target_authority = (entity.attributes or {}).get("authority")
        if not target_authority:
            return None
        # A5 — you can only persuade the authority who is PRESENT. A plea to someone
        # elsewhere doesn't reach them, so it doesn't route to world-change; it falls
        # through to ordinary speech. (A remote shout the engine adjudicates as
        # *heard* — via acoustic leak across connections, or a witness relaying a
        # report — is the future extension; see docs/design/tui-design / TODO.)
        player = self.world.state.get_entity(self.player_id)
        if player is None or entity.location_id != player.location_id:
            return None
        content = action.params.get("content") or ""
        # A *question/discussion* about the topic ("难民入营这事卡在哪儿？") is not a
        # command to change it — don't route it to the world-change adjudication.
        if self._looks_like_question(content):
            return None
        for var_id, spec in self._world_var_specs.items():
            if spec.get("mutable", True) is False:
                continue
            if target_authority not in (spec.get("set_by") or []):
                continue
            if any(k in content for k in (spec.get("request_keywords") or [])):
                return (var_id, target)
        return None

    # Map an arbiter outcome to how the authority NPC should voice it (the
    # arbiter's analytical reason stays internal — never shown to the player).
    _AUTHORITY_STANCE = {
        "success": "你决定答应这个请求",
        "partial_success": "你没有当场答应，但表示愿意考虑、或要求更多条件",
        "failure": "你拒绝了这个请求",
        "redirect": "你没有正面答应，把话题引向别处",
    }

    def _handle_world_change_request(self, action, var_id: str, authority_npc: str) -> str:
        """Adjudicate (via the arbiter) whether the authorized NPC complies with
        the player's world-changing request, factoring in their relationship. On
        compliance the world fact flips. The NPC's reply is then *voiced in
        character* (the arbiter's reasoning is never shown to the player)."""
        self._streamed_npc = None
        self.world.mutable_world_vars = self._world_vars_for_arbiter()
        outcome = self.arbiter.arbitrate(action, self.world)

        # The arbiter LLM proposes its verdict label and its state changes
        # independently, and can disagree with itself — labelling a verdict
        # "partial_success" ("我再想想") while still proposing to open the gate
        # (playtest B4). A world fact may flip ONLY on a genuine "success"; a
        # deferral/redirect/failure must leave the world untouched, so the voiced
        # line and the world state never contradict each other and the C-loop
        # isn't trivially won by a non-answer.
        if outcome.arbiter_output.outcome != "success":
            dropped = [
                c for c in outcome.accepted_state_changes
                if c.field == f"world.{var_id}"
            ]
            if dropped:
                outcome.accepted_state_changes = [
                    c for c in outcome.accepted_state_changes
                    if c.field != f"world.{var_id}"
                ]
                outcome.rejected_state_changes = list(
                    outcome.rejected_state_changes
                ) + dropped

        self._apply_state_changes(outcome)

        name = self.world.state.display_name(authority_npc)
        request = action.params.get("content") or action.raw_text or ""
        stance = self._AUTHORITY_STANCE.get(outcome.arbiter_output.outcome, "你做出了回应")
        directive = (
            f"刚才有人对你说：「{request}」。{stance}。"
            "用一句符合你身份的话回应对方（第一人称，不要分析、不要数字）。"
        )

        entity = self.world.state.get_entity(authority_npc)
        session = self.conversation_manager.get_active_session(authority_npc)
        line = None
        # Stream the reply live when possible (same UX as other NPC dialogue).
        if (self._stream_sink is not None or self._event_sink is not None) and getattr(
            self.llm_provider, "supports_streaming", False
        ):
            if self._stream_sink is not None:
                self._stream_sink(f"\n{name}：")
            line = self.npc_dialogue_generator.generate_line_stream(
                npc_id=authority_npc, entity=entity, world=self.world.state,
                memory_store=self.memory_store, conversation_session=session,
                on_delta=self._stream_delta_sink(authority_npc), directive=directive,
            )
            if self._stream_sink is not None:
                self._stream_sink("\n")
            if line:
                self._streamed_npc = authority_npc
        if not line:
            line = self.npc_dialogue_generator.generate_line(
                npc_id=authority_npc, entity=entity, world=self.world.state,
                memory_store=self.memory_store, conversation_session=session,
            )
        granted = any(c.field == f"world.{var_id}" for c in outcome.accepted_state_changes)
        line = line or ("……好吧，就照你说的办。" if granted else "不行，恕难从命。")

        # Surface the authority's reply as a structured event — a TUI/Godot frontend
        # consumes events, not the returned string. (The CLI's live stream already
        # showed it; this is purely additive and doesn't double-print there.)
        self._emit(protocol.NpcSpoke(
            tick=self.world.state.tick, npc_id=authority_npc, name=name, line=line))

        self.world.tick_advance()
        self._emit(protocol.TickAdvanced(
            tick=self.world.state.tick, new_tick=self.world.state.tick))
        # If streamed live, the line was already shown — don't repeat it.
        if self._streamed_npc == authority_npc:
            return ""
        return f"{name}：{line}"

    def _show_world(self) -> str:
        """Show the current mutable world facts (PLAY-3 Channel C)."""
        if not self._world_var_specs:
            return "这个世界没有声明可变的状态。"
        lines = ["=== 世界状态 ==="]
        for var_id, spec in self._world_var_specs.items():
            label = spec.get("label", var_id)
            val = self.world.state.world_vars.get(var_id, spec.get("initial"))
            lines.append(f"  {label}（{var_id}）: {val}")
        return "\n".join(lines)

    def _stance_topics_from_pack(self) -> dict[str, list[str]]:
        """Read pack-declared stance topics as {topic_id: [keywords]} (B2-A1)."""
        topics: dict[str, list[str]] = {}
        for t in getattr(self.pack, "stance_topics", []) or []:
            topic_id = t.get("topic_id")
            keywords = t.get("keywords") or []
            if topic_id and keywords:
                topics[topic_id] = list(keywords)
        return topics

    def _stance_labels_from_pack(self) -> dict[str, str]:
        """Read pack-declared stance topic display labels as {topic_id: label}."""
        labels: dict[str, str] = {}
        for t in getattr(self.pack, "stance_topics", []) or []:
            topic_id = t.get("topic_id")
            if topic_id and t.get("label"):
                labels[topic_id] = t["label"]
        return labels

    def _build_campaign_context(self) -> dict[str, Any]:
        """Build context dict for campaign driver signal evaluation."""
        context: dict[str, Any] = {}

        # Player's confirmed stances → world-readable flags, e.g. a pack driver
        # can condition on `stance.help_refugees` (B2-A1). A5-safe: this is the
        # world/plot layer reacting, not an NPC reading the player's mind.
        context["stance"] = {
            topic: True
            for topic in self.agenda_service.get_confirmed_stance_topics()
        }

        # Mutable world facts → `world.<var_id>` driver conditions (Channel C).
        context["world"] = dict(self.world.state.world_vars)

        # World-level metrics
        context["entity_count"] = len(self.world.state.entities)
        context["event_count"] = len(self.world.event_log)
        context["tick"] = self.world.state.tick

        # Player metrics
        player = self.world.state.get_entity(self.player_id)
        if player:
            context["player_hp"] = player.hp
            context["player_location"] = player.location_id

        # Aggregate NPC attributes as world metrics
        for eid, entity in self.world.state.entities.items():
            if eid == self.player_id:
                continue
            for attr_name, attr_val in entity.attributes.items():
                if isinstance(attr_val, (int, float)):
                    # Use attribute name directly as a world metric
                    if attr_name not in context:
                        context[attr_name] = attr_val
                    else:
                        # Average if multiple NPCs have the same attribute
                        context[attr_name] = (context[attr_name] + attr_val) / 2

        # Recent event pressure: count events in last 5 ticks
        current_tick = self.world.state.tick
        recent_events = self.world.event_log.get_events(
            since_tick=max(0, current_tick - 5)
        )
        context["recent_event_count"] = len(recent_events)
        context["recent_combat_count"] = sum(
            1 for e in recent_events if e.event_type == EventType.COMBAT
        )

        # Combat state
        context["combat_active"] = self.combat_engine.is_in_combat(self.player_id)

        # Threshold for content pack signal conditions
        context.setdefault("threshold", 1.5)

        return context

    def _get_player_location_id(self) -> str:
        """Get the player's current location ID."""
        player = self.world.state.get_entity(self.player_id)
        return player.location_id if player else "unknown"

    def _build_combat_narrative(self, events: list[Event]) -> str:
        """Build narrative from combat events."""
        return self.response_generator.generate(
            events, self.world.state, self.player_id
        )

    def look_around(self) -> str:
        """Describe the player's current surroundings."""
        player = self.world.state.get_entity(self.player_id)
        if player is None:
            return "You are nowhere."

        lines: list[str] = []
        lines.append(f"Location: {player.location_id}")
        if player.zone_id:
            lines.append(f"Zone: {player.zone_id}")

        others = [
            e for eid, e in self.world.state.entities.items()
            if eid != self.player_id
            and e.location_id == player.location_id
        ]
        if others:
            lines.append("You see:")
            for ent in others:
                zone_info = f" ({ent.zone_id})" if ent.zone_id else ""
                lines.append(f"  - {ent.entity_id}{zone_info}")
        else:
            lines.append("You are alone.")

        return "\n".join(lines)

    # -- REPL --

    # Slash commands offered for Tab completion (C-2).
    _COMPLETION_COMMANDS = (
        "/help", "/look", "/map", "/who", "/status", "/agenda", "/relationship",
        "/world", "/save", "/load",
        "/talk", "/endtalk", "/interrupt", "/combat", "/flee", "/wait", "/skip",
        "/hint", "/history", "/inspect", "/belief", "/memory", "/pack",
        "/inject", "/log", "/time", "/quit",
    )

    def _setup_readline(self) -> None:
        """Enable command Tab-completion (C-2) and ↑/↓ input history (C-3).

        Uses the stdlib ``readline`` (zero extra deps). If unavailable (e.g. a
        bare Windows shell) the REPL simply runs without these niceties.
        """
        try:
            import readline
        except ImportError:
            return

        commands = self._COMPLETION_COMMANDS

        def _completer(text: str, state: int):
            if not text.startswith("/"):
                return None
            matches = [c for c in commands if c.startswith(text)]
            return matches[state] if state < len(matches) else None

        readline.set_completer(_completer)
        # Treat '/' as part of a word so "/sa<Tab>" completes.
        try:
            readline.set_completer_delims(" \t\n")
        except Exception:
            pass
        # Bind Tab to completion across libedit (macOS) and GNU readline.
        if "libedit" in (getattr(readline, "__doc__", "") or ""):
            readline.parse_and_bind("bind ^I rl_complete")
        else:
            readline.parse_and_bind("tab: complete")
        # ↑/↓ history is on by default once readline is imported; nothing more
        # to do — each submitted line is auto-added to the history.

    def repl(self) -> None:
        """Run the interactive REPL loop."""
        self._setup_readline()
        print(self.formatter.header("RPG World Engine"))
        print(f"Content pack: {self.pack.content_pack_id}")
        print(f"Tick: {self.world.state.tick}")
        print(self.formatter.format_command_output(self.look_around(), cmd="look"))
        print(self._status_bar())
        print("Type /help for commands.\n")

        while self.running:
            prompt = "> "
            if self._conversation_mode:
                prompt = f"[talking to {self._conversation_mode}] > "
            try:
                user_input = input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                break

            if not user_input:
                self._empty_input_count += 1
                if self._empty_input_count == 1:
                    print(self.formatter.dim("  (Nearby sounds catch your attention. Type /look to inspect, or press Enter again to wait.)"))
                    continue
                # Second empty input: treat as wait
                self._empty_input_count = 0
                user_input = ""

            self._empty_input_count = 0

            if user_input.startswith("/"):
                result = self._handle_command(user_input)
                if result:
                    print(self.formatter.format_command_output(result, cmd=user_input[1:].split()[0]))
                print(self._status_bar())
                continue

            # Normal gameplay input. Stream the addressed NPC's reply live so the
            # player watches it appear instead of staring at a frozen screen.
            try:
                self.conversation_manager.check_timeouts(self.world.state.tick)
                self._stream_sink = lambda s: print(s, end="", flush=True)
                self._progress_sink = lambda m: print(self.formatter.dim(m), flush=True)
                try:
                    response = self.run_tick(user_input)
                finally:
                    self._stream_sink = None
                    self._progress_sink = None
                if response and response.strip():
                    print(self.formatter.format_narrative(response))
            except Exception as exc:
                print(self.formatter.red(f"[Error] {exc}"))
            print(self._status_bar())

    def _handle_command(self, cmd: str) -> str | None:
        """Handle slash commands. Return text to print, or None."""
        parts = cmd[1:].split(maxsplit=1)
        action = parts[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else ""

        if action == "quit" or action == "q":
            self.running = False
            return "Goodbye."

        if action == "help" or action == "h":
            return (
                "Commands:\n"
                "  /save         Save current game\n"
                "  /load <id>    Load a save\n"
                "  /look         Look around\n"
                "  /map          Show area map\n"
                "  /who          Show nearby NPCs\n"
                "  /status       Show player status\n"
                "  /agenda       Show player agenda (drives & intentions)\n"
                "  /relationship Show how nearby NPCs feel toward you\n"
                "  /world        Show mutable world facts your choices can change\n"
                "  /talk <npc>   Start conversation with NPC\n"
                "  /endtalk      End current conversation\n"
                "  /interrupt    Interrupt current conversation\n"
                "  /combat       Show combat status\n"
                "  /flee         Attempt to flee combat\n"
                "  /wait [n]     Pass n idle ticks (default 1)\n"
                "  /skip         Fast-forward through quiet time\n"
                "  /hint         Get contextual hint\n"
                "  /hint mode <none|subtle|normal|guided>  Set hint mode\n"
                "  /history [n]  Show last n events (default 10)\n"
                "  /inspect [id] Inspect entity (default player)\n"
                "  /belief [id]  Show beliefs for NPC (default player)\n"
                "  /memory [id]  Show memories for NPC (default player)\n"
                "  /inject <json>  [debug] Run an Action directly, e.g. "
                '{"verb":"look"}\n'
                "  /log filter <type|actor=id|tick=n>  [debug] Filter Event Log\n"
                "  /time [skip <n>]  [debug] Show tick, or fast-forward n ticks\n"
                "  /pack validate <path>  Validate a content pack\n"
                "  /pack export [path]    Export current world as content pack\n"
                "  /pack import <csv>     Import entities from CSV\n"
                "  /quit         Exit game"
            )

        if action == "look" or action == "l":
            return self.look_around()

        if action == "map" or action == "m":
            return self._show_map()

        if action == "status":
            return self._player_status()

        if action == "who" or action == "w":
            return self._show_who()

        if action == "agenda":
            return self._show_agenda()

        if action == "relationship" or action == "rel":
            return self._show_relationships()

        if action == "world":
            return self._show_world()

        if action == "save":
            self._flush_appraisals()  # don't save a half-applied tick (PLAY-1)
            save_data = self.persistence.save(
                world_core=self.world,
                tick=self.world.state.tick,
                content_pack_id=self.pack.content_pack_id,
                save_type="manual",
                scheduler_state=(
                    self.campaign_driver_manager.get_state()
                    if hasattr(self, "campaign_driver_manager")
                    and self.campaign_driver_manager is not None
                    else None
                ),
                agenda_state=self.agenda_service.get_state(),
                memory_state=self.memory_store.get_state(),
                belief_state=self.belief_engine.get_state(),
                combat_state=self.combat_engine.get_state(),
                conversation_state=self.conversation_manager.get_state(),
                npc_runtime_state={
                    "action_generator": self.npc_action_generator.get_state(),
                    "interaction_scheduler": self.npc_interaction_scheduler.get_state(),
                    "relationship_store": self.relationship_store.get_state(),
                },
            )
            return f"Saved: {save_data.save_id}"

        if action == "load":
            if not arg:
                saves = self.persistence.list_saves()
                if not saves:
                    return "No saves found."
                lines = ["Available saves:"]
                for s in saves:
                    lines.append(f"  {s['save_id']} (tick {s['tick']}, {s['save_type']})")
                return "\n".join(lines)
            try:
                save_data = self.persistence.load(arg)
                self.world = self.persistence.restore_world_core(save_data)
                pack_path = Path(f"fixtures/content_packs/{save_data.content_pack_id}.json")
                if pack_path.exists():
                    self.pack = CampaignLoader.load_from_file(pack_path)
                # Rebuild world-var specs from the pack; restored world state
                # already holds the saved values, so only missing vars are seeded.
                self._init_world_vars()

                # Restore subsystem states
                subj = save_data.subjectivity_state
                if "memories" in subj:
                    self.memory_store.load_state(subj["memories"])
                if "beliefs" in subj:
                    self.belief_engine.load_state(subj["beliefs"])

                player = save_data.player_state
                if "agenda" in player:
                    self.agenda_service.load_state(player["agenda"])

                sched = save_data.scheduler_state
                if sched and hasattr(self, "campaign_driver_manager"):
                    self.campaign_driver_manager.load_state(sched)

                combat = save_data.combat_state
                if combat:
                    self.combat_engine.load_state(combat)

                conv = save_data.conversation_state
                if conv:
                    self.conversation_manager.load_state(conv)

                npc = save_data.npc_runtime_state
                if npc and "action_generator" in npc:
                    self.npc_action_generator.load_state(npc["action_generator"])
                if npc and "interaction_scheduler" in npc:
                    self.npc_interaction_scheduler.load_state(npc["interaction_scheduler"])
                if npc and "relationship_store" in npc:
                    self.relationship_store.load_state(npc["relationship_store"])

                return f"Loaded: {arg} (tick {self.world.state.tick})"
            except FileNotFoundError:
                return f"Save not found: {arg}"

        if action == "talk":
            if not arg:
                # List nearby talkable NPCs
                return self._list_talkable_npcs()
            session = self.conversation_manager.start_session(
                initiator_id=self.player_id,
                participants=[arg],
                tick=self.world.state.tick,
            )
            self._conversation_mode = arg
            return f"Started conversation {session.session_id} with {arg}"

        if action == "endtalk":
            active = self.conversation_manager.get_active_session(self.player_id)
            if active is None:
                return "You are not in a conversation."
            self.conversation_manager.conclude(active.session_id, self.world.state.tick)
            self._conversation_mode = None
            return f"Conversation {active.session_id} concluded."

        if action == "interrupt":
            active = self.conversation_manager.get_active_session(self.player_id)
            if active is None:
                return "You are not in a conversation."
            self.conversation_manager.interrupt(
                active.session_id, "player_command", self.world.state.tick
            )
            self._conversation_mode = None
            return f"Conversation {active.session_id} interrupted."

        if action == "combat":
            return self._combat_status()

        if action == "flee":
            return self._handle_flee()

        if action == "hint":
            return self._handle_hint(arg)

        if action == "pack":
            return self._handle_pack_command(arg)

        # -- Debug / inspection commands --

        if action == "history":
            return self._show_history(arg)

        if action == "inspect":
            return self._inspect_entity(arg)

        if action == "belief":
            return self._show_beliefs(arg)

        if action == "memory":
            return self._show_memories(arg)

        if action == "wait":
            return self._handle_wait(arg)

        if action == "skip":
            return self._handle_skip()

        if action == "inject":
            return self._handle_inject(arg)

        if action == "log":
            return self._handle_log(arg)

        if action == "time":
            return self._handle_time(arg)

        return f"Unknown command: /{action}"

    # -- Time-control commands (P2.3) --

    MAX_SKIP_TICKS = 12  # safety cap for /skip fast-forward

    def _handle_wait(self, arg: str) -> str:
        """``/wait [n]`` — advance exactly n idle ticks (default 1).

        Unlike a bare empty input (which the Pacing Policy still holds to one
        tick), /wait lets the player deliberately pass a fixed number of ticks.
        """
        try:
            n = max(1, int(arg.strip())) if arg.strip() else 1
        except ValueError:
            return "用法：/wait [整数 tick 数]"
        n = min(n, self.MAX_SKIP_TICKS)
        lines: list[str] = []
        for _ in range(n):
            narrative = self.run_tick("")
            if narrative:
                lines.append(narrative)
        return "\n".join(lines) if lines else f"时间流逝了 {n} 个 tick。"

    def _handle_skip(self) -> str:
        """``/skip`` — fast-forward through quiet time.

        Advances the world (honouring the Pacing FAST/FORCE acceleration) while
        the area stays safe and idle, stopping as soon as something noteworthy
        appears (an NPC arrives, combat, pressure) or a safety cap is reached.
        """
        start = self.world.state.tick
        lines: list[str] = []
        steps = 0
        while steps < self.MAX_SKIP_TICKS:
            self._build_pacing_context()
            speed = self.tick_scheduler.evaluate_pacing()
            if speed not in (PacingSpeed.FAST, PacingSpeed.FORCE):
                break  # no longer quiet → stop fast-forwarding
            advanced = self._advance_tick_with_pacing(
                player_driven=True, allow_fast_forward=True
            )
            steps += advanced
            narrative = self._check_campaign_drivers()
            if narrative:
                lines.append(narrative)
        total = self.world.state.tick - start
        if total <= 0:
            return "周围并不安全，无法快进。"
        summary = f"你让时间快进了 {total} 个 tick。"
        return (summary + "\n" + "\n".join(lines)) if lines else summary

    def _handle_combat_action(self, action) -> str:
        """Handle a combat action: start or continue combat."""
        actor_id = action.actor_id
        verb = action.params.get("verb", "attack")
        target_id = action.target_id

        # Check if already in combat
        active = self.combat_engine.get_active_combat(actor_id)

        if active is None:
            # Start new combat
            if target_id is None:
                return "You need a target to attack."
            session = self.combat_engine.start_combat(
                initiator_id=actor_id,
                target_ids=[target_id],
                world=self.world.state,
                tick=self.world.state.tick,
            )
            # Interrupt any active conversation
            conv = self.conversation_manager.get_active_session(actor_id)
            if conv:
                self.conversation_manager.interrupt(
                    conv.session_id, "combat_started", self.world.state.tick
                )
            active = session

        # Resolve tick with actor action
        combat_action = PendingCombatAction(
            actor_id=actor_id,
            verb=verb,
            target_id=target_id,
        )
        events = self.combat_engine.resolve_tick(
            active.combat_id,
            self.world,
            self.world.state.tick,
            player_action=combat_action,
        )

        # Feed combat events into belief system
        self._process_events_for_subjectivity(events)

        # Build narrative
        return self._build_combat_narrative(events)

    def _combat_status(self) -> str:
        active = self.combat_engine.get_active_combat(self.player_id)
        if active is None:
            return "You are not in combat."
        lines = [f"Combat: {active.combat_id} at {active.location_id}"]
        for p in active.participants.values():
            status = ", ".join(p.status_effects) or "ok"
            lines.append(
                f"  {p.entity_id} ({p.side}): HP {p.hp}/{p.max_hp}, "
                f"STA {p.stamina}/{p.max_stamina} [{status}]"
            )
        return "\n".join(lines)

    def _handle_flee(self) -> str:
        active = self.combat_engine.get_active_combat(self.player_id)
        if active is None:
            return "You are not in combat."
        player_action = PendingCombatAction(
            actor_id=self.player_id,
            verb="flee",
        )
        events = self.combat_engine.resolve_tick(
            active.combat_id,
            self.world,
            self.world.state.tick,
            player_action=player_action,
        )
        self.world.tick_advance()
        return "\n".join(e.summary for e in events)

    def _handle_hint(self, arg: str) -> str:
        """Handle /hint and /hint mode <mode> commands."""
        if arg.startswith("mode "):
            mode_str = arg[5:].strip().lower()
            mode_map = {
                "none": SuggestionMode.NONE,
                "subtle": SuggestionMode.SUBTLE,
                "normal": SuggestionMode.NORMAL,
                "guided": SuggestionMode.GUIDED,
            }
            mode = mode_map.get(mode_str)
            if mode is None:
                return f"Unknown hint mode: {mode_str}. Use: none, subtle, normal, guided"
            self.hint_system.set_mode(mode)
            return f"Hint mode set to: {mode.value}"

        context = self._build_hint_context()
        hint = self.hint_system.generate_hint(context)
        return hint or "（暂无建议）"

    def _build_hint_context(self) -> HintContext:
        """Build a HintContext from current world state."""
        player = self.world.state.get_entity(self.player_id)
        if player is None:
            return HintContext(
                player_id=self.player_id,
                location_id="unknown",
                zone_id=None,
                nearby_entities=[],
                connected_locations=[],
            )

        nearby = [
            e.entity_id
            for eid, e in self.world.state.entities.items()
            if eid != self.player_id
            and e.location_id == player.location_id
        ]

        loc = self.world.state.locations.get(player.location_id)
        connected = [c.to_location for c in loc.connections] if loc else []

        active_conv = self.conversation_manager.get_active_session(self.player_id)

        # Recent events: last 3 events from event log
        recent = self.world.event_log.get_events(since_tick=max(0, self.world.state.tick - 3))

        return HintContext(
            player_id=self.player_id,
            location_id=player.location_id,
            zone_id=player.zone_id,
            nearby_entities=nearby,
            connected_locations=connected,
            in_combat=self.combat_engine.is_in_combat(self.player_id),
            active_conversation=active_conv,
            confirmed_drives=self.agenda_service._confirmed_drives,
            recent_events=recent,
        )

    def _status_bar(self) -> str:
        """Return the formatted status bar for the current player state."""
        player = self.world.state.get_entity(self.player_id)
        if player is None:
            return ""
        # Adapt the bar to the terminal width (C-4); clamp to a sane range.
        import shutil
        cols = shutil.get_terminal_size(fallback=(60, 24)).columns
        width = max(20, min(60, cols))
        return self.formatter.status_bar(
            hp=player.hp,
            max_hp=player.max_hp,
            location=player.location_id,
            zone=player.zone_id,
            tick=self.world.state.tick,
            in_combat=self.combat_engine.is_in_combat(self.player_id),
            width=width,
        )

    def _player_status(self) -> str:
        player = self.world.state.get_entity(self.player_id)
        if player is None:
            return "No player entity."
        lines = [
            f"Player: {player.entity_id}",
            f"Location: {player.location_id} / {player.zone_id or 'none'}",
            f"HP: {player.hp}/{player.max_hp}",
            f"Traits: {', '.join(player.traits) or 'none'}",
            f"Tick: {self.world.state.tick}",
        ]
        active_conv = self.conversation_manager.get_active_session(self.player_id)
        if active_conv:
            lines.append(
                f"Conversation: {active_conv.session_id} "
                f"(with {', '.join(p for p in active_conv.participants if p != self.player_id)})"
            )
        if self.combat_engine.is_in_combat(self.player_id):
            lines.append("Status: IN COMBAT")
        return "\n".join(lines)

    # Player-facing labels for relationship dimensions.
    _REL_DIM_LABELS = {
        "trust": "信任", "suspicion": "怀疑", "fear": "恐惧",
        "affection": "好感", "respect": "敬重", "familiarity": "熟悉",
    }

    def _show_relationships(self) -> str:
        """Show how nearby NPCs feel toward the player — the accumulated
        consequence of the player's choices (PLAY-3 Channel A)."""
        self._flush_appraisals()  # never display stale data (PLAY-1 barrier)
        snaps = self.relationship_store.relationships_toward(self.player_id)
        if not snaps:
            return "目前还没有人对你形成明确的看法。"
        lines = ["=== 他人对你的看法 ==="]
        for snap in snaps:
            name = snap.npc_id.replace("npc.", "")
            dims = {d: v for d, v in snap.dimensions.items() if v > 0}
            if not dims:
                continue
            parts = [
                f"{self._REL_DIM_LABELS.get(d, d)}({d}) {v:.2f}"
                for d, v in sorted(dims.items(), key=lambda x: x[1], reverse=True)
            ]
            lines.append(f"  {name}: " + "，".join(parts))
        return "\n".join(lines)

    def _show_agenda(self) -> str:
        """Show the player's current agenda: drives, inferences, intents."""
        agenda = self.agenda_service.get_agenda(current_tick=self.world.state.tick)
        lines: list[str] = [f"=== 议程 (Tick {agenda.tick}) ==="]

        # Confirmed drives
        if agenda.current_drives:
            lines.append("\n[已确认目标]")
            for d in agenda.current_drives:
                lines.append(f"  - {d.label} (强度: {d.strength}, 来源: {d.source})")
        else:
            lines.append("\n[已确认目标] 无")

        # System inferences (pending confirmation)
        if agenda.system_inferred:
            lines.append("\n[系统推断]")
            for inf in agenda.system_inferred:
                lines.append(f"  - {inf['claim']} (置信度: {inf['confidence']})")

        # Declared intents
        if agenda.declared_to_world:
            lines.append("\n[公开意图]")
            for intent in agenda.declared_to_world:
                lines.append(f"  - {intent}")

        if agenda.private_intent:
            lines.append("\n[隐藏意图]")
            for intent in agenda.private_intent:
                lines.append(f"  - {intent}")

        if agenda.open_questions:
            lines.append("\n[未解疑问]")
            for q in agenda.open_questions:
                lines.append(f"  - {q}")

        if agenda.long_term_aspirations:
            lines.append("\n[长期抱负]")
            for a in agenda.long_term_aspirations:
                lines.append(f"  - {a}")

        return "\n".join(lines)

    # -- Debug / inspection helpers --

    def _show_history(self, arg: str) -> str:
        """Show recent events from the Event Log."""
        try:
            n = int(arg) if arg else 10
        except ValueError:
            return "Usage: /history [n] — n must be a number."
        if n <= 0:
            return "Usage: /history [n] — n must be positive."

        events = self.world.event_log.get_events(since_tick=0)
        recent = events[-n:] if len(events) > n else events
        if not recent:
            return "No events recorded yet."

        lines: list[str] = [f"=== Recent Events (last {len(recent)} of {len(events)}) ==="]
        for e in recent:
            desc = e.summary or e.event_type.value
            actor = e.actor_id or "system"
            lines.append(f"  Tick {e.tick:>3} │ {e.event_type.value:<10} │ {actor:<12} │ {desc}")
        return "\n".join(lines)

    # -- Phase B debug commands --

    def _handle_inject(self, arg: str) -> str:
        """`/inject <json>` — build an Action from JSON and run it directly,
        bypassing the Intent Parser / Action Composer. Debug-only.

        JSON keys: `action_type` (speech|movement|physical|social|combat,
        default physical), plus the params for that type (e.g. verb, content,
        to_location, target). `target` maps to the Action's target_id.
        """
        if not arg.strip():
            return ('用法：/inject {"action_type":"physical","verb":"look"} '
                    '或 {"verb":"attack","target":"npc.guard_b"}')
        try:
            data = json.loads(arg)
        except json.JSONDecodeError as exc:
            return f"[inject] invalid JSON: {exc}"
        if not isinstance(data, dict):
            return "[inject] JSON must be an object"

        type_str = data.pop("action_type", None) or data.pop("type", None) or "physical"
        try:
            atype = ActionType(type_str)
        except ValueError:
            return f"[inject] unknown action_type: {type_str}"
        target = data.pop("target", None) or data.pop("target_id", None)
        params = dict(data)  # remaining keys are params (verb/content/to_location…)

        try:
            action = Action(
                action_id=self.world.next_action_id(),
                source_intent_id=None,
                actor_id=self.player_id,
                action_type=atype,
                target_id=target,
                params=params,
                zone_id=None,
                conversation_session_id=None,
                tick=self.world.state.tick,
            )
        except Exception as exc:  # pydantic validation (missing required params)
            return f"[inject] action error: {exc}"

        narrative = self._dispatch_player_action(action)
        return f"[inject] {narrative}"

    def _handle_log(self, arg: str) -> str:
        """`/log filter <type|actor=<id>|tick=<n>>` — filter the Event Log."""
        parts = arg.split(maxsplit=1)
        if not parts or parts[0].lower() != "filter" or len(parts) < 2:
            return ("用法：/log filter <event_type> | /log filter actor=<id> | "
                    "/log filter tick=<n>")
        spec = parts[1].strip()
        events = self.world.event_log.get_events(since_tick=0)

        if spec.startswith("actor="):
            key = spec[len("actor="):]
            matched = [e for e in events if e.actor_id == key]
            label = f"actor={key}"
        elif spec.startswith("tick="):
            try:
                tk = int(spec[len("tick="):])
            except ValueError:
                return "用法：/log filter tick=<整数>"
            matched = [e for e in events if e.tick == tk]
            label = f"tick={tk}"
        else:
            matched = [e for e in events if e.event_type.value == spec.lower()]
            label = f"type={spec.lower()}"

        if not matched:
            return f"No events match {label}."
        lines = [f"=== Events ({label}, {len(matched)}) ==="]
        for e in matched:
            actor = e.actor_id or "system"
            lines.append(
                f"  Tick {e.tick:>3} │ {e.event_type.value:<10} │ {actor:<12} │ "
                f"{e.summary or e.event_type.value}"
            )
        return "\n".join(lines)

    def _handle_time(self, arg: str) -> str:
        """`/time` shows the current tick; `/time skip <n>` fast-forwards the
        world by n ticks (player idles)."""
        parts = arg.split()
        if not parts:
            return f"Tick: {self.world.state.tick}"
        if parts[0].lower() == "skip":
            if len(parts) < 2:
                return "用法：/time skip <整数 tick 数>"
            try:
                n = int(parts[1])
            except ValueError:
                return "用法：/time skip <整数 tick 数>"
            if n <= 0:
                return "用法：/time skip <正整数>"
            start = self.world.state.tick
            for _ in range(n):
                self.world.tick_advance()
            self.tick_scheduler.tick = self.world.state.tick
            return f"快进 {n} tick（{start} → {self.world.state.tick}）。"
        return f"Tick: {self.world.state.tick}"

    def _inspect_entity(self, arg: str) -> str:
        """Inspect an entity's full state."""
        entity_id = arg if arg else self.player_id
        entity = self.world.state.get_entity(entity_id)
        if entity is None:
            return f"Entity not found: {entity_id}"

        lines: list[str] = [f"=== Inspect: {entity_id} ==="]
        lines.append(f"Type:       {entity.entity_type}")
        lines.append(f"Location:   {entity.location_id}")
        lines.append(f"Zone:       {entity.zone_id or 'none'}")
        lines.append(f"HP:         {entity.hp}/{entity.max_hp}")
        lines.append(f"Traits:     {', '.join(entity.traits) or 'none'}")
        lines.append(f"Attributes: {entity.attributes or 'none'}")
        lines.append(f"Inventory:  {', '.join(entity.inventory) or 'none'}")
        return "\n".join(lines)

    def _show_beliefs(self, arg: str) -> str:
        """Show beliefs for a given entity."""
        entity_id = arg if arg else self.player_id
        beliefs = self.belief_engine.get_beliefs(entity_id)
        if not beliefs:
            return f"No beliefs recorded for {entity_id}."

        lines: list[str] = [f"=== Beliefs: {entity_id} ({len(beliefs)} total) ==="]
        for b in beliefs:
            conf_emoji = "█" * int(b.confidence * 10) + "░" * (10 - int(b.confidence * 10))
            lines.append(
                f"  [{conf_emoji}] {b.claim} "
                f"(conviction: {b.conviction.value}, tick: {b.formed_at_tick})"
            )
        return "\n".join(lines)

    def _show_memories(self, arg: str) -> str:
        """Show memories for a given entity."""
        entity_id = arg if arg else self.player_id
        memories = self.memory_store.get_all(entity_id)
        if not memories:
            return f"No memories recorded for {entity_id}."

        lines: list[str] = [f"=== Memories: {entity_id} ({len(memories)} total) ==="]
        # Group by layer
        from verisaria.engine.schemas import MemoryLayer
        layers = [
            MemoryLayer.WORKING,
            MemoryLayer.SHORT_TERM,
            MemoryLayer.LONG_TERM,
        ]
        for layer in layers:
            layer_mems = [m for m in memories if m.layer == layer]
            if not layer_mems:
                continue
            lines.append(f"\n[{layer.value} — {len(layer_mems)}]")
            for m in layer_mems:
                sal = int(m.salience * 100)
                lines.append(f"  Tick {m.tick:>3} │ salience {sal:>3}% │ {m.content}")
        return "\n".join(lines)

    def _show_map(self) -> str:
        """Show a textual map of the player's surroundings."""
        player = self.world.state.get_entity(self.player_id)
        if player is None:
            return "You are nowhere."

        current_loc = self.world.state.locations.get(player.location_id)
        if current_loc is None:
            return f"Unknown location: {player.location_id}"

        lines: list[str] = [f"=== Map: {player.location_id} ==="]

        # Current zone
        if player.zone_id:
            lines.append(f"You are in: {player.zone_id}")

        # Zones in current location
        if current_loc.zones:
            lines.append("\n[Zones]")
            for zid, zone in current_loc.zones.items():
                marker = " ★" if zid == player.zone_id else ""
                occ_count = len(zone.occupant_ids)
                occ_str = f" ({occ_count} occupants)" if occ_count > 0 else ""
                lines.append(f"  {zid}{marker}{occ_str}")

        # Occupants in current location (grouped by zone)
        entities_here = [
            e for eid, e in self.world.state.entities.items()
            if e.location_id == player.location_id
        ]
        if entities_here:
            lines.append("\n[Here]")
            for ent in entities_here:
                marker = " ★ You" if ent.entity_id == self.player_id else ""
                zone_str = f" @{ent.zone_id}" if ent.zone_id else ""
                lines.append(f"  {ent.entity_id}{zone_str}{marker}")

        # Connections
        if current_loc.connections:
            lines.append("\n[Exits]")
            for conn in current_loc.connections:
                dist = conn.distance
                desc = conn.description or f"to {conn.to_location}"
                lines.append(f"  → {conn.to_location} ({dist}) — {desc}")
        elif current_loc.connected_locations:
            lines.append("\n[Exits]")
            for loc_id in current_loc.connected_locations:
                lines.append(f"  → {loc_id}")

        # Other known locations (summary)
        other_locs = [
            lid for lid in self.world.state.locations
            if lid != player.location_id
        ]
        if other_locs and not current_loc.connections and not current_loc.connected_locations:
            lines.append("\n[Other Locations]")
            for lid in other_locs:
                loc = self.world.state.locations[lid]
                ent_count = sum(1 for e in self.world.state.entities.values() if e.location_id == lid)
                lines.append(f"  {lid} ({len(loc.zones)} zones, {ent_count} entities)")

        return "\n".join(lines)

    def _show_who(self) -> str:
        """Show a social radar of nearby NPCs."""
        player = self.world.state.get_entity(self.player_id)
        if player is None:
            return "You are nowhere."

        npcs = [
            e for eid, e in self.world.state.entities.items()
            if e.entity_type == "npc"
            and e.location_id == player.location_id
        ]
        if not npcs:
            return "You are alone."

        lines: list[str] = [f"=== Nearby (at {player.location_id}) ==="]
        for npc in npcs:
            # Distance
            if npc.zone_id and player.zone_id and npc.zone_id == player.zone_id:
                distance = "here"
            else:
                distance = "nearby"

            # Attitude from relationship
            attitude = "unknown"
            if hasattr(self.pack, "initial_relationships"):
                for rel in self.pack.initial_relationships:
                    if rel.get("npc_id") == npc.entity_id and rel.get("target_id") == self.player_id:
                        attitude = rel.get("type", "unknown")
                        break

            # Activity hint
            in_combat = self.combat_engine.is_in_combat(npc.entity_id)
            in_conv = self.conversation_manager.get_active_session(npc.entity_id)
            status = ""
            if in_combat:
                status = " [in combat]"
            elif in_conv:
                status = " [talking]"

            zone_str = f" @{npc.zone_id}" if npc.zone_id else ""
            lines.append(
                f"  {npc.entity_id}{zone_str} — {distance}, {attitude}{status}"
            )
        return "\n".join(lines)

    def _list_talkable_npcs(self) -> str:
        """List NPCs in the same location who can be talked to."""
        player = self.world.state.get_entity(self.player_id)
        if player is None:
            return "You are nowhere."

        npcs = [
            e for eid, e in self.world.state.entities.items()
            if e.entity_type == "npc"
            and e.location_id == player.location_id
        ]
        if not npcs:
            return "No one nearby to talk to."

        lines: list[str] = ["Nearby NPCs:"]
        for npc in npcs:
            zone_str = f" @{npc.zone_id}" if npc.zone_id else ""
            # Try to infer attitude from relationship if available
            attitude = ""
            if hasattr(self.pack, "initial_relationships"):
                for rel in self.pack.initial_relationships:
                    if rel.get("npc_id") == npc.entity_id and rel.get("target_id") == self.player_id:
                        attitude = f" ({rel.get('type', 'unknown')})"
                        break
            lines.append(f"  {npc.entity_id}{zone_str}{attitude}")
        lines.append("\nUsage: /talk <npc_id>")
        return "\n".join(lines)

    def _handle_pack_command(self, arg: str) -> str:
        """Handle /pack subcommands: validate, export, import."""
        parts = arg.split(maxsplit=1)
        subcmd = parts[0].lower() if parts else ""
        subarg = parts[1] if len(parts) > 1 else ""

        if subcmd == "validate":
            if not subarg:
                return "Usage: /pack validate <path>"
            try:
                result = PackEditor.validate_pack(subarg)
                return PackEditor.format_validation(result)
            except Exception as exc:
                return f"Failed to validate: {exc}"

        if subcmd == "export":
            path = subarg or f"saves/export_{self.pack.content_pack_id}_{self.world.state.tick}.json"
            try:
                pack_data = PackEditor.export_from_world(
                    self.world.state,
                    content_pack_id=f"{self.pack.content_pack_id}_export",
                )
                saved_path = PackEditor.save_pack(pack_data, path)
                return f"Exported to: {saved_path}"
            except Exception as exc:
                return f"Failed to export: {exc}"

        if subcmd == "import":
            if not subarg:
                return "Usage: /pack import <csv_path>"
            try:
                new_entities = PackEditor.import_entities_from_csv(subarg)
                count = len(new_entities)
                # Add to current world
                from verisaria.engine.world import EntityState
                for ent_data in new_entities:
                    eid = ent_data.get("entity_id")
                    if not eid or eid in self.world.state.entities:
                        continue
                    attrs = dict(ent_data.get("attributes", {}))
                    max_stamina = ent_data.get("max_stamina", 100)
                    stamina = ent_data.get("stamina", attrs.pop("stamina", max_stamina))
                    self.world.state.entities[eid] = EntityState(
                        entity_id=eid,
                        entity_type=ent_data.get("entity_type", "npc"),
                        location_id=ent_data.get("location_id", "void"),
                        zone_id=ent_data.get("zone_id"),
                        attributes=attrs,
                        traits=ent_data.get("traits", []),
                        inventory=ent_data.get("inventory", []),
                        hp=ent_data.get("hp", 100),
                        max_hp=ent_data.get("max_hp", 100),
                        stamina=int(stamina),
                        max_stamina=max_stamina,
                    )
                return f"Imported {count} entities from {subarg}."
            except Exception as exc:
                return f"Failed to import: {exc}"

        return (
            "Pack commands:\n"
            "  /pack validate <path>  Validate a content pack\n"
            "  /pack export [path]    Export current world as content pack\n"
            "  /pack import <csv>     Import entities from CSV"
        )

