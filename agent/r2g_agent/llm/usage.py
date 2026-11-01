"""Provider-reported token accounting; unknown counters are never estimated."""


def normalize_usage(raw: dict, protocol: str) -> dict:
    def count(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    if protocol == "anthropic":
        uncached = count(raw.get("input_tokens"))
        cached = count(raw.get("cache_read_input_tokens"))
        created = count(raw.get("cache_creation_input_tokens"))
        # Anthropic input_tokens excludes both cache read and cache creation.
        input_tokens = (uncached + (cached or 0) + (created or 0)) if uncached is not None else None
        output_tokens = count(raw.get("output_tokens"))
        reasoning = None
    elif protocol == "openai-responses":
        # input_tokens is the total; cached reads and cache writes are parts of it
        input_tokens = count(raw.get("input_tokens"))
        output_tokens = count(raw.get("output_tokens"))
        details = raw.get("input_tokens_details") or {}
        cached = count(details.get("cached_tokens"))
        created = count(details.get("cache_write_tokens"))
        uncached = (input_tokens - (cached or 0) - (created or 0)) if input_tokens is not None and cached is not None else None
        reasoning = count((raw.get("output_tokens_details") or {}).get("reasoning_tokens"))
    else:
        input_tokens = count(raw.get("prompt_tokens"))
        output_tokens = count(raw.get("completion_tokens"))
        cached = count(raw.get("prompt_cache_hit_tokens"))
        if cached is None:
            cached = count((raw.get("prompt_tokens_details") or {}).get("cached_tokens"))
        uncached = count(raw.get("prompt_cache_miss_tokens"))
        if uncached is None and input_tokens is not None and cached is not None and cached <= input_tokens:
            uncached = input_tokens - cached
        created = None
        reasoning = count((raw.get("completion_tokens_details") or {}).get("reasoning_tokens"))
    total = count(raw.get("total_tokens"))
    if total is None and input_tokens is not None and output_tokens is not None:
        total = input_tokens + output_tokens
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total,
            "cache_read_input_tokens": cached, "uncached_input_tokens": uncached,
            "cache_creation_input_tokens": created, "reasoning_tokens": reasoning,
            "usage_reported": bool(raw), "raw_usage": raw}


def usage_event(mc, raw: dict, response_id: str | None, complete: bool,
                retries: list | None = None) -> dict:
    # transport_retries: reconnects before any token was streamed (llm/retry.py);
    # they are part of this one call, not extra calls.
    return {"type": "usage", "provider": mc.provider, "model": mc.model,
            "protocol": mc.protocol, "response_id": response_id, "stream_complete": complete,
            **normalize_usage(raw, mc.protocol), "transport_retries": list(retries or [])}
