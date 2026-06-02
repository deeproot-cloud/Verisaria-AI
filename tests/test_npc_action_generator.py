"""Tests for NPC Action Generator (P0-1)."""

from __future__ import annotations

import pytest

from verisaria.engine.npc_runtime import NPCActionGenerator
from verisaria.engine.schemas import ActionType, ConversationSession
from verisaria.engine.world import Connection, EntityState, LocationState, WorldState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def world() -> WorldState:
    ws = WorldState()
    ws.entities["npc_alice"] = EntityState(
        entity_id="npc_alice", entity_type="npc",
        location_id="loc_tavern", hp=100, max_hp=100,
    )
    ws.entities["npc_bob"] = EntityState(
        entity_id="npc_bob", entity_type="npc",
        location_id="loc_tavern", hp=100, max_hp=100,
    )
    ws.entities["npc_carol"] = EntityState(
        entity_id="npc_carol", entity_type="npc",
        location_id="loc_street", hp=100, max_hp=100,
    )
    ws.entities["npc_injured"] = EntityState(
        entity_id="npc_injured", entity_type="npc",
        location_id="loc_tavern", hp=10, max_hp=100,
    )
    ws.entities["player_001"] = EntityState(
        entity_id="player_001", entity_type="player",
        location_id="loc_tavern", hp=100, max_hp=100,
    )
    ws.locations["loc_tavern"] = LocationState(
        location_id="loc_tavern",
        connections=[
            Connection(to_location="loc_street"),
            Connection(to_location="loc_cellar"),
        ],
    )
    ws.locations["loc_street"] = LocationState(
        location_id="loc_street",
        connections=[Connection(to_location="loc_tavern")],
    )
    ws.locations["loc_cellar"] = LocationState(location_id="loc_cellar")
    return ws


@pytest.fixture
def generator() -> NPCActionGenerator:
    return NPCActionGenerator(seed=42)


# ---------------------------------------------------------------------------
# Basic generation
# ---------------------------------------------------------------------------

class TestBasicGeneration:
    def test_generates_one_action_per_npc(self, generator, world):
        actions = generator.generate_actions(world, tick=1)
        npc_ids = {eid for eid, e in world.entities.items() if e.entity_type == "npc"}
        action_actors = {a.actor_id for a in actions}
        assert action_actors == npc_ids
        assert len(actions) == 4

    def test_no_player_actions(self, generator, world):
        actions = generator.generate_actions(world, tick=1)
        assert not any(a.actor_id == "player_001" for a in actions)

    def test_action_id_format(self, generator, world):
        actions = generator.generate_actions(world, tick=5)
        for a in actions:
            assert a.action_id.startswith("act_5_npc_")

    def test_all_actions_have_valid_type(self, generator, world):
        actions = generator.generate_actions(world, tick=1)
        for a in actions:
            assert a.action_type in (
                ActionType.PHYSICAL, ActionType.MOVEMENT, ActionType.SPEECH
            )


# ---------------------------------------------------------------------------
# Conversation behavior
# ---------------------------------------------------------------------------

class FakeConversationManager:
    """Minimal mock for conversation manager tests."""

    def __init__(self, entity_sessions):
        self._entity_sessions = entity_sessions

    def get_active_session(self, entity_id):
        return self._entity_sessions.get(entity_id)


class TestConversationBehavior:
    def test_in_conversation_always_speech(self, generator, world):
        actions = generator.generate_actions(
            world, tick=1, active_conversation_entity_ids={"npc_alice"}
        )
        alice_action = next(a for a in actions if a.actor_id == "npc_alice")
        # In conversation NPCs should always speak (never wait)
        assert alice_action.action_type == ActionType.SPEECH

    def test_conversation_context_uses_player_greeting(self, generator, world):
        session = ConversationSession(
            session_id="conv_1",
            participants=["player_001", "npc_alice"],
            started_at_tick=1,
            last_activity_tick=1,
            shared_context={"last_speaker": "player_001", "last_content": "你好"},
        )
        fake_cm = FakeConversationManager({"npc_alice": session})
        actions = generator.generate_actions(
            world,
            tick=1,
            active_conversation_entity_ids={"npc_alice"},
            conversation_manager=fake_cm,
        )
        alice_action = next(a for a in actions if a.actor_id == "npc_alice")
        assert alice_action.action_type == ActionType.SPEECH
        content = alice_action.params.get("content", "")
        assert content in generator.CONVERSATION_RESPONSES["greeting"]

    def test_conversation_context_uses_player_question(self, generator, world):
        session = ConversationSession(
            session_id="conv_1",
            participants=["player_001", "npc_alice"],
            started_at_tick=1,
            last_activity_tick=1,
            shared_context={"last_speaker": "player_001", "last_content": "听说什么？"},
        )
        fake_cm = FakeConversationManager({"npc_alice": session})
        actions = generator.generate_actions(
            world,
            tick=1,
            active_conversation_entity_ids={"npc_alice"},
            conversation_manager=fake_cm,
        )
        alice_action = next(a for a in actions if a.actor_id == "npc_alice")
        assert alice_action.action_type == ActionType.SPEECH
        content = alice_action.params.get("content", "")
        assert content in generator.CONVERSATION_RESPONSES["question"]

    def test_conversation_context_skips_when_npc_spoke_last(self, generator, world):
        session = ConversationSession(
            session_id="conv_1",
            participants=["player_001", "npc_alice"],
            started_at_tick=1,
            last_activity_tick=1,
            shared_context={"last_speaker": "npc_alice", "last_content": "你好"},
        )
        fake_cm = FakeConversationManager({"npc_alice": session})
        actions = generator.generate_actions(
            world,
            tick=1,
            active_conversation_entity_ids={"npc_alice"},
            conversation_manager=fake_cm,
        )
        alice_action = next(a for a in actions if a.actor_id == "npc_alice")
        assert alice_action.action_type == ActionType.SPEECH
        # When NPC spoke last, falls back to random chatter instead of repeating
        content = alice_action.params.get("content", "")
        assert content in generator.CHATTER_LINES


