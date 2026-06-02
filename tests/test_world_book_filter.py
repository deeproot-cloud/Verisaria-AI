"""Tests for World Book Filter: scope-based access control.

Covers visible_to / hidden_from rules, faction/region matching,
and integration with ArbiterContext.
"""

from __future__ import annotations

import pytest

from verisaria.engine.schemas import WorldBookEntry
from verisaria.engine.world import EntityState
from verisaria.engine.world_book_filter import WorldBookFilter


@pytest.fixture
def church_entity():
    return EntityState(
        entity_id="npc.priest",
        entity_type="npc",
        location_id="town_square",
        attributes={"faction": "church_of_flame", "region": "borderlands"},
        traits=["faction.church_of_flame", "devout"],
    )


@pytest.fixture
def demon_entity():
    return EntityState(
        entity_id="npc.demon",
        entity_type="npc",
        location_id="forest",
        attributes={"faction": "demon_kin", "region": "abyss"},
    )


@pytest.fixture
def neutral_entity():
    return EntityState(
        entity_id="npc.merchant",
        entity_type="npc",
        location_id="town_square",
        attributes={"region": "borderlands"},
    )


# ---------------------------------------------------------------------------
# Basic filtering
# ---------------------------------------------------------------------------

class TestBasicFiltering:
    def test_no_restrictions_visible_to_all(self, church_entity):
        entries = [
            WorldBookEntry(
                entry_id="fact_1",
                layer="canonical_fact",
                content="The sky is blue.",
            ),
        ]
        result = WorldBookFilter.filter_for_entity(entries, church_entity)
        assert len(result) == 1

    def test_hidden_from_faction(self, church_entity, demon_entity):
        entries = [
            WorldBookEntry(
                entry_id="secret_1",
                layer="faction_propaganda",
                content="Church secret ritual.",
                access={"hidden_from": {"faction": ["demon_kin"]}},
            ),
        ]
        # Church entity should see it (not hidden from church)
        result_church = WorldBookFilter.filter_for_entity(entries, church_entity)
        assert len(result_church) == 1

        # Demon entity should NOT see it
        result_demon = WorldBookFilter.filter_for_entity(entries, demon_entity)
        assert len(result_demon) == 0

    def test_visible_to_specific_faction(self, church_entity, demon_entity):
        entries = [
            WorldBookEntry(
                entry_id="dogma_1",
                layer="faction_propaganda",
                content="Only church members know this.",
                access={"visible_to": {"faction": ["church_of_flame"]}},
            ),
        ]
        result_church = WorldBookFilter.filter_for_entity(entries, church_entity)
        assert len(result_church) == 1

        result_demon = WorldBookFilter.filter_for_entity(entries, demon_entity)
        assert len(result_demon) == 0

    def test_visible_to_all(self, church_entity, demon_entity):
        entries = [
            WorldBookEntry(
                entry_id="public_1",
                layer="canonical_fact",
                content="Common knowledge.",
                access={"visible_to": {"faction": ["all"]}},
            ),
        ]
        assert len(WorldBookFilter.filter_for_entity(entries, church_entity)) == 1
        assert len(WorldBookFilter.filter_for_entity(entries, demon_entity)) == 1

    def test_multiple_dimensions_match(self, church_entity):
        entries = [
            WorldBookEntry(
                entry_id="local_1",
                layer="local_rumor",
                content="Borderlands weather pattern.",
                access={"visible_to": {"region": ["borderlands"], "faction": ["church_of_flame"]}},
            ),
        ]
        result = WorldBookFilter.filter_for_entity(entries, church_entity)
        assert len(result) == 1

    def test_multiple_dimensions_no_match(self, church_entity):
        entries = [
            WorldBookEntry(
                entry_id="abyss_1",
                layer="local_rumor",
                content="Abyss deep lore.",
                access={"visible_to": {"region": ["abyss"]}},
            ),
        ]
        result = WorldBookFilter.filter_for_entity(entries, church_entity)
        assert len(result) == 0

    def test_hidden_overrides_visible(self, church_entity):
        entries = [
            WorldBookEntry(
                entry_id="trap_1",
                layer="faction_propaganda",
                content="Seems public but hidden from church.",
                access={
                    "visible_to": {"faction": ["all"]},
                    "hidden_from": {"faction": ["church_of_flame"]},
                },
            ),
        ]
        result = WorldBookFilter.filter_for_entity(entries, church_entity)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Trait-based scope
# ---------------------------------------------------------------------------

class TestTraitScope:
    def test_trait_prefix_faction(self, church_entity):
        # church_entity has trait "faction.church_of_flame"
        entries = [
            WorldBookEntry(
                entry_id="trait_test",
                layer="canonical_fact",
                content="Faction-specific info.",
                access={"visible_to": {"faction": ["church_of_flame"]}},
            ),
        ]
        result = WorldBookFilter.filter_for_entity(entries, church_entity)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Neutral / default entity
# ---------------------------------------------------------------------------

class TestNeutralEntity:
    def test_neutral_entity_default_visible(self, neutral_entity):
        entries = [
            WorldBookEntry(
                entry_id="open_1",
                layer="canonical_fact",
                content="No restrictions.",
            ),
        ]
        result = WorldBookFilter.filter_for_entity(entries, neutral_entity)
        assert len(result) == 1

    def test_neutral_entity_not_matching_visible_to(self, neutral_entity):
        entries = [
            WorldBookEntry(
                entry_id="secret_1",
                layer="faction_propaganda",
                content="Church only.",
                access={"visible_to": {"faction": ["church_of_flame"]}},
            ),
        ]
        result = WorldBookFilter.filter_for_entity(entries, neutral_entity)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_none_entity(self):
        entries = [
            WorldBookEntry(entry_id="e1", layer="canonical_fact", content="X"),
        ]
        result = WorldBookFilter.filter_for_entity(entries, None)
        assert len(result) == 0

    def test_empty_entries(self, church_entity):
        result = WorldBookFilter.filter_for_entity([], church_entity)
        assert len(result) == 0

    def test_list_attribute(self):
        entity = EntityState(
            entity_id="npc.hybrid",
            entity_type="npc",
            location_id="town_square",
            attributes={"faction": ["church_of_flame", "merchant_guild"]},
        )
        entries = [
            WorldBookEntry(
                entry_id="multi",
                layer="canonical_fact",
                content="Merchant guild info.",
                access={"visible_to": {"faction": ["merchant_guild"]}},
            ),
        ]
        result = WorldBookFilter.filter_for_entity(entries, entity)
        assert len(result) == 1
