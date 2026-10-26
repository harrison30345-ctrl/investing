"""
LLM provider adapter.

Agents call `provider.complete_json(system, user, schema)` and get back a parsed
dict plus usage and cost. Only this module knows which vendor is behind it, so
a second provider can be added later without touching agent code.

Anthropic implementation notes:
  * Model defaults to claude-opus-5-5 (QE_LLM_MODEL). Thinking is always on for
    this model; depth is controlled by `effort` (QE_LLM_EFFORT, default high).
  * Structured outputs (`output_config.format` with a JSON schema) guarantee the
    reply parses. We still validate it, because a `max_tokens` stop truncates.
  * Server-side refusal fallbacks are enabled (`fallbacks="default"`): if a
    safety classifier declines, the API re-runs the request on a fallback model
    and `served_model` records which model actually answered.
  * Costs are estimated from token usage at list prices (below). The provider's
    invoice is authoritative; these numbers drive the budget guard.
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# USD per million tokens (Anthropic list prices, Oct 2026).
PRICES = {
    "claude-opus-5-5": {"in": 4.00, "out": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-sonnet-5-5": {"in": 2.00, "out": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-5-5": {"in": 0.10, "out": 0.50, "cache_read": 0.01, "cache_write": 0.125},
}
CHARS_PER_TOKEN = 3.5     # conservative for JSON-heavy prompts (more tokens, higher estimate)


class LLMError(RuntimeError):
    retryable = False


class LLMTransientError(LLMError):
    """Rate limits, timeouts, 5xx, network: worth retrying later."""
    retryable = True


class LLMPermanentError(LLMError):
    """Bad request, auth, unknown model: retrying won't help."""


@dataclass
class LLMResult:
    data: Optional[Dict[str, Any]]
    stop_reason: str
    model: str
    served_model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    request_id: Optional[str] = None
    duration_ms: int = 0
    raw_text: str = ""
    notes: List[str] = field(default_factory=list)


def price_for(model: str) -> Dict[str, float]:
    if model not in PRICES:
        # Unknown model: assume the most expensive known rates so the budget guard stays safe.
        return max(PRICES.values(), key=lambda p: p["out"])
    return PRICES[model]


def cost_of(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> float:
    p = price_for(model)
    return (input_tokens * p["in"] + output_tokens * p["out"] + cache_read * p["cache_read"]
            + cache_write * p["cache_write"]) / 1e6


def estimate_max_cost(model: str, prompt_chars: int, max_tokens: int) -> float:
    """Worst case for one call: the whole prompt uncached plus every allowed output token."""
    return cost_of(model, int(prompt_chars / CHARS_PER_TOKEN) + 500, max_tokens)


class LLMProvider(ABC):
    name: str = "base"

    def __init__(self, model: str, max_tokens: int = 16000, effort: str = "high"):
        self.model, self.max_tokens, self.effort = model, max_tokens, effort

    @abstractmethod
    def complete_json(self, system: str, user: str, schema: Dict[str, Any]) -> LLMResult:
        ...


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, api_key: Optional[str], model: str, max_tokens: int = 16000, effort: str = "high",
                 timeout_s: float = 240.0, http_client=None):
        super().__init__(model, max_tokens, effort)
        import anthropic
        self._anthropic = anthropic
        # max_retries=0: retries are handled (and logged) by the orchestrator's task attempts.
        kwargs = {"timeout": timeout_s, "max_retries": 0}
        if api_key:
            kwargs["api_key"] = api_key
        if http_client is not None:      # tests inject a mock transport
            kwargs["http_client"] = http_client
        self._client = anthropic.Anthropic(**kwargs)

    def complete_json(self, system: str, user: str, schema: Dict[str, Any]) -> LLMResult:
        a = self._anthropic
        t0 = time.monotonic()
        try:
            resp = self._client.beta.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": schema}},
                betas=[self.FALLBACK_BETA],
                fallbacks="default",
            )
        except (a.RateLimitError, a.APITimeoutError, a.APIConnectionError, a.InternalServerError) as exc:
            raise LLMTransientError(f"{type(exc).__name__}: {exc}") from exc
        except a.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code in (408, 409, 429, 529):
                raise LLMTransientError(f"HTTP {exc.status_code}: {exc.message}") from exc
            raise LLMPermanentError(f"HTTP {exc.status_code}: {exc.message}") from exc

        usage = resp.usage
        in_t = int(getattr(usage, "input_tokens", 0) or 0)
        out_t = int(getattr(usage, "output_tokens", 0) or 0)
        cr = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cw = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        served = getattr(resp, "model", self.model) or self.model
        result = LLMResult(
            data=None, stop_reason=resp.stop_reason or "", model=self.model, served_model=served,
            input_tokens=in_t, output_tokens=out_t, cache_read_tokens=cr, cache_write_tokens=cw,
            # Priced at the requested model's rates; if a fallback served the request this is an estimate.
            cost_usd=cost_of(self.model, in_t, out_t, cr, cw),
            request_id=getattr(resp, "_request_id", None), duration_ms=int((time.monotonic() - t0) * 1000))
        if served != self.model:
            result.notes.append(f"Served by fallback model {served}")
        if resp.stop_reason == "refusal":
            details = getattr(resp, "stop_details", None)
            result.notes.append(f"Refused: {getattr(details, 'category', None)} {getattr(details, 'explanation', '')}")
            return result
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        result.raw_text = text
        if resp.stop_reason == "max_tokens":
            result.notes.append("Output hit max_tokens and is incomplete")
            return result
        try:
            result.data = json.loads(text)
        except json.JSONDecodeError as exc:
            result.notes.append(f"Unparseable JSON: {exc}")
        return result


class ScriptedProvider(LLMProvider):
    """
    Deterministic stand-in for tests: returns queued responses (dicts, or callables
    taking (system, user)) and fake token counts. Never used by the app itself.
    """
    name = "scripted"

    def __init__(self, responses: List[Any], model: str = "claude-opus-5-5", tokens=(4000, 1500)):
        super().__init__(model)
        self.responses = list(responses)
        self.calls: List[Dict[str, str]] = []
        self.tokens = tokens

    def complete_json(self, system: str, user: str, schema: Dict[str, Any]) -> LLMResult:
        self.calls.append({"system": system, "user": user})
        if not self.responses:
            raise LLMPermanentError("ScriptedProvider has no responses left")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        data = nxt(system, user) if callable(nxt) else nxt
        stop = "refusal" if data == "REFUSE" else "end_turn"
        i, o = self.tokens
        return LLMResult(data=None if stop == "refusal" else data, stop_reason=stop, model=self.model,
                         served_model=self.model, input_tokens=i, output_tokens=o,
                         cost_usd=cost_of(self.model, i, o))


def get_provider() -> Optional[LLMProvider]:
    """The configured provider, or None when AI is disabled (QE_LLM_PROVIDER=none or no key)."""
    from ..config import get_settings
    s = get_settings()
    if s.llm_provider != "anthropic":
        return None
    if not s.anthropic_api_key:
        return None
    return AnthropicProvider(s.anthropic_api_key, s.llm_model, s.llm_max_tokens, s.llm_effort, s.llm_timeout_s)
