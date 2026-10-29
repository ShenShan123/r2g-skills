"""Provider-only adapters; host agents retain their native tools and loops."""
import json
import re
import sys
from pathlib import Path

from bridge import ROOT


def flash_key(path="/data1/zhangdy/.llm_env"):
    # No import of an R2G runner or its hardware/tool configuration.
    blocks, current = [], {}
    for line in Path(path).read_text().splitlines():
        match = re.match(r"\s*export\s+(DEEPSEEK_[A-Z_]+)=(.*)$", line)
        if not match: continue
        name, value = match[1], match[2].strip().strip('"').strip("'")
        if name == "DEEPSEEK_API_KEY" and current:
            blocks.append(current); current = {}
        current[name] = value
    if current: blocks.append(current)
    candidates = [b for b in blocks if b.get("DEEPSEEK_MODEL") == "deepseek-v4.1-flash"
                  and b.get("DEEPSEEK_API_KEY")]
    if len(candidates) != 1: raise ValueError("exactly one configured Flash credential block required")
    return candidates[0]["DEEPSEEK_API_KEY"]


class ScoutClient:
    def __init__(self, provider):
        self.provider = provider
        self.model = provider.model
        self.reasoning = {}

    def chat_completion(self, messages, tools=None, tool_choice="auto"):
        from rtlscout.llm_client import ChatResponse, TokenUsage, ToolCall
        wire = []
        for raw in messages:
            message = dict(raw)
            ids = tuple(t["id"] for t in message.get("tool_calls") or [])
            if message.get("role") == "assistant" and ids in self.reasoning:
                message["reasoning_content"] = self.reasoning[ids]
            wire.append(message)
        body = {"messages": wire}
        if tools:
            body.update(tools=tools, tool_choice=tool_choice)
        data = self.provider.request(body, purpose="decision" if tools else "summary")
        msg = data["choices"][0]["message"]
        ids = tuple(t["id"] for t in msg.get("tool_calls") or [])
        if ids and isinstance(msg.get("reasoning_content"), str):
            self.reasoning[ids] = msg["reasoning_content"]
        usage = data["usage"]
        cached = usage.get("prompt_cache_hit_tokens", (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
        return ChatResponse(content=msg.get("content"),
            tool_calls=[ToolCall(id=t["id"], name=t["function"]["name"],
                                 arguments=t["function"]["arguments"]) for t in msg.get("tool_calls") or []],
            usage=TokenUsage(input_tokens=usage["prompt_tokens"]-cached,
                             output_tokens=usage["completion_tokens"], cache_read_input_tokens=cached),
            provider="official-deepseek", finish_reason=data["choices"][0].get("finish_reason"))


class LegacyFunctionsClient:
    """Map old OpenAI function-call transport to the current tools wire schema."""
    def __init__(self, provider):
        self.provider = provider

    def create(self, **kwargs):
        body = {k: v for k, v in kwargs.items()
                if k not in {"model", "functions", "function_call", "api_key", "api_base", "request_timeout"}}
        pending = None
        messages = []
        for i, raw in enumerate(body["messages"]):
            msg = dict(raw)
            if msg.get("function_call"):
                call = dict(msg.pop("function_call"))
                reasoning = call.pop("_table4_reasoning_content", None)
                call.pop("_table4_provider_id", None)
                if reasoning is not None:
                    msg["reasoning_content"] = reasoning
                pending = "native_call_" + str(i)
                msg["tool_calls"] = [{"id": pending, "type": "function", "function": call}]
            if msg.get("role") == "function":
                if pending is None: raise ValueError("native function result lacks its call")
                msg.update(role="tool", tool_call_id=pending)
                msg.pop("name", None)
            messages.append(msg)
        body["messages"] = messages
        if kwargs.get("functions"):
            body["tools"] = [{"type": "function", "function": f} for f in kwargs["functions"]]
            choice = kwargs.get("function_call", "auto")
            body["tool_choice"] = {"type": "function", "function": choice} if isinstance(choice, dict) else choice
            body["parallel_tool_calls"] = False
        result = self.provider.request(body)
        for choice in result["choices"]:
            msg = choice["message"]
            calls = msg.pop("tool_calls", None)
            if calls:
                if len(calls) != 1: raise ValueError("legacy host expects a single function call")
                # LangChain preserves the function_call dictionary in
                # AIMessage.additional_kwargs. Keep exact provider metadata
                # there, then remove it before sending native tool arguments.
                msg["function_call"] = {**calls[0]["function"],
                    "_table4_reasoning_content": msg.pop("reasoning_content", None),
                    "_table4_provider_id": result.get("id")}
        return result
