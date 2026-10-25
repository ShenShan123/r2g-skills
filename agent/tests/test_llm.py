import contextlib
import json

import httpx
import pytest

from r2g_agent.config import ModelConfig
from r2g_agent.llm.anthropic import AnthropicAdapter
from r2g_agent.llm.openai_compat import OpenAICompatAdapter

HIST = [{"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "f", "arguments": {"a": 1}}]},
        {"role": "tool", "tool_call_id": "t1", "name": "f", "content": "{\"r\":1}"}]


def _fake_stream(lines, status=200, headers=None):
    class R:
        status_code = status
        def iter_lines(self):
            return iter(lines)
        def read(self):
            return b"boom"
    R.headers = headers or {}
    @contextlib.contextmanager
    def s(*a, **k):
        _fake_stream.last = k
        yield R()
    return s


def test_openai_wire_and_stream(monkeypatch):
    w = OpenAICompatAdapter.to_wire(HIST, "sys")
    assert w[0] == {"role": "system", "content": "sys"}
    assert json.loads(w[2]["tool_calls"][0]["function"]["arguments"]) == {"a": 1}
    assert w[3] == {"role": "tool", "tool_call_id": "t1", "content": "{\"r\":1}"}
    lines = ['data: {"choices":[{"delta":{"content":"hi"}}]}',
             'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"job_","arguments":"{\\"job"}}]}}]}',
             'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"status","arguments":"_id\\":\\"x\\"}"}}]},"finish_reason":"tool_calls"}]}',
             "data: [DONE]"]
    monkeypatch.setattr(httpx, "stream", _fake_stream(lines))
    ad = OpenAICompatAdapter(ModelConfig(provider="deepseek").resolved(), "k")
    evs = list(ad.stream(HIST, [{"name": "job_status", "description": "d", "parameters": {}}], "sys"))
    assert evs[0] == {"type": "text", "delta": "hi"}
    assert evs[1] == {"type": "tool_call", "id": "c1", "name": "job_status", "arguments": {"job_id": "x"}}
    assert evs[-1]["stop_reason"] == "tool_calls"
    assert _fake_stream.last["headers"]["Authorization"] == "Bearer k"


def test_openai_http_error(monkeypatch):
    monkeypatch.setattr(httpx, "stream", _fake_stream([], status=401))
    ad = OpenAICompatAdapter(ModelConfig(provider="openai").resolved(), "k")
    assert list(ad.stream(HIST, [], ""))[0]["type"] == "error"


def test_anthropic_wire_and_stream(monkeypatch):
    w = AnthropicAdapter.to_wire(HIST + [{"role": "tool", "tool_call_id": "t2", "name": "g", "content": "x"}])
    assert w[1]["content"][0]["type"] == "tool_use"
    # consecutive tool results share one user turn
    assert [b["tool_use_id"] for b in w[2]["content"]] == ["t1", "t2"]
    lines = ['data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
             'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"ok"}}',
             'data: {"type":"content_block_start","index":1,"content_block":{"type":"tool_use","id":"tu1","name":"list_jobs"}}',
             'data: {"type":"content_block_delta","index":1,"delta":{"type":"input_json_delta","partial_json":"{\\"limit\\": 3}"}}',
             'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"}}']
    monkeypatch.setattr(httpx, "stream", _fake_stream(lines))
    ad = AnthropicAdapter(ModelConfig(provider="anthropic").resolved(), "k")
    evs = list(ad.stream(HIST, [{"name": "list_jobs", "description": "d", "parameters": {}}], "sys"))
    assert evs[0] == {"type": "text", "delta": "ok"}
    assert evs[1] == {"type": "tool_call", "id": "tu1", "name": "list_jobs", "arguments": {"limit": 3}}
    assert _fake_stream.last["headers"]["x-api-key"] == "k"
    assert _fake_stream.last["json"]["tools"][0]["input_schema"] == {}


def test_every_provider_resolves():
    from r2g_agent.config import PROVIDERS
    for p, pre in PROVIDERS.items():
        r = ModelConfig(provider=p).resolved()
        assert r.protocol in ("openai", "anthropic")
        if p not in ("custom",):
            assert r.base_url.startswith("http")


def test_thinking_switch_per_provider():
    from r2g_agent.config import ModelConfig
    from r2g_agent.llm.openai_compat import thinking_params
    assert thinking_params(ModelConfig(provider="deepseek", thinking=False)) == {"thinking": {"type": "disabled"}}
    assert thinking_params(ModelConfig(provider="deepseek")) == {"thinking": {"type": "enabled"}}
    assert thinking_params(ModelConfig(provider="qwen", thinking=False)) == {"enable_thinking": False}
    assert thinking_params(ModelConfig(provider="kimi", thinking=False)) == {}   # no known switch: untouched


def _sse(*payloads):
    return ["data: " + json.dumps(payload) for payload in payloads]


def test_openai_terminal_usage_and_observed_model_preserve_tool_calls(monkeypatch):
    lines = _sse(
        {"id": "chat-123", "model": "observed-revision", "usage": None,
         "choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
             "name": "job_status", "arguments": '{"job_id":'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {
            "arguments": '"j1"}'}}]}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 51, "completion_tokens": 9, "total_tokens": 60,
            "prompt_cache_hit_tokens": 32, "completion_tokens_details": {"reasoning_tokens": 3},
            "reasoning_content": "must not enter metadata"}},
    ) + ["data: [DONE]"]
    monkeypatch.setattr(httpx, "stream", _fake_stream(lines, headers={"x-request-id": "req-123"}))
    adapter = OpenAICompatAdapter(ModelConfig(provider="deepseek", model="requested-alias").resolved(), None)
    events = list(adapter.stream(HIST, []))
    assert events[0] == {"type": "tool_call", "id": "c1", "name": "job_status", "arguments": {"job_id": "j1"}}
    assert events[-1]["metadata"] == {
        "provider": "deepseek", "model_requested": "requested-alias", "model_observed": "observed-revision",
        "response_id": "chat-123", "request_id": "req-123", "stream_complete": True,
        "usage": {"status": "reported", "input_tokens": 51, "output_tokens": 9, "total_tokens": 60,
                  "cache_read_input_tokens": 32, "cache_creation_input_tokens": None, "reasoning_tokens": 3},
    }
    assert "stream_options" not in _fake_stream.last["json"]


