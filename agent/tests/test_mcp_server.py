"""The MCP server exposes exactly the agent's tool registry, on mcp 1.x and 2.x."""
import json

import pytest

mcp = pytest.importorskip("mcp")


def test_build_lists_the_registry_and_calls_through():
    from r2g_agent import mcp_server, tools
    srv = mcp_server.build()
    assert srv is not None
    names = [t.name for t in mcp_server._list()]
    assert names == [s["name"] for s in tools.specs()]
    out = mcp_server._call("no_such_tool", {})
    assert json.loads(out[0].text)["error"].startswith("unknown tool")
