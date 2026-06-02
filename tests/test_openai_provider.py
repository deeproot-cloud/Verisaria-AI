"""Tests for OpenAICompatibleProvider.

One provider for ANY OpenAI-compatible /chat/completions endpoint (OpenAI,
MiniMax, Together, Groq, local vLLM, ...). A vendor like MiniMax is just
configuration (base_url + model + api_key) — never a dedicated subclass.
"""
from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

from verisaria.runtime.session import GameSession
from verisaria.engine.llm import LLMCallRequest, OpenAICompatibleProvider
from verisaria.engine.schemas import ParsedIntent

MINIMAL_PACK = "fixtures/content_packs/minimal_valid.json"


def _chat_response(content: str) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": content}}]}
    ).encode("utf-8")


def _mock_urlopen(m, body: bytes) -> None:
    m.return_value.__enter__ = MagicMock(
        return_value=MagicMock(read=MagicMock(return_value=body))
    )
    m.return_value.__exit__ = MagicMock(return_value=False)


class TestOpenAICompatibleProviderUnit:
    def _provider(self) -> OpenAICompatibleProvider:
        return OpenAICompatibleProvider(
            model="MiniMax-M2.5-highspeed",
            api_key="sk-test",
            base_url="https://api.minimaxi.com/v1",
        )

    def test_call_success(self):
        content = json.dumps({
            "intent_id": "int_1", "source": "natural_language", "raw_text": "look around",
            "intent_type": "look", "actor_id": "player_001", "commitment": "committed",
            "confidence": 1.0, "timestamp": 1,
        })
        with patch("urllib.request.urlopen") as m:
            _mock_urlopen(m, _chat_response(content))
            res = self._provider().call(LLMCallRequest(
                task_type="parse_player_intent", prompt="Parse: look around",
                schema_model=ParsedIntent,
            ))
        assert res.success is True
        assert res.data is not None
        assert res.model_used == "MiniMax-M2.5-highspeed"

    def test_extra_params_are_merged_into_request_body(self):
        """Vendor-specific params (e.g. MiniMax `thinking`) ride on the generic
        provider via config — not a subclass."""
        provider = OpenAICompatibleProvider(
            model="MiniMax-M2.7-highspeed", api_key="sk-test",
            base_url="https://api.minimaxi.com/v1",
            extra_params={"thinking": {"type": "disabled"}, "max_completion_tokens": 1024},
        )
        with patch("urllib.request.urlopen") as m:
            _mock_urlopen(m, _chat_response('{"line": "hi"}'))
            provider.call(LLMCallRequest(task_type="t", prompt="x"))
            req = m.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        assert body["thinking"] == {"type": "disabled"}
        assert body["max_completion_tokens"] == 1024

    def test_request_targets_chat_completions_with_auth_and_json_mode(self):
        with patch("urllib.request.urlopen") as m:
            _mock_urlopen(m, _chat_response('{"line": "hi"}'))
            self._provider().call(LLMCallRequest(
                task_type="t", prompt="say hi", schema_model=ParsedIntent,
            ))
            req = m.call_args[0][0]
        assert req.full_url == "https://api.minimaxi.com/v1/chat/completions"
        assert req.get_header("Authorization") == "Bearer sk-test"
        body = json.loads(req.data.decode("utf-8"))
        assert body["model"] == "MiniMax-M2.5-highspeed"
        assert body["messages"][0]["role"] == "user"
        # JSON mode requested when a schema is expected.
        assert body["response_format"] == {"type": "json_object"}

    def test_connection_error_is_categorized(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            res = self._provider().call(LLMCallRequest(task_type="t", prompt="hi"))
        assert res.success is False
        assert res.error_category is not None

    def test_schema_validation_failure(self):
        with patch("urllib.request.urlopen") as m:
            _mock_urlopen(m, _chat_response('{"intent_type": "look"}'))  # missing fields
            res = self._provider().call(LLMCallRequest(
                task_type="t", prompt="x", schema_model=ParsedIntent,
            ))
        assert res.success is False
        assert "Schema validation failed" in (res.error or "")

    def test_unexpected_response_shape_is_handled(self):
        with patch("urllib.request.urlopen") as m:
            _mock_urlopen(m, json.dumps({"error": "nope"}).encode("utf-8"))
            res = self._provider().call(LLMCallRequest(task_type="t", prompt="x"))
        assert res.success is False


class TestStreaming:
    def _provider(self) -> OpenAICompatibleProvider:
        return OpenAICompatibleProvider(
            model="MiniMax-M2", api_key="sk-test",
            base_url="https://api.minimaxi.com/v1",
            extra_params={"thinking": {"type": "disabled"}},
        )

    def test_supports_streaming_flag(self):
        assert self._provider().supports_streaming is True
        from verisaria.engine.llm import FakeLLMProvider
        assert FakeLLMProvider().supports_streaming is False

    def test_call_stream_suppresses_think_block(self):
        """A reasoning model may emit <think>…</think> even with thinking
        'disabled'; those tokens must NOT stream to the player — only the answer."""
        sse = [
            b'data: {"choices":[{"delta":{"content":"<think>"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"\xe6\x8e\xa8\xe7\x90\x86\xe8\xbf\x87\xe7\xa8\x8b"}}]}\n',  # 推理过程
            b'data: {"choices":[{"delta":{"content":"</think>"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"\xe7\x9c\x9f\xe6\xad\xa3"}}]}\n',  # 真正
            b'data: {"choices":[{"delta":{"content":"\xe7\x9a\x84\xe5\x8f\xb0\xe8\xaf\x8d"}}]}\n',  # 的台词
            b"data: [DONE]\n",
        ]
        deltas: list[str] = []
        with patch("urllib.request.urlopen") as m:
            m.return_value.__enter__ = MagicMock(return_value=iter(sse))
            m.return_value.__exit__ = MagicMock(return_value=False)
            res = self._provider().call_stream(
                LLMCallRequest(task_type="generate_npc_dialogue", prompt="x"),
                on_delta=deltas.append,
            )
        streamed = "".join(deltas)
        assert "推理过程" not in streamed          # reasoning never shown to player
        assert streamed == "真正的台词"             # only the answer streamed
        assert res.raw_response == "真正的台词"

    def test_call_stream_unwraps_fenced_json_without_leaking_it(self):
        """Playtest B5: the model sometimes wraps a plain-line request in a fenced
        JSON object (```json {"line":"…"}```); the raw fence leaked to the player.
        The stream must unwrap to the inner line — never showing braces/fences."""
        sse = [
            b'data: {"choices":[{"delta":{"content":"```json\\n"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"{\\"line\\": \\""}}]}\n',
            b'data: {"choices":[{"delta":{"content":"\xe9\x82\xa3\xe5\xb8\xae\xe4\xba\xba"}}]}\n',  # 那帮人
            b'data: {"choices":[{"delta":{"content":"\xe6\x83\xb3\xe5\xb9\xb2\xe4\xbb\x80\xe4\xb9\x88"}}]}\n',  # 想干什么
            b'data: {"choices":[{"delta":{"content":"\\"}\\n```"}}]}\n',
            b"data: [DONE]\n",
        ]
        deltas: list[str] = []
        with patch("urllib.request.urlopen") as m:
            m.return_value.__enter__ = MagicMock(return_value=iter(sse))
            m.return_value.__exit__ = MagicMock(return_value=False)
            res = self._provider().call_stream(
                LLMCallRequest(task_type="generate_npc_dialogue", prompt="x"),
                on_delta=deltas.append,
            )
        streamed = "".join(deltas)
        assert "```" not in streamed and "{" not in streamed and "line" not in streamed
        assert streamed == "那帮人想干什么"
        assert res.raw_response == "那帮人想干什么"

    def test_call_stream_unwraps_bare_json_object(self):
        """A bare {"line":"…"} (no fence) must also unwrap, not leak braces."""
        sse = [
            b'data: {"choices":[{"delta":{"content":"{\\"line\\":\\""}}]}\n',
            b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd\xe5\x90\xa7"}}]}\n',  # 好吧
            b'data: {"choices":[{"delta":{"content":"\\"}"}}]}\n',
            b"data: [DONE]\n",
        ]
        deltas: list[str] = []
        with patch("urllib.request.urlopen") as m:
            m.return_value.__enter__ = MagicMock(return_value=iter(sse))
            m.return_value.__exit__ = MagicMock(return_value=False)
            res = self._provider().call_stream(
                LLMCallRequest(task_type="generate_npc_dialogue", prompt="x"),
                on_delta=deltas.append,
            )
        assert "".join(deltas) == "好吧"
        assert res.raw_response == "好吧"

    def test_call_stream_accumulates_text_and_fires_on_delta(self):
        sse = [
            b'data: {"choices":[{"delta":{"content":"\xe9\x9a\xbe"}}]}\n',  # 难
            b'data: {"choices":[{"delta":{"content":"\xe6\xb0\x91"}}]}\n',  # 民
            b'data: {"choices":[{"delta":{"content":"\xe4\xb9\x9f\xe6\x98\xaf\xe4\xba\xba"}}]}\n',  # 也是人
            b"data: [DONE]\n",
        ]
        deltas: list[str] = []
        with patch("urllib.request.urlopen") as m:
            m.return_value.__enter__ = MagicMock(return_value=iter(sse))
            m.return_value.__exit__ = MagicMock(return_value=False)
            res = self._provider().call_stream(
                LLMCallRequest(task_type="generate_npc_dialogue", prompt="say"),
                on_delta=deltas.append,
            )
            req = m.call_args[0][0]
        assert res.success is True
        assert res.raw_response == "难民也是人"
        # on_delta fired and the streamed text accumulates to the full line (the
        # exact chunk boundaries are an impl detail — a holdback guards markers).
        assert deltas
        assert "".join(deltas) == "难民也是人"
        body = json.loads(req.data.decode("utf-8"))
        assert body["stream"] is True
        # plain-text streaming → no JSON-mode that would fragment as `{"line":...`
        assert "response_format" not in body


class TestMiniMaxIsJustConfig:
    """`--llm minimax` wires the GENERIC provider — no MiniMax-specific class."""

    def test_minimax_backend_builds_openai_compatible_provider(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax")
        s = GameSession(MINIMAL_PACK, save_dir=str(tmp_path), llm_backend="minimax")
        assert isinstance(s.llm_provider, OpenAICompatibleProvider)
        assert s.llm_provider.base_url == "https://api.minimaxi.com/v1"
        # M3 chosen after a head-to-head (cleaner A5 secrecy, engages nuanced
        # prompts, honours thinking:disabled natively, fastest floor).
        assert s.llm_provider.model == "MiniMax-M3"
        # Short structured tasks → thinking off; reasoning_split keeps `content`
        # clean (any reasoning the model still emits goes to a separate field).
        assert s.llm_provider.extra_params.get("thinking") == {"type": "disabled"}
        assert s.llm_provider.extra_params.get("reasoning_split") is True
