"""OpenAI chat-completions protocol (DeepSeek, Qwen, Kimi, GLM, Doubao, OpenAI,
Gemini's compatible endpoint, Ollama/vLLM), streamed, with function calling."""
from __future__ import annotations

import json

import httpx

from .metadata import ResponseMetadata


def thinking_params(mc) -> dict:
    """The provider's own deep-thinking switch (verified 2026-10-23 on deepseek-flash
    and qwen3.8-flash: both default to ON; these turn it off)."""
    from ..config import PROVIDERS
    kind = PROVIDERS.get(mc.provider, {}).get("thinking")
    if kind == "deepseek":
        return {"thinking": {"type": "enabled" if mc.thinking else "disabled"}}
    if kind == "qwen":
        return {"enable_thinking": bool(mc.thinking)}
    return {}


class OpenAICompatAdapter:
    def __init__(self, mc, key: str | None):
        self.mc, self.key = mc, key

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.key:
            h["Authorization"] = "Bearer " + self.key
        return h

    @staticmethod
    def to_wire(messages: list[dict], system: str) -> list[dict]:
        out = [{"role": "system", "content": system}] if system else []
        for m in messages:
            if m["role"] == "assistant":
                w = {"role": "assistant", "content": m.get("content") or ""}
                if m.get("tool_calls"):
                    w["tool_calls"] = [{"id": t["id"], "type": "function",
                                        "function": {"name": t["name"],
                                                     "arguments": json.dumps(t["arguments"], ensure_ascii=False)}}
                                       for t in m["tool_calls"]]
                out.append(w)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def stream(self, messages: list[dict], tools: list[dict], system: str = ""):
        body = {"model": self.mc.model, "messages": self.to_wire(messages, system), "stream": True,
                "temperature": self.mc.temperature, "max_tokens": self.mc.max_tokens}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        body.update(thinking_params(self.mc))
        calls: dict[int, dict] = {}
        stop = "stop"
        metadata = ResponseMetadata(self.mc.provider, self.mc.model)
        stream_complete = False
        try:
            with httpx.stream("POST", self.mc.base_url + "/chat/completions", headers=self._headers(),
                              json=body, timeout=httpx.Timeout(180, connect=20)) as r:
                metadata.headers(getattr(r, "headers", {}))
                if r.status_code >= 400:
                    yield {"type": "error", "message": "HTTP %d: %s" % (r.status_code, r.read().decode(errors="replace")[:500]),
                           "metadata": metadata.snapshot()}
                    return
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        stream_complete = True
                        break
                    try:
                        ev = json.loads(data)
                    except ValueError:
                        continue
                    metadata.identity(ev)
                    metadata.openai_usage(ev.get("usage"))
                    for ch in ev.get("choices") or []:
                        d = ch.get("delta") or {}
                        if d.get("content"):
                            yield {"type": "text", "delta": d["content"]}
                        for tc in d.get("tool_calls") or []:
                            slot = calls.setdefault(tc.get("index", len(calls)), {"id": "", "name": "", "args": ""})
                            slot["id"] = tc.get("id") or slot["id"]
                            fn = tc.get("function") or {}
                            slot["name"] += fn.get("name") or ""
                            slot["args"] += fn.get("arguments") or ""
                        if ch.get("finish_reason"):
                            stop = ch["finish_reason"]
        except httpx.HTTPError as e:
            yield {"type": "error", "message": "连接模型失败：%s" % e, "metadata": metadata.snapshot()}
            return
        except Exception as e:                                  # noqa: BLE001
            # e.g. a SOCKS proxy in the environment without socksio installed:
            # report it in the conversation instead of failing the request
            yield {"type": "error", "message": "调用模型出错：%s: %s" % (type(e).__name__, e),
                   "metadata": metadata.snapshot()}
            return
        for i in sorted(calls):
            c = calls[i]
            try:
                args = json.loads(c["args"]) if c["args"].strip() else {}
            except ValueError:
                args = {"_unparsed": c["args"]}
            yield {"type": "tool_call", "id": c["id"] or "call_%d" % i, "name": c["name"], "arguments": args}
        yield {"type": "done", "stop_reason": "tool_calls" if calls else stop,
               "metadata": metadata.snapshot(stream_complete=stream_complete)}
