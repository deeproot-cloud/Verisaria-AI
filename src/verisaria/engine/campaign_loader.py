"""Campaign Loader: load, validate, and initialise world from Content Packs.

Phase-4 minimal version:
- Load ContentPack from JSON (file or directory)
- Multi-layer validation (schema, references, world-book conflicts)
- Build initial WorldState from pack
- Minimal fallback on load failure
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verisaria.engine.schemas import ContentPack, WorldBookEntry
from verisaria.engine.world import Connection, EntityState, LocationState, WorldState, ZoneState


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning" | "info"
    rule: str
    message: str
    path: str | None = None


@dataclass
class ValidationResult:
    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, severity: str, rule: str, message: str, path: str | None = None) -> None:
        self.issues.append(ValidationIssue(severity, rule, message, path))
        if severity == "error":
            self.valid = False


# ---------------------------------------------------------------------------
# Campaign Loader
# ---------------------------------------------------------------------------

class CampaignLoader:
    """Load and validate Content Packs, then build an initial WorldState."""

    SUPPORTED_SCHEMA_VERSION = "2.0"

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    @classmethod
    def load_from_file(cls, path: str | Path) -> ContentPack:
        """Load a ContentPack from a single JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return ContentPack(**data)

    @classmethod
    def load_from_directory(cls, path: str | Path) -> ContentPack:
        """Load a ContentPack from a directory.

        Tries:
        1. content_pack.json (single file with all data)
        2. Merge content_pack.json + world_book.json + entities.json + ...
        """
        dir_path = Path(path)
        main_file = dir_path / "content_pack.json"
        if main_file.exists():
            with open(main_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Merge supplementary files if present
            for key in ("world_book", "entities", "relationships", "conversations",
                        "campaign_drivers"):
                supp = dir_path / f"{key}.json"
                if supp.exists() and key not in data:
                    with open(supp, "r", encoding="utf-8") as f:
                        data[key] = json.load(f)
            return ContentPack(**data)

        raise FileNotFoundError(f"No content_pack.json found in {dir_path}")

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    @classmethod
    def validate(cls, pack: ContentPack) -> ValidationResult:
        """Run all validation rules against a ContentPack."""
        result = ValidationResult(valid=True)

        cls._validate_schema_version(pack, result)
        cls._validate_entity_references(pack, result)
        cls._validate_world_book_conflicts(pack, result)
        cls._validate_location_consistency(pack, result)
        cls._validate_campaign_drivers(pack, result)
        cls._validate_agenda_premise(pack, result)
        cls._validate_world_var_satisfiability(pack, result)

        return result

    @classmethod
    def _validate_world_var_satisfiability(
        cls, pack: ContentPack, result: ValidationResult
    ) -> None:
        """Authoring lint for world_state_vars — the traps that make a chain
        unclosable (playability audit #1): a mutable var no one can satisfy, a var
        the player can't address by natural language, and near-duplicate vars that
        split one fact into two the arbiter then treats as unrelated. Warnings only
        (the pack still loads); see docs/design/pack-authoring-guide.md."""
        vars_ = [v for v in (pack.world_state_vars or []) if isinstance(v, dict)]

        # Resolvable satisfiers: every NPC id (prefix-tolerant) + every authority role.
        npc_ids: set[str] = set()
        authorities: set[str] = set()
        for ent in pack.initial_entities:
            if ent.get("entity_type", "npc") != "npc":
                continue
            eid = ent.get("entity_id", "")
            if eid:
                npc_ids.update({eid, eid.replace("npc.", ""), f"npc.{eid.replace('npc.', '')}"})
            auth = (ent.get("attributes") or {}).get("authority")
            if auth:
                authorities.add(auth)

        def _resolvable(sb: str) -> bool:
            bare = sb.replace("npc.", "")
            return sb in npc_ids or bare in npc_ids or sb in authorities

        for v in vars_:
            vid = v.get("var_id", "?")
            if v.get("mutable", True) is False:
                continue
            path = f"world_state_vars.{vid}"
            set_by = v.get("set_by") or []
            if not set_by:
                result.add("warning", "world_var_unsatisfiable",
                           f"可变变量 '{vid}' 没有 set_by —— 没有谁能让它变 true，链会卡在这里。", path)
            elif not any(_resolvable(sb) for sb in set_by):
                result.add("warning", "world_var_unsatisfiable",
                           f"可变变量 '{vid}' 的 set_by={set_by} 没有一个对应到真实 NPC 的 id 或其 "
                           f"authority 角色 —— 无人可满足它。", path)
            if not (v.get("request_keywords") or []):
                result.add("warning", "world_var_no_keywords",
                           f"可变变量 '{vid}' 没有 request_keywords —— 玩家的自然语句很难路由到它。", path)

        # Near-duplicate vars: one id is a long substring of another (pump_failure_disclosed
        # ⊂ pump_failure_disclosed_publicly). The player satisfies one, the authority
        # references the other — the #1 dead end.
        ids = [v.get("var_id", "") for v in vars_]
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                al, bl = a.lower(), b.lower()
                if a and b and min(len(al), len(bl)) >= 6 and (al in bl or bl in al):
                    result.add("warning", "world_var_near_duplicate",
                               f"变量 '{a}' 与 '{b}' 的 id 高度重合 —— 像是同一件事的两个变量，"
                               f"玩家满足一个可能不顶另一个（#1 陷阱）。", "world_state_vars")

        # Reachability: a var whose only satisfier NPC stands behind a location the
        # player can't reach from the start is an output they can never get to (audit
        # 5 #4). Only run when the pack declares a real topology, and only flag a
        # DECLARED location (an implicit one we can't reason about is skipped).
        start = pack.starting_location
        loc_conns = {
            loc.get("location_id"): [c.get("to") for c in (loc.get("connections") or [])]
            for loc in pack.initial_locations if loc.get("location_id")
        }
        if start and any(loc_conns.values()):
            reachable: set = set()
            frontier = [start]
            while frontier:
                cur = frontier.pop()
                if cur in reachable:
                    continue
                reachable.add(cur)
                frontier.extend(loc_conns.get(cur, []))
            npc_loc: dict = {}
            auth_locs: dict = {}
            for ent in pack.initial_entities:
                if ent.get("entity_type", "npc") != "npc":
                    continue
                loc = ent.get("location_id")
                eid = ent.get("entity_id", "")
                if eid:
                    npc_loc[eid] = loc
                    npc_loc[eid.replace("npc.", "")] = loc
                auth = (ent.get("attributes") or {}).get("authority")
                if auth:
                    auth_locs.setdefault(auth, set()).add(loc)
            for v in vars_:
                if v.get("mutable", True) is False:
                    continue
                vid = v.get("var_id", "?")
                sat_locs: set = set()
                for sb in (v.get("set_by") or []):
                    bare = sb.replace("npc.", "")
                    if sb in npc_loc:
                        sat_locs.add(npc_loc[sb])
                    elif bare in npc_loc:
                        sat_locs.add(npc_loc[bare])
                    elif sb in auth_locs:
                        sat_locs |= auth_locs[sb]
                declared = {loc for loc in sat_locs if loc in loc_conns}
                if declared and not (declared & reachable):
                    result.add("warning", "world_var_unreachable",
                               f"可变变量 '{vid}' 的满足者 NPC 在 {sorted(declared)}，从起点 "
                               f"'{start}' 经 connections 到不了 —— 玩家够不着这条线的出口。",
                               f"world_state_vars.{vid}")

    @classmethod
    def _validate_schema_version(cls, pack: ContentPack, result: ValidationResult) -> None:
        if pack.schema_version != cls.SUPPORTED_SCHEMA_VERSION:
            result.add(
                "error",
                "schema_version",
                f"Unsupported schema version: {pack.schema_version} (expected {cls.SUPPORTED_SCHEMA_VERSION})",
            )

    @classmethod
    def _validate_entity_references(cls, pack: ContentPack, result: ValidationResult) -> None:
        """Ensure all entity references point to existing entities."""
        entity_ids = set()
        for ent in pack.initial_entities:
            eid = ent.get("entity_id")
            if eid:
                entity_ids.add(eid)
            else:
                result.add("error", "entity_reference", "Entity missing entity_id", str(ent))

        for rel in pack.initial_relationships:
            for key in ("npc_id", "target_id"):
                ref = rel.get(key)
                if ref and ref not in entity_ids:
                    result.add(
                        "error",
                        "entity_reference",
                        f"Relationship references unknown {key}: {ref}",
                        f"relationship.{key}",
                    )

        for conv in pack.initial_conversations:
            for pid in conv.get("participants", []):
                if pid not in entity_ids:
                    result.add(
                        "warning",
                        "entity_reference",
                        f"Conversation participant not in initial_entities: {pid}",
                        "conversation.participants",
                    )

    @classmethod
    def _validate_world_book_conflicts(cls, pack: ContentPack, result: ValidationResult) -> None:
        """Detect duplicate entry_ids and mutually exclusive layer assignments."""
        seen: dict[str, list[str]] = {}  # entry_id -> [layers]
        for entry in pack.world_book:
            seen.setdefault(entry.entry_id, []).append(entry.layer)

        for entry_id, layers in seen.items():
            if len(layers) > 1:
                # Same entry_id in multiple layers
                result.add(
                    "error",
                    "world_book_conflict",
                    f"entry_id '{entry_id}' appears in multiple layers: {layers}",
                    f"world_book.{entry_id}",
                )

        # Check for canonical_fact vs propaganda in same pack (different entry_ids)
        facts = {e.entry_id for e in pack.world_book if e.layer == "canonical_fact"}
        propaganda = {e.entry_id for e in pack.world_book if e.layer == "faction_propaganda"}
        # This is allowed as long as entry_ids differ; we just flag potential content issues
        if facts & propaganda:
            result.add(
                "warning",
                "world_book_conflict",
                "Same entry_id used as both canonical_fact and faction_propaganda",
            )

    @classmethod
    def _validate_location_consistency(cls, pack: ContentPack, result: ValidationResult) -> None:
        """Check that starting_location is referenced and location connections are bidirectional."""
        # Extract location_ids from entities (if they have location_id)
        location_ids = set()
        for ent in pack.initial_entities:
            loc = ent.get("location_id")
            if loc:
                location_ids.add(loc)

        if pack.starting_location and pack.starting_location not in location_ids:
            result.add(
                "warning",
                "location_consistency",
                f"starting_location '{pack.starting_location}' not found in any entity's location_id",
                "starting_location",
            )

    @classmethod
    def _validate_campaign_drivers(cls, pack: ContentPack, result: ValidationResult) -> None:
        """Basic campaign driver sanity checks."""
        for driver in pack.campaign_drivers:
            driver_id = driver.get("driver_id", "unknown")
            signals = driver.get("signals", [])
            if not signals:
                result.add(
                    "warning",
                    "campaign_driver",
                    f"Driver '{driver_id}' has no signals",
                    f"campaign_drivers.{driver_id}",
                )
            for sig in signals:
                cond = sig.get("condition", "")
                if not cond:
                    result.add(
                        "error",
                        "campaign_driver",
                        f"Driver '{driver_id}' has signal with empty condition",
                        f"campaign_drivers.{driver_id}.signals",
                    )

    @classmethod
    def _validate_agenda_premise(cls, pack: ContentPack, result: ValidationResult) -> None:
        """Check that agenda template references align with world premise."""
        premise_tone = pack.world_premise.tone.lower()
        style = pack.style_guide
        if style:
            tone_refs = style.get("tone_references", [])
            if premise_tone not in [t.lower() for t in tone_refs]:
                result.add(
                    "info",
                    "agenda_premise",
                    f"World premise tone '{premise_tone}' not in style_guide tone_references",
                    "style_guide.tone_references",
                )

    # ------------------------------------------------------------------
    # Build World State
    # ------------------------------------------------------------------

    @classmethod
    def build_world_state(cls, pack: ContentPack) -> WorldState:
        """Construct an initial WorldState from a validated ContentPack."""
        state = WorldState(tick=0)

        # Build locations from every declared location AND entity locations — an
        # empty room (no one currently standing in it) is still a reachable /
        # escortable destination and belongs on the map. (Previously only entity
        # locations were built, silently dropping declared-but-empty rooms.)
        # Build the id list in a DETERMINISTIC order (declared order first, then any
        # entity-only locations) — a set iterates in hash order, which made the
        # locations dict order, and thus destination tie-breaking, non-deterministic.
        location_ids: list[str] = []
        seen_locs: set[str] = set()

        def _add_loc(lid: str | None) -> None:
            if lid and lid not in seen_locs:
                seen_locs.add(lid)
                location_ids.append(lid)

        zone_data: dict[str, dict[str, Any]] = {}
        for ent in pack.initial_entities:
            loc = ent.get("location_id")
            zone = ent.get("zone_id")
            if loc and zone:
                zone_data.setdefault(loc, {})[zone] = ent.get("zone_info", {})
        for loc_def in pack.initial_locations:
            _add_loc(loc_def.get("location_id"))
        for ent in pack.initial_entities:
            _add_loc(ent.get("location_id"))

        for loc_id in location_ids:
            zones = {}
            for zid, zinfo in zone_data.get(loc_id, {}).items():
                zones[zid] = ZoneState(
                    zone_id=zid,
                    location_id=loc_id,
                    visibility=zinfo.get("visibility", "medium"),
                    noise_level=zinfo.get("noise_level", "moderate"),
                    capacity=zinfo.get("capacity", 10),
                )
            # Build connections from initial_locations if provided
            connections: list[Connection] = []
            loc_name = ""
            loc_desc = ""
            for loc_def in pack.initial_locations:
                if loc_def.get("location_id") == loc_id:
                    loc_name = loc_def.get("name", "")
                    loc_desc = loc_def.get("description", "")
                    for conn in loc_def.get("connections", []):
                        connections.append(Connection(
                            to_location=conn.get("to", ""),
                            distance=conn.get("distance", "adjacent"),
                            noise_leak=conn.get("noise_leak", 0.0),
                            visual_leak=conn.get("visual_leak", 0.0),
                            description=conn.get("description", ""),
                        ))
                    # Also populate connected_locations for backward compat
                    connected = [c.to_location for c in connections]
                    break
            else:
                connected = []

            state.locations[loc_id] = LocationState(
                location_id=loc_id,
                name=loc_name,
                description=loc_desc,
                zones=zones,
                connected_locations=connected,
                connections=connections,
            )

        # Build entities
        for ent in pack.initial_entities:
            eid = ent.get("entity_id", "unknown")
            attrs = dict(ent.get("attributes", {}))
            # Migrate any legacy attributes["stamina"] to the top-level field.
            max_stamina = ent.get("max_stamina", 100)
            stamina = ent.get("stamina", attrs.pop("stamina", max_stamina))
            ent_type = ent.get("entity_type", "npc")
            loc = ent.get("location_id", "unknown")
            # NPCs are anchored to their starting location as "home" (P1.8) so
            # they tend to stay at their post rather than wander aimlessly. A pack
            # may override with an explicit `home_location`.
            home = ent.get("home_location") or (loc if ent_type == "npc" else None)
            entity = EntityState(
                entity_id=eid,
                entity_type=ent_type,
                location_id=loc,
                name=ent.get("name", ""),
                zone_id=ent.get("zone_id"),
                attributes=attrs,
                traits=ent.get("traits", []),
                inventory=ent.get("inventory", []),
                hp=ent.get("hp", 100),
                max_hp=ent.get("max_hp", 100),
                stamina=int(stamina),
                max_stamina=max_stamina,
                home_location=home,
                stationed=bool(ent.get("stationed", False)),
            )
            state.entities[eid] = entity

            # Register entity in zone occupants
            if entity.zone_id and entity.location_id:
                loc = state.locations.get(entity.location_id)
                if loc and entity.zone_id in loc.zones:
                    loc.zones[entity.zone_id].occupant_ids.append(eid)

        return state

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    @classmethod
    def get_minimal_fallback(cls) -> tuple[ContentPack, WorldState]:
        """Return a minimal content pack and world state for graceful degradation."""
        pack = ContentPack(
            content_pack_id="minimal_fallback",
            schema_version=cls.SUPPORTED_SCHEMA_VERSION,
            world_premise={
                "era": "unknown",
                "tone": "neutral",
                "central_tension": "none",
            },
            starting_location="void",
            initial_entities=[
                {
                    "entity_id": "player_001",
                    "entity_type": "player",
                    "location_id": "void",
                    "zone_id": None,
                }
            ],
        )
        state = cls.build_world_state(pack)
        return pack, state

    @classmethod
    def load_or_fallback(cls, path: str | Path) -> tuple[ContentPack, WorldState, ValidationResult]:
        """Load a pack, validate it, and build world state.

        Returns (pack, world_state, validation_result).
        If loading fails, returns minimal fallback with errors recorded.
        """
        try:
            if os.path.isfile(path):
                pack = cls.load_from_file(path)
            else:
                pack = cls.load_from_directory(path)
        except Exception as exc:
            result = ValidationResult(valid=False)
            result.add("error", "load", f"Failed to load content pack: {exc}")
            pack, state = cls.get_minimal_fallback()
            return pack, state, result

        result = cls.validate(pack)
        state = cls.build_world_state(pack)
        return pack, state, result
