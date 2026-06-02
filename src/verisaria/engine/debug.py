"""Debug Service: explainability and introspection for the world runtime.

All significant state transitions and LLM calls should be traceable
through this service. It does not modify state — only reads and formats.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verisaria.engine.schemas import (
    Action,
    ArbiterOutput,
    Event,
    ParsedIntent,
)
from verisaria.engine.world import WorldCore


# ---------------------------------------------------------------------------
# Trace records
# ---------------------------------------------------------------------------

@dataclass
class IntentTrace:
    request_id: str
    raw_input: str
    parsed_intent: ParsedIntent | None
    model_used: str
    latency_ms: float = 0.0
    error: str | None = None


@dataclass
class ActionTrace:
    action_id: str
    source_intent_id: str | None
    action_type: str
    actor_id: str
    params: dict[str, Any]
    tick: int


@dataclass
class EventTrace:
    event_id: str
    event_type: str
    actor_id: str
    summary: str
    tick: int
    canonical_facts: dict[str, Any]


@dataclass
class ArbiterTrace:
    call_id: str
    source_action_id: str
    outcome: str
    evidence_refs: list[dict[str, Any]]
    state_changes_proposed: list[dict[str, Any]]
    confidence: float
    narration_hint: str | None


@dataclass
class TickTrace:
    tick: int
    intent_traces: list[IntentTrace] = field(default_factory=list)
    action_traces: list[ActionTrace] = field(default_factory=list)
    arbiter_traces: list[ArbiterTrace] = field(default_factory=list)
    event_traces: list[EventTrace] = field(default_factory=list)
    llm_calls: int = 0
    llm_budget_remaining: int = 0


# ---------------------------------------------------------------------------
# Debug Service
# ---------------------------------------------------------------------------

class DebugService:
    """Centralized tracing and explainability service.

    Usage:
        debug = DebugService(world)
        debug.trace_intent(...)
        debug.trace_action(...)
        debug.trace_arbiter(...)
        debug.trace_event(...)

        # Query
        debug.show_events(since_tick=1)
        debug.show_tick_trace(tick=5)
        debug.explain_event("evt_5_1")
    """

    def __init__(self, world: WorldCore) -> None:
        self.world = world
        self._tick_traces: dict[int, TickTrace] = {}
        self._intent_by_request: dict[str, IntentTrace] = {}
        self._arbiter_by_call: dict[str, ArbiterTrace] = {}

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def trace_intent(
        self,
        request_id: str,
        raw_input: str,
        parsed_intent: ParsedIntent | None,
        model_used: str = "",
        latency_ms: float = 0.0,
        error: str | None = None,
    ) -> None:
        trace = IntentTrace(
            request_id=request_id,
            raw_input=raw_input,
            parsed_intent=parsed_intent,
            model_used=model_used,
            latency_ms=latency_ms,
            error=error,
        )
        self._intent_by_request[request_id] = trace
        tick = self.world.state.tick
        self._ensure_tick(tick).intent_traces.append(trace)

    def trace_action(self, action: Action) -> None:
        trace = ActionTrace(
            action_id=action.action_id,
            source_intent_id=action.source_intent_id,
            action_type=action.action_type.value,
            actor_id=action.actor_id,
            params=dict(action.params),
            tick=action.tick,
        )
        tick = action.tick
        self._ensure_tick(tick).action_traces.append(trace)

    def trace_arbiter(self, arbiter_output: ArbiterOutput) -> None:
        trace = ArbiterTrace(
            call_id=arbiter_output.arbiter_id,
            source_action_id=arbiter_output.source_action_id,
            outcome=arbiter_output.outcome,
            evidence_refs=[
                {"path": ref.path, "value": ref.value, "source": ref.source}
                for ref in arbiter_output.evidence_refs
            ],
            state_changes_proposed=[
                {"field": sc.field, "delta": sc.delta, "reason": sc.reason}
                for sc in arbiter_output.state_changes_proposed
            ],
            confidence=arbiter_output.confidence,
            narration_hint=arbiter_output.narration_hint,
        )
        self._arbiter_by_call[arbiter_output.arbiter_id] = trace
        tick = self.world.state.tick
        self._ensure_tick(tick).arbiter_traces.append(trace)

    def trace_event(self, event: Event) -> None:
        trace = EventTrace(
            event_id=event.event_id,
            event_type=event.event_type.value,
            actor_id=event.actor_id,
            summary=event.summary,
            tick=event.tick,
            canonical_facts=dict(event.canonical_facts),
        )
        tick = event.tick
        self._ensure_tick(tick).event_traces.append(trace)

    def record_llm_budget(self, tick: int, calls_used: int, budget_remaining: int) -> None:
        tt = self._ensure_tick(tick)
        tt.llm_calls = calls_used
        tt.llm_budget_remaining = budget_remaining

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def show_events(self, since_tick: int = 0) -> list[dict[str, Any]]:
        """Return formatted event summaries since a given tick."""
        events = self.world.event_log.get_events(since_tick=since_tick)
        return [
            {
                "event_id": e.event_id,
                "tick": e.tick,
                "type": e.event_type.value,
                "actor": e.actor_id,
                "summary": e.summary,
            }
            for e in events
        ]

    def explain_event(self, event_id: str) -> dict[str, Any] | None:
        """Full explainability for a single event."""
        event = self.world.event_log.get_event(event_id)
        if event is None:
            return None

        # Find related traces
        tick = event.tick
        tt = self._tick_traces.get(tick)
        related_actions = []
        if tt:
            related_actions = [
                {"action_id": a.action_id, "params": a.params}
                for a in tt.action_traces
                if a.tick == tick
            ]

        return {
            "event": {
                "event_id": event.event_id,
                "type": event.event_type.value,
                "summary": event.summary,
                "canonical_facts": dict(event.canonical_facts),
            },
            "related_actions": related_actions,
            "tick": tick,
        }

    def show_tick_trace(self, tick: int) -> dict[str, Any] | None:
        """Complete trace of everything that happened in a tick."""
        tt = self._tick_traces.get(tick)
        if tt is None:
            return None

        return {
            "tick": tick,
            "intents": [
                {
                    "request_id": i.request_id,
                    "raw_input": i.raw_input,
                    "intent_type": i.parsed_intent.intent_type.value if i.parsed_intent else None,
                    "model": i.model_used,
                    "error": i.error,
                }
                for i in tt.intent_traces
            ],
            "actions": [
                {"action_id": a.action_id, "type": a.action_type, "actor": a.actor_id}
                for a in tt.action_traces
            ],
            "events": [
                {"event_id": e.event_id, "type": e.event_type, "summary": e.summary}
                for e in tt.event_traces
            ],
            "llm_calls": tt.llm_calls,
            "llm_budget_remaining": tt.llm_budget_remaining,
        }

    def show_arbiter_decision(self, call_id: str) -> dict[str, Any] | None:
        """Show Arbiter evidence chain for debugging."""
        trace = self._arbiter_by_call.get(call_id)
        if trace is None:
            return None
        return {
            "call_id": trace.call_id,
            "outcome": trace.outcome,
            "confidence": trace.confidence,
            "evidence_refs": trace.evidence_refs,
            "state_changes_proposed": trace.state_changes_proposed,
            "narration_hint": trace.narration_hint,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ensure_tick(self, tick: int) -> TickTrace:
        if tick not in self._tick_traces:
            self._tick_traces[tick] = TickTrace(tick=tick)
        return self._tick_traces[tick]
