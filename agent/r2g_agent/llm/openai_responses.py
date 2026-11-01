"""OpenAI Responses protocol, streamed, with function calling.

GPT-6 models (gpt-6-astra, gpt-6.1-sol, gpt-6.1-terra) accept Chat Completions
but call tools only through Responses (OpenAI migration guide, checked
2026-10-31), so this adapter speaks /responses with the same event interface as
OpenAICompatAdapter.

Requests are stateless (store=false): the whole conversation is sent each turn,
as with Chat Completions, so the harnesses' own compaction keeps working. The
model's reasoning items come back encrypted and are replayed in front of the
assistant turn that produced them, so the model keeps its reasoning across tool
calls. They are held by the adapter, keyed by that turn's first tool-call id
(or its text), and never enter the shared message shape: a compacted-away turn
simply has nothing to replay.
"""
from __future__ import annotations

import json

import httpx

from . import retry
from .usage import usage_event


class OpenAIResponsesAdapter:
    def __init__(self, mc, key: str | None, reasoning_effort: str | None = None):
        self.mc, self.key = mc, key
        self.reasoning_effort = reasoning_effort
        self._reasoning: dict[str, list] = {}

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = "Bearer " + self.key
        return h

    @staticmethod
    def _turn_key(tool_call_ids: list[str], text: str) -> str:
        return ("call:" + tool_call_ids[0]) if tool_call_ids else ("text:" + text)

    def to_wire(self, messages: list[dict]) -> list[dict]:
        out = []
        for m in messages:
            if m["role"] == "assistant":
                calls = m.get("tool_calls") or []
                out += self._reasoning.get(self._turn_key([t["id"] for t in calls], m.get("content") or ""), [])
                if m.get("content"):
                    out.append({"role": "assistant", "content": m["content"]})
                for t in calls:
                    out.append({"type": "function_call", "call_id": t["id"], "name": t["name"],
                                "arguments": json.dumps(t["arguments"], ensure_ascii=False)})
            elif m["role"] == "tool":
                out.append({"type": "function_call_output", "call_id": m["tool_call_id"],
                            "output": m["content"]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def stream(self, messages: list[dict], tools: list[dict], system: str = ""):
        # no temperature: reasoning models reject it
        body = {"model": self.mc.model, "input": self.to_wire(messages), "stream": True,
                "store": False, "include": ["reasoning.encrypted_content"],
                "max_output_tokens": self.mc.max_tokens}
        if system:
            body["instructions"] = system
        if tools:
            body["tools"] = [{"type": "function", "name": t["name"], "description": t.get("description", ""),
                              "parameters": t.get("parameters") or {"type": "object", "properties": {}}}
                             for t in tools]
        if self.reasoning_effort:
            body["reasoning"] = {"effort": self.reasoning_effort}
        calls, reasoning, text = [], [], ""
        stop = "stop"
        usage, response_id, complete, closing = {}, None, False, False
        retries: list = []
        try:
            with retry.stream(lambda: httpx.stream("POST", self.mc.base_url + "/responses", headers=self._headers(),
                                                  json=body, timeout=httpx.Timeout(300, connect=20)), retries) as r:
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
                    kind = ev.get("type", "")
                    if kind == "response.output_text.delta":
                        text += ev.get("delta") or ""
                        yield {"type": "text", "delta": ev.get("delta") or ""}
                    elif kind == "response.reasoning_summary_text.delta":
                        yield {"type": "thinking", "delta": ev.get("delta") or ""}
                    elif kind == "response.output_item.done":
                        item = ev.get("item") or {}
                        if item.get("type") == "function_call":
                            calls.append(item)
                        elif item.get("type") == "reasoning":
                            reasoning.append(item)
                    elif kind in ("response.created", "response.completed", "response.incomplete", "response.failed"):
                        resp = ev.get("response") or {}
                        response_id = resp.get("id") or response_id
                        if isinstance(resp.get("usage"), dict):
                            usage.update(resp["usage"])
                        if kind == "response.completed":
                            complete = True
                        elif kind == "response.incomplete":
                            complete = True
                            stop = "length" if (resp.get("incomplete_details") or {}).get("reason") == "max_output_tokens" \
                                else "incomplete"
                        elif kind == "response.failed":
                            err = resp.get("error") or {}
                            yield {"type": "error", "message": "模型请求失败：%s" % (err.get("message") or err.get("code") or "failed")}
                            return
                    elif kind == "error" or ("error" in ev and not kind):   # relay's interrupted-stream marker
                        err = ev.get("error") if isinstance(ev.get("error"), dict) else ev
                        yield {"type": "error", "message": "模型请求失败：%s" % (err.get("message") or "stream error")}
                        return
        except GeneratorExit:
            closing = True
            raise
        except httpx.HTTPError as e:
            yield {"type": "error", "message": "连接模型失败：%s" % e}
            return
        except Exception as e:                                  # noqa: BLE001
            yield {"type": "error", "message": "调用模型出错：%s: %s" % (type(e).__name__, e)}
            return
        finally:
            if not closing:
                yield usage_event(self.mc, usage, response_id, complete, retries)
        ids = [c.get("call_id") or "call_%d" % i for i, c in enumerate(calls)]
        if reasoning:
            self._reasoning[self._turn_key(ids, text)] = reasoning
        for cid, c in zip(ids, calls):
            raw = c.get("arguments") or ""
            try:
                args = json.loads(raw) if raw.strip() else {}
            except ValueError:
                args = {"_unparsed": raw}
            yield {"type": "tool_call", "id": cid, "name": c.get("name", ""), "arguments": args}
        yield {"type": "done", "stop_reason": "tool_calls" if calls else stop}
