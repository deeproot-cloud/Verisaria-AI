"""Persistence Layer: save/load world state with hash verification.

Phase-7 minimal version: single-file JSON saves, no SQLite.
All state is serialised to `saves/{save_id}.json`.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from verisaria.engine.schemas import Event, SaveData
from verisaria.engine.world import Connection, EntityState, LocationState, WorldCore, WorldState, ZoneState


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _world_state_to_dict(state: WorldState) -> dict[str, Any]:
    return {
        "tick": state.tick,
        "entities": {
            eid: dataclasses.asdict(ent)
            for eid, ent in state.entities.items()
        },
        "locations": {
            lid: {
                "location_id": loc.location_id,
                "zones": {
                    zid: dataclasses.asdict(zone)
                    for zid, zone in loc.zones.items()
                },
                "connected_locations": list(loc.connected_locations),
                "connections": [
                    dataclasses.asdict(c) for c in loc.connections
                ],
            }
            for lid, loc in state.locations.items()
        },
        "world_vars": dict(state.world_vars),
    }


def _world_state_from_dict(data: dict[str, Any]) -> WorldState:
    state = WorldState(tick=data.get("tick", 0))
    state.world_vars = dict(data.get("world_vars", {}))
    for eid, edata in data.get("entities", {}).items():
        state.entities[eid] = EntityState(**edata)
    for lid, ldata in data.get("locations", {}).items():
        zones = {}
        for zid, zdata in ldata.get("zones", {}).items():
            zones[zid] = ZoneState(**zdata)
        connections = [
            Connection(**c) for c in ldata.get("connections", [])
        ]
        state.locations[lid] = LocationState(
            location_id=lid,
            zones=zones,
            connected_locations=list(ldata.get("connected_locations", [])),
            connections=connections,
        )
    return state


def _events_to_dicts(events: list[Event]) -> list[dict[str, Any]]:
    return [e.model_dump() for e in events]


def _events_from_dicts(data: list[dict[str, Any]]) -> list[Event]:
    return [Event(**d) for d in data]


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------

def _compute_hash(data: dict[str, Any]) -> str:
    """Compute SHA-256 of a dict's canonical JSON representation."""
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _strip_hashes(save_dict: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of save dict with hash fields removed."""
    result = dict(save_dict)
    result.pop("snapshot_hash", None)
    result.pop("event_log_hash", None)
    return result


# ---------------------------------------------------------------------------
# Persistence Layer
# ---------------------------------------------------------------------------

class PersistenceLayer:
    """Save and load complete world snapshots as single JSON files."""

    def __init__(self, save_dir: str | Path = "saves") -> None:
        # The dir is created lazily on the first save (see ``save``), not here, so
        # constructing a session that never saves doesn't litter an empty save dir.
        self.save_dir = Path(save_dir)

    # -- Save --

    def save(
        self,
        world_core: WorldCore,
        tick: int,
        content_pack_id: str = "unknown",
        content_pack_version: str = "1.0.0",
        save_type: str = "manual",
        scheduler_state: dict[str, Any] | None = None,
        agenda_state: dict[str, Any] | None = None,
        campaign_state: dict[str, Any] | None = None,
        memory_state: dict[str, Any] | None = None,
        belief_state: dict[str, Any] | None = None,
        combat_state: dict[str, Any] | None = None,
        conversation_state: dict[str, Any] | None = None,
        npc_runtime_state: dict[str, Any] | None = None,
        rng_seed: int = 42,
        rng_consumed: int = 0,
        llm_fixture_version: str = "unknown",
    ) -> SaveData:
        """Create a full snapshot save."""
        save_id = f"save_{tick}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

        events = world_core.event_log.get_events(since_tick=0)
        events_data = _events_to_dicts(events)

        world_data = _world_state_to_dict(world_core.state)
        world_data["events"] = events_data
        world_data["events_cursor"] = len(events)

        subjectivity_data: dict[str, Any] = {}
        if memory_state:
            subjectivity_data["memories"] = memory_state
        if belief_state:
            subjectivity_data["beliefs"] = belief_state

        player_data: dict[str, Any] = {}
        if agenda_state:
            player_data["agenda"] = agenda_state

        save_dict: dict[str, Any] = {
            "save_id": save_id,
            "save_type": save_type,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "tick": tick,
            "rng_state": f"seed:{rng_seed},consumed:{rng_consumed}",
            "llm_fixture_version": llm_fixture_version,
            "content_pack_id": content_pack_id,
            "content_pack_version": content_pack_version,
            "world_state": world_data,
            "subjectivity_state": subjectivity_data,
            "player_state": player_data,
            "scheduler_state": scheduler_state or {},
            "combat_state": combat_state or {},
            "conversation_state": conversation_state or {},
            "npc_runtime_state": npc_runtime_state or {},
            "llm_budget_state": {},
        }

        # Compute hashes
        event_log_hash = _compute_hash({"events": events_data})
        snapshot_hash = _compute_hash(_strip_hashes(save_dict))

        save_dict["snapshot_hash"] = f"sha256:{snapshot_hash}"
        save_dict["event_log_hash"] = f"sha256:{event_log_hash}"

        save_data = SaveData(**save_dict)

        # Write to disk (create the save dir lazily, on first actual save)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        path = self.save_dir / f"{save_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(save_dict, f, ensure_ascii=False, indent=2)

        return save_data

    # -- Load --

    def load(self, save_id: str) -> SaveData:
        """Load a save from disk."""
        path = self.save_dir / f"{save_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Save not found: {save_id}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return SaveData(**data)

    def restore_world_core(self, save_data: SaveData) -> WorldCore:
        """Rebuild a WorldCore from save data.

        Returns a new WorldCore with restored state and events.
        """
        world_data = save_data.world_state
        state = _world_state_from_dict(world_data)
        core = WorldCore(initial_state=state)

        # Restore events
        for edata in world_data.get("events", []):
            event = Event(**edata)
            core.event_log.append(event)

        # Restore action seq from last event
        if core.event_log._events:
            last_event = core.event_log._events[-1]
            # event_id format: evt_{tick}_{seq}
            parts = last_event.event_id.split("_")
            if len(parts) == 3:
                core._action_seq = int(parts[2])

        return core

    # -- Verification --

    def verify(self, save_data: SaveData) -> tuple[bool, list[str]]:
        """Verify a save's integrity.

        Returns (is_valid, list_of_issues).
        """
        issues: list[str] = []
        save_dict = save_data.model_dump()

        # Verify snapshot hash
        stored_snapshot = save_data.snapshot_hash or ""
        computed_snapshot = "sha256:" + _compute_hash(_strip_hashes(save_dict))
        if stored_snapshot != computed_snapshot:
            issues.append(
                f"snapshot_hash mismatch: stored={stored_snapshot}, computed={computed_snapshot}"
            )

        # Verify event log hash
        stored_event_hash = save_data.event_log_hash or ""
        events = save_data.world_state.get("events", [])
        computed_event_hash = "sha256:" + _compute_hash({"events": events})
        if stored_event_hash != computed_event_hash:
            issues.append(
                f"event_log_hash mismatch: stored={stored_event_hash}, computed={computed_event_hash}"
            )

        return len(issues) == 0, issues

    # -- Listing / deletion --

    def list_saves(self) -> list[dict[str, Any]]:
        """List all save files with metadata."""
        saves: list[dict[str, Any]] = []
        for path in sorted(self.save_dir.glob("save_*.json")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                saves.append({
                    "save_id": data.get("save_id", path.stem),
                    "save_type": data.get("save_type", "unknown"),
                    "tick": data.get("tick", 0),
                    "created_at": data.get("created_at", ""),
                    "content_pack_id": data.get("content_pack_id", "unknown"),
                })
            except Exception:
                continue
        return saves

    def delete_save(self, save_id: str) -> bool:
        """Delete a save file."""
        path = self.save_dir / f"{save_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def prune_auto_saves(self, max_auto_saves: int = 10) -> int:
        """Delete oldest auto-saves if over limit.

        Returns number deleted.
        """
        auto_saves = [
            s for s in self.list_saves() if s.get("save_type") == "auto"
        ]
        if len(auto_saves) <= max_auto_saves:
            return 0

        # Sort by created_at ascending
        auto_saves.sort(key=lambda s: s.get("created_at", ""))
        to_delete = auto_saves[:-max_auto_saves]
        deleted = 0
        for s in to_delete:
            if self.delete_save(s["save_id"]):
                deleted += 1
        return deleted
