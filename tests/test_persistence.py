"""Tests for Persistence Layer: save, load, verify, restore, prune."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verisaria.engine.persistence import PersistenceLayer, _compute_hash, _strip_hashes
from verisaria.engine.schemas import Action, ActionType, EventType, SaveData
from verisaria.engine.world import EntityState, WorldCore, WorldState


@pytest.fixture
def temp_save_dir(tmp_path: Path) -> Path:
    return tmp_path / "saves"


@pytest.fixture
def persistence(temp_save_dir: Path) -> PersistenceLayer:
    return PersistenceLayer(save_dir=temp_save_dir)


@pytest.fixture
def populated_world() -> WorldCore:
    state = WorldState(tick=5)
    state.entities["player_001"] = EntityState(
        entity_id="player_001",
        entity_type="player",
        location_id="town_square",
        zone_id="center",
    )
    state.entities["npc.guard_b"] = EntityState(
        entity_id="npc.guard_b",
        entity_type="npc",
        location_id="town_square",
        zone_id="center",
        attributes={"strength": 0.7},
    )
    core = WorldCore(initial_state=state)

    # Commit a few actions to build event log
    action1 = Action(
        action_id="act_5_1",
        actor_id="player_001",
        action_type=ActionType.SPEECH,
        target_id="npc.guard_b",
        params={"content": "你好", "verb": "say"},
        tick=5,
    )
    core.commit_action(action1)

    action2 = Action(
        action_id="act_5_2",
        actor_id="player_001",
        action_type=ActionType.MOVEMENT,
        params={"to_location": "tavern", "to_zone": "main_hall", "verb": "move"},
        tick=5,
    )
    core.commit_action(action2)

    return core


# ---------------------------------------------------------------------------
# Save / Load
# ---------------------------------------------------------------------------

class TestSaveLoad:
    def test_save_dir_created_lazily_on_first_save_not_on_init(
        self, tmp_path: Path, populated_world: WorldCore
    ) -> None:
        """Constructing a PersistenceLayer (which every GameSession does) must not
        eagerly litter an empty save dir — otherwise tests/replays that never save
        leave `saves/`/`_replay_saves/` behind in the repo. The dir is created on
        the first actual save."""
        d = tmp_path / "lazy_saves"
        layer = PersistenceLayer(save_dir=d)
        assert not d.exists()  # no eager mkdir on construction

        layer.save(world_core=populated_world, tick=5, content_pack_id="x")
        assert d.exists()  # created on first save

    def test_save_creates_file(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        save_data = persistence.save(
            world_core=populated_world,
            tick=5,
            content_pack_id="frontier_town",
            save_type="manual",
        )
        path = persistence.save_dir / f"{save_data.save_id}.json"
        assert path.exists()

    def test_save_contains_expected_fields(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        save_data = persistence.save(
            world_core=populated_world,
            tick=5,
            content_pack_id="frontier_town",
            save_type="manual",
            rng_seed=42,
            rng_consumed=3,
        )
        assert save_data.tick == 5
        assert save_data.content_pack_id == "frontier_town"
        assert save_data.save_type == "manual"
        assert save_data.rng_state == "seed:42,consumed:3"
        assert save_data.snapshot_hash is not None
        assert save_data.event_log_hash is not None
        assert len(save_data.world_state.get("events", [])) == 2

    def test_load_roundtrip(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        original = persistence.save(
            world_core=populated_world,
            tick=5,
            content_pack_id="frontier_town",
        )
        loaded = persistence.load(original.save_id)
        assert loaded.save_id == original.save_id
        assert loaded.tick == original.tick
        assert loaded.snapshot_hash == original.snapshot_hash
        assert loaded.event_log_hash == original.event_log_hash
        assert len(loaded.world_state["events"]) == 2

    def test_load_missing_raises(self, persistence: PersistenceLayer) -> None:
        with pytest.raises(FileNotFoundError):
            persistence.load("nonexistent_save")


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------

class TestRestore:
    def test_restore_world_core_state(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        save_data = persistence.save(world_core=populated_world, tick=5)
        restored = persistence.restore_world_core(save_data)

        assert restored.state.tick == 5
        assert "player_001" in restored.state.entities
        assert "npc.guard_b" in restored.state.entities
        # Note: player_001 moved to tavern in the fixture's movement action
        assert restored.state.entities["player_001"].location_id == "tavern"
        assert len(restored.event_log) == 2

    def test_restore_event_log_content(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        save_data = persistence.save(world_core=populated_world, tick=5)
        restored = persistence.restore_world_core(save_data)

        events = restored.event_log.get_events(since_tick=0)
        assert events[0].actor_id == "player_001"
        assert events[0].event_type == EventType.SPEECH
        assert events[1].event_type == EventType.MOVEMENT

    def test_restore_action_seq(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        save_data = persistence.save(world_core=populated_world, tick=5)
        restored = persistence.restore_world_core(save_data)
        # After 2 events, next should be act_5_3
        assert restored.next_action_id() == "act_5_3"


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

class TestVerification:
    def test_verify_valid_save(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        save_data = persistence.save(world_core=populated_world, tick=5)
        is_valid, issues = persistence.verify(save_data)
        assert is_valid is True
        assert issues == []

    def test_detect_snapshot_tampering(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        save_data = persistence.save(world_core=populated_world, tick=5)
        # Tamper with the loaded data
        tampered_dict = save_data.model_dump()
        tampered_dict["world_state"]["tick"] = 999
        tampered = SaveData(**tampered_dict)

        is_valid, issues = persistence.verify(tampered)
        assert is_valid is False
        assert any("snapshot_hash mismatch" in i for i in issues)

    def test_detect_event_log_tampering(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        save_data = persistence.save(world_core=populated_world, tick=5)
        tampered_dict = save_data.model_dump()
        tampered_dict["world_state"]["events"][0]["actor_id"] = "hacker"
        tampered = SaveData(**tampered_dict)

        is_valid, issues = persistence.verify(tampered)
        assert is_valid is False
        assert any("event_log_hash mismatch" in i for i in issues)


# ---------------------------------------------------------------------------
# Listing / Deletion / Pruning
# ---------------------------------------------------------------------------

class TestListDelete:
    def test_list_saves(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        persistence.save(world_core=populated_world, tick=1, save_type="manual")
        persistence.save(world_core=populated_world, tick=2, save_type="manual")

        saves = persistence.list_saves()
        assert len(saves) == 2
        assert all(s["save_type"] == "manual" for s in saves)

    def test_delete_save(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        save_data = persistence.save(world_core=populated_world, tick=1)
        assert persistence.delete_save(save_data.save_id) is True
        assert persistence.delete_save(save_data.save_id) is False
        assert len(persistence.list_saves()) == 0

    def test_prune_auto_saves(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        for i in range(12):
            persistence.save(world_core=populated_world, tick=i, save_type="auto")

        assert len(persistence.list_saves()) == 12
        deleted = persistence.prune_auto_saves(max_auto_saves=10)
        assert deleted == 2
        assert len(persistence.list_saves()) == 10

    def test_prune_does_not_touch_manual(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        for i in range(12):
            persistence.save(world_core=populated_world, tick=i, save_type="auto")
        persistence.save(world_core=populated_world, tick=99, save_type="manual")

        persistence.prune_auto_saves(max_auto_saves=10)
        saves = persistence.list_saves()
        assert len(saves) == 11
        manual = [s for s in saves if s["save_type"] == "manual"]
        assert len(manual) == 1


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------

class TestHashHelpers:
    def test_compute_hash_deterministic(self) -> None:
        data = {"a": 1, "b": [2, 3]}
        h1 = _compute_hash(data)
        h2 = _compute_hash(data)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_strip_hashes_removes_fields(self) -> None:
        data = {"snapshot_hash": "abc", "event_log_hash": "def", "tick": 1}
        stripped = _strip_hashes(data)
        assert "snapshot_hash" not in stripped
        assert "event_log_hash" not in stripped
        assert stripped["tick"] == 1


# ---------------------------------------------------------------------------
# External state (scheduler, agenda, campaign)
# ---------------------------------------------------------------------------

class TestExternalState:
    def test_save_with_scheduler_state(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        scheduler_state = {"tick": 5, "policy_id": "default_pacing"}
        save_data = persistence.save(
            world_core=populated_world,
            tick=5,
            scheduler_state=scheduler_state,
        )
        assert save_data.scheduler_state == scheduler_state
        loaded = persistence.load(save_data.save_id)
        assert loaded.scheduler_state["policy_id"] == "default_pacing"

    def test_save_with_agenda_state(self, persistence: PersistenceLayer, populated_world: WorldCore) -> None:
        agenda_state = {"current_drives": [{"id": "drive_1", "label": " survive"}]}
        save_data = persistence.save(
            world_core=populated_world,
            tick=5,
            agenda_state=agenda_state,
        )
        loaded = persistence.load(save_data.save_id)
        assert loaded.player_state["agenda"]["current_drives"][0]["id"] == "drive_1"
