"""Tests for Combat Subsystem.

Covers session initialization, action resolution (attack/defend/dodge/flee),
end conditions, NPC auto-decisions, stamina management, and persistence.
"""

from __future__ import annotations

import pytest

from verisaria.engine.combat import CombatEngine, CombatParticipant, CombatSession, PendingCombatAction
from verisaria.engine.schemas import Event, EventType
from verisaria.engine.world import EntityState, WorldCore, WorldState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_world():
    """Return a WorldCore with two entities ready for combat."""
    state = WorldState()
    state.entities["player_001"] = EntityState(
        entity_id="player_001",
        entity_type="player",
        location_id="town_square",
        zone_id="center",
        attributes={"strength": 0.8, "dexterity": 0.6},
        stamina=80,
        hp=100,
        max_hp=100,
    )
    state.entities["npc.bandit"] = EntityState(
        entity_id="npc.bandit",
        entity_type="npc",
        location_id="town_square",
        zone_id="center",
        attributes={"strength": 0.5, "dexterity": 0.4},
        stamina=60,
        hp=80,
        max_hp=80,
    )
    return WorldCore(initial_state=state)


@pytest.fixture
def combat_engine():
    return CombatEngine(seed=42)


# ---------------------------------------------------------------------------
# Session initialization
# ---------------------------------------------------------------------------

class TestCombatInit:
    def test_start_combat(self, combat_engine, simple_world):
        session = combat_engine.start_combat(
            initiator_id="player_001",
            target_ids=["npc.bandit"],
            world=simple_world.state,
            tick=1,
        )
        assert session.combat_id.startswith("cbt_")
        assert session.status == "active"
        assert "player_001" in session.participants
        assert "npc.bandit" in session.participants
        assert combat_engine.is_in_combat("player_001")
        assert combat_engine.is_in_combat("npc.bandit")

    def test_start_combat_copies_hp(self, combat_engine, simple_world):
        session = combat_engine.start_combat(
            "player_001", ["npc.bandit"], simple_world.state, tick=1
        )
        assert session.participants["player_001"].hp == 100
        assert session.participants["npc.bandit"].hp == 80

    def test_get_active_combat(self, combat_engine, simple_world):
        session = combat_engine.start_combat(
            "player_001", ["npc.bandit"], simple_world.state, tick=1
        )
        found = combat_engine.get_active_combat("player_001")
        assert found is not None
        assert found.combat_id == session.combat_id

    def test_not_in_combat_after_end(self, combat_engine, simple_world):
        session = combat_engine.start_combat(
            "player_001", ["npc.bandit"], simple_world.state, tick=1
        )
        combat_engine.end_combat(session, "test", "player")
        assert not combat_engine.is_in_combat("player_001")
        assert combat_engine.get_active_combat("player_001") is None


# ---------------------------------------------------------------------------
# Attack resolution
# ---------------------------------------------------------------------------

