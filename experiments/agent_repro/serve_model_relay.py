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
from starlette.background import BackgroundTask


MAX_REQUEST_BYTES = 8 * 1024 * 1024
ALLOWED_FIELDS = {
    "model", "messages", "stream", "stream_options", "tools", "tool_choice",
    "parallel_tool_calls", "temperature", "top_p", "max_tokens",
    "max_completion_tokens", "presence_penalty", "frequency_penalty", "stop",
    "seed", "response_format", "logprobs", "top_logprobs", "thinking",
    "enable_thinking", "reasoning_effort", "n",
}
# OpenAI Responses (GPT-6 tool calling); store is forced off below, so a
# worker can neither persist nor fetch conversations by id
RESPONSES_FIELDS = {
    "model", "input", "instructions", "stream", "store", "include", "tools", "tool_choice",
    "parallel_tool_calls", "max_output_tokens", "reasoning", "text", "truncation",
}
# Anthropic Messages, as DeepSeek serves it at https://api.deepseek.com/anthropic (the native
# transport of DeepSeek Harness, 2026-11-12). Upstream path is <base>/v1/messages; the key goes
# in x-api-key, never Authorization.
MESSAGES_FIELDS = {
    "model", "messages", "system", "max_tokens", "stream", "tools", "tool_choice", "thinking",
    "temperature", "top_p", "top_k", "stop_sequences", "metadata", "output_config",
    # DeepSeek Harness attribution fields, sent to DeepSeek when the harness runs natively
    "dsh_plugin_packages", "dsh_session_log",
}
# Client headers the Messages route may forward: protocol negotiation only (dsh sends a beta
# header for mid-conversation tool changes). Values are bounded and never carry credentials.
MESSAGES_HEADERS = ("anthropic-version", "anthropic-beta", "user-agent",
                    "x-deepseek-harness-user-id", "x-deepseek-harness-session-id")
ROUTES = {"openai": ("/chat/completions", ALLOWED_FIELDS, "messages"),
          "openai-responses": ("/responses", RESPONSES_FIELDS, "input"),
          "anthropic": ("/messages", MESSAGES_FIELDS, "messages")}


def create_app(base_url: str, model: str, api_key: str, *, transport=None,
               protocol: str = "openai", upstream_model: str | None = None) -> FastAPI:
    """Workers must name `model`; `upstream_model`, if set, is the name sent upstream (an aggregator
    such as OpenRouter lists the same model as `openai/<name>`). The worker-facing contract is unchanged."""
    parsed = urlsplit(base_url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Relay requires a fixed HTTPS upstream without URL credentials")
    if not model or not api_key:
        raise ValueError("Configured model and upstream credential are required")
    path, allowed, listed = ROUTES[protocol]
    # Messages roots (https://api.deepseek.com/anthropic) take the /v1 segment themselves.
    endpoint = base_url.rstrip("/") + ("/v1" + path if protocol == "anthropic" else path)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await client.aclose()

    # 2026-10-29 outage: after ~30 h and ~56 concurrent agents every request hung.
    # A stream whose client went away before its generator ran never released its
    # pooled connection; at the default 100 the pool was full of leaked slots and
    # each request waited out the pool timeout. No connection cap (the provider
    # limits rate, not us), a short pool wait, and the upstream response is closed
    # by a background task as well as by the generator.
    client = httpx.AsyncClient(transport=transport, follow_redirects=False,
                              timeout=httpx.Timeout(240, connect=20, pool=30),
                              limits=httpx.Limits(max_connections=None, max_keepalive_connections=32,
                                                  keepalive_expiry=30))
    app = FastAPI(title="R2G fixed model relay", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "model": model, "upstream_fixed": True,
                "credentials_forwarded_to_worker": False}

    @app.post("/v1" + path)
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
        if not isinstance(body, dict):
            return JSONResponse({"error": "unsupported request fields"}, status_code=400)
        if set(body) - allowed:
            # field NAMES only (bounded), so a client can be configured; never values
            extra = sorted(str(k)[:40] for k in set(body) - allowed)[:20]
            return JSONResponse({"error": "unsupported request fields", "fields": extra}, status_code=400)
        if body.get("model") != model:
            return JSONResponse({"error": "model is fixed by relay"}, status_code=400)
        if not isinstance(body.get(listed), list):
            return JSONResponse({"error": "%s must be a list" % listed}, status_code=400)
        if protocol == "openai-responses":
            body["store"] = False
        if upstream_model:
            body["model"] = upstream_model
        if request.url.query:
            return JSONResponse({"error": "query parameters are not supported"}, status_code=400)
        # Never forward client headers (Authorization, Host, forwarding headers,
        # cookies, or arbitrary endpoint overrides). Disable redirects as well.
        if protocol == "anthropic":
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
            for h in MESSAGES_HEADERS:
                v = request.headers.get(h)
                if v and len(v) <= 200 and all(c.isalnum() or c in "-_,. /()+:" for c in v):
                    headers[h] = v
        else:
            headers = {"Authorization": "Bearer " + api_key}
        upstream_request = client.build_request(
            "POST", endpoint, json=body,
            headers={**headers, "Content-Type": "application/json", "Accept-Encoding": "identity"})
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
                                 headers={"Cache-Control": "no-store"},
                                 background=BackgroundTask(response.aclose))

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8774)
    parser.add_argument("--expected-model", default="deepseek-flash")
    parser.add_argument("--upstream-model", default="",
                        help="model name sent upstream when the provider names it differently (OpenRouter: openai/gpt-6.1-sol)")
    args = parser.parse_args()
    from r2g_agent import config
    import uvicorn
    model = config.load().model.resolved()
    if model.protocol not in ROUTES or model.model != args.expected_model:
        raise SystemExit("Configured protocol/model does not match the approved relay")
    key = config.api_key(model)
    if not key:
        raise SystemExit("Configured upstream credential is unavailable")
    app = create_app(model.base_url, model.model, key, protocol=model.protocol,
                     upstream_model=args.upstream_model or None)
    # Explicit IPv4 loopback only; no host CLI override, access/body/header logs.
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False,
                log_level="critical")


if __name__ == "__main__":
    main()
