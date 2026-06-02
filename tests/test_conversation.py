"""Tests for Conversation Manager.

Covers session lifecycle, state machine, timeout detection,
pronoun resolution, and integration with InteractionService.
"""

from __future__ import annotations

import pytest

from verisaria.engine.conversation import ConversationManager
from verisaria.engine.interaction import ActionComposer, InteractionService
from verisaria.engine.schemas import (
    ActionType,
    CommitmentLevel,
    ConversationSession,
    ParsedIntent,
)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

class TestSessionLifecycle:
    def test_start_session(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["npc_a"], tick=1)
        assert session.session_id.startswith("conv_")
        assert session.participants == ["player", "npc_a"]
        assert session.status == "active"
        assert session.started_at_tick == 1

    def test_start_session_dedupes_participants(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["player", "npc_a"], tick=1)
        assert session.participants == ["player", "npc_a"]

    def test_process_turn(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["npc_a"], tick=1)
        updated = mgr.process_turn(session.session_id, "player", "你好", tick=2)
        assert updated is not None
        assert updated.turn_count == 1
        assert updated.last_activity_tick == 2
        assert "last_speaker" in updated.shared_context

    def test_process_turn_on_nonexistent_session(self):
        mgr = ConversationManager()
        assert mgr.process_turn("conv_missing", "player", "hi", tick=1) is None

    def test_conclude(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["npc_a"], tick=1)
        mgr.process_turn(session.session_id, "player", "bye", tick=2)
        concluded = mgr.conclude(session.session_id, tick=3)
        assert concluded is not None
        assert concluded.status == "concluded"

    def test_abandon(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["npc_a"], tick=1)
        abandoned = mgr.abandon(session.session_id, tick=5)
        assert abandoned is not None
        assert abandoned.status == "abandoned"

    def test_conclude_clears_entity_mapping(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["npc_a"], tick=1)
        assert mgr.get_active_session("player") is not None
        mgr.conclude(session.session_id, tick=2)
        assert mgr.get_active_session("player") is None


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class TestStateMachine:
    def test_cannot_turn_in_interrupted_session(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["npc_a"], tick=1)
        mgr.interrupt(session.session_id, "combat", tick=2)
        result = mgr.process_turn(session.session_id, "player", "hi", tick=3)
        assert result is None

    def test_cannot_resume_concluded_session(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["npc_a"], tick=1)
        mgr.conclude(session.session_id, tick=2)
        result = mgr.resume(session.session_id, tick=3)
        assert result is None

    def test_resume_interrupted(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["npc_a"], tick=1)
        mgr.interrupt(session.session_id, "combat", tick=2)
        resumed = mgr.resume(session.session_id, tick=3)
        assert resumed is not None
        assert resumed.status == "active"
        assert mgr.get_active_session("player") is not None

    def test_abandon_is_terminal(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["npc_a"], tick=1)
        mgr.abandon(session.session_id, tick=2)
        assert mgr.conclude(session.session_id, tick=3) is None
        assert mgr.resume(session.session_id, tick=3) is None


# ---------------------------------------------------------------------------
# Timeout detection
# ---------------------------------------------------------------------------

class TestTimeout:
    def test_timeout_abandons_stale_session(self):
        mgr = ConversationManager(timeout_ticks=5)
        session = mgr.start_session("player", ["npc_a"], tick=1)
        mgr.process_turn(session.session_id, "player", "hi", tick=2)

        abandoned = mgr.check_timeouts(current_tick=8)
        assert len(abandoned) == 1
        assert abandoned[0].status == "abandoned"
        assert mgr.get_active_session("player") is None

    def test_no_timeout_for_recent_activity(self):
        mgr = ConversationManager(timeout_ticks=10)
        session = mgr.start_session("player", ["npc_a"], tick=1)
        mgr.process_turn(session.session_id, "player", "hi", tick=5)

        abandoned = mgr.check_timeouts(current_tick=14)
        assert len(abandoned) == 0
        assert mgr.get_active_session("player") is not None

    def test_only_active_sessions_checked(self):
        mgr = ConversationManager(timeout_ticks=5)
        session = mgr.start_session("player", ["npc_a"], tick=1)
        mgr.conclude(session.session_id, tick=2)

        abandoned = mgr.check_timeouts(current_tick=20)
        assert len(abandoned) == 0


# ---------------------------------------------------------------------------
# Topic extraction
# ---------------------------------------------------------------------------

class TestTopicExtraction:
    def test_extracts_keywords(self):
        mgr = ConversationManager()
        topics = mgr._extract_topics("iron sword quality")
        assert "iron" in topics
        assert "sword" in topics
        assert "quality" in topics

    def test_extracts_chinese_keywords(self):
        mgr = ConversationManager()
        topics = mgr._extract_topics("铁匠铺的剑质量很好")
        # Naive extraction treats consecutive CJK as one token
        assert len(topics) >= 1
        assert "铁匠铺" in topics[0]

    def test_filters_stop_words(self):
        mgr = ConversationManager()
        topics = mgr._extract_topics("the quick brown fox")
        assert "the" not in topics
        assert "quick" in topics

    def test_limits_to_five(self):
        mgr = ConversationManager()
        topics = mgr._extract_topics("a b c d e f g h i j")
        assert len(topics) <= 5


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------

