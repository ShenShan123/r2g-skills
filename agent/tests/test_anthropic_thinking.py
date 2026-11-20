"""Anthropic-compatible thinking survives the tool-result continuation."""
import contextlib
import json

import httpx

from r2g_agent.config import ModelConfig
from r2g_agent.llm.anthropic import AnthropicAdapter


def test_thinking_signature_and_redacted_blocks_replay_in_order(monkeypatch):
    requests = []
    events = [
        {"type": "message_start", "message": {"id": "m1"}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "inspect inputs"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "signed-"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "opaque"}},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": "Checking "}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "files."}},
        {"type": "content_block_start", "index": 2,
         "content_block": {"type": "tool_use", "id": "call1", "name": "shell", "input": {}}},
        {"type": "content_block_delta", "index": 2,
         "delta": {"type": "input_json_delta", "partial_json": '{"command":"ls"}'}},
        {"type": "content_block_start", "index": 3,
         "content_block": {"type": "redacted_thinking", "data": "unchanged-ciphertext"}},
        {"type": "content_block_start", "index": 4,
         "content_block": {"type": "tool_use", "id": "call2", "name": "shell", "input": {"command": "pwd"}}},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    ]

    class Response:
        status_code = 200

        def iter_lines(self):
            return iter("data: " + json.dumps(event) for event in events)

    @contextlib.contextmanager
    def stream(*_args, **kwargs):
        requests.append(kwargs["json"])
        yield Response()

    monkeypatch.setattr(httpx, "stream", stream)
    adapter = AnthropicAdapter(ModelConfig(provider="anthropic").resolved(), "test-key")
    first = list(adapter.stream([{"role": "user", "content": "produce dataset"}], []))
    assert next(e for e in first if e["type"] == "thinking")["delta"] == "inspect inputs"
    calls = [{k: e[k] for k in ("id", "name", "arguments")} for e in first if e["type"] == "tool_call"]
    assert calls[1]["arguments"] == {"command": "pwd"}  # input may arrive entirely in block_start
    history = [
        {"role": "user", "content": "produce dataset"},
        {"role": "assistant", "content": "Checking files.", "tool_calls": calls},
        {"role": "tool", "tool_call_id": "call1", "name": "shell", "content": "files"},
        {"role": "tool", "tool_call_id": "call2", "name": "shell", "content": "/work"},
    ]
    # A second real adapter invocation must send the complete original assistant
    # block order immediately before the combined tool results.
    list(adapter.stream(history, []))
    replay = requests[1]["messages"][1]["content"]
    assert [b["type"] for b in replay] == ["thinking", "text", "tool_use", "redacted_thinking", "tool_use"]
    assert replay[0] == {"type": "thinking", "thinking": "inspect inputs", "signature": "signed-opaque"}
    assert replay[3] == {"type": "redacted_thinking", "data": "unchanged-ciphertext"}
    assert [b["tool_use_id"] for b in requests[1]["messages"][2]["content"]] == ["call1", "call2"]
    assert "thinking" not in history[1]  # no provider blocks in the shared message shape

    compacted = [{**history[1], "content": "compacted"}]
    wire = adapter.to_wire(compacted, adapter._assistant_blocks)
    assert all(b["type"] not in ("thinking", "redacted_thinking") for b in wire[0]["content"])
    assert wire[0]["content"][0] == {"type": "text", "text": "compacted"}


def test_text_only_reasoning_is_keyed_by_its_own_turn():
    adapter = AnthropicAdapter(ModelConfig(provider="anthropic").resolved(), "k")
    adapter._assistant_blocks["text:answer"] = [
        {"type": "thinking", "thinking": "private", "signature": "opaque"},
        {"type": "text", "text": "answer"},
    ]
    wire = adapter.to_wire([{"role": "assistant", "content": "answer"}], adapter._assistant_blocks)
    assert wire[0]["content"][0]["signature"] == "opaque"
    assert adapter.to_wire([{"role": "user", "content": "new task"}], adapter._assistant_blocks) == [
        {"role": "user", "content": "new task"}]
