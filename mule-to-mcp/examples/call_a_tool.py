"""Minimal MCP client demo: connect to a generated server over stdio and call
a couple of its tools for real, against the live mock Orders API.

Usage:
    python examples/call_a_tool.py [path-to-generated-server.py]

Defaults to ./generated-orders-mcp/server.py, matching the README quickstart.
The generated server's auth env vars (e.g. ORDERS_CLIENT_ID/ORDERS_CLIENT_SECRET)
must already be exported in this process's environment -- they are inherited
by the child server process over stdio, exactly as an MCP host would do it.
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import Client, StdioServerParameters


async def main() -> None:
    server_path = sys.argv[1] if len(sys.argv) > 1 else "generated-orders-mcp/server.py"
    # The MCP stdio transport does NOT inherit the parent process's environment
    # by default (only a minimal safe subset) -- pass it through explicitly so
    # the generated server's auth env vars (e.g. ORDERS_CLIENT_ID/SECRET) reach
    # the child process, exactly as a real MCP host would need to configure it.
    params = StdioServerParameters(command=sys.executable, args=[server_path], env=dict(os.environ))

    async with Client(params) as client:
        tools = await client.list_tools()
        print(f"Discovered {len(tools.tools)} tool(s): {', '.join(t.name for t in tools.tools)}")
        print()

        print(">>> call_tool('list_orders', {})")
        result = await client.call_tool("list_orders", {})
        print(result.structured_content)
        print()

        print(">>> call_tool('get_order_by_id', {'order_id': 'o1'})")
        result = await client.call_tool("get_order_by_id", {"order_id": "o1"})
        print(result.structured_content)


if __name__ == "__main__":
    asyncio.run(main())
