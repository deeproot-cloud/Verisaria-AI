"""Tests for World Core."""

import pytest

from verisaria.engine.schemas import Action, ActionType, EventType
from verisaria.engine.world import EntityState, LocationState, WorldCore, WorldState, ZoneState


@pytest.fixture
def simple_world() -> WorldCore:
    state = WorldState(
        tick=1,
        entities={
            "player_001": EntityState(
                entity_id="player_001",
                entity_type="player",
                location_id="town_square",
                zone_id="center",
            ),
            "npc.guard_b": EntityState(
                entity_id="npc.guard_b",
                entity_type="npc",
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
                        occupant_ids=["player_001", "npc.guard_b"],
                    ),
                    "market_corner": ZoneState(
                        zone_id="market_corner",
                        location_id="town_square",
                        occupant_ids=[],
                    ),
                },
            ),
        },
    )
    return WorldCore(initial_state=state)


class TestWorldCore:
    def test_commit_speech_action(self, simple_world: WorldCore):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.SPEECH,
            params={"content": "快离开这儿", "volume": "low"},
            tick=1,
        )
        event = simple_world.commit_action(action)
        assert event.event_type == EventType.SPEECH
        assert event.actor_id == "player_001"
        assert "快离开这儿" in event.summary
        assert event.canonical_facts["volume"] == "low"
        assert len(simple_world.event_log) == 1

    def test_commit_movement_action_updates_state(self, simple_world: WorldCore):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.MOVEMENT,
            params={"to_location": "town_square", "to_zone": "market_corner"},
            tick=1,
        )
        event = simple_world.commit_action(action)
        assert event.event_type == EventType.MOVEMENT

        # Entity moved
        entity = simple_world.state.get_entity("player_001")
        assert entity is not None
        assert entity.zone_id == "market_corner"

        # Zone occupants updated
        old_zone = simple_world.state.get_zone("town_square", "center")
        new_zone = simple_world.state.get_zone("town_square", "market_corner")
        assert old_zone is not None
        assert new_zone is not None
        assert "player_001" not in old_zone.occupant_ids
        assert "player_001" in new_zone.occupant_ids

    def test_commit_physical_look_action(self, simple_world: WorldCore):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.PHYSICAL,
            params={"verb": "look"},
            tick=1,
        )
        event = simple_world.commit_action(action)
        assert event.event_type == EventType.PHYSICAL
        assert event.canonical_facts["verb"] == "look"

    def test_event_log_append_only(self, simple_world: WorldCore):
        for i in range(3):
            action = Action(
                action_id=f"act_1_{i}",
                actor_id="player_001",
                action_type=ActionType.SPEECH,
                params={"content": f"msg_{i}"},
                tick=1,
            )
            simple_world.commit_action(action)

        assert len(simple_world.event_log) == 3
        events = simple_world.event_log.get_events(since_tick=1)
        assert len(events) == 3
        assert events[0].event_id == "evt_1_1"
        assert events[1].event_id == "evt_1_2"

    def test_snapshot(self, simple_world: WorldCore):
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.SPEECH,
            params={"content": "test"},
            tick=1,
        )
        simple_world.commit_action(action)
        snap = simple_world.snapshot()
        assert snap["tick"] == 1
        assert snap["event_count"] == 1
        assert snap["entity_count"] == 2

    def test_tick_advance(self, simple_world: WorldCore):
        assert simple_world.state.tick == 1
        simple_world.tick_advance()
        assert simple_world.state.tick == 2

    def test_commit_action_with_external_summary(self, simple_world: WorldCore):
        """When summary/canonical_facts are provided, use them instead of regenerating."""
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.SPEECH,
            params={"content": "快离开这儿", "volume": "low"},
            tick=1,
        )
        event = simple_world.commit_action(
            action,
            summary="custom summary from rules engine",
            canonical_facts={"content": "快离开这儿", "volume": "low", "extra": True},
        )
        assert event.summary == "custom summary from rules engine"
        assert event.canonical_facts["extra"] is True
        assert event.canonical_facts["volume"] == "low"

    def test_commit_action_fallback_when_no_external_provided(self, simple_world: WorldCore):
        """When no external summary/facts provided, fallback to default generation."""
        action = Action(
            action_id="act_1_1",
            actor_id="player_001",
            action_type=ActionType.SPEECH,
            params={"content": "hello", "volume": "normal"},
            tick=1,
        )
        event = simple_world.commit_action(action)
        assert "hello" in event.summary
        assert event.canonical_facts["volume"] == "normal"
