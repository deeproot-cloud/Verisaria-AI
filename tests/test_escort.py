"""P2c slice 1: escort — a co-located NPC, if willing (arbiter-adjudicated, like a
world-change), accompanies the player to a named place, so witness / on-site
prerequisites become reachable. See docs/design/dynamic-world-model.md."""
from __future__ import annotations

from types import SimpleNamespace

from verisaria.runtime.session import GameSession
from verisaria.engine.validator import ValidatedOutcome
from verisaria.engine.schemas import ArbiterOutput, ActionType

PACK = "fixtures/content_packs/frostgate_watchpost.json"  # voss is at the gatehouse with the player


def _session(tmp_path) -> GameSession:
    return GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")


def _speech(content, target="npc.sentry_voss"):
    return SimpleNamespace(action_type=ActionType.SPEECH, target_id=target,
                           params={"content": content}, raw_text=content)


def _stub_arbiter(g, outcome):
    ao = ArbiterOutput(arbiter_id="t", source_action_id="a", outcome=outcome,
                       reason="r", confidence=0.8)
    g.arbiter.arbitrate = lambda action, world: ValidatedOutcome(
        accepted=True, arbiter_output=ao, accepted_state_changes=[], rejected_state_changes=[])


def test_escort_request_detected(tmp_path):
    g = _session(tmp_path)
    assert g._escort_request(_speech("跟我去兵营")) == ("npc.sentry_voss", "barracks")


def test_escort_request_needs_keyword_and_destination(tmp_path):
    g = _session(tmp_path)
    assert g._escort_request(_speech("你信教会那套吗？")) is None   # no escort phrasing
    assert g._escort_request(_speech("跟我走吧")) is None           # no named destination


def test_escort_request_needs_colocation(tmp_path):
    g = _session(tmp_path)
    # hale (军需官) is at the barracks, not co-located with the player at the gatehouse
    assert g._escort_request(_speech("跟我去难民营", target="npc.quartermaster_hale")) is None


def test_escort_success_moves_npc_and_player(tmp_path):
    g = _session(tmp_path)
    _stub_arbiter(g, "success")
    g._handle_escort_request(_speech("跟我去兵营"), "npc.sentry_voss", "barracks")
    assert g.world.state.get_entity("npc.sentry_voss").location_id == "barracks"
    assert g.world.state.get_entity(g.player_id).location_id == "barracks"   # they go together


def test_escort_refusal_moves_no_one(tmp_path):
    g = _session(tmp_path)
    _stub_arbiter(g, "failure")
    before = g.world.state.get_entity("npc.sentry_voss").location_id
    g._handle_escort_request(_speech("跟我去兵营"), "npc.sentry_voss", "barracks")
    assert g.world.state.get_entity("npc.sentry_voss").location_id == before
    assert g.world.state.get_entity(g.player_id).location_id == before


def test_escort_destination_matches_abbreviation(tmp_path):
    """Slice 2: an abbreviated destination resolves (the report's '档案署' vs
    '低温档案署'). Frostgate has no abbreviations, so simulate one."""
    g = _session(tmp_path)
    g.world.state.locations["archive_stack"] = type(
        g.world.state.locations["barracks"])(location_id="archive_stack", name="低温档案署")
    assert g._resolve_destination_in_text("跟我去档案署作证") == "archive_stack"
    assert g._resolve_destination_in_text("跟我去兵营") == "barracks"      # full name still works
    assert g._resolve_destination_in_text("我们随便走走") is None          # no location named


def test_escort_uses_willingness_prompt_not_world_change():
    """Escort is adjudicated as plain willingness (persona + stance), not a
    world-change with prerequisites — the prompt must drop all prerequisite
    machinery that biased escort toward refusal."""
    from verisaria.engine.arbiter import LLMArbiter, ArbiterContext
    from verisaria.engine.llm import FakeLLMProvider, LLMOrchestrator
    from verisaria.engine.schemas import Action

    arb = LLMArbiter(llm_orchestrator=LLMOrchestrator(primary_provider=FakeLLMProvider()))
    action = Action(action_id="a", actor_id="player_001", action_type=ActionType.SPEECH,
                    target_id="npc.x", tick=1, params={"content": "跟我去兵营"})
    ctx = ArbiterContext(
        action=action, actor_attributes={}, target_attributes={"charisma": 0.6},
        target_traits=["热心肠", "乐意帮忙"],
        location_id="x", zone_id=None, recent_events=[], world_book_entries=[],
        escort={"npc_name": "哨兵伏斯", "dest": "兵营", "relationship": {"trust": 0.4}})
    prompt = arb._build_prompt(ctx)
    assert "陪同当事人前往" in prompt and "社交意愿" in prompt
    assert "哨兵伏斯" in prompt and "兵营" in prompt and "trust 0.40" in prompt
    assert "热心肠" in prompt and "乐意帮忙" in prompt   # persona traits reach the judge
    assert "new_prerequisite" not in prompt and "process_started" not in prompt
    assert "可改变的世界状态" not in prompt          # no world-var prerequisite section


def test_handle_escort_passes_stance_and_resets_context(tmp_path):
    g = _session(tmp_path)
    captured: dict = {}

    def fake_arb(action, world):
        captured["escort"] = dict(getattr(world, "escort_request", None) or {})
        ao = ArbiterOutput(arbiter_id="t", source_action_id="a", outcome="failure",
                           reason="r", confidence=0.5)
        return ValidatedOutcome(accepted=True, arbiter_output=ao,
                                accepted_state_changes=[], rejected_state_changes=[])

    g.arbiter.arbitrate = fake_arb
    g._handle_escort_request(_speech("跟我去兵营"), "npc.sentry_voss", "barracks")
    assert captured["escort"]["dest"] == "兵营"               # destination label passed
    assert isinstance(captured["escort"]["relationship"], dict)
    assert getattr(g.world, "escort_request", None) is None    # reset after adjudication


def test_escort_routes_through_run_tick(tmp_path):
    from verisaria.engine.schemas import ParsedIntent, CommitmentLevel
    g = _session(tmp_path)
    _stub_arbiter(g, "success")
    g.intent_parser.parse = lambda raw_text, **kw: ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw_text,
        intent_type=ActionType.SPEECH, actor_id="player_001",
        target_id="npc.sentry_voss", content=raw_text,
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw_text, timestamp=0)
    g.run_tick("跟我去兵营")
    assert g.world.state.get_entity("npc.sentry_voss").location_id == "barracks"


def test_escort_wins_over_world_change_keyword_collision(tmp_path):
    """Grand-integration: a clear escort '跟我去X' must route to escort even when a
    var keyword overlaps the destination wording — world-change used to eat it."""
    from verisaria.engine.schemas import ParsedIntent, CommitmentLevel, NewPrerequisite
    g = _session(tmp_path)
    _stub_arbiter(g, "success")
    # a var sentry_voss governs whose keyword collides with the escort destination "兵营"
    g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="barracks_thing", set_by=["npc.sentry_voss"], request_keywords=["兵营"]))
    g.intent_parser.parse = lambda raw_text, **kw: ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw_text,
        intent_type=ActionType.SPEECH, actor_id="player_001",
        target_id="npc.sentry_voss", content=raw_text,
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw_text, timestamp=0)
    g.run_tick("跟我去兵营")
    # escort won → voss relocated; a world-change verdict would move no one
    assert g.world.state.get_entity("npc.sentry_voss").location_id == "barracks"
