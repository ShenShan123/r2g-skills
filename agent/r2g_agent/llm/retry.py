"""Bounded reconnects for a model request that provably produced no tokens.

Only two failure shapes are retried, both before any response body is read:
the connection never opened (connect error / connect timeout), or the endpoint
answered with an overload/unavailable status (429, 5xx gateway family, 529).
In both cases no generation was streamed, so a retry neither double-counts nor
hides tokens. Once a stream has started, or on ambiguous read timeouts/resets,
the call fails as before and its usage stays unknown, never zero.

Every retry is recorded on the call's usage event (`transport_retries`), so the
call count and token totals stay exact while the reconnects remain visible.
In the 2026-10-28 cost runs, five relay "HTTP 502 upstream unavailable"
answers voided three otherwise successful measured attempts.
"""
from __future__ import annotations

import contextlib
import os
import time

import httpx

RETRY_STATUS = frozenset({429, 500, 502, 503, 504, 529})
RETRY_EXCEPTIONS = (httpx.ConnectError, httpx.ConnectTimeout)
MAX_DELAY_S = 30.0
MAX_RETRY_AFTER_S = 60.0
SLEEP = time.sleep


def max_retries() -> int:
    try:
        value = int(os.environ.get("R2G_LLM_RETRIES", "4"))
    except ValueError:
        value = 4
    return max(0, min(value, 10))


def _delay(n: int, response=None) -> float:
    header = getattr(response, "headers", None) or {}
    try:
        after = float(header.get("retry-after"))
        if after >= 0:
            return min(after, MAX_RETRY_AFTER_S)
    except (TypeError, ValueError):
        pass
    return min(2.0 ** (n + 1), MAX_DELAY_S)


@contextlib.contextmanager
def stream(open_request, log: list):
    """`with stream(lambda: httpx.stream(...), log) as response:`.

    Yields the first response that is not a retryable pre-stream failure (or
    the last one once retries are exhausted); appends one record per retry."""
    n = 0
    while True:
        cm = open_request()
        try:
            response = cm.__enter__()
        except RETRY_EXCEPTIONS as exc:
            if n >= max_retries():
                raise
            reason, wait = type(exc).__name__, _delay(n)
        else:
            if response.status_code not in RETRY_STATUS or n >= max_retries():
                break
            reason, wait = "HTTP %d" % response.status_code, _delay(n, response)
            cm.__exit__(None, None, None)
        n += 1
        log.append({"retry": n, "reason": reason, "delay_s": wait})
        SLEEP(wait)
    try:
        yield response
    finally:
        cm.__exit__(None, None, None)
