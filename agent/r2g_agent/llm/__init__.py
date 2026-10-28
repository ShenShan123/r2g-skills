"""Model adapters behind one interface.

Messages use one internal shape regardless of provider:

  {"role": "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments": dict}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}

`stream(messages, tools, system)` yields events:

  {"type": "text", "delta": str}
  {"type": "tool_call", "id": str, "name": str, "arguments": dict}
  {"type": "usage", "input_tokens": int | None, "output_tokens": int | None, ...}
  {"type": "done", "stop_reason": str}
  {"type": "error", "message": str}

Tools are given as JSON-schema function specs: {"name", "description", "parameters"}.
Usage counters come from the provider, including cache details when reported.
Missing usage remains unknown (None), including failed or interrupted requests.
"""
from __future__ import annotations

from ..config import ModelConfig, api_key
from .anthropic import AnthropicAdapter
from .openai_compat import OpenAICompatAdapter


def make_adapter(mc: ModelConfig):
    r = mc.resolved()
    if not r.base_url:
        raise ValueError("未配置模型接口地址（base_url）")
    if not r.model:
        raise ValueError("未配置模型名（model）")
    key = api_key(mc)
    if r.protocol == "anthropic":
        return AnthropicAdapter(r, key)
    return OpenAICompatAdapter(r, key)
