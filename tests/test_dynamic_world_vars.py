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
    assert "已满足的前置不得再加码" in prompt                    # (d) no soft re-escalation (F1)


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


def test_keyword_request_phrased_as_question_still_routes(tmp_path):
    """Audit 5 #1: a real request to the authority that HITS a keyword routes even
    when phrased as a question ('你敢不敢把门开了？') — it was silently dropped as
    discussion before. A topic question with no keyword stays discussion."""
    g = _session(tmp_path)
    voss = g.world.state.get_entity("npc.sentry_voss")
    player = g.world.state.get_entity(g.player_id)
    voss.location_id = player.location_id
    g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="door_unbarred", set_by=["npc.sentry_voss"], request_keywords=["开门"]))

    asking = Action(action_id="a", actor_id=g.player_id, action_type=ActionType.SPEECH,
                    target_id="npc.sentry_voss", tick=1,
                    params={"content": "你敢不敢现在就开门？"})  # question form + keyword
    assert g._world_change_request(asking) == ("door_unbarred", "npc.sentry_voss")

    topic = Action(action_id="a", actor_id=g.player_id, action_type=ActionType.SPEECH,
                   target_id="npc.sentry_voss", tick=1,
                   params={"content": "这道门到底卡在哪儿？"})  # question, no keyword
    assert g._world_change_request(topic) is None


def test_statement_leading_request_routes_via_raw_text(tmp_path):
    """Grand-integration: the parser condenses a long '先陈述后请求' utterance and
    paraphrases the keyword out of `content`, so it used to decay to chatter. The
    verbatim keyword survives in raw_text → the request still routes."""
    g = _session(tmp_path)
    brann = g.world.state.get_entity(AUTH)  # watch_authority for refugees_admitted (VAR)
    player = g.world.state.get_entity(g.player_id)
    brann.location_id = player.location_id
    action = SimpleNamespace(
        action_type=ActionType.SPEECH, target_id=AUTH,
        params={"content": "城里断粮了，外头那些人快撑不住了"},          # cleaned — no keyword
        raw_text="队长，城里断粮了，求你放难民进来吧")                    # raw — has "放难民进来"
    assert g._world_change_request(action) == (VAR, AUTH)


def test_player_leverage_over_surfaces_ledger_facts(tmp_path):
    """Audit 5 #2 leverage model: established facts on a var the NPC governs are the
    player's leverage over them (fed to the appraiser); none → empty."""
    g = _session(tmp_path)
    assert g._player_leverage_over(AUTH) == []                # nothing established yet
    g.fact_ledger.add(text="你已查到她私抽热能的账", regarding=VAR, npc_id=AUTH, tick=1)
    assert "你已查到她私抽热能的账" in g._player_leverage_over(AUTH)


def test_unrouted_authority_speech_gets_a_readable_hint(tmp_path):
    """Audit 5 #1b: a colloquial, on-domain address to the authority that didn't
    formalize gets a hint (names the NPC), not a silent dead end."""
    g = _session(tmp_path)
    voss = g.world.state.get_entity("npc.sentry_voss")
    player = g.world.state.get_entity(g.player_id)
    voss.location_id = player.location_id
    g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="door_unbarred", set_by=["npc.sentry_voss"], label="把门打开",
        request_keywords=["开门"]))

    on_topic = Action(action_id="a", actor_id=g.player_id, action_type=ActionType.SPEECH,
                      target_id="npc.sentry_voss", tick=1,
                      params={"content": "你就不能把门那边松松手吗"})  # touches 把门, no keyword
    hint = g._unrouted_authority_hint(on_topic)
    assert hint and "哨兵伏斯" in hint

    off = Action(action_id="a", actor_id=g.player_id, action_type=ActionType.SPEECH,
                 target_id="npc.sentry_voss", tick=1, params={"content": "今天天气不错"})
    assert g._unrouted_authority_hint(off) is None


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


# --- F1: convergence guardrails (playability audit) ---

def test_near_duplicate_prereq_reuses_existing_var(tmp_path):
    """F1#1: the arbiter must not spawn a near-duplicate of an existing var (the
    audit's pump_failure_disclosed_publicly beside pump_failure_disclosed). A new
    prereq whose id derives from an existing one reuses it, folding in keywords."""
    g = _session(tmp_path)
    first = g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="pump_failure_disclosed", label="泵闸故障是否公开",
        set_by=[AUTH], request_keywords=["公开"]))
    assert first == "pump_failure_disclosed"

    dup = g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="pump_failure_disclosed_publicly", label="泵闸故障是否已公开公示",
        set_by=[AUTH], request_keywords=["公示", "广播"]), serves="tow_order_halted")
    assert dup == "pump_failure_disclosed"                          # reused, not new
    assert "pump_failure_disclosed_publicly" not in g._world_var_specs
    assert "公示" in g._world_var_specs["pump_failure_disclosed"]["request_keywords"]


