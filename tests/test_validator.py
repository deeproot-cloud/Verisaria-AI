"""Tests for State Validator."""

import pytest

from verisaria.engine.schemas import ArbiterOutput, EvidenceRef, StateChange
from verisaria.engine.validator import StateValidator, ValidationIssue


@pytest.fixture
def validator() -> StateValidator:
    return StateValidator(
        context={
            "npc": {
                "guard_b": {
                    "attributes": {"perception": 0.75, "alertness": 0.3},
                    "traits": {"anxious": True, "greedy": True},
                }
            },
            "world_state": {
                "locations": {
                    "town_square": {"zones": {"center": {"exposure": "high"}}}
                }
            },
        }
    )


class TestEvidenceRefValidation:
    def test_valid_evidence_ref(self, validator: StateValidator):
        output = ArbiterOutput(
            arbiter_id="arb_001",
            source_action_id="act_001",
            outcome="success",
            reason="test",
            evidence_refs=[
                EvidenceRef(
                    path="npc.guard_b.attributes.perception",
                    value=0.75,
                    source="attribute",
                )
            ],
            state_changes_proposed=[],
            confidence=0.8,
        )
        result = validator.validate(output)
        assert result.accepted
        assert len(result.issues) == 0

    def test_evidence_ref_not_found(self, validator: StateValidator):
        output = ArbiterOutput(
            arbiter_id="arb_001",
            source_action_id="act_001",
            outcome="success",
            reason="test",
            evidence_refs=[
                EvidenceRef(
                    path="npc.guard_b.attributes.strength",
                    value=0.5,
                    source="attribute",
                )
            ],
            state_changes_proposed=[],
            confidence=0.8,
        )
        result = validator.validate(output)
        assert not result.accepted
        assert any(i.rule == "field_existence" for i in result.issues)

    def test_evidence_ref_value_mismatch(self, validator: StateValidator):
        output = ArbiterOutput(
            arbiter_id="arb_001",
            source_action_id="act_001",
            outcome="success",
            reason="test",
            evidence_refs=[
                EvidenceRef(
                    path="npc.guard_b.attributes.perception",
                    value=0.99,  # actual is 0.75
                    source="attribute",
                )
            ],
            state_changes_proposed=[],
            confidence=0.8,
        )
        result = validator.validate(output)
        # Value mismatch is a warning, not an error
        assert result.accepted
        assert any(i.rule == "value_consistency" for i in result.issues)


class TestStateChangeValidation:
    def test_valid_state_change(self, validator: StateValidator):
        output = ArbiterOutput(
            arbiter_id="arb_001",
            source_action_id="act_001",
            outcome="success",
            reason="test",
            evidence_refs=[],
            state_changes_proposed=[
                StateChange(
                    field="npc.guard_b.alertness",
                    delta=0.2,
                    reason="被搭话引起警觉",
                )
            ],
            confidence=0.8,
        )
        result = validator.validate(output)
        assert result.accepted
        assert len(result.accepted_state_changes) == 1

    def test_state_change_out_of_bounds_truncated(self, validator: StateValidator):
        output = ArbiterOutput(
            arbiter_id="arb_001",
            source_action_id="act_001",
            outcome="success",
            reason="test",
            evidence_refs=[],
            state_changes_proposed=[
                StateChange(
                    field="npc.guard_b.alertness",
                    delta=1.5,  # above max 1.0
                    reason="test",
                )
            ],
            confidence=0.8,
        )
        result = validator.validate(output)
        assert result.accepted
        assert result.accepted_state_changes[0].delta == 1.0  # truncated
        assert len(result.truncation_log) == 1

    def test_state_change_below_bounds_truncated(self, validator: StateValidator):
        output = ArbiterOutput(
            arbiter_id="arb_001",
            source_action_id="act_001",
            outcome="success",
            reason="test",
            evidence_refs=[],
            state_changes_proposed=[
                StateChange(
                    field="npc.guard_b.alertness",
                    delta=-0.5,  # below min 0.0
                    reason="test",
                )
            ],
            confidence=0.8,
        )
        result = validator.validate(output)
        assert result.accepted
        assert result.accepted_state_changes[0].delta == 0.0  # truncated

    def test_state_change_unauthorized(self, validator: StateValidator):
        output = ArbiterOutput(
            arbiter_id="arb_001",
            source_action_id="act_001",
            outcome="success",
            reason="test",
            evidence_refs=[],
            state_changes_proposed=[
                StateChange(
                    field="npc.guard_b.alertness",
                    delta=0.2,
                    reason="test",
                )
            ],
            confidence=0.8,
        )
        authorized = {"npc.guard_b.attributes.perception"}  # alertness not authorized
        result = validator.validate(output, authorized_fields=authorized)
        assert not result.accepted
        assert len(result.rejected_state_changes) == 1

    def test_state_change_immutable_field(self, validator: StateValidator):
        output = ArbiterOutput(
            arbiter_id="arb_001",
            source_action_id="act_001",
            outcome="success",
            reason="test",
            evidence_refs=[],
            state_changes_proposed=[
                StateChange(
                    field="event_log.evt_001.summary",
                    delta="hacked",
                    reason="test",
                )
            ],
            confidence=0.8,
        )
        result = validator.validate(output)
        assert not result.accepted
        assert any("immutability" in i.rule for i in result.issues)
