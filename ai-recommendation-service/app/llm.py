"""Provider interface for the itinerary planner (ADR 0021). Config picks one; with none, plans are deterministic only.

Retries live here, once, rather than in each provider: 5xx, timeouts and connection failures are retried a bounded
number of times inside a deadline. No 4xx is, 429 included: the caller has a deterministic planner to fall back on
right now, which beats making a traveller wait out someone else's rate limit. So the Anthropic SDK's own retries are
off.
"""

import json
import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Protocol

import anthropic
import httpx2
from opentelemetry import metrics

from .config import Settings, get_settings

log = logging.getLogger(__name__)

MAX_RETRIES = 2
BACKOFF_S = 0.5

# OpenTelemetry GenAI conventions (open-telemetry/semantic-conventions-genai, model/gen-ai): development status.
_meter = metrics.get_meter("smarttours.ai")
_duration = _meter.create_histogram(
    "gen_ai.client.inference.duration",
    unit="s",
    description="GenAI client inference operation duration.",
    explicit_bucket_boundaries_advisory=[0.1, 0.5, 1, 2, 5, 10, 20, 40, 80, 160],
)
_input_tokens = _meter.create_counter(
    "gen_ai.client.inference.usage.input_tokens", unit="{token}", description="Input tokens used, including cached."
)
_output_tokens = _meter.create_counter(
    "gen_ai.client.inference.usage.output_tokens",
    unit="{token}",
    description="Output tokens used, including reasoning.",
)


@dataclass(frozen=True)
class LlmResult:
    text: str
    input_tokens: int
    output_tokens: int
    model: str


class LlmError(Exception):
    """input_tokens and output_tokens: what the failed call still cost, when the provider said."""

    kind = "error"

    def __init__(self, message: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class LlmTransientError(LlmError):
    kind = "transient"


class LlmRejectedError(LlmError):
    """A 4xx: the same request would fail the same way."""

    kind = "rejected"


class LlmRefusedError(LlmError):
    kind = "refused"


class LlmTruncatedError(LlmError):
    kind = "truncated"


class LLMClient(Protocol):
    provider: str
    model: str

    def complete(
        self, system: str, messages: list[dict], schema: dict, max_tokens: int, timeout: float
    ) -> LlmResult: ...


class AnthropicClient:
    """The Messages API through the official SDK, with structured output (output_config.format)."""

    provider = "anthropic"

    def __init__(self, api_key: str, model: str, effort: str, fallbacks: str, base_url: str | None = None) -> None:
        self.model = model
        self._effort = effort
        self._fallbacks = fallbacks
        self._client = anthropic.Anthropic(api_key=api_key, base_url=base_url or None, max_retries=0)

    def complete(self, system: str, messages: list[dict], schema: dict, max_tokens: int, timeout: float) -> LlmResult:
        # Server-side refusal fallback is beta, and not on every model: ANTHROPIC_FALLBACKS empty turns it off.
        extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": self._fallbacks} if self._fallbacks else {}
        try:
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                output_config={"effort": self._effort, "format": {"type": "json_schema", "schema": schema}},
                timeout=timeout,
                **extra,
            )
        except anthropic.APIConnectionError as exc:  # includes APITimeoutError
            raise LlmTransientError(type(exc).__name__) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LlmTransientError(f"HTTP {exc.status_code}") from exc
            raise LlmRejectedError(f"HTTP {exc.status_code}") from exc
        used = (response.usage.input_tokens, response.usage.output_tokens)
        if response.stop_reason == "refusal":
            raise LlmRefusedError("The model declined", *used)
        if response.stop_reason == "max_tokens":
            raise LlmTruncatedError("The answer hit max_tokens", *used)
        text = next((block.text for block in response.content if block.type == "text"), "")
        return LlmResult(text, *used, response.model)