class TestReferenceResolution:
    def test_resolve_chinese_pronoun(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["npc_a"], tick=1)
        mgr.process_turn(session.session_id, "player", "你好", tick=2)
        resolved = mgr.resolve_reference(session.session_id, "他在哪里？")
        assert "npc_a" in resolved

    def test_resolve_english_pronoun(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["npc_guard"], tick=1)
        mgr.process_turn(session.session_id, "player", "hello", tick=2)
        resolved = mgr.resolve_reference(session.session_id, "Where is he?")
        assert "npc_guard" in resolved

    def test_resolve_demonstrative(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["npc_a"], tick=1)
        mgr.process_turn(session.session_id, "player", "关于铁匠铺", tick=2)
        resolved = mgr.resolve_reference(session.session_id, "这个很重要")
        assert "铁匠铺" in resolved

    def test_no_session_returns_original(self):
        mgr = ConversationManager()
        text = "他在哪里？"
        assert mgr.resolve_reference("missing", text) == text


# ---------------------------------------------------------------------------
# Integration with InteractionService
# ---------------------------------------------------------------------------

class TestInteractionServiceIntegration:
    def test_speech_intent_auto_attaches_session(self):
        mgr = ConversationManager()
        service = InteractionService(ActionComposer(), conversation_manager=mgr)

        intent = ParsedIntent(
            intent_id="int_1",
            source="natural_language",
            raw_text="你好 npc_a",
            intent_type=ActionType.SPEECH,
            actor_id="player",
            target_id="npc_a",
            content="你好",
            commitment=CommitmentLevel.COMMITTED,
            confidence=1.0,
            timestamp=1,
        )
        result = service.process_intent(intent, tick=1)
        assert result.action is not None
        assert result.action.conversation_session_id is not None
        session = mgr.get_session(result.action.conversation_session_id)
        assert session is not None
        assert "player" in session.participants
        assert "npc_a" in session.participants
        assert session.turn_count == 1

    def test_non_speech_does_not_create_session(self):
        mgr = ConversationManager()
        service = InteractionService(ActionComposer(), conversation_manager=mgr)

        intent = ParsedIntent(
            intent_id="int_1",
            source="quick_command",
            raw_text="move to loc_a zone_1",
            intent_type=ActionType.MOVEMENT,
            actor_id="player",
            target_id=None,
            content=None,
            commitment=CommitmentLevel.COMMITTED,
            confidence=1.0,
            timestamp=1,
            modifiers={"to_location": "loc_a", "to_zone": "zone_1"},
        )
        result = service.process_intent(intent, tick=1)
        assert result.action is not None
        assert result.action.conversation_session_id is None
        assert mgr.get_active_session("player") is None

    def test_existing_session_reused(self):
        mgr = ConversationManager()
        service = InteractionService(ActionComposer(), conversation_manager=mgr)

        # First speech starts session
        intent1 = ParsedIntent(
            intent_id="int_1",
            source="natural_language",
            raw_text="hello npc_a",
            intent_type=ActionType.SPEECH,
            actor_id="player",
            target_id="npc_a",
            content="hello",
            commitment=CommitmentLevel.COMMITTED,
            confidence=1.0,
            timestamp=1,
        )
        r1 = service.process_intent(intent1, tick=1)
        sid = r1.action.conversation_session_id

        # Second speech reuses same session
        intent2 = ParsedIntent(
            intent_id="int_2",
            source="natural_language",
            raw_text="goodbye npc_a",
            intent_type=ActionType.SPEECH,
            actor_id="player",
            target_id="npc_a",
            content="goodbye",
            commitment=CommitmentLevel.COMMITTED,
            confidence=1.0,
            timestamp=2,
        )
        r2 = service.process_intent(intent2, tick=2)
        assert r2.action.conversation_session_id == sid
        session = mgr.get_session(sid)
        assert session.turn_count == 2

    def test_speech_without_target_no_session(self):
        mgr = ConversationManager()
        service = InteractionService(ActionComposer(), conversation_manager=mgr)

        intent = ParsedIntent(
            intent_id="int_1",
            source="natural_language",
            raw_text="shouting into the void",
            intent_type=ActionType.SPEECH,
            actor_id="player",
            target_id=None,
            content="shouting into the void",
            commitment=CommitmentLevel.COMMITTED,
            confidence=1.0,
            timestamp=1,
        )
        result = service.process_intent(intent, tick=1)
        assert result.action is not None
        assert result.action.conversation_session_id is None

    def test_intent_with_session_id_uses_it(self):
        mgr = ConversationManager()
        session = mgr.start_session("player", ["npc_a", "npc_b"], tick=1)
        service = InteractionService(ActionComposer(), conversation_manager=mgr)

        intent = ParsedIntent(
            intent_id="int_1",
            source="natural_language",
            raw_text="hello npc_b",
            intent_type=ActionType.SPEECH,
            actor_id="player",
            target_id="npc_b",
            content="hello",
            commitment=CommitmentLevel.COMMITTED,
            confidence=1.0,
            timestamp=2,
            conversation_session_id=session.session_id,
        )
        result = service.process_intent(intent, tick=2)
        assert result.action.conversation_session_id == session.session_id
        assert mgr.get_session(session.session_id).turn_count == 1


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_roundtrip_state(self):
        mgr = ConversationManager(timeout_ticks=7)
        session = mgr.start_session("player", ["npc_a"], tick=1)
        mgr.process_turn(session.session_id, "player", "hi", tick=2)

        state = mgr.get_state()
        mgr2 = ConversationManager()
        mgr2.load_state(state)

        assert mgr2._timeout_ticks == 7
        restored = mgr2.get_session(session.session_id)
        assert restored is not None
        assert restored.status == "active"
        assert restored.turn_count == 1
        assert mgr2.get_active_session("player") is not None