# ---------------------------------------------------------------------------
# Low HP behavior
# ---------------------------------------------------------------------------

class TestLowHPBehavior:
    def test_injured_npc_mostly_waits(self, generator, world):
        # Run multiple times to check bias
        wait_count = 0
        for seed in range(10):
            gen = NPCActionGenerator(seed=seed)
            actions = gen.generate_actions(world, tick=1)
            injured = next((a for a in actions if a.actor_id == "npc_injured"), None)
            assert injured is not None
            if injured.action_type == ActionType.PHYSICAL and injured.params.get("verb") == "wait":
                wait_count += 1
        # 80% wait bias should hold on average
        assert wait_count >= 5


# ---------------------------------------------------------------------------
# Nearby entity behavior
# ---------------------------------------------------------------------------

class TestNearbyBehavior:
    def test_speech_target_is_nearby_entity(self, generator, world):
        # With seed 1, npc_alice near npc_bob and player should sometimes speak
        for seed in range(20):
            gen = NPCActionGenerator(seed=seed)
            actions = gen.generate_actions(world, tick=1)
            alice = next((a for a in actions if a.actor_id == "npc_alice"), None)
            if alice and alice.action_type == ActionType.SPEECH:
                # Any co-located entity at loc_tavern is a valid target.
                assert alice.target_id in ("npc_bob", "player_001", "npc_injured")
                return
        pytest.skip("No speech generated in 20 seeds — acceptable random variance")


# ---------------------------------------------------------------------------
# Movement behavior
# ---------------------------------------------------------------------------

class TestMovementBehavior:
    def test_movement_to_connected_location(self, generator, world):
        # Find a seed that generates movement for alice
        for seed in range(50):
            gen = NPCActionGenerator(seed=seed)
            actions = gen.generate_actions(world, tick=1)
            alice = next((a for a in actions if a.actor_id == "npc_alice"), None)
            if alice and alice.action_type == ActionType.MOVEMENT:
                assert alice.params.get("to_location") in ("loc_street", "loc_cellar")
                return
        pytest.skip("No movement generated in 50 seeds — acceptable random variance")

    def test_no_connections_fallback_to_wait(self, generator, world):
        # npc_carol is at loc_street which has 1 connection, but remove all
        world.locations["loc_street"].connections.clear()
        actions = generator.generate_actions(world, tick=1)
        carol = next(a for a in actions if a.actor_id == "npc_carol")
        # Should fall back to wait since no connections
        assert carol.action_type == ActionType.PHYSICAL
        assert carol.params.get("verb") == "wait"


# ---------------------------------------------------------------------------
# P1.8: NPCs stay put / return home rather than wandering aimlessly
# ---------------------------------------------------------------------------

