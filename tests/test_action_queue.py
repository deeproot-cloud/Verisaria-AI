"""Tests for Action Queue: sorting, conflict detection, execution order.
"""

from __future__ import annotations

import pytest

from verisaria.engine.action_queue import ActionQueue, ActionConflict
from verisaria.engine.schemas import Action, ActionType, Event
from verisaria.engine.world import EntityState, LocationState, WorldCore, WorldState, ZoneState


@pytest.fixture
def populated_world():
    state = WorldState()
    state.entities["player_001"] = EntityState(
        entity_id="player_001", entity_type="player",
        location_id="town_square", zone_id="center",
    )
    state.entities["npc.guard_b"] = EntityState(
        entity_id="npc.guard_b", entity_type="npc",
        location_id="town_square", zone_id="center",
    )
    state.entities["npc.ele"] = EntityState(
        entity_id="npc.ele", entity_type="npc",
        location_id="tavern", zone_id="main_hall",
    )
    state.locations["town_square"] = LocationState(
        location_id="town_square",
        zones={
            "center": ZoneState(
                zone_id="center", location_id="town_square",
                capacity=10, occupant_ids=["player_001", "npc.guard_b"],
            ),
        },
    )
    return WorldCore(initial_state=state)


@pytest.fixture
def queue():
    return ActionQueue()


def make_action(
    actor_id: str,
    action_type: ActionType,
    target_id: str | None = None,
    params: dict | None = None,
    action_id: str = "act_0_1",
) -> Action:
    default_params: dict[str, Any] = {}
    if action_type == ActionType.COMBAT:
        default_params["verb"] = "attack"
    elif action_type == ActionType.PHYSICAL:
        default_params["verb"] = "look"
    elif action_type == ActionType.MOVEMENT:
        default_params["to_location"] = "town_square"
        default_params["to_zone"] = "center"
    elif action_type == ActionType.SPEECH:
        default_params["content"] = "hello"
    if params:
        default_params.update(params)
    return Action(
        action_id=action_id,
        actor_id=actor_id,
        action_type=action_type,
        target_id=target_id,
        params=default_params,
        tick=0,
    )


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

class TestSorting:
    def test_combat_before_movement(self, queue):
        combat = make_action("npc.guard_b", ActionType.COMBAT, action_id="act_a")
        move = make_action("player_001", ActionType.MOVEMENT, action_id="act_b")
        queue.submit(combat)
        queue.submit(move)
        sorted_a = queue._sort_actions(queue.get_pending())
        assert sorted_a[0].action_id == "act_a"
        assert sorted_a[1].action_id == "act_b"

    def test_player_before_npc_same_type(self, queue):
        npc = make_action("npc.guard_b", ActionType.PHYSICAL, action_id="act_a")
        player = make_action("player_001", ActionType.PHYSICAL, action_id="act_b")
        queue.submit(npc)
        queue.submit(player)
        sorted_a = queue._sort_actions(queue.get_pending())
        assert sorted_a[0].action_id == "act_b"
        assert sorted_a[1].action_id == "act_a"

    def test_lexical_tie_break(self, queue):
        a = make_action("npc.a", ActionType.PHYSICAL, action_id="act_001")
        b = make_action("npc.b", ActionType.PHYSICAL, action_id="act_002")
        queue.submit(b)
        queue.submit(a)
        sorted_a = queue._sort_actions(queue.get_pending())
        assert sorted_a[0].action_id == "act_001"
        assert sorted_a[1].action_id == "act_002"


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------

class TestConflictDetection:
    def test_same_actor_conflict(self, queue):
        a1 = make_action("player_001", ActionType.COMBAT, action_id="act_a")
        a2 = make_action("player_001", ActionType.MOVEMENT, action_id="act_b")
        queue.submit(a1)
        queue.submit(a2)
        conflicts = queue._detect_conflicts(queue.get_pending())
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "same_actor"

    def test_same_target_combat_conflict(self, queue):
        a1 = make_action("player_001", ActionType.COMBAT, target_id="npc.guard_b", action_id="act_a")
        a2 = make_action("npc.ele", ActionType.COMBAT, target_id="npc.guard_b", action_id="act_b")
        queue.submit(a1)
        queue.submit(a2)
        conflicts = queue._detect_conflicts(queue.get_pending())
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "same_target"

    def test_no_conflict_different_targets(self, queue):
        a1 = make_action("player_001", ActionType.COMBAT, target_id="npc.guard_b", action_id="act_a")
        a2 = make_action("npc.ele", ActionType.COMBAT, target_id="player_001", action_id="act_b")
        queue.submit(a1)
        queue.submit(a2)
        conflicts = queue._detect_conflicts(queue.get_pending())
        assert len(conflicts) == 0

    def test_same_target_movement_conflict(self, queue):
        a1 = make_action("player_001", ActionType.MOVEMENT, target_id="npc.guard_b", action_id="act_a")
        a2 = make_action("npc.ele", ActionType.MOVEMENT, target_id="npc.guard_b", action_id="act_b")
        queue.submit(a1)
        queue.submit(a2)
        conflicts = queue._detect_conflicts(queue.get_pending())
        assert len(conflicts) == 1


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

