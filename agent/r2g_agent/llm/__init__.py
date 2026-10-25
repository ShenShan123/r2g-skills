"""Model adapters behind one interface.

Messages use one internal shape regardless of provider:

  {"role": "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments": dict}]}
  {"role": "tool", "tool_call_id": str, "name": str, "content": str}

`stream(messages, tools, system)` yields events:

  {"type": "text", "delta": str}
  {"type": "tool_call", "id": str, "name": str, "arguments": dict}
  {"type": "done", "stop_reason": str, "metadata": dict}
  {"type": "error", "message": str, "metadata": dict}

Terminal metadata records provider, model_requested, model_observed, response_id,
request_id, stream_complete and usage. Unknown identities/counts are None. Usage
status is reported only when input/output counts and the stream terminator were
received; partial/unresolved usage must not be treated as zero cost. Counts are
provider-reported values, including separate cache counts where available.
Metadata contains no message content or hidden reasoning. Compatible endpoints
may omit usage; request parameters are not changed to force usage support.

Tools are given as JSON-schema function specs: {"name", "description", "parameters"}.
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
