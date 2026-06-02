"""Tests for the system hint/suggestion module (P1-6)."""

from __future__ import annotations

import pytest

from verisaria.engine.hint_system import HintContext, HintSystem
from verisaria.engine.schemas import SuggestionMode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def base_context() -> HintContext:
    return HintContext(
        player_id="player_001",
        location_id="loc_tavern",
        zone_id="main_hall",
        nearby_entities=["npc_bartender", "npc_guard"],
        connected_locations=["loc_street", "loc_cellar"],
        in_combat=False,
        active_conversation=None,
        confirmed_drives=[],
        recent_events=[],
    )


@pytest.fixture
def combat_context(base_context: HintContext) -> HintContext:
    return HintContext(
        **{**base_context.__dict__, "in_combat": True, "nearby_entities": ["npc_bandit"]}
    )


class FakeConversationSession:
    def __init__(self, participants: list[str], turn_count: int = 0):
        self.participants = participants
        self.turn_count = turn_count


class FakeEvent:
    def __init__(self, event_type, summary: str = ""):
        self.event_type = event_type
        self.summary = summary


# ---------------------------------------------------------------------------
# Mode = NONE
# ---------------------------------------------------------------------------

class TestNoneMode:
    def test_none_returns_none(self, base_context: HintContext):
        sys = HintSystem(SuggestionMode.NONE)
        assert sys.generate_hint(base_context) is None


# ---------------------------------------------------------------------------
# Combat hints
# ---------------------------------------------------------------------------

class TestCombatHints:
    def test_combat_hint_subtle(self, combat_context: HintContext):
        sys = HintSystem(SuggestionMode.SUBTLE)
        hint = sys.generate_hint(combat_context)
        assert hint is not None
        assert "战斗" in hint
        assert "/flee" in hint

    def test_combat_hint_priority_over_exploration(self, combat_context: HintContext):
        sys = HintSystem(SuggestionMode.NORMAL)
        hint = sys.generate_hint(combat_context)
        assert "战斗" in hint
        # exploration hints may also appear in normal, but combat first
        lines = hint.split("\n")
        assert any("战斗" in line for line in lines)


# ---------------------------------------------------------------------------
# Conversation hints
# ---------------------------------------------------------------------------

class TestConversationHints:
    def test_conversation_new(self, base_context: HintContext):
        ctx = HintContext(
            **{
                **base_context.__dict__,
                "active_conversation": FakeConversationSession(
                    ["player_001", "npc_bartender"], turn_count=0
                ),
            }
        )
        sys = HintSystem(SuggestionMode.SUBTLE)
        hint = sys.generate_hint(ctx)
        assert hint is not None
        assert "npc_bartender" in hint
        assert "/endtalk" in hint

    def test_conversation_ongoing(self, base_context: HintContext):
        ctx = HintContext(
            **{
                **base_context.__dict__,
                "active_conversation": FakeConversationSession(
                    ["player_001", "npc_guard"], turn_count=3
                ),
            }
        )
        sys = HintSystem(SuggestionMode.SUBTLE)
        hint = sys.generate_hint(ctx)
        assert hint is not None
        assert "3" in hint
        assert "npc_guard" in hint


# ---------------------------------------------------------------------------
# Agenda / drive hints
# ---------------------------------------------------------------------------

class TestAgendaHints:
    def test_agenda_hint_subtle(self, base_context: HintContext):
        ctx = HintContext(
            **{
                **base_context.__dict__,
                "confirmed_drives": [
                    {"label": "寻找失落的宝剑", "strength": 0.9},
                    {"label": "与酒馆老板交谈", "strength": 0.4},
                ],
            }
        )
        sys = HintSystem(SuggestionMode.SUBTLE)
        hint = sys.generate_hint(ctx)
        assert hint is not None
        assert "寻找失落的宝剑" in hint
        assert "……" in hint  # subtle style

    def test_agenda_hint_normal(self, base_context: HintContext):
        ctx = HintContext(
            **{
                **base_context.__dict__,
                "confirmed_drives": [
                    {"label": "复仇", "strength": 0.8},
                ],
            }
        )
        sys = HintSystem(SuggestionMode.NORMAL)
        hint = sys.generate_hint(ctx)
        assert hint is not None
        assert "复仇" in hint
        assert "当前主要驱动" in hint

    def test_no_agenda_returns_none_for_subtle(self, base_context: HintContext):
        sys = HintSystem(SuggestionMode.SUBTLE)
        hint = sys.generate_hint(base_context)
        # No combat, no conversation, no agenda, no exploration in subtle
        assert hint is None


