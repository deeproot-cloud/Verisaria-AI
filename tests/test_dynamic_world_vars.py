"""Dynamic prerequisite vars (P1): the GM (arbiter) may promote an emergent
condition into a first-class world var so the player has a structural path to
satisfy it — anti-cheese intact (created != satisfied; flips only on success).
See docs/design/dynamic-world-model.md."""
from __future__ import annotations

import os
from types import SimpleNamespace

from verisaria.runtime.session import GameSession
from verisaria.engine.validator import ValidatedOutcome
from verisaria.engine.schemas import ArbiterOutput, NewPrerequisite, Action, ActionType

PACK = "fixtures/content_packs/frostgate_watchpost.json"
VAR = "refugees_admitted"
AUTH = "npc.captain_brann"


def _session(tmp_path) -> GameSession:
    return GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")


def _request_with_prereq(g, prereq, outcome="partial_success"):
    ao = ArbiterOutput(arbiter_id="t", source_action_id="a", outcome=outcome,
                       reason="r", confidence=0.5, new_prerequisite=prereq)
    g.arbiter.arbitrate = lambda action, world: ValidatedOutcome(
        accepted=True, arbiter_output=ao,
        accepted_state_changes=[], rejected_state_changes=[])
    g._handle_world_change_request(
        SimpleNamespace(params={"content": "请开城门"}, raw_text="请开城门"), VAR, AUTH)


def test_new_prerequisite_registers_as_dynamic_var_initially_false(tmp_path):
    g = _session(tmp_path)
    _request_with_prereq(g, NewPrerequisite(
        var_id="clinician_cosign_obtained", label="联签已取得",
        set_by=["npc.captain_brann"], request_keywords=["联签"]))

    spec = g._world_var_specs.get("clinician_cosign_obtained")
    assert spec is not None and spec["dynamic"] is True and spec["mutable"] is True
    # created != satisfied — anti-cheese intact
    assert g.world.state.world_vars["clinician_cosign_obtained"] is False


def test_dynamic_var_is_settable_and_flips_only_on_success(tmp_path):
    g = _session(tmp_path)
    _request_with_prereq(g, NewPrerequisite(
        var_id="evidence_secured", label="证据", set_by=["npc.captain_brann"]))
    # the dynamic spec passes the mutability gate, so a real success can flip it
    assert g.set_world_var("evidence_secured", True) is True
    assert g.world.state.world_vars["evidence_secured"] is True


def test_dynamic_var_dedups_and_never_overwrites_pack_var(tmp_path):
    g = _session(tmp_path)
    before = dict(g._world_var_specs[VAR])
    # a prereq colliding with a pack var id must not overwrite it
    assert g._register_dynamic_prerequisite(NewPrerequisite(var_id=VAR, label="X")) is None
    assert g._world_var_specs[VAR] == before
    # registering the same dynamic id twice yields one spec
    sb = ["npc.captain_brann"]
    assert g._register_dynamic_prerequisite(NewPrerequisite(var_id="dup_v", set_by=sb)) == "dup_v"
    assert g._register_dynamic_prerequisite(NewPrerequisite(var_id="dup_v", set_by=sb)) is None


def test_dynamic_var_count_is_capped(tmp_path):
    g = _session(tmp_path)
    for i in range(g._MAX_DYNAMIC_VARS + 5):
        g._register_dynamic_prerequisite(NewPrerequisite(var_id=f"v_{i}", set_by=["npc.captain_brann"]))
    dyn = [s for s in g._world_var_specs.values() if s.get("dynamic")]
    assert len(dyn) == g._MAX_DYNAMIC_VARS


def test_garbage_var_id_is_skipped(tmp_path):
    g = _session(tmp_path)
    sb = ["npc.captain_brann"]
    assert g._register_dynamic_prerequisite(NewPrerequisite(var_id="纯中文", set_by=sb)) is None
    assert g._register_dynamic_prerequisite(NewPrerequisite(var_id="   ", set_by=sb)) is None


