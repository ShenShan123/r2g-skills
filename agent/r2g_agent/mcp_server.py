"""MCP stdio transport for the existing host executor and tool registry.

Optional request_id is transport metadata shared with HTTP/CLI retries.
Session tools require a session explicitly approved when the server starts.
"""
from __future__ import annotations

import copy
import json

import anyio
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from . import tools
from .execution import HostExecutor, response
from .tool_outputs import ToolContext


def call_tool(executor: HostExecutor | None, name: str, arguments: dict, *, session_id=None,
              allow_session_tools=False, read_only=False) -> dict:
    if executor is None:
        return {"error": "host execution runtime is not initialized", "error_code": "runtime_unavailable"}
    if not isinstance(arguments, dict):
        return {"error": "arguments must be an object", "error_code": "invalid_execution"}
    arguments = dict(arguments)
    try:
        context = executor.host_context("mcp", arguments.pop("request_id", None), session_id,
                                        allow_session_tools=allow_session_tools, read_only=read_only)
        return response(executor.execute(name, arguments, context))
    except ValueError as exc:
        return {"error": str(exc), "error_code": "invalid_execution"}


def build(executor: HostExecutor | None = None, *, session_id: str | None = None,
          allow_session_tools: bool = False, read_only: bool = False) -> Server:
    if executor is None:
        from .server import STATE
        executor = STATE.get("executor")
    if executor:
        executor.host_context("mcp", session_id=session_id, allow_session_tools=allow_session_tools, read_only=read_only)
    elif session_id or allow_session_tools:
        raise ValueError("session approval requires an initialized host runtime")
    srv = Server("r2g")

    @srv.list_tools()
    async def _list() -> list[types.Tool]:
        context = ToolContext(session_id or "", executor.sessions.outputs if executor else None, read_only=read_only)
        out = []
        for spec in tools.specs(session_scoped=allow_session_tools, context=context):
            parameters = copy.deepcopy(spec["parameters"])
            parameters.setdefault("properties", {})["request_id"] = {
                "type": "string", "minLength": 1, "maxLength": 200,
                "description": "Optional idempotency key; reuse only for the same request and source bytes."}
            out.append(types.Tool(name=spec["name"], description=spec["description"], inputSchema=parameters))
        return out

    @srv.call_tool()
    async def _call(name: str, arguments: dict) -> list[types.TextContent]:
        res = await anyio.to_thread.run_sync(lambda: call_tool(
            executor, name, arguments or {}, session_id=session_id,
            allow_session_tools=allow_session_tools, read_only=read_only))
        return [types.TextContent(type="text", text=json.dumps(res, ensure_ascii=False, default=str))]

    return srv


async def serve(**options) -> None:
    srv = build(**options)
    async with stdio_server() as (r, w):
        await srv.run(r, w, srv.create_initialization_options())
