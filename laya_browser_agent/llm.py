"""Unified Local LLM Client Interface for Laya Browser Agent.

Provides a clean interface for System 2 operations (planning, visual screen locator,
progressive discovery, remediation, dialogue) with support for:
1. Native Ollama JSON schema and chat completions
2. Thinking-tag stripping for modern reasoning models (Qwen 3, Qwen 3.5)
3. Dynamic model discovery prioritizing locally installed models
4. RecordedLLMClient for 100% deterministic, offline, zero-cost test suites (T1)
"""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple, Protocol, runtime_checkable
import httpx


def strip_thinking_tags(text: str) -> str:
    """Removes <think>...</think> blocks from models with reasoning heads."""
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return cleaned.strip()


def extract_json_payload(text: str) -> Optional[Any]:
    """Robustly extracts JSON from raw text, markdown blocks, or surrounding prose."""
    cleaned = strip_thinking_tags(text).strip()
    if not cleaned:
        return None

    # 1. Direct parse attempt
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 2. Markdown code block ```json ... ```
    match = re.search(r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # 3. Outer bracket extraction (object or array)
    for pattern in (r"(\{.*\})", r"(\[.*\])"):
        bracket_match = re.search(pattern, cleaned, re.DOTALL)
        if bracket_match:
            try:
                return json.loads(bracket_match.group(1))
            except Exception:
                pass

    return None


def resolve_local_model(preferred: Optional[str] = None, base_url: str = "http://localhost:11434") -> str:
    """Discovers available local models from the Ollama runner.

    Returns the first of: OLLAMA_MODEL env var, preferred (only if installed),
    best priority match from /api/tags, or the fallback string.
    Callers must pass base_url as a keyword arg; positional URL is refused.
    """
    if preferred and (preferred.startswith("http://") or preferred.startswith("https://")):
        raise ValueError(
            f"resolve_local_model: first argument looks like a URL ({preferred!r}). "
            "Pass base_url as a keyword argument."
        )

    env_model = os.environ.get("OLLAMA_MODEL")
    if env_model:
        return env_model

    installed: list = []
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"{base_url.rstrip('/')}/api/tags")
            if resp.status_code == 200:
                installed = [m.get("name") for m in resp.json().get("models", []) if m.get("name")]
    except Exception:
        pass

    # Honour preferred only if it's actually installed
    if preferred and installed:
        for m in installed:
            if preferred in m or m in preferred:
                return m

    # Priority list for auto-selection
    for cand in ("qwen3.5:latest", "qwen3.5", "qwen2.5:7b", "qwen3:14b", "qwen2.5:latest"):
        for m in installed:
            if cand in m:
                return m
    if installed:
        return installed[0]

    # preferred passed but Ollama offline — trust it as-is (best effort)
    if preferred:
        return preferred
    return "qwen3.5:latest"


@runtime_checkable
class LLMClient(Protocol):
    """Protocol for LLM interactions in Laya Browser Agent."""

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        timeout: float = 25.0,
    ) -> Tuple[Optional[Any], int, int, float]:
        """Sends chat request and returns (parsed_json, input_tokens, output_tokens, latency_ms)."""
        ...


class OllamaClient:
    """Live Ollama API client with thinking-block hygiene and schema binding."""

    def __init__(self, base_url: str = "http://localhost:11434", model: Optional[str] = None, think: bool = False):
        self.base_url = base_url.rstrip("/")
        self.model = model or resolve_local_model(preferred=None, base_url=self.base_url)
        # Reasoning models (qwen3/qwen3.5) spend ~20 s per call "thinking" by default. Agent calls are
        # short structured-JSON decisions, so thinking is off unless explicitly requested.
        self.think = bool(think)
        self.llm_call_count: int = 0

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        timeout: float = 25.0,
    ) -> Tuple[Optional[Any], int, int, float]:
        t0 = time.time()
        self.llm_call_count += 1
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": self.think,
            "format": schema if schema else "json",
            "options": {"temperature": temperature},
        }

        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                elapsed_ms = (time.time() - t0) * 1000.0
                if resp.status_code == 200:
                    data = resp.json()
                    in_tokens = int(data.get("prompt_eval_count") or 0)
                    out_tokens = int(data.get("eval_count") or 0)
                    raw_content = data.get("message", {}).get("content", "")
                    parsed = extract_json_payload(raw_content)
                    return parsed, in_tokens, out_tokens, elapsed_ms
        except Exception:
            pass

        return None, 0, 0, (time.time() - t0) * 1000.0


class RecordedLLMClient:
    """Deterministic, recorded LLM client for 100% reproducible offline test suites (T1).

    Set strict=True to raise RuntimeError for prompts that match no recording and no
    fallback_handler.  Useful for catching test gaps.
    """

    def __init__(
        self,
        recordings: Optional[Dict[str, Any]] = None,
        recording_file: Optional[str] = None,
        fallback_handler: Optional[Any] = None,
        strict: bool = False,
    ):
        self.recordings: Dict[str, Any] = dict(recordings or {})
        self.call_history: List[Dict[str, Any]] = []
        self.fallback_handler = fallback_handler
        self.strict = strict
        self.llm_call_count: int = 0

        if recording_file and os.path.exists(recording_file):
            try:
                with open(recording_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        self.recordings.update(loaded)
            except Exception:
                pass

    def add_recording(self, key_substring: str, response_json: Any):
        """Registers a canned JSON response for any prompt containing key_substring."""
        self.recordings[key_substring.lower()] = response_json

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        schema: Optional[Dict[str, Any]] = None,
        temperature: float = 0.0,
        timeout: float = 25.0,
    ) -> Tuple[Optional[Any], int, int, float]:
        full_text = " ".join([m.get("content", "") for m in messages]).lower()
        self.call_history.append({"messages": messages, "schema": schema})
        self.llm_call_count += 1

        # Match against registered recordings
        for key, canned in self.recordings.items():
            if key in full_text:
                return canned, 10, 10, 0.1

        # Check fallback handler if provided
        if self.fallback_handler:
            try:
                result = self.fallback_handler(messages, schema)
                if result is not None:
                    return result, 10, 10, 0.1
            except Exception:
                pass

        if self.strict:
            preview = full_text[:120].replace("\n", " ")
            raise RuntimeError(
                f"RecordedLLMClient (strict): no recording matched prompt: {preview!r}"
            )
        # Permissive default — returns empty dict so the caller degrades gracefully
        return {}, 0, 0, 0.1


# Global LLM client holder
_global_llm_client: Optional[LLMClient] = None


def get_llm_client(base_url: Optional[str] = None, model: Optional[str] = None) -> LLMClient:
    """Gets the active LLM client or initializes default OllamaClient."""
    if _global_llm_client is not None:
        return _global_llm_client
    return OllamaClient(base_url=base_url or "http://localhost:11434", model=model)


def configure_llm_client(base_url: Optional[str] = None, model: Optional[str] = None) -> LLMClient:
    """Configures the active OllamaClient with model and base_url unless already in test mock."""
    global _global_llm_client
    if not isinstance(_global_llm_client, RecordedLLMClient):
        _global_llm_client = OllamaClient(base_url=base_url or "http://localhost:11434", model=model)
    return _global_llm_client


def set_llm_client(client: Optional[LLMClient]) -> None:
    """Sets the global LLM client (used in tests for RecordedLLMClient)."""
    global _global_llm_client
    _global_llm_client = client


def reset_llm_client() -> None:
    """Resets to default OllamaClient."""
    global _global_llm_client
    _global_llm_client = None
