"""Tests for PackEditor (validate, export, import)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verisaria.engine.pack_editor import PackEditor
from verisaria.engine.world import EntityState, LocationState, WorldState, ZoneState


class TestValidate:
    def test_validate_valid_pack(self):
        result = PackEditor.validate_pack("fixtures/content_packs/valid_frontier_town.json")
        # frontier_town has some warnings but should be structurally valid
        assert result is not None

    def test_validate_minimal_pack(self):
        result = PackEditor.validate_pack("fixtures/content_packs/minimal_valid.json")
        assert result is not None

    def test_validate_nonexistent(self):
        with pytest.raises(FileNotFoundError):
            PackEditor.validate_pack("nonexistent.json")

    def test_format_validation_no_issues(self):
        from verisaria.engine.campaign_loader import ValidationResult
        result = ValidationResult(valid=True)
        text = PackEditor.format_validation(result)
        assert "valid" in text.lower()

    def test_format_validation_with_issues(self):
        from verisaria.engine.campaign_loader import ValidationResult, ValidationIssue
        result = ValidationResult(valid=False)
        result.issues.append(ValidationIssue("error", "test", "something wrong", "path.here"))
        result.issues.append(ValidationIssue("warning", "test2", "be careful", None))
        text = PackEditor.format_validation(result)
        assert "ERROR" in text
        assert "WARN" in text
        assert "something wrong" in text
        assert "be careful" in text


class TestExport:
    def test_export_from_empty_world(self):
        world = WorldState(tick=0)
        pack = PackEditor.export_from_world(world, content_pack_id="test")
        assert pack["content_pack_id"] == "test"
        assert pack["schema_version"] == "2.0"

    def test_export_entities(self):
        world = WorldState(tick=5)
        world.entities["player_001"] = EntityState(
            entity_id="player_001",
            entity_type="player",
            location_id="tavern",
            zone_id="main_hall",
            hp=80,
            max_hp=100,
            traits=["brave"],
        )
        world.entities["npc_001"] = EntityState(
            entity_id="npc_001",
            entity_type="npc",
            location_id="tavern",
            hp=100,
        )
        pack = PackEditor.export_from_world(world)
        assert len(pack["initial_entities"]) == 2
        player = pack["initial_entities"][0]
        assert player["entity_type"] == "player"
        assert pack["starting_location"] == "tavern"

    def test_export_locations(self):
        world = WorldState(tick=0)
        world.locations["tavern"] = LocationState(
            location_id="tavern",
            zones={"main_hall": ZoneState(zone_id="main_hall", location_id="tavern")},
        )
        pack = PackEditor.export_from_world(world)
        assert len(pack["initial_locations"]) == 1
        assert pack["initial_locations"][0]["location_id"] == "tavern"

    def test_save_pack(self, tmp_path):
        pack = {"content_pack_id": "test", "schema_version": "2.0"}
        path = PackEditor.save_pack(pack, tmp_path / "test.json")
        assert Path(path).exists()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert data["content_pack_id"] == "test"


@pytest.fixture
def frontier_town_path():
    return "fixtures/content_packs/valid_frontier_town.json"


class TestImport:
    def test_import_entities_from_csv(self, tmp_path):
        csv_path = tmp_path / "npcs.csv"
        import csv as csv_mod
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv_mod.DictWriter(f, fieldnames=["entity_id", "entity_type", "location_id", "zone_id", "traits", "hp", "max_hp"])
            writer.writeheader()
            writer.writerow({
                "entity_id": "npc.a",
                "entity_type": "npc",
                "location_id": "tavern",
                "zone_id": "main_hall",
                "traits": "kind,perceptive",
                "hp": "80",
                "max_hp": "100",
            })
            writer.writerow({
                "entity_id": "npc.b",
                "entity_type": "npc",
                "location_id": "street",
                "zone_id": "",
                "traits": "brave",
                "hp": "100",
                "max_hp": "100",
            })
        entities = PackEditor.import_entities_from_csv(csv_path)
        assert len(entities) == 2
        assert entities[0]["entity_id"] == "npc.a"
        assert entities[0]["traits"] == ["kind", "perceptive"]
        assert entities[1].get("zone_id") in (None, "")

    def test_import_empty_csv(self, tmp_path):
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("entity_id,entity_type,location_id\n", encoding="utf-8")
        entities = PackEditor.import_entities_from_csv(csv_path)
        assert entities == []

    def test_import_with_attributes_json(self, tmp_path):
        csv_path = tmp_path / "npcs.csv"
        csv_path.write_text(
            'entity_id,entity_type,location_id,attributes\n'
            'npc.c,npc,tavern,"{""strength"": 0.8}"\n',
            encoding="utf-8",
        )
        entities = PackEditor.import_entities_from_csv(csv_path)
        assert len(entities) == 1
        assert entities[0]["attributes"] == {"strength": 0.8}


class TestPackCommandIntegration:
    def test_pack_validate_subcommand(self, frontier_town_path, tmp_path):
        from verisaria.runtime.session import GameSession
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        result = session._handle_pack_command(f"validate {frontier_town_path}")
        assert "valid" in result.lower() or "Validation Result:" in result

    def test_pack_export_subcommand(self, frontier_town_path, tmp_path):
        from verisaria.runtime.session import GameSession
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        export_path = tmp_path / "export.json"
        result = session._handle_pack_command(f"export {export_path}")
        assert "Exported to:" in result
        assert Path(export_path).exists()

    def test_pack_import_subcommand(self, frontier_town_path, tmp_path):
        from verisaria.runtime.session import GameSession
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        csv_path = tmp_path / "npcs.csv"
        csv_path.write_text(
            "entity_id,entity_type,location_id\n"
            "npc.imported,npc,tavern\n",
            encoding="utf-8",
        )
        result = session._handle_pack_command(f"import {csv_path}")
        assert "Imported 1" in result
        assert "npc.imported" in session.world.state.entities

    def test_pack_no_subcommand(self, frontier_town_path, tmp_path):
        from verisaria.runtime.session import GameSession
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        result = session._handle_pack_command("")
        assert "Pack commands:" in result

    def test_pack_validate_missing_arg(self, frontier_town_path, tmp_path):
        from verisaria.runtime.session import GameSession
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        result = session._handle_pack_command("validate")
        assert "Usage" in result
