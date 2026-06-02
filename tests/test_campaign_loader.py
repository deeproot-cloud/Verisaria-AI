"""Tests for Campaign Loader: load, validate, build world state, fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from verisaria.engine.campaign_loader import CampaignLoader, ValidationResult
from verisaria.engine.schemas import ContentPack


FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "content_packs"


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

class TestLoad:
    def test_load_valid_pack_from_file(self) -> None:
        path = FIXTURE_DIR / "valid_frontier_town.json"
        pack = CampaignLoader.load_from_file(path)
        assert pack.content_pack_id == "frontier_town"
        assert pack.schema_version == "2.0"
        assert len(pack.initial_entities) == 3
        assert len(pack.world_book) == 2

    def test_load_minimal_pack(self) -> None:
        path = FIXTURE_DIR / "minimal_valid.json"
        pack = CampaignLoader.load_from_file(path)
        assert pack.content_pack_id == "minimal"
        assert len(pack.initial_entities) == 1

    def test_load_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError):
            CampaignLoader.load_from_file(FIXTURE_DIR / "nonexistent.json")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidate:
    def test_valid_pack_passes(self) -> None:
        pack = CampaignLoader.load_from_file(FIXTURE_DIR / "valid_frontier_town.json")
        result = CampaignLoader.validate(pack)
        assert result.valid is True
        assert not any(i.severity == "error" for i in result.issues)

    def test_invalid_schema_version(self) -> None:
        pack = CampaignLoader.load_from_file(FIXTURE_DIR / "invalid_schema_version.json")
        result = CampaignLoader.validate(pack)
        assert result.valid is False
        assert any(i.rule == "schema_version" for i in result.issues)

    def test_broken_entity_reference(self) -> None:
        pack = CampaignLoader.load_from_file(FIXTURE_DIR / "broken_refs.json")
        result = CampaignLoader.validate(pack)
        assert result.valid is False
        assert any(
            i.rule == "entity_reference" and "npc.missing" in i.message
            for i in result.issues
        )

    def test_world_book_duplicate_entry_id(self) -> None:
        pack = CampaignLoader.load_from_file(FIXTURE_DIR / "world_book_conflict.json")
        result = CampaignLoader.validate(pack)
        assert result.valid is False
        assert any(
            i.rule == "world_book_conflict" and "dup_entry" in i.message
            for i in result.issues
        )

    def test_minimal_pack_passes(self) -> None:
        pack = CampaignLoader.load_from_file(FIXTURE_DIR / "minimal_valid.json")
        result = CampaignLoader.validate(pack)
        assert result.valid is True


# ---------------------------------------------------------------------------
# Build World State
# ---------------------------------------------------------------------------

class TestBuildWorldState:
    def test_build_from_valid_pack(self) -> None:
        pack = CampaignLoader.load_from_file(FIXTURE_DIR / "valid_frontier_town.json")
        state = CampaignLoader.build_world_state(pack)

        assert "player_001" in state.entities
        assert "npc.guard_b" in state.entities
        assert "npc.ele" in state.entities

        # Location consistency
        assert state.entities["player_001"].location_id == "town_square"
        assert state.entities["npc.ele"].location_id == "tavern"

        # Zones created
        assert "town_square" in state.locations
        assert "tavern" in state.locations

    def test_entity_registered_in_zone_occupants(self) -> None:
        pack = CampaignLoader.load_from_file(FIXTURE_DIR / "valid_frontier_town.json")
        state = CampaignLoader.build_world_state(pack)

        town_square = state.locations["town_square"]
        assert "player_001" in town_square.zones["center"].occupant_ids
        assert "npc.guard_b" in town_square.zones["center"].occupant_ids

    def test_build_from_minimal_pack(self) -> None:
        pack = CampaignLoader.load_from_file(FIXTURE_DIR / "minimal_valid.json")
        state = CampaignLoader.build_world_state(pack)
        assert "player_001" in state.entities
        assert state.entities["player_001"].location_id == "void"


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------

class TestFallback:
    def test_minimal_fallback_structure(self) -> None:
        pack, state = CampaignLoader.get_minimal_fallback()
        assert pack.content_pack_id == "minimal_fallback"
        assert pack.schema_version == "2.0"
        assert "player_001" in state.entities

    def test_load_or_fallback_success(self) -> None:
        path = FIXTURE_DIR / "valid_frontier_town.json"
        pack, state, result = CampaignLoader.load_or_fallback(path)
        assert result.valid is True
        assert pack.content_pack_id == "frontier_town"
        assert len(state.entities) == 3

    def test_load_or_fallback_on_missing_file(self) -> None:
        path = FIXTURE_DIR / "nonexistent.json"
        pack, state, result = CampaignLoader.load_or_fallback(path)
        assert result.valid is False
        assert pack.content_pack_id == "minimal_fallback"
        assert "player_001" in state.entities


# ---------------------------------------------------------------------------
# Integration: end-to-end load → validate → build
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_full_pipeline_valid_pack(self) -> None:
        path = FIXTURE_DIR / "valid_frontier_town.json"
        pack, state, result = CampaignLoader.load_or_fallback(path)

        assert result.valid
        assert state.tick == 0
        assert len(state.entities) == 3
        assert len(state.locations) >= 2

        # Guard has correct attributes
        guard = state.entities["npc.guard_b"]
        assert guard.attributes.get("strength") == 0.7
        assert "anxious" in guard.traits

    def test_full_pipeline_broken_pack_uses_fallback(self) -> None:
        # A pack with wrong schema version loads but fails validation;
        # load_or_fallback still returns the loaded pack (not fallback) because
        # file parsing succeeded.
        path = FIXTURE_DIR / "invalid_schema_version.json"
        pack, state, result = CampaignLoader.load_or_fallback(path)
        assert result.valid is False
        assert pack.schema_version == "1.0"
        assert len(state.entities) == 0  # Original pack has no entities
