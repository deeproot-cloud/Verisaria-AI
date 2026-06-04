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


def test_orchestrator_retries_parse_failures():
    """A malformed-JSON (PARSE) failure is retried — nondeterministic models often
    recover on a fresh sample, so the turn isn't lost to '我没理解'."""
    from verisaria.engine.llm import LLMOrchestrator, LLMCallResult, LLMErrorCategory, LLMCallRequest

    class _FlakyProvider:
        def __init__(self): self.calls = 0
        def call(self, request):
            self.calls += 1
            if self.calls == 1:
                return LLMCallResult(success=False, error="JSON extraction failed",
                                     error_category=LLMErrorCategory.PARSE)
            return LLMCallResult(success=True, data={"ok": True})

    prov = _FlakyProvider()
    orch = LLMOrchestrator(primary_provider=prov)
    result = orch.call(LLMCallRequest(task_type="parse_player_intent", prompt="x"))
    assert result.success and prov.calls == 2   # retried once, then succeeded


def test_named_third_party_not_flagged_ambiguous():
    """A clearly-named third party inside speech ('当着哨兵伏斯的面') must not steal the
    turn from the addressed NPC; a pronoun stays ambiguous."""
    from verisaria.engine.intent import IntentParser
    from verisaria.engine.llm import LLMOrchestrator, FakeLLMProvider
    from verisaria.runtime.session import GameSession

    g = GameSession("fixtures/content_packs/frostgate_watchpost.json",
                    save_dir="/tmp/vx_intent", llm_backend="fake")
    parser = IntentParser(llm_orchestrator=LLMOrchestrator(primary_provider=FakeLLMProvider()))
    w = g.world.state
    assert parser._uniquely_names_entity("哨兵伏斯", w) is True    # names sentry_voss
    assert parser._uniquely_names_entity("队长布兰", w) is True    # names captain_brann
    assert parser._uniquely_names_entity("他", w) is False        # pronoun stays ambiguous
    assert parser._uniquely_names_entity("某个谁", w) is False     # names nobody
    # location names/ids resolve too (the escort destination friction)
    assert parser._uniquely_names_entity("兵营", w) is True        # barracks display name
    assert parser._uniquely_names_entity("barracks", w) is True    # location id


def test_resolve_target_ref_matches_display_name():
    """A display-name address the LLM left unresolved ('队长布兰') must resolve to the
    NPC — else the ambiguity filter would drop it into a targetless action."""
    from verisaria.engine.intent import IntentParser
    from verisaria.engine.llm import LLMOrchestrator, FakeLLMProvider
    from verisaria.engine.schemas import ParsedIntent, ActionType, CommitmentLevel
    from verisaria.runtime.session import GameSession

    g = GameSession("fixtures/content_packs/frostgate_watchpost.json",
                    save_dir="/tmp/vx_ref", llm_backend="fake")
    parser = IntentParser(llm_orchestrator=LLMOrchestrator(primary_provider=FakeLLMProvider()))
    intent = ParsedIntent(
        intent_id="i", source="natural_language", raw_text="对队长布兰说：你好",
        intent_type=ActionType.SPEECH, actor_id="player_001", target_id=None,
        target_ref="队长布兰", content="你好",
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content="x", timestamp=0)
    out = parser._resolve_target_ref(intent, g.world.state)
    assert out.target_id == "npc.captain_brann"


def test_movement_with_npc_mention_does_not_pop_menu():
    """'去兵营找哨兵伏斯': the NPC mention is dropped, so a valid destination doesn't
    get hijacked into a global clarification menu."""
    from verisaria.engine.intent import IntentParser
    from verisaria.engine.llm import LLMOrchestrator, FakeLLMProvider, LLMCallResult
    from verisaria.engine.schemas import ParsedIntent, ActionType, CommitmentLevel
    from verisaria.runtime.session import GameSession

    g = GameSession("fixtures/content_packs/frostgate_watchpost.json",
                    save_dir="/tmp/vx_mv", llm_backend="fake")
    parser = IntentParser(llm_orchestrator=LLMOrchestrator(primary_provider=FakeLLMProvider()))
    pi = ParsedIntent(
        intent_id="i", source="natural_language", raw_text="去兵营找哨兵伏斯",
        intent_type=ActionType.MOVEMENT, actor_id="player_001", target_id=None,
        modifiers={"to_location": "barracks"}, ambiguities=["哨兵伏斯"],
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content="去兵营找哨兵伏斯", timestamp=0)
    parser.llm.call = lambda req: LLMCallResult(success=True, data=pi.model_dump())

    result = parser.parse("去兵营找哨兵伏斯", actor_id="player_001", tick=1, world=g.world.state)
    assert isinstance(result, ParsedIntent)            # not a clarification menu
    assert result.intent_type.value == "movement"