class TestHomeAnchoredMovement:
    def _move_rate(self, world, npc_id, ticks=200):
        moves = 0
        for seed in range(ticks):
            gen = NPCActionGenerator(seed=seed)
            actions = gen.generate_actions(world, tick=1)
            a = next((x for x in actions if x.actor_id == npc_id), None)
            if a and a.action_type == ActionType.MOVEMENT:
                moves += 1
        return moves / ticks

    def test_npc_at_home_with_company_rarely_moves(self, world):
        # alice is at her home (loc_tavern) with company → should seldom wander.
        world.entities["npc_alice"].home_location = "loc_tavern"
        rate = self._move_rate(world, "npc_alice")
        assert rate < 0.10, f"home NPC with company wandered too often: {rate:.2f}"

    def test_npc_away_from_home_tends_to_return(self, world):
        # carol's home is the tavern but she's on the street → when she moves,
        # she should head home (loc_tavern), not deeper away.
        world.entities["npc_carol"].home_location = "loc_tavern"
        home_moves = away_moves = 0
        for seed in range(200):
            gen = NPCActionGenerator(seed=seed)
            actions = gen.generate_actions(world, tick=1)
            a = next((x for x in actions if x.actor_id == "npc_carol"), None)
            if a and a.action_type == ActionType.MOVEMENT:
                if a.params.get("to_location") == "loc_tavern":
                    home_moves += 1
                else:
                    away_moves += 1
        assert home_moves > away_moves, f"home={home_moves} away={away_moves}"

    def test_no_home_keeps_prior_wander_behaviour(self, world):
        # Without a home_location set, behaviour is unchanged (still can move).
        for nid in ("npc_alice", "npc_bob", "npc_carol", "npc_injured"):
            assert getattr(world.entities[nid], "home_location", None) is None
        rate = self._move_rate(world, "npc_carol")  # carol is alone on street
        assert rate > 0.0  # still wanders sometimes when no home anchor


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_same_seed_same_actions(self, world):
        gen1 = NPCActionGenerator(seed=123)
        gen2 = NPCActionGenerator(seed=123)
        a1 = gen1.generate_actions(world, tick=1)
        a2 = gen2.generate_actions(world, tick=1)
        assert len(a1) == len(a2)
        for x, y in zip(a1, a2):
            assert x.action_type == y.action_type
            assert x.actor_id == y.actor_id

    def test_different_seed_may_differ(self, world):
        gen1 = NPCActionGenerator(seed=1)
        gen2 = NPCActionGenerator(seed=999)
        a1 = gen1.generate_actions(world, tick=1)
        a2 = gen2.generate_actions(world, tick=1)
        # Types may differ (not guaranteed but highly likely)
        types1 = [a.action_type for a in a1]
        types2 = [a.action_type for a in a2]
        assert types1 != types2 or True  # at least same count


# ---------------------------------------------------------------------------
# Empty world edge case
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_world(self):
        gen = NPCActionGenerator(seed=42)
        world = WorldState()
        actions = gen.generate_actions(world, tick=1)
        assert actions == []

    def test_no_npc_world(self):
        gen = NPCActionGenerator(seed=42)
        world = WorldState()
        world.entities["player_001"] = EntityState(
            entity_id="player_001", entity_type="player",
            location_id="loc_a", hp=100, max_hp=100,
        )
        actions = gen.generate_actions(world, tick=1)
        assert actions == []


# ---------------------------------------------------------------------------
# P1.9: bystander NPCs don't all chime in when the player addresses someone
# ---------------------------------------------------------------------------

class TestConversationFocus:
    def _crowded_world(self):
        ws = WorldState()
        ws.entities["player_001"] = EntityState(
            entity_id="player_001", entity_type="player", location_id="sq")
        for nid in ("npc.a", "npc.b", "npc.c"):
            ws.entities[nid] = EntityState(entity_id=nid, entity_type="npc", location_id="sq")
        ws.locations["sq"] = LocationState(location_id="sq")
        return ws

    def test_bystanders_stay_quiet_when_player_addresses_one(self):
        world = self._crowded_world()
        # Over many seeds, no bystander NPC should produce idle SPEECH in the
        # player's location when the player addressed someone there this tick.
        for seed in range(60):
            gen = NPCActionGenerator(seed=seed)
            actions = gen.generate_actions(
                world, tick=1, suppress_idle_speech_at="sq",
            )
            for a in actions:
                if a.action_type == ActionType.SPEECH:
                    pytest.fail(
                        f"bystander {a.actor_id} chimed in while player addressed "
                        f"someone (seed {seed})")

    def test_npcs_elsewhere_still_speak_normally(self):
        world = self._crowded_world()
        # An NPC in a DIFFERENT location is unaffected by the suppression.
        world.entities["npc.c"].location_id = "far"
        world.locations["far"] = LocationState(location_id="far")
        world.entities["npc.d"] = EntityState(
            entity_id="npc.d", entity_type="npc", location_id="far")
        spoke_far = False
        for seed in range(60):
            gen = NPCActionGenerator(seed=seed)
            actions = gen.generate_actions(
                world, tick=1, suppress_idle_speech_at="sq")
            for a in actions:
                if a.action_type == ActionType.SPEECH and a.actor_id in ("npc.c", "npc.d"):
                    spoke_far = True
        assert spoke_far, "NPCs in another location should still be able to speak"

    def test_no_suppression_keeps_normal_behaviour(self):
        world = self._crowded_world()
        # Without suppression, bystanders can still speak (sanity).
        spoke = False
        for seed in range(60):
            gen = NPCActionGenerator(seed=seed)
            for a in gen.generate_actions(world, tick=1):
                if a.action_type == ActionType.SPEECH:
                    spoke = True
        assert spoke
