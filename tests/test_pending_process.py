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


def test_pending_process_survives_save_load(tmp_path):
    g = _session(tmp_path)
    _dyn(g, "proc_v")
    g.world.state.tick = 2
    g._begin_pending_process(ProcessStarted(var_id="proc_v", matures_in_ticks=3))  # due 5
    save_id = g._handle_command("/save").replace("Saved: ", "").strip()

    g2 = _session(tmp_path)
    g2._handle_command(f"/load {save_id}")
    assert g2._world_var_specs.get("proc_v", {}).get("pending_until") == 5