@pytest.mark.parametrize("usage,expected", [
    (None, "unresolved"), ({}, "unresolved"),
    ({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}, "reported"),
    ({"prompt_tokens": 17}, "partial"),
    ({"prompt_tokens": True, "completion_tokens": -1, "total_tokens": "17"}, "unresolved"),
])
def test_openai_missing_usage_is_unknown_not_zero(monkeypatch, usage, expected):
    monkeypatch.setattr(httpx, "stream", _fake_stream(_sse({"usage": usage, "choices": []}) + ["data: [DONE]"]))
    events = list(OpenAICompatAdapter(ModelConfig(provider="custom", model="alias"), None).stream([], []))
    metadata = events[-1]["metadata"]
    assert metadata["model_requested"] == "alias"
    assert metadata["model_observed"] is None
    assert metadata["response_id"] is None
    assert metadata["usage"]["status"] == expected
    if expected == "unresolved":
        assert all(value is None for key, value in metadata["usage"].items() if key != "status")
    if expected == "partial":
        assert metadata["usage"]["output_tokens"] is None
        assert metadata["usage"]["total_tokens"] is None


@pytest.mark.parametrize("broken_transport", [False, True])
def test_openai_interrupted_stream_usage_is_partial(monkeypatch, broken_transport):
    def lines():
        yield from _sse({"id": "chat-partial", "model": "revision", "choices": [],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 2}})
        if broken_transport:
            raise httpx.ReadError("connection lost")
    monkeypatch.setattr(httpx, "stream", _fake_stream(lines()))
    terminal = list(OpenAICompatAdapter(ModelConfig(provider="openai").resolved(), None).stream([], []))[-1]
    assert terminal["type"] == ("error" if broken_transport else "done")
    assert terminal["metadata"]["stream_complete"] is False
    assert terminal["metadata"]["usage"]["status"] == "partial"
    assert terminal["metadata"]["model_observed"] == "revision"


def test_anthropic_usage_is_cumulative_and_cache_counts_remain_separate(monkeypatch):
    lines = _sse(
        {"type": "message_start", "message": {"id": "msg-123", "model": "claude-revision", "usage": {
            "input_tokens": 5, "output_tokens": 0, "cache_read_input_tokens": 80, "cache_creation_input_tokens": 20}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private text"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "ok"}},
        {"type": "message_delta", "delta": {"stop_reason": None}, "usage": {"output_tokens": 2}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}},
        {"type": "message_stop"},
    )
    monkeypatch.setattr(httpx, "stream", _fake_stream(lines, headers={"request-id": "req-123"}))
    events = list(AnthropicAdapter(ModelConfig(provider="anthropic", model="claude-alias").resolved(), None).stream([], []))
    assert events[0] == {"type": "text", "delta": "ok"}
    assert events[-1]["metadata"] == {
        "provider": "anthropic", "model_requested": "claude-alias", "model_observed": "claude-revision",
        "response_id": "msg-123", "request_id": "req-123", "stream_complete": True,
        "usage": {"status": "reported", "input_tokens": 5, "output_tokens": 7, "total_tokens": None,
                  "cache_read_input_tokens": 80, "cache_creation_input_tokens": 20, "reasoning_tokens": None},
    }
    assert "private text" not in json.dumps(events)


@pytest.mark.parametrize("last_event", [{"type": "message_stop"}, {"type": "error", "error": {"type": "overloaded_error"}}])
def test_anthropic_initial_usage_does_not_claim_final_counts(monkeypatch, last_event):
    lines = _sse(
        {"type": "message_start", "message": {"id": "msg-123", "usage": {"input_tokens": 5, "output_tokens": 0}}},
        last_event,
    )
    monkeypatch.setattr(httpx, "stream", _fake_stream(lines))
    terminal = list(AnthropicAdapter(ModelConfig(provider="anthropic").resolved(), None).stream([], []))[-1]
    assert terminal["metadata"]["usage"]["status"] == "partial"
    assert terminal["metadata"]["usage"]["output_tokens"] == 0


def test_http_failure_has_unresolved_usage_and_request_id(monkeypatch):
    monkeypatch.setattr(httpx, "stream", _fake_stream([], status=429, headers={"x-request-id": "req-failed"}))
    for adapter_type, provider in ((AnthropicAdapter, "anthropic"), (OpenAICompatAdapter, "deepseek")):
        terminal = list(adapter_type(ModelConfig(provider=provider).resolved(), None).stream([], []))[-1]
        assert terminal["type"] == "error"
        assert terminal["metadata"]["request_id"] == "req-failed"
        assert terminal["metadata"]["usage"]["status"] == "unresolved"


def test_openai_private_reasoning_is_not_a_public_event(monkeypatch):
    lines = _sse({'choices': [{'delta': {'reasoning_content': 'PRIVATE_REASONING', 'content': 'public'}}]})
    monkeypatch.setattr(httpx, 'stream', _fake_stream(lines))
    events = list(OpenAICompatAdapter(ModelConfig(provider='deepseek').resolved(), None).stream([], []))
    assert 'PRIVATE_REASONING' not in json.dumps(events)
    assert {'type': 'text', 'delta': 'public'} in events
