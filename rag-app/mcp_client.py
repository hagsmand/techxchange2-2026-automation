"""
mcp_client.py — GitHub MCP Server client using the official MCP Python SDK.

Spawns `npx -y @modelcontextprotocol/server-github` as a subprocess and
communicates via the MCP SDK's StdioServerParameters / ClientSession.

The GitHub MCP server requires a GITHUB_PERSONAL_ACCESS_TOKEN env var.

Reference: https://github.com/github/github-mcp-server
           https://github.com/modelcontextprotocol/python-sdk
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Context manager — preferred usage pattern
# ─────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def github_mcp_session(github_token: str):
    """
    Async context manager that yields a connected GitHubMCPClient.

    Usage:
        async with github_mcp_session(token) as client:
            tools = await client.list_tools_openai_format()
            result = await client.call_tool("list_issues", {...})
    """
    client = GitHubMCPClient(github_token)
    await client._start()
    try:
        yield client
    finally:
        await client._stop()


# ─────────────────────────────────────────────────────────────────────────────
# Client class
# ─────────────────────────────────────────────────────────────────────────────

class GitHubMCPClient:
    """
    Async client for the GitHub MCP Server using the official MCP Python SDK.

    Spawns `npx -y @modelcontextprotocol/server-github` as a subprocess and
    manages the full MCP lifecycle (initialize → tool calls → shutdown).

    Supports both async context manager and manual lifecycle:
        async with GitHubMCPClient(token) as c: ...
        # or
        c = GitHubMCPClient(token); await c._start(); ...; await c._stop()
    """

    def __init__(self, github_token: str):
        self._github_token = github_token
        self._session: ClientSession | None = None
        self._exit_stack = None
        self._connected = False

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def __aenter__(self):
        await self._start()
        return self

    async def __aexit__(self, *args):
        await self._stop()

    async def _start(self):
        """Spawn the GitHub MCP server subprocess and run the MCP handshake."""
        from contextlib import AsyncExitStack
        env = {**os.environ, "GITHUB_PERSONAL_ACCESS_TOKEN": self._github_token}

        server_params = StdioServerParameters(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env=env,
        )

        try:
            self._exit_stack = AsyncExitStack()
            read, write = await self._exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()
            self._connected = True
            logger.info("✅ Connected to GitHub MCP Server via official SDK")
        except FileNotFoundError:
            logger.error("npx not found — Node.js must be installed to run the GitHub MCP server")
            self._connected = False
        except Exception as e:
            logger.warning("Failed to start GitHub MCP server: %s", e)
            self._connected = False

    async def _stop(self):
        """Cleanly shut down the MCP session and subprocess."""
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception:
                pass
            self._exit_stack = None
        self._session = None
        self._connected = False

    # ── public API ───────────────────────────────────────────────────────────

    async def list_tools_openai_format(self) -> list[dict]:
        """
        Fetch all GitHub MCP tools and return them in OpenAI function-call format.
        Returns [] when the server is unavailable.
        """
        if not self._connected or not self._session:
            return []
        try:
            result = await self._session.list_tools()
            tools = result.tools
            logger.info("Loaded %d tools from GitHub MCP Server", len(tools))
            return [_mcp_tool_to_openai(t) for t in tools]
        except Exception as e:
            logger.warning("list_tools failed: %s", e)
            return []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """
        Execute a GitHub MCP tool and return the result as a plain string.
        Returns a JSON error string when the server is unavailable.
        """
        if not self._connected or not self._session:
            import json
            return json.dumps({"error": "GitHub MCP server not connected"})
        try:
            result = await self._session.call_tool(name, arguments)
            parts: list[str] = []
            for block in result.content:
                # SDK returns typed content objects (TextContent, ImageContent, …)
                if hasattr(block, "text"):
                    parts.append(block.text)
                else:
                    parts.append(str(block))
            output = "\n".join(parts) if parts else "(empty result)"
            logger.info("Tool %s → %d chars", name, len(output))
            return output
        except Exception as e:
            logger.warning("call_tool(%s) failed: %s", name, e)
            import json
            return json.dumps({"error": str(e)})

    @property
    def is_connected(self) -> bool:
        return self._connected


# ─────────────────────────────────────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────────────────────────────────────

def _mcp_tool_to_openai(tool) -> dict:
    """Convert an MCP SDK Tool object to OpenAI function-call format."""
    # tool.inputSchema is already a dict (JSON Schema)
    parameters = tool.inputSchema if tool.inputSchema else {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": parameters,
        },
    }
