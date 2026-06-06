"""Tests for Response Generator.

Verifies template selection, variable substitution, and integration
with Event types.
"""

from __future__ import annotations

import pytest

from verisaria.engine.response_generator import ResponseGenerator
from verisaria.engine.schemas import Event, EventType
from verisaria.engine.world import EntityState, WorldState


@pytest.fixture
def sample_world():
    state = WorldState()
    state.entities["player_001"] = EntityState(
        entity_id="player_001",
        entity_type="player",
        location_id="town_square",
        hp=100,
        max_hp=100,
    )
    state.entities["npc.guard_b"] = EntityState(
        entity_id="npc.guard_b",
        entity_type="npc",
        location_id="town_square",
        hp=80,
        max_hp=80,
    )
    return state


@pytest.fixture
def generator():
    return ResponseGenerator()


# ---------------------------------------------------------------------------
# Physical events
# ---------------------------------------------------------------------------

class TestPhysical:
    def test_look(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.PHYSICAL,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            summary="player_001 look",
            canonical_facts={"verb": "look"},
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "环顾四周" in text

    def test_wait(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.PHYSICAL,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            summary="player_001 wait",
            canonical_facts={"verb": "wait"},
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "静观其变" in text

    def test_steal(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.PHYSICAL,
            actor_id="player_001",
            target_id="npc.guard_b",
            tick=0,
            location_id="town_square",
            summary="player_001 steal",
            canonical_facts={"verb": "steal"},
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "试图从" in text
        assert "guard b" in text


# ---------------------------------------------------------------------------
# Movement events
# ---------------------------------------------------------------------------

class TestMovement:
    def test_move(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.MOVEMENT,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            summary="player_001 move",
            canonical_facts={"verb": "move", "to_location": "tavern"},
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "移动到了tavern" in text


# ---------------------------------------------------------------------------
# Speech events
# ---------------------------------------------------------------------------

class TestSpeech:
    def test_talk(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            target_id="npc.guard_b",
            tick=0,
            location_id="town_square",
            summary="player_001 talk",
            canonical_facts={"verb": "talk", "content": "你好"},
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "说：「你好」" in text
        assert "对guard b" in text

    def test_whisper(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            target_id="npc.guard_b",
            tick=0,
            location_id="town_square",
            summary="player_001 whisper",
            canonical_facts={"verb": "whisper", "content": "快走", "volume": "low"},
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "低声对guard b说" in text
        assert "快走" in text

    def test_shout(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.SPEECH,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            summary="player_001 shout",
            canonical_facts={"verb": "shout", "content": "停下！", "volume": "loud"},
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "大声喊道" in text


# ---------------------------------------------------------------------------
# Combat events
# ---------------------------------------------------------------------------

class TestCombat:
    def test_attack(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.COMBAT,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            summary="combat hit",
            canonical_facts={
                "sub_type": "combat_hit",
                "attacker": "player_001",
                "defender": "npc.guard_b",
                "damage": 15,
            },
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "击中" in text
        assert "15点伤害" in text

    def test_defend(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.COMBAT,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            summary="combat_defend",
            canonical_facts={"sub_type": "combat_defend", "actor_id": "player_001"},
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "防御" in text

    def test_incapacitated(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.COMBAT,
            actor_id="combat_system",
            tick=0,
            location_id="town_square",
            summary="npc.guard_b collapses",
            canonical_facts={"sub_type": "incapacitated", "entity_id": "npc.guard_b"},
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "倒在了地上" in text

    def test_combat_end(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.COMBAT,
            actor_id="combat_system",
            tick=0,
            location_id="town_square",
            summary="combat end",
            canonical_facts={"sub_type": "combat_end", "reason": "hostile wiped out"},
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "战斗结束了" in text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_events(self, generator, sample_world):
        text = generator.generate([], sample_world, "player_001")
        assert "时间悄然流逝" in text

    def test_unknown_verb_fallback(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.PHYSICAL,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            summary="player_001 does something weird",
            canonical_facts={"verb": "weird_unknown_verb"},
        )
        text = generator.generate([event], sample_world, "player_001")
        # Falls back to default template
        assert "做出了一个动作" in text

    def test_multiple_events(self, generator, sample_world):
        events = [
            Event(
                event_id="evt_0_1",
                event_type=EventType.PHYSICAL,
                actor_id="player_001",
                tick=0,
                location_id="town_square",
                summary="look",
                canonical_facts={"verb": "look"},
            ),
            Event(
                event_id="evt_0_2",
                event_type=EventType.MOVEMENT,
                actor_id="player_001",
                tick=0,
                location_id="town_square",
                summary="move",
                canonical_facts={"verb": "move", "to_location": "tavern"},
            ),
        ]
        text = generator.generate(events, sample_world, "player_001")
        assert "环顾四周" in text
        assert "移动到了tavern" in text
        assert "\n" in text

    def test_status_delta(self, generator):
        before = {"hp": 100, "location_id": "town_square"}
        after = {"hp": 85, "location_id": "tavern"}
        text = generator.generate_status_delta(before, after)
        assert text is not None
        assert "HP -15" in text
        assert "位置变更" in text

    def test_no_delta(self, generator):
        before = {"hp": 100}
        after = {"hp": 100}
        text = generator.generate_status_delta(before, after)
        assert text is None

    def test_player_display_name(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.PHYSICAL,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            summary="look",
            canonical_facts={"verb": "look"},
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "你环顾四周" in text

    def test_npc_display_name(self, generator, sample_world):
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.PHYSICAL,
            actor_id="npc.guard_b",
            tick=0,
            location_id="town_square",
            summary="climb",
            canonical_facts={"verb": "climb"},  # a narrated action (idle look is filtered noise)
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "guard b" in text
        assert "你" not in text

    def test_npc_idle_look_is_filtered_as_noise(self, generator, sample_world):
        """Audit 5 #3: a bystander's idle 'look' ('环顾四周') is filler — it must not
        be narrated, so the scene isn't drowned in every NPC looking around each tick."""
        event = Event(
            event_id="evt_0_1", event_type=EventType.PHYSICAL, actor_id="npc.guard_b",
            tick=0, location_id="town_square", summary="look",
            canonical_facts={"verb": "look"},
        )
        text = generator.generate([event], sample_world, "player_001")
        assert "环顾四周" not in text and "guard b" not in text

    def test_filters_events_by_player_location(self, generator, sample_world):
        """Events from other locations should not appear in narrative."""
        events = [
            Event(
                event_id="evt_0_1",
                event_type=EventType.PHYSICAL,
                actor_id="player_001",
                tick=0,
                location_id="town_square",
                summary="player look",
                canonical_facts={"verb": "look"},
            ),
            Event(
                event_id="evt_0_2",
                event_type=EventType.PHYSICAL,
                actor_id="npc.ele",
                tick=0,
                location_id="tavern",  # different location
                summary="ele wait",
                canonical_facts={"verb": "wait"},
            ),
        ]
        text = generator.generate(events, sample_world, "player_001")
        assert "环顾四周" in text
        assert "ele" not in text
        assert "静观其变" not in text

    def test_player_own_event_visible_even_from_other_location(
        self, generator, sample_world
    ):
        """Player always sees their own events (e.g. movement)."""
        event = Event(
            event_id="evt_0_1",
            event_type=EventType.MOVEMENT,
            actor_id="player_001",
            tick=0,
            location_id="town_square",
            summary="player move",
            canonical_facts={"verb": "move", "to_location": "tavern"},
        )
        # Player has already moved to tavern in world state
        sample_world.entities["player_001"].location_id = "tavern"
        text = generator.generate([event], sample_world, "player_001")
        assert "移动到了tavern" in text


# ---------------------------------------------------------------------------
# Location filtering during movement (P1.4)
# ---------------------------------------------------------------------------

class TestMovingPlayerLocationFilter:
    """On a movement tick, a mid-transit player hears NO other actor — neither
    the destination they only arrive into (P1.4) nor the origin they just left
    (P1.6). Only the player's own movement is narrated."""

    def _world_two_locs(self):
        from verisaria.engine.world import LocationState
        state = WorldState()
        state.entities["player_001"] = EntityState(
            entity_id="player_001", entity_type="player", location_id="tavern", hp=100
        )
        state.entities["npc.ele"] = EntityState(
            entity_id="npc.ele", entity_type="npc", location_id="tavern", hp=80
        )
        state.entities["npc.guard_b"] = EntityState(
            entity_id="npc.guard_b", entity_type="npc", location_id="town_square", hp=80
        )
        state.locations["town_square"] = LocationState(location_id="town_square")
        state.locations["tavern"] = LocationState(location_id="tavern")
        return state

    def _move_event(self, to_location):
        return Event(
            event_id="evt_move", event_type=EventType.MOVEMENT, actor_id="player_001",
            tick=0, location_id="town_square", summary="player 移动",
            canonical_facts={"verb": "move", "to_location": to_location},
        )

    def test_destination_npc_not_pre_seen_during_move(self, generator):
        # Player moving town_square -> tavern; ele is at the destination (tavern).
        world = self._world_two_locs()
        ele_speech = Event(
            event_id="evt_0_1", event_type=EventType.SPEECH, actor_id="npc.ele",
            tick=0, location_id="tavern", summary="npc.ele 说话",
            canonical_facts={"content": "你好", "volume": "normal"},
        )
        text = generator.generate(
            [ele_speech, self._move_event("tavern")], world, "player_001",
            viewer_location="town_square",
        )
        # ele's speech at the destination must NOT be shown mid-transit.
        assert "你好" not in text
        # The player's own movement is still narrated.
        assert "移动" in text or "位置" in text

    def test_origin_npc_not_re_heard_during_move(self, generator):
        # P1.6: player moving AWAY (tavern -> town_square); ele is at the origin
        # (tavern) and speaks — the leaving player must NOT hear it this tick.
        world = self._world_two_locs()
        world.entities["player_001"].location_id = "town_square"  # already arrived
        ele_speech = Event(
            event_id="evt_0_1", event_type=EventType.SPEECH, actor_id="npc.ele",
            tick=0, location_id="tavern", summary="npc.ele 说话",
            canonical_facts={"content": "有何需求", "volume": "normal"},
        )
        move = Event(
            event_id="evt_move", event_type=EventType.MOVEMENT, actor_id="player_001",
            tick=0, location_id="tavern", summary="player 移动",
            canonical_facts={"verb": "move", "to_location": "town_square"},
        )
        text = generator.generate(
            [ele_speech, move], world, "player_001", viewer_location="tavern",
        )
        assert "有何需求" not in text  # left the room → don't hear it
        assert "移动" in text or "位置" in text

    def test_non_move_tick_still_shows_local_npc(self, generator):
        # When the player is NOT moving, a co-located NPC is heard normally.
        world = self._world_two_locs()
        world.entities["player_001"].location_id = "town_square"
        guard_speech = Event(
            event_id="evt_0_1", event_type=EventType.SPEECH, actor_id="npc.guard_b",
            tick=0, location_id="town_square", summary="npc.guard_b 说话",
            canonical_facts={"content": "站住", "volume": "normal"},
        )
        text = generator.generate([guard_speech], world, "player_001")
        assert "站住" in text


class TestNpcIdleNotNarrated:
    """An NPC's idle 'wait' is noise and must not appear in player narrative
    (P1.4 — '去酒馆' should not surface 'ele静观其变')."""

    def test_npc_wait_suppressed(self, generator, sample_world):
        ele_wait = Event(
            event_id="evt_0_1", event_type=EventType.PHYSICAL, actor_id="npc.guard_b",
            tick=0, location_id="town_square", summary="npc.guard_b wait",
            canonical_facts={"verb": "wait"},
        )
        text = generator.generate([ele_wait], sample_world, "player_001")
        assert "静观其变" not in text

    def test_player_own_wait_still_shown(self, generator, sample_world):
        # The player's own wait is a deliberate action and still narrated.
        own_wait = Event(
            event_id="evt_0_1", event_type=EventType.PHYSICAL, actor_id="player_001",
            tick=0, location_id="town_square", summary="player_001 wait",
            canonical_facts={"verb": "wait"},
        )
        text = generator.generate([own_wait], sample_world, "player_001")
        assert "静观其变" in text
