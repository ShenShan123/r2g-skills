"""The r2g tools as an MCP server (stdio), for any MCP-capable client.

  r2g mcp            # e.g. register in Claude Code / Cursor / Cherry Studio

Exposes exactly the registry the built-in agent uses (tools.REGISTRY).
"""
from __future__ import annotations

import json

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

from . import tools


def _list() -> list[types.Tool]:
    return [types.Tool(name=s["name"], description=s["description"], inputSchema=s["parameters"])
            for s in tools.specs()]


def _call(name: str, arguments: dict | None) -> list[types.TextContent]:
    res = tools.call(name, arguments or {})
    return [types.TextContent(type="text", text=json.dumps(res, ensure_ascii=False, default=str))]


def build() -> Server:
    if hasattr(Server, "list_tools"):        # mcp 1.x: decorator registration
        srv = Server("r2g")

        @srv.list_tools()
        async def _list1() -> list[types.Tool]:
            return _list()

        @srv.call_tool()
        async def _call1(name: str, arguments: dict) -> list[types.TextContent]:
            return _call(name, arguments)

        return srv

    # mcp 2.x: handlers are constructor arguments taking (ctx, params)
    async def _list2(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=_list())

    async def _call2(ctx, params) -> types.CallToolResult:
        return types.CallToolResult(content=_call(params.name, params.arguments))

    return Server("r2g", on_list_tools=_list2, on_call_tool=_call2)


async def serve() -> None:
    srv = build()
    async with stdio_server() as (r, w):
        await srv.run(r, w, srv.create_initialization_options())
