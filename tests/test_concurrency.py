"""PLAY-1 latency: per-tick NPC LLM calls run concurrently for REAL network
providers, while FakeLLM / replay stays deterministic serial (A10 red line)."""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

from verisaria.engine.llm import (
    FakeLLMProvider, LLMCallRequest, LLMCallResult, LLMOrchestrator,
    LLMProvider, OllamaProvider, OpenAICompatibleProvider,
)
from verisaria.engine.npc_runtime import NPCActionGenerator


class _StubGen:
    def generate_line(self, **kwargs):
        return "LIVE-LINE"


def test_pick_speech_prefers_precomputed_line_cache():
    """The serial NPC loop reads precomputed (concurrent) lines from the cache
    instead of blocking on a live call — keeping the loop (rng/seq) unchanged."""
    gen = NPCActionGenerator(seed=42, dialogue_generator=_StubGen())
    gen._line_cache = {"npc.x": "CACHED-LINE"}
    assert gen._pick_speech_content("npc.x", memory_store=None) == "CACHED-LINE"
    # No cache → falls back to a live call.
    gen._line_cache = None
    assert gen._pick_speech_content("npc.x", memory_store=None) == "LIVE-LINE"


def test_concurrent_dialogue_precompute_reaches_narrative(tmp_path):
    """End-to-end: with a concurrent provider, NPC dialogue is precomputed and
    the cached line surfaces in the narrative (on- and off-screen dialogue no
    longer blocks the player's critical path serially)."""
    from verisaria.runtime.session import GameSession
    from verisaria.engine.schemas import ActionType, CommitmentLevel, ParsedIntent

    s = GameSession("fixtures/content_packs/frostgate_watchpost.json", save_dir=str(tmp_path))
    s.llm_provider.supports_concurrency = True  # force the precompute path
    s.npc_dialogue_generator.generate_line = (
        lambda **k: "PRECOMPUTED-LINE" if k.get("npc_id") == "npc.sentry_voss" else None
    )
    s.intent_parser.parse = lambda raw_text, **kw: ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw_text,
        intent_type=ActionType.SPEECH, actor_id="player_001", target_id="npc.sentry_voss",
        content=raw_text, commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw_text, timestamp=0,
    )
    out = s.run_tick("你好")
    assert "PRECOMPUTED-LINE" in out


def test_streaming_renders_addressed_npc_and_skips_it_in_narrative(tmp_path):
    """The addressed NPC's reply streams to the sink live, and is then omitted
    from the assembled narrative so it isn't printed twice."""
    from verisaria.runtime.session import GameSession
    from verisaria.engine.schemas import ActionType, CommitmentLevel, ParsedIntent

    s = GameSession("fixtures/content_packs/frostgate_watchpost.json", save_dir=str(tmp_path))
    s.llm_provider.supports_concurrency = True
    s.llm_provider.supports_streaming = True
    s.intent_parser.parse = lambda raw_text, **kw: ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw_text,
        intent_type=ActionType.SPEECH, actor_id="player_001", target_id="npc.sentry_voss",
        content=raw_text, commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw_text, timestamp=0,
    )
    LINE = "这事我做不了主"

    def fake_stream(**k):
        sink = k.get("on_delta")
        for tok in ["这", "事", "我", "做不了主"]:
            if sink:
                sink(tok)
        return LINE

    s.npc_dialogue_generator.generate_line_stream = fake_stream
    received: list[str] = []
    s._stream_sink = received.append

    out = s.run_tick("难民也是人")

    assert LINE in "".join(received)            # streamed live to the sink
    assert s._streamed_npc == "npc.sentry_voss"
    assert LINE not in out                       # NOT re-printed in the narrative


def test_intent_parse_progress_emitted_for_real_provider_only(tmp_path):
    """During the (slow) intent parse a real provider emits a progress message so
    the screen isn't frozen; FakeLLM (instant) emits nothing — no flicker."""
    from verisaria.runtime.session import GameSession
    from verisaria.engine.schemas import ActionType, CommitmentLevel, ParsedIntent

    PACK = "fixtures/content_packs/frostgate_watchpost.json"

    def look(raw):
        return ParsedIntent(
            intent_id="i", source="natural_language", raw_text=raw,
            intent_type=ActionType.LOOK, actor_id="player_001",
            commitment=CommitmentLevel.COMMITTED, confidence=0.9,
            performed_content=raw, timestamp=0,
        )

    s = GameSession(PACK, save_dir=str(tmp_path))  # FakeLLM
    s.intent_parser.parse = lambda raw_text, **kw: look(raw_text)
    got: list[str] = []
    s._progress_sink = got.append
    s.run_tick("看看四周")
    assert got == []

    s2 = GameSession(PACK, save_dir=str(tmp_path))
    s2.llm_provider.supports_concurrency = True  # treat as a slow real provider
    s2.intent_parser.parse = lambda raw_text, **kw: look(raw_text)
    got2: list[str] = []
    s2._progress_sink = got2.append
    s2.run_tick("看看四周")
    assert got2


def test_provider_concurrency_flags():
    """A10: only real network providers opt into concurrency; FakeLLM stays
    serial so replays are deterministic."""
    assert FakeLLMProvider().supports_concurrency is False
    assert OllamaProvider().supports_concurrency is True
    assert OpenAICompatibleProvider(model="m", api_key="k").supports_concurrency is True


class _StubProvider(LLMProvider):
    supports_concurrency = True

    def call(self, request: LLMCallRequest) -> LLMCallResult:
        time.sleep(0.003)  # widen the read-modify-write window on the budget
        return LLMCallResult(success=True, data={}, model_used="stub")


def test_orchestrator_budget_is_thread_safe():
    """Concurrent calls must not lose budget increments (race on the counter)."""
    orch = LLMOrchestrator(primary_provider=_StubProvider(), max_calls_per_tick=10_000)
    n = 300
    with ThreadPoolExecutor(max_workers=32) as ex:
        results = list(ex.map(
            lambda _: orch.call(LLMCallRequest(task_type="generate_npc_dialogue", prompt="x")),
            range(n),
        ))
    assert all(r.success for r in results)
    assert orch._calls_this_tick == n
