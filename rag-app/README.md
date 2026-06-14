# GitHub MCP App

A GitHub-powered chat application built on the **Model Context Protocol (MCP)**.
Ask natural-language questions about any repository — issues, pull requests, commits,
code, CI/CD — and get back beautifully rendered, structured responses.

```
GitHub MCP Server ←──── stdio (JSON-RPC 2.0 via MCP SDK)
       ↑
   MCPAgent  ──── ReAct loop ──── OpenCode Zen LLM
       ↑
   FastAPI /chat
       ↑
   Browser UI  (Preact · Declarative UI · Template Registry)
```

---

## What is a GitHub MCP App?

This app is an **MCP Host** — it:

1. **Spawns** `@github/github-mcp-server` via `npx` (stdio transport)
2. **Negotiates** the MCP handshake with the official `mcp` Python SDK
3. **Fetches** all ~40 GitHub tools (`list_issues`, `create_pull_request`, `search_code`, …)
4. **Runs** a ReAct-style LLM loop: LLM decides which tool to call → MCP executes it → LLM writes the final answer
5. **Renders** the answer using a Declarative UI template registry in the browser

---

## Prerequisites

| Dependency | Notes |
|---|---|
| [uv](https://docs.astral.sh/uv/) | Python package / venv manager |
| [Node.js ≥ 18](https://nodejs.org) | Required for `npx @github/github-mcp-server` |
| An [OpenCode Zen API key](https://opencode.ai/zen) | Free tier available |
| A [GitHub Personal Access Token](https://github.com/settings/tokens) | Scopes: `repo`, `read:org`, `read:user` |

---

## Quick start

### 1. Clone / enter the project

```bash
cd rag-app
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
# OpenCode Zen
OPENCODE_ZEN_API_KEY=sk-...
OPENCODE_ZEN_BASE_URL=https://opencode.ai/zen/v1
OPENCODE_ZEN_MODEL=deepseek-v4-flash

# GitHub MCP Server
GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...
```

> **Available models on Zen:**
> `deepseek-v4-pro`, `deepseek-v4-flash`, `deepseek-v4-flash-free`,
> `glm-5.1`, `kimi-k2.5`, `minimax-m2.7`

### 4. Start the server

```bash
uv run uvicorn api:app --reload
```

Open **http://localhost:8000** in your browser.

---

## Architecture

### Backend

| File | Role |
|---|---|
| [`api.py`](api.py) | FastAPI app — `/chat`, `/health`, `/` endpoints |
| [`mcp_agent.py`](mcp_agent.py) | ReAct agent — connects to GitHub MCP, runs tool loop, returns Declarative UI payload |
| [`mcp_client.py`](mcp_client.py) | GitHub MCP client using the official `mcp` Python SDK (stdio transport) |

### Frontend (`static/index.html`)

The frontend uses the **Declarative UI / Template Registry** pattern:

- The LLM response always carries `{ ui_type, data }`
- A `TEMPLATE_REGISTRY` maps `ui_type` strings → Preact components
- `MessageRouter` reads `ui_type` and dispatches — no scattered if/else

#### UI Templates

| `ui_type` | Template | Usage |
|---|---|---|
| `text` | `TextTemplate` | Conversational answers |
| `card` | `CardTemplate` | Single highlighted item |
| `card_list` | `CardListTemplate` | Grid of cards |
| `table` | `TableTemplate` | Tabular data |
| `code` | `CodeTemplate` | Syntax-highlighted code |
| `steps` | `StepsTemplate` | Numbered step list |
| `alert_list` | `AlertListTemplate` | Severity-coloured alerts |
| `metric` | `MetricTemplate` | Single KPI |
| **`pr_list`** | `PRListTemplate` | Pull request list with OPEN / CLOSED / MERGED badges |
| **`issue_list`** | `IssueListTemplate` | Issue list with labels |
| **`repo_card`** | `RepoCardTemplate` | Repository overview with stats |
| **`commit_list`** | `CommitListTemplate` | Commit history with short SHAs |
| **`diff`** | `DiffTemplate` | Unified diff / patch viewer |

---

## API endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves the chat UI |
| `POST` | `/chat` | `{ messages: [...] }` → `{ ui_type, data }` |
| `GET` | `/health` | Checks GitHub MCP Server connectivity |

### Health check example

```bash
curl http://localhost:8000/health
```

```json
{
  "github_mcp": "connected",
  "tools_available": 43,
  "tool_names": ["create_or_update_file", "search_repositories", "get_file_contents", ...]
}
```

---

## Example prompts

| Prompt | Expected `ui_type` |
|---|---|
| `Get details about ameeng/techxchange2-2026-automation` | `repo_card` |
| `Show open pull requests in ameeng/techxchange2-2026-automation` | `pr_list` |
| `List open issues in ameeng/techxchange2-2026-automation` | `issue_list` |
| `Show the last 10 commits on main in ameeng/techxchange2-2026-automation` | `commit_list` |
| `Search for Python FastAPI repos with over 1000 stars` | `table` or `card_list` |
| `What files changed in PR #3 of ameeng/techxchange2-2026-automation?` | `diff` |
| `Steps to create a GitHub Actions workflow` | `steps` |

---

## Environment variables

| Variable | Default | Required | Description |
|---|---|---|---|
| `OPENCODE_ZEN_API_KEY` | — | ✅ | Your OpenCode Zen API key |
| `OPENCODE_ZEN_BASE_URL` | `https://opencode.ai/zen/v1` | | API base URL |
| `OPENCODE_ZEN_MODEL` | `deepseek-v4-flash` | | LLM model |
| `GITHUB_PERSONAL_ACCESS_TOKEN` | — | ✅ | GitHub PAT (`repo`, `read:org`, `read:user`) |

---

## How the ReAct loop works

```
User: "List open PRs in ameeng/techxchange2-2026-automation"
  │
  ▼  Round 1 — LLM output:
  TOOL_CALL
  { "tool": "list_pull_requests", "args": { "owner": "ameeng", "repo": "techxchange2-2026-automation", "state": "open" } }
  │
  ▼  MCP executes list_pull_requests → returns JSON
  │
  ▼  Round 2 — LLM output:
  FINAL_ANSWER
  {
    "ui_type": "pr_list",
    "data": {
      "repo": "ameeng/techxchange2-2026-automation",
      "prs": [
        { "number": 5, "title": "feat: add GitHub MCP client", "state": "open", "author": "ameeng", ... }
      ]
    }
  }
  │
  ▼  Browser renders PRListTemplate
```
