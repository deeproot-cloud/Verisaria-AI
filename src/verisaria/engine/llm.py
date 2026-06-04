"""LLM Orchestrator and Providers.

All LLM calls in the system must go through LLMOrchestrator.call().
This module provides:
- LLMOrchestrator: unified interface with retry, budget tracking, fallback
- FakeLLMProvider: deterministic fixture-based responses for testing
- OllamaProvider: real local LLM via Ollama HTTP API
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ValidationError


# ---------------------------------------------------------------------------
# Domain types for LLM calls
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LLMCallRequest:
    task_type: str  # e.g. "parse_player_intent", "arbiter_decide"
    prompt: str
    schema_model: type[BaseModel] | None = None  # expected output schema
    model_preference: str = "ollama"  # "ollama" | "gpt"
    temperature: float = 0.7


# ---------------------------------------------------------------------------
# Error categorisation
# ---------------------------------------------------------------------------

class LLMErrorCategory(str, Enum):
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    PARSE = "parse"
    VALIDATION = "validation"
    BUDGET = "budget"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LLMCallResult:
    success: bool
    data: dict[str, Any] | None = None
    raw_response: str = ""
    error: str | None = None
    model_used: str = ""
    tokens_used: int = 0
    error_category: LLMErrorCategory | None = None


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

@dataclass
class RetryPolicy:
    """Control retry behaviour for transient LLM failures."""

    max_retries: int = 2
    base_delay: float = 1.0
    max_delay: float = 10.0
    # Categories that warrant a retry (transient errors). PARSE and VALIDATION are
    # included because nondeterministic models (notably MiniMax on a large schema
    # like ArbiterOutput) intermittently emit malformed JSON or an illegal field —
    # a fresh sample usually passes, which beats failing the turn into a fallback
    # (and the misleading "LLM 不可用" that schema rejection used to log as).
    retryable: set[str] = field(default_factory=lambda: {
        LLMErrorCategory.TIMEOUT.value,
        LLMErrorCategory.CONNECTION.value,
        LLMErrorCategory.PARSE.value,
        LLMErrorCategory.VALIDATION.value,
    })

    def should_retry(self, category: LLMErrorCategory | None, attempt: int) -> bool:
        if category is None:
            return False
        if category.value not in self.retryable:
            return False
        return attempt < self.max_retries

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff with jitter cap."""
        import math
        delay = self.base_delay * (2 ** attempt)
        return min(delay, self.max_delay)


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    # Whether independent calls may be issued concurrently (PLAY-1). Network
    # providers are I/O-bound and benefit; FakeLLM stays serial so replays are
    # deterministic (A10). Subclasses override.
    supports_concurrency: bool = False
    # Whether the provider can stream plain-text deltas via ``call_stream`` (used
    # to render an NPC reply token-by-token, cutting perceived latency).
    supports_streaming: bool = False

    @abstractmethod
    def call(self, request: LLMCallRequest) -> LLMCallResult:
        ...


# ---------------------------------------------------------------------------
# Fake Provider: deterministic fixture-based responses
# ---------------------------------------------------------------------------

