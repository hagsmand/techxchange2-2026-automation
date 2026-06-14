"""Smoke tests for the GitHub MCP App."""


def test_placeholder():
    """Placeholder — ensures pytest exits 0 until real tests are added."""
    assert True


def test_api_imports():
    """Verify the main modules can be imported without errors."""
    import importlib
    import sys
    from pathlib import Path

    # Add rag-app to path so imports resolve
    rag_app = str(Path(__file__).parent.parent)
    if rag_app not in sys.path:
        sys.path.insert(0, rag_app)

    for module in ["api", "mcp_client", "mcp_agent"]:
        importlib.import_module(module)


def test_env_example_has_required_keys():
    """Verify .env.example documents all required environment variables."""
    from pathlib import Path
    env_example = (Path(__file__).parent.parent / ".env.example").read_text()
    required = [
        "OPENCODE_ZEN_API_KEY",
        "OPENCODE_ZEN_BASE_URL",
        "OPENCODE_ZEN_MODEL",
        "GITHUB_PERSONAL_ACCESS_TOKEN",
    ]
    for key in required:
        assert key in env_example, f"Missing {key} in .env.example"
