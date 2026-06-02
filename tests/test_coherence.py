"""Tests for the independent Coherence Checker module (P1-7)."""

from __future__ import annotations

import pytest

from verisaria.engine.coherence import CoherenceChecker, CoherenceIssue
from verisaria.engine.schemas import CommitmentLevel, ParsedIntent
from verisaria.engine.world import Connection, EntityState, LocationState, WorldState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def checker() -> CoherenceChecker:
    return CoherenceChecker()


@pytest.fixture
def world() -> WorldState:
    ws = WorldState()
    ws.entities["player_001"] = EntityState(
        entity_id="player_001",
        entity_type="player",
        location_id="loc_tavern",
        zone_id="main_hall",
        hp=100,
    )
    ws.entities["npc_guard"] = EntityState(
        entity_id="npc_guard",
        entity_type="npc",
        location_id="loc_tavern",
        zone_id="main_hall",
        hp=80,
    )
    ws.entities["npc_far"] = EntityState(
        entity_id="npc_far",
        entity_type="npc",
        location_id="loc_street",
        hp=50,
    )
    ws.entities["npc_dead"] = EntityState(
        entity_id="npc_dead",
        entity_type="npc",
        location_id="loc_tavern",
        hp=0,
    )
    ws.locations["loc_tavern"] = LocationState(
        location_id="loc_tavern",
        connections=[
            Connection(to_location="loc_street", distance="adjacent"),
            Connection(to_location="loc_cellar", distance="near"),
        ],
    )
    ws.locations["loc_street"] = LocationState(
        location_id="loc_street",
        connections=[Connection(to_location="loc_tavern", distance="adjacent")],
    )
    ws.locations["loc_cellar"] = LocationState(location_id="loc_cellar")
    ws.locations["loc_dungeon"] = LocationState(location_id="loc_dungeon")
    return ws


def make_intent(
    actor_id: str = "player_001",
    target_id: str | None = None,
    intent_type: str = "speech",
    commitment: str = "committed",
    modifiers: dict | None = None,
) -> ParsedIntent:
    return ParsedIntent(
        intent_id="intent_1",
        source="natural_language",
        raw_text="test",
        intent_type=intent_type,  # type: ignore[arg-type]
        actor_id=actor_id,
        target_id=target_id,
        commitment=commitment,  # type: ignore[arg-type]
        confidence=1.0,
        modifiers=modifiers or {},
        timestamp=0,
    )


# ---------------------------------------------------------------------------
# Actor checks
# ---------------------------------------------------------------------------