class FakeLLMProvider(LLMProvider):
    """Deterministic LLM provider for testing.

    Loads fixtures from `fixtures/<content_pack>/<task_type>/`.
    Each fixture is a JSON file with:
    - fixture_id: str
    - task_type: str
    - input: dict
    - expected_output: dict
    - llm_response.raw: str (raw LLM JSON response)

    Matching is done by (task_type, input.raw_text) for now.
    """

    def __init__(self, fixtures_root: str | Path = "fixtures") -> None:
        self.fixtures_root = Path(fixtures_root)
        self._cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._load_all_fixtures()

    def _load_all_fixtures(self) -> None:
        if not self.fixtures_root.exists():
            return
        for task_dir in self.fixtures_root.rglob("*"):
            if not task_dir.is_dir():
                continue
            for fixture_file in task_dir.glob("*.json"):
                try:
                    data = json.loads(fixture_file.read_text(encoding="utf-8"))
                    task_type = data.get("task_type", "")
                    raw_text = data.get("input", {}).get("raw_text", "")
                    # Skip fixtures without raw_text (e.g. arbiter fixtures)
                    # They should be registered programmatically for tests
                    if not raw_text:
                        continue
                    key = (task_type, raw_text)
                    self._cache[key] = data
                except (json.JSONDecodeError, KeyError):
                    continue

    def call(self, request: LLMCallRequest) -> LLMCallResult:
        key = (request.task_type, request.prompt)
        fixture = self._cache.get(key)

        # Fallback 1: fuzzy match by task_type + raw_text contained in prompt
        if fixture is None:
            for cached_key, cached_fixture in self._cache.items():
                cached_task_type, cached_prompt = cached_key
                if cached_task_type == request.task_type:
                    raw_text = cached_fixture.get("input", {}).get("raw_text", "")
                    if raw_text and raw_text in request.prompt:
                        fixture = cached_fixture
                        break

        # Fallback 2: if only one fixture exists for this task_type, use it
        if fixture is None:
            same_type_fixtures = [
                cf for ck, cf in self._cache.items() if ck[0] == request.task_type
            ]
            if len(same_type_fixtures) == 1:
                fixture = same_type_fixtures[0]

        if fixture is None:
            return LLMCallResult(
                success=False,
                error=f"No fixture found for task_type={request.task_type!r} prompt={request.prompt!r}",
                model_used="fake",
                error_category=LLMErrorCategory.UNKNOWN,
            )

        expected = fixture.get("expected_output", {})
        raw = fixture.get("llm_response", {}).get("raw", "")

        # If schema_model is provided, validate the output
        if request.schema_model is not None:
            try:
                request.schema_model.model_validate(expected)
            except Exception as e:
                return LLMCallResult(
                    success=False,
                    error=f"Fixture output failed schema validation: {e}",
                    raw_response=raw,
                    model_used="fake",
                    error_category=LLMErrorCategory.VALIDATION,
                )

        return LLMCallResult(
            success=True,
            data=expected,
            raw_response=raw,
            model_used="fake",
            tokens_used=0,
        )

    def register_fixture(
        self,
        task_type: str,
        prompt: str,
        expected_output: dict[str, Any],
        raw_response: str = "",
    ) -> None:
        """Programmatically register a fixture (useful for tests)."""
        self._cache[(task_type, prompt)] = {
            "task_type": task_type,
            "input": {"raw_text": prompt},
            "expected_output": expected_output,
            "llm_response": {"raw": raw_response or json.dumps(expected_output)},
        }


# ---------------------------------------------------------------------------
# Ollama Provider
# ---------------------------------------------------------------------------

class OllamaProvider(LLMProvider):
    """Local LLM provider via Ollama HTTP API.

    Uses stdlib urllib only — zero extra dependencies.
    """

    supports_concurrency = True

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "gpt-oss:latest",
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def call(self, request: LLMCallRequest) -> LLMCallResult:
        prompt = request.prompt
        # Append schema hint if a Pydantic model is expected
        if request.schema_model is not None:
            schema_json = request.schema_model.model_json_schema()
            prompt += (
                "\n\n---\n"
                "IMPORTANT: Your response must be valid JSON matching this schema:\n"
                f"{json.dumps(schema_json, ensure_ascii=False, indent=2)}\n"
                "Output ONLY the JSON object. No markdown, no explanation."
            )

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": request.temperature,
            },
        }

        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                response_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            reason = str(exc.reason) if hasattr(exc, "reason") else str(exc)
            category = LLMErrorCategory.TIMEOUT if "timeout" in reason.lower() else LLMErrorCategory.CONNECTION
            return LLMCallResult(
                success=False,
                error=f"Ollama connection error: {exc}",
                model_used=self.model,
                error_category=category,
            )
        except TimeoutError as exc:
            return LLMCallResult(
                success=False,
                error=f"Ollama timeout: {exc}",
                model_used=self.model,
                error_category=LLMErrorCategory.TIMEOUT,
            )
        except json.JSONDecodeError as exc:
            return LLMCallResult(
                success=False,
                error=f"Ollama returned invalid JSON: {exc}",
                model_used=self.model,
                error_category=LLMErrorCategory.PARSE,
            )

        raw_text = response_data.get("response", "")

        # Try to extract JSON from the response
        parsed_data, parse_error = self._extract_json(raw_text)

        if parse_error:
            return LLMCallResult(
                success=False,
                raw_response=raw_text,
                error=f"JSON extraction failed: {parse_error}",
                model_used=self.model,
                error_category=LLMErrorCategory.PARSE,
            )

        # Schema validation
        if request.schema_model is not None:
            try:
                validated = request.schema_model.model_validate(parsed_data)
                parsed_data = validated.model_dump()
            except ValidationError as exc:
                return LLMCallResult(
                    success=False,
                    data=parsed_data,
                    raw_response=raw_text,
                    error=f"Schema validation failed: {exc}",
                    model_used=self.model,
                    error_category=LLMErrorCategory.VALIDATION,
                )

        return LLMCallResult(
            success=True,
            data=parsed_data,
            raw_response=raw_text,
            model_used=self.model,
        )

    @staticmethod
    def _extract_json(raw_text: str) -> tuple[dict[str, Any] | None, str | None]:
        return _extract_json_object(raw_text)


