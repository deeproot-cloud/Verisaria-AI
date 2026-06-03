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


def test_set_by_matches_npc_id_or_authority_role(tmp_path):
    """A world var's set_by may name an NPC by id OR by its authority role."""
    g = GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")
    # frostgate's captain has authority for the gate var; match by id either way
    assert g._authority_npc_for(["npc.captain_brann"]) == "npc.captain_brann"
    # an unknown role resolves to nobody
    assert g._authority_npc_for(["no_such_role"]) is None


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
