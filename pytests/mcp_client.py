"""A real MCP client session over stdio, as an agent would open one."""
import json
import os
import sys

import anyio
from mcp import ClientSession, StdioServerParameters, stdio_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_session(db_path: str, log_path: str, calls: list[tuple[str, dict]]):
    """Start the server, discover tools, run the calls; return (tool list, [(is_error, payload or text)])."""
    async def go():
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "mcp_server.server"], cwd=ROOT,
            env={**os.environ, "METRICS_DB": db_path, "MCP_CALL_LOG": log_path})
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                tools = (await s.list_tools()).tools
                out = []
                for name, args in calls:
                    res = await s.call_tool(name, args)
                    text = "".join(getattr(c, "text", "") for c in res.content)
                    out.append((res.is_error, text if res.is_error else json.loads(text)))
                return tools, out
    return anyio.run(go)
