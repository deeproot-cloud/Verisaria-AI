"""Content Pack Editor: validate, export, import.

Zero-dependency utilities for content pack management.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from verisaria.engine.campaign_loader import CampaignLoader, ValidationResult
from verisaria.engine.schemas import ContentPack
from verisaria.engine.world import WorldState


class PackEditor:
    """Edit, validate, and transform content packs."""

    # ------------------------------------------------------------------
    # Validate
    # ------------------------------------------------------------------

    @staticmethod
    def validate_pack(path: str | Path) -> ValidationResult:
        """Load and validate a content pack from file or directory."""
        path = Path(path)
        if path.is_dir():
            pack = CampaignLoader.load_from_directory(path)
        else:
            pack = CampaignLoader.load_from_file(path)
        return CampaignLoader.validate(pack)

    @staticmethod
    def format_validation(result: ValidationResult) -> str:
        """Format a ValidationResult into a human-friendly report."""
        if result.valid and not result.issues:
            return "✓ Content pack is valid. No issues found."

        lines: list[str] = []
        errors = [i for i in result.issues if i.severity == "error"]
        warnings = [i for i in result.issues if i.severity == "warning"]
        infos = [i for i in result.issues if i.severity == "info"]

        lines.append(f"Validation Result: {len(errors)} errors, {len(warnings)} warnings, {len(infos)} infos")
        lines.append("=" * 50)

        for issue in errors:
            path = f" [{issue.path}]" if issue.path else ""
            lines.append(f"  [ERROR]   {issue.rule}{path}: {issue.message}")
        for issue in warnings:
            path = f" [{issue.path}]" if issue.path else ""
            lines.append(f"  [WARN]    {issue.rule}{path}: {issue.message}")
        for issue in infos:
            path = f" [{issue.path}]" if issue.path else ""
            lines.append(f"  [INFO]    {issue.rule}{path}: {issue.message}")

        if errors:
            lines.append("\nFix errors before using this pack.")
        elif warnings:
            lines.append("\nPack usable, but review warnings.")
        else:
            lines.append("\nPack valid.")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    @staticmethod
    def export_from_world(world_state: WorldState, content_pack_id: str = "exported") -> dict[str, Any]:
        """Generate a content pack template from a live WorldState."""
        pack: dict[str, Any] = {
            "content_pack_id": content_pack_id,
            "schema_version": "2.0",
            "world_premise": {"era": "unknown", "tone": "neutral", "central_tension": "none"},
            "world_book": [],
            "starting_location": "",
            "initial_entities": [],
            "initial_relationships": [],
            "initial_conversations": [],
            "initial_locations": [],
            "player_agenda_template": {},
            "style_guide": {},
            "constraints": {},
            "campaign_drivers": [],
            "rule_presets": {},
        }

        # Locations
        locs = []
        for lid, loc in world_state.locations.items():
            loc_data: dict[str, Any] = {
                "location_id": lid,
                "zones": {},
                "connected_locations": loc.connected_locations,
            }
            for zid, zone in loc.zones.items():
                loc_data["zones"][zid] = {
                    "zone_id": zid,
                    "visibility": zone.visibility,
                    "exposure": zone.exposure,
                    "noise_level": zone.noise_level,
                }
            locs.append(loc_data)
        pack["initial_locations"] = locs

        # Entities
        entities = []
        for eid, ent in world_state.entities.items():
            ent_data: dict[str, Any] = {
                "entity_id": ent.entity_id,
                "entity_type": ent.entity_type,
                "location_id": ent.location_id,
                "zone_id": ent.zone_id,
                "attributes": ent.attributes,
                "traits": ent.traits,
                "inventory": ent.inventory,
                "hp": ent.hp,
                "max_hp": ent.max_hp,
            }
            entities.append(ent_data)
        pack["initial_entities"] = entities

        # Infer starting_location from player entity
        for ent in entities:
            if ent.get("entity_type") == "player":
                pack["starting_location"] = ent.get("location_id", "")
                break
        if not pack["starting_location"] and locs:
            pack["starting_location"] = locs[0]["location_id"]

        return pack

    @staticmethod
    def save_pack(pack: dict[str, Any], path: str | Path) -> str:
        """Save a pack dict to a JSON file. Returns the saved path."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(pack, f, ensure_ascii=False, indent=2)
        return str(path)

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    @staticmethod
    def import_entities_from_csv(csv_path: str | Path) -> list[dict[str, Any]]:
        """Read entities from a CSV file.

        Expected columns:
        entity_id, entity_type, location_id, zone_id, traits, hp, max_hp
        Optional: attributes (JSON string)
        """
        entities: list[dict[str, Any]] = []
        with open(csv_path, "r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ent: dict[str, Any] = {
                    "entity_id": row.get("entity_id", "").strip(),
                    "entity_type": row.get("entity_type", "npc").strip(),
                    "location_id": row.get("location_id", "").strip(),
                }
                zone = row.get("zone_id", "").strip()
                if zone:
                    ent["zone_id"] = zone
                traits = row.get("traits", "").strip()
                if traits:
                    ent["traits"] = [t.strip() for t in traits.split(",") if t.strip()]
                hp = row.get("hp", "").strip()
                if hp:
                    try:
                        ent["hp"] = int(hp)
                    except ValueError:
                        pass
                max_hp = row.get("max_hp", "").strip()
                if max_hp:
                    try:
                        ent["max_hp"] = int(max_hp)
                    except ValueError:
                        pass
                attrs = row.get("attributes", "").strip()
                if attrs:
                    try:
                        ent["attributes"] = json.loads(attrs)
                    except json.JSONDecodeError:
                        pass
                entities.append(ent)
        return entities