def test_phantom_set_by_npc_is_not_registered(tmp_path):
    """A var whose set_by names only non-existent NPCs is a dead end — not registered.
    A mix keeps just the real satisfier(s)."""
    g = _session(tmp_path)
    assert g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="union_pause_order_received", set_by=["npc.union_steward"])) is None
    assert "union_pause_order_received" not in g._world_var_specs

    vid = g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="mixed_v", set_by=["npc.union_steward", "npc.captain_brann"]))
    assert vid == "mixed_v"
    assert g._world_var_specs["mixed_v"]["set_by"] == ["npc.captain_brann"]


def test_arbiter_prompt_includes_npc_roster():
    from verisaria.engine.arbiter import LLMArbiter, ArbiterContext
    from verisaria.engine.llm import FakeLLMProvider, LLMOrchestrator

    arb = LLMArbiter(llm_orchestrator=LLMOrchestrator(primary_provider=FakeLLMProvider()))
    action = Action(action_id="a", actor_id="player_001", action_type=ActionType.SOCIAL,
                    target_id="npc.x", tick=1, params={"verb": "persuade", "content": "x"})
    ctx = ArbiterContext(
        action=action, actor_attributes={}, target_attributes={}, location_id="l",
        zone_id=None, recent_events=[], world_book_entries=[],
        npc_roster=[{"id": "npc.courier_tamsin", "authority": "union_authority", "location": "valley_platform"}],
    )
    prompt = arb._build_prompt(ctx)
    assert "npc.courier_tamsin" in prompt and "union_authority" in prompt
    assert "set_by 只能从这里选真实 id" in prompt


def test_normalize_collapses_underscore_runs_from_mixed_id(tmp_path):
    g = _session(tmp_path)
    # a mixed cjk/ascii id (LLM slipped) keeps the ascii stem, collapsed cleanly
    assert g._normalize_var_id("union停洗指令变为True") == "union_true"
    assert g._normalize_var_id("Archive  Review—Completed") == "archive_review_completed"


def test_arbiter_prompt_teaches_new_prerequisite_with_example():
    """The prompt must imperatively elicit new_prerequisite (the regression: real
    MiniMax never used it) — with an ascii-snake-case requirement and an example."""
    from verisaria.engine.arbiter import LLMArbiter, ArbiterContext
    from verisaria.engine.llm import FakeLLMProvider, LLMOrchestrator

    arb = LLMArbiter(llm_orchestrator=LLMOrchestrator(primary_provider=FakeLLMProvider()))
    action = Action(action_id="a", actor_id="player_001", action_type=ActionType.SOCIAL,
                    target_id="npc.x", tick=1, params={"verb": "persuade", "content": "暂停"})
    ctx = ArbiterContext(
        action=action, actor_attributes={}, target_attributes={},
        location_id="x", zone_id=None, recent_events=[], world_book_entries=[],
        mutable_world_vars=[{"var_id": "v", "label": "V", "current": False, "set_by": ["r"]}],
    )
    prompt = arb._build_prompt(ctx)
    assert "new_prerequisite" in prompt
    assert "ascii" in prompt and "蛇形" in prompt              # id format required
    assert "union_pause_order_received" in prompt             # worked example present
    assert "不能只把它写进 reason 或 established_fact" in prompt  # division of labour
    # convergence rules (a)+(b)+(c): no infinite prerequisite recursion
    assert "避免前置无限递归" in prompt
    assert "本身能在一两步内被满足" in prompt                    # (a) bottom-out: shallow prereqs only
    assert "做足铺垫时就放行" in prompt                          # (b) ledger sufficiency → success
    assert "不要与自己的立场自相矛盾" in prompt                  # (c) honor the authority's stated condition