class OpenAiCompatibleClient:
    """POST /chat/completions with a JSON schema: OpenAI, a local Ollama, or anything else speaking the same dialect."""

    provider = "openai"

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        self.model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx2.Client(base_url=base_url.rstrip("/"), headers=headers)

    def complete(self, system: str, messages: list[dict], schema: dict, max_tokens: int, timeout: float) -> LlmResult:
        body = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, *messages],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "itinerary", "strict": True, "schema": schema},
            },
        }
        try:
            response = self._client.post("/chat/completions", json=body, timeout=timeout)
        except httpx2.TransportError as exc:
            raise LlmTransientError(type(exc).__name__) from exc
        if response.status_code >= 500:
            raise LlmTransientError(f"HTTP {response.status_code}")
        if response.status_code >= 400:
            raise LlmRejectedError(f"HTTP {response.status_code}")
        try:
            payload = response.json()
            choice = payload["choices"][0]
            usage = payload.get("usage") or {}
            used = (int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)))
            if choice.get("finish_reason") == "content_filter":
                raise LlmRefusedError("The model declined", *used)
            if choice.get("finish_reason") == "length":
                raise LlmTruncatedError("The answer hit max_tokens", *used)
            return LlmResult(choice["message"]["content"] or "", *used, payload.get("model", self.model))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlmTransientError("A response without the expected fields") from exc


