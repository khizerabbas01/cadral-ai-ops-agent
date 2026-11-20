"""LLM client: OpenAI-compatible chat completions (Groq / Gemini / OpenRouter / OpenAI),
streaming + tool calling, retries with backoff, and per-call cost/latency tracking."""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from .config import settings
from .db import session_scope
from .models import LLMCall

log = logging.getLogger("llm")

# USD per 1M tokens (input, output). Free tiers cost $0 but we track list-price equivalent so the
# dashboard shows what the system would cost on a paid plan.
PRICES: dict[str, tuple[float, float]] = {
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "meta-llama/llama-4-scout-17b-16e-instruct": (0.11, 0.34),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gpt-4o-mini": (0.15, 0.60),
}


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResult:
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    model: str = ""


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def price(model: str, pt: int, ct: int) -> float:
    pin, pout = PRICES.get(model, (0.5, 1.0))
    return round(pt / 1e6 * pin + ct / 1e6 * pout, 8)


def _record(trace_id: int | None, purpose: str, res: LLMResult, ok: bool = True, error: str | None = None) -> None:
    try:
        with session_scope() as s:
            s.add(LLMCall(trace_id=trace_id, purpose=purpose, model=res.model, prompt_tokens=res.prompt_tokens,
                          completion_tokens=res.completion_tokens, cost_usd=res.cost_usd,
                          latency_ms=res.latency_ms, ok=ok, error=error))
    except Exception:  # metrics must never break the request path
        log.exception("failed to record llm call")


_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=settings.llm_timeout)
    return _client


RETRYABLE = {408, 409, 429, 500, 502, 503, 504}


def chat(messages: list[dict], *, purpose: str, model: str | None = None, tools: list[dict] | None = None,
         tool_choice: str | None = None, temperature: float = 0.2, max_tokens: int = 700,
         json_mode: bool = False, on_token: Callable[[str], None] | None = None,
         trace_id: int | None = None, max_retries: int = 4) -> LLMResult:
    """Call the chat model. Streams tokens to `on_token` when given. Retries 429/5xx/timeouts with
    exponential backoff + jitter; on a final failure falls back once to the fast model."""
    if not settings.llm_enabled:
        raise LLMError("LLM disabled (no API key / mock mode)")
    model = model or settings.llm_model
    body: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature,
                            "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = tool_choice or "auto"
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if on_token:
        body["stream"] = True
        if settings.llm_provider in {"openai", "groq", "openrouter"}:
            body["stream_options"] = {"include_usage": True}

    url = f"{settings.resolved_llm_base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"}
    last_err: Exception | None = None
    for attempt in range(max_retries):
        t0 = time.perf_counter()
        streamed_any = False
        try:
            if on_token:
                res = LLMResult(model=model)
                calls: dict[int, dict] = {}
                with _http().stream("POST", url, headers=headers, json=body) as r:
                    if r.status_code >= 400:
                        r.read()
                        raise httpx.HTTPStatusError(r.text[:500], request=r.request, response=r)
                    for line in r.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        usage = chunk.get("usage") or (chunk.get("x_groq") or {}).get("usage")
                        if usage:
                            res.prompt_tokens = usage.get("prompt_tokens", 0)
                            res.completion_tokens = usage.get("completion_tokens", 0)
                        for ch in chunk.get("choices") or []:
                            delta = ch.get("delta") or {}
                            if delta.get("content"):
                                res.content += delta["content"]
                                streamed_any = True
                                on_token(delta["content"])
                            for tc in delta.get("tool_calls") or []:
                                slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                                slot["id"] = tc.get("id") or slot["id"]
                                fn = tc.get("function") or {}
                                slot["name"] += fn.get("name") or ""
                                slot["arguments"] += fn.get("arguments") or ""
                res.tool_calls = [calls[i] for i in sorted(calls)]
            else:
                r = _http().post(url, headers=headers, json=body)
                if r.status_code >= 400:
                    raise httpx.HTTPStatusError(r.text[:500], request=r.request, response=r)
                data = r.json()
                msg = data["choices"][0]["message"]
                usage = data.get("usage") or {}
                res = LLMResult(model=model, content=msg.get("content") or "",
                                prompt_tokens=usage.get("prompt_tokens", 0),
                                completion_tokens=usage.get("completion_tokens", 0))
                res.tool_calls = [{"id": tc.get("id", ""), "name": tc["function"]["name"],
                                   "arguments": tc["function"].get("arguments") or "{}"}
                                  for tc in msg.get("tool_calls") or []]
            if not res.prompt_tokens:
                res.prompt_tokens = estimate_tokens(json.dumps(messages))
                res.completion_tokens = estimate_tokens(res.content + json.dumps(res.tool_calls))
            res.latency_ms = int((time.perf_counter() - t0) * 1000)
            res.cost_usd = price(model, res.prompt_tokens, res.completion_tokens)
            _record(trace_id, purpose, res)
            return res
        except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError, json.JSONDecodeError, KeyError) as e:
            last_err = e
            status = getattr(getattr(e, "response", None), "status_code", None)
            fail = LLMResult(model=model, latency_ms=int((time.perf_counter() - t0) * 1000))
            _record(trace_id, purpose, fail, ok=False, error=f"{status}: {str(e)[:300]}")
            if streamed_any or (status is not None and status not in RETRYABLE):
                break  # can't safely retry a half-streamed answer / non-retryable client error
            wait = min(20.0, (2 ** attempt) + random.random())
            retry_after = getattr(getattr(e, "response", None), "headers", {}).get("retry-after") if status else None
            if retry_after:
                try:
                    wait = min(30.0, float(retry_after))
                except ValueError:
                    pass
            log.warning("LLM call failed (%s), retry %d in %.1fs", status or type(e).__name__, attempt + 1, wait)
            time.sleep(wait)

    if model != settings.llm_fast_model and not streamed_any:
        log.warning("falling back to %s", settings.llm_fast_model)
        return chat(messages, purpose=purpose + ":fallback", model=settings.llm_fast_model, tools=tools,
                    tool_choice=tool_choice, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode,
                    on_token=on_token, trace_id=trace_id, max_retries=2)
    raise LLMError(f"LLM call failed after retries: {last_err}")


def chat_json(messages: list[dict], *, purpose: str, model: str | None = None, trace_id: int | None = None,
              max_tokens: int = 400) -> tuple[dict, LLMResult]:
    res = chat(messages, purpose=purpose, model=model, json_mode=True, temperature=0.0,
               max_tokens=max_tokens, trace_id=trace_id)
    text = res.content.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    try:
        return json.loads(text), res
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1]), res
        raise LLMError(f"model did not return JSON: {text[:200]}")