def test_subdivision_no_longer_hard_capped(tmp_path):
    """F1 third run: the hard per-terminal count cap was reverted — it only blocked
    registration (not the arbiter's prose demands) and over-blocked a genuinely
    distinct prerequisite. Distinct prereqs for one terminal now all register;
    escalation is bounded by sufficiency, not a count."""
    g = _session(tmp_path)
    T = VAR
    ids = []
    for vid, kw in [("step_archived", "存档"), ("step_posted", "公示"), ("step_broadcast", "广播")]:
        ids.append(g._register_dynamic_prerequisite(NewPrerequisite(
            var_id=vid, label=vid, set_by=[AUTH], request_keywords=[kw]), serves=T))
    assert all(ids)  # none refused by a count cap


def test_sufficiency_backstop_forces_success_when_all_prereqs_met(tmp_path):
    """F1: once every declared prerequisite of a terminal is genuinely True, a
    reneging arbiter verdict (partial_success) is overridden to success and the
    terminal flips — the world honours its own stated terms (anti-cheese intact:
    the prereqs are really True, no bluff)."""
    g = _session(tmp_path)
    assert g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="report_filed", label="报告已提交", set_by=[AUTH],
        request_keywords=["提交"]), serves=VAR) == "report_filed"
    g.world.state.world_vars["report_filed"] = True            # the prereq is satisfied

    ao = ArbiterOutput(arbiter_id="t", source_action_id="a", outcome="partial_success",
                       reason="还得再走一道审议", confidence=0.5)
    g.arbiter.arbitrate = lambda action, world: ValidatedOutcome(
        accepted=True, arbiter_output=ao, accepted_state_changes=[], rejected_state_changes=[])
    g._handle_world_change_request(
        SimpleNamespace(params={"content": "请照办"}, raw_text="请照办"), VAR, AUTH)
    assert g.world.state.world_vars.get(VAR) is True           # forced success → flipped


def test_sufficiency_backstop_silent_without_met_prereqs(tmp_path):
    """The backstop must NOT fire when a terminal has no prereqs, or they're unmet —
    a bare request still needs a genuine success (anti-cheese preserved)."""
    g = _session(tmp_path)
    ao = ArbiterOutput(arbiter_id="t", source_action_id="a", outcome="partial_success",
                       reason="r", confidence=0.5)
    g.arbiter.arbitrate = lambda action, world: ValidatedOutcome(
        accepted=True, arbiter_output=ao, accepted_state_changes=[], rejected_state_changes=[])

    # (1) no declared prereqs at all → not forced
    g._handle_world_change_request(
        SimpleNamespace(params={"content": "请照办"}, raw_text="请照办"), VAR, AUTH)
    assert g.world.state.world_vars.get(VAR) in (False, None)

    # (2) a declared prereq that is NOT satisfied → still not forced
    g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="report_filed", set_by=[AUTH]), serves=VAR)
    g._handle_world_change_request(
        SimpleNamespace(params={"content": "请照办"}, raw_text="请照办"), VAR, AUTH)
    assert g.world.state.world_vars.get(VAR) in (False, None)


def test_arbiter_context_lists_satisfied_prerequisites(tmp_path):
    """The prompt-side half: a terminal's met prerequisites are surfaced to the
    arbiter (so rule (d) can forbid re-escalation)."""
    g = _session(tmp_path)
    g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="report_filed", label="报告已提交", set_by=[AUTH]), serves=VAR)
    g.world.state.world_vars["report_filed"] = True
    entry = next(e for e in g._world_vars_for_arbiter() if e["var_id"] == VAR)
    assert entry.get("satisfied_prerequisites") == ["报告已提交"]


def test_distinct_prereqs_for_different_terminals_still_register(tmp_path):
    """The guardrails must not over-merge: unrelated prerequisites still register."""
    g = _session(tmp_path)
    a = g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="witness_testified", label="证人作证", set_by=[AUTH], request_keywords=["作证"]), serves="t1")
    b = g._register_dynamic_prerequisite(NewPrerequisite(
        var_id="document_filed", label="文件归档", set_by=[AUTH], request_keywords=["归档"]), serves="t2")
    assert a == "witness_testified" and b == "document_filed"