def _extract_json_object(raw_text: str) -> tuple[dict[str, Any] | None, str | None]:
    """Extract a JSON object from raw LLM text (markdown block, plain, or the
    first embedded object). Shared by all HTTP providers."""
    text = raw_text.strip()

    # Drop reasoning blocks from thinking models (MiniMax M-series, gpt-oss, …):
    # their <think>…</think> content is not the answer and its brace noise
    # otherwise derails the greedy {...} match below. Prefer text after the last
    # </think>; also strip any fully-paired blocks.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1].strip()
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

    code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if code_block:
        text = code_block.group(1)

    # strict=False tolerates literal control chars (newlines/tabs) inside string
    # values — a common shape of malformed JSON from chat models.
    try:
        return json.loads(text, strict=False), None
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(), strict=False), None
        except json.JSONDecodeError as exc:
            return None, str(exc)

    return None, "No JSON object found in response"


class _StreamFilter:
    """Incrementally filters a streamed PLAIN-TEXT completion for live display.

    Two things must never flash to the player: (1) ``<think>…</think>`` reasoning,
    and (2) a structured wrapper — a reasoning model sometimes answers a bare-line
    request with ```json {"line":"…"}``` (or a bare ``{…}``). We detect the wrapper
    from the first visible char and, instead of streaming the braces/fence, hold
    everything back and emit only the unwrapped ``line`` value at the end.
    """

    _HOLD = len("</think>")

    def __init__(self) -> None:
        self._buf = ""
        self._emitted = 0
        self._mode: str | None = None  # None (undecided) | "plain" | "structured"

    def _visible(self) -> str:
        b = self._buf
        if "</think>" in b:
            return b.rsplit("</think>", 1)[1]
        if "<think>" in b:
            return ""
        return b

    def _unwrapped_line(self) -> str:
        obj, _ = _extract_json_object(self._buf)
        if isinstance(obj, dict):
            line = obj.get("line")
            if isinstance(line, str):
                return line.strip()
        return ""

    def _step(self, final: bool) -> str:
        vis = self._visible()
        if self._mode is None:
            head = vis.lstrip()
            if head:
                self._mode = "structured" if head[0] in "`{" else "plain"
        if self._mode == "structured":
            if not final:
                return ""  # never stream a JSON/fence wrapper live
            line = self._unwrapped_line()
            out = line[self._emitted:]
            self._emitted = len(line)
            return out
        if self._mode is None and not final:
            return ""  # undecided (only whitespace/think so far) — hold back
        end = len(vis) if final else max(self._emitted, len(vis) - self._HOLD)
        if end > self._emitted:
            out = vis[self._emitted:end]
            self._emitted = end
            return out
        return ""

    def feed(self, delta: str) -> str:
        self._buf += delta
        return self._step(final=False)

    def finish(self) -> str:
        return self._step(final=True)

    @property
    def text(self) -> str:
        """The cleaned full line for ``raw_response`` (unwrapped if structured)."""
        if self._mode == "structured":
            line = self._unwrapped_line()
            if line:
                return line
        return re.sub(r"<think>.*?</think>", "", self._buf, flags=re.DOTALL).strip()


# ---------------------------------------------------------------------------
# OpenAI-compatible Provider
# ---------------------------------------------------------------------------

