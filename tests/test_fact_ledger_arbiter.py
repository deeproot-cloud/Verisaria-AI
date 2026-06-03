"""Arbiter ↔ FactLedger wiring (Channel C): partial_success establishes an
intermediate fact that the engine remembers and re-serves to later arbitration —
without ever flipping the terminal flag on a non-success. Verifies the PLUMBING
(record + re-surface), not the LLM's judgement. See emergent-fact-ledger.md."""
from __future__ import annotations

from types import SimpleNamespace

from verisaria.runtime.session import GameSession
from verisaria.engine.validator import ValidatedOutcome
from verisaria.engine.schemas import ArbiterOutput

PACK = "fixtures/content_packs/frostgate_watchpost.json"
VAR = "refugees_admitted"
AUTH = "npc.captain_brann"
FACT = "守军愿松口，条件是先安置老弱病残"


def _session(tmp_path) -> GameSession:
    return GameSession(PACK, save_dir=str(tmp_path), llm_backend="fake")


def _stub_arbiter(g: GameSession, outcome: str, fact: str | None) -> None:
    ao = ArbiterOutput(
        arbiter_id="t", source_action_id="a", outcome=outcome,  # type: ignore[arg-type]
        reason="r", confidence=0.5, established_fact=fact,
    )
    vo = ValidatedOutcome(
        accepted=True, arbiter_output=ao,
        accepted_state_changes=[], rejected_state_changes=[],
    )
    g.arbiter.arbitrate = lambda action, world: vo


def _request(g: GameSession) -> str:
    action = SimpleNamespace(params={"content": "请开城门放难民进来"}, raw_text="请开城门")
    return g._handle_world_change_request(action, VAR, AUTH)


def test_partial_success_records_fact_without_flipping_flag(tmp_path):
    g = _session(tmp_path)
    before = g.world.state.world_vars.get(VAR)
    _stub_arbiter(g, "partial_success", FACT)
    _request(g)

    assert g.world.state.world_vars.get(VAR) == before          # invariant: no flip
    assert FACT in [f.text for f in g.fact_ledger.relevant(VAR)]  # but remembered
    assert g.fact_ledger.relevant(VAR)[0].npc_id == AUTH


def test_recorded_fact_resurfaces_in_next_arbiter_context(tmp_path):
    g = _session(tmp_path)
    _stub_arbiter(g, "partial_success", FACT)
    _request(g)

    rows = {r["var_id"]: r for r in g._world_vars_for_arbiter()}
    assert FACT in (rows[VAR].get("established_facts") or [])


def test_failure_and_redirect_record_nothing(tmp_path):
    g = _session(tmp_path)
    _stub_arbiter(g, "failure", "should be ignored")
    _request(g)
    assert g.fact_ledger.all() == []


def test_partial_success_with_blank_fact_records_nothing(tmp_path):
    g = _session(tmp_path)
    _stub_arbiter(g, "partial_success", "")  # arbiter declined to state a fact
    _request(g)
    assert g.fact_ledger.all() == []