class TestActorChecks:
    def test_actor_exists(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(actor_id="player_001")
        issues = checker.check(intent, world)
        assert not any(i.issue_type == "actor_not_found" for i in issues)

    def test_actor_not_found(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(actor_id="ghost")
        issues = checker.check(intent, world)
        errors = [i for i in issues if i.severity == "error"]
        assert any(i.issue_type == "actor_not_found" for i in errors)

    def test_actor_incapacitated(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(actor_id="npc_dead")
        issues = checker.check(intent, world)
        errors = [i for i in issues if i.severity == "error"]
        assert any(i.issue_type == "actor_incapacitated" for i in errors)


# ---------------------------------------------------------------------------
# Target checks
# ---------------------------------------------------------------------------

class TestTargetChecks:
    def test_target_exists_as_entity(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="npc_guard")
        issues = checker.check(intent, world)
        assert not any(i.issue_type == "target_not_found" for i in issues)

    def test_target_exists_as_location(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="loc_street", intent_type="movement")
        issues = checker.check(intent, world)
        assert not any(i.issue_type == "target_not_found" for i in issues)

    def test_target_not_found(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="ghost")
        issues = checker.check(intent, world)
        errors = [i for i in issues if i.severity == "error"]
        assert any(i.issue_type == "target_not_found" for i in errors)

    def test_combat_must_target_entity(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="loc_tavern", intent_type="combat")
        issues = checker.check(intent, world)
        errors = [i for i in issues if i.severity == "error"]
        assert any(i.issue_type == "combat_target_invalid" for i in errors)

    def test_speech_to_location_warns(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="loc_tavern", intent_type="speech")
        issues = checker.check(intent, world)
        warnings = [i for i in issues if i.severity == "warning"]
        assert any(i.issue_type == "speech_target_location" for i in warnings)


# ---------------------------------------------------------------------------
# Spatial checks
# ---------------------------------------------------------------------------

class TestSpatialChecks:
    def test_same_location_ok(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="npc_guard", intent_type="speech")
        issues = checker.check(intent, world)
        assert not any(i.issue_type == "spatial_mismatch" for i in issues)

    def test_different_location_error(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="npc_far", intent_type="speech")
        issues = checker.check(intent, world)
        errors = [i for i in issues if i.severity == "error"]
        assert any(i.issue_type == "spatial_mismatch" for i in errors)

    def test_movement_skips_spatial(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="loc_street", intent_type="movement")
        issues = checker.check(intent, world)
        assert not any(i.issue_type == "spatial_mismatch" for i in issues)

    def test_no_target_skips_spatial(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id=None, intent_type="look")
        issues = checker.check(intent, world)
        assert not any(i.issue_type == "spatial_mismatch" for i in issues)


# ---------------------------------------------------------------------------
# Movement checks
# ---------------------------------------------------------------------------

class TestMovementChecks:
    def test_reachable_location(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="loc_street", intent_type="movement")
        issues = checker.check(intent, world)
        assert not any(i.issue_type.startswith("movement") for i in issues)

    def test_unreachable_location(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="loc_dungeon", intent_type="movement")
        issues = checker.check(intent, world)
        errors = [i for i in issues if i.severity == "error"]
        assert any(i.issue_type == "movement_unreachable" for i in errors)

    def test_unknown_location(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="loc_void", intent_type="movement")
        issues = checker.check(intent, world)
        errors = [i for i in issues if i.severity == "error"]
        assert any(i.issue_type == "movement_unknown_location" for i in errors)

    def test_no_destination(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id=None, intent_type="movement")
        issues = checker.check(intent, world)
        errors = [i for i in issues if i.severity == "error"]
        assert any(i.issue_type == "movement_no_destination" for i in errors)

    def test_destination_in_modifiers(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(
            target_id=None,
            intent_type="movement",
            modifiers={"destination": "loc_street"},
        )
        issues = checker.check(intent, world)
        assert not any(i.issue_type.startswith("movement") for i in issues)


# ---------------------------------------------------------------------------
# Location substring resolution (P0.3)
# ---------------------------------------------------------------------------

class TestLocationSubstringResolution:
    """A partial location name (e.g. the LLM returns 'street' for 'loc_street')
    should resolve rather than failing as an unknown location."""

    def test_substring_resolves_movement_target(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="street", intent_type="movement")
        issues = checker.check(intent, world)
        assert not any(i.issue_type == "movement_unknown_location" for i in issues)

    def test_substring_resolves_for_target_check(self, checker: CoherenceChecker, world: WorldState):
        # A speech/look intent pointed at a partial location name must not be
        # flagged target_not_found.
        intent = make_intent(target_id="street", intent_type="look")
        issues = checker.check(intent, world)
        assert not any(i.issue_type == "target_not_found" for i in issues)

    def test_ambiguous_or_absent_substring_still_unknown(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="atlantis", intent_type="movement")
        issues = checker.check(intent, world)
        assert any(i.issue_type == "movement_unknown_location" for i in issues)


# ---------------------------------------------------------------------------
# Off-scene target → friendly, narrative message (P0.3)
# ---------------------------------------------------------------------------

class TestOffSceneTarget:
    """Talking to an NPC who is elsewhere should yield a clear 'not here'
    message, not a raw arbitration/coherence error string."""

    def test_off_scene_target_gives_friendly_hint(self, checker: CoherenceChecker, world: WorldState):
        # player at loc_tavern, npc_far at loc_street
        intent = make_intent(target_id="npc_far", intent_type="speech")
        issues = checker.check(intent, world)
        spatial = [i for i in issues if i.issue_type == "spatial_mismatch"]
        assert spatial, "expected a spatial mismatch for an off-scene target"
        msg = spatial[0].message
        # Must read like in-world guidance, not a raw 'cannot interact' dump.
        assert "不在" in msg or "not here" in msg.lower()


# ---------------------------------------------------------------------------
# Combat checks
# ---------------------------------------------------------------------------

class TestCombatChecks:
    def test_combat_no_target(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id=None, intent_type="combat")
        issues = checker.check(intent, world)
        errors = [i for i in issues if i.severity == "error"]
        assert any(i.issue_type == "combat_no_target" for i in errors)

    def test_combat_target_alive(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="npc_guard", intent_type="combat")
        issues = checker.check(intent, world)
        assert not any(i.issue_type == "combat_target_incapacitated" for i in issues)

    def test_combat_target_incapacitated_warns(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="npc_dead", intent_type="combat")
        issues = checker.check(intent, world)
        warnings = [i for i in issues if i.severity == "warning"]
        assert any(i.issue_type == "combat_target_incapacitated" for i in warnings)

    def test_non_combat_skips_combat_checks(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(target_id="npc_guard", intent_type="speech")
        issues = checker.check(intent, world)
        assert not any(i.issue_type.startswith("combat") for i in issues)


# ---------------------------------------------------------------------------
# Commitment checks
# ---------------------------------------------------------------------------

class TestCommitmentChecks:
    def test_low_commitment_with_wait_ok(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(intent_type="wait", commitment="considering")
        issues = checker.check(intent, world)
        assert not any(i.issue_type == "low_commitment_with_action" for i in issues)

    def test_low_commitment_with_action_warns(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(intent_type="speech", commitment="considering")
        issues = checker.check(intent, world)
        warnings = [i for i in issues if i.severity == "warning"]
        assert any(i.issue_type == "low_commitment_with_action" for i in warnings)

    def test_committed_no_warning(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(intent_type="combat", commitment="committed")
        issues = checker.check(intent, world)
        assert not any(i.issue_type == "low_commitment_with_action" for i in issues)


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------

class TestIntegration:
    def test_multiple_issues(self, checker: CoherenceChecker, world: WorldState):
        """An invalid intent can trigger multiple independent issues."""
        intent = make_intent(
            actor_id="ghost",
            target_id="loc_dungeon",
            intent_type="combat",
            commitment="considering",
        )
        issues = checker.check(intent, world)
        types = {i.issue_type for i in issues}
        assert "actor_not_found" in types
        assert "combat_target_invalid" in types
        assert "low_commitment_with_action" in types

    def test_valid_intent_no_issues(self, checker: CoherenceChecker, world: WorldState):
        intent = make_intent(
            target_id="npc_guard",
            intent_type="speech",
            commitment="committed",
        )
        issues = checker.check(intent, world)
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []
