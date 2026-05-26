# MewgenicsReferenceMCP — Agent Doctrine

## What This Repo Is

A game reference knowledge base for Mewgenics. Agents use it to look up mechanics, abilities, UI elements, assets, and structured game data. The primary interface is an MCP server. Do not treat this as a general codebase — the "product" is the data and the retrieval pipeline.

## Scripting-First Rule

Do not read wiki pages or large files into context to copy them to disk. Use scripts.

**The rule:** Can a script answer this question or perform this action? If yes, write the script.

Context loading is reserved for cases where:
1. There is a specific reason it cannot be accomplished via scripting, AND
2. All scripting approaches have been exhausted

Verification is done via stats, sampling (2–3 spot-checked files), and the eval suite — not by reading full output into context.

## Architecture

- **Language:** Python, managed with `uv`
- **Embedding model:** `Jina v5-text-small` (local, Apache 2.0, cross-platform)
- **Vector store:** ChromaDB (file-based, ships in repo)
- **Structured data:** SQLite (abilities, cat names, body parts, etc.)
- **Interface:** MCP server only
- **Paths:** `pathlib.Path` everywhere — no string concatenation
- **Platforms:** Mac (including Apple Silicon) and Windows

## Content Model

| Content type | Storage | Agent access |
|---|---|---|
| Mechanics, abilities, lore | Atomic `.md` files + ChromaDB embeddings | `search()` → `read_file()` |
| Large tabular data (abilities table, body parts, cat names) | SQLite | `query_db()` with filters |
| Images | Files on disk, referenced from `.md` sidecars | Retrieved via the doc that references them |

Large lists are never returned whole. Agents query SQLite with filters. Short tables (a few dozen rows) may live in markdown.

## MCP Tools

- `search(query, n=5, types=[...])` — semantic search, returns ranked paths + excerpts
- `read_file(path)` — read a specific file into context
- `query_db(table, filters={}, limit=20)` — query structured data
- `list_db_tables()` — discover available tables and their schemas

## Path Handling

This repo is developed on both Windows and Unix. All text files use LF line endings (enforced via `.gitattributes`). Use `pathlib.Path` in all Python code. Never construct paths via string concatenation.

## Integrating This Repo

1. Clone the repo
2. `uv sync`
3. Start the MCP server: `uv run mcp_server.py`
4. Add to your project's MCP config
5. Add one paragraph to your `CLAUDE.md` or `AGENTS.md` describing what the server can do
