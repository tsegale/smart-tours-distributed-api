"""The providers against a local HTTP stub: the real Anthropic SDK and the real request, without a key or a bill.

This proves what we send and how we read and classify responses as documented. It can't prove what a live model does.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.config import Settings
from app.itinerary.prompt import PLAN_SCHEMA
from app.llm import (
    AnthropicClient,
    FakeLLMClient,
    LlmRefusedError,
    LlmRejectedError,
    LlmResult,
    LlmTransientError,
    LlmTruncatedError,
    OpenAiCompatibleClient,
    build_client,
    complete_with_retries,
)


class Stub:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses: list[tuple[int, dict]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                outer.requests.append(
                    {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
                )
                status, payload = outer.responses.pop(0)
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def stub():
    server = Stub()
    yield server
    server.server.shutdown()


def anthropic_message(stop_reason="end_turn", text='{"days": []}'):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


def anthropic(stub) -> AnthropicClient:
    return AnthropicClient("test-key", "claude-test", "medium", "default", stub.url)


def call(client):
    return client.complete("system", [{"role": "user", "content": "hello"}], PLAN_SCHEMA, 1000, 5.0)


def test_anthropic_request_and_response(stub):
    stub.responses.append((200, anthropic_message()))

    result = call(anthropic(stub))

    assert result == LlmResult('{"days": []}', 120, 30, "claude-test")
    sent = stub.requests[0]
    assert sent["path"].startswith("/v1/messages")
    headers = sent["headers"]
    # The SDK decides how the key travels. What matters is that it does.
    assert "test-key" in (headers.get("x-api-key"), headers.get("authorization", "").removeprefix("Bearer "))
    assert headers["anthropic-beta"] == "server-side-fallback-2026-07-01"
    body = sent["body"]
    assert body["model"] == "claude-test"
    assert body["system"] == "system"
    assert body["output_config"] == {"effort": "medium", "format": {"type": "json_schema", "schema": PLAN_SCHEMA}}
    assert body["fallbacks"] == "default"
    assert "tool_choice" not in body, "forced tool use is a 400 on current models"
    assert "temperature" not in body, "sampling parameters are a 400 on the current API version"


def test_anthropic_fallbacks_can_be_turned_off(stub):
    stub.responses.append((200, anthropic_message()))
    call(AnthropicClient("test-key", "claude-test", "medium", "", stub.url))
    assert "fallbacks" not in stub.requests[0]["body"]


def test_anthropic_refusal_keeps_the_usage(stub):
    stub.responses.append((200, anthropic_message(stop_reason="refusal")))
    with pytest.raises(LlmRefusedError) as raised:
        call(anthropic(stub))
    assert (raised.value.input_tokens, raised.value.output_tokens) == (120, 30)


def test_anthropic_truncation(stub):
    stub.responses.append((200, anthropic_message(stop_reason="max_tokens")))
    with pytest.raises(LlmTruncatedError):
        call(anthropic(stub))


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, LlmRejectedError),
        (401, LlmRejectedError),
        (429, LlmRejectedError),
        (500, LlmTransientError),
        (529, LlmTransientError),
    ],
)
def test_anthropic_status_classes(stub, status, expected):
    stub.responses.append((status, {"type": "error", "error": {"type": "x", "message": "no"}}))
    with pytest.raises(expected):
        call(anthropic(stub))
    assert len(stub.requests) == 1, "the SDK must not retry on its own: the policy here decides"


def test_anthropic_connection_failure_is_transient():
    client = AnthropicClient("test-key", "claude-test", "medium", "default", "http://127.0.0.1:9")
    with pytest.raises(LlmTransientError):
        call(client)


def openai_message(finish_reason="stop", content='{"days": []}'):
    return {
        "model": "llama-test",
        "choices": [{"finish_reason": finish_reason, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 90, "completion_tokens": 20},
    }


def test_openai_compatible_request_and_response(stub):
    stub.responses.append((200, openai_message()))

    result = call(OpenAiCompatibleClient(stub.url + "/v1/", "local-key", "llama-test"))

    assert result == LlmResult('{"days": []}', 90, 20, "llama-test")
    sent = stub.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["headers"]["authorization"] == "Bearer local-key"
    assert sent["body"]["messages"][0] == {"role": "system", "content": "system"}
    assert sent["body"]["response_format"]["json_schema"]["schema"] == PLAN_SCHEMA


def test_openai_compatible_sends_no_auth_header_without_a_key(stub):
    stub.responses.append((200, openai_message()))
    call(OpenAiCompatibleClient(stub.url, "", "llama-test"))
    assert "authorization" not in stub.requests[0]["headers"]


@pytest.mark.parametrize(
    ("status", "payload", "expected"),
    [
        (400, {}, LlmRejectedError),
        (429, {}, LlmRejectedError),
        (503, {}, LlmTransientError),
        (200, {"unexpected": True}, LlmTransientError),
        (200, openai_message(finish_reason="length"), LlmTruncatedError),
        (200, openai_message(finish_reason="content_filter"), LlmRefusedError),
    ],
)
def test_openai_compatible_failures(stub, status, payload, expected):
    stub.responses.append((status, payload))
    with pytest.raises(expected):
        call(OpenAiCompatibleClient(stub.url, "", "llama-test"))


class Scripted:
    provider, model = "scripted", "scripted-1"

    def __init__(self, *outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0
        self.timeouts: list[float] = []

    def complete(self, system, messages, schema, max_tokens, timeout):
        self.calls += 1
        self.timeouts.append(timeout)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


OK = LlmResult("{}", 1, 1, "scripted-1")


def retry(client, *, deadline=100.0, clock=lambda: 0.0, sleep=lambda _: None, timeout=60.0):
    return complete_with_retries(
        client,
        system="s",
        messages=[],
        schema={},
        max_tokens=10,
        timeout=timeout,
        deadline=deadline,
        sleep=sleep,
        clock=clock,
    )


def test_a_transient_failure_is_retried():
    client = Scripted(LlmTransientError("HTTP 503"), LlmTransientError("HTTP 503"), OK)
    assert retry(client) is OK
    assert client.calls == 3


def test_retries_are_bounded():
    client = Scripted(*[LlmTransientError("HTTP 503") for _ in range(5)])
    with pytest.raises(LlmTransientError):
        retry(client)
    assert client.calls == 3


@pytest.mark.parametrize("failure", [LlmRejectedError("HTTP 429"), LlmRefusedError("no"), LlmTruncatedError("cut")])
def test_a_permanent_failure_is_not_retried(failure):
    client = Scripted(failure, OK)
    with pytest.raises(type(failure)):
        retry(client)
    assert client.calls == 1


def test_an_exhausted_deadline_makes_no_call():
    client = Scripted(OK)
    with pytest.raises(LlmTransientError):
        retry(client, deadline=0.0)
    assert client.calls == 0


def test_the_timeout_shrinks_to_the_time_left():
    client = Scripted(OK)
    retry(client, deadline=7.0, timeout=60.0)
    assert client.timeouts == [7.0]


def test_no_provider_means_no_client():
    assert build_client(Settings(llm_provider="")) is None


def test_fake_provider_needs_no_key():
    assert isinstance(build_client(Settings(llm_provider="fake")), FakeLLMClient)


@pytest.mark.parametrize(
    "settings",
    [
        {"llm_provider": "anthropic", "anthropic_api_key": ""},
        {"llm_provider": "openai", "openai_base_url": ""},
        {"llm_provider": "something-else"},
    ],
)
def test_a_misconfigured_provider_fails_loudly(settings):
    with pytest.raises(ValueError):
        build_client(Settings(**settings))
