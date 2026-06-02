"""Tests for P3.2: State Validator completeness / causal checks.

Adds two reject-the-output checks on top of the existing per-field validation
(design §6.4.2):

- 状态一致性: a proposed change set must not contain contradictions, e.g. the
  same field set to two different absolute values, the same item placed into two
  inventories, or hp pushed above max_hp.
- 因果检查: a memory/belief change must cite evidence (evidence_refs); an
  unsupported subjective change is rejected.
"""

from __future__ import annotations

import pytest

from verisaria.engine.schemas import ArbiterOutput, EvidenceRef, StateChange
from verisaria.engine.validator import StateValidator


def _output(changes, evidence=None, outcome="success") -> ArbiterOutput:
    return ArbiterOutput(
        arbiter_id="arb_001",
        source_action_id="act_001",
        outcome=outcome,
        reason="test",
        evidence_refs=evidence or [],
        state_changes_proposed=changes,
        confidence=0.8,
    )


@pytest.fixture
def validator() -> StateValidator:
    return StateValidator(
        context={
            "npc": {"guard_b": {"attributes": {"hp": 80}, "traits": {}}},
            "entities": {
                "player_001": {"hp": 50, "max_hp": 100},
            },
        }
    )


class TestStateConsistency:
    def test_conflicting_absolute_sets_rejected(self, validator):
        # Same field set to two different absolute (string) values → contradiction.
        out = _output([
            StateChange(field="player_001.location_id", delta="tavern", reason="r1"),
            StateChange(field="player_001.location_id", delta="market", reason="r2"),
        ])
        result = validator.validate(out)
        assert not result.accepted
        assert any(i.rule == "state_consistency" for i in result.issues)

    def test_same_absolute_set_twice_is_fine(self, validator):
        # Identical absolute sets are redundant but not contradictory.
        out = _output([
            StateChange(field="player_001.location_id", delta="tavern", reason="r1"),
            StateChange(field="player_001.location_id", delta="tavern", reason="r2"),
        ])
        result = validator.validate(out)
        assert result.accepted

    def test_item_in_two_inventories_rejected(self, validator):
        # Same item added to two different owners' inventories → contradiction.
        out = _output([
            StateChange(field="player_001.inventory.add", delta="short_sword", reason="r1"),
            StateChange(field="npc.guard_b.inventory.add", delta="short_sword", reason="r2"),
        ])
        result = validator.validate(out)
        assert not result.accepted
        assert any(i.rule == "state_consistency" for i in result.issues)

    def test_numeric_deltas_to_same_field_allowed(self, validator):
        # Two numeric deltas to the same field accumulate; not a contradiction.
        out = _output([
            StateChange(field="npc.guard_b.alertness", delta=0.1, reason="r1"),
            StateChange(field="npc.guard_b.alertness", delta=0.2, reason="r2"),
        ])
        result = validator.validate(out)
        assert result.accepted

    def test_hp_above_max_rejected(self, validator):
        # Setting hp beyond the entity's max_hp is an inconsistent state.
        out = _output([
            StateChange(field="player_001.hp", delta=999, reason="overheal"),
        ])
        result = validator.validate(out)
        assert not result.accepted
        assert any(i.rule == "state_consistency" for i in result.issues)


class TestCausality:
    def test_belief_change_without_evidence_rejected(self, validator):
        out = _output([
            StateChange(field="npc.guard_b.belief_player_thief", delta=0.5, reason="hunch"),
        ])
        result = validator.validate(out)
        assert not result.accepted
        assert any(i.rule == "causality" for i in result.issues)

    def test_belief_change_with_evidence_accepted(self, validator):
        out = _output(
            [StateChange(field="npc.guard_b.belief_player_thief", delta=0.5, reason="saw it")],
            evidence=[EvidenceRef(
                path="npc.guard_b.attributes.hp", value=80, source="attribute",
            )],
        )
        result = validator.validate(out)
        assert result.accepted

    def test_memory_change_without_evidence_rejected(self, validator):
        out = _output([
            StateChange(field="npc.guard_b.memory_recent", delta="saw a theft", reason="x"),
        ])
        result = validator.validate(out)
        assert not result.accepted
        assert any(i.rule == "causality" for i in result.issues)

    def test_plain_attribute_change_needs_no_evidence(self, validator):
        # Non-subjective changes (attributes) do not require evidence_refs.
        out = _output([
            StateChange(field="npc.guard_b.alertness", delta=0.2, reason="startled"),
        ])
        result = validator.validate(out)
        assert result.accepted


class TestConsistencyDoesNotBreakNormalChanges:
    def test_single_change_still_accepted(self, validator):
        out = _output([
            StateChange(field="npc.guard_b.alertness", delta=0.2, reason="r"),
        ])
        result = validator.validate(out)
        assert result.accepted
        assert len(result.accepted_state_changes) == 1
