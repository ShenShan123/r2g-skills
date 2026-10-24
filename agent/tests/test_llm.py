import contextlib
import json

import httpx

from r2g_agent.config import ModelConfig
from r2g_agent.llm.anthropic import AnthropicAdapter
from r2g_agent.llm.openai_compat import OpenAICompatAdapter

HIST = [{"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "t1", "name": "f", "arguments": {"a": 1}}]},
        {"role": "tool", "tool_call_id": "t1", "name": "f", "content": "{\"r\":1}"}]


def _fake_stream(lines, status=200):
    class R:
        status_code = status
        def iter_lines(self):
            return iter(lines)
        def read(self):
            return b"boom"
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
