# MewgenicsRef MCP — Integration Guide

## Setup

```bash
git clone https://github.com/byronaltice/MewgenicsReferenceMCP
cd MewgenicsReferenceMCP
uv sync
```

**What's already in the repo (no pipeline needed):**
- `content/` — all scraped wiki pages as markdown files
- `data/mewgenics.db` — SQLite database (abilities 1160 rows, mutations 760 rows, disorders 126 rows)

**What's NOT in the repo (generated locally, gitignored):**
- `data/chroma/` — ChromaDB vector embeddings

On a fresh clone, you must generate the embeddings before `search` will work:

```bash
uv run scripts/embed.py
```

This downloads the embedding model (~600 MB on first run) and indexes all content files. `read_file`, `query_db`, and `list_db_tables` work immediately without this step.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `HF_TOKEN` | Yes (for `search`) | HuggingFace API token. Needed to download the Jina embedding model. Get one at https://huggingface.co/settings/tokens |

Set it in your shell before starting the server:

```bash
export HF_TOKEN=hf_...
```

## Running the server

```bash
uv run mcp_server.py
```

The server prints `MewgenicsRef MCP server ready` on startup and listens on stdio. If `HF_TOKEN` is missing, it logs a warning but starts anyway — the `search` tool will return an error, while `read_file`, `query_db`, and `list_db_tables` still work.

## Adding to a Claude Code project

Add the following to your project's `.claude/mcp.json` (or the equivalent MCP config for your tool):

```json
{
  "mcpServers": {
    "mewgenics": {
      "command": "uv",
      "args": ["run", "mcp_server.py"],
      "cwd": "/absolute/path/to/MewgenicsReferenceMCP",
      "env": {
        "HF_TOKEN": "<your-token>"
      }
    }
  }
}
```

Replace `/absolute/path/to/MewgenicsReferenceMCP` with the actual path on your machine.

After adding the server, add one paragraph to your project's `CLAUDE.md` describing what it can do, for example:

> The `mewgenics` MCP server provides access to Mewgenics game reference data. Use `search` to look up mechanics, abilities, lore, and UI elements by natural language query; `read_file` to read a specific wiki page; `query_db` to filter structured data (abilities, mutations, disorders) from the SQLite database; and `list_db_tables` to discover what tables exist.

## Tools

### `search(query, n=5, types=["wiki"])`

Semantic search over embedded wiki content. Returns up to `n` ranked results (max 20).

- **`query`** — natural language search string
- **`n`** — number of results (default 5, max 20)
- **`types`** — filter by content type (currently `["wiki"]` for all content)

Each result:

```json
{
  "path": "content/classes/fighter.md",
  "section": "Skills",
  "title": "Fighter",
  "excerpt": "first 300 chars of the matching chunk...",
  "score": 0.42
}
```

Lower `score` = closer semantic match. Use `read_file` on the returned `path` to get the full document.

Example queries:
- `search("warrior passive abilities", n=10)`
- `search("what does the heal mutation do")`
- `search("cat disorder effects on breeding")`

### `read_file(path)`

Read a wiki page or content file by its repo-relative path.

- **`path`** — e.g. `"content/classes/fighter.md"`

Returns the full file contents. Paths outside `content/` are rejected for security. Returns a clear error string if the file is not found.

Typical pattern: `search(...)` to find relevant paths, then `read_file(path)` for the full text.

### `query_db(table, filters={}, limit=20)`

Query the SQLite structured data store.

- **`table`** — one of `abilities`, `mutations`, `disorders`
- **`filters`** — column→value dict, AND-joined (e.g. `{"class": "Warrior"}`)
- **`limit`** — max rows returned (default 20, max 100)

Returns a list of row dicts. Use `list_db_tables` first to see available columns.

Example queries:
- `query_db("abilities", {"class": "Warrior"}, limit=50)`
- `query_db("mutations", {"rarity": "Rare"})`
- `query_db("disorders", limit=10)`

### `list_db_tables()`

Discover all available SQLite tables, their columns, and row counts.

Returns:

```json
[
  {
    "name": "abilities",
    "description": "Game abilities with class, type, description, and icon path",
    "columns": ["name", "class", "type", "description", "attributes", "icon_path"],
    "row_count": 1160
  },
  ...
]
```

Call this before `query_db` if you don't know the schema.

## Re-ingesting content

If the wiki changes and you need to update the scraped content:

```bash
# Re-scrape wiki pages → updates content/ and data/mewgenics.db
uv run scripts/scrape.py

# Re-embed updated content → updates data/chroma/
uv run scripts/embed.py
```

The embed script is incremental — it skips chunks already in the vector store. If you need a full re-embed, delete `data/chroma/` first.

**Note:** Only run `scrape.py` when wiki content has actually changed. On a fresh clone, the scraped content is already committed — run `embed.py` only.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `search` returns `ERROR: HF_TOKEN not set` | Missing env var | Set `HF_TOKEN` and restart server |
| `search` returns `ERROR: data/chroma/ not found` | Embeddings not generated | Run `uv run scripts/embed.py` |
| `query_db` returns `ERROR: data/mewgenics.db not found` | DB missing (should be in repo) | Run `git pull` — if still missing, run `uv run scripts/scrape.py` |
| Model download hangs | First run downloads ~600 MB | Wait; check network and HuggingFace token validity |
