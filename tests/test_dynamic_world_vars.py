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
    _request_with_prereq(g, NewPrerequisite(var_id="evidence_secured", label="证据"))
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
    assert g._register_dynamic_prerequisite(NewPrerequisite(var_id="dup_v")) == "dup_v"
    assert g._register_dynamic_prerequisite(NewPrerequisite(var_id="dup_v")) is None


def test_dynamic_var_count_is_capped(tmp_path):
    g = _session(tmp_path)
    for i in range(g._MAX_DYNAMIC_VARS + 5):
        g._register_dynamic_prerequisite(NewPrerequisite(var_id=f"v_{i}"))
    dyn = [s for s in g._world_var_specs.values() if s.get("dynamic")]
    assert len(dyn) == g._MAX_DYNAMIC_VARS


def test_garbage_var_id_is_skipped(tmp_path):
    g = _session(tmp_path)
    assert g._register_dynamic_prerequisite(NewPrerequisite(var_id="纯中文")) is None
    assert g._register_dynamic_prerequisite(NewPrerequisite(var_id="   ")) is None


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
