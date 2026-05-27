# Handoff: MewgenicsReferenceMCP Setup

**Session:** fix-embed-script-docs  
**Started:** 2026-05-26  
**Topic:** Designing and building the MewgenicsReferenceMCP game reference repo from scratch

---

## What Was Accomplished

- Designed the full architecture: atomic markdown files + ChromaDB embeddings + SQLite for large tables + MCP server
- Created the GitHub repo at https://github.com/byronaltice/MewgenicsReferenceMCP
- Cloned to `~/p/MewgenicsReferenceMCP` on Mac
- Wrote doctrine: `CLAUDE.md`, `docs/splitting-strategy.md`, `docs/large-list-handling.md`, `docs/wiki-scope.md`
- Sampled 3 wiki pages to understand HTML structure (House, Classes, Abilities)
- Wrote and ran `scripts/scrape.py` — scraped 10 priority pages, downloaded 2484 images concurrently, extracted 3 SQLite tables
- Wrote and ran `scripts/embed.py` — embedded 100 chunks using `jinaai/jina-embeddings-v3` into ChromaDB
- Built `mcp_server.py` with 4 tools: `search`, `read_file`, `query_db`, `list_db_tables`
- Wrote `docs/integration.md` with full setup instructions
- Wired up `MewgenicsBreedingManager` repo: added MCP section to `CLAUDE.md`, created local `.claude/mcp.json` (gitignored)
- Clarified in docs: on fresh clone, only `uv run scripts/embed.py` is needed — `scrape.py` is for wiki updates only

---

## Current State

Everything is committed and pushed. The repo is functional. On any new machine:
1. `git clone https://github.com/byronaltice/MewgenicsReferenceMCP`
2. `uv sync`
3. Set `HF_TOKEN` env var
4. `uv run scripts/embed.py` (generates local ChromaDB — ~600MB model download on first run)
5. Configure `.claude/mcp.json` in the consuming project pointing to this repo
6. Start server: `uv run mcp_server.py`

---

## What's Next

- **Verify the MCP server works end-to-end** in a live Claude Code session on Windows — run `list_db_tables()` then `search("fighter class")` to confirm
- **Scrape remaining class pages** (Tank, Cleric, Thief, Necromancer, Butcher, Druid, Psychic, Tinkerer, Monk, Jester) — add URLs to `docs/wiki-scope.md` first
- **Build the eval suite** — 15-20 hand-labeled query→expected-file pairs, check recall@3 to validate embedding quality
- **Set up GitHub Actions CI** — `macos-latest` + `windows-latest` runners, `uv sync` + test suite
- **Body parts table** — 2500+ images, needs its own SQLite table and scrape logic (not yet started)
- **Lower-priority pages** (listed in `docs/wiki-scope.md`) — Status Effects, Items, Enemies, etc.

---

## Key Files

| File | Purpose |
|---|---|
| `CLAUDE.md` | Agent doctrine — read first |
| `docs/integration.md` | Setup and MCP config instructions |
| `docs/wiki-scope.md` | Prioritized page list |
| `docs/splitting-strategy.md` | How wiki pages become atomic markdown |
| `docs/large-list-handling.md` | SQLite vs markdown decision rules |
| `scripts/scrape.py` | Wiki scraper (run only when wiki changes) |
| `scripts/embed.py` | Embedding pipeline (run on fresh clone + after scrape) |
| `mcp_server.py` | MCP server entry point |
| `data/mewgenics.db` | SQLite — abilities (1160), mutations (760), disorders (126) |
| `data/chroma/` | ChromaDB — gitignored, generated locally |
| `content/` | Atomic markdown files + images |
