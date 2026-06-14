---
name: use-uv
description: Use when working with any Python library, package, or environment in this project. Always use uv to manage virtualenvs and install packages — never use pip, pip3, or global Python directly.
---

# Use UV for Python Environment Management

**Rule:** ALL Python dependency management must use `uv`. Never use `pip`, `pip3`, `python -m pip`, or install packages globally — ever.

---

## Starting a new Python project

Always initialise with `uv init`:

```bash
uv init <project-name>
cd <project-name>
```

This creates `pyproject.toml`, `.python-version`, `.venv`, `README.md`, and a starter `main.py`.

Use `--no-workspace` if the project should be standalone (not nested in a uv workspace):

```bash
uv init --no-workspace <project-name>
```

---

## Adding dependencies

Always use `uv add` — it updates `pyproject.toml` **and** installs into `.venv` in one step:

```bash
uv add langchain langchain-openai faiss-cpu
```

For dev-only dependencies:

```bash
uv add --dev pytest ruff
```

Never manually edit `dependencies = [...]` and then run `pip install`.

---

## Syncing an existing project

When a `pyproject.toml` already exists (e.g. after cloning):

```bash
uv sync
```

This creates/updates `.venv` and installs all declared dependencies.

---

## Running scripts

Use `uv run` — it automatically uses the project's `.venv`:

```bash
uv run main.py
uv run pytest
```

Or invoke the venv's Python directly:

```bash
.venv/bin/python main.py
```

---

## Checking installed packages

```bash
uv pip list
```

---

## Never do these

- ❌ `pip install ...`
- ❌ `pip3 install ...`
- ❌ `python -m pip install ...`
- ❌ `uv pip install ...` when a `pyproject.toml` exists — use `uv add` instead
- ❌ Creating a venv manually with `python -m venv`
- ❌ Installing packages globally

---

## Quick reference

| Task | Command |
|---|---|
| New project | `uv init <name>` |
| Add a package | `uv add <package>` |
| Add a dev package | `uv add --dev <package>` |
| Install from pyproject.toml | `uv sync` |
| Run a script | `uv run <script.py>` |
| List installed packages | `uv pip list` |
