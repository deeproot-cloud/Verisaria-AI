"""Tests for Player Agenda Service: signals, aggregation, reflection, proposals."""

from __future__ import annotations

import pytest

from verisaria.engine.agenda import AgendaService, ReflectionScene
from verisaria.engine.schemas import Drive


class TestSignalAggregation:
    def test_add_single_signal(self) -> None:
        svc = AgendaService("player_001")
        svc.add_signal("我想调查教会", tick=1, source_id="intent_001")
        assert svc.get_state()["signal_count"] == 1

    def test_off_mode_returns_no_proposals(self) -> None:
        svc = AgendaService("player_001", inference_mode="off")
        for i in range(10):
            svc.add_signal("调查教会", tick=i + 1, source_id=f"intent_{i}")
        proposals = svc.aggregate_signals(current_tick=15)
        assert len(proposals) == 0

    def test_aggregation_reaches_threshold(self) -> None:
        svc = AgendaService("player_001")
        for i in range(5):
            svc.add_signal("我想调查教会的秘密", tick=i + 1, source_id=f"intent_{i}")
        proposals = svc.aggregate_signals(current_tick=6)
        assert len(proposals) == 1
        assert proposals[0].confidence >= 0.7
        assert "调查教会" in proposals[0].claim

    def test_aggregation_below_threshold(self) -> None:
        svc = AgendaService("player_001")
        for i in range(3):
            svc.add_signal("调查教会", tick=i + 1)
        proposals = svc.aggregate_signals(current_tick=10)
        assert len(proposals) == 0

    def test_time_decay_reduces_confidence(self) -> None:
        svc = AgendaService("player_001")
        for i in range(5):
            svc.add_signal("调查教会", tick=1)  # All very old
        proposals = svc.aggregate_signals(current_tick=50)
        # All signals are 49+ ticks old → weight 0.3 each
        # total_weight = 1.5, normalized = 1.5/5 = 0.3 < 0.7
        assert len(proposals) == 0

    def test_duplicate_claim_filtered(self) -> None:
        svc = AgendaService("player_001")
        for i in range(5):
            svc.add_signal("调查教会", tick=i + 1)
        # First aggregation creates a proposal
        proposals = svc.aggregate_signals(current_tick=6)
        assert len(proposals) == 1
        # Confirm it
        svc.confirm_proposal(proposals[0].proposal_id, tick=6)
        # Add more signals with same claim
        for i in range(5):
            svc.add_signal("调查教会", tick=20 + i)
        # Should be filtered as duplicate
        new_proposals = svc.aggregate_signals(current_tick=26)
        assert len(new_proposals) == 0

    def test_rejected_path_filtered(self) -> None:
        svc = AgendaService("player_001")
        for i in range(5):
            svc.add_signal("加入教会", tick=i + 1)
        proposals = svc.aggregate_signals(current_tick=6)
        assert len(proposals) == 1
        svc.reject_proposal(proposals[0].proposal_id)
        # Same claim again
        for i in range(5):
            svc.add_signal("加入教会", tick=20 + i)
        new_proposals = svc.aggregate_signals(current_tick=26)
        assert len(new_proposals) == 0


class TestReflectionTrigger:
    def test_rest_triggers_reflection(self) -> None:
        svc = AgendaService("player_001")
        triggered, reason = svc.should_trigger_reflection(tick=10, triggers={"rest": True})
        assert triggered is True
        assert reason == "rest"

    def test_major_event_triggers(self) -> None:
        svc = AgendaService("player_001")
        triggered, reason = svc.should_trigger_reflection(
            tick=10, triggers={"major_event": True}
        )
        assert triggered is True
        assert reason == "major_event"

    def test_drift_triggers(self) -> None:
        svc = AgendaService("player_001")
        triggered, reason = svc.should_trigger_reflection(
            tick=50, triggers={"drift_ticks": 25}
        )
        assert triggered is True
        assert reason == "goal_drift"

    def test_no_trigger(self) -> None:
        svc = AgendaService("player_001")
        triggered, _ = svc.should_trigger_reflection(tick=10, triggers={})
        assert triggered is False

    def test_unconfirmed_inferences_trigger(self) -> None:
        svc = AgendaService("player_001")
        for i in range(5):
            svc.add_signal("调查教会", tick=i + 1)
        svc.aggregate_signals(current_tick=6)
        triggered, reason = svc.should_trigger_reflection(tick=12, triggers={})
        assert triggered is True
        assert reason == "unconfirmed_inferences"

    def test_off_mode_no_reflection(self) -> None:
        svc = AgendaService("player_001", inference_mode="off")
        scene = svc.generate_reflection_scene(current_tick=10, trigger="rest")
        assert scene is None


