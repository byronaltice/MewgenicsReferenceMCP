"""
MCP server for Mewgenics game reference data.

Exposes semantic search (ChromaDB), file reading, and SQLite queries
over the Mewgenics wiki and structured data.

Usage:
    uv run mcp_server.py
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent
CONTENT_DIR = REPO_ROOT / "content"
CHROMA_DIR = REPO_ROOT / "data" / "chroma"
DB_PATH = REPO_ROOT / "data" / "mewgenics.db"

# ---------------------------------------------------------------------------
# Embedding model config (mirrors scripts/embed.py)
# ---------------------------------------------------------------------------

PRIMARY_MODEL = "jinaai/jina-embeddings-v3"
FALLBACK_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CHROMA_COLLECTION = "mewgenics_wiki"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger("mewgenics_mcp")

# ---------------------------------------------------------------------------
# Startup checks
# ---------------------------------------------------------------------------

_hf_token: str | None = os.environ.get("HF_TOKEN")
if not _hf_token:
    log.warning(
        "HF_TOKEN env var not set — the `search` tool will return errors. "
        "Other tools are unaffected."
    )

if not DB_PATH.exists():
    log.warning("data/mewgenics.db not found — `query_db` and `list_db_tables` will return errors.")

if not CHROMA_DIR.exists():
    log.warning("data/chroma/ not found — `search` will return errors.")

# ---------------------------------------------------------------------------
# Lazy model + collection loader
# ---------------------------------------------------------------------------

_model: Any = None
_model_id: str | None = None
_chroma_collection: Any = None


def _get_model() -> tuple[Any, str]:
    """Load embedding model on first call (lazy). Returns (model, model_id)."""
    global _model, _model_id
    if _model is not None:
        return _model, _model_id  # type: ignore[return-value]

    from sentence_transformers import SentenceTransformer

    for candidate in (PRIMARY_MODEL, FALLBACK_MODEL):
        try:
            log.info("Loading embedding model: %s", candidate)
            m = SentenceTransformer(candidate, trust_remote_code=True, device="cpu")
            log.info("Embedding model loaded: %s", candidate)
            _model = m
            _model_id = candidate
            return _model, _model_id
        except Exception as exc:
            log.warning("Failed to load %s: %s", candidate, exc)
            if candidate == FALLBACK_MODEL:
                raise RuntimeError("Both primary and fallback embedding models failed.") from exc

    raise RuntimeError("Unreachable")


def _get_collection() -> Any:
    """Return ChromaDB collection, creating client on first call."""
    global _chroma_collection
    if _chroma_collection is not None:
        return _chroma_collection

    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    _chroma_collection = client.get_collection(CHROMA_COLLECTION)
    return _chroma_collection


# ---------------------------------------------------------------------------
# Known table metadata
# ---------------------------------------------------------------------------

_TABLE_DESCRIPTIONS: dict[str, str] = {
    "abilities": "Game abilities with class, type, description, and icon path",
    "mutations": "Cat mutations with rarity, effects, and description",
    "disorders": "Cat disorders with severity, triggers, and description",
}

# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------

mcp = FastMCP("MewgenicsRef")


@mcp.tool()
def search(query: str, n: int = 5, types: list[str] = ["wiki"]) -> list[dict] | str:
    """Semantic search over Mewgenics wiki content stored in ChromaDB.

    Args:
        query: Natural language query (e.g. "warrior class passive abilities")
        n: Number of results to return (1-20, default 5)
        types: Filter by content_type values. Default ["wiki"].
    Returns:
        List of dicts with path, section, title, excerpt, and score.
    """
    if not _hf_token:
        return (
            "ERROR: HF_TOKEN environment variable is not set. "
            "Cannot load embedding model. Set HF_TOKEN and restart the server."
        )

    if not CHROMA_DIR.exists():
        return "ERROR: data/chroma/ directory not found. Run `uv run scripts/embed.py` first."

    n = max(1, min(n, 20))

    try:
        model, _ = _get_model()
    except Exception as exc:
        return f"ERROR: Failed to load embedding model: {exc}"

    try:
        collection = _get_collection()
    except Exception as exc:
        return f"ERROR: Failed to open ChromaDB collection: {exc}"

    try:
        import torch

        with torch.no_grad():
            vec = model.encode([query], show_progress_bar=False).tolist()

        where: dict | None = None
        if types:
            if len(types) == 1:
                where = {"content_type": {"$eq": types[0]}}
            else:
                where = {"content_type": {"$in": types}}

        kwargs: dict[str, Any] = {
            "query_embeddings": vec,
            "n_results": n,
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = collection.query(**kwargs)

        output: list[dict] = []
        for doc, meta, dist in zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            output.append(
                {
                    "path": meta.get("source_path", ""),
                    "section": meta.get("section", ""),
                    "title": meta.get("title", ""),
                    "excerpt": doc[:300],
                    "score": round(float(dist), 4),
                }
            )

        # Lower cosine distance = better match; already sorted by ChromaDB
        return output

    except Exception as exc:
        return f"ERROR: Search failed: {exc}"


@mcp.tool()
def read_file(path: str) -> str:
    """Read a content file by its repo-relative path.

    Args:
        path: Repo-relative path, e.g. "content/classes/fighter.md"
    Returns:
        File contents as a string, or an error message.
    """
    try:
        target = (REPO_ROOT / path).resolve()
    except Exception as exc:
        return f"ERROR: Invalid path: {exc}"

    # Security: only allow paths inside content/
    content_root = CONTENT_DIR.resolve()
    if not str(target).startswith(str(content_root)):
        return (
            f"ERROR: Path must be inside content/ directory. "
            f"Got: {path!r}"
        )

    if not target.exists():
        return f"ERROR: File not found: {path!r}"

    if not target.is_file():
        return f"ERROR: Path is not a file: {path!r}"

    try:
        return target.read_text(encoding="utf-8")
    except Exception as exc:
        return f"ERROR: Could not read file: {exc}"


@mcp.tool()
def query_db(table: str, filters: dict = {}, limit: int = 20) -> list[dict] | str:
    """Query the Mewgenics SQLite database.

    Args:
        table: Table name (e.g. "abilities", "mutations", "disorders")
        filters: Column→value pairs for WHERE clause (AND-joined)
        limit: Max rows to return (1-100, default 20)
    Returns:
        List of row dicts, or an error message string.
    """
    if not DB_PATH.exists():
        return "ERROR: data/mewgenics.db not found. Run the ingest pipeline first."

    limit = max(1, min(limit, 100))

    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row

        # Validate table name against known tables to prevent injection
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        if cursor.fetchone() is None:
            conn.close()
            known = _list_known_tables(conn if False else sqlite3.connect(str(DB_PATH)))
            table_names = [t["name"] for t in known]
            return (
                f"ERROR: Table {table!r} does not exist. "
                f"Available tables: {table_names}"
            )

        # Build parameterized query
        where_clauses: list[str] = []
        params: list[Any] = []
        for col, val in filters.items():
            # Validate column name (only allow alphanumeric + underscore)
            if not col.replace("_", "").isalnum():
                conn.close()
                return f"ERROR: Invalid column name: {col!r}"
            where_clauses.append(f"{col} = ?")
            params.append(val)

        sql = f"SELECT * FROM {table}"  # table name validated above
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        sql += f" LIMIT {limit}"

        rows = conn.execute(sql, params).fetchall()
        conn.close()

        return [dict(row) for row in rows]

    except sqlite3.Error as exc:
        return f"ERROR: Database error: {exc}"


def _list_known_tables(conn: sqlite3.Connection) -> list[dict]:
    """Internal helper — list tables with metadata."""
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]
    result = []
    for name in tables:
        col_cursor = conn.execute(f"PRAGMA table_info({name})")
        columns = [row[1] for row in col_cursor.fetchall()]
        count_row = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()
        row_count = count_row[0] if count_row else 0
        description = _TABLE_DESCRIPTIONS.get(
            name, f"Table '{name}' containing {row_count} records"
        )
        result.append(
            {
                "name": name,
                "description": description,
                "columns": columns,
                "row_count": row_count,
            }
        )
    return result


@mcp.tool()
def list_db_tables() -> list[dict] | str:
    """Discover available SQLite tables and their schemas.

    Returns:
        List of dicts with name, description, columns, and row_count.
    """
    if not DB_PATH.exists():
        return "ERROR: data/mewgenics.db not found. Run the ingest pipeline first."

    try:
        conn = sqlite3.connect(str(DB_PATH))
        result = _list_known_tables(conn)
        conn.close()
        return result
    except sqlite3.Error as exc:
        return f"ERROR: Database error: {exc}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("MewgenicsRef MCP server ready")
    mcp.run()
