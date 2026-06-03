"""World Core: manages the world state and Event Log.

The World Core is the single source of truth for:
- Event Log (append-only, immutable)
- Current world state (entities, locations, zones)
- Action execution pipeline

All state mutations go through commit_action() which produces Events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verisaria.engine.schemas import Action, Event, EventType


# ---------------------------------------------------------------------------
# World State
# ---------------------------------------------------------------------------

@dataclass
class EntityState:
    entity_id: str
    entity_type: str  # "player" | "npc"
    location_id: str
    name: str = ""    # human-readable display name (pack-declared); id-derived if blank
    zone_id: str | None = None
    attributes: dict[str, float] = field(default_factory=dict)
    traits: list[str] = field(default_factory=list)
    inventory: list[str] = field(default_factory=list)
    hp: int = 100
    max_hp: int = 100
    stamina: int = 100
    max_stamina: int = 100
    # An NPC's "post" — where it belongs and tends to return to (P1.8). When set,
    # the NPC seldom wanders off and heads home when away.
    home_location: str | None = None


@dataclass
class ZoneState:
    zone_id: str
    location_id: str
    visibility: str = "medium"  # "high" | "medium" | "low"
    exposure: str = "medium"    # "high" | "medium" | "low"
    noise_level: str = "moderate"  # "loud" | "moderate" | "quiet"
    capacity: int = 10
    occupant_ids: list[str] = field(default_factory=list)


@dataclass
class Connection:
    to_location: str
    distance: str = "adjacent"  # adjacent | near | far
    noise_leak: float = 0.0
    visual_leak: float = 0.0
    description: str = ""


@dataclass
class LocationState:
    location_id: str
    name: str = ""    # human-readable display name (pack-declared); id if blank
    zones: dict[str, ZoneState] = field(default_factory=dict)
    connected_locations: list[str] = field(default_factory=list)
    connections: list[Connection] = field(default_factory=list)


@dataclass
class WorldState:
    tick: int = 0
    entities: dict[str, EntityState] = field(default_factory=dict)
    locations: dict[str, LocationState] = field(default_factory=dict)
    # Pack-declared mutable world facts the player's choices can change (PLAY-3
    # Channel C), e.g. {"refugees_admitted": True}. Read by campaign drivers,
    # narration and (for public facts) NPC grounding.
    world_vars: dict[str, Any] = field(default_factory=dict)

    def get_entity(self, entity_id: str) -> EntityState | None:
        return self.entities.get(entity_id)

    def display_name(self, entity_id: str) -> str:
        """Human-readable name for an entity (pack-declared, else id-derived)."""
        e = self.entities.get(entity_id)
        if e is not None and e.name:
            return e.name
        return entity_id.replace("npc.", "").replace("_", " ")

    def location_label(self, location_id: str) -> str:
        """Human-readable name for a location (pack-declared, else the id)."""
        loc = self.locations.get(location_id)
        if loc is not None and getattr(loc, "name", ""):
            return loc.name
        return location_id or ""

    def get_zone(self, location_id: str, zone_id: str) -> ZoneState | None:
        loc = self.locations.get(location_id)
        if loc is None:
            return None
        return loc.zones.get(zone_id)


# ---------------------------------------------------------------------------
# Event Log
# ---------------------------------------------------------------------------

class EventLog:
    """Append-only log of all world events."""

    def __init__(self) -> None:
        self._events: list[Event] = []

    def append(self, event: Event) -> None:
        self._events.append(event)

    def get_events(self, since_tick: int = 0) -> list[Event]:
        return [e for e in self._events if e.tick >= since_tick]

    def get_events_by_actor(self, actor_id: str) -> list[Event]:
        return [e for e in self._events if e.actor_id == actor_id]

    def get_event(self, event_id: str) -> Event | None:
        for e in self._events:
            if e.event_id == event_id:
                return e
        return None

    def __len__(self) -> int:
        return len(self._events)


# ---------------------------------------------------------------------------
# World Core
# ---------------------------------------------------------------------------

class WorldCore:
    """Central world runtime."""

    def __init__(self, initial_state: WorldState | None = None) -> None:
        self.state = initial_state or WorldState()
        self.event_log = EventLog()
        self._action_seq = 0

    def next_action_id(self) -> str:
        self._action_seq += 1
        return f"act_{self.state.tick}_{self._action_seq}"

    def next_event_id(self) -> str:
        return f"evt_{self.state.tick}_{len(self.event_log) + 1}"

    def tick_advance(self) -> None:
        """Advance to the next tick."""
        self.state.tick += 1
        self._action_seq = 0

    def commit_action(
        self,
        action: Action,
        summary: str | None = None,
        canonical_facts: dict[str, Any] | None = None,
    ) -> Event:
        """Execute an Action and produce an immutable Event.

        If ``summary`` or ``canonical_facts`` are provided (e.g. from
        RulesEngine resolution), they are used directly instead of the
        fallback generation logic.  State mutation still goes through
        ``_update_state_from_action()``.
        """
        actor = self.state.get_entity(action.actor_id)
        location_id = actor.location_id if actor else "unknown"
        zone_id = action.zone_id or (actor.zone_id if actor else None)

        event = Event(
            event_id=self.next_event_id(),
            event_type=self._action_to_event_type(action),
            actor_id=action.actor_id,
            target_id=action.target_id,
            tick=self.state.tick,
            location_id=location_id,
            zone_id=zone_id,
            summary=summary if summary is not None else self._generate_summary(action),
            canonical_facts=canonical_facts if canonical_facts is not None else self._extract_canonical_facts(action),
            source_action_id=action.action_id,
        )
        self.event_log.append(event)
        self._update_state_from_action(action)
        return event

    def _action_to_event_type(self, action: Action) -> EventType:
        mapping = {
            "speech": EventType.SPEECH,
            "movement": EventType.MOVEMENT,
            "physical": EventType.PHYSICAL,
            "social": EventType.SOCIAL,
            "combat": EventType.COMBAT,
            "look": EventType.PHYSICAL,
            "wait": EventType.SYSTEM,
        }
        return mapping.get(action.action_type.value, EventType.SYSTEM)

    def _generate_summary(self, action: Action) -> str:
        verb = action.params.get("verb", action.action_type.value)
        target = f" 对 {action.target_id}" if action.target_id else ""
        if action.action_type.value == "speech":
            content = action.params.get("content", "")
            return f"{action.actor_id}{target} 说: {content}"
        return f"{action.actor_id}{target} {verb}"

    def _extract_canonical_facts(self, action: Action) -> dict[str, Any]:
        facts: dict[str, Any] = {"action_type": action.action_type.value}
        if action.action_type.value == "speech":
            facts["content"] = action.params.get("content", "")
            facts["volume"] = action.params.get("volume", "normal")
        elif action.action_type.value == "movement":
            facts["to_location"] = action.params.get("to_location", "")
            facts["to_zone"] = action.params.get("to_zone", "")
        elif "verb" in action.params:
            facts["verb"] = action.params["verb"]
        return facts

    def _update_state_from_action(self, action: Action) -> None:
        """Update mutable world state based on action."""
        if action.action_type.value == "movement":
            entity = self.state.get_entity(action.actor_id)
            if entity is not None:
                old_loc = entity.location_id
                old_zone = entity.zone_id
                new_loc = action.params.get("to_location")
                new_zone = action.params.get("to_zone")

                # If no zone specified, pick the first zone in the new location
                if new_loc and new_zone is None:
                    loc = self.state.locations.get(new_loc)
                    if loc and loc.zones:
                        new_zone = next(iter(loc.zones.keys()))

                # Update entity location
                if new_loc:
                    entity.location_id = new_loc
                if new_zone is not None:
                    entity.zone_id = new_zone

                # Update zone occupants
                old_zone_obj = self.state.get_zone(old_loc, old_zone) if old_zone else None
                if old_zone_obj and action.actor_id in old_zone_obj.occupant_ids:
                    old_zone_obj.occupant_ids.remove(action.actor_id)
                new_zone_obj = self.state.get_zone(new_loc, new_zone) if new_zone else None
                if new_zone_obj and action.actor_id not in new_zone_obj.occupant_ids:
                    new_zone_obj.occupant_ids.append(action.actor_id)

        elif action.action_type.value == "physical":
            entity = self.state.get_entity(action.actor_id)
            if entity is not None:
                verb = action.params.get("verb", "")
                stamina_costs = {
                    "steal": 10,
                    "sneak": 15,
                    "climb": 10,
                    "look": 2,
                }
                cost = stamina_costs.get(verb, 0)
                if cost > 0:
                    entity.stamina = max(0, entity.stamina - cost)

    def snapshot(self) -> dict[str, Any]:
        """Return a serializable snapshot of current state."""
        return {
            "tick": self.state.tick,
            "entity_count": len(self.state.entities),
            "event_count": len(self.event_log),
            "entities": [
                {
                    "entity_id": e.entity_id,
                    "location_id": e.location_id,
                    "zone_id": e.zone_id,
                }
                for e in self.state.entities.values()
            ],
        }
