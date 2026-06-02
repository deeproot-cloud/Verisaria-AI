"""Tests for OllamaProvider.

Includes unit tests with mocked urllib and an optional integration test
that calls the real local Ollama instance.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from verisaria.engine.llm import LLMCallRequest, OllamaProvider
from verisaria.engine.schemas import ParsedIntent


# ---------------------------------------------------------------------------
# Unit tests (mocked)
# ---------------------------------------------------------------------------

class TestOllamaProviderUnit:
    def test_extract_json_plain(self):
        raw = '{"intent_type": "look", "actor_id": "player"}'
        data, err = OllamaProvider._extract_json(raw)
        assert err is None
        assert data == {"intent_type": "look", "actor_id": "player"}

    def test_extract_json_markdown_block(self):
        raw = '```json\n{"intent_type": "look"}\n```'
        data, err = OllamaProvider._extract_json(raw)
        assert err is None
        assert data == {"intent_type": "look"}

    def test_extract_json_no_json(self):
        raw = "This is just plain text without any JSON."
        data, err = OllamaProvider._extract_json(raw)
        assert data is None
        assert err is not None

    def test_extract_json_embedded(self):
        raw = 'Some explanation before {"key": "value"} and after'
        data, err = OllamaProvider._extract_json(raw)
        assert err is None
        assert data == {"key": "value"}

    def test_extract_json_strips_reasoning_think_block(self):
        """Reasoning models (MiniMax M-series, gpt-oss…) prefix the answer with a
        <think>…</think> block whose brace noise otherwise derails extraction."""
        raw = (
            "<think>\nThe user wants JSON. Maybe like {example: 1} or so.\n</think>\n"
            '{"line": "难民不能进，这是规矩。", "tone": "stern"}'
        )
        data, err = OllamaProvider._extract_json(raw)
        assert err is None
        assert data == {"line": "难民不能进，这是规矩。", "tone": "stern"}

    def test_call_success_with_mock(self):
        provider = OllamaProvider()
        mock_response = json.dumps({
            "response": json.dumps({
                "intent_id": "int_1",
                "source": "natural_language",
                "raw_text": "look around",
                "intent_type": "look",
                "actor_id": "player_001",
                "commitment": "committed",
                "confidence": 1.0,
                "timestamp": 1,
            })
        }).encode("utf-8")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock(
                return_value=MagicMock(read=MagicMock(return_value=mock_response))
            )
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

            request = LLMCallRequest(
                task_type="parse_player_intent",
                prompt="Parse: look around",
                schema_model=ParsedIntent,
            )
            result = provider.call(request)
            assert result.success is True
            assert result.data is not None
            assert result.model_used == "gpt-oss:latest"

    def test_call_connection_error(self):
        import urllib.error
        provider = OllamaProvider()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Connection refused")):
            request = LLMCallRequest(task_type="test", prompt="hello")
            result = provider.call(request)
            assert result.success is False
            assert "Ollama connection error" in result.error

    def test_call_schema_validation_failure(self):
        provider = OllamaProvider()
        # Return JSON that doesn't match ParsedIntent schema
        mock_response = json.dumps({
            "response": '{"intent_type": "look"}'  # missing required fields
        }).encode("utf-8")

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value.__enter__ = MagicMock(
                return_value=MagicMock(read=MagicMock(return_value=mock_response))
            )
            mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

            request = LLMCallRequest(
                task_type="parse_player_intent",
                prompt="Parse: look around",
                schema_model=ParsedIntent,
            )
            result = provider.call(request)
            assert result.success is False
            assert "Schema validation failed" in result.error


# ---------------------------------------------------------------------------
# Integration test (requires running Ollama)
# ---------------------------------------------------------------------------

class TestOllamaProviderIntegration:
    @pytest.fixture
    def provider(self):
        return OllamaProvider()

    def test_ollama_responds(self, provider):
        """Verify Ollama is reachable and returns JSON."""
        request = LLMCallRequest(
            task_type="test",
            prompt='Return ONLY this JSON: {"status": "ok", "value": 42}',
        )
        result = provider.call(request)
        assert result.success is True, f"Ollama error: {result.error}"
        assert result.data == {"status": "ok", "value": 42}

    def test_ollama_intent_parsing(self, provider):
        """End-to-end: ask Ollama to parse a simple intent."""
        request = LLMCallRequest(
            task_type="parse_player_intent",
            prompt=(
                'Parse the player input "look around" into JSON. '
                'Fields: intent_type (look/movement/speech/physical/social/combat), '
                'actor_id, target_id (or null), content (or null), verb. '
                'Output only valid JSON, no markdown.'
            ),
        )
        result = provider.call(request)
        assert result.success is True, f"Error: {result.error}"
        data = result.data or {}
        assert "intent_type" in data
        assert data.get("intent_type") == "look"

    def test_orchestrator_factory(self):
        from verisaria.engine.llm import LLMOrchestrator
        orch = LLMOrchestrator.with_ollama(fallback_to_fake=True)
        assert isinstance(orch.primary, OllamaProvider)
        assert orch.fallback is not None
