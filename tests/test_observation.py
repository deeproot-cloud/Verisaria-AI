"""Tests for Observation Dispatch."""

import pytest

from verisaria.engine.observation import ObservationDispatcher
from verisaria.engine.schemas import Event, EventType
from verisaria.engine.world import EntityState, LocationState, WorldState, ZoneState


@pytest.fixture
def world_with_zones() -> WorldState:
    return WorldState(
        tick=1,
        entities={
            "player_001": EntityState(
                entity_id="player_001",
                entity_type="player",
                location_id="town_square",
                zone_id="center",
            ),
            "npc.ele": EntityState(
                entity_id="npc.ele",
                entity_type="npc",
                location_id="town_square",
                zone_id="center",
            ),
            "npc.guard_b": EntityState(
                entity_id="npc.guard_b",
                entity_type="npc",
                location_id="town_square",
                zone_id="market_corner",
            ),
            "npc.blacksmith": EntityState(
                entity_id="npc.blacksmith",
                entity_type="npc",
                location_id="blacksmith",
                zone_id="forge_area",
            ),
        },
        locations={
            "town_square": LocationState(
                location_id="town_square",
                zones={
                    "center": ZoneState(
                        zone_id="center",
                        location_id="town_square",
                        visibility="high",
                        noise_level="loud",
                    ),
                    "market_corner": ZoneState(
                        zone_id="market_corner",
                        location_id="town_square",
                        visibility="medium",
                        noise_level="moderate",
                    ),
                    "alley_entrance": ZoneState(
                        zone_id="alley_entrance",
                        location_id="town_square",
                        visibility="low",
                        noise_level="quiet",
                    ),
                },
            ),
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
        },
    )


@pytest.fixture
def dispatcher() -> ObservationDispatcher:
    return ObservationDispatcher()


class TestSpeechObservation:
    def test_same_zone_speech_full_perception(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            target_id="npc.ele",
            tick=1,
            location_id="town_square",
            zone_id="center",
            summary="player_001 对 npc.ele 低声说话",
            canonical_facts={"content": "快离开这儿", "volume": "low"},
        )
        observations = dispatcher.dispatch(event, world_with_zones)
        obs_map = {o.observer_id: o for o in observations}

        # Actor and target get full perception
        assert "player_001" in obs_map
        assert "npc.ele" in obs_map
        player_obs = obs_map["player_001"]
        assert "sight" in player_obs.perception.channels
        assert "hearing" in player_obs.perception.channels
        # Direct participants always hear full content regardless of volume
        assert player_obs.perception.heard_full_content is True

        # Target (same zone, direct participant) also gets full content
        ele_obs = obs_map["npc.ele"]
        assert ele_obs.perception.heard_full_content is True

    def test_different_zone_normal_volume_no_hearing(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            tick=1,
            location_id="town_square",
            zone_id="center",
            summary="player_001 说话",
            canonical_facts={"content": "着火了！", "volume": "normal"},
        )
        observations = dispatcher.dispatch(event, world_with_zones)
        obs_map = {o.observer_id: o for o in observations}

        # Same location, different zone + normal volume = no hearing
        if "npc.guard_b" in obs_map:
            guard_obs = obs_map["npc.guard_b"]
            assert "hearing" not in guard_obs.perception.channels
            # But can still see if visibility allows
            if "sight" in guard_obs.perception.channels:
                assert guard_obs.detail_level == "partial"

    def test_different_zone_low_volume_no_hearing(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            tick=1,
            location_id="town_square",
            zone_id="center",
            summary="player_001 低声说话",
            canonical_facts={"content": "秘密计划", "volume": "low"},
        )
        observations = dispatcher.dispatch(event, world_with_zones)
        obs_map = {o.observer_id: o for o in observations}

        # Different zone + low volume = no hearing for non-participants
        if "npc.guard_b" in obs_map:
            guard_obs = obs_map["npc.guard_b"]
            assert "hearing" not in guard_obs.perception.channels

    def test_loud_volume_crosses_zones(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            tick=1,
            location_id="town_square",
            zone_id="center",
            summary="player_001 愤怒大喊",
            canonical_facts={"content": "你们到底在隐瞒什么！", "volume": "loud"},
        )
        observations = dispatcher.dispatch(event, world_with_zones)
        obs_map = {o.observer_id: o for o in observations}

        # Loud speech crosses zones within same location
        assert "npc.guard_b" in obs_map
        guard_obs = obs_map["npc.guard_b"]
        assert "hearing" in guard_obs.perception.channels
        assert guard_obs.perception.heard_full_content is True
        assert guard_obs.perception.distance == "far"


class TestMovementObservation:
    def test_movement_sight_only(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.MOVEMENT,
            actor_id="player_001",
            tick=1,
            location_id="town_square",
            zone_id="center",
            summary="player_001 移动到 town_square.center",
            canonical_facts={"to_location": "town_square", "to_zone": "center"},
        )
        observations = dispatcher.dispatch(event, world_with_zones)
        obs_map = {o.observer_id: o for o in observations}

        assert "npc.ele" in obs_map
        ele_obs = obs_map["npc.ele"]
        assert "sight" in ele_obs.perception.channels
        assert "hearing" not in ele_obs.perception.channels
        assert ele_obs.perception.saw is not None


class TestCombatObservation:
    def test_combat_visible_and_loud(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.COMBAT,
            actor_id="player_001",
            target_id="npc.guard_b",
            tick=1,
            location_id="town_square",
            zone_id="center",
            summary="player_001 攻击 npc.guard_b",
            canonical_facts={"verb": "attack"},
        )
        observations = dispatcher.dispatch(event, world_with_zones)
        obs_map = {o.observer_id: o for o in observations}

        # Combat is both visible and loud
        assert "npc.ele" in obs_map
        ele_obs = obs_map["npc.ele"]
        assert "sight" in ele_obs.perception.channels
        assert "hearing" in ele_obs.perception.channels


