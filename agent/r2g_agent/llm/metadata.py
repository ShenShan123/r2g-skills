"""Provider-reported response identity and token counts, without message content.

Missing counts stay unknown. Counts are cumulative provider values, not deltas;
in particular Anthropic's input_tokens excludes its separately reported cache
counts. No totals, prices, or costs are inferred here.
"""
from __future__ import annotations


class ResponseMetadata:
    def __init__(self, provider: str, model_requested: str):
        self.provider = provider
        self.model_requested = model_requested
        self.model_observed: str | None = None
        self.response_id: str | None = None
        self.request_id: str | None = None
        self._final_usage_received = False
        self.usage: dict[str, int | None] = dict.fromkeys((
            "input_tokens", "output_tokens", "total_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens", "reasoning_tokens",
        ))

    def identity(self, payload: dict) -> None:
        for source, target in (("model", "model_observed"), ("id", "response_id")):
            value = payload.get(source)
            if isinstance(value, str) and value:
                setattr(self, target, value)

    def headers(self, headers) -> None:
        self.request_id = headers.get("x-request-id") or headers.get("request-id") or None

    def _counts(self, **counts) -> None:
        for key, value in counts.items():
            if type(value) is int and value >= 0:
                self.usage[key] = value

    def openai_usage(self, usage) -> None:
        if not isinstance(usage, dict):
            return
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        self._counts(
            input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            cache_read_input_tokens=usage.get("prompt_cache_hit_tokens"),
        )
        if isinstance(prompt_details, dict):
            self._counts(cache_read_input_tokens=prompt_details.get("cached_tokens"))
        if isinstance(completion_details, dict):
            self._counts(reasoning_tokens=completion_details.get("reasoning_tokens"))
        if type(usage.get("completion_tokens")) is int and usage["completion_tokens"] >= 0:
            self._final_usage_received = True

    def anthropic_usage(self, usage, *, final: bool = False) -> None:
        if isinstance(usage, dict):
            self._counts(**{key: usage.get(key) for key in (
                "input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
            )})
            if final and type(usage.get("output_tokens")) is int and usage["output_tokens"] >= 0:
                self._final_usage_received = True

    def snapshot(self, *, stream_complete: bool = False) -> dict:
        if self._final_usage_received and stream_complete and all(
            self.usage[key] is not None for key in ("input_tokens", "output_tokens")
        ):
            status = "reported"
        elif any(value is not None for value in self.usage.values()):
            status = "partial"
        else:
            status = "unresolved"
        return {
            "provider": self.provider, "model_requested": self.model_requested,
            "model_observed": self.model_observed, "response_id": self.response_id,
            "request_id": self.request_id, "stream_complete": stream_complete,
            "usage": {"status": status, **self.usage},
        }
