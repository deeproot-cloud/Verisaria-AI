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
    # The dialogue rides PlayerSpoke/NpcSpoke; any Narration event is speech-stripped
    # (movement/look/ambient only) so it never double-prints the spoken lines.
    for e in events:
        if isinstance(e, P.Narration):
            assert "你好，队长。" not in e.text


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
    assert moved and moved[0].from_loc == "门楼" and moved[0].to_loc == "难民营"  # display names


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


def test_relationship_shift_emits_event(tmp_path):
    """Channel A consequence inline: when an appraisal's deltas are applied, the
    engine emits RelationshipShifted (npc + dimension + delta + new descriptor)."""
    from types import SimpleNamespace

    s, events = GameSession(PACK, save_dir=str(tmp_path)), []
    s._event_sink = events.append
    # Bypass the LLM appraisal: feed an already-computed result straight to apply.
    job = ("npc.captain_brann", "evt_1")
    result = SimpleNamespace(deltas={"suspicion": 0.3}, belief="这外来者可疑")
    # brann is the action's addressee → full weight (a bystander would be scaled).
    s._apply_appraisal_results([job], [result], tick=2, action_target="npc.captain_brann")

    shifts = [e for e in events if isinstance(e, P.RelationshipShifted)]
    assert shifts, "a relationship shift should surface as an event"
    sh = shifts[0]
    assert sh.npc_id == "npc.captain_brann" and sh.descriptor.dimension == "suspicion"
    assert abs(sh.delta - 0.3) < 1e-9
    assert sh.descriptor.band in ("slight", "moderate", "strong")  # carries new stance


def test_world_change_request_emits_authority_reply(tmp_path):
    """The Channel-C path (persuade the authority) bypasses _dispatch_player_action,
    so it must emit its own events — else a TUI shows nothing when you plead to the
    captain. Assert the authority's reply (NpcSpoke) + the flip + tick all surface."""
    s = GameSession(PACK, save_dir=str(tmp_path))
    events: list = []
    s._event_sink = events.append
    s.intent_parser.parse = lambda raw_text, **kw: ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw_text,
        intent_type=ActionType.SPEECH, actor_id="player_001",
        target_id="npc.captain_brann", content="开城门，放难民进来。", modifiers={},
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw_text, timestamp=0,
    )
    s.llm_provider.register_fixture(
        task_type="arbiter_decide", prompt="__arb__",
        expected_output={
            "arbiter_id": "a", "source_action_id": "x", "outcome": "success",
            "reason": "队长同意", "evidence_refs": [],
            "state_changes_proposed": [{"field": "world.refugees_admitted", "delta": True, "reason": "ok"}],
            "confidence": 0.9, "narration_hint": "",
        },
    )
    s.run_tick("队长，开城门放难民进来。")

    assert any(isinstance(e, P.NpcSpoke) and e.npc_id == "npc.captain_brann" for e in events)
    assert any(isinstance(e, P.WorldVarChanged) for e in events)   # granted → flipped
    assert any(isinstance(e, P.TickAdvanced) for e in events)
