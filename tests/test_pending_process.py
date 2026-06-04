"""P2: offscreen process maturation — a GM-initiated process (council review,
application) matures a dynamic var to True after a delay, so chains that bottom out
at 'submitted, waiting' can complete. See docs/design/dynamic-world-model.md."""
from __future__ import annotations

from verisaria.runtime.session import GameSession
from verisaria.engine.schemas import NewPrerequisite, ProcessStarted

PACK = "fixtures/content_packs/frostgate_watchpost.json"
VAR = "refugees_admitted"
SB = ["npc.captain_brann"]


def _session(tmp_path) -> GameSession:
    return GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")


def _dyn(g, var_id):
    g._register_dynamic_prerequisite(NewPrerequisite(var_id=var_id, set_by=SB))


def test_process_matures_only_after_delay(tmp_path):
    g = _session(tmp_path)
    _dyn(g, "council_auth")
    g.world.state.tick = 5
    assert g._begin_pending_process(ProcessStarted(var_id="council_auth", matures_in_ticks=3)) == "council_auth"
    assert g._world_var_specs["council_auth"]["pending_until"] == 8

    g.world.state.tick = 7
    g._advance_pending_processes()
    assert g.world.state.world_vars["council_auth"] is False   # not yet

    g.world.state.tick = 8
    g._advance_pending_processes()
    assert g.world.state.world_vars["council_auth"] is True     # matured
    assert "pending_until" not in g._world_var_specs["council_auth"]


def test_process_started_only_for_existing_dynamic_var(tmp_path):
    g = _session(tmp_path)
    assert g._begin_pending_process(ProcessStarted(var_id="nonexistent")) is None
    assert g._begin_pending_process(ProcessStarted(var_id=VAR)) is None  # pack var, not dynamic


def test_process_delay_is_clamped(tmp_path):
    g = _session(tmp_path)
    _dyn(g, "p")
    g.world.state.tick = 0
    g._begin_pending_process(ProcessStarted(var_id="p", matures_in_ticks=999))
    assert g._world_var_specs["p"]["pending_until"] == g._MAX_PROCESS_TICKS   # capped
    g._begin_pending_process(ProcessStarted(var_id="p", matures_in_ticks=0))
    assert g._world_var_specs["p"]["pending_until"] == 1                      # floored to 1


def test_run_tick_matures_due_process(tmp_path):
    g = _session(tmp_path)
    _dyn(g, "rt_proc")
    g._begin_pending_process(ProcessStarted(var_id="rt_proc", matures_in_ticks=1))
    due = g._world_var_specs["rt_proc"]["pending_until"]
    for _ in range(due + 2):                       # play on; ticks advance
        g.run_tick("")
        if g.world.state.world_vars["rt_proc"]:
            break
    assert g.world.state.world_vars["rt_proc"] is True


def test_process_start_reply_is_announced_to_player(tmp_path):
    """When a process is kicked off, the authority NPC's voiced line must tell the
    player it's underway (the playtest's 'visible dialogue lagged truth' note)."""
    from types import SimpleNamespace
    from verisaria.engine.validator import ValidatedOutcome
    from verisaria.engine.schemas import ArbiterOutput

    g = _session(tmp_path)
    _dyn(g, "council_review")
    captured: dict = {}
    g.npc_dialogue_generator.generate_line = lambda **kw: captured.setdefault("d", kw.get("directive")) or "好。"

    ao = ArbiterOutput(arbiter_id="t", source_action_id="a", outcome="partial_success",
                       reason="r", confidence=0.5,
                       process_started=ProcessStarted(var_id="council_review", matures_in_ticks=3))
    g.arbiter.arbitrate = lambda action, world: ValidatedOutcome(
        accepted=True, arbiter_output=ao, accepted_state_changes=[], rejected_state_changes=[])
    g._handle_world_change_request(
        SimpleNamespace(params={"content": "请你提交理事会审议"}, raw_text="x"), VAR, "npc.captain_brann")

    assert "你已经去办了" in (captured["d"] or "")


def test_pending_process_merges_into_later_pack_var(tmp_path):
    """If a dynamic var with a pending process is later baked into the pack, loading
    an older save must keep its pending_until (the pack spec lacks it) so the process
    still matures — not silently drop it."""
    g = _session(tmp_path)
    _dyn(g, "baked_v")
    g.world.state.tick = 1
    g._begin_pending_process(ProcessStarted(var_id="baked_v", matures_in_ticks=4))  # due 5
    save_id = g._handle_command("/save").replace("Saved: ", "").strip()

    g2 = _session(tmp_path)
    # simulate the author having since declared baked_v in the pack
    g2.pack.world_state_vars = list(getattr(g2.pack, "world_state_vars", []) or []) + [
        {"var_id": "baked_v", "label": "已固化", "initial": False, "mutable": True}]
    g2._handle_command(f"/load {save_id}")
    assert g2._world_var_specs["baked_v"].get("pending_until") == 5   # merged, not lost


def test_pending_process_survives_save_load(tmp_path):
    g = _session(tmp_path)
    _dyn(g, "proc_v")
    g.world.state.tick = 2
    g._begin_pending_process(ProcessStarted(var_id="proc_v", matures_in_ticks=3))  # due 5
    save_id = g._handle_command("/save").replace("Saved: ", "").strip()

    g2 = _session(tmp_path)
    g2._handle_command(f"/load {save_id}")
    assert g2._world_var_specs.get("proc_v", {}).get("pending_until") == 5
