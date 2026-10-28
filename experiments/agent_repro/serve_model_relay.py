#!/usr/bin/env python3
"""Loopback-only, fixed-provider relay; upstream credentials stay on this host.

Reach it from a worker through an SSH reverse tunnel. The configured R2G model
and upstream are immutable for the process lifetime. Client authorization and
all other incoming headers are ignored. No request/response bodies are logged.
"""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
import json
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


MAX_REQUEST_BYTES = 8 * 1024 * 1024
ALLOWED_FIELDS = {
    "model", "messages", "stream", "stream_options", "tools", "tool_choice",
    "parallel_tool_calls", "temperature", "top_p", "max_tokens",
    "max_completion_tokens", "presence_penalty", "frequency_penalty", "stop",
    "seed", "response_format", "logprobs", "top_logprobs", "thinking",
    "enable_thinking", "reasoning_effort", "n",
}


def create_app(base_url: str, model: str, api_key: str, *, transport=None) -> FastAPI:
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Relay requires a fixed HTTPS upstream without URL credentials")
    if not model or not api_key:
        raise ValueError("Configured model and upstream credential are required")
    endpoint = base_url.rstrip("/") + "/chat/completions"

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await client.aclose()

    client = httpx.AsyncClient(transport=transport, follow_redirects=False,
                              timeout=httpx.Timeout(240, connect=20))
    app = FastAPI(title="R2G fixed model relay", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "model": model, "upstream_fixed": True,
                "credentials_forwarded_to_worker": False}

    @app.post("/v1/chat/completions")
    async def complete(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_REQUEST_BYTES:
                return JSONResponse({"error": "request too large"}, status_code=413)
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"error": "invalid JSON"}, status_code=400)
        if not isinstance(body, dict) or set(body) - ALLOWED_FIELDS:
            return JSONResponse({"error": "unsupported request fields"}, status_code=400)
        if body.get("model") != model:
            return JSONResponse({"error": "model is fixed by relay"}, status_code=400)
        if not isinstance(body.get("messages"), list):
            return JSONResponse({"error": "messages must be a list"}, status_code=400)
        if request.url.query:
            return JSONResponse({"error": "query parameters are not supported"}, status_code=400)
        # Never forward client headers (Authorization, Host, forwarding headers,
        # cookies, or arbitrary endpoint overrides). Disable redirects as well.
        upstream_request = client.build_request(
            "POST", endpoint, json=body,
            headers={"Authorization": "Bearer " + api_key,
                     "Content-Type": "application/json", "Accept-Encoding": "identity"})
        try:
            response = await client.send(upstream_request, stream=True)
        except Exception:
            return JSONResponse({"error": "upstream unavailable"}, status_code=502)
        if response.status_code != 200:
            status = response.status_code
            await response.aclose()
            # Provider errors may echo input/headers; keep them off the worker.
            return JSONResponse({"error": "upstream request failed",
                                 "upstream_status": status}, status_code=502)

        async def stream():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            except Exception:
                # Do not turn transport exceptions into a possibly sensitive
                # traceback or fabricate token counters for an incomplete stream.
                yield b'\ndata: {"error":{"message":"upstream stream interrupted"}}\n\n'
            finally:
                await response.aclose()

        media_type = "text/event-stream" if body.get("stream") else "application/json"
        return StreamingResponse(stream(), media_type=media_type,
                                 headers={"Cache-Control": "no-store"})

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8774)
    parser.add_argument("--expected-model", default="deepseek-flash")
    args = parser.parse_args()
    from r2g_agent import config
    import uvicorn
    model = config.load().model.resolved()
    if model.protocol != "openai" or model.model != args.expected_model:
        raise SystemExit("Configured protocol/model does not match the approved relay")
    key = config.api_key(model)
    if not key:
        raise SystemExit("Configured upstream credential is unavailable")
    app = create_app(model.base_url, model.model, key)
    # Explicit IPv4 loopback only; no host CLI override, access/body/header logs.
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False,
                log_level="critical")


if __name__ == "__main__":
    main()
