"""End-to-end pipeline test: Player Input → Intent → Action → Event.

This is the Phase 1 baseline replay test. It verifies that a deterministic
fixture-driven input produces a predictable Event in the Event Log.
"""

import pytest

from verisaria.engine.interaction import ActionComposer
from verisaria.engine.llm import FakeLLMProvider, LLMCallRequest, LLMOrchestrator
from verisaria.engine.rules import RulesEngine
from verisaria.engine.schemas import ActionType, EventType, ParsedIntent
from verisaria.engine.world import EntityState, LocationState, WorldCore, WorldState, ZoneState


@pytest.fixture
def pipeline():
    """Build the minimal pipeline fixture."""
    # World setup
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
                zones={
                    "center": ZoneState(
                        zone_id="center",
                        location_id="town_square",
                        occupant_ids=["player_001"],
                    ),
                },
            ),
        },
    )
    world = WorldCore(initial_state=state)
    rules = RulesEngine()
    llm = LLMOrchestrator(primary_provider=FakeLLMProvider("fixtures"))
    composer = ActionComposer()
    return {"world": world, "rules": rules, "llm": llm, "composer": composer}


class TestMinimalPipeline:
    def test_look_around_produces_event(self, pipeline):
        """Replay: player says '看看周围' → look Event in Event Log."""
        world = pipeline["world"]
        rules = pipeline["rules"]
        llm = pipeline["llm"]
        composer = pipeline["composer"]

        # ① Parse intent via Fake LLM
        result = llm.call(
            LLMCallRequest(
                task_type="parse_player_intent",
                prompt="看看周围",
                schema_model=ParsedIntent,
            )
        )
        assert result.success
        intent = ParsedIntent.model_validate(result.data)
        assert intent.intent_type == ActionType.LOOK

        # ② Compose Action
        action = composer.compose(intent, tick=world.state.tick, seq=1)
        assert action is not None
        assert action.action_type == ActionType.PHYSICAL
        assert action.params["verb"] == "look"

        # ③ Rules Engine resolves
        resolution = rules.resolve(action, world.state)
        assert resolution.can_execute
        assert resolution.event_type == EventType.PHYSICAL
        assert not resolution.requires_arbiter

        # ④ Commit to world
        event = world.commit_action(action)
        assert event.event_type == EventType.PHYSICAL
        assert event.actor_id == "player_001"
        assert len(world.event_log) == 1

        # ⑤ Verify replayability: same input → same event structure
        snap = world.snapshot()
        assert snap["tick"] == 1
        assert snap["event_count"] == 1
        assert snap["entity_count"] == 1

    def test_speech_produces_event(self, pipeline):
        """Replay: player says '快离开这儿' → speech Event."""
        # Register a speech fixture
        provider = FakeLLMProvider("fixtures")
        provider.register_fixture(
            task_type="parse_player_intent",
            prompt="快离开这儿",
            expected_output={
                "intent_id": "intent_speech_001",
                "source": "natural_language",
                "raw_text": "快离开这儿",
                "intent_type": "speech",
                "actor_id": "player_001",
                "commitment": "committed",
                "confidence": 0.95,
                "performed_content": "快离开这儿",
                "timestamp": 1,
            },
        )
        llm = LLMOrchestrator(primary_provider=provider)
        world = pipeline["world"]
        rules = pipeline["rules"]
        composer = pipeline["composer"]

        result = llm.call(
            LLMCallRequest(
                task_type="parse_player_intent",
                prompt="快离开这儿",
                schema_model=ParsedIntent,
            )
        )
        assert result.success
        intent = ParsedIntent.model_validate(result.data)
        assert intent.intent_type == ActionType.SPEECH

        action = composer.compose(intent, tick=world.state.tick, seq=1)
        resolution = rules.resolve(action, world.state)
        assert resolution.can_execute
        assert resolution.event_type == EventType.SPEECH
        assert "快离开这儿" not in resolution.summary  # neutral summary; content in facts

        event = world.commit_action(action)
        assert event.event_type == EventType.SPEECH
        assert event.canonical_facts["content"] == "快离开这儿"
