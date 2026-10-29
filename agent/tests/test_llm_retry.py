"""Pre-stream reconnects: retried, recorded on the one call, never hiding tokens."""
import contextlib

import httpx
import pytest

from r2g_agent.config import ModelConfig
from r2g_agent.llm import retry
from r2g_agent.llm.anthropic import AnthropicAdapter
from r2g_agent.llm.openai_compat import OpenAICompatAdapter

OK_LINES = ['data: {"id":"r1","choices":[{"delta":{"content":"hi"},"finish_reason":"stop"}]}',
            'data: {"id":"r1","choices":[],"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}}',
            "data: [DONE]"]


def _script(steps):
    """steps: list of exceptions, int statuses, or line lists (200)."""
    calls = {"n": 0}

    @contextlib.contextmanager
    def fake(*a, **k):
        step = steps[min(calls["n"], len(steps) - 1)]
        calls["n"] += 1
        if isinstance(step, Exception):
            raise step

        class R:
            status_code = step if isinstance(step, int) else 200
            headers = {}
            def iter_lines(self):
                for line in ([] if isinstance(step, int) else step):
                    if isinstance(line, Exception):
                        raise line
                    yield line
            def read(self):
                return b"upstream unavailable"
        yield R()
    return fake, calls


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(retry, "SLEEP", lambda s: None)


def _openai():
    return OpenAICompatAdapter(ModelConfig(provider="deepseek").resolved(), "k")


def test_connect_error_and_502_are_retried_within_one_call(monkeypatch):
    fake, calls = _script([httpx.ConnectError("refused"), 502, OK_LINES])
    monkeypatch.setattr(httpx, "stream", fake)
    evs = list(_openai().stream([{"role": "user", "content": "q"}], [], ""))
    usage = [e for e in evs if e["type"] == "usage"]
    assert calls["n"] == 3 and len(usage) == 1
    assert usage[0]["stream_complete"] and usage[0]["total_tokens"] == 12
    assert [r["reason"] for r in usage[0]["transport_retries"]] == ["ConnectError", "HTTP 502"]
    assert not any(e["type"] == "error" for e in evs)


def test_client_errors_are_not_retried(monkeypatch):
    fake, calls = _script([401, OK_LINES])
    monkeypatch.setattr(httpx, "stream", fake)
    evs = list(_openai().stream([{"role": "user", "content": "q"}], [], ""))
    assert calls["n"] == 1 and evs[0]["type"] == "error"


def test_a_started_stream_is_never_retried(monkeypatch):
    broken = OK_LINES[:1] + [httpx.ReadError("reset")]
    fake, calls = _script([broken, OK_LINES])
    monkeypatch.setattr(httpx, "stream", fake)
    evs = list(_openai().stream([{"role": "user", "content": "q"}], [], ""))
    usage = [e for e in evs if e["type"] == "usage"][0]
    assert calls["n"] == 1 and any(e["type"] == "error" for e in evs)
    assert usage["stream_complete"] is False and usage["transport_retries"] == []


def test_retries_are_bounded(monkeypatch):
    monkeypatch.setenv("R2G_LLM_RETRIES", "2")
    fake, calls = _script([503])
    monkeypatch.setattr(httpx, "stream", fake)
    evs = list(_openai().stream([{"role": "user", "content": "q"}], [], ""))
    usage = [e for e in evs if e["type"] == "usage"][0]
    assert calls["n"] == 3 and evs[0]["type"] == "error"
    assert len(usage["transport_retries"]) == 2 and usage["stream_complete"] is False


def test_anthropic_overload_is_retried(monkeypatch):
    lines = ['data: {"type":"message_start","message":{"id":"m1","usage":{"input_tokens":5,"output_tokens":1}}}',
             'data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
             'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}',
             'data: {"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}',
             'data: {"type":"message_stop"}']
    fake, calls = _script([529, lines])
    monkeypatch.setattr(httpx, "stream", fake)
    ad = AnthropicAdapter(ModelConfig(provider="anthropic", protocol="anthropic").resolved(), "k")
    usage = [e for e in ad.stream([{"role": "user", "content": "q"}], [], "") if e["type"] == "usage"][0]
    assert calls["n"] == 2 and usage["stream_complete"]
    assert usage["transport_retries"][0]["reason"] == "HTTP 529"


def test_retry_after_header_is_honoured():
    class R:
        headers = {"retry-after": "7"}
    assert retry._delay(0, R()) == 7.0
    assert retry._delay(0) == 2.0 and retry._delay(10) == retry.MAX_DELAY_S
