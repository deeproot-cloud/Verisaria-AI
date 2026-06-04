"""Channel-C observability: each world-change adjudication traces the arbiter
verdict, any established fact, and whether the flag flipped — so the fact ledger
is visible on real runs (CLI/TUI --log). Logger only; never a player-facing event."""
from __future__ import annotations

import logging
from types import SimpleNamespace

from verisaria.runtime.session import GameSession
from verisaria.engine.validator import ValidatedOutcome
from verisaria.engine.schemas import ArbiterOutput

PACK = "fixtures/content_packs/frostgate_watchpost.json"
VAR = "refugees_admitted"
AUTH = "npc.captain_brann"


def _capture_channel_c(tmp_path, outcome: str, fact: str | None):
    records: list[str] = []

    class _H(logging.Handler):
        def emit(self, r): records.append(r.getMessage())

    logger = logging.getLogger("verisaria.channel_c")
    h = _H(); logger.addHandler(h); logger.setLevel(logging.INFO)
    try:
        g = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
        ao = ArbiterOutput(arbiter_id="t", source_action_id="a", outcome=outcome,  # type: ignore[arg-type]
                           reason="测试理由", confidence=0.5, established_fact=fact)
        g.arbiter.arbitrate = lambda action, world: ValidatedOutcome(
            accepted=True, arbiter_output=ao,
            accepted_state_changes=[], rejected_state_changes=[])
        g._handle_world_change_request(
            SimpleNamespace(params={"content": "请开城门"}, raw_text="请开城门"), VAR, AUTH)
    finally:
        logger.removeHandler(h)
    return records


def test_channel_c_logs_verdict_and_established_fact(tmp_path):
    msgs = _capture_channel_c(tmp_path, "partial_success", "守军愿松口，条件是先安置老弱")
    joined = "\n".join(msgs)
    assert "world-change" in joined and VAR in joined and "partial_success" in joined
    assert "守军愿松口，条件是先安置老弱" in joined        # the established fact is traced
    assert "ledger" in joined                              # current ledger snapshot logged


def test_channel_c_logs_no_flip_marker_on_partial(tmp_path):
    msgs = _capture_channel_c(tmp_path, "partial_success", "条件未满足")
    assert not any("⟳FLIP" in m for m in msgs)             # partial_success never flips


def test_channel_c_marks_fallback_verdict(tmp_path):
    """A deterministic fallback (LLM unavailable) is flagged, so a mid-negotiation
    fallback isn't mistaken for a real refusal."""
    records: list[str] = []

    class _H(logging.Handler):
        def emit(self, r): records.append(r.getMessage())

    logger = logging.getLogger("verisaria.channel_c")
    h = _H(); logger.addHandler(h); logger.setLevel(logging.INFO)
    try:
        g = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
        ao = ArbiterOutput(arbiter_id="t", source_action_id="a", outcome="failure",
                           reason="LLM 不可用，按默认规则处理。", confidence=0.5, is_fallback=True)
        g.arbiter.arbitrate = lambda action, world: ValidatedOutcome(
            accepted=True, arbiter_output=ao,
            accepted_state_changes=[], rejected_state_changes=[])
        g._handle_world_change_request(
            SimpleNamespace(params={"content": "请开城门"}, raw_text="请开城门"), VAR, AUTH)
    finally:
        logger.removeHandler(h)
    assert any("FALLBACK" in m for m in records)


def test_channel_c_logs_collateral_world_changes(tmp_path):
    """A success that flips a SECOND world var (collateral) is visible in the log,
    not a mystery in the final /world."""
    from verisaria.engine.schemas import StateChange
    records: list[str] = []

    class _H(logging.Handler):
        def emit(self, r): records.append(r.getMessage())

    logger = logging.getLogger("verisaria.channel_c")
    h = _H(); logger.addHandler(h); logger.setLevel(logging.INFO)
    try:
        g = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
        sc = StateChange(field="world.refugees_admitted", delta=True, reason="附带")
        ao = ArbiterOutput(arbiter_id="t", source_action_id="a", outcome="success",
                           reason="同意", confidence=0.8)
        g.arbiter.arbitrate = lambda action, world: ValidatedOutcome(
            accepted=True, arbiter_output=ao,
            accepted_state_changes=[sc], rejected_state_changes=[])
        g._handle_world_change_request(
            SimpleNamespace(params={"content": "请开城门"}, raw_text="请开城门"), VAR, AUTH)
    finally:
        logger.removeHandler(h)
    assert any("world-changes applied=" in m and "refugees_admitted" in m for m in records)


def test_authority_reply_directive_grounds_npc_against_fabrication(tmp_path):
    """The voiced reply's directive carries the requested var's real state + a
    no-fabrication guardrail, so the NPC can't claim unrealized progress (the
    '我看到了梅档案官的章' contradiction)."""
    g = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
    captured: dict = {}

    def spy(**kw):
        captured["directive"] = kw.get("directive")
        return "恕难从命。"

    g.npc_dialogue_generator.generate_line = spy
    ao = ArbiterOutput(arbiter_id="t", source_action_id="a", outcome="failure",
                       reason="r", confidence=0.5)
    g.arbiter.arbitrate = lambda action, world: ValidatedOutcome(
        accepted=True, arbiter_output=ao,
        accepted_state_changes=[], rejected_state_changes=[])
    g._handle_world_change_request(
        SimpleNamespace(params={"content": "请开城门"}, raw_text="请开城门"), VAR, AUTH)

    d = captured["directive"] or ""
    assert "只陈述你确实知道为真的事" in d        # no-fabrication guardrail
    assert "尚未成立" in d                        # refugees_admitted is currently False


