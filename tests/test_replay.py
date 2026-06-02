"""Tests for Replay Framework.

Verifies deterministic execution, scenario serialization, and divergence detection.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verisaria.engine.replay import (
    ReplayEngine,
    ReplayResult,
    ReplayScenario,
    ReplayStep,
    ReplayVerifier,
)
from verisaria.engine.schemas import Event, EventType


# ---------------------------------------------------------------------------
# Scenario serialization
# ---------------------------------------------------------------------------

class TestReplayScenario:
    def test_roundtrip_dict(self):
        scenario = ReplayScenario(
            name="test_scenario",
            description="A simple test",
            content_pack_path="fixtures/content_packs/minimal_valid.json",
            steps=[
                ReplayStep(raw_input="看看周围"),
                ReplayStep(raw_input="去市场角落"),
            ],
            seed=42,
        )
        data = scenario.to_dict()
        restored = ReplayScenario.from_dict(data)
        assert restored.name == scenario.name
        assert len(restored.steps) == 2
        assert restored.steps[0].raw_input == "看看周围"

    def test_save_and_load(self, tmp_path):
        scenario = ReplayScenario(
            name="file_test",
            steps=[ReplayStep(raw_input="hello")],
        )
        path = tmp_path / "scenario.json"
        scenario.save(path)
        restored = ReplayScenario.load(path)
        assert restored.name == "file_test"
        assert restored.steps[0].raw_input == "hello"


# ---------------------------------------------------------------------------
# ReplayEngine execution
# ---------------------------------------------------------------------------

class TestReplayEngine:
    def test_run_single_look(self):
        scenario = ReplayScenario(
            name="single_look",
            content_pack_path="fixtures/content_packs/valid_frontier_town.json",
            steps=[
                ReplayStep(
                    raw_input="看看周围",
                    expected_event_summary="look",
                    expected_event_type="physical",
                ),
            ],
        )
        engine = ReplayEngine(scenario)
        result = engine.run()
        assert result.success is True
        assert len(result.events) >= 1
        # Player action should be first (higher priority than NPC)
        assert result.events[0].event_type.value == "physical"
        assert "look" in result.events[0].summary

    def test_run_multiple_steps(self):
        scenario = ReplayScenario(
            name="look_and_move",
            content_pack_path="fixtures/content_packs/valid_frontier_town.json",
            steps=[
                ReplayStep(raw_input="看看周围"),
                ReplayStep(raw_input="去市场角落"),
            ],
        )
        engine = ReplayEngine(scenario)
        result = engine.run()
        assert result.success is True
        assert len(result.events) >= 2
        # Player events should be in order (higher priority)
        player_events = [e for e in result.events if e.actor_id == "player_001"]
        assert len(player_events) == 2
        assert player_events[0].event_type.value == "physical"
        assert player_events[1].event_type.value == "movement"

    def test_expected_mismatch_detected(self):
        scenario = ReplayScenario(
            name="mismatch_test",
            content_pack_path="fixtures/content_packs/valid_frontier_town.json",
            steps=[
                ReplayStep(
                    raw_input="看看周围",
                    expected_event_summary="NONEXISTENT_STRING",
                ),
            ],
        )
        engine = ReplayEngine(scenario)
        result = engine.run()
        assert result.success is False
        assert any("NONEXISTENT_STRING" in m for m in result.mismatches)

    def test_expected_event_type_mismatch(self):
        scenario = ReplayScenario(
            name="type_mismatch",
            content_pack_path="fixtures/content_packs/valid_frontier_town.json",
            steps=[
                ReplayStep(
                    raw_input="看看周围",
                    expected_event_type="combat",
                ),
            ],
        )
        engine = ReplayEngine(scenario)
        result = engine.run()
        assert result.success is False
        assert any("combat" in m for m in result.mismatches)

    def test_result_serialization(self):
        scenario = ReplayScenario(
            name="serial_test",
            content_pack_path="fixtures/content_packs/valid_frontier_town.json",
            steps=[ReplayStep(raw_input="看看周围")],
        )
        engine = ReplayEngine(scenario)
        result = engine.run()
        data = result.to_dict()
        assert data["success"] is True
        assert data["scenario_name"] == "serial_test"
        assert data["event_count"] >= 1
        assert "final_snapshot" in data

    def test_step_result_structure(self):
        scenario = ReplayScenario(
            name="step_test",
            content_pack_path="fixtures/content_packs/valid_frontier_town.json",
            steps=[ReplayStep(raw_input="看看周围")],
        )
        engine = ReplayEngine(scenario)
        result = engine.run()
        assert len(result.step_results) == 1
        step = result.step_results[0]
        assert step["executed"] is True
        assert step["input"] == "看看周围"
        assert step["event_count"] >= 1


# ---------------------------------------------------------------------------
# Determinism verification
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_look_is_deterministic(self):
        scenario = ReplayScenario(
            name="determinism_look",
            content_pack_path="fixtures/content_packs/valid_frontier_town.json",
            steps=[ReplayStep(raw_input="看看周围")],
        )
        is_det, mismatches = ReplayVerifier.verify_determinism(scenario, runs=3)
        assert is_det is True, f"Non-deterministic: {mismatches}"
        assert len(mismatches) == 0

    def test_move_is_deterministic(self):
        scenario = ReplayScenario(
            name="determinism_move",
            content_pack_path="fixtures/content_packs/valid_frontier_town.json",
            steps=[
                ReplayStep(raw_input="看看周围"),
                ReplayStep(raw_input="去市场角落"),
            ],
        )
        is_det, mismatches = ReplayVerifier.verify_determinism(scenario, runs=3)
        assert is_det is True, f"Non-deterministic: {mismatches}"


# ---------------------------------------------------------------------------
# ReplayVerifier
# ---------------------------------------------------------------------------

class TestReplayVerifier:
    def test_compare_identical_events(self):
        events = [
            Event(
                event_id="evt_0_1",
                event_type=EventType.PHYSICAL,
                actor_id="player_001",
                summary="player_001 look",
                tick=0,
                location_id="town_square",
                canonical_facts={},
            ),
        ]
        issues = ReplayVerifier.compare_events(events, events)
        assert len(issues) == 0

    def test_compare_event_type_mismatch(self):
        e1 = Event(
            event_id="evt_0_1",
            event_type=EventType.PHYSICAL,
            actor_id="player_001",
            summary="player_001 look",
            tick=0,
            location_id="town_square",
            canonical_facts={},
        )
        e2 = Event(
            event_id="evt_0_1",
            event_type=EventType.COMBAT,
            actor_id="player_001",
            summary="player_001 look",
            tick=0,
            location_id="town_square",
            canonical_facts={},
        )
        issues = ReplayVerifier.compare_events([e1], [e2])
        assert len(issues) == 1
        assert "type mismatch" in issues[0]

    def test_compare_count_mismatch(self):
        e1 = Event(
            event_id="evt_0_1",
            event_type=EventType.PHYSICAL,
            actor_id="player_001",
            summary="look",
            tick=0,
            location_id="town_square",
            canonical_facts={},
        )
        issues = ReplayVerifier.compare_events([e1], [e1, e1])
        assert len(issues) == 1
        assert "count mismatch" in issues[0]

    def test_compare_snapshots_identical(self):
        snap = {
            "tick": 5,
            "entity_count": 3,
            "event_count": 5,
            "entities": [
                {"entity_id": "player_001", "location_id": "town_square", "zone_id": None},
            ],
        }
        issues = ReplayVerifier.compare_snapshots(snap, snap)
        assert len(issues) == 0

    def test_compare_snapshots_entity_missing(self):
        expected = {
            "tick": 5,
            "entity_count": 3,
            "event_count": 5,
            "entities": [
                {"entity_id": "player_001", "location_id": "town_square", "zone_id": None},
                {"entity_id": "npc_a", "location_id": "tavern", "zone_id": None},
            ],
        }
        actual = {
            "tick": 5,
            "entity_count": 3,
            "event_count": 5,
            "entities": [
                {"entity_id": "player_001", "location_id": "town_square", "zone_id": None},
            ],
        }
        issues = ReplayVerifier.compare_snapshots(expected, actual)
        assert any("npc_a missing" in i for i in issues)

    def test_compare_snapshots_location_divergence(self):
        expected = {
            "tick": 5,
            "entity_count": 1,
            "event_count": 1,
            "entities": [
                {"entity_id": "player_001", "location_id": "town_square", "zone_id": None},
            ],
        }
        actual = {
            "tick": 5,
            "entity_count": 1,
            "event_count": 1,
            "entities": [
                {"entity_id": "player_001", "location_id": "tavern", "zone_id": None},
            ],
        }
        issues = ReplayVerifier.compare_snapshots(expected, actual)
        assert any("location_id" in i for i in issues)