class TestReflectionScene:
    def test_generate_scene_with_proposals(self) -> None:
        svc = AgendaService("player_001")
        for i in range(5):
            svc.add_signal("我想保护身边的人", tick=i + 1)
        scene = svc.generate_reflection_scene(current_tick=6, trigger="rest")
        assert scene is not None
        assert scene.trigger == "rest"
        assert len(scene.proposals) >= 1
        assert len(scene.narration_hint) > 0

    def test_generate_scene_no_proposals(self) -> None:
        svc = AgendaService("player_001")
        scene = svc.generate_reflection_scene(current_tick=10, trigger="rest")
        # No signals → no proposals → no scene (unless major_event)
        assert scene is None

    def test_major_event_always_generates(self) -> None:
        svc = AgendaService("player_001")
        scene = svc.generate_reflection_scene(current_tick=10, trigger="major_event")
        assert scene is not None


class TestProposalLifecycle:
    def test_confirm_proposal(self) -> None:
        svc = AgendaService("player_001")
        for i in range(5):
            svc.add_signal("调查真相", tick=i + 1)
        proposals = svc.aggregate_signals(current_tick=6)
        drive = svc.confirm_proposal(proposals[0].proposal_id, tick=6)
        assert drive is not None
        assert drive.confirmed is True
        assert drive.confirmed_at_tick == 6

    def test_reject_proposal(self) -> None:
        svc = AgendaService("player_001")
        for i in range(5):
            svc.add_signal("加入黑暗势力", tick=i + 1)
        proposals = svc.aggregate_signals(current_tick=6)
        assert svc.reject_proposal(proposals[0].proposal_id) is True
        assert "加入黑暗势力" in svc._rejected_paths

    def test_confirm_invalid_id(self) -> None:
        svc = AgendaService("player_001")
        drive = svc.confirm_proposal("nonexistent", tick=6)
        assert drive is None


class TestAgendaOutput:
    def test_get_agenda_structure(self) -> None:
        svc = AgendaService("player_001")
        svc.add_declared_intent("找份工作", is_private=False)
        svc.add_open_question("铁匠铺在哪里？")
        agenda = svc.get_agenda(current_tick=5)
        assert agenda.player_id == "player_001"
        assert agenda.tick == 5
        assert len(agenda.declared_to_world) == 1
        assert len(agenda.open_questions) == 1

    def test_get_agenda_with_drives(self) -> None:
        svc = AgendaService("player_001")
        svc.add_declared_drive(
            Drive(
                id="drive_survive",
                label="先解决温饱",
                strength=0.8,
                source="player_declared",
            )
        )
        agenda = svc.get_agenda(current_tick=1)
        assert len(agenda.current_drives) == 1
        assert agenda.current_drives[0].label == "先解决温饱"

    def test_system_inferred_in_agenda(self) -> None:
        svc = AgendaService("player_001")
        for i in range(5):
            svc.add_signal("调查教会", tick=i + 1)
        svc.aggregate_signals(current_tick=6)
        agenda = svc.get_agenda(current_tick=6)
        assert len(agenda.system_inferred) == 1
        assert agenda.system_inferred[0]["requires_confirmation"] is True


class TestDeclaredContent:
    def test_private_intent(self) -> None:
        svc = AgendaService("player_001")
        svc.add_declared_intent("暗中帮助恶魔", is_private=True)
        agenda = svc.get_agenda(current_tick=1)
        assert "暗中帮助恶魔" in agenda.private_intent
        assert "暗中帮助恶魔" not in agenda.declared_to_world

    def test_long_term_aspiration(self) -> None:
        svc = AgendaService("player_001")
        svc.add_long_term_aspiration("成为传奇冒险者")
        agenda = svc.get_agenda(current_tick=1)
        assert "成为传奇冒险者" in agenda.long_term_aspirations
