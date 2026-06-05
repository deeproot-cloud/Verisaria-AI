"""Step 2c: the EngineSession protocol facade — submit a Command, get back the
event stream + a player-perceivable WorldSnapshot (the contract a TUI/Godot drives)."""
from __future__ import annotations

import json

from verisaria.protocol.engine_session import EngineSession
from verisaria import protocol as P
from verisaria.engine.schemas import ParsedIntent, ActionType, CommitmentLevel, Action

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
    assert "队长布兰" in names and "哨兵伏斯" in names  # pack-declared display names
    assert any(w.var_id == "refugees_admitted" for w in res.snapshot.world_vars)
    # the whole result is JSON-serializable (Godot IPC)
    json.dumps(res.to_dict())


def test_snapshot_carries_scene_framing(tmp_path):
    """The focus panel «处境» needs the location description (loader → LocationState
    → snapshot) and the pack's central tension — both player-perceivable framing."""
    snap = _es(tmp_path).snapshot()
    assert "门楼" in snap.location.name
    assert snap.location.description and "箭垛" in snap.location.description
    assert snap.central_tension and "猜忌" in snap.central_tension


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


def test_submit_streaming_delivers_events_live(tmp_path):
    """A TUI needs events AS THEY FIRE (not buffered to the end). submit_streaming
    pushes each Event to on_event live and returns the final snapshot; the normal
    buffered sink is restored afterward."""
    es = _es(tmp_path)
    es.game.intent_parser.parse = lambda raw_text, **kw: ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw_text,
        intent_type=ActionType.SPEECH, actor_id="player_001",
        target_id="npc.captain_brann", content="你好，队长。", modifiers={},
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw_text, timestamp=0,
    )
    live: list = []
    snap = es.submit_streaming(P.SubmitInput("对队长布兰说：你好。"), on_event=live.append)

    assert any(isinstance(e, P.PlayerSpoke) for e in live)
    assert any(isinstance(e, P.TickAdvanced) for e in live)
    assert snap.tick > 0
    # the live sink was temporary — engine restored to its prior sink afterward
    assert es.game._event_sink is not live.append


def test_submit_streaming_surfaces_feedback_as_notice(tmp_path):
    """A turn that produces only a feedback string (parse failed / can't do that /
    clarification re-prompt) and no in-world events must surface that string as a
    Notice — otherwise the TUI is silent (real bug: a plea produced 0 visible output)."""
    from verisaria.engine.intent import ClarificationRequest

    es = _es(tmp_path)
    es.game.intent_parser.parse = lambda raw_text, **kw: ClarificationRequest(
        request_id="c", original_input=raw_text,
        clarifying_question="我没理解你的意思", ambiguity_type="parse_failed",
    )
    seen: list = []
    es.submit_streaming(P.SubmitInput("队长，开城门吧"), on_event=seen.append)

    notices = [e for e in seen if isinstance(e, P.Notice)]
    assert notices and "我没理解" in notices[0].text


def test_substantive_turn_emits_no_notice(tmp_path):
    """A normal turn (events fired) must NOT also emit a Notice for its narrative."""
    es = _es(tmp_path)
    es.game.intent_parser.parse = lambda raw_text, **kw: ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw_text,
        intent_type=ActionType.MOVEMENT, actor_id="player_001", target_id=None,
        content=None, modifiers={"to_location": "refugee_camp"},
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw_text, timestamp=0,
    )
    seen: list = []
    es.submit_streaming(P.SubmitInput("我去难民营"), on_event=seen.append)
    assert not any(isinstance(e, P.Notice) for e in seen)


