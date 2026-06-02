"""Tests for spatial semantics: cross-location observation via connections.

Covers visibility, noise, noise_leak, visual_leak across zones and locations.
"""

from __future__ import annotations

import pytest

from verisaria.engine.observation import ObservationDispatcher
from verisaria.engine.schemas import Event, EventType
from verisaria.engine.world import Connection, EntityState, LocationState, WorldState, ZoneState


@pytest.fixture
def multi_location_world():
    """World with two connected locations: town_square and tavern."""
    state = WorldState()

    # town_square with two zones
    state.locations["town_square"] = LocationState(
        location_id="town_square",
        zones={
            "center": ZoneState(
                zone_id="center", location_id="town_square",
                visibility="high", noise_level="loud", capacity=10,
            ),
            "alley": ZoneState(
                zone_id="alley", location_id="town_square",
                visibility="low", noise_level="quiet", capacity=5,
            ),
        },
        connected_locations=["tavern"],
        connections=[
            Connection(
                to_location="tavern",
                distance="adjacent",
                noise_leak=0.3,
                visual_leak=0.1,
            ),
        ],
    )

    # tavern with one zone
    state.locations["tavern"] = LocationState(
        location_id="tavern",
        zones={
            "main_hall": ZoneState(
                zone_id="main_hall", location_id="tavern",
                visibility="medium", noise_level="moderate", capacity=15,
            ),
        },
        connected_locations=["town_square"],
        connections=[
            Connection(
                to_location="town_square",
                distance="adjacent",
                noise_leak=0.3,
                visual_leak=0.1,
            ),
        ],
    )

    state.entities["player_001"] = EntityState(
        entity_id="player_001", entity_type="player",
        location_id="town_square", zone_id="center",
    )
    state.entities["npc.guard_b"] = EntityState(
        entity_id="npc.guard_b", entity_type="npc",
        location_id="town_square", zone_id="alley",
    )
    state.entities["npc.ele"] = EntityState(
        entity_id="npc.ele", entity_type="npc",
        location_id="tavern", zone_id="main_hall",
    )

    return state


@pytest.fixture
def dispatcher():
    return ObservationDispatcher()


# ---------------------------------------------------------------------------
# Same zone
# ---------------------------------------------------------------------------

class TestSameZone:
    def test_same_zone_full_perception(self, dispatcher, multi_location_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            zone_id="center",
            summary="player says hello",
            canonical_facts={"content": "hello", "volume": "normal"},
        )
        observations = dispatcher.dispatch(event, multi_location_world)
        player_obs = [o for o in observations if o.observer_id == "player_001"]
        assert len(player_obs) == 1
        assert player_obs[0].perception.heard_full_content is True


# ---------------------------------------------------------------------------
# Same location, different zone
# ---------------------------------------------------------------------------

class TestCrossZone:
    def test_different_zone_speech_low_volume(self, dispatcher, multi_location_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            zone_id="center",
            summary="player whispers",
            canonical_facts={"content": "secret", "volume": "low"},
        )
        observations = dispatcher.dispatch(event, multi_location_world)
        guard_obs = [o for o in observations if o.observer_id == "npc.guard_b"]
        # guard is in alley (quiet zone), player in center (loud zone)
        # low volume whisper may not cross zones well
        if guard_obs:
            assert guard_obs[0].perception.heard_full_content is False

    def test_different_zone_loud_speech(self, dispatcher, multi_location_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            zone_id="center",
            summary="player shouts",
            canonical_facts={"content": "help!", "volume": "loud"},
        )
        observations = dispatcher.dispatch(event, multi_location_world)
        guard_obs = [o for o in observations if o.observer_id == "npc.guard_b"]
        assert len(guard_obs) == 1
        assert guard_obs[0].perception.heard_full_content is True


# ---------------------------------------------------------------------------
# Cross-location via connections
# ---------------------------------------------------------------------------

