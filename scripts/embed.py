"""
Embedding pipeline for Mewgenics wiki content.

Reads all markdown files from content/, chunks at H2 section boundaries,
generates embeddings using sentence-transformers, and stores in ChromaDB.

Embedding model: jinaai/jina-embeddings-v3 (interim — doctrine specifies
"Jina v5-text-small" but v3 is the stable release available as of 2026-05;
update model ID when v5-text-small is released on HuggingFace).
Fallback: all-MiniLM-L6-v2 if jina-embeddings-v3 fails to load.

Requires HF_TOKEN environment variable for HuggingFace authentication.

Usage:
    uv run scripts/embed.py           # embed all content
    uv run scripts/embed.py --test    # embed + run sanity queries
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# HuggingFace authentication — must happen before any model loading
# ---------------------------------------------------------------------------

def _require_hf_token() -> str:
    """Return HF_TOKEN from environment, or exit with a clear error message."""
    token = os.environ.get("HF_TOKEN")
    if not token:
        print("ERROR: HF_TOKEN environment variable is not set. Cannot authenticate with HuggingFace. Exiting.")
        sys.exit(1)
    return token


def _hf_login(token: str) -> None:
    """Log in to HuggingFace Hub with the provided token."""
    try:
        import huggingface_hub
        huggingface_hub.login(token=token)
        print("HuggingFace login succeeded.")
    except Exception as exc:
        print(f"WARNING: HuggingFace login failed: {exc}")
        print("  Proceeding — public models may still load without login.")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
CONTENT_DIR = REPO_ROOT / "content"
CHROMA_DIR = REPO_ROOT / "data" / "chroma"

# ---------------------------------------------------------------------------
# Model config
# ---------------------------------------------------------------------------

PRIMARY_MODEL = "jinaai/jina-embeddings-v3"
# NOTE: BAAI/bge-m3 requires ~570M params and 2+ GB RAM — too large for some
# machines. Using all-MiniLM-L6-v2 as fallback (~80 MB, fast, high quality).
# When running on a machine with enough RAM, swap FALLBACK_MODEL back to
# "BAAI/bge-m3" for higher embedding quality.
FALLBACK_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"\S+")


def _word_count(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _split_h3(heading: str, body: str, title: str, max_words: int = 1000) -> list[tuple[str, str]]:
    """Split an oversized H2 section further at H3 boundaries."""
    parts = re.split(r"\n(?=### )", body)
    chunks: list[tuple[str, str]] = []
    for part in parts:
        lines = part.splitlines()
        sub_heading = lines[0].lstrip("#").strip() if lines and lines[0].startswith("###") else heading
        text = part.strip()
        if not text:
            continue
        # still too large — hard truncate at max_words (rare edge case)
        words = text.split()
        if len(words) > max_words:
            text = " ".join(words[:max_words])
        chunks.append((sub_heading, text))
    return chunks if chunks else [(heading, body)]


def chunk_file(path: Path) -> list[dict]:
    """Return a list of chunk dicts for a single markdown file."""
    relative = path.relative_to(REPO_ROOT).as_posix()
    raw = path.read_text(encoding="utf-8")

    # Extract H1 title from first line if present
    lines = raw.splitlines()
    title = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("# ") else path.stem

    # Split on H2 boundaries
    # Pattern: split at \n## keeping the delimiter attached to the following part
    sections = re.split(r"\n(?=## )", raw)

    chunks: list[dict] = []

    for i, section in enumerate(sections):
        if i == 0:
            # Content before first H2 (intro / H1 block)
            heading = "root"
            body = section.strip()
        else:
            sec_lines = section.splitlines()
            heading = sec_lines[0].lstrip("# ").strip() if sec_lines else "root"
            body = "\n".join(sec_lines[1:]).strip()

        if not body:
            continue

        # Build context-preserving text for the chunk
        if heading == "root":
            chunk_text = body
        else:
            chunk_text = f"# {title}\n\n## {heading}\n\n{body}"

        # Skip navigation artifacts
        if _word_count(chunk_text) < 20:
            continue

        # Potentially split oversized chunks
        if _word_count(chunk_text) > 1000:
            sub_chunks = _split_h3(heading, body, title)
            for sub_heading, sub_body in sub_chunks:
                if sub_heading == heading:
                    sub_text = chunk_text  # already built above, re-use
                else:
                    sub_text = f"# {title}\n\n## {heading} — {sub_heading}\n\n{sub_body}"
                if _word_count(sub_text) >= 20:
                    chunks.append({
                        "doc_id": f"{relative}::{sub_heading}",
                        "text": sub_text,
                        "metadata": {
                            "source_path": relative,
                            "section": sub_heading,
                            "title": title,
                            "content_type": "wiki",
                        },
                    })
        else:
            chunks.append({
                "doc_id": f"{relative}::{heading}",
                "text": chunk_text,
                "metadata": {
                    "source_path": relative,
                    "section": heading,
                    "title": title,
                    "content_type": "wiki",
                },
            })

    return chunks


# ---------------------------------------------------------------------------
# Embedding model loader
# ---------------------------------------------------------------------------

def load_model():
    """Load the embedding model, falling back to all-MiniLM-L6-v2 if primary fails."""
    from sentence_transformers import SentenceTransformer

    for model_id in (PRIMARY_MODEL, FALLBACK_MODEL):
        try:
            if model_id == PRIMARY_MODEL:
                print(f"Loading model: {model_id}  (first run downloads ~600 MB — please wait)")
            else:
                print(f"  WARNING: Primary model ({PRIMARY_MODEL}) failed to load. Falling back to {FALLBACK_MODEL}.")
                print(f"Loading fallback model: {model_id}")
            # trust_remote_code required for jina-embeddings-v3
            # Force CPU device — jina-v3 OOMs on Apple Silicon MPS with large batches
            model = SentenceTransformer(model_id, trust_remote_code=True, device="cpu")
            print(f"  Model loaded: {model_id}")
            return model, model_id
        except Exception as exc:
            print(f"  Failed to load {model_id}: {exc}")
            if model_id == FALLBACK_MODEL:
                raise RuntimeError("Both primary and fallback models failed to load.") from exc

    raise RuntimeError("Unreachable")


# ---------------------------------------------------------------------------
# ChromaDB helpers
# ---------------------------------------------------------------------------

def get_collection(chroma_dir: Path = CHROMA_DIR):
    import chromadb

    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))
    collection = client.get_or_create_collection(
        name="mewgenics_wiki",
        metadata={"hnsw:space": "cosine"},
    )
    return collection


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_embed() -> tuple[int, int, int, str]:
    """Embed all content files. Returns (file_count, chunk_count, new_embeddings, model_id)."""
    md_files = sorted(CONTENT_DIR.rglob("*.md"))
    if not md_files:
        print("No markdown files found in content/")
        return 0, 0, 0, PRIMARY_MODEL

    print(f"Found {len(md_files)} markdown files")

    collection = get_collection()
    model, model_id = load_model()

    # Gather existing doc IDs to skip already-embedded chunks
    existing_ids: set[str] = set()
    # ChromaDB get() with no filters returns all; use peek for a quick check
    # then get full list
    result = collection.get(include=[])
    existing_ids = set(result["ids"])
    print(f"Existing embeddings in collection: {len(existing_ids)}")

    all_chunks: list[dict] = []
    for path in md_files:
        all_chunks.extend(chunk_file(path))

    # Filter to new-only
    new_chunks = [c for c in all_chunks if c["doc_id"] not in existing_ids]
    print(f"Total chunks: {len(all_chunks)}  |  New (to embed): {len(new_chunks)}")

    if not new_chunks:
        print("Nothing new to embed.")
        return len(md_files), len(all_chunks), 0, model_id

    import gc
    import torch

    # Embed one chunk at a time — jina-v3 is memory-hungry on Apple Silicon CPU;
    # single-item batches prevent OOM and the outer loop allows aggressive GC.
    batch_size = 1
    stored = 0
    texts = [c["text"] for c in new_chunks]
    ids = [c["doc_id"] for c in new_chunks]
    metadatas = [c["metadata"] for c in new_chunks]

    for batch_start in range(0, len(new_chunks), batch_size):
        batch_end = min(batch_start + batch_size, len(new_chunks))
        batch_texts = texts[batch_start:batch_end]
        batch_ids = ids[batch_start:batch_end]
        batch_meta = metadatas[batch_start:batch_end]

        with torch.no_grad():
            embeddings = model.encode(batch_texts, show_progress_bar=False).tolist()

        collection.add(
            ids=batch_ids,
            documents=batch_texts,
            embeddings=embeddings,
            metadatas=batch_meta,
        )
        stored += len(batch_texts)
        gc.collect()

        if stored % 10 == 0 or batch_end == len(new_chunks):
            print(f"  Embedded {stored}/{len(new_chunks)} chunks...", flush=True)

    return len(md_files), len(all_chunks), stored, model_id


# ---------------------------------------------------------------------------
# Sanity query
# ---------------------------------------------------------------------------

def run_test():
    """Run sanity queries against the populated collection."""
    print("\n--- Sanity queries ---")
    collection = get_collection()

    model, model_id = load_model()

    queries = [
        "warrior class abilities",
        "healing spells",
    ]

    for query in queries:
        print(f"\nQuery: \"{query}\"")
        vec = model.encode([query]).tolist()
        results = collection.query(
            query_embeddings=vec,
            n_results=3,
            include=["documents", "metadatas", "distances"],
        )
        for i, (doc, meta, dist) in enumerate(zip(
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        )):
            snippet = doc[:100].replace("\n", " ")
            print(f"  [{i+1}] {meta['source_path']}  §{meta['section']}  (dist={dist:.4f})")
            print(f"       {snippet!r}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Embed Mewgenics wiki content into ChromaDB")
    parser.add_argument("--test", action="store_true", help="Run sanity queries after embedding")
    args = parser.parse_args()

    # Authenticate with HuggingFace before loading any model
    token = _require_hf_token()
    _hf_login(token)

    file_count, chunk_count, new_stored, model_id = run_embed()

    print("\n=== Summary ===")
    print(f"  Model used      : {model_id}")
    print(f"  Files processed : {file_count}")
    print(f"  Total chunks    : {chunk_count}")
    print(f"  New embeddings  : {new_stored}")

    if args.test:
        run_test()


if __name__ == "__main__":
    main()