class OpenAICompatibleProvider(LLMProvider):
    """Any OpenAI-compatible ``/chat/completions`` endpoint — OpenAI, MiniMax,
    Together, Groq, local vLLM, ... Stdlib urllib only, zero extra deps.

    The backend is pure configuration (``base_url`` + ``model`` + ``api_key``),
    so ONE provider serves every compatible vendor. A vendor like MiniMax is a
    config preset, never a dedicated subclass.
    """

    supports_concurrency = True
    supports_streaming = True

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
        extra_params: dict[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Vendor-specific request fields merged into the payload (keeps the
        # provider generic). E.g. MiniMax `thinking={"type":"disabled"}` to skip
        # reasoning latency on short structured tasks, or `max_completion_tokens`.
        self.extra_params = extra_params or {}

    def call(self, request: LLMCallRequest) -> LLMCallResult:
        prompt = request.prompt
        # Same schema hint as OllamaProvider — belt-and-suspenders alongside the
        # native JSON mode below, and a fallback for endpoints lacking it.
        if request.schema_model is not None:
            schema_json = request.schema_model.model_json_schema()
            prompt += (
                "\n\n---\n"
                "IMPORTANT: Your response must be valid JSON matching this schema:\n"
                f"{json.dumps(schema_json, ensure_ascii=False, indent=2)}\n"
                "Output ONLY the JSON object. No markdown, no explanation."
            )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": request.temperature,
            "stream": False,
        }
        if request.schema_model is not None:
            payload["response_format"] = {"type": "json_object"}
        # Vendor-specific fields (thinking/max_completion_tokens/…) last so a
        # preset can override defaults if needed.
        payload.update(self.extra_params)

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                response_data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            reason = str(exc.reason) if hasattr(exc, "reason") else str(exc)
            category = (
                LLMErrorCategory.TIMEOUT if "timeout" in reason.lower()
                else LLMErrorCategory.CONNECTION
            )
            return LLMCallResult(
                success=False, error=f"OpenAI-compatible connection error: {exc}",
                model_used=self.model, error_category=category,
            )
        except TimeoutError as exc:
            return LLMCallResult(
                success=False, error=f"OpenAI-compatible timeout: {exc}",
                model_used=self.model, error_category=LLMErrorCategory.TIMEOUT,
            )
        except json.JSONDecodeError as exc:
            return LLMCallResult(
                success=False, error=f"OpenAI-compatible returned invalid JSON: {exc}",
                model_used=self.model, error_category=LLMErrorCategory.PARSE,
            )

        try:
            raw_text = response_data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            return LLMCallResult(
                success=False,
                error=f"Unexpected response shape: {exc}",
                raw_response=json.dumps(response_data)[:500],
                model_used=self.model, error_category=LLMErrorCategory.PARSE,
            )

        parsed_data, parse_error = _extract_json_object(raw_text)
        if parse_error:
            return LLMCallResult(
                success=False, raw_response=raw_text,
                error=f"JSON extraction failed: {parse_error}",
                model_used=self.model, error_category=LLMErrorCategory.PARSE,
            )

        if request.schema_model is not None:
            try:
                validated = request.schema_model.model_validate(parsed_data)
                parsed_data = validated.model_dump()
            except ValidationError as exc:
                return LLMCallResult(
                    success=False, data=parsed_data, raw_response=raw_text,
                    error=f"Schema validation failed: {exc}",
                    model_used=self.model, error_category=LLMErrorCategory.VALIDATION,
                )

        return LLMCallResult(
            success=True, data=parsed_data, raw_response=raw_text,
            model_used=self.model,
        )

    def call_stream(
        self,
        request: LLMCallRequest,
        on_delta: Callable[[str], None] | None = None,
    ) -> LLMCallResult:
        """Stream a PLAIN-TEXT completion, firing ``on_delta(text)`` per chunk and
        returning the accumulated text in ``raw_response``. Used to render an NPC
        reply token-by-token; structured (schema) calls stay on ``call``.

        Streams cleanest with thinking disabled (no ``<think>`` tokens leaking to
        the player) — the case for our MiniMax preset.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": request.temperature,
            "stream": True,
        }
        payload.update(self.extra_params)

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )

        flt = _StreamFilter()

        def _emit(chunk: str) -> None:
            if chunk and on_delta is not None:
                on_delta(chunk)

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for raw in resp:
                    line = (raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)).strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0]["delta"].get("content") or ""
                    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                        continue
                    if delta:
                        _emit(flt.feed(delta))
            _emit(flt.finish())
        except urllib.error.URLError as exc:
            reason = str(exc.reason) if hasattr(exc, "reason") else str(exc)
            category = (
                LLMErrorCategory.TIMEOUT if "timeout" in reason.lower()
                else LLMErrorCategory.CONNECTION
            )
            return LLMCallResult(
                success=False, error=f"OpenAI-compatible stream error: {exc}",
                model_used=self.model, error_category=category,
            )
        except TimeoutError as exc:
            return LLMCallResult(
                success=False, error=f"OpenAI-compatible stream timeout: {exc}",
                model_used=self.model, error_category=LLMErrorCategory.TIMEOUT,
            )

        return LLMCallResult(success=True, raw_response=flt.text, model_used=self.model)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

# Per-tick budget priority (design §6.7.2). Lower index = higher priority.
# When the per-tick budget is spent, high-priority tasks are still allowed
# (player-facing work must never be silently dropped) while low-priority tasks
# are refused so their callers degrade to rule/template fallbacks.
TASK_PRIORITY_ORDER: tuple[str, ...] = (
    "arbiter_decide",
    "parse_player_intent",
    "npc_decision",
    "observation_interpretation",
    "generate_npc_dialogue",
    "reflection_prompt",
    "agenda_inference",
    "coherence_check",
)
# Tasks at or above this rank are always allowed to spend budget (they are
# player-facing or world-truth-critical).
_HIGH_PRIORITY_CUTOFF = 2


class LLMOrchestrator:
    """Unified entry point for all LLM calls in the system.

    Responsibilities:
    - Route calls to the appropriate provider (Ollama / GPT / Fake)
    - Track LLM budget per tick (one logical call = one budget unit; internal
      retries/fallback do not multiply consumption)
    - Priority-based degradation when the budget is spent (design §6.7.2)
    - Retry with exponential backoff on transient failures
    - Fallback to secondary provider on persistent failure
    - Validate outputs against Pydantic schemas
    """

    def __init__(
        self,
        primary_provider: LLMProvider | None = None,
        fallback_provider: LLMProvider | None = None,
        max_calls_per_tick: int = 50,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.primary = primary_provider or FakeLLMProvider()
        self.fallback = fallback_provider
        self.max_calls_per_tick = max_calls_per_tick
        self.retry_policy = retry_policy or RetryPolicy()
        self._calls_this_tick = 0
        # Guards the per-tick budget counter so concurrent NPC calls (PLAY-1)
        # don't lose increments on the read-modify-write.
        self._budget_lock = threading.Lock()

    @classmethod
    def with_ollama(
        cls,
        model: str = "gpt-oss:latest",
        fallback_to_fake: bool = True,
        max_calls_per_tick: int = 10,
    ) -> LLMOrchestrator:
        """Convenience factory: Ollama primary, optional Fake fallback."""
        ollama = OllamaProvider(model=model)
        fallback = FakeLLMProvider() if fallback_to_fake else None
        return cls(primary_provider=ollama, fallback_provider=fallback, max_calls_per_tick=max_calls_per_tick)

    def reset_tick_budget(self) -> None:
        self._calls_this_tick = 0

    def _budget_allows(self, task_type: str) -> bool:
        """Whether ``task_type`` may spend budget this tick.

        High-priority tasks (rank <= cutoff) are always allowed; everything else
        is refused once the per-tick budget is spent so it degrades gracefully.
        """
        if self._calls_this_tick < self.max_calls_per_tick:
            return True
        try:
            rank = TASK_PRIORITY_ORDER.index(task_type)
        except ValueError:
            rank = len(TASK_PRIORITY_ORDER)  # unknown → lowest priority
        return rank <= _HIGH_PRIORITY_CUTOFF

    def call(
        self,
        request: LLMCallRequest,
        *,
        allow_fallback: bool = True,
    ) -> LLMCallResult:
        # Budget check with priority-based degradation. One logical call costs
        # one unit regardless of internal retries/fallback. Check + increment are
        # atomic so concurrent callers can't over-spend the budget.
        with self._budget_lock:
            if not self._budget_allows(request.task_type):
                return LLMCallResult(
                    success=False,
                    error=f"LLM budget exceeded: {self._calls_this_tick}/{self.max_calls_per_tick} calls this tick",
                    error_category=LLMErrorCategory.BUDGET,
                )
            self._calls_this_tick += 1

        # Primary path with retries
        result = self._call_with_retries(request)

        # Fallback path
        if not result.success and allow_fallback and self.fallback is not None:
            result = self.fallback.call(request)

        return result

    def _call_with_retries(self, request: LLMCallRequest) -> LLMCallResult:
        """Call primary provider with retry logic.

        Retries are an infrastructure concern and do NOT consume budget — the
        single budget unit is accounted by the caller (``call``).
        """
        policy = self.retry_policy
        last_result: LLMCallResult | None = None

        for attempt in range(policy.max_retries + 1):
            result = self.primary.call(request)

            if result.success:
                return result

            last_result = result

            if not policy.should_retry(result.error_category, attempt):
                break

            delay = policy.delay_for(attempt)
            time.sleep(delay)

        return last_result or LLMCallResult(
            success=False,
            error="Primary provider failed after retries",
            error_category=LLMErrorCategory.UNKNOWN,
        )