class TestCrossLocation:
    def test_adjacent_location_loud_speech_heard(self, dispatcher, multi_location_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            zone_id="center",
            summary="player shouts",
            canonical_facts={"content": "help!", "volume": "loud"},
        )
        observations = dispatcher.dispatch(event, multi_location_world)
        ele_obs = [o for o in observations if o.observer_id == "npc.ele"]
        assert len(ele_obs) == 1
        assert "hearing" in ele_obs[0].perception.channels

    def test_adjacent_location_normal_speech_not_heard(self, dispatcher, multi_location_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            zone_id="center",
            summary="player talks",
            canonical_facts={"content": "hello", "volume": "normal"},
        )
        observations = dispatcher.dispatch(event, multi_location_world)
        ele_obs = [o for o in observations if o.observer_id == "npc.ele"]
        # normal speech with noise_leak 0.3 should not cross location
        assert len(ele_obs) == 0

    def test_adjacent_location_combat_seen(self, dispatcher, multi_location_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.COMBAT,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            zone_id="center",
            summary="player attacks guard",
            canonical_facts={"verb": "attack"},
        )
        observations = dispatcher.dispatch(event, multi_location_world)
        ele_obs = [o for o in observations if o.observer_id == "npc.ele"]
        # combat is visible and loud, but visual_leak is only 0.1
        # Should still be heard (combat always adds hearing), sight depends on leak
        assert len(ele_obs) == 1
        assert "hearing" in ele_obs[0].perception.channels

    def test_unconnected_location_no_observation(self, dispatcher, multi_location_world):
        # Add a third unconnected location
        multi_location_world.locations["forest"] = LocationState(
            location_id="forest",
            zones={"clearing": ZoneState(
                zone_id="clearing", location_id="forest",
                visibility="high", noise_level="quiet", capacity=20,
            )},
        )
        multi_location_world.entities["npc.wolf"] = EntityState(
            entity_id="npc.wolf", entity_type="npc",
            location_id="forest", zone_id="clearing",
        )

        event = Event(
            event_id="evt_0_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            zone_id="center",
            summary="player shouts",
            canonical_facts={"content": "help!", "volume": "loud"},
        )
        observations = dispatcher.dispatch(event, multi_location_world)
        wolf_obs = [o for o in observations if o.observer_id == "npc.wolf"]
        assert len(wolf_obs) == 0

    def test_high_noise_leak_cross_location(self, dispatcher, multi_location_world):
        # Increase noise leak between town_square and tavern
        multi_location_world.locations["town_square"].connections[0].noise_leak = 0.6

        event = Event(
            event_id="evt_0_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            zone_id="center",
            summary="player talks",
            canonical_facts={"content": "hello", "volume": "normal"},
        )
        observations = dispatcher.dispatch(event, multi_location_world)
        ele_obs = [o for o in observations if o.observer_id == "npc.ele"]
        # With high noise_leak, normal speech should be heard across location
        assert len(ele_obs) == 1
        assert "hearing" in ele_obs[0].perception.channels


# ---------------------------------------------------------------------------
# Movement visibility
# ---------------------------------------------------------------------------

class TestMovementVisibility:
    def test_movement_visible_cross_zone(self, dispatcher, multi_location_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.MOVEMENT,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            zone_id="center",
            summary="player moves",
            canonical_facts={"verb": "move", "to_location": "tavern"},
        )
        observations = dispatcher.dispatch(event, multi_location_world)
        guard_obs = [o for o in observations if o.observer_id == "npc.guard_b"]
        # guard is in same location different zone, visibility of center is high
        assert len(guard_obs) == 1
        assert "sight" in guard_obs[0].perception.channels

    def test_movement_visible_adjacent_location(self, dispatcher, multi_location_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.MOVEMENT,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            zone_id="center",
            summary="player moves",
            canonical_facts={"verb": "move", "to_location": "tavern"},
        )
        observations = dispatcher.dispatch(event, multi_location_world)
        ele_obs = [o for o in observations if o.observer_id == "npc.ele"]
        # visual_leak is only 0.1, movement needs > 0.1 to be seen
        assert len(ele_obs) == 0


# ---------------------------------------------------------------------------
# Persistence roundtrip
# ---------------------------------------------------------------------------

class TestConnectionPersistence:
    def test_connection_roundtrip(self):
        from verisaria.engine.persistence import _world_state_to_dict, _world_state_from_dict
        state = WorldState()
        state.locations["a"] = LocationState(
            location_id="a",
            connections=[
                Connection(to_location="b", noise_leak=0.3, visual_leak=0.1),
            ],
        )
        data = _world_state_to_dict(state)
        restored = _world_state_from_dict(data)
        assert len(restored.locations["a"].connections) == 1
        assert restored.locations["a"].connections[0].to_location == "b"
        assert restored.locations["a"].connections[0].noise_leak == 0.3