def test_bystander_appraisal_weighted_below_the_addressee(tmp_path):
    """Playability audit #5: a perceived player action moves the ADDRESSEE's stance
    at full weight, but a mere bystander (an NPC it wasn't directed at) forms a far
    weaker impression — else a co-located guard never spoken to accrues the most
    suspicion of anyone."""
    from verisaria.engine.appraisal import AppraisalResult
    es = _es(tmp_path)
    g = es.game
    captured: dict = {}
    orig = g.relationship_store.apply
    g.relationship_store.apply = lambda npc, tgt, deltas, tick: (
        captured.__setitem__(npc, dict(deltas)), orig(npc, tgt, deltas, tick))[1]

    target, bystander = "npc.captain_brann", "npc.sentry_voss"
    jobs = [(target, "e1"), (bystander, "e1")]
    results = [AppraisalResult(belief="b", deltas={"suspicion": 0.2}),
               AppraisalResult(belief="b", deltas={"suspicion": 0.2})]
    g._apply_appraisal_results(jobs, results, tick=1, action_target=target)

    assert captured[target]["suspicion"] == 0.2                       # addressee: full weight
    assert captured[bystander]["suspicion"] == 0.2 * g._BYSTANDER_APPRAISAL_WEIGHT
    assert captured[bystander]["suspicion"] < captured[target]["suspicion"]


def test_pack_opening_drives_present_at_open(tmp_path, monkeypatch):
    """Playability audit #7: a pack's player_agenda_template.current_drives is
    loaded into the agenda at open, so the player starts with their stated goal
    instead of drives=[]."""
    from verisaria.engine.campaign_loader import CampaignLoader
    real = CampaignLoader.load_or_fallback

    def _wrapped(path):
        pack, state, validation = real(path)
        pack.player_agenda_template = {"current_drives": [
            {"id": "drive_x", "label": "查清真相", "strength": 0.6, "source": "player_declared"}]}
        return pack, state, validation

    monkeypatch.setattr(CampaignLoader, "load_or_fallback", staticmethod(_wrapped))
    snap = _es(tmp_path).snapshot()
    assert "查清真相" in snap.agenda.drives


def test_no_disembodied_npc_speech_on_move_tick(tmp_path):
    """Playability audit #2: on a tick the player moves, an NPC at the location
    just left must NOT emit NpcSpoke — mid-transit the player hears no one (P1.4),
    else the left-behind NPC speaks disembodied at the destination."""
    es = _es(tmp_path)  # frostgate: player at gatehouse with brann + voss
    g = es.game
    g.intent_parser.parse = lambda raw_text, **kw: ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw_text,
        intent_type=ActionType.MOVEMENT, actor_id="player_001", target_id=None,
        content=None, modifiers={"to_location": "barracks"},
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw_text, timestamp=0,
    )
    # brann (left behind at the gatehouse) tries to chatter on the move tick
    g._collect_npc_actions = lambda **kw: [Action(
        action_id="a", source_intent_id=None, actor_id="npc.captain_brann",
        action_type=ActionType.SPEECH, target_id=None, params={"content": "站住！"},
        zone_id=None, conversation_session_id=None, tick=g.world.state.tick,
    )]
    res = es.submit(P.SubmitInput("我去兵营"))
    assert any(isinstance(e, P.PlayerMoved) for e in res.events)
    assert not any(isinstance(e, P.NpcSpoke) for e in res.events)  # no disembodied line


def test_snapshot_surfaces_world_clock(tmp_path):
    """The snapshot carries a player-perceivable time-of-day + clock label derived
    from the world clock (default opening 08:00 → 第1天 08:00, 晨)."""
    snap = _es(tmp_path).snapshot()
    assert snap.clock == "第1天 08:00"
    assert "晨" in snap.time_of_day
    assert snap.weather and any(ord(ch) > 0x2600 for ch in snap.weather)  # a sky with a glyph


def test_snapshot_surfaces_dynamic_var_and_pending_process(tmp_path):
    """The world snapshot distinguishes a GM-spawned dynamic var and exposes a
    maturing offscreen process (pending_in), so the TUI can show ⏳."""
    from verisaria.engine.schemas import NewPrerequisite, ProcessStarted
    es = _es(tmp_path)
    g = es.game
    g._register_dynamic_prerequisite(NewPrerequisite(var_id="council_ok", set_by=["npc.captain_brann"]))
    g.world.state.tick = 5
    g._begin_pending_process(ProcessStarted(var_id="council_ok", matures_in_ticks=3))  # due 8

    wv = {w.var_id: w for w in es.snapshot().world_vars}
    assert wv["council_ok"].dynamic is True
    assert wv["council_ok"].pending_in == 3            # 8 - 5
    assert wv["refugees_admitted"].dynamic is False    # pack var
    assert wv["refugees_admitted"].pending_in is None
