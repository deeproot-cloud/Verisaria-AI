"""Replay Framework: deterministic execution verification.

Given a fixed initial state and a fixed input sequence, the engine must
produce identical output every time. This is the core guarantee that
enables regression testing and debugging.

Phase-10 minimal version:
- ReplayScenario: serializable scenario definition
- ReplayEngine: executes scenarios and captures results
- ReplayVerifier: compares two runs for divergence
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verisaria.engine.campaign_loader import CampaignLoader
from verisaria.runtime.session import GameSession
from verisaria.engine.schemas import Event
from verisaria.engine.world import WorldCore


# ---------------------------------------------------------------------------
# Replay Scenario
# ---------------------------------------------------------------------------

@dataclass
class ReplayStep:
    """A single step in a replay scenario.

    One of raw_input, parsed_intent_dict, or action_dict must be provided.
    For MVP, `raw_input` is the simplest — it exercises the full pipeline
    including IntentParser (requires matching LLM fixtures).
    """

    raw_input: str | None = None
    expected_event_summary: str | None = None
    expected_event_type: str | None = None


@dataclass
class ReplayScenario:
    """A fully self-contained replay test case."""

    name: str
    description: str = ""
    content_pack_path: str = "fixtures/content_packs/minimal_valid.json"
    initial_state_overrides: dict[str, Any] = field(default_factory=dict)
    steps: list[ReplayStep] = field(default_factory=list)
    seed: int = 42

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "content_pack_path": self.content_pack_path,
            "initial_state_overrides": self.initial_state_overrides,
            "steps": [
                {
                    "raw_input": s.raw_input,
                    "expected_event_summary": s.expected_event_summary,
                    "expected_event_type": s.expected_event_type,
                }
                for s in self.steps
            ],
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReplayScenario:
        steps = [
            ReplayStep(
                raw_input=s.get("raw_input"),
                expected_event_summary=s.get("expected_event_summary"),
                expected_event_type=s.get("expected_event_type"),
            )
            for s in data.get("steps", [])
        ]
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            content_pack_path=data.get("content_pack_path", "fixtures/content_packs/minimal_valid.json"),
            initial_state_overrides=data.get("initial_state_overrides", {}),
            steps=steps,
            seed=data.get("seed", 42),
        )

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> ReplayScenario:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# Replay Result
# ---------------------------------------------------------------------------

@dataclass
class ReplayResult:
    """Outcome of a single replay run."""

    success: bool
    scenario_name: str
    events: list[Event]
    final_snapshot: dict[str, Any]
    step_results: list[dict[str, Any]]
    mismatches: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "scenario_name": self.scenario_name,
            "event_count": len(self.events),
            "final_snapshot": self.final_snapshot,
            "step_results": self.step_results,
            "mismatches": self.mismatches,
        }


# ---------------------------------------------------------------------------
# Replay Engine
# ---------------------------------------------------------------------------

class ReplayEngine:
    """Execute a ReplayScenario and produce a ReplayResult.

    The engine creates an isolated GameSession for each run to ensure
    no cross-scenario state leakage.
    """

    def __init__(self, scenario: ReplayScenario) -> None:
        self.scenario = scenario

    def run(self) -> ReplayResult:
        """Execute the full scenario.

        Returns a ReplayResult with all events and any mismatches against
        expected values defined in the scenario steps.
        """
        session = GameSession(
            self.scenario.content_pack_path,
            save_dir="_replay_saves",  # ephemeral
        )

        # Apply state overrides
        for key, value in self.scenario.initial_state_overrides.items():
            self._apply_override(session.world, key, value)

        events: list[Event] = []
        step_results: list[dict[str, Any]] = []
        mismatches: list[str] = []

        for idx, step in enumerate(self.scenario.steps):
            step_events: list[Event] = []
            step_before_tick = session.world.state.tick

            if step.raw_input is not None:
                narrative = session.run_tick(step.raw_input)
                # Collect events produced in this tick
                step_events = session.world.event_log.get_events(
                    since_tick=step_before_tick
                )
            else:
                mismatches.append(f"Step {idx}: no executable input provided")
                step_results.append({
                    "step_index": idx,
                    "executed": False,
                    "narrative": "",
                    "events": [],
                })
                continue

            events.extend(step_events)

            # Validate expectations
            if step.expected_event_summary and step_events:
                actual = step_events[0].summary
                if step.expected_event_summary not in actual:
                    mismatches.append(
                        f"Step {idx}: expected summary containing "
                        f"'{step.expected_event_summary}', got '{actual}'"
                    )

            if step.expected_event_type and step_events:
                actual = step_events[0].event_type.value
                if actual != step.expected_event_type:
                    mismatches.append(
                        f"Step {idx}: expected event_type "
                        f"'{step.expected_event_type}', got '{actual}'"
                    )

            step_results.append({
                "step_index": idx,
                "executed": True,
                "input": step.raw_input,
                "narrative": narrative if step.raw_input is not None else "",
                "event_count": len(step_events),
                "event_ids": [e.event_id for e in step_events],
            })

        final_snapshot = session.world.snapshot()
        success = len(mismatches) == 0

        return ReplayResult(
            success=success,
            scenario_name=self.scenario.name,
            events=events,
            final_snapshot=final_snapshot,
            step_results=step_results,
            mismatches=mismatches,
        )

    @staticmethod
    def _apply_override(world: WorldCore, key: str, value: Any) -> None:
        """Apply a simple dot-path override to world state."""
        parts = key.split(".")
        obj = world.state
        for part in parts[:-1]:
            obj = getattr(obj, part, obj)
        if hasattr(obj, parts[-1]):
            setattr(obj, parts[-1], value)


# ---------------------------------------------------------------------------
# Replay Verifier
# ---------------------------------------------------------------------------

class ReplayVerifier:
    """Compare two ReplayResults or two Event sequences for divergence."""

    @staticmethod
    def compare_events(
        expected: list[Event],
        actual: list[Event],
    ) -> list[str]:
        """Return a list of divergence descriptions."""
        mismatches: list[str] = []

        if len(expected) != len(actual):
            mismatches.append(
                f"Event count mismatch: expected {len(expected)}, got {len(actual)}"
            )

        for idx, (exp, act) in enumerate(zip(expected, actual)):
            if exp.event_type != act.event_type:
                mismatches.append(
                    f"Event {idx}: type mismatch expected={exp.event_type.value} "
                    f"actual={act.event_type.value}"
                )
            if exp.actor_id != act.actor_id:
                mismatches.append(
                    f"Event {idx}: actor mismatch expected={exp.actor_id} "
                    f"actual={act.actor_id}"
                )
            if exp.summary != act.summary:
                mismatches.append(
                    f"Event {idx}: summary mismatch\n  expected={exp.summary}\n  actual={act.summary}"
                )

        return mismatches

    @staticmethod
    def compare_snapshots(
        expected: dict[str, Any],
        actual: dict[str, Any],
    ) -> list[str]:
        """Return a list of snapshot divergence descriptions."""
        mismatches: list[str] = []

        for key in ("tick", "entity_count", "event_count"):
            exp_val = expected.get(key)
            act_val = actual.get(key)
            if exp_val != act_val:
                mismatches.append(
                    f"Snapshot {key}: expected={exp_val} actual={act_val}"
                )

        # Deep compare entity list
        exp_entities = {e["entity_id"]: e for e in expected.get("entities", [])}
        act_entities = {e["entity_id"]: e for e in actual.get("entities", [])}

        for eid, exp_ent in exp_entities.items():
            act_ent = act_entities.get(eid)
            if act_ent is None:
                mismatches.append(f"Entity {eid} missing from actual snapshot")
                continue
            for field in ("location_id", "zone_id"):
                if exp_ent.get(field) != act_ent.get(field):
                    mismatches.append(
                        f"Entity {eid}.{field}: expected={exp_ent.get(field)} "
                        f"actual={act_ent.get(field)}"
                    )

        for eid in act_entities:
            if eid not in exp_entities:
                mismatches.append(f"Entity {eid} unexpected in actual snapshot")

        return mismatches

    @classmethod
    def verify_determinism(
        cls,
        scenario: ReplayScenario,
        runs: int = 3,
    ) -> tuple[bool, list[str]]:
        """Run the same scenario multiple times and verify identical results.

        Returns (is_deterministic, list_of_mismatches).
        """
        engine = ReplayEngine(scenario)
        results = [engine.run() for _ in range(runs)]

        mismatches: list[str] = []
        base = results[0]

        for run_idx, result in enumerate(results[1:], start=2):
            snap_issues = cls.compare_snapshots(base.final_snapshot, result.final_snapshot)
            if snap_issues:
                mismatches.append(f"Run {run_idx} snapshot divergence:")
                mismatches.extend(f"  {i}" for i in snap_issues)

            event_issues = cls.compare_events(base.events, result.events)
            if event_issues:
                mismatches.append(f"Run {run_idx} event divergence:")
                mismatches.extend(f"  {i}" for i in event_issues)

        return len(mismatches) == 0, mismatches
