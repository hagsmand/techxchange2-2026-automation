"""
api.py — GitHub MCP App Host server.

Endpoints:
  GET  /              → Chat host UI (AppBridge host)
  POST /chat          → ReAct agent → returns { view, data } payload
  GET  /ui/<name>     → Serves sandboxed View HTML pages
  GET  /health        → GitHub MCP Server connectivity check
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv(Path(__file__).parent / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")

app = FastAPI(title="GitHub MCP App")

STATIC_DIR  = Path(__file__).parent / "static"
VIEWS_DIR   = STATIC_DIR / "views"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


class ChatRequest(BaseModel):
    messages: list[dict]


@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/ui/{view_name}")
async def serve_view(view_name: str):
    """
    Serve a sandboxed View HTML page.
    These are the MCP App iframes — self-contained HTML that communicate
    back to the host via postMessage JSON-RPC.
    """
    path = VIEWS_DIR / f"{view_name}.html"
    if not path.exists():
        return JSONResponse({"error": f"View '{view_name}' not found"}, status_code=404)
    return HTMLResponse(content=path.read_text(), headers={
        # Allow embedding in same-origin iframe
        "X-Frame-Options": "SAMEORIGIN",
        "Content-Security-Policy": "default-src 'self' 'unsafe-inline' 'unsafe-eval';",
    })


@app.post("/chat")
async def chat(req: ChatRequest):
    """
    Run the GitHub MCP agent and return a View payload:
      { view: "github_repo"|"github_prs"|"github_issues"|"github_commits"|"github_search",
        data: {...},          // passed to iframe via postMessage
        text: "..." }         // plain-text answer for non-iframe fallback
    """
    from mcp_agent import MCPAgent
    agent = MCPAgent()
    payload = await agent.run(req.messages)
    return JSONResponse(content=payload)


@app.post("/tool")
async def proxy_tool(req: dict):
    """
    AppBridge tool proxy: the iframe calls tools/call via postMessage,
    the host relays them here, executes via GitHub MCP, returns result.

    Request body: { name: str, arguments: dict }
    """
    from mcp_client import GitHubMCPClient
    github_token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    name = req.get("name", "")
    arguments = req.get("arguments", {})

    async with GitHubMCPClient(github_token) as mcp:
        if not mcp.is_connected:
            return JSONResponse({"error": "GitHub MCP server not connected"}, status_code=503)
        result = await mcp.call_tool(name, arguments)
        return JSONResponse({"content": [{"type": "text", "text": result}]})


@app.get("/health")
async def health():
    from mcp_client import GitHubMCPClient
    github_token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    async with GitHubMCPClient(github_token) as mcp:
        tools = await mcp.list_tools_openai_format()
        return JSONResponse({
            "github_mcp": "connected" if mcp.is_connected else "unavailable",
            "tools_available": len(tools),
            "tool_names": [t["function"]["name"] for t in tools[:10]],
        })
