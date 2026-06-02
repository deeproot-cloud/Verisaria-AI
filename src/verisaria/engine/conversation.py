"""Conversation Manager: session lifecycle, topic tracking, pronoun resolution.

Phase-9 minimal version: rule-driven, no LLM.
"""

from __future__ import annotations

import re
from typing import Any

from verisaria.engine.schemas import ConversationSession


# Simple stop-words for naive topic extraction
_STOP_WORDS = {
    "的", "了", "在", "是", "我", "你", "他", "她", "它", "我们", "你们", "他们",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could", "should",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as", "into",
    "through", "during", "before", "after", "above", "below", "between",
    "and", "but", "or", "yet", "so", "if", "because", "although", "though",
    "this", "that", "these", "those", "i", "me", "my", "mine", "you", "your",
    "yours", "he", "him", "his", "she", "her", "hers", "it", "its", "they",
    "them", "their", "theirs", "we", "us", "our", "ours",
}

_PRONOUN_PATTERNS = {
    "他": "{target}",
    "她": "{target}",
    "它": "{target}",
    "he": "{target}",
    "she": "{target}",
    "it": "{target}",
    "him": "{target}",
    "her": "{target}",
    "they": "{target}",
    "them": "{target}",
    "this": "{topic}",
    "that": "{topic}",
    "这个": "{topic}",
    "那个": "{topic}",
}


