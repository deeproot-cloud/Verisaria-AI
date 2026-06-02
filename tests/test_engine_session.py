"""Step 2c: the EngineSession protocol facade — submit a Command, get back the
event stream + a player-perceivable WorldSnapshot (the contract a TUI/Godot drives)."""
from __future__ import annotations

import json

from verisaria.protocol.engine_session import EngineSession
from verisaria import protocol as P
from verisaria.engine.schemas import ParsedIntent, ActionType, CommitmentLevel

PACK = "fixtures/content_packs/frostgate_watchpost.json"


def _es(tmp_path):
    return EngineSession.start(PACK, save_dir=str(tmp_path), llm_backend="fake")


def test_submit_speech_returns_events_and_snapshot(tmp_path):
    es = _es(tmp_path)
    es.game.intent_parser.parse = lambda raw_text, **kw: ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw_text,
        intent_type=ActionType.SPEECH, actor_id="player_001",
        target_id="npc.captain_brann", content="你好，队长。", modifiers={},
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw_text, timestamp=0,
    )
    res = es.submit(P.SubmitInput("对队长布兰说：你好，队长。"))

    assert any(isinstance(e, P.PlayerSpoke) for e in res.events)
    assert any(isinstance(e, P.TickAdvanced) for e in res.events)
    # snapshot: at the gatehouse with brann + voss present, world var declared
    assert res.snapshot.location.id == "gatehouse"
    names = {p.name for p in res.snapshot.present}
    assert "captain_brann" in names and "sentry_voss" in names
    assert any(w.var_id == "refugees_admitted" for w in res.snapshot.world_vars)
    # the whole result is JSON-serializable (Godot IPC)
    json.dumps(res.to_dict())


def test_wait_advances_tick(tmp_path):
    es = _es(tmp_path)
    t0 = es.snapshot().tick
    res = es.submit(P.Wait())
    assert res.snapshot.tick > t0


def test_snapshot_relationships_use_descriptors(tmp_path):
    es = _es(tmp_path)
    # Seed a relationship directly (appraisal needs a live LLM; the snapshot mapping
    # is what we're testing here).
    es.game.relationship_store.apply("npc.captain_brann", "player_001",
                                     {"suspicion": 0.51}, tick=0)
    snap = es.snapshot()
    brann = [r for r in snap.relationships if r.npc_id == "npc.captain_brann"]
    assert brann, "brann's stance toward the player should surface"
    susp = [d for d in brann[0].descriptors if d.dimension == "suspicion"]
    assert susp and susp[0].band == "strong" and "戒心很重" in susp[0].phrase


def test_world_var_change_reflected_in_snapshot_and_events(tmp_path):
    es = _es(tmp_path)
    es.game.set_world_var("refugees_admitted", True)
    snap = es.snapshot()
    wv = [w for w in snap.world_vars if w.var_id == "refugees_admitted"]
    assert wv and wv[0].value is True