def test_generic_arbiter_path_gates_world_change_on_success(tmp_path):
    """Anti-cheese: the GENERIC arbiter path (_handle_arbiter_action) must drop a
    world.* change on a non-success verdict, like _handle_world_change_request —
    otherwise a failed/partial action could flip a terminal flag."""
    from verisaria.engine.schemas import Action, ActionType, StateChange

    def _run(outcome: str):
        g = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
        sc = StateChange(field=f"world.{VAR}", delta=True, reason="x")
        ao = ArbiterOutput(arbiter_id="t", source_action_id="a", outcome=outcome,
                           reason="r", confidence=0.5)
        g.arbiter.arbitrate = lambda action, world: ValidatedOutcome(
            accepted=True, arbiter_output=ao, accepted_state_changes=[sc], rejected_state_changes=[])
        action = Action(action_id="a", actor_id="player_001", action_type=ActionType.SOCIAL,
                        target_id=AUTH, tick=1, params={"verb": "persuade", "content": "x"})
        g._handle_arbiter_action(action)
        return g.world.state.world_vars.get(VAR)

    assert _run("failure") is False           # non-success → flag NOT flipped
    assert _run("partial_success") is False   # partial → still not flipped
    assert _run("success") is True            # success → applied


def test_set_by_matches_npc_id_or_authority_role(tmp_path):
    """A world var's set_by may name an NPC by id OR by its authority role, and the
    id is matched tolerant of a missing npc. prefix (the GM sometimes drops it)."""
    g = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
    assert g._authority_npc_for(["npc.captain_brann"]) == "npc.captain_brann"
    assert g._authority_npc_for(["captain_brann"]) == "npc.captain_brann"  # missing prefix
    assert g._authority_npc_for(["no_such_role"]) is None
    # the matcher itself: prefixed / bare id / authority role all match
    assert g._set_by_matches("npc.clinician_oro", "memory_authority", ["clinician_oro"])
    assert g._set_by_matches("npc.clinician_oro", "memory_authority", ["npc.clinician_oro"])
    assert g._set_by_matches("npc.clinician_oro", "memory_authority", ["memory_authority"])
    assert not g._set_by_matches("npc.clinician_oro", "memory_authority", ["someone_else"])


def test_channel_c_logs_proposed_prereq_that_was_dropped(tmp_path):
    """When the GM proposes a new_prerequisite that the engine drops (dup/cap/bad
    id), the log says so — distinguishing 'LLM didn't emit' from 'engine dropped'."""
    from verisaria.engine.schemas import NewPrerequisite
    records: list[str] = []

    class _H(logging.Handler):
        def emit(self, r): records.append(r.getMessage())

    logger = logging.getLogger("verisaria.channel_c")
    h = _H(); logger.addHandler(h); logger.setLevel(logging.INFO)
    try:
        g = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
        ao = ArbiterOutput(arbiter_id="t", source_action_id="a", outcome="partial_success",
                           reason="r", confidence=0.5,
                           new_prerequisite=NewPrerequisite(var_id="纯中文"))  # bad id → dropped
        g.arbiter.arbitrate = lambda action, world: ValidatedOutcome(
            accepted=True, arbiter_output=ao,
            accepted_state_changes=[], rejected_state_changes=[])
        g._handle_world_change_request(
            SimpleNamespace(params={"content": "请开城门"}, raw_text="请开城门"), VAR, AUTH)
    finally:
        logger.removeHandler(h)
    assert any("NOT registered" in m for m in records)


def test_follow_up_routes_for_pack_var_mid_negotiation(tmp_path):
    """A keyword-less follow-up to an authority whose var already carries ledger
    facts (a commitment in progress) still routes to Channel-C — so it can become
    process_started/success instead of decaying into chatter."""
    from verisaria.engine.schemas import Action, ActionType

    g = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
    captain = g.world.state.get_entity(AUTH)
    captain.location_id = g.world.state.get_entity(g.player_id).location_id  # co-locate
    # a content with NO request_keyword for refugees_admitted
    action = Action(action_id="a", actor_id=g.player_id, action_type=ActionType.SPEECH,
                    target_id=AUTH, tick=1, params={"content": "那这事现在到哪一步了，你倒是给个准话。"})
    assert g._world_change_request(action) is None        # no keyword, no ledger → no route

    g.fact_ledger.add("队长已同意启动安置流程", regarding=VAR, npc_id=AUTH, tick=0)
    assert g._world_change_request(action) == (VAR, AUTH)  # mid-negotiation → routes


def test_world_change_trigger_accepts_set_by_npc_id(tmp_path):
    """The TRIGGER layer (_world_change_request), not just the read layer, must
    accept a set_by named by npc id — even for an NPC with no `authority` role."""
    from verisaria.engine.schemas import Action, ActionType

    g = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
    voss = g.world.state.get_entity("npc.sentry_voss")
    voss.attributes = dict(voss.attributes or {}); voss.attributes.pop("authority", None)
    # co-locate voss with the player (Channel C requires presence)
    player = g.world.state.get_entity(g.player_id)
    voss.location_id = player.location_id
    # declare a var voss can set, named BY ID (no authority role on voss)
    g._world_var_specs["test_gate"] = {
        "label": "测试", "initial": False, "mutable": True,
        "set_by": ["npc.sentry_voss"], "request_keywords": ["开门"],
    }
    action = Action(action_id="a", actor_id=g.player_id, action_type=ActionType.SPEECH,
                    target_id="npc.sentry_voss", tick=1, params={"content": "请开门"})
    assert g._world_change_request(action) == ("test_gate", "npc.sentry_voss")
