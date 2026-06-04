"""Tests for LLM error recovery: retry, fallback, error categorisation (P2-2)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from verisaria.engine.llm import (
    FakeLLMProvider,
    LLMCallRequest,
    LLMCallResult,
    LLMErrorCategory,
    LLMOrchestrator,
    RetryPolicy,
)


# ---------------------------------------------------------------------------
# Error categorisation
# ---------------------------------------------------------------------------

class TestErrorCategorisation:
    def test_fake_provider_missing_fixture_is_unknown(self):
        provider = FakeLLMProvider()
        result = provider.call(LLMCallRequest(task_type="x", prompt="y"))
        assert not result.success
        assert result.error_category == LLMErrorCategory.UNKNOWN

    def test_fake_provider_validation_failure(self):
        provider = FakeLLMProvider()
        from pydantic import BaseModel

        class DummySchema(BaseModel):
            name: str

        provider.register_fixture(
            task_type="test", prompt="bad", expected_output={"invalid": True}
        )
        result = provider.call(
            LLMCallRequest(task_type="test", prompt="bad", schema_model=DummySchema)
        )
        assert not result.success
        assert result.error_category == LLMErrorCategory.VALIDATION


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

class TestRetryPolicy:
    def test_should_retry_timeout(self):
        policy = RetryPolicy()
        assert policy.should_retry(LLMErrorCategory.TIMEOUT, attempt=0)
        assert policy.should_retry(LLMErrorCategory.TIMEOUT, attempt=1)
        assert not policy.should_retry(LLMErrorCategory.TIMEOUT, attempt=2)

    def test_should_retry_connection(self):
        policy = RetryPolicy()
        assert policy.should_retry(LLMErrorCategory.CONNECTION, attempt=0)

    def test_should_retry_validation(self):
        # VALIDATION is retryable: a nondeterministic model occasionally emits an
        # illegal field against a large schema (ArbiterOutput); a fresh sample passes.
        policy = RetryPolicy()
        assert policy.should_retry(LLMErrorCategory.VALIDATION, attempt=0)
        assert not policy.should_retry(LLMErrorCategory.VALIDATION, attempt=2)  # capped

    def test_should_retry_parse(self):
        # PARSE is retryable: nondeterministic models intermittently emit malformed
        # JSON; a fresh sample usually parses (better than failing the turn).
        policy = RetryPolicy()
        assert policy.should_retry(LLMErrorCategory.PARSE, attempt=0)
        assert not policy.should_retry(LLMErrorCategory.PARSE, attempt=2)  # capped

    def test_should_not_retry_none(self):
        policy = RetryPolicy()
        assert not policy.should_retry(None, attempt=0)

    def test_delay_exponential(self):
        policy = RetryPolicy(base_delay=1.0)
        assert policy.delay_for(0) == 1.0
        assert policy.delay_for(1) == 2.0
        assert policy.delay_for(2) == 4.0

    def test_delay_capped(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=5.0)
        assert policy.delay_for(10) == 5.0


# ---------------------------------------------------------------------------
# Orchestrator retry
# ---------------------------------------------------------------------------

class TestOrchestratorRetry:
    def test_retry_on_transient_failure_then_success(self):
        primary = MagicMock()
        # First two calls fail with timeout, third succeeds
        primary.call.side_effect = [
            LLMCallResult(
                success=False,
                error="timeout",
                error_category=LLMErrorCategory.TIMEOUT,
            ),
            LLMCallResult(
                success=False,
                error="timeout",
                error_category=LLMErrorCategory.TIMEOUT,
            ),
            LLMCallResult(success=True, data={"ok": True}),
        ]

        orch = LLMOrchestrator(
            primary_provider=primary,
            retry_policy=RetryPolicy(max_retries=2, base_delay=0.0),
            max_calls_per_tick=10,
        )

        result = orch.call(LLMCallRequest(task_type="test", prompt="x"))
        assert result.success
        assert result.data == {"ok": True}
        assert primary.call.call_count == 3

    def test_retries_then_falls_back_on_validation_failure(self):
        # VALIDATION is now retryable (a fresh sample often passes the schema); after
        # exhausting retries it still falls back rather than failing the turn.
        primary = MagicMock()
        primary.call.return_value = LLMCallResult(
            success=False,
            error="bad schema",
            error_category=LLMErrorCategory.VALIDATION,
        )

        fallback = FakeLLMProvider()
        fallback.register_fixture(
            task_type="test", prompt="x", expected_output={"from": "fallback"}
        )

        orch = LLMOrchestrator(
            primary_provider=primary,
            fallback_provider=fallback,
            retry_policy=RetryPolicy(max_retries=2, base_delay=0.0),
            max_calls_per_tick=10,
        )

        result = orch.call(LLMCallRequest(task_type="test", prompt="x"))
        assert result.success
        assert result.data["from"] == "fallback"
        # Primary retried (1 + 2 retries), then fallback
        assert primary.call.call_count == 3

    def test_exhaust_retries_then_fallback(self):
        primary = MagicMock()
        primary.call.return_value = LLMCallResult(
            success=False,
            error="connection refused",
            error_category=LLMErrorCategory.CONNECTION,
        )

        fallback = FakeLLMProvider()
        fallback.register_fixture(
            task_type="test", prompt="x", expected_output={"from": "fallback"}
        )

        orch = LLMOrchestrator(
            primary_provider=primary,
            fallback_provider=fallback,
            retry_policy=RetryPolicy(max_retries=1, base_delay=0.0),
            max_calls_per_tick=10,
        )

        result = orch.call(LLMCallRequest(task_type="test", prompt="x"))
        assert result.success
        assert result.data["from"] == "fallback"
        # Primary: initial + 1 retry = 2 calls
        assert primary.call.call_count == 2

    def test_budget_category_returned(self):
        provider = FakeLLMProvider()
        provider.register_fixture(
            task_type="test", prompt="x", expected_output={"ok": True}
        )
        orch = LLMOrchestrator(
            primary_provider=provider, max_calls_per_tick=1
        )
        # Exhaust budget
        orch.call(LLMCallRequest(task_type="test", prompt="x"))
        result = orch.call(LLMCallRequest(task_type="test", prompt="x"))
        assert not result.success
        assert result.error_category == LLMErrorCategory.BUDGET
