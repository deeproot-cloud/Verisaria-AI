"""Player Agenda Service: signal aggregation, inference, Reflection Scene triggers.

Phase-6 minimal version: rule-based signal aggregation, no LLM.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from verisaria.engine.schemas import Drive, PlayerAgenda, SuggestionMode


# ---------------------------------------------------------------------------
# Intent Signal
# ---------------------------------------------------------------------------

@dataclass
class IntentSignal:
    note: str
    tick: int
    source_id: str | None = None
    topic_tags: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reflection Proposal
# ---------------------------------------------------------------------------

@dataclass
class AgendaProposal:
    proposal_id: str
    action: Literal["add", "reconsider", "remove"]
    claim: str
    confidence: float
    evidence: list[str] = field(default_factory=list)
    drive_data: dict[str, Any] | None = None


@dataclass
class ReflectionScene:
    reflection_id: str
    trigger: str
    proposals: list[AgendaProposal] = field(default_factory=list)
    narration_hint: str = ""


# ---------------------------------------------------------------------------
# Agenda Service
# ---------------------------------------------------------------------------

class AgendaService:
    """Manage player intent signals, aggregate inferences, trigger Reflection."""

    AGGREGATION_THRESHOLD = 0.7
    MIN_SIGNAL_COUNT = 5
    RECENT_WINDOW = 5
    MID_WINDOW = 10
    DRIFT_THRESHOLD_TICKS = 20

    def __init__(
        self,
        player_id: str,
        inference_mode: Literal["off", "reflection_only", "normal"] = "reflection_only",
        stance_topics: dict[str, list[str]] | None = None,
        stance_labels: dict[str, str] | None = None,
    ) -> None:
        self.player_id = player_id
        self.inference_mode = inference_mode
        # Pack-declared display labels per stance topic (e.g. help_refugees →
        # "接纳难民"), so a confirmed goal reads as a goal, not a verbatim quote.
        self._stance_labels = stance_labels or {}
        # Pack-declared stance topics (topic_id → keywords). When a signal's note
        # contains a topic's keyword, the signal is tagged with that topic_id, so
        # differently-phrased intents about the same thing cluster reliably
        # (replacing fragile bigram clustering) — and confirmed stances expose a
        # stable key the world can read (B2-A1).
        self._stance_topics = stance_topics or {}
        self._confirmed_topics: set[str] = set()
        self._signals: list[IntentSignal] = []
        self._proposals: list[AgendaProposal] = []
        self._confirmed_drives: list[Drive] = []
        self._rejected_paths: list[str] = []
        self._declared_to_world: list[str] = []
        self._private_intent: list[str] = []
        self._open_questions: list[str] = []
        self._long_term_aspirations: list[str] = []
        self._seq = 0
        self._last_reflection_tick: int = 0

    # -- Signal intake --

    def add_signal(
        self,
        note: str,
        tick: int,
        source_id: str | None = None,
        topic_tags: list[str] | None = None,
    ) -> None:
        """Add a player_intent_note to the signal pool."""
        self._signals.append(
            IntentSignal(
                note=note,
                tick=tick,
                source_id=source_id,
                topic_tags=topic_tags or self._tags_for_note(note),
            )
        )

    def _tags_for_note(self, note: str) -> list[str]:
        """Tag a note with matching pack stance topics (stable clustering); fall
        back to keyword bigrams when no declared topic matches."""
        matched = [
            topic_id
            for topic_id, keywords in self._stance_topics.items()
            if any(kw in note for kw in keywords)
        ]
        return matched or self._extract_keywords(note)

    # -- Aggregation --

    def aggregate_signals(self, current_tick: int) -> list[AgendaProposal]:
        """Cluster signals by topic and generate candidate inferences.

        Returns proposals that exceed the confidence threshold.
        """
        if self.inference_mode == "off":
            return []

        # Group signals by primary topic tag
        clusters: dict[str, list[IntentSignal]] = {}
        for sig in self._signals:
            primary = sig.topic_tags[0] if sig.topic_tags else "general"
            clusters.setdefault(primary, []).append(sig)

        proposals: list[AgendaProposal] = []
        for topic, signals in clusters.items():
            if len(signals) < self.MIN_SIGNAL_COUNT:
                continue
            # One goal per topic: once a topic is confirmed, stop re-proposing it
            # (dedup by topic, not by the verbatim utterance which differs each
            # tick and otherwise spawns a new "goal" every turn).
            if topic in self._confirmed_topics:
                continue

            confidence = self._compute_cluster_confidence(signals, current_tick)
            if confidence >= self.AGGREGATION_THRESHOLD:
                self._seq += 1
                # Prefer the pack's stance label ("接纳难民") over a raw quote.
                claim = self._stance_labels.get(topic) or self._synthesize_claim(topic, signals)
                # Skip if already confirmed or rejected
                if self._is_duplicate_or_rejected(claim):
                    continue
                proposals.append(
                    AgendaProposal(
                        proposal_id=f"prop_{self._seq}",
                        action="add",
                        claim=claim,
                        confidence=round(confidence, 2),
                        evidence=[s.source_id for s in signals if s.source_id][:5],
                        drive_data={
                            "id": f"drive_{topic}_{self._seq}",
                            "label": claim,
                            "strength": round(confidence, 2),
                            "source": "system_inferred",
                            "topic": topic,
                        },
                    )
                )

        self._proposals.extend(proposals)
        return proposals

    def _compute_cluster_confidence(
        self, signals: list[IntentSignal], current_tick: int
    ) -> float:
        """Compute time-decayed confidence for a signal cluster."""
        total_weight = 0.0
        for sig in signals:
            age = current_tick - sig.tick
            if age <= self.RECENT_WINDOW:
                weight = 1.0
            elif age <= self.MID_WINDOW:
                weight = 0.6
            else:
                weight = 0.3
            total_weight += weight
        # Normalise: more signals → higher confidence, capped at 1.0
        return min(1.0, total_weight / self.MIN_SIGNAL_COUNT)

    def _synthesize_claim(self, topic: str, signals: list[IntentSignal]) -> str:
        """Generate a human-readable claim from signal cluster."""
        # Simple heuristic: use the most recent signal's note
        latest = max(signals, key=lambda s: s.tick)
        return latest.note

    def _is_duplicate_or_rejected(self, claim: str) -> bool:
        """Check if claim is already confirmed or rejected."""
        for drive in self._confirmed_drives:
            if drive.label == claim:
                return True
        if claim in self._rejected_paths:
            return True
        return False

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """Extract simple keywords for clustering."""
        keywords: set[str] = set()
        cleaned = (
            text.replace("，", " ")
            .replace("。", " ")
            .replace("；", " ")
            .replace("、", " ")
            .replace("！", " ")
            .replace("？", " ")
        )
        for word in cleaned.split():
            w = word.strip().lower()
            if len(w) >= 2:
                keywords.add(w)
        # Bigrams for Chinese
        for i in range(len(text) - 1):
            bigram = text[i : i + 2]
            if any(c in " ，。；、！？ \t\n" for c in bigram):
                continue
            keywords.add(bigram)
        return list(keywords)[:5] or ["general"]

    def aggregate_and_autoconfirm(
        self, current_tick: int, threshold: float | None = None
    ) -> list[Drive]:
        """Aggregate signals and immediately confirm strong stances into Drives.

        This is the per-tick entry the CLI uses. It breaks the deadlock where
        aggregation only ran inside a reflection scene (which itself only fired
        on proposals that aggregation produces): repeating an intent enough times
        (``MIN_SIGNAL_COUNT``) now turns it into a confirmed goal the player can
        see in ``/agenda`` and the world can read. Deterministic — no LLM.

        Returns the drives newly confirmed on this call (empty if none crossed
        the bar), so the caller can surface them to the player.
        """
        threshold = self.AGGREGATION_THRESHOLD if threshold is None else threshold
        proposals = self.aggregate_signals(current_tick)
        confirmed: list[Drive] = []
        for p in proposals:
            if p.confidence >= threshold:
                drive = self.confirm_proposal(p.proposal_id, current_tick)
                if drive is not None:
                    confirmed.append(drive)
        return confirmed

    # -- Reflection triggers --

    def should_trigger_reflection(
        self,
        tick: int,
        triggers: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Check if a Reflection Scene should trigger.

        Returns (should_trigger, trigger_reason).
        """
        triggers = triggers or {}

        if triggers.get("rest"):
            return True, "rest"
        if triggers.get("major_event"):
            return True, "major_event"
        if triggers.get("new_location"):
            return True, "new_location"
        if triggers.get("drift_ticks", 0) >= self.DRIFT_THRESHOLD_TICKS:
            return True, "goal_drift"

        # Unconfirmed inferences pending
        unconfirmed = [p for p in self._proposals if p.confidence >= self.AGGREGATION_THRESHOLD]
        if unconfirmed and (tick - self._last_reflection_tick) >= 5:
            return True, "unconfirmed_inferences"

        return False, ""

    def generate_reflection_scene(
        self,
        current_tick: int,
        trigger: str,
    ) -> ReflectionScene | None:
        """Generate a Reflection Scene with Agenda Proposals."""
        if self.inference_mode == "off":
            return None

        # Aggregate fresh proposals
        proposals = self.aggregate_signals(current_tick)
        if not proposals and trigger != "major_event":
            return None

        self._last_reflection_tick = current_tick
        return ReflectionScene(
            reflection_id=f"ref_{current_tick}",
            trigger=trigger,
            proposals=proposals,
            narration_hint=self._narration_hint_for_trigger(trigger),
        )

    @staticmethod
    def _narration_hint_for_trigger(trigger: str) -> str:
        hints = {
            "rest": "你在休息时不由自主地回想起最近的经历...",
            "major_event": "刚刚发生的事让你心神不宁...",
            "new_location": "新环境让你有机会整理思绪...",
            "goal_drift": "你意识到自己已经很久没有明确的目标了...",
            "unconfirmed_inferences": "一些念头在脑海中反复出现...",
        }
        return hints.get(trigger, "")

    # -- Proposal lifecycle --

    def confirm_proposal(self, proposal_id: str, tick: int) -> Drive | None:
        """Confirm a proposal into a Drive."""
        for p in self._proposals:
            if p.proposal_id == proposal_id and p.drive_data:
                drive = Drive(
                    id=p.drive_data["id"],
                    label=p.drive_data["label"],
                    strength=p.drive_data.get("strength", 0.5),
                    source="system_inferred",
                    declared_at_tick=None,
                    confirmed=True,
                    confirmed_at_tick=tick,
                    evidence_refs=p.evidence,
                )
                self._confirmed_drives.append(drive)
                topic = p.drive_data.get("topic")
                if topic:
                    self._confirmed_topics.add(topic)  # all topics → dedup
                return drive
        return None

    def get_confirmed_stance_topics(self) -> set[str]:
        """Pack stance topics the player has confirmed (stable keys the world can
        read via the campaign-driver context) — excludes ad-hoc bigram topics."""
        return {t for t in self._confirmed_topics if t in self._stance_topics}

    def reject_proposal(self, proposal_id: str) -> bool:
        """Reject a proposal; its claim goes into rejected_paths."""
        for p in self._proposals:
            if p.proposal_id == proposal_id:
                self._rejected_paths.append(p.claim)
                return True
        return False

    def add_declared_drive(self, drive: Drive) -> None:
        """Add a player-declared drive directly."""
        self._confirmed_drives.append(drive)

    def add_declared_intent(self, text: str, is_private: bool = False) -> None:
        """Record player-declared intent."""
        if is_private:
            self._private_intent.append(text)
        else:
            self._declared_to_world.append(text)

    def add_open_question(self, question: str) -> None:
        self._open_questions.append(question)

    def add_long_term_aspiration(self, aspiration: str) -> None:
        self._long_term_aspirations.append(aspiration)

    # -- Output --

    def get_agenda(self, current_tick: int) -> PlayerAgenda:
        """Build the current PlayerAgenda snapshot."""
        return PlayerAgenda(
            player_id=self.player_id,
            tick=current_tick,
            current_drives=self._confirmed_drives,
            declared_to_world=self._declared_to_world,
            private_intent=self._private_intent,
            system_inferred=[
                {
                    "claim": p.claim,
                    "confidence": p.confidence,
                    "requires_confirmation": True,
                    "evidence_refs": p.evidence,
                }
                for p in self._proposals
                if p.confidence >= self.AGGREGATION_THRESHOLD
                # Don't echo an already-confirmed topic as a pending inference.
                and (p.drive_data or {}).get("topic") not in self._confirmed_topics
            ],
            open_questions=self._open_questions,
            rejected_paths=self._rejected_paths,
            long_term_aspirations=self._long_term_aspirations,
            suggestion_mode=SuggestionMode.SUBTLE,
            inference_mode=self.inference_mode,
        )

    def get_state(self) -> dict[str, Any]:
        """Return fully serializable service state."""
        return {
            "player_id": self.player_id,
            "inference_mode": self.inference_mode,
            "signals": [asdict(s) for s in self._signals],
            "proposals": [asdict(p) for p in self._proposals],
            "confirmed_drives": [d.model_dump() for d in self._confirmed_drives],
            "confirmed_topics": sorted(self._confirmed_topics),
            "rejected_paths": list(self._rejected_paths),
            "declared_to_world": list(self._declared_to_world),
            "private_intent": list(self._private_intent),
            "open_questions": list(self._open_questions),
            "long_term_aspirations": list(self._long_term_aspirations),
            "seq": self._seq,
            "last_reflection_tick": self._last_reflection_tick,
            # Backward-compat counts
            "signal_count": len(self._signals),
            "proposal_count": len(self._proposals),
            "confirmed_drive_count": len(self._confirmed_drives),
            "rejected_path_count": len(self._rejected_paths),
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore from serialized state."""
        self.player_id = state.get("player_id", self.player_id)
        self.inference_mode = state.get("inference_mode", self.inference_mode)
        self._signals = [IntentSignal(**s) for s in state.get("signals", [])]
        self._proposals = [AgendaProposal(**p) for p in state.get("proposals", [])]
        self._confirmed_drives = [Drive(**d) for d in state.get("confirmed_drives", [])]
        self._confirmed_topics = set(state.get("confirmed_topics", []))
        self._rejected_paths = list(state.get("rejected_paths", []))
        self._declared_to_world = list(state.get("declared_to_world", []))
        self._private_intent = list(state.get("private_intent", []))
        self._open_questions = list(state.get("open_questions", []))
        self._long_term_aspirations = list(state.get("long_term_aspirations", []))
        self._seq = state.get("seq", 0)
        self._last_reflection_tick = state.get("last_reflection_tick", 0)
