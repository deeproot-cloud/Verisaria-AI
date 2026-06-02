"""Combat Subsystem: rule-driven 1v1 / 1vN encounter resolution.

Phase-11 minimal version:
- No LLM involvement (deterministic)
- Tick-based execution
- Support: attack, defend, dodge, flee
- No magic, no AoE, no combos, no equipment bonuses
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from verisaria.engine.schemas import Action, Event, EventType
from verisaria.engine.world import WorldCore, WorldState


# ---------------------------------------------------------------------------
# Combat data structures
# ---------------------------------------------------------------------------

@dataclass
class CombatParticipant:
    entity_id: str
    side: str  # "player" | "hostile" | "ally"
    hp: int
    max_hp: int
    stamina: int
    max_stamina: int = 100
    initiative_bonus: float = 0.0
    defend_active: bool = False
    dodge_active: bool = False
    status_effects: list[str] = field(default_factory=list)


@dataclass
class CombatSession:
    combat_id: str
    started_at_tick: int
    location_id: str
    participants: dict[str, CombatParticipant] = field(default_factory=dict)
    status: str = "active"  # active | ended
    winner_side: str | None = None
    end_reason: str | None = None


@dataclass
class PendingCombatAction:
    actor_id: str
    verb: str  # attack | defend | dodge | flee
    target_id: str | None = None


# ---------------------------------------------------------------------------
# Combat Engine
# ---------------------------------------------------------------------------

class CombatEngine:
    """Rule-driven combat resolution.

    Design:
    - Each tick, collect combat actions from all participants.
    - Player submits via CLI; NPCs auto-decide based on simple heuristics.
    - Actions resolve in initiative order (dexterity + defend_bonus).
    - Attack damage = max(5, attacker.str*30 - defender.dex*10).
    - Defend halves incoming damage and grants +1 initiative next tick.
    - Dodge: dexterity roll > 0.5 to avoid all damage, else full damage.
    - Flee: dexterity > 0.6 → success, move to adjacent location, end combat for entity.
    - Stamina recovery: +5 per tick for all living participants.
    """

    ATTACK_STAMINA = 15
    DEFEND_STAMINA = 10
    DODGE_STAMINA = 20
    FLEE_STAMINA = 10
    STAMINA_RECOVERY = 5
    MIN_DAMAGE = 5

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self._sessions: dict[str, CombatSession] = {}
        self._entity_combat: dict[str, str] = {}  # entity_id -> combat_id
        self._seq = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start_combat(
        self,
        initiator_id: str,
        target_ids: list[str],
        world: WorldState,
        tick: int,
    ) -> CombatSession:
        """Initialize a combat session from the current world state."""
        self._seq += 1
        combat_id = f"cbt_{tick}_{self._seq}"

        initiator = world.get_entity(initiator_id)
        location_id = initiator.location_id if initiator else "unknown"

        participants: dict[str, CombatParticipant] = {}
        all_ids = [initiator_id] + target_ids

        for eid in all_ids:
            ent = world.get_entity(eid)
            if ent is None:
                continue
            attrs = ent.attributes
            side = "player" if eid == initiator_id else "hostile"
            participants[eid] = CombatParticipant(
                entity_id=eid,
                side=side,
                hp=ent.hp,
                max_hp=ent.max_hp,
                stamina=ent.stamina,
                max_stamina=ent.max_stamina,
                initiative_bonus=attrs.get("dexterity", 0.5) * 0.5,
            )
            self._entity_combat[eid] = combat_id

        session = CombatSession(
            combat_id=combat_id,
            started_at_tick=tick,
            location_id=location_id,
            participants=participants,
        )
        self._sessions[combat_id] = session
        return session

    def end_combat(
        self,
        session: CombatSession,
        reason: str,
        winner_side: str | None,
    ) -> None:
        """Mark a combat session as ended and clean up entity mappings."""
        session.status = "ended"
        session.end_reason = reason
        session.winner_side = winner_side
        for eid in list(session.participants.keys()):
            if self._entity_combat.get(eid) == session.combat_id:
                del self._entity_combat[eid]

    # ------------------------------------------------------------------
    # Tick resolution
    # ------------------------------------------------------------------

    def resolve_tick(
        self,
        combat_id: str,
        world: WorldCore,
        tick: int,
        player_action: PendingCombatAction | None = None,
    ) -> list[Event]:
        """Resolve one combat tick: collect actions, execute, check end conditions."""
        session = self._sessions.get(combat_id)
        if session is None or session.status != "active":
            return []

        # Reset per-tick flags
        for p in session.participants.values():
            p.defend_active = False
            p.dodge_active = False

        # Collect actions
        actions = self._collect_actions(session, player_action)

        # Sort by initiative (descending)
        actions.sort(key=lambda a: self._initiative_score(session, a.actor_id), reverse=True)

        events: list[Event] = []
        for action in actions:
            ev = self._execute_action(session, action, world, tick)
            if ev:
                events.append(ev)

        # Check incapacitation / death
        for p in list(session.participants.values()):
            if p.hp <= 0 and "incapacitated" not in p.status_effects:
                p.status_effects.append("incapacitated")
                events.append(
                    self._make_event(
                        combat_id, tick, "incapacitated",
                        f"{p.entity_id} 倒下，失去战斗能力",
                        {"entity_id": p.entity_id, "side": p.side},
                    )
                )
                # Sync world state
                ent = world.state.get_entity(p.entity_id)
                if ent:
                    ent.hp = 0

        # Stamina recovery for living participants
        for p in session.participants.values():
            if "incapacitated" not in p.status_effects:
                p.stamina = min(p.max_stamina, p.stamina + self.STAMINA_RECOVERY)

        # Sync stamina back to world state so non-combat systems see combat
        # usage/recovery (P2.2).
        for p in session.participants.values():
            ent = world.state.get_entity(p.entity_id)
            if ent is not None:
                ent.stamina = p.stamina

        # Check end conditions
        end_events = self._check_end_conditions(session, world, tick)
        events.extend(end_events)

        return events

    def _collect_actions(
        self,
        session: CombatSession,
        player_action: PendingCombatAction | None,
    ) -> list[PendingCombatAction]:
        """Gather all combat actions for this tick."""
        actions: list[PendingCombatAction] = []
        submitted_ids: set[str] = set()

        if player_action is not None:
            actions.append(player_action)
            submitted_ids.add(player_action.actor_id)

        # Auto-generate NPC actions
        for eid, participant in session.participants.items():
            if eid in submitted_ids:
                continue
            if "incapacitated" in participant.status_effects:
                continue
            action = self._npc_decide_action(participant, session)
            actions.append(action)

        return actions

    def _npc_decide_action(
        self,
        participant: CombatParticipant,
        session: CombatSession,
    ) -> PendingCombatAction:
        """Simple heuristic for NPC combat decisions."""
        hp_ratio = participant.hp / participant.max_hp if participant.max_hp > 0 else 1.0

        # Flee if low hp and high dexterity
        if hp_ratio < 0.3 and participant.initiative_bonus > 0.3 and participant.stamina >= self.FLEE_STAMINA:
            return PendingCombatAction(actor_id=participant.entity_id, verb="flee")

        # Defend if low hp
        if hp_ratio < 0.4 and participant.stamina >= self.DEFEND_STAMINA:
            return PendingCombatAction(actor_id=participant.entity_id, verb="defend")

        # Attack if enough stamina
        if participant.stamina >= self.ATTACK_STAMINA:
            # Pick target: hostile to player's side, or any non-ally
            targets = [
                p for p in session.participants.values()
                if p.entity_id != participant.entity_id and p.side != participant.side
                and "incapacitated" not in p.status_effects
            ]
            if targets:
                target = self._rng.choice(targets)
                return PendingCombatAction(
                    actor_id=participant.entity_id,
                    verb="attack",
                    target_id=target.entity_id,
                )

        # Default: defend if possible
        if participant.stamina >= self.DEFEND_STAMINA:
            return PendingCombatAction(actor_id=participant.entity_id, verb="defend")

        return PendingCombatAction(actor_id=participant.entity_id, verb="dodge")

    # ------------------------------------------------------------------
    # Action execution
    # ------------------------------------------------------------------

    def _execute_action(
        self,
        session: CombatSession,
        action: PendingCombatAction,
        world: WorldCore,
        tick: int,
    ) -> Event | None:
        """Execute a single combat action and return an Event."""
        participant = session.participants.get(action.actor_id)
        if participant is None or "incapacitated" in participant.status_effects:
            return None

        # Stamina check
        cost = self._stamina_cost(action.verb)
        if participant.stamina < cost:
            return self._make_event(
                session.combat_id, tick, "combat_fail",
                f"{action.actor_id} 体力不足，无法 {action.verb}",
                {"actor_id": action.actor_id, "verb": action.verb, "reason": "insufficient_stamina"},
            )

        participant.stamina -= cost

        if action.verb == "attack":
            return self._execute_attack(session, action, world, tick)
        if action.verb == "defend":
            return self._execute_defend(session, action, tick)
        if action.verb == "dodge":
            return self._execute_dodge(session, action, tick)
        if action.verb == "flee":
            return self._execute_flee(session, action, world, tick)

        return None

    def _execute_attack(
        self,
        session: CombatSession,
        action: PendingCombatAction,
        world: WorldCore,
        tick: int,
    ) -> Event | None:
        attacker = session.participants.get(action.actor_id)
        defender = session.participants.get(action.target_id) if action.target_id else None
        if attacker is None or defender is None:
            return None

        # Calculate damage
        attack_str = world.state.get_entity(action.actor_id)
        str_val = attack_str.attributes.get("strength", 0.5) if attack_str else 0.5
        def_dex = world.state.get_entity(action.target_id)
        dex_val = def_dex.attributes.get("dexterity", 0.5) if def_dex else 0.5

        damage = max(self.MIN_DAMAGE, int(str_val * 30 - dex_val * 10))

        # Check dodge
        if defender.dodge_active:
            if self._rng.random() < dex_val:
                return self._make_event(
                    session.combat_id, tick, "combat_dodge",
                    f"{action.target_id} 闪避了 {action.actor_id} 的攻击",
                    {"attacker": action.actor_id, "defender": action.target_id, "damage": 0},
                )
            # Dodge failed — full damage

        # Check defend
        if defender.defend_active:
            damage = max(1, damage // 2)
            defender.defend_active = False

        defender.hp -= damage

        # Sync world state
        ent = world.state.get_entity(action.target_id)
        if ent:
            ent.hp = max(0, ent.hp - damage)

        return self._make_event(
            session.combat_id, tick, "combat_hit",
            f"{action.actor_id} 攻击 {action.target_id}，造成 {damage} 点伤害",
            {
                "attacker": action.actor_id,
                "defender": action.target_id,
                "damage": damage,
                "defender_hp_remaining": defender.hp,
            },
        )

    def _execute_defend(
        self,
        session: CombatSession,
        action: PendingCombatAction,
        tick: int,
    ) -> Event:
        participant = session.participants[action.actor_id]
        participant.defend_active = True
        participant.initiative_bonus += 1.0

        return self._make_event(
            session.combat_id, tick, "combat_defend",
            f"{action.actor_id} 采取防御姿态",
            {"actor_id": action.actor_id, "initiative_bonus": participant.initiative_bonus},
        )

    def _execute_dodge(
        self,
        session: CombatSession,
        action: PendingCombatAction,
        tick: int,
    ) -> Event:
        participant = session.participants[action.actor_id]
        participant.dodge_active = True

        return self._make_event(
            session.combat_id, tick, "combat_dodge_stance",
            f"{action.actor_id} 准备闪避",
            {"actor_id": action.actor_id},
        )

    def _execute_flee(
        self,
        session: CombatSession,
        action: PendingCombatAction,
        world: WorldCore,
        tick: int,
    ) -> Event:
        participant = session.participants[action.actor_id]
        dex_val = world.state.get_entity(action.actor_id)
        dex = dex_val.attributes.get("dexterity", 0.5) if dex_val else 0.5

        if dex > 0.6:
            # Flee success — move to adjacent location if possible
            ent = world.state.get_entity(action.actor_id)
            if ent:
                loc = world.state.locations.get(ent.location_id)
                if loc and loc.connections:
                    new_loc = self._rng.choice(loc.connections).to_location
                    ent.location_id = new_loc
                elif loc and loc.connected_locations:
                    # Fallback to legacy connected_locations
                    new_loc = self._rng.choice(loc.connected_locations)
                    ent.location_id = new_loc

            # Remove from combat
            if action.actor_id in session.participants:
                del session.participants[action.actor_id]
            if self._entity_combat.get(action.actor_id) == session.combat_id:
                del self._entity_combat[action.actor_id]

            return self._make_event(
                session.combat_id, tick, "combat_flee",
                f"{action.actor_id} 成功脱离战斗",
                {"actor_id": action.actor_id, "success": True},
            )

        return self._make_event(
            session.combat_id, tick, "combat_flee_fail",
            f"{action.actor_id} 尝试逃跑但失败了",
            {"actor_id": action.actor_id, "success": False},
        )

    # ------------------------------------------------------------------
    # End conditions
    # ------------------------------------------------------------------

    def _check_end_conditions(
        self,
        session: CombatSession,
        world: WorldCore,
        tick: int,
    ) -> list[Event]:
        """Check if combat should end and generate events."""
        events: list[Event] = []

        # Group by side
        sides: dict[str, list[CombatParticipant]] = {}
        for p in session.participants.values():
            sides.setdefault(p.side, []).append(p)

        # Check if any side has all incapacitated
        for side, parts in sides.items():
            alive = [p for p in parts if "incapacitated" not in p.status_effects]
            if not alive:
                # This side is wiped out
                other_sides = [s for s in sides if s != side]
                winner = other_sides[0] if other_sides else None
                self.end_combat(session, f"{side}_wiped_out", winner)
                events.append(
                    self._make_event(
                        session.combat_id, tick, "combat_end",
                        f"战斗结束：{side} 全军覆没",
                        {"winner_side": winner, "reason": f"{side}_wiped_out"},
                    )
                )
                return events

        # Check if player fled (only player left the session)
        if "player" in sides and len(sides["player"]) == 0:
            self.end_combat(session, "player_fled", "hostile")
            events.append(
                self._make_event(
                    session.combat_id, tick, "combat_end",
                    "战斗结束：玩家逃离",
                    {"winner_side": "hostile", "reason": "player_fled"},
                )
            )
            return events

        return events

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _initiative_score(self, session: CombatSession, entity_id: str) -> float:
        p = session.participants.get(entity_id)
        if p is None:
            return 0.0
        return p.initiative_bonus

    def _stamina_cost(self, verb: str) -> int:
        return {
            "attack": self.ATTACK_STAMINA,
            "defend": self.DEFEND_STAMINA,
            "dodge": self.DODGE_STAMINA,
            "flee": self.FLEE_STAMINA,
        }.get(verb, 10)

    def _make_event(
        self,
        combat_id: str,
        tick: int,
        event_type: str,
        summary: str,
        canonical_facts: dict[str, Any],
    ) -> Event:
        session = self._sessions.get(combat_id)
        loc_id = session.location_id if session else "combat"
        return Event(
            event_id=f"evt_{tick}_{combat_id}_{self._seq}_{event_type}",
            event_type=EventType.COMBAT,
            actor_id="combat_system",
            tick=tick,
            location_id=loc_id,
            summary=summary,
            canonical_facts={"combat_id": combat_id, "sub_type": event_type, **canonical_facts},
        )

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_active_combat(self, entity_id: str) -> CombatSession | None:
        combat_id = self._entity_combat.get(entity_id)
        if combat_id is None:
            return None
        session = self._sessions.get(combat_id)
        if session is None or session.status != "active":
            return None
        return session

    def get_session(self, combat_id: str) -> CombatSession | None:
        return self._sessions.get(combat_id)

    def is_in_combat(self, entity_id: str) -> bool:
        return self.get_active_combat(entity_id) is not None

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def get_state(self) -> dict[str, Any]:
        return {
            "sessions": {
                cid: {
                    "combat_id": s.combat_id,
                    "started_at_tick": s.started_at_tick,
                    "location_id": s.location_id,
                    "participants": {
                        eid: {
                            "entity_id": p.entity_id,
                            "side": p.side,
                            "hp": p.hp,
                            "max_hp": p.max_hp,
                            "stamina": p.stamina,
                            "max_stamina": p.max_stamina,
                            "initiative_bonus": p.initiative_bonus,
                            "defend_active": p.defend_active,
                            "dodge_active": p.dodge_active,
                            "status_effects": list(p.status_effects),
                        }
                        for eid, p in s.participants.items()
                    },
                    "status": s.status,
                    "winner_side": s.winner_side,
                    "end_reason": s.end_reason,
                }
                for cid, s in self._sessions.items()
            },
            "entity_combat": dict(self._entity_combat),
            "seq": self._seq,
        }

    def load_state(self, state: dict[str, Any]) -> None:
        self._sessions = {}
        for cid, sdata in state.get("sessions", {}).items():
            participants = {}
            for eid, pdata in sdata.get("participants", {}).items():
                participants[eid] = CombatParticipant(
                    entity_id=pdata["entity_id"],
                    side=pdata["side"],
                    hp=pdata["hp"],
                    max_hp=pdata["max_hp"],
                    stamina=pdata["stamina"],
                    max_stamina=pdata["max_stamina"],
                    initiative_bonus=pdata["initiative_bonus"],
                    defend_active=pdata["defend_active"],
                    dodge_active=pdata["dodge_active"],
                    status_effects=list(pdata.get("status_effects", [])),
                )
            self._sessions[cid] = CombatSession(
                combat_id=sdata["combat_id"],
                started_at_tick=sdata["started_at_tick"],
                location_id=sdata["location_id"],
                participants=participants,
                status=sdata["status"],
                winner_side=sdata.get("winner_side"),
                end_reason=sdata.get("end_reason"),
            )
        self._entity_combat = dict(state.get("entity_combat", {}))
        self._seq = state.get("seq", 0)
