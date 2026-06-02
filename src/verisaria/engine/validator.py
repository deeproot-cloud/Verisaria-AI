"""State Validator: validates ArbiterOutput and proposed state changes.

Responsibilities:
- Validate evidence_refs point to real fields in context
- Validate state_changes are within allowed bounds
- Reject illegal proposals, record rejection reasons
- Apply safe truncation for out-of-bounds values

No LLM calls. No narrative generation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from verisaria.engine.schemas import ArbiterOutput, EvidenceRef, StateChange


# Sentinel for "no value seen yet" (distinct from a legitimate None/falsey set).
_UNSET = object()


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

@dataclass
class ValidationIssue:
    field: str
    rule: str
    message: str
    severity: str  # "error" | "warning"


@dataclass
class ValidatedOutcome:
    accepted: bool
    arbiter_output: ArbiterOutput
    accepted_state_changes: list[StateChange]
    rejected_state_changes: list[StateChange]
    issues: list[ValidationIssue] = field(default_factory=list)
    truncation_log: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# State Validator
# ---------------------------------------------------------------------------

class StateValidator:
    """Validate Arbiter output against world state context."""

    # Known numeric field bounds (field_prefix → (min, max))
    BOUNDS: dict[str, tuple[float, float]] = {
        "alertness": (0.0, 1.0),
        "suspicion": (0.0, 1.0),
        "trust": (0.0, 1.0),
        "hp": (0.0, 1000.0),
        "stamina": (0.0, 1000.0),
    }

    def __init__(self, context: dict[str, Any] | None = None) -> None:
        self.context = context or {}

    def validate(
        self,
        arbiter_output: ArbiterOutput,
        authorized_fields: set[str] | None = None,
    ) -> ValidatedOutcome:
        """Validate an ArbiterOutput and return a ValidatedOutcome."""
        issues: list[ValidationIssue] = []
        accepted_changes: list[StateChange] = []
        rejected_changes: list[StateChange] = []
        truncation_log: list[str] = []

        # 1. Validate evidence_refs
        for ref in arbiter_output.evidence_refs:
            ref_issues = self._validate_evidence_ref(ref)
            issues.extend(ref_issues)

        # 2. Validate state_changes_proposed
        for change in arbiter_output.state_changes_proposed:
            change_issues, adjusted_change = self._validate_state_change(
                change, authorized_fields
            )
            # 2a. Causality: subjective (memory/belief) changes must cite evidence.
            change_issues.extend(
                self._validate_causality(change, arbiter_output.evidence_refs)
            )
            issues.extend(change_issues)

            if any(i.severity == "error" for i in change_issues):
                rejected_changes.append(change)
            else:
                accepted_changes.append(adjusted_change)
                if adjusted_change.delta != change.delta:
                    truncation_log.append(
                        f"Truncated {change.field}: {change.delta} → {adjusted_change.delta}"
                    )

        # 3. Cross-change consistency: the proposed set must not contradict
        # itself or the known world state (design §6.4.2 状态一致性).
        consistency_issues = self._validate_consistency(
            arbiter_output.state_changes_proposed
        )
        issues.extend(consistency_issues)

        accepted = not any(i.severity == "error" for i in issues)

        return ValidatedOutcome(
            accepted=accepted,
            arbiter_output=arbiter_output,
            accepted_state_changes=accepted_changes,
            rejected_state_changes=rejected_changes,
            issues=issues,
            truncation_log=truncation_log,
        )

    def _validate_evidence_ref(self, ref: EvidenceRef) -> list[ValidationIssue]:
        """Check that an evidence ref points to a real field in context."""
        issues: list[ValidationIssue] = []

        # Check if path exists in context
        if not self._path_exists(ref.path):
            issues.append(
                ValidationIssue(
                    field=ref.path,
                    rule="field_existence",
                    message=f"Evidence ref points to non-existent field: {ref.path}",
                    severity="error",
                )
            )
        else:
            # Check value consistency
            actual_value = self._get_path_value(ref.path)
            if actual_value is not None and actual_value != ref.value:
                issues.append(
                    ValidationIssue(
                        field=ref.path,
                        rule="value_consistency",
                        message=f"Evidence ref value mismatch: ref={ref.value}, actual={actual_value}",
                        severity="warning",
                    )
                )

        return issues

    def _validate_state_change(
        self,
        change: StateChange,
        authorized_fields: set[str] | None,
    ) -> tuple[list[ValidationIssue], StateChange]:
        """Validate a single state change. Returns (issues, adjusted_change)."""
        issues: list[ValidationIssue] = []
        adjusted_delta = change.delta

        # Check authorization
        if authorized_fields is not None and change.field not in authorized_fields:
            issues.append(
                ValidationIssue(
                    field=change.field,
                    rule="authorization",
                    message=f"LLM not authorized to modify field: {change.field}",
                    severity="error",
                )
            )

        # Check bounds for numeric fields
        if isinstance(change.delta, (int, float)):
            adjusted_delta = self._apply_bounds(change.field, change.delta, issues)

        # Reject changes to immutable fields
        immutable_prefixes = ("event_log", "events", "event.")
        if any(change.field.startswith(p) for p in immutable_prefixes):
            issues.append(
                ValidationIssue(
                    field=change.field,
                    rule="immutability",
                    message=f"Cannot modify immutable field: {change.field}",
                    severity="error",
                )
            )

        adjusted_change = StateChange(
            field=change.field,
            delta=adjusted_delta,
            reason=change.reason,
        )

        return issues, adjusted_change

    # Field-name markers for subjective (causality-gated) changes.
    _SUBJECTIVE_MARKERS = ("belief", "memory", "suspicion", "trust", "affection")

    def _validate_causality(
        self, change: StateChange, evidence_refs: list[EvidenceRef]
    ) -> list[ValidationIssue]:
        """Reject subjective changes (memory/belief) that cite no evidence.

        A Memory/Belief update must be grounded in something the NPC observed;
        an arbiter proposing one with an empty evidence_refs list is hallucinating
        a cause (design §6.4.2 因果检查).
        """
        last = change.field.rsplit(".", 1)[-1]
        is_subjective = any(marker in last for marker in self._SUBJECTIVE_MARKERS)
        if is_subjective and not evidence_refs:
            return [
                ValidationIssue(
                    field=change.field,
                    rule="causality",
                    message=(
                        f"Subjective change {change.field} has no evidence_refs "
                        "to justify it"
                    ),
                    severity="error",
                )
            ]
        return []

    def _validate_consistency(
        self, changes: list[StateChange]
    ) -> list[ValidationIssue]:
        """Detect contradictions within the proposed change set / vs world state.

        Catches: (a) the same field set to two different absolute values,
        (b) the same item added to two different inventories, (c) hp set above
        the entity's known max_hp. Any contradiction rejects the whole output.
        """
        issues: list[ValidationIssue] = []

        # (a) Conflicting absolute (non-numeric) sets to the same field.
        absolute_sets: dict[str, Any] = {}
        for change in changes:
            if isinstance(change.delta, (int, float, bool)):
                continue  # numeric/bool deltas accumulate, not absolute sets
            prev = absolute_sets.get(change.field, _UNSET)
            if prev is not _UNSET and prev != change.delta:
                issues.append(
                    ValidationIssue(
                        field=change.field,
                        rule="state_consistency",
                        message=(
                            f"Contradictory sets for {change.field}: "
                            f"{prev!r} vs {change.delta!r}"
                        ),
                        severity="error",
                    )
                )
            absolute_sets[change.field] = change.delta

        # (b) Same item added into two different inventories.
        item_owner: dict[Any, str] = {}
        for change in changes:
            if not change.field.endswith("inventory.add"):
                continue
            owner = change.field[: -len(".inventory.add")]
            existing = item_owner.get(change.delta)
            if existing is not None and existing != owner:
                issues.append(
                    ValidationIssue(
                        field=change.field,
                        rule="state_consistency",
                        message=(
                            f"Item {change.delta!r} added to two inventories: "
                            f"{existing} and {owner}"
                        ),
                        severity="error",
                    )
                )
            item_owner[change.delta] = owner

        # (c) hp pushed above the entity's known max_hp.
        for change in changes:
            if not change.field.endswith(".hp"):
                continue
            if not isinstance(change.delta, (int, float)):
                continue
            entity_id = change.field[: -len(".hp")]
            max_hp = self._entity_max_hp(entity_id)
            if max_hp is not None and change.delta > max_hp:
                issues.append(
                    ValidationIssue(
                        field=change.field,
                        rule="state_consistency",
                        message=(
                            f"hp {change.delta} exceeds {entity_id} max_hp {max_hp}"
                        ),
                        severity="error",
                    )
                )

        return issues

    def _entity_max_hp(self, entity_id: str) -> float | None:
        """Look up an entity's max_hp from the validator context, if present."""
        entities = self.context.get("entities")
        if isinstance(entities, dict):
            ent = entities.get(entity_id)
            if isinstance(ent, dict) and "max_hp" in ent:
                return ent["max_hp"]
        return None

    def _path_exists(self, path: str) -> bool:
        """Check if a dot-separated path exists in context."""
        parts = path.split(".")
        current: Any = self.context
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return False
        return True

    def _get_path_value(self, path: str) -> Any:
        """Get the value at a dot-separated path in context."""
        parts = path.split(".")
        current: Any = self.context
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current

    def _apply_bounds(
        self, field: str, value: float | int, issues: list[ValidationIssue]
    ) -> float | int:
        """Apply bounds to a numeric value based on field name."""
        # Find matching bound by suffix
        for suffix, (min_val, max_val) in self.BOUNDS.items():
            if field.endswith(suffix):
                if value < min_val:
                    issues.append(
                        ValidationIssue(
                            field=field,
                            rule="bounds",
                            message=f"Value {value} below minimum {min_val} for {field}",
                            severity="warning",
                        )
                    )
                    return min_val
                if value > max_val:
                    issues.append(
                        ValidationIssue(
                            field=field,
                            rule="bounds",
                            message=f"Value {value} above maximum {max_val} for {field}",
                            severity="warning",
                        )
                    )
                    return max_val
                break
        return value
