"""Step 2b: GameSession emits structured protocol.Event to an attached event_sink,
additively — the legacy string return is unchanged (so the CLI keeps working)."""
from __future__ import annotations

from verisaria.runtime.session import GameSession
from verisaria import protocol as P
from verisaria.engine.schemas import ParsedIntent, ActionType, CommitmentLevel

PACK = "fixtures/content_packs/frostgate_watchpost.json"
PLAYER = "player_001"


def _speech(target, content):
    def parse(raw_text, **kw):
        return ParsedIntent(
            intent_id="i", source="natural_language", raw_text=raw_text,
            intent_type=ActionType.SPEECH, actor_id=PLAYER, target_id=target,
            content=content, modifiers={}, commitment=CommitmentLevel.COMMITTED,
            confidence=0.9, performed_content=raw_text, timestamp=0,
        )
    return parse


def _capture(tmp_path):
    s = GameSession(PACK, save_dir=str(tmp_path))
    events: list = []
    s._event_sink = events.append
    return s, events


def test_speech_tick_emits_player_spoke_and_tick_advanced(tmp_path):
    s, events = _capture(tmp_path)
    s.intent_parser.parse = _speech("npc.captain_brann", "你好，队长。")
    out = s.run_tick("对队长布兰说：你好，队长。")

    assert isinstance(out, str)  # legacy return preserved
    assert any(isinstance(e, P.PlayerSpoke) and e.line == "你好，队长。" for e in events)
    assert any(isinstance(e, P.TickAdvanced) for e in events)
    assert any(isinstance(e, P.Narration) for e in events)


def test_movement_tick_emits_player_moved(tmp_path):
    s, events = _capture(tmp_path)

    def parse(raw_text, **kw):
        return ParsedIntent(
            intent_id="i", source="natural_language", raw_text=raw_text,
            intent_type=ActionType.MOVEMENT, actor_id=PLAYER, target_id=None,
            content=None, modifiers={"to_location": "refugee_camp"},
            commitment=CommitmentLevel.COMMITTED, confidence=0.9,
            performed_content=raw_text, timestamp=0,
        )
    s.intent_parser.parse = parse
    s.run_tick("我去难民营。")

    moved = [e for e in events if isinstance(e, P.PlayerMoved)]
    assert moved and moved[0].from_loc == "gatehouse" and moved[0].to_loc == "refugee_camp"


def test_world_var_change_emits_event(tmp_path):
    s, events = _capture(tmp_path)
    assert s.set_world_var("refugees_admitted", True) is True
    ev = [e for e in events if isinstance(e, P.WorldVarChanged)]
    assert ev and ev[0].var_id == "refugees_admitted" and ev[0].value is True
    assert ev[0].label  # carries the human label


def test_no_sink_is_a_noop(tmp_path):
    """Without an event_sink the engine behaves exactly as before (no crash)."""
    s = GameSession(PACK, save_dir=str(tmp_path))
    s.intent_parser.parse = _speech("npc.captain_brann", "嗨")
    assert isinstance(s.run_tick("对布兰说：嗨"), str)
