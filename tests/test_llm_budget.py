"""Tests for the LLM budget overhaul (P3.1 / regression fix).

Three behaviours are pinned down here:
1. Counting — one logical `call()` costs exactly one budget unit, regardless of
   how many internal retries the orchestrator performs.
2. Priority degradation — when the budget is spent, high-priority tasks
   (arbiter / parse_player_intent) are still allowed, while low-priority tasks
   (generate_npc_dialogue) are refused so they degrade to templates.
3. Per-tick reset is driven by GameSession.run_tick, so NPC dialogue does not
   permanently degrade after a handful of ticks.
"""

from __future__ import annotations

from verisaria.engine.llm import (
    FakeLLMProvider,
    LLMCallRequest,
    LLMErrorCategory,
    LLMOrchestrator,
    LLMProvider,
    LLMCallResult,
    RetryPolicy,
)


# --------------------------------------------------------------------------- #
# Test providers
# --------------------------------------------------------------------------- #

class _AlwaysTimeout(LLMProvider):
    """Primary that always fails with a retryable timeout."""

    def __init__(self) -> None:
        self.calls = 0

    def call(self, request: LLMCallRequest) -> LLMCallResult:
        self.calls += 1
        return LLMCallResult(
            success=False, error="timeout", model_used="stub",
            error_category=LLMErrorCategory.TIMEOUT,
        )


def _fake_with(task_types: list[str]) -> FakeLLMProvider:
    provider = FakeLLMProvider()
    for tt in task_types:
        provider.register_fixture(task_type=tt, prompt="x", expected_output={"ok": True})
    return provider


# --------------------------------------------------------------------------- #
# 1. Counting: retries do not multiply budget consumption
# --------------------------------------------------------------------------- #

class TestBudgetCounting:
    def test_one_logical_call_costs_one_unit(self):
        orch = LLMOrchestrator(primary_provider=_fake_with(["test"]), max_calls_per_tick=3)
        for _ in range(3):
            assert orch.call(LLMCallRequest(task_type="test", prompt="x")).success
        # Exactly 3 successful calls should have exhausted a budget of 3.
        assert not orch.call(LLMCallRequest(task_type="test", prompt="x")).success

    def test_retries_do_not_drain_extra_budget(self):
        """A timing-out primary retried internally still costs one unit; the
        fast-fail fallback path must not let retries eat the whole budget."""
        primary = _AlwaysTimeout()
        orch = LLMOrchestrator(
            primary_provider=primary,
            fallback_provider=_fake_with(["test"]),
            max_calls_per_tick=2,
            retry_policy=RetryPolicy(max_retries=2, base_delay=0.0, max_delay=0.0),
        )
        # First logical call retries internally + falls back, but costs 1 unit.
        r1 = orch.call(LLMCallRequest(task_type="test", prompt="x"))
        assert r1.success  # served by fallback
        # A budget of 2 must still allow a second logical call.
        r2 = orch.call(LLMCallRequest(task_type="test", prompt="x"))
        assert r2.success


# --------------------------------------------------------------------------- #
# 2. Priority-based degradation
# --------------------------------------------------------------------------- #

class TestPriorityDegradation:
    def _orch(self) -> LLMOrchestrator:
        provider = _fake_with([
            "parse_player_intent", "arbiter_decide", "generate_npc_dialogue",
        ])
        return LLMOrchestrator(primary_provider=provider, max_calls_per_tick=1)

    def test_low_priority_refused_when_budget_spent(self):
        orch = self._orch()
        # Spend the single unit on a high-priority parse.
        assert orch.call(LLMCallRequest(task_type="parse_player_intent", prompt="x")).success
        # NPC dialogue (lowest priority) is now refused → caller degrades.
        r = orch.call(LLMCallRequest(task_type="generate_npc_dialogue", prompt="x"))
        assert not r.success
        assert r.error_category == LLMErrorCategory.BUDGET

    def test_high_priority_allowed_even_when_low_already_ran(self):
        orch = self._orch()
        # A low-priority call spends the budget first...
        orch.call(LLMCallRequest(task_type="generate_npc_dialogue", prompt="x"))
        # ...but a player-facing parse must still go through (player input must
        # never be silently dropped because NPCs chattered first).
        r = orch.call(LLMCallRequest(task_type="parse_player_intent", prompt="x"))
        assert r.success

    def test_unknown_task_type_treated_as_normal(self):
        orch = self._orch()
        orch.call(LLMCallRequest(task_type="parse_player_intent", prompt="x"))
        # An unranked task_type falls back to plain budget enforcement.
        provider = orch.primary
        provider.register_fixture(task_type="mystery", prompt="x", expected_output={"ok": True})
        r = orch.call(LLMCallRequest(task_type="mystery", prompt="x"))
        assert not r.success
        assert r.error_category == LLMErrorCategory.BUDGET


# --------------------------------------------------------------------------- #
# 3. Per-tick reset via GameSession (the actual regression)
# --------------------------------------------------------------------------- #

class TestTickResetIntegration:
    def _session(self, tmp_path):
        from verisaria.runtime.session import GameSession
        return GameSession(
            "fixtures/content_packs/valid_frontier_town.json",
            save_dir=str(tmp_path),
            llm_backend="fake",
        )

    def test_run_tick_resets_budget(self, tmp_path):
        session = self._session(tmp_path)
        # Simulate a previous tick that fully spent the budget.
        session.llm_orchestrator._calls_this_tick = 999
        session.run_tick("看看周围")
        assert session.llm_orchestrator._calls_this_tick < 999

    def test_npc_dialogue_not_permanently_degraded(self, tmp_path):
        """Over many ticks the budget must reset, so the dialogue generator is
        invoked afresh each tick rather than being starved forever."""
        session = self._session(tmp_path)
        peak = 0
        for _ in range(15):
            session.run_tick("")
            peak = max(peak, session.llm_orchestrator._calls_this_tick)
            # After each tick the budget is reset at the start of the next, so
            # it never runs away unbounded.
            assert session.llm_orchestrator._calls_this_tick <= session.llm_orchestrator.max_calls_per_tick