class FakeLLMClient:
    """A provider that needs no network, for tests, CI and the smoke test. It says nothing about any real model's
    quality.

    It reads the catalogue out of the prompt as a real model would, and answers by scenario:
    good: a valid one-item-per-day plan. repair: an invalid plan first, then a valid one once told what was wrong.
    invalid: invalid whatever it is told. injected: does what a malicious description asks, recommending a tour that
    doesn't exist.
    """

    provider = "fake"

    def __init__(self, scenario: str = "good", model: str = "fake-planner") -> None:
        self.model = model
        self.scenario = scenario
        self.calls: list[list[dict]] = []

    def complete(self, system: str, messages: list[dict], schema: dict, max_tokens: int, timeout: float) -> LlmResult:
        self.calls.append(messages)
        prompt = "\n".join(m["content"] for m in messages)
        bad = self.scenario in ("invalid", "injected") or (self.scenario == "repair" and len(messages) == 1)
        plan = self._invalid(messages[0]["content"]) if bad else self._valid(messages[0]["content"])
        text = json.dumps(plan)
        return LlmResult(text, (len(system) + len(prompt)) // 4, len(text) // 4, self.model)

    @staticmethod
    def _catalogue(message: str) -> dict:
        return json.loads(re.search(r"<catalogue>\n(.*)\n</catalogue>", message, re.DOTALL).group(1))

    def _invalid(self, message: str) -> dict:
        request = json.loads(re.search(r"<traveller_request>\n(.*)\n</traveller_request>", message, re.DOTALL).group(1))
        item = {
            "tourId": 999_999,
            "departureId": 999_999,
            "startTime": f"{request['startDate']}T09:00:00+02:00",
            "durationMinutes": 60,
            "price": "1.00",
            "travelMinutesFromPrevious": 0,
        }
        return {"days": [{"date": request["startDate"], "items": [item]}]}

    def _valid(self, message: str) -> dict:
        """The first departure of the most relevant tours, kept only while they obey the rules."""
        request = json.loads(re.search(r"<traveller_request>\n(.*)\n</traveller_request>", message, re.DOTALL).group(1))
        catalogue = self._catalogue(message)
        roads = {tuple(road["tours"]): road["roadMinutes"] for road in catalogue["roads"]}
        spent, days, chosen = Decimal(0), {}, []
        budget, party = Decimal(request["budget"]), request["partySize"]
        for tour in catalogue["tours"]:
            for departure in tour["departures"]:
                start = datetime.fromisoformat(departure["startTime"])
                end = start + timedelta(minutes=tour["durationMinutes"])
                day = start.date().isoformat()
                cost = Decimal(tour["price"]) * party
                last_day = datetime.fromisoformat(request["startDate"]) + timedelta(days=request["days"] - 1)
                if (
                    day in days
                    or departure["seatsLeft"] < party
                    or spent + cost > budget
                    or end.date() > last_day.date()
                ):
                    continue
                if any(not _clear(tour["tourId"], start, end, other, roads) for other in chosen):
                    continue
                chosen.append((tour["tourId"], start, end))
                spent += cost
                days[day] = {
                    "date": day,
                    "items": [
                        {
                            "tourId": tour["tourId"],
                            "departureId": departure["departureId"],
                            "startTime": departure["startTime"],
                            "durationMinutes": tour["durationMinutes"],
                            "price": tour["price"],
                            "travelMinutesFromPrevious": 0,
                        }
                    ],
                }
                break
        return {"days": [days[day] for day in sorted(days)]}


def _clear(tour_id, start, end, other, roads) -> bool:
    other_id, other_start, other_end = other
    road = roads.get((min(tour_id, other_id), max(tour_id, other_id)), 0)
    gap = (start - other_end) if other_start <= start else (other_start - end)
    return gap.total_seconds() / 60 >= road


def complete_with_retries(
    client: LLMClient,
    *,
    system: str,
    messages: list[dict],
    schema: dict,
    max_tokens: int,
    timeout: float,
    deadline: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> LlmResult:
    """One planning call. deadline is on clock's scale, and bounds this call's retries along with everything before
    it."""
    for attempt in range(MAX_RETRIES + 1):
        remaining = deadline - clock()
        if remaining <= 0:
            raise LlmTransientError("Out of time")
        began = time.perf_counter()
        attributes = {
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": client.provider,
            "gen_ai.request.model": client.model,
        }
        try:
            result = client.complete(system, messages, schema, max_tokens, min(timeout, remaining))
        except LlmError as exc:
            _record(began, {**attributes, "error.type": exc.kind}, exc.input_tokens, exc.output_tokens)
            if not isinstance(exc, LlmTransientError) or attempt == MAX_RETRIES:
                raise
            delay = BACKOFF_S * 2**attempt * random.uniform(0.5, 1.5)
            log.warning("LLM call failed (%s), retrying in %.1fs", exc, delay)
            sleep(min(delay, max(deadline - clock(), 0)))
            continue
        _record(began, {**attributes, "gen_ai.response.model": result.model}, result.input_tokens, result.output_tokens)
        return result
    raise AssertionError("unreachable: the last attempt returns or raises")


def _record(began: float, attributes: dict, input_tokens: int, output_tokens: int) -> None:
    _duration.record(time.perf_counter() - began, attributes)
    usage = {**attributes, "gen_ai.token.modality": "text"}
    # The conventions want strictly positive measurements.
    if input_tokens > 0:
        _input_tokens.add(input_tokens, usage)
    if output_tokens > 0:
        _output_tokens.add(output_tokens, usage)


def build_client(settings: Settings) -> LLMClient | None:
    match settings.llm_provider:
        case "":
            return None
        case "fake":
            return FakeLLMClient(settings.llm_fake_scenario)
        case "anthropic":
            if not settings.anthropic_api_key:
                raise ValueError("LLM_PROVIDER=anthropic needs ANTHROPIC_API_KEY")
            return AnthropicClient(
                settings.anthropic_api_key,
                settings.anthropic_model,
                settings.anthropic_effort,
                settings.anthropic_fallbacks,
                settings.anthropic_base_url,
            )
        case "openai":
            if not settings.openai_base_url or not settings.openai_model:
                raise ValueError("LLM_PROVIDER=openai needs OPENAI_BASE_URL and OPENAI_MODEL")
            return OpenAiCompatibleClient(settings.openai_base_url, settings.openai_api_key, settings.openai_model)
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")


@lru_cache
def get_llm_client() -> LLMClient | None:
    return build_client(get_settings())
