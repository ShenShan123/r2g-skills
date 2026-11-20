"""Anthropic Messages protocol (Claude), streamed, with tool use."""
from __future__ import annotations

import json

import httpx

from . import retry
from .usage import usage_event


class AnthropicAdapter:
    VERSION = "2023-06-01"

    def __init__(self, mc, key: str | None):
        self.mc, self.key = mc, key
        # Signed thinking must be replayed unchanged, in its original position
        # among text/tool blocks. Keep protocol blocks private to this adapter;
        # the shared conversation format remains provider independent.
        self._assistant_blocks: dict[str, list[dict]] = {}

    @staticmethod
    def _turn_key(calls: list[dict], text: str) -> str:
        return ("call:" + calls[0]["id"]) if calls else ("text:" + text)

    @staticmethod
    def to_wire(messages: list[dict], assistant_blocks: dict[str, list[dict]] | None = None) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            if m["role"] == "assistant":
                calls = m.get("tool_calls") or []
                original = (assistant_blocks or {}).get(AnthropicAdapter._turn_key(calls, m.get("content") or ""))
                # A changed/compacted turn must not silently restore stale text or
                # tool arguments. Replay only an unchanged assistant turn.
                if original is not None:
                    original_text = "".join(b.get("text", "") for b in original if b["type"] == "text")
                    original_calls = [{"id": b["id"], "name": b["name"], "arguments": b["input"]}
                                      for b in original if b["type"] == "tool_use"]
                    if original_text == (m.get("content") or "") and original_calls == calls:
                        out.append({"role": "assistant", "content": original})
                        continue
                blocks = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for t in calls:
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
                "messages": self.to_wire(messages, self._assistant_blocks), "stream": True}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [{"name": t["name"], "description": t["description"],
                              "input_schema": t["parameters"]} for t in tools]
        headers = {"x-api-key": self.key or "", "anthropic-version": self.VERSION,
                   "content-type": "application/json"}
        blocks: dict[int, dict] = {}
        stop = "end_turn"
        usage, response_id, complete, closing = {}, None, False, False
        retries: list = []
        try:
            with retry.stream(lambda: httpx.stream("POST", self.mc.base_url + "/v1/messages", headers=headers, json=body,
                                                  timeout=httpx.Timeout(180, connect=20)), retries) as r:
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
                    if t == "message_start":
                        msg = ev.get("message") or {}
                        response_id = msg.get("id") or response_id
                        usage.update(msg.get("usage") or {})
                    elif t == "message_stop":
                        complete = True
                    elif t == "content_block_start":
                        cb = ev["content_block"]
                        blocks[ev["index"]] = {**cb, "_json": ""}
                        if cb["type"] == "text" and cb.get("text"):
                            yield {"type": "text", "delta": cb["text"]}
                    elif t == "content_block_delta":
                        d = ev["delta"]
                        block = blocks[ev["index"]]
                        if d.get("type") == "text_delta":
                            block["text"] = block.get("text", "") + d["text"]
                            yield {"type": "text", "delta": d["text"]}
                        elif d.get("type") == "input_json_delta":
                            block["_json"] += d.get("partial_json", "")
                        elif d.get("type") == "thinking_delta":
                            block["thinking"] = block.get("thinking", "") + d.get("thinking", "")
                            yield {"type": "thinking", "delta": d.get("thinking", "")}
                        elif d.get("type") == "signature_delta":
                            block["signature"] = block.get("signature", "") + d.get("signature", "")
                    elif t == "message_delta":
                        usage.update(ev.get("usage") or {})
                        stop = (ev.get("delta") or {}).get("stop_reason") or stop
                    elif t == "error":
                        yield {"type": "error", "message": str(ev.get("error"))}
                        return
        except GeneratorExit:
            closing = True
            raise
        except httpx.HTTPError as e:
            yield {"type": "error", "message": "连接模型失败：%s" % e}
            return
        except Exception as e:                                  # noqa: BLE001
            # e.g. a SOCKS proxy in the environment without socksio installed:
            # report it in the conversation instead of failing the request
            yield {"type": "error", "message": "调用模型出错：%s: %s" % (type(e).__name__, e)}
            return
        finally:
            if not closing:
                yield usage_event(self.mc, usage, response_id, complete, retries)
        original, calls = [], []
        for i in sorted(blocks):
            b = blocks[i]
            wire = {k: v for k, v in b.items() if k != "_json"}
            if b["type"] == "tool_use":
                try:
                    args = json.loads(b["_json"]) if b["_json"].strip() else b.get("input") or {}
                except ValueError:
                    args = {"_unparsed": b["_json"]}
                wire["input"] = args
                calls.append({"id": b["id"], "name": b["name"], "arguments": args})
            original.append(wire)
        if any(b["type"] in ("thinking", "redacted_thinking") for b in original):
            text = "".join(b.get("text", "") for b in original if b["type"] == "text")
            self._assistant_blocks[self._turn_key(calls, text)] = original
        for call in calls:
            yield {"type": "tool_call", **call}
        yield {"type": "done", "stop_reason": stop}
