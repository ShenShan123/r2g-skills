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


def build() -> Server:
    srv = Server("r2g")

    @srv.list_tools()
    async def _list() -> list[types.Tool]:
        return [types.Tool(name=s["name"], description=s["description"], inputSchema=s["parameters"])
                for s in tools.specs()]

    @srv.call_tool()
    async def _call(name: str, arguments: dict) -> list[types.TextContent]:
        res = tools.call(name, arguments or {})
        return [types.TextContent(type="text", text=json.dumps(res, ensure_ascii=False, default=str))]

    return srv


async def serve() -> None:
    srv = build()
    async with stdio_server() as (r, w):
        await srv.run(r, w, srv.create_initialization_options())