class TestDifferentLocation:
    def test_different_location_no_perception(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            tick=1,
            location_id="town_square",
            zone_id="center",
            summary="player_001 说话",
            canonical_facts={"content": "test", "volume": "normal"},
        )
        observations = dispatcher.dispatch(event, world_with_zones)
        observer_ids = {o.observer_id for o in observations}

        # Blacksmith is in different location, should not perceive
        assert "npc.blacksmith" not in observer_ids


class TestActorAlwaysPerceives:
    def test_actor_perceives_even_in_different_zone(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        # Move player to market_corner
        world_with_zones.entities["player_001"].zone_id = "market_corner"

        event = Event(
            event_id="evt_1_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            target_id="npc.ele",
            tick=1,
            location_id="town_square",
            zone_id="center",
            summary="player_001 对 npc.ele 说话",
            canonical_facts={"content": "快走", "volume": "low"},
        )
        observations = dispatcher.dispatch(event, world_with_zones)
        obs_map = {o.observer_id: o for o in observations}

        # Actor always perceives, even if not in same zone
        assert "player_001" in obs_map
        player_obs = obs_map["player_001"]
        assert player_obs.perception.heard_full_content is True
        assert player_obs.detail_level == "full"


class TestObservationSkip:
    def test_same_location_low_visibility_records_skip(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        """An observer in the same location but a different zone with a
        low-visibility event gets a skip record."""
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.MOVEMENT,
            actor_id="player_001",
            tick=1,
            location_id="town_square",
            zone_id="alley_entrance",
            summary="player_001 移动到 alley_entrance",
            canonical_facts={"to_location": "town_square", "to_zone": "alley_entrance"},
        )
        dispatcher.dispatch(event, world_with_zones)
        skips = dispatcher.get_skips()
        skip_map = {s.observer_id: s for s in skips}

        # npc.guard_b is in market_corner (same location, different zone).
        # alley_entrance has visibility="low", so guard_b cannot see the movement.
        assert "npc.guard_b" in skip_map
        assert skip_map["npc.guard_b"].reason == "insufficient_perception"
        assert skip_map["npc.guard_b"].source_event_id == "evt_1_1"

    def test_clear_resets_skips(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.MOVEMENT,
            actor_id="player_001",
            tick=1,
            location_id="town_square",
            zone_id="alley_entrance",
            summary="test",
            canonical_facts={"to_location": "town_square", "to_zone": "alley_entrance"},
        )
        dispatcher.dispatch(event, world_with_zones)
        assert len(dispatcher.get_skips()) > 0
        dispatcher.clear()
        assert dispatcher.get_skips() == []


class TestTrivialSelfActionFiltered:
    """P1.3: an NPC should not form a memory of its own idle wait/look — those
    are noise, not memories ('记住自己发呆')."""

    def test_actor_does_not_observe_own_wait(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.PHYSICAL,
            actor_id="npc.guard_b",
            tick=1,
            location_id="town_square",
            zone_id="market_corner",
            summary="npc.guard_b wait",
            canonical_facts={"verb": "wait"},
        )
        observations = dispatcher.dispatch(event, world_with_zones)
        observer_ids = {o.observer_id for o in observations}
        assert "npc.guard_b" not in observer_ids  # no self-memory of own wait

    def test_actor_does_not_observe_own_look(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.PHYSICAL,
            actor_id="npc.ele",
            tick=1,
            location_id="town_square",
            zone_id="center",
            summary="npc.ele look",
            canonical_facts={"verb": "look"},
        )
        observations = dispatcher.dispatch(event, world_with_zones)
        observer_ids = {o.observer_id for o in observations}
        assert "npc.ele" not in observer_ids

    def test_wait_event_produces_no_observations_at_all(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        # A pure idle 'wait' is noise for everyone, not just the actor.
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.PHYSICAL,
            actor_id="npc.ele",
            tick=1,
            location_id="town_square",
            zone_id="center",
            summary="npc.ele wait",
            canonical_facts={"verb": "wait"},
        )
        observations = dispatcher.dispatch(event, world_with_zones)
        assert observations == []

    def test_actor_still_observes_own_meaningful_action(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        # Stealing is meaningful — the actor still perceives its own act.
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.PHYSICAL,
            actor_id="npc.guard_b",
            tick=1,
            location_id="town_square",
            zone_id="market_corner",
            summary="npc.guard_b steal short_sword",
            canonical_facts={"verb": "steal"},
        )
        observations = dispatcher.dispatch(event, world_with_zones)
        observer_ids = {o.observer_id for o in observations}
        assert "npc.guard_b" in observer_ids

    def test_others_still_observe_a_look(
        self, dispatcher: ObservationDispatcher, world_with_zones: WorldState
    ):
        # ele looking around — guard in same zone is NOT filtered (only the
        # actor's *self*-observation of trivial actions is dropped).
        world_with_zones.entities["npc.guard_b"].zone_id = "center"
        event = Event(
            event_id="evt_1_1",
            event_type=EventType.PHYSICAL,
            actor_id="npc.ele",
            tick=1,
            location_id="town_square",
            zone_id="center",
            summary="npc.ele look",
            canonical_facts={"verb": "look"},
        )
        observations = dispatcher.dispatch(event, world_with_zones)
        observer_ids = {o.observer_id for o in observations}
        assert "npc.guard_b" in observer_ids
