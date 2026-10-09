"""MCP server exposing the billing tools, so any MCP client can use them (written for mcp 2.x)."""

from __future__ import annotations

import anyio
import httpx
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .tools import API_TOOLS, execute, open_api


def create_server(http: httpx.Client) -> Server:
    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(name=tool.name, description=tool.description, input_schema=tool.input_schema)
                for tool in API_TOOLS
            ]
        )

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        # The HTTP client is synchronous: run it off the event loop.
        result = await anyio.to_thread.run_sync(execute, http, params.name, params.arguments or {})
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=result.content)], is_error=result.is_error
        )

    return Server("nimbus-billing", version="0.1.0", on_list_tools=list_tools, on_call_tool=call_tool)


def main(api_url: str | None = None) -> None:
    async def serve() -> None:
        with open_api(api_url) as http:
            server = create_server(http)
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(serve)
