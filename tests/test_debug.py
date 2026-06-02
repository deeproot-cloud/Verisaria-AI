"""Tests for Debug Service explainability."""

import pytest

from verisaria.engine.debug import DebugService
from verisaria.engine.schemas import (
    Action,
    ActionType,
    ArbiterOutput,
    CommitmentLevel,
    EventType,
    EvidenceRef,
    ParsedIntent,
)
from verisaria.engine.world import EntityState, LocationState, WorldCore, WorldState, ZoneState


@pytest.fixture
def debug_world():
    state = WorldState(
        tick=1,
        entities={
            "player_001": EntityState(
                entity_id="player_001",
                entity_type="player",
                location_id="town_square",
                zone_id="center",
            ),
        },
        locations={
            "town_square": LocationState(
                location_id="town_square",
                zones={"center": ZoneState(zone_id="center", location_id="town_square")},
            ),
        },
    )
    world = WorldCore(initial_state=state)
    return world


@pytest.fixture
def debug(debug_world):
    return DebugService(debug_world)


class TestDebugTracing:
    def test_trace_intent(self, debug: DebugService, debug_world: WorldCore):
        intent = ParsedIntent(
            intent_id="intent_001",
            source="natural_language",
            raw_text="看看周围",
            intent_type=ActionType.LOOK,
            actor_id="player_001",
            commitment=CommitmentLevel.COMMITTED,
            confidence=0.98,
            timestamp=1,
        )
        debug.trace_intent(
            request_id="req_001",
            raw_input="看看周围",
            parsed_intent=intent,
            model_used="fake",
        )

        trace = debug.show_tick_trace(tick=1)
        assert trace is not None
        assert len(trace["intents"]) == 1
        assert trace["intents"][0]["raw_input"] == "看看周围"
        assert trace["intents"][0]["model"] == "fake"

    def test_trace_action_and_event(self, debug: DebugService, debug_world: WorldCore):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.SPEECH,
            params={"content": "test"},
            tick=1,
        )
        debug.trace_action(action)
        event = debug_world.commit_action(action)
        debug.trace_event(event)

        trace = debug.show_tick_trace(tick=1)
        assert trace is not None
        assert len(trace["actions"]) == 1
        assert len(trace["events"]) == 1
        assert trace["events"][0]["event_id"] == event.event_id

    def test_explain_event(self, debug: DebugService, debug_world: WorldCore):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.SPEECH,
            params={"content": "快离开"},
            tick=1,
        )
        debug.trace_action(action)
        event = debug_world.commit_action(action)
        debug.trace_event(event)

        explanation = debug.explain_event(event.event_id)
        assert explanation is not None
        assert explanation["event"]["summary"] == event.summary
        assert len(explanation["related_actions"]) == 1
        assert explanation["related_actions"][0]["action_id"] == "act_1_1"

    def test_show_arbiter_decision(self, debug: DebugService):
        arbiter = ArbiterOutput(
            arbiter_id="arb_001",
            source_action_id="act_001",
            outcome="partial_success",
            reason="卫兵紧张",
            evidence_refs=[
                EvidenceRef(
                    path="npc.guard_b.traits.anxious", value=True, source="trait"
                )
            ],
            state_changes_proposed=[],
            confidence=0.78,
            narration_hint="卫兵警惕地看了你一眼",
        )
        debug.trace_arbiter(arbiter)

        decision = debug.show_arbiter_decision("arb_001")
        assert decision is not None
        assert decision["outcome"] == "partial_success"
        assert len(decision["evidence_refs"]) == 1
        assert decision["evidence_refs"][0]["path"] == "npc.guard_b.traits.anxious"
        assert decision["narration_hint"] == "卫兵警惕地看了你一眼"

    def test_llm_budget_tracking(self, debug: DebugService):
        debug.record_llm_budget(tick=1, calls_used=3, budget_remaining=7)
        trace = debug.show_tick_trace(tick=1)
        assert trace is not None
        assert trace["llm_calls"] == 3
        assert trace["llm_budget_remaining"] == 7

    def test_show_events_filtered(self, debug: DebugService, debug_world: WorldCore):
        for i in range(3):
            action = Action(
                action_id=f"act_{i}_1",
                actor_id="player_001",
                action_type=ActionType.SPEECH,
                params={"content": f"msg_{i}"},
                tick=i,
            )
            debug_world.state.tick = i
            event = debug_world.commit_action(action)
            debug.trace_event(event)

        events = debug.show_events(since_tick=1)
        assert len(events) == 2  # tick 1 and 2
        assert events[0]["tick"] == 1
        assert events[1]["tick"] == 2
