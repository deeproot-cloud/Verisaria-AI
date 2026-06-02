"""Integration tests for AgendaService wired into GameSession (P1-2)."""

import pytest

from verisaria.engine.agenda import AgendaService
from verisaria.runtime.session import GameSession
from verisaria.engine.schemas import Drive


FIXTURE_PATH = "fixtures/content_packs/valid_frontier_town.json"


@pytest.fixture
def session() -> GameSession:
    return GameSession(FIXTURE_PATH)


class TestAgendaCommand:
    """Test /agenda command output."""

    def test_agenda_command_empty(self, session: GameSession):
        """Fresh session should show empty agenda."""
        result = session._handle_command("/agenda")
        assert "议程" in result
        assert "已确认目标" in result
        assert "无" in result

    def test_agenda_command_with_drives(self, session: GameSession):
        """Agenda with confirmed drives should display them."""
        session.agenda_service.add_declared_drive(
            Drive(
                id="drive_survive",
                label="先解决温饱",
                strength=0.8,
                source="player_declared",
            )
        )
        result = session._handle_command("/agenda")
        assert "先解决温饱" in result
        assert "0.8" in result

    def test_agenda_command_with_declared_intent(self, session: GameSession):
        """Agenda with declared intents should show them."""
        session.agenda_service.add_declared_intent("找份工作")
        result = session._handle_command("/agenda")
        assert "公开意图" in result
        assert "找份工作" in result

    def test_agenda_command_with_private_intent(self, session: GameSession):
        """Agenda with private intents should show hidden section."""
        session.agenda_service.add_declared_intent("暗中帮助恶魔", is_private=True)
        result = session._handle_command("/agenda")
        assert "隐藏意图" in result
        assert "暗中帮助恶魔" in result

    def test_agenda_command_with_aspirations(self, session: GameSession):
        """Long-term aspirations should be shown."""
        session.agenda_service.add_long_term_aspiration("成为传奇冒险者")
        result = session._handle_command("/agenda")
        assert "长期抱负" in result
        assert "成为传奇冒险者" in result

    def test_agenda_in_help(self, session: GameSession):
        """/agenda should appear in help text."""
        result = session._handle_command("/help")
        assert "/agenda" in result


class TestSignalIntake:
    """Test that player actions feed signals into AgendaService."""

    def test_signal_count_after_action(self, session: GameSession):
        """Performing an action should add a signal to the agenda service."""
        initial_count = session.agenda_service.get_state()["signal_count"]

        # Manually add a signal (simulating what run_tick does)
        session.agenda_service.add_signal(
            note="我想调查教会",
            tick=session.world.state.tick,
            source_id=None,
        )

        assert session.agenda_service.get_state()["signal_count"] == initial_count + 1

    def test_aggregation_after_multiple_signals(self, session: GameSession):
        """Enough similar signals should trigger aggregation."""
        for i in range(5):
            session.agenda_service.add_signal(
                note="调查教会的秘密",
                tick=i + 1,
                source_id=f"intent_{i}",
            )
        proposals = session.agenda_service.aggregate_signals(current_tick=6)
        assert len(proposals) >= 1


class TestConfirmedDrivesInHint:
    """Test that confirmed_drives are passed to hint context."""

    def test_hint_context_has_drives(self, session: GameSession):
        """HintContext should include confirmed drives from agenda."""
        session.agenda_service.add_declared_drive(
            Drive(
                id="drive_explore",
                label="探索未知领域",
                strength=0.9,
                source="player_declared",
            )
        )
        context = session._build_hint_context()
        assert len(context.confirmed_drives) == 1
        assert context.confirmed_drives[0].label == "探索未知领域"

    def test_hint_context_empty_drives(self, session: GameSession):
        """HintContext with no drives should have empty list."""
        context = session._build_hint_context()
        assert context.confirmed_drives == []


class TestReflectionScene:
    """Test that reflection scenes can trigger during ticks."""

    def test_reflection_narration_appended(self, session: GameSession):
        """When reflection triggers, narration_hint should be appended."""
        # Add enough signals — don't pre-aggregate, let generate_reflection_scene do it
        for i in range(5):
            session.agenda_service.add_signal(
                note="调查教会",
                tick=i + 1,
                source_id=f"intent_{i}",
            )

        # Use "rest" trigger which always works
        scene = session.agenda_service.generate_reflection_scene(
            current_tick=6, trigger="rest"
        )
        assert scene is not None
        assert len(scene.narration_hint) > 0
        assert "休息" in scene.narration_hint

    def test_reflection_major_event_always_generates(self, session: GameSession):
        """major_event trigger should always generate a scene even with no signals."""
        scene = session.agenda_service.generate_reflection_scene(
            current_tick=10, trigger="major_event"
        )
        assert scene is not None
        assert "心神不宁" in scene.narration_hint
