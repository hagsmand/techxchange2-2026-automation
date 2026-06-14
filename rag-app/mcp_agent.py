"""
mcp_agent.py — ReAct-style GitHub MCP App agent.

Uses prompt-based tool calling (ReAct pattern) so it works with ANY
OpenAI-compatible model — no native function-calling required.

Flow:
  1. Connect to GitHub MCP Server via official Python SDK
  2. LLM selects a GitHub tool (or goes straight to FINAL_ANSWER)
  3. Execute tool via MCP → inject result back as a user message
  4. LLM writes FINAL_ANSWER { ui_type, data }
  Max 8 tool calls to support multi-step GitHub workflows.

Supported UI types:
  text | card | card_list | table | code | steps |
  alert_list | metric | pr_list | issue_list | repo_card | commit_list | diff
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from mcp_client import GitHubMCPClient

load_dotenv(Path(__file__).parent / ".env")

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8
# Maximum characters of a tool result to feed back to the LLM.
# GitHub MCP can return huge JSON arrays; we truncate to keep prompts
# within token budget while still giving the model the key fields.
MAX_TOOL_RESULT_CHARS = 20_000


# ─────────────────────────────────────────────────────────────────────────────
# System prompt
# ─────────────────────────────────────────────────────────────────────────────

def _build_system_prompt(tools: list[dict]) -> str:
    """Build a ReAct system prompt embedding all GitHub MCP tool schemas."""

    if tools:
        lines = []
        for t in tools:
            fn = t["function"]
            params = json.dumps(fn.get("parameters", {}), indent=2)
            lines.append(f"### {fn['name']}\n{fn['description']}\nParameters:\n{params}")
        tool_descriptions = "\n\n---\n\n".join(lines)
    else:
        tool_descriptions = "(No GitHub tools available — answer from general knowledge)"

    return f"""You are a GitHub MCP App — an intelligent assistant that uses the GitHub MCP Server
to help developers manage repositories, issues, pull requests, CI/CD pipelines, and more.

=== AVAILABLE GITHUB TOOLS ===
{tool_descriptions}

=== HOW TO CALL TOOLS ===
Output EXACTLY this block (nothing before or after):

TOOL_CALL
{{
  "tool": "<tool_name>",
  "args": {{<arguments as valid JSON>}}
}}

You will receive TOOL_RESULT, then continue reasoning or write FINAL_ANSWER.

=== FINAL ANSWER FORMAT ===
When ready, output EXACTLY this block (no code fences, no markdown, raw text only):

FINAL_ANSWER
{{
  "view": "<view name — see guide below>",
  "data": {{...data to pass to the view iframe...}},
  "text": "<one-sentence plain-text summary for the chat>"
}}

=== VIEW SELECTION GUIDE ===
Pick the view that best matches the content. Use "text" only for pure conversational/general answers.

GitHub interactive views (rendered as interactive iframes):
- github_repo     → single repository details
                    data: the raw repo JSON object (e.g. items[0] from search_repositories)
- github_prs      → list of pull requests
                    data: {{ "full_name": "owner/repo", "prs": [...raw PR objects...] }}
- github_issues   → list of issues
                    data: {{ "full_name": "owner/repo", "issues": [...raw issue objects...] }}
- github_commits  → list of commits
                    data: {{ "full_name": "owner/repo", "branch": "main", "commits": [...raw commit objects...] }}
- github_search   → repository search results
                    data: {{ "query": "...", "items": [...raw repo objects...] }}

Plain (non-iframe) views:
- text      → {{ "content": "..." }}
- table     → {{ "caption": "...", "columns": ["..."], "rows": [["..."]] }}
- code      → {{ "language": "python|bash|json|yaml", "code": "..." }}
- steps     → {{ "title": "...", "steps": ["..."] }}

=== GITHUB TOOL SELECTION GUIDE ===
- Repo info (single repo)           → search_repositories with "owner/repo" as query → use view: github_repo
                                      data: items[0] from the result (the first/best match)