class TestResolution:
    def test_single_action_executes(self, queue, populated_world):
        action = make_action("player_001", ActionType.PHYSICAL, params={"verb": "look"})
        queue.submit(action)
        events = queue.resolve(populated_world)
        assert len(events) == 1
        assert queue.get_executed() == [action.action_id]

    def test_same_actor_lower_priority_cancelled(self, queue, populated_world):
        combat = make_action("player_001", ActionType.COMBAT, action_id="act_a")
        move = make_action("player_001", ActionType.MOVEMENT, action_id="act_b")
        queue.submit(combat)
        queue.submit(move)
        events = queue.resolve(populated_world)
        # Combat wins, movement cancelled
        assert combat.action_id in queue.get_executed()
        assert move.action_id in queue.get_cancelled()

    def test_same_target_combat_wins(self, queue, populated_world):
        # Both player and NPC attack the SAME third entity
        player_atk = make_action(
            "player_001", ActionType.COMBAT,
            target_id="npc.ele", action_id="act_a"
        )
        npc_atk = make_action(
            "npc.guard_b", ActionType.COMBAT,
            target_id="npc.ele", action_id="act_b"
        )
        queue.submit(npc_atk)
        queue.submit(player_atk)
        events = queue.resolve(populated_world)
        # Player priority > NPC priority, so NPC attack cancelled
        assert player_atk.action_id in queue.get_executed()
        assert npc_atk.action_id in queue.get_cancelled()

    def test_zone_capacity_invalidates_later_movement(self, queue, populated_world):
        # Add a new entity outside the zone
        populated_world.state.entities["npc.outsider"] = EntityState(
            entity_id="npc.outsider", entity_type="npc",
            location_id="tavern", zone_id="main_hall",
        )
        # Set capacity to current occupants + 1
        zone = populated_world.state.get_zone("town_square", "center")
        zone.capacity = len(zone.occupant_ids) + 1  # room for exactly 1 more

        move1 = make_action(
            "npc.ele", ActionType.MOVEMENT,
            params={"to_location": "town_square", "to_zone": "center"},
            action_id="act_a",
        )
        move2 = make_action(
            "npc.outsider", ActionType.MOVEMENT,
            params={"to_location": "town_square", "to_zone": "center"},
            action_id="act_b",
        )
        # npc.ele has lower actor priority than npc.outsider? No — both are npc.*
        # So lexical tie-break: act_a < act_b, so move1 executes first
        # After move1, zone is full, move2 should be invalidated
        queue.submit(move2)
        queue.submit(move1)
        events = queue.resolve(populated_world)
        executed = queue.get_executed()
        assert move1.action_id in executed
        assert move2.action_id not in executed

    def test_empty_queue_returns_empty(self, queue, populated_world):
        events = queue.resolve(populated_world)
        assert events == []

    def test_clear_resets_queue(self, queue):
        queue.submit(make_action("player_001", ActionType.PHYSICAL))
        queue.clear()
        assert queue.get_pending() == []


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------

class TestInvalidation:
    def test_actor_removed_invalidates_action(self, queue, populated_world):
        action = make_action("player_001", ActionType.COMBAT, action_id="act_a")
        queue.submit(action)
        # Remove actor
        del populated_world.state.entities["player_001"]
        events = queue.resolve(populated_world)
        assert action.action_id not in queue.get_executed()

    def test_target_removed_invalidates_action(self, queue, populated_world):
        action = make_action(
            "player_001", ActionType.COMBAT,
            target_id="npc.guard_b", action_id="act_a"
        )
        queue.submit(action)
        # Remove target
        del populated_world.state.entities["npc.guard_b"]
        events = queue.resolve(populated_world)
        assert action.action_id not in queue.get_executed()


# ---------------------------------------------------------------------------
# Resolution passthrough (dual-track elimination)
# ---------------------------------------------------------------------------

class TestResolutionPassthrough:
    def test_resolution_summary_used_in_event(self, queue, populated_world):
        """RulesEngine resolution summary is passed through to the Event."""
        action = make_action("player_001", ActionType.SPEECH, params={"content": "hello"})
        fake_resolution = type("FakeRes", (), {
            "summary": "rules engine summary",
            "canonical_facts": {"content": "hello", "custom": True},
        })()
        queue.submit(action, resolution=fake_resolution)
        events = queue.resolve(populated_world)
        assert len(events) == 1
        assert events[0].summary == "rules engine summary"
        assert events[0].canonical_facts["custom"] is True

    def test_no_resolution_uses_fallback(self, queue, populated_world):
        """Without a resolution, commit_action falls back to default generation."""
        action = make_action("player_001", ActionType.SPEECH, params={"content": "fallback"})
        queue.submit(action)  # no resolution
        events = queue.resolve(populated_world)
        assert len(events) == 1
        assert "fallback" in events[0].summary

    def test_clear_clears_resolutions(self, queue):
        action = make_action("player_001", ActionType.SPEECH)
        fake_resolution = type("FakeRes", (), {
            "summary": "test",
            "canonical_facts": {},
        })()
        queue.submit(action, resolution=fake_resolution)
        queue.clear()
        assert queue.get_pending() == []
