"""Tests for LLM Orchestrator and Fake Provider."""

import pytest
from pydantic import BaseModel

from verisaria.engine.llm import FakeLLMProvider, LLMCallRequest, LLMOrchestrator
from verisaria.engine.schemas import ParsedIntent


class TestFakeLLMProvider:
    def test_loads_fixture_from_disk(self):
        provider = FakeLLMProvider(fixtures_root="fixtures")
        result = provider.call(
            LLMCallRequest(
                task_type="parse_player_intent",
                prompt="看看周围",
                schema_model=ParsedIntent,
            )
        )
        assert result.success
        assert result.data is not None
        assert result.data["raw_text"] == "看看周围"
        assert result.data["intent_type"] == "look"

    def test_missing_fixture_returns_error(self):
        provider = FakeLLMProvider(fixtures_root="fixtures")
        result = provider.call(
            LLMCallRequest(task_type="parse_player_intent", prompt="不存在的输入")
        )
        assert not result.success
        assert "No fixture found" in (result.error or "")

    def test_register_fixture_programmatically(self):
        provider = FakeLLMProvider(fixtures_root="fixtures")
        provider.register_fixture(
            task_type="arbiter_decide",
            prompt="偷短剑",
            expected_output={
                "arbiter_id": "arb_test",
                "source_action_id": "act_test",
                "outcome": "success",
                "reason": "没人看见",
                "evidence_refs": [],
                "state_changes_proposed": [],
                "confidence": 0.95,
            },
        )
        result = provider.call(
            LLMCallRequest(task_type="arbiter_decide", prompt="偷短剑")
        )
        assert result.success
        assert result.data["outcome"] == "success"

    def test_schema_validation_failure(self):
        provider = FakeLLMProvider(fixtures_root="fixtures")
        # Register a fixture with invalid data for ParsedIntent schema
        provider.register_fixture(
            task_type="bad_intent",
            prompt="bad",
            expected_output={"invalid_key": True},
        )
        result = provider.call(
            LLMCallRequest(
                task_type="bad_intent",
                prompt="bad",
                schema_model=ParsedIntent,
            )
        )
        assert not result.success
        assert "schema validation" in (result.error or "").lower()


class TestLLMOrchestrator:
    def test_budget_enforcement(self):
        provider = FakeLLMProvider()
        provider.register_fixture(
            task_type="test", prompt="x", expected_output={"ok": True}
        )
        orch = LLMOrchestrator(primary_provider=provider, max_calls_per_tick=2)

        r1 = orch.call(LLMCallRequest(task_type="test", prompt="x"))
        r2 = orch.call(LLMCallRequest(task_type="test", prompt="x"))
        r3 = orch.call(LLMCallRequest(task_type="test", prompt="x"))

        assert r1.success
        assert r2.success
        assert not r3.success
        assert "budget exceeded" in (r3.error or "").lower()

    def test_reset_tick_budget(self):
        provider = FakeLLMProvider()
        provider.register_fixture(
            task_type="test", prompt="x", expected_output={"ok": True}
        )
        orch = LLMOrchestrator(primary_provider=provider, max_calls_per_tick=1)

        r1 = orch.call(LLMCallRequest(task_type="test", prompt="x"))
        assert r1.success

        orch.reset_tick_budget()
        r2 = orch.call(LLMCallRequest(task_type="test", prompt="x"))
        assert r2.success

    def test_fallback_on_failure(self):
        primary = FakeLLMProvider()
        fallback = FakeLLMProvider()
        fallback.register_fixture(
            task_type="test", prompt="x", expected_output={"from": "fallback"}
        )
        orch = LLMOrchestrator(
            primary_provider=primary,
            fallback_provider=fallback,
        )
        result = orch.call(LLMCallRequest(task_type="test", prompt="x"))
        assert result.success
        assert result.data["from"] == "fallback"