- Search repos                      → search_repositories  → use view: github_search
                                      data: {{"query": "...", "items": [...]}}
- List issues                       → list_issues          → use view: github_issues
                                      data: {{"full_name": "owner/repo", "issues": [...]}}
- List PRs                          → list_pull_requests   → use view: github_prs
                                      data: {{"full_name": "owner/repo", "prs": [...]}}
- List commits                      → list_commits         → use view: github_commits
                                      data: {{"full_name": "owner/repo", "branch": "main", "commits": [...]}}
- PR files / reviews                → get_pull_request_files, get_pull_request_reviews
- Create/update files               → create_or_update_file
- Code search                       → search_code
- Single issue                      → get_issue
- Single PR                         → get_pull_request

=== RULES ===
- For GitHub questions: call the most appropriate tool first, then write FINAL_ANSWER.
- For conversational questions: go straight to FINAL_ANSWER with view "text".
- Chain up to {MAX_TOOL_ROUNDS} tool calls for multi-step workflows.
- Output ONLY a TOOL_CALL block OR a FINAL_ANSWER block — no prose, no markdown.
- NEVER use code fences. Output raw text only.
- When owner/repo is unknown, ask for clarification using view "text".
- In "data", copy the raw JSON objects/arrays from the TOOL_RESULT exactly as given.
  Do NOT summarise, paraphrase, or replace them with prose descriptions.
  The iframe view will display them — the user needs the real JSON data.
