"""Tests for LLM Arbiter."""

import pytest

from verisaria.engine.arbiter import LLMArbiter
from verisaria.engine.llm import FakeLLMProvider, LLMOrchestrator
from verisaria.engine.schemas import Action, ActionType
from verisaria.engine.world import EntityState, LocationState, WorldCore, WorldState, ZoneState


@pytest.fixture
def arbiter_with_fixture() -> LLMArbiter:
    provider = FakeLLMProvider("fixtures")
    # Load and register the arbiter fixture programmatically
    import json
    fixture_path = "fixtures/frontier_town/arbiter_decide/steal_short_sword.json"
    with open(fixture_path) as f:
        fixture = json.load(f)
    provider.register_fixture(
        task_type="arbiter_decide",
        prompt="steal",  # fuzzy match: 'steal' appears in arbiter prompt
        expected_output=fixture["expected_output"],
        raw_response=fixture["llm_response"]["raw"],
    )
    llm = LLMOrchestrator(primary_provider=provider)
    return LLMArbiter(llm_orchestrator=llm)


@pytest.fixture
def arbiter_no_fixture() -> LLMArbiter:
    provider = FakeLLMProvider("fixtures")
    llm = LLMOrchestrator(primary_provider=provider)
    return LLMArbiter(llm_orchestrator=llm)


@pytest.fixture
def steal_world() -> WorldCore:
    state = WorldState(
        tick=1,
        entities={
            "player_001": EntityState(
                entity_id="player_001",
                entity_type="player",
                location_id="blacksmith",
                zone_id="forge_area",
                attributes={"dexterity": 0.70, "perception": 0.60},
            ),
            "npc.guard_b": EntityState(
                entity_id="npc.guard_b",
                entity_type="npc",
                location_id="town_square",
                zone_id="center",
                attributes={"perception": 0.75, "alertness": 0.3},
                traits=["anxious", "greedy", "loyal_to_duty"],
            ),
        },
        locations={
            "blacksmith": LocationState(
                location_id="blacksmith",
                zones={
                    "forge_area": ZoneState(
                        zone_id="forge_area",
                        location_id="blacksmith",
                        visibility="high",
                        noise_level="loud",
                    ),
                },
            ),
            "town_square": LocationState(
                location_id="town_square",
                zones={
                    "center": ZoneState(
                        zone_id="center",
                        location_id="town_square",
                        visibility="high",
                        noise_level="loud",
                    ),
                },
            ),
        },
    )
    return WorldCore(initial_state=state)


class TestLLMArbiter:
    def test_arbitrate_with_fixture(
        self, arbiter_with_fixture: LLMArbiter, steal_world: WorldCore
    ):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.PHYSICAL,
            params={"verb": "steal", "target": "short_sword"},
            tick=1,
        )
        result = arbiter_with_fixture.arbitrate(action, steal_world)

        # Should get a valid outcome from the fixture
        assert result.accepted
        assert result.arbiter_output.outcome == "partial_success"
        assert result.arbiter_output.confidence == 0.72
        assert len(result.arbiter_output.evidence_refs) == 3
        assert len(result.arbiter_output.state_changes_proposed) == 2
        assert result.arbiter_output.narration_hint is not None

    def test_arbitrate_fallback_on_missing_fixture(
        self, arbiter_no_fixture: LLMArbiter, steal_world: WorldCore
    ):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.SOCIAL,
            params={"verb": "persuade", "target": "npc.guard_b"},
            tick=1,
        )
        result = arbiter_no_fixture.arbitrate(action, steal_world)

        # No fixture for this action → fallback
        assert result.accepted  # fallback is always accepted
        assert result.arbiter_output.outcome == "partial_success"  # social default
        assert "LLM 不可用" in result.arbiter_output.reason
        assert len(result.arbiter_output.state_changes_proposed) == 0


def test_fallback_reason_reflects_validation_cause():
    """A schema/JSON rejection of ArbiterOutput must NOT be logged as 'LLM 不可用'
    (the API may be healthy); the fallback reason should name the real cause."""
    from verisaria.engine.arbiter import LLMArbiter
    from verisaria.engine.llm import LLMOrchestrator, FakeLLMProvider, LLMCallResult, LLMErrorCategory
    from verisaria.engine.schemas import Action, ActionType

    arb = LLMArbiter(llm_orchestrator=LLMOrchestrator(primary_provider=FakeLLMProvider()))
    action = Action(action_id="a", actor_id="player_001", action_type=ActionType.SOCIAL,
                    tick=1, params={"verb": "persuade"})
    bad = LLMCallResult(success=False, error="Schema validation failed",
                        error_category=LLMErrorCategory.VALIDATION)
    out = arb._fallback_outcome("arb_x", action, bad)
    assert out.arbiter_output.is_fallback is True
    assert "schema" in out.arbiter_output.reason and "LLM 不可用" not in out.arbiter_output.reason

    # a genuine connection failure DOES say LLM unavailable
    down = LLMCallResult(success=False, error="conn", error_category=LLMErrorCategory.CONNECTION)
    out2 = arb._fallback_outcome("arb_y", action, down)
    assert "LLM 不可用" in out2.arbiter_output.reason
