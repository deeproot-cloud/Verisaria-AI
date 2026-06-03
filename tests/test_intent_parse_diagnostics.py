"""A parse failure must trace its real cause (so a --log run diagnoses why natural
language got the opaque '我没理解', rather than just that it did)."""
from __future__ import annotations

import logging

from verisaria.engine.intent import IntentParser, ClarificationRequest
from verisaria.engine.llm import LLMCallResult, LLMErrorCategory
from verisaria.engine.world import WorldState


class _FailingLLM:
    def call(self, request):
        return LLMCallResult(
            success=False, error="schema validation failed: missing intent_type",
            error_category=LLMErrorCategory.VALIDATION,
        )


def test_parse_failure_is_logged_with_cause(caplog):
    parser = IntentParser(llm_orchestrator=_FailingLLM())
    world = WorldState(tick=0)

    with caplog.at_level(logging.WARNING, logger="verisaria.intent"):
        result = parser.parse("去 mnemonic_clinic", actor_id="player_001", tick=1, world=world)

    assert isinstance(result, ClarificationRequest)        # still degrades gracefully
    assert result.ambiguity_type == "parse_failed"
    msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert "去 mnemonic_clinic" in msgs                     # the input that failed
    assert "schema validation failed" in msgs              # the real cause, not the opaque msg