class ConversationManager:
    """Manage conversation sessions between entities.

    Rule-driven (no LLM). Handles:
    - Session lifecycle: start, turn, interrupt, resume, conclude, abandon
    - Topic tracking via naive keyword extraction
    - Simple pronoun/reference resolution
    - Timeout detection
    """

    def __init__(self, timeout_ticks: int = 10) -> None:
        self._sessions: dict[str, ConversationSession] = {}
        self._entity_sessions: dict[str, str] = {}  # entity_id -> active session_id
        self._timeout_ticks = timeout_ticks
        self._seq = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_session(
        self,
        initiator_id: str,
        participants: list[str],
        tick: int,
    ) -> ConversationSession:
        """Create a new conversation session."""
        self._seq += 1
        session_id = f"conv_{tick}_{self._seq}"
        # Deduplicate and ensure initiator is included
        unique_parts = list(dict.fromkeys([initiator_id] + participants))

        session = ConversationSession(
            session_id=session_id,
            participants=unique_parts,
            started_at_tick=tick,
            last_activity_tick=tick,
        )
        self._sessions[session_id] = session
        for pid in unique_parts:
            self._entity_sessions[pid] = session_id
        return session

    def process_turn(
        self,
        session_id: str,
        speaker_id: str,
        content: str,
        tick: int,
    ) -> ConversationSession | None:
        """Record a turn in an active session and update topics."""
        session = self._sessions.get(session_id)
        if session is None:
            return None

        if session.status not in ("active", "resumed"):
            return None

        # Update session
        session.turn_count += 1
        session.last_activity_tick = tick

        # Naive topic extraction
        topics = self._extract_topics(content)
        for t in topics:
            if t not in session.topic_stack:
                session.topic_stack.append(t)

        # Shared context: record last speaker and content snippet
        session.shared_context["last_speaker"] = speaker_id
        session.shared_context["last_content"] = content[:200]

        return session

    def interrupt(
        self,
        session_id: str,
        reason: str,
        tick: int,
    ) -> ConversationSession | None:
        """Interrupt an active session."""
        session = self._sessions.get(session_id)
        if session is None or session.status not in ("active", "resumed"):
            return None

        session.status = "interrupted"
        session.pending_interruptions.append(reason)
        session.last_activity_tick = tick

        # Remove from entity active mapping
        for pid in session.participants:
            if self._entity_sessions.get(pid) == session_id:
                del self._entity_sessions[pid]

        return session

    def resume(
        self,
        session_id: str,
        tick: int,
    ) -> ConversationSession | None:
        """Resume an interrupted session."""
        session = self._sessions.get(session_id)
        if session is None or session.status != "interrupted":
            return None

        session.status = "active"
        session.last_activity_tick = tick
        for pid in session.participants:
            self._entity_sessions[pid] = session_id
        return session

    def conclude(
        self,
        session_id: str,
        tick: int,
    ) -> ConversationSession | None:
        """Conclude a session (natural end)."""
        session = self._sessions.get(session_id)
        if session is None or session.status in ("concluded", "abandoned"):
            return None

        session.status = "concluded"
        session.last_activity_tick = tick

        for pid in session.participants:
            if self._entity_sessions.get(pid) == session_id:
                del self._entity_sessions[pid]

        return session

    def abandon(
        self,
        session_id: str,
        tick: int,
    ) -> ConversationSession | None:
        """Abandon a session (timeout or forced)."""
        session = self._sessions.get(session_id)
        if session is None or session.status in ("concluded", "abandoned"):
            return None

        session.status = "abandoned"
        session.last_activity_tick = tick

        for pid in session.participants:
            if self._entity_sessions.get(pid) == session_id:
                del self._entity_sessions[pid]

        return session

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_active_session(self, entity_id: str) -> ConversationSession | None:
        """Return the active/resumed session an entity is currently in."""
        session_id = self._entity_sessions.get(entity_id)
        if session_id is None:
            return None
        session = self._sessions.get(session_id)
        if session is None or session.status not in ("active", "resumed"):
            return None
        return session

    def get_session(self, session_id: str) -> ConversationSession | None:
        return self._sessions.get(session_id)

    def check_timeouts(self, current_tick: int) -> list[ConversationSession]:
        """Find and abandon sessions that have exceeded the timeout."""
        abandoned: list[ConversationSession] = []
        for session in list(self._sessions.values()):
            if session.status not in ("active", "resumed"):
                continue
            if current_tick - session.last_activity_tick > self._timeout_ticks:
                self.abandon(session.session_id, current_tick)
                abandoned.append(session)
        return abandoned

    # ------------------------------------------------------------------
    # Reference resolution
    # ------------------------------------------------------------------

    def resolve_reference(self, session_id: str, raw_text: str) -> str:
        """Replace pronouns in raw_text with likely referents.

        Returns the resolved text; if ambiguous, returns original.
        """
        session = self._sessions.get(session_id)
        if session is None:
            return raw_text

        target = self._infer_target(session)
        topic = self._infer_topic(session)

        result = raw_text
        for pronoun, template in _PRONOUN_PATTERNS.items():
            # Use word boundaries for English, simple replacement for Chinese
            if pronoun.isascii():
                pattern = r"\b" + re.escape(pronoun) + r"\b"
                replacement = template.format(target=target, topic=topic)
                result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
            else:
                replacement = template.format(target=target, topic=topic)
                result = result.replace(pronoun, replacement)

        return result

    def _infer_target(self, session: ConversationSession) -> str:
        """Infer the most likely entity referent for pronouns."""
        last_speaker = session.shared_context.get("last_speaker")
        candidates = [p for p in session.participants if p != last_speaker]
        return candidates[0] if candidates else session.participants[0]

    def _infer_topic(self, session: ConversationSession) -> str:
        """Infer the most likely topic referent for demonstratives."""
        if session.topic_stack:
            return session.topic_stack[-1]
        return session.shared_context.get("last_content", "")

    # ------------------------------------------------------------------
    # Topic extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_topics(content: str) -> list[str]:
        """Naive keyword extraction: English words + Chinese phrases, filter stop words."""
        english = re.findall(r"[a-z]{2,}", content.lower())
        chinese = re.findall(r"[\u4e00-\u9fff]{2,}", content)
        words = english + chinese
        topics = [w for w in words if w.lower() not in _STOP_WORDS]
        return topics[:5]  # Limit to 5 topics per turn

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        """Return serializable state for persistence."""
        return {
            "sessions": {sid: s.model_dump() for sid, s in self._sessions.items()},
            "entity_sessions": dict(self._entity_sessions),
            "timeout_ticks": self._timeout_ticks,
            "seq": self._seq,
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore from serialized state."""
        self._sessions = {
            sid: ConversationSession(**data)
            for sid, data in state.get("sessions", {}).items()
        }
        self._entity_sessions = dict(state.get("entity_sessions", {}))
        self._timeout_ticks = state.get("timeout_ticks", 10)
        self._seq = state.get("seq", 0)
