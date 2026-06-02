"""Tests for Rules Engine."""

import pytest

from verisaria.engine.rules import RulesEngine
from verisaria.engine.schemas import Action, ActionType, EventType
from verisaria.engine.world import EntityState, LocationState, WorldState, ZoneState


@pytest.fixture
def rules() -> RulesEngine:
    return RulesEngine()


@pytest.fixture
def world_state() -> WorldState:
    return WorldState(
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
                        capacity=10,
                        occupant_ids=["player_001"],
                    ),
                    "market_corner": ZoneState(
                        zone_id="market_corner",
                        location_id="town_square",
                        capacity=5,
                        occupant_ids=[],
                    ),
                    "full_zone": ZoneState(
                        zone_id="full_zone",
                        location_id="town_square",
                        capacity=1,
                        occupant_ids=["npc_001"],
                    ),
                },
            ),
        },
    )


class TestSpeechRules:
    def test_speech_direct(self, rules: RulesEngine, world_state: WorldState):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.SPEECH,
            params={"content": "快离开", "volume": "low"},
            tick=1,
        )
        result = rules.resolve(action, world_state)
        assert result.can_execute
        assert result.event_type == EventType.SPEECH
        assert "快离开" in result.summary
        assert result.canonical_facts["volume"] == "low"
        assert not result.requires_arbiter


class TestMovementRules:
    def test_movement_valid(self, rules: RulesEngine, world_state: WorldState):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.MOVEMENT,
            params={"to_location": "town_square", "to_zone": "market_corner"},
            tick=1,
        )
        result = rules.resolve(action, world_state)
        assert result.can_execute
        assert result.event_type == EventType.MOVEMENT
        assert result.state_changes["new_zone"] == "market_corner"

    def test_movement_actor_not_found(self, rules: RulesEngine, world_state: WorldState):
        action = Action(
            action_id="act_1_1",
            actor_id="ghost_player",  # not in world
            action_type=ActionType.MOVEMENT,
            params={"to_location": "town_square", "to_zone": "market_corner"},
            tick=1,
        )
        result = rules.resolve(action, world_state)
        assert not result.can_execute
        assert "actor not found" in result.reason

    def test_movement_zone_at_capacity(self, rules: RulesEngine, world_state: WorldState):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.MOVEMENT,
            params={"to_location": "town_square", "to_zone": "full_zone"},
            tick=1,
        )
        result = rules.resolve(action, world_state)
        assert not result.can_execute
        assert "at capacity" in result.reason


class TestPhysicalRules:
    def test_look_direct(self, rules: RulesEngine, world_state: WorldState):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.PHYSICAL,
            params={"verb": "look"},
            tick=1,
        )
        result = rules.resolve(action, world_state)
        assert result.can_execute
        assert result.event_type == EventType.PHYSICAL
        assert not result.requires_arbiter

    def test_steal_skill_check(self, rules: RulesEngine, world_state: WorldState):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.PHYSICAL,
            params={"verb": "steal", "target": "short_sword"},
            tick=1,
        )
        result = rules.resolve(action, world_state)
        assert result.can_execute
        assert not result.requires_arbiter
        assert result.state_changes["stamina_delta"] == -10  # partial (no target entity)


class TestSocialRules:
    def test_social_needs_arbiter(self, rules: RulesEngine, world_state: WorldState):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.SOCIAL,
            params={"verb": "persuade", "target": "npc.guard_b"},
            tick=1,
        )
        result = rules.resolve(action, world_state)
        assert result.can_execute
        assert result.event_type == EventType.SOCIAL
        assert result.requires_arbiter

    def test_greeting_does_not_need_arbiter(self, rules: RulesEngine, world_state: WorldState):
        # A friendly greeting is not a persuasion contest — it must NOT go to the
        # arbiter (whose meta-hint would otherwise leak to the player). (P0.4)
        for verb in ("greet", "chat"):
            action = Action(
                action_id="act_1_1",
                actor_id="player_001",
                action_type=ActionType.SOCIAL,
                params={"verb": verb, "target": "npc.guard_b"},
                tick=1,
            )
            result = rules.resolve(action, world_state)
            assert result.can_execute
            assert not result.requires_arbiter, f"{verb} should not require arbiter"

    def test_contest_social_verbs_still_need_arbiter(self, rules: RulesEngine, world_state: WorldState):
        for verb in ("persuade", "deceive", "bribe", "intimidate"):
            action = Action(
                action_id="act_1_1",
                actor_id="player_001",
                action_type=ActionType.SOCIAL,
                params={"verb": verb, "target": "npc.guard_b"},
                tick=1,
            )
            result = rules.resolve(action, world_state)
            assert result.requires_arbiter, f"{verb} should still require arbiter"


class TestCombatRules:
    def test_combat_goes_to_subsystem(self, rules: RulesEngine, world_state: WorldState):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.COMBAT,
            params={"verb": "attack", "target": "npc.guard_b"},
            tick=1,
        )
        result = rules.resolve(action, world_state)
        assert result.can_execute
        assert result.event_type == EventType.COMBAT
        assert "Combat Subsystem" in result.reason
