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

    def test_declared_empty_location_is_loaded(self) -> None:
        """A location declared in initial_locations but with no entity standing in it
        must still be built (a reachable / escortable empty room), not dropped."""
        pack = ContentPack(
            content_pack_id="x", schema_version="1.0.0",
            world_premise={"era": "e", "tone": "t", "central_tension": "c"},
            starting_location="hall",
            initial_entities=[
                {"entity_id": "player_001", "entity_type": "player", "location_id": "hall"}],
            initial_locations=[
                {"location_id": "hall", "name": "大厅",
                 "connections": [{"to": "empty_room", "distance": "adjacent"}]},
                {"location_id": "empty_room", "name": "空房间", "connections": []},
            ],
        )
        state = CampaignLoader.build_world_state(pack)
        assert "empty_room" in state.locations          # not silently dropped
        assert state.locations["empty_room"].name == "空房间"

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


# ---------------------------------------------------------------------------
# world_state_vars authoring lint (playability audit #1 — pack-side traps)
# ---------------------------------------------------------------------------

def _pack_with_vars(vars_, entities=None):
    ents = [{"entity_id": "player_001", "entity_type": "player", "location_id": "hall"}]
    ents += entities or [
        {"entity_id": "npc.warden", "entity_type": "npc", "location_id": "hall",
         "attributes": {"authority": "gate_authority"}}]
    return ContentPack(
        content_pack_id="x", schema_version="2.0",
        world_premise={"era": "e", "tone": "t", "central_tension": "c"},
        starting_location="hall", initial_entities=ents,
        initial_locations=[{"location_id": "hall", "name": "大厅", "connections": []}],
        world_state_vars=vars_,
    )


def _rules(result, rule):
    return [i for i in result.issues if i.rule == rule]


class TestWorldVarLint:
    def test_clean_var_passes(self):
        pack = _pack_with_vars([{
            "var_id": "gate_open", "label": "门是否开", "mutable": True,
            "set_by": ["gate_authority"], "request_keywords": ["开门"]}])
        result = CampaignLoader.validate(pack)
        assert not _rules(result, "world_var_unsatisfiable")
        assert not _rules(result, "world_var_no_keywords")

    def test_set_by_resolvable_by_npc_id(self):
        pack = _pack_with_vars([{
            "var_id": "v", "label": "l", "mutable": True,
            "set_by": ["npc.warden"], "request_keywords": ["k"]}])
        assert not _rules(CampaignLoader.validate(pack), "world_var_unsatisfiable")

    def test_unsatisfiable_var_flagged(self):
        pack = _pack_with_vars([{
            "var_id": "v", "label": "l", "mutable": True,
            "set_by": ["npc.ghost"], "request_keywords": ["k"]}])  # ghost isn't an entity
        issues = _rules(CampaignLoader.validate(pack), "world_var_unsatisfiable")
        assert issues and "v" in issues[0].message

    def test_missing_keywords_flagged(self):
        pack = _pack_with_vars([{
            "var_id": "v", "label": "l", "mutable": True,
            "set_by": ["gate_authority"], "request_keywords": []}])
        assert _rules(CampaignLoader.validate(pack), "world_var_no_keywords")

    def test_near_duplicate_vars_flagged(self):
        pack = _pack_with_vars([
            {"var_id": "disclosed", "label": "公开", "mutable": True,
             "set_by": ["gate_authority"], "request_keywords": ["k"]},
            {"var_id": "disclosed_publicly", "label": "公开公示", "mutable": True,
             "set_by": ["gate_authority"], "request_keywords": ["k2"]}])
        assert _rules(CampaignLoader.validate(pack), "world_var_near_duplicate")

    def test_lint_warnings_do_not_fail_the_pack(self):
        pack = _pack_with_vars([{
            "var_id": "v", "label": "l", "mutable": True, "set_by": [], "request_keywords": []}])
        result = CampaignLoader.validate(pack)
        assert result.valid is True  # authoring guidance, not a hard error
        assert _rules(result, "world_var_unsatisfiable")

    def test_unreachable_satisfier_flagged(self):
        pack = ContentPack(
            content_pack_id="x", schema_version="2.0",
            world_premise={"era": "e", "tone": "t", "central_tension": "c"},
            starting_location="hall",
            initial_entities=[
                {"entity_id": "player_001", "entity_type": "player", "location_id": "hall"},
                {"entity_id": "npc.warden", "entity_type": "npc", "location_id": "vault",
                 "attributes": {"authority": "vault_authority"}}],
            initial_locations=[
                {"location_id": "hall", "name": "大厅", "connections": [{"to": "yard"}]},
                {"location_id": "yard", "name": "院子", "connections": [{"to": "hall"}]},
                {"location_id": "vault", "name": "金库", "connections": []}],  # walled off
            world_state_vars=[{
                "var_id": "vault_open", "label": "金库开", "mutable": True,
                "set_by": ["vault_authority"], "request_keywords": ["开金库"]}],
        )
        issues = _rules(CampaignLoader.validate(pack), "world_var_unreachable")
        assert issues and "vault_open" in issues[0].message

    def test_reachable_satisfier_not_flagged(self):
        pack = ContentPack(
            content_pack_id="x", schema_version="2.0",
            world_premise={"era": "e", "tone": "t", "central_tension": "c"},
            starting_location="hall",
            initial_entities=[
                {"entity_id": "player_001", "entity_type": "player", "location_id": "hall"},
                {"entity_id": "npc.warden", "entity_type": "npc", "location_id": "yard",
                 "attributes": {"authority": "gate_authority"}}],
            initial_locations=[
                {"location_id": "hall", "name": "大厅", "connections": [{"to": "yard"}]},
                {"location_id": "yard", "name": "院子", "connections": [{"to": "hall"}]}],
            world_state_vars=[{
                "var_id": "gate_open", "label": "门开", "mutable": True,
                "set_by": ["gate_authority"], "request_keywords": ["开门"]}],
        )
        assert not _rules(CampaignLoader.validate(pack), "world_var_unreachable")

    def test_real_clean_pack_has_no_var_lint_warnings(self):
        pack = CampaignLoader.load_from_file("fixtures/content_packs/escort_proving_ground.json")
        result = CampaignLoader.validate(pack)
        for rule in ("world_var_unsatisfiable", "world_var_no_keywords",
                     "world_var_near_duplicate", "world_var_unreachable"):
            assert not _rules(result, rule), f"{rule}: {[i.message for i in _rules(result, rule)]}"