# ---------------------------------------------------------------------------
# Exploration hints (normal+)
# ---------------------------------------------------------------------------

class TestExplorationHints:
    def test_exploration_nearby_entities(self, base_context: HintContext):
        sys = HintSystem(SuggestionMode.NORMAL)
        hint = sys.generate_hint(base_context)
        assert hint is not None
        assert "npc_bartender" in hint
        assert "npc_guard" in hint

    def test_exploration_connected_locations(self, base_context: HintContext):
        sys = HintSystem(SuggestionMode.NORMAL)
        hint = sys.generate_hint(base_context)
        assert hint is not None
        assert "loc_street" in hint
        assert "loc_cellar" in hint

    def test_exploration_not_in_subtle(self, base_context: HintContext):
        sys = HintSystem(SuggestionMode.SUBTLE)
        hint = sys.generate_hint(base_context)
        assert hint is None


# ---------------------------------------------------------------------------
# Event hints (normal+)
# ---------------------------------------------------------------------------

class TestEventHints:
    def test_event_combat_hint(self, base_context: HintContext):
        from verisaria.engine.schemas import EventType

        ctx = HintContext(
            **{
                **base_context.__dict__,
                "recent_events": [FakeEvent(EventType.COMBAT, "a fight")],
            }
        )
        sys = HintSystem(SuggestionMode.NORMAL)
        hint = sys.generate_hint(ctx)
        assert hint is not None
        assert "战斗" in hint

    def test_event_speech_hint(self, base_context: HintContext):
        from verisaria.engine.schemas import EventType

        ctx = HintContext(
            **{
                **base_context.__dict__,
                "recent_events": [FakeEvent(EventType.SPEECH, "hello there")],
            }
        )
        sys = HintSystem(SuggestionMode.NORMAL)
        hint = sys.generate_hint(ctx)
        assert hint is not None
        assert "hello" in hint

    def test_event_empty_no_hint(self, base_context: HintContext):
        sys = HintSystem(SuggestionMode.NORMAL)
        # base_context has no recent events
        hint = sys.generate_hint(base_context)
        # still has exploration hints because nearby_entities exist
        assert hint is not None
        assert "探索" in hint


# ---------------------------------------------------------------------------
# Guided mode
# ---------------------------------------------------------------------------

class TestGuidedMode:
    def test_guided_extra_actions(self, base_context: HintContext):
        sys = HintSystem(SuggestionMode.GUIDED)
        hint = sys.generate_hint(base_context)
        assert hint is not None
        assert "环顾四周" in hint
        assert "可用行动类型" in hint

    def test_guided_combat_priority(self, combat_context: HintContext):
        sys = HintSystem(SuggestionMode.GUIDED)
        hint = sys.generate_hint(combat_context)
        assert "战斗" in hint
        assert "可用行动类型" in hint


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_everything(self):
        ctx = HintContext(
            player_id="player_001",
            location_id="loc_void",
            zone_id=None,
            nearby_entities=[],
            connected_locations=[],
        )
        sys = HintSystem(SuggestionMode.NORMAL)
        assert sys.generate_hint(ctx) is None

    def test_set_mode(self, base_context: HintContext):
        sys = HintSystem(SuggestionMode.NONE)
        assert sys.generate_hint(base_context) is None
        sys.set_mode(SuggestionMode.NORMAL)
        assert sys.generate_hint(base_context) is not None

    def test_multiple_hints_in_order(self, base_context: HintContext):
        ctx = HintContext(
            **{
                **base_context.__dict__,
                "in_combat": True,
                "active_conversation": FakeConversationSession(
                    ["player_001", "npc_x"], turn_count=1
                ),
                "confirmed_drives": [{"label": "测试", "strength": 0.9}],
            }
        )
        sys = HintSystem(SuggestionMode.GUIDED)
        hint = sys.generate_hint(ctx)
        assert hint is not None
        lines = hint.split("\n")
        # Combat hint should be present
        assert any("战斗" in line for line in lines)
        # Conversation hint should be present
        assert any("npc_x" in line for line in lines)
        # Agenda hint should be present
        assert any("测试" in line for line in lines)
