"""Integration tests for Campaign Driver pressure events in GameSession (P1-3)."""

import pytest

from verisaria.engine.campaign import (
    CampaignDriver,
    CampaignDriverManager,
    PossibleEvent,
    Signal,
)
from verisaria.runtime.session import GameSession
from verisaria.engine.schemas import EventType


FIXTURE_PATH = "fixtures/content_packs/valid_frontier_town.json"


@pytest.fixture
def session() -> GameSession:
    return GameSession(FIXTURE_PATH)


class TestCampaignContext:
    """Test that campaign context is built correctly from world state."""

    def test_context_has_basic_metrics(self, session: GameSession):
        """Context should include entity count, event count, tick."""
        ctx = session._build_campaign_context()
        assert "entity_count" in ctx
        assert "event_count" in ctx
        assert "tick" in ctx
        assert ctx["entity_count"] > 0

    def test_context_has_player_metrics(self, session: GameSession):
        """Context should include player HP and location."""
        ctx = session._build_campaign_context()
        assert "player_hp" in ctx
        assert "player_location" in ctx

    def test_context_has_npc_attributes(self, session: GameSession):
        """Context should aggregate NPC attributes as world metrics."""
        ctx = session._build_campaign_context()
        # The content pack has npc.guard_b with perception: 0.6
        assert "perception" in ctx
        assert ctx["perception"] > 0

    def test_context_has_threshold(self, session: GameSession):
        """Context should have a default threshold for signal conditions."""
        ctx = session._build_campaign_context()
        assert "threshold" in ctx
        assert ctx["threshold"] == 1.5

    def test_context_has_combat_state(self, session: GameSession):
        """Context should include combat_active flag."""
        ctx = session._build_campaign_context()
        assert "combat_active" in ctx
        assert ctx["combat_active"] is False


class TestCampaignDriverTriggering:
    """Test that campaign drivers generate events when conditions are met."""

    def test_driver_triggers_with_high_food_price(self, session: GameSession):
        """When food_price exceeds a numeric threshold, driver should trigger."""
        driver = CampaignDriver(
            driver_id="test_pressure",
            driver_type="social_pressure",
            description="Test pressure driver",
            signals=[Signal(condition="food_price > 1.5", weight=0.5)],
            possible_events=[PossibleEvent(event_type="market_argument", probability=1.0)],
            severity=0.3,
            cooldown_ticks=5,
        )
        session.campaign_driver_manager = CampaignDriverManager([driver], seed=42)

        # Inject food_price into context via NPC attribute
        from verisaria.engine.world import EntityState
        merchant = EntityState(
            entity_id="npc.merchant",
            entity_type="npc",
            location_id="town_square",
            zone_id="center",
            attributes={"food_price": 3.0},
        )
        session.world.state.entities["npc.merchant"] = merchant

        events_before = len(session.world.event_log)
        narrative = session._check_campaign_drivers()

        assert narrative is not None
        assert len(session.world.event_log) > events_before
        last_event = session.world.event_log._events[-1]
        assert last_event.event_type == EventType.SYSTEM
        assert "campaign_pressure" in last_event.tags
        assert last_event.canonical_facts["driver_id"] == "test_pressure"

    def test_no_event_when_conditions_not_met(self, session: GameSession):
        """When conditions aren't met, no pressure events should be generated."""
        driver = CampaignDriver(
            driver_id="test_quiet",
            driver_type="social_pressure",
            description="Test quiet driver",
            signals=[Signal(condition="food_price > 100", weight=0.5)],
            possible_events=[PossibleEvent(event_type="riot", probability=1.0)],
            severity=0.3,
            cooldown_ticks=5,
        )
        session.campaign_driver_manager = CampaignDriverManager([driver], seed=42)

        events_before = len(session.world.event_log)
        narrative = session._check_campaign_drivers()

        assert narrative is None
        assert len(session.world.event_log) == events_before

    def test_cooldown_prevents_retrigger(self, session: GameSession):
        """Driver should not trigger again during cooldown period."""
        driver = CampaignDriver(
            driver_id="test_cooldown",
            driver_type="test",
            description="Cooldown test",
            signals=[Signal(condition="true", weight=1.0)],
            possible_events=[PossibleEvent(event_type="test_event", probability=1.0)],
            severity=0.1,
            cooldown_ticks=10,
        )
        session.campaign_driver_manager = CampaignDriverManager([driver], seed=42)

        # First trigger
        narrative1 = session._check_campaign_drivers()
        assert narrative1 is not None

        # Second trigger within cooldown — should not fire
        narrative2 = session._check_campaign_drivers()
        assert narrative2 is None

    def test_multiple_event_types_weighted(self, session: GameSession):
        """Driver with multiple possible events should select one."""
        driver = CampaignDriver(
            driver_id="test_weighted",
            driver_type="test",
            description="Weighted event selection",
            signals=[Signal(condition="true", weight=1.0)],
            possible_events=[
                PossibleEvent(event_type="event_a", probability=0.7),
                PossibleEvent(event_type="event_b", probability=0.3),
            ],
            severity=0.1,
            cooldown_ticks=0,
        )
        session.campaign_driver_manager = CampaignDriverManager([driver], seed=42)

        narrative = session._check_campaign_drivers()
        assert narrative is not None

        last_event = session.world.event_log._events[-1]
        assert last_event.canonical_facts["event_type"] in ("event_a", "event_b")


class TestPressureEventStructure:
    """Test that pressure events have the correct structure."""

    def test_pressure_event_has_correct_fields(self, session: GameSession):
        """Pressure event should have all required fields."""
        driver = CampaignDriver(
            driver_id="struct_test",
            driver_type="test",
            description="Structure test",
            signals=[Signal(condition="true", weight=1.0)],
            possible_events=[PossibleEvent(event_type="test_event", probability=1.0)],
            severity=0.1,
            cooldown_ticks=0,
        )
        session.campaign_driver_manager = CampaignDriverManager([driver], seed=42)

        session._check_campaign_drivers()

        event = session.world.event_log._events[-1]
        assert event.event_type == EventType.SYSTEM
        assert event.actor_id == "system"
        assert event.source_action_id is None
        assert "campaign_pressure" in event.tags
        assert "压力事件" in event.summary
        assert event.canonical_facts["source"] == "campaign_driver"

    def test_pressure_event_location_is_player_location(self, session: GameSession):
        """Pressure event should be at the player's current location."""
        driver = CampaignDriver(
            driver_id="loc_test",
            driver_type="test",
            description="Location test",
            signals=[Signal(condition="true", weight=1.0)],
            possible_events=[PossibleEvent(event_type="test_event", probability=1.0)],
            severity=0.1,
            cooldown_ticks=0,
        )
        session.campaign_driver_manager = CampaignDriverManager([driver], seed=42)

        session._check_campaign_drivers()

        event = session.world.event_log._events[-1]
        player = session.world.state.get_entity(session.player_id)
        assert event.location_id == player.location_id
