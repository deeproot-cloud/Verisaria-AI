"""Autonomous NPC enter/leave: when an NPC walks into or out of the player's
location on its own, the player perceives it as an ``NpcMoved`` event (the living
world). A5: judged from where the player narrated this tick (viewer_location); a
move the player can't see stays silent, and a player move suppresses the diff
(the next snapshot's «附近» list redraws the scene instead)."""
from __future__ import annotations

from types import SimpleNamespace

from verisaria.runtime.session import GameSession
from verisaria import protocol as P
from verisaria.engine.schemas import (
    Action, ActionType, ParsedIntent, CommitmentLevel,
)

PACK = "fixtures/content_packs/frostgate_watchpost.json"
# player + brann + voss at gatehouse; hale at barracks (connected); kaze at refugee_camp.


def _session(tmp_path):
    g = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
    events: list = []
    g._event_sink = events.append
    return g, events


def _moves(events):
    return [e for e in events if isinstance(e, P.NpcMoved)]


# -- the perception helper, exercised directly --

def test_perimeter_emits_when_npc_leaves_player_location(tmp_path):
    g, events = _session(tmp_path)
    # brann was co-located (gatehouse); it has since walked to the barracks
    g.world.state.get_entity("npc.captain_brann").location_id = "barracks"
    g._emit_npc_perimeter_moves({"npc.captain_brann": "gatehouse"}, "gatehouse")
    mv = _moves(events)
    assert len(mv) == 1
    assert mv[0].npc_id == "npc.captain_brann" and mv[0].name == "队长布兰"
    assert mv[0].to_loc == "兵营"   # display label, not the raw id


def test_perimeter_emits_when_npc_enters_player_location(tmp_path):
    g, events = _session(tmp_path)
    # hale was at the barracks; it has since walked into the gatehouse (with the player)
    g.world.state.get_entity("npc.quartermaster_hale").location_id = "gatehouse"
    g._emit_npc_perimeter_moves({"npc.quartermaster_hale": "barracks"}, "gatehouse")
    mv = _moves(events)
    assert len(mv) == 1 and mv[0].npc_id == "npc.quartermaster_hale"


def test_perimeter_silent_for_moves_the_player_cannot_see(tmp_path):
    g, events = _session(tmp_path)
    # an NPC drifting between two locations that are NOT the player's — unperceived
    g.world.state.get_entity("npc.refugee_kaze").location_id = "barracks"
    g._emit_npc_perimeter_moves({"npc.refugee_kaze": "refugee_camp"}, "gatehouse")
    assert _moves(events) == []


def test_perimeter_silent_without_a_viewer_location(tmp_path):
    g, events = _session(tmp_path)
    g.world.state.get_entity("npc.captain_brann").location_id = "barracks"
    g._emit_npc_perimeter_moves({"npc.captain_brann": "gatehouse"}, None)
    assert _moves(events) == []


# -- wired through a real tick --

def _npc_move_action(g, actor_id: str, to_loc: str) -> Action:
    return Action(
        action_id=f"mv_{g.world.state.tick}", source_intent_id=None, actor_id=actor_id,
        action_type=ActionType.MOVEMENT, target_id=None,
        params={"verb": "go", "to_location": to_loc, "to_zone": None},
        zone_id=None, conversation_session_id=None, tick=g.world.state.tick)


def test_npc_autonomous_move_surfaces_on_a_wait_tick(tmp_path):
    """Player holds still (empty input); brann wanders off to the barracks → the
    player sees brann leave, surfaced as NpcMoved through run_tick."""
    g, events = _session(tmp_path)
    g._collect_npc_actions = lambda **kw: [_npc_move_action(g, "npc.captain_brann", "barracks")]
    g.run_tick("")  # wait
    assert g.world.state.get_entity("npc.captain_brann").location_id == "barracks"
    mv = [e for e in _moves(events) if e.npc_id == "npc.captain_brann"]
    assert mv and mv[0].name == "队长布兰" and mv[0].to_loc == "兵营"


def test_player_move_suppresses_npc_perimeter_diff(tmp_path):
    """When the PLAYER moves, NPC enter/leave is suppressed (the next snapshot's
    «附近» redraws the scene); a brann move the same tick must not also fire."""
    g, events = _session(tmp_path)
    g._collect_npc_actions = lambda **kw: [_npc_move_action(g, "npc.captain_brann", "barracks")]
    g.intent_parser.parse = lambda raw_text, **kw: ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw_text,
        intent_type=ActionType.MOVEMENT, actor_id="player_001", target_id=None,
        content=None, modifiers={"to_location": "barracks"},
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw_text, timestamp=0)
    g.run_tick("去兵营")
    assert g.world.state.get_entity(g.player_id).location_id == "barracks"  # player moved
    assert any(isinstance(e, P.PlayerMoved) for e in events)
    assert _moves(events) == []  # NPC perimeter diff suppressed