def test_arbiter_prompt_carries_target_persona_and_stated_stance():
    """The world-change judge must see the authority's traits AND their own world-book
    stance (their stated release-condition), so it can honor it instead of inventing
    contradictory new prerequisites."""
    from verisaria.engine.arbiter import LLMArbiter, ArbiterContext
    from verisaria.engine.llm import FakeLLMProvider, LLMOrchestrator

    arb = LLMArbiter(llm_orchestrator=LLMOrchestrator(primary_provider=FakeLLMProvider()))
    action = Action(action_id="a", actor_id="player_001", action_type=ActionType.SOCIAL,
                    target_id="npc.kang", tick=1, params={"verb": "persuade", "content": "开闸"})
    ctx = ArbiterContext(
        action=action, actor_attributes={}, target_attributes={"faction": "gate"},
        target_traits=["公道", "讲道理"],
        target_world_book=["闸官老康为人公道：只要有亲历者当面讲清，他就肯开闸放水。"],
        location_id="x", zone_id=None, recent_events=[], world_book_entries=[],
        mutable_world_vars=[{"var_id": "sluice_opened", "label": "开闸", "current": False, "set_by": ["gate"]}],
    )
    prompt = arb._build_prompt(ctx)
    assert "公道" in prompt and "讲道理" in prompt                  # traits
    assert "只要有亲历者当面讲清" in prompt                        # the authority's stated condition
    assert "TA 自己心里清楚的立场" in prompt


def test_dynamic_var_routes_a_request_by_npc_id_set_by(tmp_path):
    """A GM-spawned var with set_by = an npc id + keywords routes a player request
    (relies on the id-or-role trigger fix)."""
    g = _session(tmp_path)
    voss = g.world.state.get_entity("npc.sentry_voss")
    player = g.world.state.get_entity(g.player_id)
    voss.location_id = player.location_id  # co-locate (Channel C requires presence)
    g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="door_unbarred", set_by=["npc.sentry_voss"], request_keywords=["开门"]))
    action = Action(action_id="a", actor_id=g.player_id, action_type=ActionType.SPEECH,
                    target_id="npc.sentry_voss", tick=1, params={"content": "请开门"})
    assert g._world_change_request(action) == ("door_unbarred", "npc.sentry_voss")


def test_dynamic_var_routes_even_with_no_keyword_match(tmp_path):
    """A GM-invented var with empty/mismatched keywords still routes when the player
    addresses its authority NPC substantively — the arbiter then judges relevance."""
    g = _session(tmp_path)
    voss = g.world.state.get_entity("npc.sentry_voss")
    player = g.world.state.get_entity(g.player_id)
    voss.location_id = player.location_id
    g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="statement_filed", set_by=["npc.sentry_voss"]))  # NO keywords
    action = Action(action_id="a", actor_id=g.player_id, action_type=ActionType.SPEECH,
                    target_id="npc.sentry_voss", tick=1,
                    params={"content": "请你把那份声明签了交给我。"})
    assert g._world_change_request(action) == ("statement_filed", "npc.sentry_voss")


def test_pack_var_still_needs_keyword_match(tmp_path):
    """The relaxed routing is scoped to dynamic vars: a pack var with no keyword
    match (and no dynamic var for that NPC) still doesn't route — behavior preserved."""
    g = _session(tmp_path)
    captain = g.world.state.get_entity("npc.captain_brann")
    player = g.world.state.get_entity(g.player_id)
    captain.location_id = player.location_id
    action = Action(action_id="a", actor_id=g.player_id, action_type=ActionType.SPEECH,
                    target_id="npc.captain_brann", tick=1,
                    params={"content": "今天天气真好啊。"})
    assert g._world_change_request(action) is None


def test_dynamic_var_survives_save_load(tmp_path):
    g = _session(tmp_path)
    g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="seal_verified", label="封存令已核验", set_by=["npc.captain_brann"]))
    g.world.state.world_vars["seal_verified"] = True  # progressed
    msg = g._handle_command("/save")
    save_id = msg.replace("Saved: ", "").strip()

    g2 = _session(tmp_path)
    assert "seal_verified" not in g2._world_var_specs   # not from the pack
    g2._handle_command(f"/load {save_id}")
    assert g2._world_var_specs.get("seal_verified", {}).get("dynamic") is True
    assert g2.world.state.world_vars.get("seal_verified") is True   # value restored
