"""Tests for Combat action routing through ActionQueue (P2-3)."""

from __future__ import annotations

import pytest

from verisaria.engine.action_queue import ActionQueue
from verisaria.engine.schemas import Action, ActionType
from verisaria.engine.world import WorldCore, WorldState


class TestActionQueueCombatRouting:
    def test_resolve_with_combat_returns_combat_actions_separately(self):
        from verisaria.engine.world import EntityState
        queue = ActionQueue()
        world = WorldCore(initial_state=WorldState())
        world.state.entities["player_001"] = EntityState(
            entity_id="player_001", entity_type="player", location_id="loc_a"
        )
        world.state.entities["npc_guard"] = EntityState(
            entity_id="npc_guard", entity_type="npc", location_id="loc_a"
        )

        queue.submit(
            Action(
                action_id="act_0_1",
                actor_id="player_001",
                action_type=ActionType.COMBAT,
                params={"verb": "attack"},
                tick=0,
            )
        )
        queue.submit(
            Action(
                action_id="act_0_2",
                actor_id="npc_guard",
                action_type=ActionType.PHYSICAL,
                params={"verb": "look"},
                tick=0,
            )
        )

        events, combat_actions = queue.resolve_with_combat(world)
        assert len(events) == 1
        assert events[0].actor_id == "npc_guard"
        assert len(combat_actions) == 1
        assert combat_actions[0].action_type == ActionType.COMBAT

    def test_resolve_without_combat_ignores_combat(self):
        from verisaria.engine.world import EntityState
        queue = ActionQueue()
        world = WorldCore(initial_state=WorldState())
        world.state.entities["player_001"] = EntityState(
            entity_id="player_001", entity_type="player", location_id="loc_a"
        )

        queue.submit(
            Action(
                action_id="act_0_1",
                actor_id="player_001",
                action_type=ActionType.COMBAT,
                params={"verb": "attack"},
                tick=0,
            )
        )

        events = queue.resolve(world)
        assert len(events) == 0  # combat skipped in regular resolve

    def test_combat_conflict_same_actor_cancels_lower_priority(self):
        from verisaria.engine.world import EntityState
        queue = ActionQueue()
        world = WorldCore(initial_state=WorldState())
        world.state.entities["player_001"] = EntityState(
            entity_id="player_001", entity_type="player", location_id="loc_a"
        )

        queue.submit(
            Action(
                action_id="act_0_1",
                actor_id="player_001",
                action_type=ActionType.COMBAT,
                params={"verb": "attack"},
                tick=0,
            )
        )
        queue.submit(
            Action(
                action_id="act_0_2",
                actor_id="player_001",
                action_type=ActionType.SPEECH,
                params={"verb": "talk", "content": "hello"},
                tick=0,
            )
        )

        events, combat_actions = queue.resolve_with_combat(world)
        # Same-actor conflict: combat (priority 0) wins, speech cancelled
        assert len(combat_actions) == 1
        assert combat_actions[0].action_type == ActionType.COMBAT
        assert len(events) == 0  # speech cancelled