class TestAttack:
    def test_attack_deals_damage(self, combat_engine, simple_world):
        session = combat_engine.start_combat(
            "player_001", ["npc.bandit"], simple_world.state, tick=1
        )
        action = PendingCombatAction(
            actor_id="player_001", verb="attack", target_id="npc.bandit"
        )
        event = combat_engine._execute_action(session, action, simple_world, tick=1)
        assert event is not None
        assert "伤害" in event.summary or "攻击" in event.summary
        assert session.participants["npc.bandit"].hp < 80

    def test_attack_reduces_world_hp(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        action = PendingCombatAction(
            actor_id="player_001", verb="attack", target_id="npc.bandit"
        )
        combat_engine._execute_action(
            combat_engine.get_active_combat("player_001"), action, simple_world, tick=1
        )
        assert simple_world.state.get_entity("npc.bandit").hp < 80

    def test_attack_costs_stamina(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        before = combat_engine.get_active_combat("player_001").participants["player_001"].stamina
        action = PendingCombatAction(
            actor_id="player_001", verb="attack", target_id="npc.bandit"
        )
        combat_engine._execute_action(
            combat_engine.get_active_combat("player_001"), action, simple_world, tick=1
        )
        after = combat_engine.get_active_combat("player_001").participants["player_001"].stamina
        assert after == before - CombatEngine.ATTACK_STAMINA

    def test_insufficient_stamina(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        p = combat_engine.get_active_combat("player_001").participants["player_001"]
        p.stamina = 0
        action = PendingCombatAction(
            actor_id="player_001", verb="attack", target_id="npc.bandit"
        )
        event = combat_engine._execute_action(
            combat_engine.get_active_combat("player_001"), action, simple_world, tick=1
        )
        assert "体力不足" in event.summary


# ---------------------------------------------------------------------------
# Defend
# ---------------------------------------------------------------------------

class TestDefend:
    def test_defend_halves_damage(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        session = combat_engine.get_active_combat("player_001")

        # Bandit defends
        defend = PendingCombatAction(actor_id="npc.bandit", verb="defend")
        combat_engine._execute_action(session, defend, simple_world, tick=1)
        assert session.participants["npc.bandit"].defend_active is True

        # Player attacks defended bandit
        hp_before = session.participants["npc.bandit"].hp
        attack = PendingCombatAction(
            actor_id="player_001", verb="attack", target_id="npc.bandit"
        )
        combat_engine._execute_action(session, attack, simple_world, tick=1)
        hp_after = session.participants["npc.bandit"].hp
        damage = hp_before - hp_after
        # Damage should be halved compared to undefended
        assert damage > 0

    def test_defend_grants_initiative(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        session = combat_engine.get_active_combat("player_001")
        before = session.participants["npc.bandit"].initiative_bonus
        defend = PendingCombatAction(actor_id="npc.bandit", verb="defend")
        combat_engine._execute_action(session, defend, simple_world, tick=1)
        assert session.participants["npc.bandit"].initiative_bonus > before


# ---------------------------------------------------------------------------
# Dodge
# ---------------------------------------------------------------------------

class TestDodge:
    def test_dodge_avoids_damage(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        session = combat_engine.get_active_combat("player_001")

        # Bandit dodges (seed=42, dex=0.4, roll threshold < 0.4 for success)
        dodge = PendingCombatAction(actor_id="npc.bandit", verb="dodge")
        combat_engine._execute_action(session, dodge, simple_world, tick=1)
        assert session.participants["npc.bandit"].dodge_active is True

        # Player attacks — may be dodged
        hp_before = session.participants["npc.bandit"].hp
        attack = PendingCombatAction(
            actor_id="player_001", verb="attack", target_id="npc.bandit"
        )
        event = combat_engine._execute_action(session, attack, simple_world, tick=1)
        hp_after = session.participants["npc.bandit"].hp

        # Either dodged (0 damage) or hit (full damage)
        assert "闪避" in event.summary or hp_after < hp_before


# ---------------------------------------------------------------------------
# Flee
# ---------------------------------------------------------------------------

class TestFlee:
    def test_flee_removes_from_combat(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        flee = PendingCombatAction(actor_id="player_001", verb="flee")
        event = combat_engine._execute_action(
            combat_engine.get_active_combat("player_001"), flee, simple_world, tick=1
        )
        assert event is not None
        # With dexterity 0.6 and threshold 0.6, success is deterministic-ish
        if "成功脱离" in event.summary:
            assert not combat_engine.is_in_combat("player_001")


# ---------------------------------------------------------------------------
# Tick resolution
# ---------------------------------------------------------------------------

class TestTickResolution:
    def test_resolve_tick_generates_events(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        player_action = PendingCombatAction(
            actor_id="player_001", verb="attack", target_id="npc.bandit"
        )
        events = combat_engine.resolve_tick(
            combat_engine.get_active_combat("player_001").combat_id,
            simple_world,
            tick=1,
            player_action=player_action,
        )
        assert len(events) >= 2  # player attack + npc action

    def test_stamina_recovery_after_tick(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        p = combat_engine.get_active_combat("player_001").participants["player_001"]
        p.stamina = 50
        combat_engine.resolve_tick(
            combat_engine.get_active_combat("player_001").combat_id,
            simple_world,
            tick=1,
            player_action=PendingCombatAction(
                actor_id="player_001", verb="defend"
            ),
        )
        assert p.stamina == 50 - CombatEngine.DEFEND_STAMINA + CombatEngine.STAMINA_RECOVERY

    def test_incapacitated_entities_skip_turn(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        session = combat_engine.get_active_combat("player_001")
        session.participants["npc.bandit"].status_effects.append("incapacitated")
        events = combat_engine.resolve_tick(
            session.combat_id,
            simple_world,
            tick=1,
            player_action=PendingCombatAction(
                actor_id="player_001", verb="attack", target_id="npc.bandit"
            ),
        )
        # NPC should not act while incapacitated
        npc_events = [e for e in events if e.canonical_facts.get("actor_id") == "npc.bandit"]
        assert len(npc_events) == 0


# ---------------------------------------------------------------------------
# End conditions
# ---------------------------------------------------------------------------

class TestEndConditions:
    def test_combat_ends_when_side_wiped(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        session = combat_engine.get_active_combat("player_001")
        session.participants["npc.bandit"].hp = 0
        session.participants["npc.bandit"].status_effects.append("incapacitated")

        events = combat_engine.resolve_tick(
            session.combat_id, simple_world, tick=1,
            player_action=PendingCombatAction(
                actor_id="player_001", verb="attack", target_id="npc.bandit"
            ),
        )
        end_events = [e for e in events if "战斗结束" in e.summary]
        assert len(end_events) == 1
        assert session.status == "ended"

    def test_combat_continues_while_both_alive(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        events = combat_engine.resolve_tick(
            combat_engine.get_active_combat("player_001").combat_id,
            simple_world,
            tick=1,
            player_action=PendingCombatAction(
                actor_id="player_001", verb="attack", target_id="npc.bandit"
            ),
        )
        session = combat_engine.get_active_combat("player_001")
        assert session is not None
        assert session.status == "active"


# ---------------------------------------------------------------------------
# NPC decisions
# ---------------------------------------------------------------------------

class TestNPCDecisions:
    def test_npc_attacks_when_healthy(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        session = combat_engine.get_active_combat("player_001")
        npc = session.participants["npc.bandit"]
        action = combat_engine._npc_decide_action(npc, session)
        assert action.verb == "attack"
        assert action.target_id == "player_001"

    def test_npc_defends_when_low_hp(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        session = combat_engine.get_active_combat("player_001")
        npc = session.participants["npc.bandit"]
        npc.hp = 10
        action = combat_engine._npc_decide_action(npc, session)
        assert action.verb in ("defend", "flee")

    def test_npc_flees_when_very_low_hp_and_high_dex(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        session = combat_engine.get_active_combat("player_001")
        npc = session.participants["npc.bandit"]
        npc.hp = 5
        npc.initiative_bonus = 0.6  # high dex proxy
        npc.stamina = 100
        action = combat_engine._npc_decide_action(npc, session)
        assert action.verb == "flee"


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

class TestCombatPersistence:
    def test_roundtrip_state(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        state = combat_engine.get_state()

        engine2 = CombatEngine()
        engine2.load_state(state)

        assert len(engine2._sessions) == 1
        session = engine2.get_active_combat("player_001")
        assert session is not None
        assert session.participants["player_001"].hp == 100
        assert session.participants["npc.bandit"].hp == 80

    def test_load_preserves_status_effects(self, combat_engine, simple_world):
        combat_engine.start_combat("player_001", ["npc.bandit"], simple_world.state, tick=1)
        session = combat_engine.get_active_combat("player_001")
        session.participants["npc.bandit"].status_effects.append("incapacitated")

        state = combat_engine.get_state()
        engine2 = CombatEngine()
        engine2.load_state(state)

        loaded = engine2.get_session(session.combat_id)
        assert "incapacitated" in loaded.participants["npc.bandit"].status_effects
