"""Anthropic Messages protocol (Claude), streamed, with tool use."""
from __future__ import annotations

import json

import httpx


class AnthropicAdapter:
    VERSION = "2023-06-01"

    def __init__(self, mc, key: str | None):
        self.mc, self.key = mc, key

    @staticmethod
    def to_wire(messages: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "assistant":
                blocks = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for t in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": t["id"], "name": t["name"], "input": t["arguments"]})
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                # consecutive tool results go into ONE user turn
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list) \
                        and out[-1]["content"] and out[-1]["content"][0].get("type") == "tool_result":
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def stream(self, messages: list[dict], tools: list[dict], system: str = ""):
        body = {"model": self.mc.model, "max_tokens": self.mc.max_tokens, "temperature": self.mc.temperature,
                "messages": self.to_wire(messages), "stream": True}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["name"], "description": t["description"],
                              "input_schema": t["parameters"]} for t in tools]
        headers = {"x-api-key": self.key or "", "anthropic-version": self.VERSION,
                   "content-type": "application/json"}
        blocks: dict[int, dict] = {}
        stop = "end_turn"
        try:
            with httpx.stream("POST", self.mc.base_url + "/v1/messages", headers=headers, json=body,
                              timeout=httpx.Timeout(180, connect=20)) as r:
                if r.status_code >= 400:
                    yield {"type": "error", "message": "HTTP %d: %s" % (r.status_code, r.read().decode(errors="replace")[:500])}
                    return
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    t = ev.get("type")
                    if t == "content_block_start":
                        cb = ev["content_block"]
                        blocks[ev["index"]] = {"type": cb["type"], "id": cb.get("id"), "name": cb.get("name"),
                                               "json": ""}
                    elif t == "content_block_delta":
                        d = ev["delta"]
                        if d.get("type") == "text_delta":
                            yield {"type": "text", "delta": d["text"]}
                        elif d.get("type") == "input_json_delta":
                            blocks[ev["index"]]["json"] += d.get("partial_json", "")
                    elif t == "message_delta":
                        stop = (ev.get("delta") or {}).get("stop_reason") or stop
                    elif t == "error":
                        yield {"type": "error", "message": str(ev.get("error"))}
                        return
        except httpx.HTTPError as e:
            yield {"type": "error", "message": "连接模型失败：%s" % e}
            return
        for i in sorted(blocks):
            b = blocks[i]
            if b["type"] == "tool_use":
                try:
                    args = json.loads(b["json"]) if b["json"].strip() else {}
                except ValueError:
                    args = {"_unparsed": b["json"]}
                yield {"type": "tool_call", "id": b["id"], "name": b["name"], "arguments": args}
        yield {"type": "done", "stop_reason": stop}
