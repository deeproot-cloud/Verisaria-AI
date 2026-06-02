"""Interaction layer: Action Composer and player interaction management.

Action Composer converts ParsedIntent into Action objects.
Interaction Service manages the clarification loop and conversation state.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass

from verisaria.engine.schemas import (
    Action,
    ActionType,
    CommitmentLevel,
    ParsedIntent,
)

if typing.TYPE_CHECKING:
    from verisaria.engine.conversation import ConversationManager


# ---------------------------------------------------------------------------
# Action Composer
# ---------------------------------------------------------------------------

class ActionComposer:
    """Convert ParsedIntent into Action.

    Responsibilities:
    - Map intent_type to action_type
    - Extract params from intent modifiers/content
    - Handle commitment levels (considering → no action)
    - Generate action_id
    """

    def compose(
        self,
        intent: ParsedIntent,
        tick: int,
        seq: int = 1,
        world = None,
    ) -> Action | None:
        """Compose an Action from a ParsedIntent.

        Returns None if commitment is 'considering' (no world effect).
        For 'preparing', returns a preview Action that should not be committed.
        """
        if intent.commitment == CommitmentLevel.CONSIDERING:
            return None

        action_type = self._map_intent_type(intent.intent_type)

        # Force combat type for combat-related raw text (LLM may map these to PHYSICAL)
        raw_lower = intent.raw_text.lower()
        if any(v in raw_lower for v in ("防御", "defend", "闪避", "dodge", "逃跑", "flee")):
            action_type = ActionType.COMBAT

        params = self._build_params(intent, action_type, world)

        return Action(
            action_id=f"act_{tick}_{seq}",
            source_intent_id=intent.intent_id,
            actor_id=intent.actor_id,
            action_type=action_type,
            target_id=intent.target_id,
            params=params,
            zone_id=None,  # populated by world/state if needed
            conversation_session_id=intent.conversation_session_id,
            tick=tick,
        )

    def _map_intent_type(self, intent_type) -> ActionType:
        """Map intent type enum to action type enum."""
        # Direct mappings
        if intent_type in (ActionType.SPEECH, ActionType.MOVEMENT,
                          ActionType.PHYSICAL, ActionType.SOCIAL,
                          ActionType.COMBAT):
            return intent_type
        # Special cases
        if intent_type == ActionType.LOOK:
            return ActionType.PHYSICAL
        if intent_type == ActionType.WAIT:
            return ActionType.PHYSICAL
        # Default fallback
        return ActionType.PHYSICAL

    def _build_params(self, intent: ParsedIntent, action_type: ActionType, world = None) -> dict:
        """Build action params based on intent content and modifiers."""
        params: dict = {}
        mods = intent.modifiers or {}

        if action_type == ActionType.SPEECH:
            params["content"] = intent.performed_content or intent.content or ""
            if mods.get("volume"):
                params["volume"] = mods["volume"]
            if mods.get("emotion"):
                params["emotion"] = mods["emotion"]

        elif action_type == ActionType.MOVEMENT:
            to_location = mods.get("to_location")
            if to_location:
                # Validate/resovle if world is available; otherwise use as-is.
                if world is not None:
                    loc = world.locations.get(to_location)
                    if loc is not None:
                        params["to_location"] = to_location
                    else:
                        entity = world.get_entity(to_location)
                        if entity is not None:
                            params["to_location"] = entity.location_id
                        else:
                            params["to_location"] = to_location
                else:
                    params["to_location"] = to_location
            elif intent.target_id and world is not None:
                loc = world.locations.get(intent.target_id)
                if loc is not None:
                    params["to_location"] = intent.target_id
                else:
                    entity = world.get_entity(intent.target_id)
                    if entity is not None:
                        params["to_location"] = entity.location_id
            params["to_zone"] = mods.get("to_zone")

        elif action_type == ActionType.PHYSICAL:
            # look/wait/steal etc.
            verb = self._infer_verb(intent)
            params["verb"] = verb
            if intent.target_id:
                params["target"] = intent.target_id

        elif action_type in (ActionType.SOCIAL, ActionType.COMBAT):
            verb = self._infer_verb(intent)
            params["verb"] = verb
            if intent.target_id:
                params["target"] = intent.target_id

        # Merge any remaining modifiers as extra params
        for key, value in mods.items():
            if key not in params and key not in ("to_location", "to_zone", "volume", "emotion"):
                params[key] = value

        return params

    def _infer_verb(self, intent: ParsedIntent) -> str:
        """Infer the specific verb from intent type and content."""
        intent_type = intent.intent_type.value
        raw = intent.raw_text.lower()

        # Type-specific verbs (check first to avoid false matches)
        if intent_type == "social":
            # Greetings / chitchat are not a persuasion contest — give them a
            # non-contest verb so the Rules Engine resolves them directly and
            # the arbiter's meta-hint never leaks to the player. (P0.4)
            greet_markers = ("打招呼", "问好", "寒暄", "你好", "招呼", "问候", "搭话", "greet", "hello", "hi ")
            if any(m in raw for m in greet_markers):
                return "greet"
            if "说服" in raw or "persuade" in raw:
                return "persuade"
            if "恐吓" in raw or "intimidate" in raw:
                return "intimidate"
            if "欺骗" in raw or "deceive" in raw:
                return "deceive"
            if "贿赂" in raw or "bribe" in raw:
                return "bribe"
            return "persuade"  # default for social

        if intent_type == "combat":
            if "攻击" in raw or "attack" in raw:
                return "attack"
            if "防御" in raw or "defend" in raw:
                return "defend"
            if "闪避" in raw or "dodge" in raw:
                return "dodge"
            if "逃跑" in raw or "flee" in raw or "逃离" in raw:
                return "flee"
            return "attack"  # default for combat

        # Physical / movement / look / wait
        if intent_type == "look" or "看" in raw or " examine" in raw:
            return "look"
        if intent_type == "wait" or "等" in raw or "wait" in raw:
            return "wait"
        if "防御" in raw or "defend" in raw:
            return "defend"
        if "闪避" in raw or "dodge" in raw:
            return "dodge"
        if "逃跑" in raw or "flee" in raw or "逃离" in raw:
            return "flee"
        if "偷" in raw or "steal" in raw:
            return "steal"
        if "爬" in raw or "climb" in raw:
            return "climb"
        if "走" in raw or "move" in raw or "去" in raw:
            return "move"
        if "说" in raw or "speak" in raw or "talk" in raw:
            return "talk"

        return intent_type


# ---------------------------------------------------------------------------
# Interaction Service
# ---------------------------------------------------------------------------

@dataclass
class InteractionResult:
    action: Action | None
    clarification_needed: bool = False
    clarification_request: dict | None = None
    preview_only: bool = False  # True for preparing commitment


class InteractionService:
    """Manage player interaction flow.

    Handles the loop: input → parse → clarify? → compose → execute.
    """

    def __init__(
        self,
        action_composer: ActionComposer | None = None,
        conversation_manager: ConversationManager | None = None,
    ) -> None:
        self.composer = action_composer or ActionComposer()
        self.conversation_manager = conversation_manager
        self._pending_clarifications: dict[str, ParsedIntent] = {}

    def process_intent(
        self,
        intent: ParsedIntent,
        tick: int,
        seq: int = 1,
        world = None,
    ) -> InteractionResult:
        """Process a ParsedIntent and produce an Action or clarification."""
        # Handle commitment levels
        if intent.commitment == CommitmentLevel.CONSIDERING:
            return InteractionResult(
                action=None,
                preview_only=False,
            )

        # Compose action
        action = self.composer.compose(intent, tick=tick, seq=seq, world=world)

        if action is None:
            return InteractionResult(action=None)

        # Attach conversation session for speech intents
        action = self._attach_conversation_session(action, intent)

        # Preparing = preview only, don't commit to world
        if intent.commitment == CommitmentLevel.PREPARING:
            return InteractionResult(
                action=action,
                preview_only=True,
            )

        return InteractionResult(action=action)

    def _attach_conversation_session(self, action: Action, intent: ParsedIntent) -> Action:
        """Link speech actions to an active ConversationSession."""
        if action.action_type != ActionType.SPEECH:
            return action
        if self.conversation_manager is None:
            return action

        # If intent already carries a session_id, use it
        if intent.conversation_session_id:
            action.conversation_session_id = intent.conversation_session_id
            self.conversation_manager.process_turn(
                session_id=intent.conversation_session_id,
                speaker_id=intent.actor_id,
                content=intent.content or "",
                tick=action.tick,
            )
            return action

        # Check if actor is already in an active session
        active_session = self.conversation_manager.get_active_session(intent.actor_id)
        if active_session is not None:
            # Verify target is a participant (or no target)
            if intent.target_id is None or intent.target_id in active_session.participants:
                action.conversation_session_id = active_session.session_id
                self.conversation_manager.process_turn(
                    session_id=active_session.session_id,
                    speaker_id=intent.actor_id,
                    content=intent.content or "",
                    tick=action.tick,
                )
                return action

        # Auto-start a new session if target is specified
        if intent.target_id and intent.target_id != intent.actor_id:
            session = self.conversation_manager.start_session(
                initiator_id=intent.actor_id,
                participants=[intent.target_id],
                tick=action.tick,
            )
            action.conversation_session_id = session.session_id
            self.conversation_manager.process_turn(
                session_id=session.session_id,
                speaker_id=intent.actor_id,
                content=intent.content or "",
                tick=action.tick,
            )

        return action