- Always use a github_* view (not "text") when you have GitHub data to display.
""".strip()


# ─────────────────────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────────────────────

class MCPAgent:
    """
    ReAct-style GitHub MCP App agent.
    Connects to the GitHub MCP Server, orchestrates tool calls,
    and returns a Declarative UI JSON payload for the frontend.
    """

    def __init__(self):
        self._llm = OpenAI(
            api_key=os.environ.get("OPENCODE_ZEN_API_KEY", ""),
            base_url=os.environ.get("OPENCODE_ZEN_BASE_URL", "https://opencode.ai/zen/v1"),
        )
        self._model = os.environ.get("OPENCODE_ZEN_MODEL", "deepseek-v4-flash")
        self._github_token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")

    async def run(self, user_messages: list[dict]) -> dict[str, Any]:
        """Run the ReAct loop and return a Declarative UI payload."""
        async with GitHubMCPClient(self._github_token) as mcp:
            tools = await mcp.list_tools_openai_format()

            if mcp.is_connected:
                logger.info("GitHub MCP connected — %d tools available", len(tools))
            else:
                logger.warning("GitHub MCP unavailable — running in offline mode")

            system_prompt = _build_system_prompt(tools)
            messages = [{"role": "system", "content": system_prompt}, *user_messages]

            for round_num in range(MAX_TOOL_ROUNDS):
                logger.info("Agent round %d/%d", round_num + 1, MAX_TOOL_ROUNDS)

                response = self._llm.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    max_tokens=4096,
                    temperature=0.1,
                )
                raw = (response.choices[0].message.content or "").strip()
                logger.debug("LLM output (first 400 chars): %s", raw[:400])

                # Normalised view: strip fences so keyword checks work even
                # when the model wraps its output in ```json ... ```
                raw_normalised = _strip_fences(raw)

                # ── FINAL_ANSWER ────────────────────────────────────────────
                if "FINAL_ANSWER" in raw or "FINAL_ANSWER" in raw_normalised:
                    result = _extract_final_answer(raw)
                    # If extraction succeeded and view is not "text" fallback, return it
                    if result.get("view") != "text" or not result.get("data", {}).get("content", "").startswith("FINAL_ANSWER"):
                        return result
                    # Otherwise fall through to try re-asking

                # ── Direct bare JSON (model skipped the keyword entirely) ───
                direct = _try_parse_json(raw) or _try_parse_json(raw_normalised)
                if direct:
                    logger.debug("Model returned bare JSON without FINAL_ANSWER keyword")
                    return direct

                # ── TOOL_CALL ───────────────────────────────────────────────
                if "TOOL_CALL" in raw or "TOOL_CALL" in raw_normalised:
                    tool_name, tool_args = _extract_tool_call(raw)
                    if tool_name:
                        logger.info("  → %s(%s)", tool_name, json.dumps(tool_args)[:120])
                        result = await mcp.call_tool(tool_name, tool_args)
                        logger.info("  ← %d chars returned", len(result))

                        # Trim tool results before feeding to LLM:
                        #   1. Parse JSON and strip irrelevant nested fields (PR head/base repos, etc.)
                        #   2. Hard-truncate to MAX_TOOL_RESULT_CHARS as safety net
                        trimmed = _trim_tool_result(result)
                        truncated = trimmed if len(trimmed) <= MAX_TOOL_RESULT_CHARS else (
                            trimmed[:MAX_TOOL_RESULT_CHARS] + "\n... (truncated)"
                        )
                        messages.append({"role": "assistant", "content": raw})
                        messages.append({
                            "role": "user",
                            "content": (
                                f"TOOL_RESULT for {tool_name}:\n{truncated}\n\n"
                                "IMPORTANT: You MUST now output EXACTLY a FINAL_ANSWER block "
                                "in valid JSON — NO prose, NO markdown, NO tables, NO explanation.\n"
                                "Example:\n"
                                'FINAL_ANSWER\n'
                                '{"view":"github_prs","data":{"full_name":"owner/repo","prs":[...]},'
                                '"text":"one sentence summary"}'
                            ),
                        })
                        continue

                # ── No pattern matched — nudge toward FINAL_ANSWER ──────────
                logger.info("No TOOL_CALL/FINAL_ANSWER/JSON pattern — nudging model")
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": (
                        "STOP. Output ONLY a FINAL_ANSWER block in JSON — no prose, no markdown:\n"
                        'FINAL_ANSWER\n{"view":"...","data":{...},"text":"..."}'
                    ),
                })

            # ── Fallback after max rounds ────────────────────────────────────
            logger.warning("Max rounds reached — forcing FINAL_ANSWER")
            final = self._llm.chat.completions.create(
                    model=self._model,
                    messages=messages + [{
                        "role": "user",
                        "content": (
                            "You have all the information you need. "
                            "Output your FINAL_ANSWER now:\n"
                            'FINAL_ANSWER\n{"view": "...", "data": {...}, "text": "..."}'
                        ),
                    }],
                    max_tokens=4096,
                    temperature=0.1,
                )
            raw = (final.choices[0].message.content or "").strip()
            return _extract_final_answer(raw)


# ─────────────────────────────────────────────────────────────────────────────
# Tool-result trimmer
# ─────────────────────────────────────────────────────────────────────────────

_REPO_KEEP = {
    "full_name", "html_url", "description", "private", "language",
    "stargazers_count", "forks_count", "open_issues_count", "default_branch",
    "license", "topics", "name", "owner",
}
_PR_KEEP = {
    "number", "title", "state", "user", "created_at", "updated_at",
    "merged_at", "closed_at", "html_url", "body", "labels", "draft",
}
_ISSUE_KEEP = {
    "number", "title", "state", "user", "created_at", "updated_at",
    "closed_at", "html_url", "body", "labels", "assignees",
}
_COMMIT_KEEP = {"sha", "commit", "author", "html_url"}


def _trim_repo(obj: dict) -> dict:
    out = {k: v for k, v in obj.items() if k in _REPO_KEEP}
    # Slim down license to just the name
    if isinstance(out.get("license"), dict):
        out["license"] = {"name": out["license"].get("name")}
    # Slim down owner to just login
    if isinstance(out.get("owner"), dict):
        out["owner"] = {"login": out["owner"].get("login")}
    return out


def _trim_pr(obj: dict) -> dict:
    out = {k: v for k, v in obj.items() if k in _PR_KEEP}
    if isinstance(out.get("user"), dict):
        out["user"] = {"login": out["user"].get("login")}
    if isinstance(out.get("labels"), list):
        out["labels"] = [{"name": lb.get("name")} for lb in out["labels"]]
    if isinstance(out.get("body"), str):
        out["body"] = out["body"][:300]
    return out


def _trim_issue(obj: dict) -> dict:
    out = {k: v for k, v in obj.items() if k in _ISSUE_KEEP}
    if isinstance(out.get("user"), dict):
        out["user"] = {"login": out["user"].get("login")}
    if isinstance(out.get("labels"), list):
        out["labels"] = [{"name": lb.get("name")} for lb in out["labels"]]
    if isinstance(out.get("assignees"), list):
        out["assignees"] = [{"login": a.get("login")} for a in out["assignees"]]
    if isinstance(out.get("body"), str):
        out["body"] = out["body"][:300]
    return out


def _trim_commit(obj: dict) -> dict:
    out = {k: v for k, v in obj.items() if k in _COMMIT_KEEP}
    # Keep only message + author.name + author.date from the nested commit
    if isinstance(out.get("commit"), dict):
        c = out["commit"]
        out["commit"] = {
            "message": c.get("message", "")[:200],
            "author": {
                "name": c.get("author", {}).get("name"),
                "date": c.get("author", {}).get("date"),
            },
        }
    if isinstance(out.get("author"), dict):
        out["author"] = {"login": out["author"].get("login")}
    return out


def _trim_tool_result(text: str) -> str:
    """
    Parse a GitHub MCP tool result (JSON string) and slim it down:
    - Strip PR head/base/diff fields, nested repo objects, node_ids, etc.
    - Limit lists to 20 items.
    Returns the trimmed JSON string, or the original text if not parseable.
    """
    try:
        data = json.loads(text)
    except Exception:
        return text  # not JSON — return as-is

    try:
        # List of PRs
        if isinstance(data, list) and data and isinstance(data[0], dict):
            first = data[0]
            if "merged_at" in first or "head" in first:
                # PR list
                return json.dumps([_trim_pr(p) for p in data[:20]])
            if "state" in first and "labels" in first and "body" in first:
                # Issue list
                return json.dumps([_trim_issue(i) for i in data[:20]])
            if "sha" in first or "commit" in first:
                # Commit list
                return json.dumps([_trim_commit(c) for c in data[:20]])
            if "full_name" in first or "stargazers_count" in first:
                # Repo search results
                return json.dumps([_trim_repo(r) for r in data[:20]])

        # Single repo object
        if isinstance(data, dict) and ("full_name" in data or "stargazers_count" in data):
            return json.dumps(_trim_repo(data))

        # Search result wrapper { items: [...], total_count: ... }
        if isinstance(data, dict) and "items" in data:
            items = data["items"]
            if items and "full_name" in items[0]:
                data["items"] = [_trim_repo(r) for r in items[:20]]
            elif items and ("merged_at" in items[0] or "head" in items[0]):
                data["items"] = [_trim_pr(p) for p in items[:20]]
            elif items and "state" in items[0]:
                data["items"] = [_trim_issue(i) for i in items[:20]]
            return json.dumps(data)

    except Exception as exc:
        logger.debug("_trim_tool_result partial failure: %s", exc)

    return text  # fallback — return original


# ─────────────────────────────────────────────────────────────────────────────
# Parsers
# ─────────────────────────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    """Strip markdown code fences (``` or ~~~) from a string."""
    text = text.strip()
    # Remove opening fence: ```json, ```JSON, ```, ~~~, etc.
    text = re.sub(r"^```[a-zA-Z]*\s*\n?", "", text)
    text = re.sub(r"^~~~[a-zA-Z]*\s*\n?", "", text)
    # Remove closing fence
    text = re.sub(r"\n?```\s*$", "", text)
    text = re.sub(r"\n?~~~\s*$", "", text)
    return text.strip()


def _try_parse_json(text: str) -> dict | None:
    """Try to parse text as JSON, stripping fences first. Returns None on failure."""
    for candidate in [text, _strip_fences(text)]:
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict) and ("view" in payload or "ui_type" in payload):
                return payload
        except Exception:
            pass
    return None


