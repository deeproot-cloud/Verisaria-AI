"""Streaming dialogue → structured SpeechToken events (the TUI typewriter path).

The engine already streams an addressed NPC's reply token-by-token to the raw CLI
sink; here we assert it ALSO emits protocol ``SpeechToken`` events through the
event sink (gated on a streaming-capable provider), followed by the committing
``NpcSpoke`` — so a TUI/Godot frontend can render the reply as it generates.
"""
from __future__ import annotations

from verisaria.protocol.engine_session import EngineSession
from verisaria import protocol as P
from verisaria.engine.schemas import ParsedIntent, ActionType, CommitmentLevel

PACK = "fixtures/content_packs/frostgate_watchpost.json"
TOKENS = ["你", "说", "得", "在理"]
LINE = "你说得在理"


def _speak_to_captain(es: EngineSession) -> None:
    es.game.llm_provider.supports_streaming = True
    es.game.llm_provider.supports_concurrency = True  # gates _precompute_npc_lines
    es.game.intent_parser.parse = lambda raw_text, **kw: ParsedIntent(
        intent_id="i", source="natural_language", raw_text=raw_text,
        intent_type=ActionType.SPEECH, actor_id="player_001",
        target_id="npc.captain_brann", content=raw_text,
        commitment=CommitmentLevel.COMMITTED, confidence=0.9,
        performed_content=raw_text, timestamp=0,
    )

    def fake_stream(**k):
        sink = k.get("on_delta")
        for tok in TOKENS:
            if sink:
                sink(tok)
        return LINE

    es.game.npc_dialogue_generator.generate_line_stream = fake_stream


def test_submit_streaming_emits_speech_tokens_then_npc_spoke(tmp_path):
    es = EngineSession.start(PACK, save_dir=str(tmp_path), llm_backend="fake")
    _speak_to_captain(es)

    events: list = []
    es.submit_streaming(P.SubmitInput("难民也是人。"), on_event=events.append)

    toks = [e for e in events if isinstance(e, P.SpeechToken)]
    assert [t.token for t in toks] == TOKENS
    assert all(t.npc_id == "npc.captain_brann" for t in toks)

    spoke = [e for e in events
             if isinstance(e, P.NpcSpoke) and e.npc_id == "npc.captain_brann"]
    assert spoke and spoke[0].line == LINE

    # ordering: every token streams BEFORE the line is committed as NpcSpoke
    last_tok = max(i for i, e in enumerate(events) if isinstance(e, P.SpeechToken))
    spoke_idx = next(i for i, e in enumerate(events)
                     if isinstance(e, P.NpcSpoke) and e.npc_id == "npc.captain_brann")
    assert last_tok < spoke_idx


def test_no_speech_tokens_when_provider_cannot_stream(tmp_path):
    """A non-streaming provider (FakeLLM default) emits no SpeechToken — the
    frontend just gets the whole NpcSpoke, no half-built typewriter."""
    es = EngineSession.start(PACK, save_dir=str(tmp_path), llm_backend="fake")
    _speak_to_captain(es)
    es.game.llm_provider.supports_streaming = False  # override the helper

    events: list = []
    es.submit_streaming(P.SubmitInput("难民也是人。"), on_event=events.append)
    assert not any(isinstance(e, P.SpeechToken) for e in events)
