"""Observation Dispatch: Event → Perception for each observer.

Phase-2 minimal version: deterministic rule-based filtering.
No LLM-generated Interpretation yet (that comes in Phase 3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verisaria.engine.schemas import Event, EventType, Observation, Perception
from verisaria.engine.world import WorldState


# ---------------------------------------------------------------------------
# Skip record ("not noticed" is also information)
# ---------------------------------------------------------------------------

@dataclass
class ObservationSkip:
    observer_id: str
    source_event_id: str
    reason: str
    tick: int


# ---------------------------------------------------------------------------
# Observation Dispatcher
# ---------------------------------------------------------------------------

class ObservationDispatcher:
    """Dispatch Events to observers, generating Perception or Skip records.

    Filtering rules (deterministic, Phase 2):
    - Same location + same zone → full perception
    - Same location + different zone → depends on visibility / noise
    - Different location → no perception (unless extremely loud)
    - Actor/target of the event always get full perception
    """

    def __init__(self) -> None:
        self._obs_seq = 0
        self._skips: list[ObservationSkip] = []

    # Trivial idle actions are noise — they should not become anyone's memory.
    _NOISE_VERBS = {"wait"}
    # Verbs that are trivial enough that the *actor itself* need not remember
    # doing them (you don't form a memory of glancing around or idling).
    _TRIVIAL_SELF_VERBS = {"wait", "look"}

    def dispatch(self, event: Event, world: WorldState) -> list[Observation]:
        """Generate Observations for all eligible observers of an Event.

        Observers who do not perceive the event are recorded as
        ``ObservationSkip`` entries (accessible via ``get_skips()``).
        """
        # A pure idle 'wait' produces no observations for anyone (P1.3).
        verb = event.canonical_facts.get("verb", "") if event.canonical_facts else ""
        if verb in self._NOISE_VERBS:
            return []

        observations: list[Observation] = []
        observers = self._find_potential_observers(event, world)

        # An actor does not form a memory of its own trivial action (e.g.
        # 'npc.guard_b look') — that is noise, not a memory worth keeping (P1.3).
        if verb in self._TRIVIAL_SELF_VERBS:
            observers.discard(event.actor_id)

        for observer_id in observers:
            perception = self._build_perception(event, observer_id, world)
            if perception is None:
                self._record_skip(event, observer_id, world)
                continue

            self._obs_seq += 1
            observations.append(
                Observation(
                    observation_id=f"obs_{event.tick}_{self._obs_seq}",
                    observer_id=observer_id,
                    source_event_id=event.event_id,
                    tick=event.tick,
                    perception=perception,
                    interpretation=None,  # Phase 3
                    detail_level=self._calculate_detail_level(perception),
                )
            )

        return observations

    def _record_skip(
        self, event: Event, observer_id: str, world: WorldState
    ) -> None:
        """Record why an observer did not perceive an event."""
        observer = world.get_entity(observer_id)
        reason = "out_of_range"
        if observer is not None:
            same_location = observer.location_id == event.location_id
            adjacent = self._is_adjacent(observer.location_id, event.location_id, world)
            if same_location or adjacent:
                reason = "insufficient_perception"
        self._skips.append(
            ObservationSkip(
                observer_id=observer_id,
                source_event_id=event.event_id,
                reason=reason,
                tick=event.tick,
            )
        )

    def get_skips(self) -> list[ObservationSkip]:
        """Return all skip records accumulated since the last clear."""
        return list(self._skips)

    def clear(self) -> None:
        """Reset skip records and observation sequence counters."""
        self._skips.clear()
        self._obs_seq = 0

    def _find_potential_observers(self, event: Event, world: WorldState) -> set[str]:
        """Find all entities that could potentially observe this event."""
        observers: set[str] = set()

        # Always include actor and target
        observers.add(event.actor_id)
        if event.target_id:
            observers.add(event.target_id)

        # Include all entities in the same location
        for entity_id, entity in world.entities.items():
            if entity.location_id == event.location_id:
                observers.add(entity_id)

        # Include adjacent locations via connections (noise/visual leak)
        event_loc = world.locations.get(event.location_id)
        if event_loc:
            for conn in event_loc.connections:
                adjacent_loc = world.locations.get(conn.to_location)
                if adjacent_loc:
                    for entity_id, entity in world.entities.items():
                        if entity.location_id == conn.to_location:
                            observers.add(entity_id)

        return observers

    def _build_perception(
        self, event: Event, observer_id: str, world: WorldState
    ) -> Perception | None:
        """Build Perception for a single observer, or None if not perceivable."""
        observer = world.get_entity(observer_id)
        if observer is None:
            return None

        # Actor/target always perceive fully
        is_direct_participant = observer_id in (event.actor_id, event.target_id or "")

        same_zone = observer.zone_id == event.zone_id
        same_location = observer.location_id == event.location_id
        adjacent = self._is_adjacent(observer.location_id, event.location_id, world)
        connection = self._get_connection(event.location_id, observer.location_id, world)

        if not same_location and not is_direct_participant and not adjacent:
            return None

        # Determine channels and detail based on event type + position
        channels: list[str] = []
        saw: str | None = None
        heard_keywords: list[str] = []
        heard_full_content = False
        distance: str = "near" if same_zone else "far"
        attention_level = "focused" if is_direct_participant else "distracted"

        # Get zone visibility/noise if relevant
        zone = world.get_zone(event.location_id, event.zone_id) if event.zone_id else None
        visibility = zone.visibility if zone else "medium"
        noise_level = zone.noise_level if zone else "moderate"

        # Adjacent location modifiers
        visual_leak = connection.visual_leak if connection else 0.0
        noise_leak = connection.noise_leak if connection else 0.0

        # ---- Speech ----
        if event.event_type == EventType.SPEECH:
            volume = event.canonical_facts.get("volume", "normal")

            # Sight: can see the speaker if same zone or high visibility
            if same_zone or (visibility == "high" and same_location):
                channels.append("sight")
                saw = event.summary
            elif adjacent and visual_leak > 0.2:
                channels.append("sight")
                saw = event.summary
                distance = "far"

            # Hearing: depends on volume + zone + adjacency
            if is_direct_participant:
                channels.append("hearing")
                heard_full_content = True
            elif same_zone:
                channels.append("hearing")
                if volume == "low":
                    heard_keywords = self._extract_keywords(
                        event.canonical_facts.get("content", "")
                    )
                    heard_full_content = False
                elif volume in ("normal", "loud"):
                    heard_full_content = True
            elif volume == "loud" and same_location:
                channels.append("hearing")
                heard_full_content = True
                distance = "far"
            elif adjacent:
                if volume == "loud" and noise_leak > 0.2:
                    channels.append("hearing")
                    heard_full_content = True
                    distance = "far"
                elif noise_leak > 0.5:
                    channels.append("hearing")
                    heard_keywords = self._extract_keywords(
                        event.canonical_facts.get("content", "")
                    )
                    distance = "far"

        # ---- Movement ----
        elif event.event_type == EventType.MOVEMENT:
            if same_zone or (visibility != "low" and same_location):
                channels.append("sight")
                saw = event.summary
            elif adjacent and visual_leak > 0.1:
                channels.append("sight")
                saw = event.summary
                distance = "far"

        # ---- Physical ----
        elif event.event_type == EventType.PHYSICAL:
            verb = event.canonical_facts.get("verb", "")
            if same_zone or (visibility != "low" and same_location):
                channels.append("sight")
                saw = event.summary
            elif adjacent and visual_leak > 0.15:
                channels.append("sight")
                saw = event.summary
                distance = "far"
            # Some physical actions make noise
            if verb in ("attack", "break", "throw") or same_zone:
                channels.append("hearing")
            elif adjacent and noise_leak > 0.3 and verb in ("attack", "break", "throw"):
                channels.append("hearing")
                distance = "far"

        # ---- Combat ----
        elif event.event_type == EventType.COMBAT:
            if same_zone or (visibility != "low" and same_location):
                channels.append("sight")
                saw = event.summary
            elif adjacent and visual_leak > 0.1:
                channels.append("sight")
                saw = event.summary
                distance = "far"
            channels.append("hearing")
            if adjacent:
                distance = "far"

        # ---- Social ----
        elif event.event_type == EventType.SOCIAL:
            if same_zone or (visibility != "low" and same_location):
                channels.append("sight")
                saw = event.summary
            elif adjacent and visual_leak > 0.2:
                channels.append("sight")
                saw = event.summary
                distance = "far"
            if same_zone:
                channels.append("hearing")
            elif adjacent and noise_leak > 0.3:
                channels.append("hearing")
                distance = "far"

        # If no channels, observer didn't perceive anything
        if not channels:
            return None

        return Perception(
            channels=channels,  # type: ignore[arg-type]
            saw=saw,
            heard_keywords=heard_keywords,
            heard_full_content=heard_full_content,
            distance=distance,  # type: ignore[arg-type]
            attention_level=attention_level,  # type: ignore[arg-type]
        )

    @staticmethod
    def _is_adjacent(
        from_loc: str | None, to_loc: str | None, world: WorldState
    ) -> bool:
        if not from_loc or not to_loc:
            return False
        if from_loc == to_loc:
            return False
        loc = world.locations.get(from_loc)
        if loc is None:
            return False
        return any(c.to_location == to_loc for c in loc.connections)

    @staticmethod
    def _get_connection(
        from_loc: str | None, to_loc: str | None, world: WorldState
    ) -> Any:
        if not from_loc or not to_loc:
            return None
        loc = world.locations.get(from_loc)
        if loc is None:
            return None
        for c in loc.connections:
            if c.to_location == to_loc:
                return c
        return None

    def _calculate_detail_level(self, perception: Perception) -> str:
        """Calculate detail level based on perception quality."""
        if perception.heard_full_content and perception.saw:
            return "full"
        if perception.saw or perception.heard_keywords:
            return "partial"
        return "minimal"

    def _extract_keywords(self, content: str) -> list[str]:
        """Extract salient keywords from speech content."""
        if not content:
            return []
        # Simple keyword extraction: nouns and verbs (Chinese heuristic)
        # In MVP, just split by common separators and filter short words
        words = content.replace("，", " ").replace("。", " ").replace("！", " ").split()
        return [w for w in words if len(w) >= 2][:3]  # max 3 keywords