def _extract_first_json_object(text: str) -> dict | None:
    """
    Walk through `text` looking for the first balanced {...} that decodes to a
    valid { view, data } or { ui_type, data } payload. Handles nested braces correctly.
    """
    start = 0
    while True:
        start = text.find("{", start)
        if start == -1:
            return None
        depth = 0
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    result = _try_parse_json(candidate)
                    if result:
                        return result
                    # Try next occurrence
                    start = i + 1
                    break
        else:
            break
    return None


def _extract_tool_call(text: str) -> tuple[str, dict]:
    """Extract tool name and args from a TOOL_CALL block."""
    # Strip any fences wrapping the whole output
    cleaned = _strip_fences(text) if text.strip().startswith("```") else text
    try:
        # Match everything after TOOL_CALL keyword up to the outermost }
        match = re.search(r"TOOL_CALL\s*\n?\s*(\{.*\})", cleaned, re.DOTALL)
        if match:
            payload = json.loads(match.group(1))
            return payload.get("tool", ""), payload.get("args", {})
    except Exception as e:
        logger.warning("Failed to parse TOOL_CALL: %s", e)
    return "", {}


def _extract_final_answer(text: str) -> dict[str, Any]:
    """
    Extract { ui_type, data } from LLM output using multiple strategies:

    1. Strip outer markdown fence, then find FINAL_ANSWER keyword + JSON
    2. Find FINAL_ANSWER keyword in raw text + JSON
    3. Scan for the first balanced {...} with ui_type/data keys
    4. Try parsing the whole (fence-stripped) text as JSON
    5. Graceful fallback: wrap raw text in a text template

    This handles every observed model output pattern:
    - FINAL_ANSWER\\n{ ... }
    - ```json\\nFINAL_ANSWER\\n{ ... }\\n```
    - ```json\\n{ ... }\\n```   (keyword omitted)
    - { ... }                   (bare JSON)
    - prose with embedded JSON
    """
    logger.debug("_extract_final_answer input (first 300): %s", text[:300])

    # ── Strategy 1 & 2: look for FINAL_ANSWER keyword ──────────────────────
    # Try both the raw text and the fence-stripped version
    for candidate_text in [text, _strip_fences(text)]:
        match = re.search(r"FINAL_ANSWER\s*\n?\s*(\{.*\})", candidate_text, re.DOTALL)
        if match:
            result = _try_parse_json(match.group(1))
            if result:
                logger.debug("Parsed via FINAL_ANSWER keyword")
                return result

    # ── Strategy 3: whole-text / fence-stripped direct parse ───────────────
    result = _try_parse_json(text)
    if result:
        logger.debug("Parsed as bare JSON (whole text)")
        return result

    result = _try_parse_json(_strip_fences(text))
    if result:
        logger.debug("Parsed as bare JSON (fence-stripped)")
        return result

    # ── Strategy 4: scan for first balanced { } with ui_type/data ──────────
    for candidate_text in [text, _strip_fences(text)]:
        result = _extract_first_json_object(candidate_text)
        if result:
            logger.debug("Parsed via first-balanced-brace scan")
            return result

    # ── Strategy 5: graceful fallback ──────────────────────────────────────
    logger.warning("All extraction strategies failed — returning raw text fallback")
    return {"view": "text", "data": {"content": text.strip()}, "text": text.strip()[:200]}
