"""Tests for /map command."""

from __future__ import annotations

import pytest

from verisaria.runtime.session import GameSession
from verisaria.engine.world import Connection, EntityState, LocationState, ZoneState


@pytest.fixture
def minimal_pack_path(tmp_path_factory):
    return "fixtures/content_packs/minimal_valid.json"


@pytest.fixture
def frontier_town_path():
    return "fixtures/content_packs/valid_frontier_town.json"


class TestMapCommand:
    def test_map_no_player(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        # Remove player to test edge case
        session.world.state.entities.pop("player_001", None)
        result = session._handle_command("/map")
        assert "nowhere" in result

    def test_map_basic_location(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        result = session._handle_command("/map")
        assert "town_square" in result
        assert "★ You" in result
        assert "npc.guard_b" in result

    def test_map_shows_zones(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        result = session._handle_command("/map")
        assert "[Zones]" in result
        assert "center" in result

    def test_map_shows_other_locations(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        result = session._handle_command("/map")
        assert "tavern" in result or "[Other Locations]" in result

    def test_map_with_connections(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        # Add a second location with connection
        session.world.state.locations["forest"] = LocationState(
            location_id="forest",
            zones={"entrance": ZoneState(zone_id="entrance", location_id="forest")},
            connected_locations=["void"],
            connections=[Connection(to_location="void", distance="adjacent", description="back to void")],
        )
        # Update void to connect back
        session.world.state.locations["void"].connected_locations = ["forest"]
        session.world.state.locations["void"].connections = [
            Connection(to_location="forest", distance="near", description="into the woods")
        ]
        # Move player to void
        player = session.world.state.get_entity("player_001")
        if player:
            player.location_id = "void"

        result = session._handle_command("/map")
        assert "[Exits]" in result
        assert "forest" in result
        assert "into the woods" in result

    def test_map_with_zone_occupants(self, minimal_pack_path, tmp_path):
        session = GameSession(minimal_pack_path, save_dir=str(tmp_path))
        # Add zone with occupant
        zone = ZoneState(zone_id="main", location_id="void", occupant_ids=["player_001"])
        session.world.state.locations["void"].zones["main"] = zone
        player = session.world.state.get_entity("player_001")
        if player:
            player.zone_id = "main"

        result = session._handle_command("/map")
        assert "main" in result
        assert "(1 occupants)" in result or "occupants" in result

    def test_map_formatter_colors(self, frontier_town_path, tmp_path):
        session = GameSession(frontier_town_path, save_dir=str(tmp_path))
        result = session._handle_command("/map")
        formatted = session.formatter.format_command_output(result, cmd="map")
        # In ANSI mode, expect some color codes
        assert "town_square" in formatted
