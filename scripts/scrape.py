"""
Mewgenics wiki scraper.

Fetches priority pages, converts to atomic markdown, downloads images
concurrently, and extracts large tables into SQLite.

Usage:
    uv run scripts/scrape.py
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import aiohttp
import requests
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

WIKI_BASE = "https://mewgenics.wiki.gg"
HEADERS = {"User-Agent": "MewgenicsRef/1.0"}
REQUEST_DELAY = 1.0  # seconds between page fetches
IMAGE_CONCURRENCY = 20

REPO_ROOT = Path(__file__).parent.parent
CONTENT_DIR = REPO_ROOT / "content"
DATA_DIR = REPO_ROOT / "data"
DB_PATH = DATA_DIR / "mewgenics.db"

# (url, output_path, section_dir, sqlite_table or None)
PAGES: list[tuple[str, Path, Path, str | None]] = [
    (
        f"{WIKI_BASE}/wiki/House",
        CONTENT_DIR / "house" / "index.md",
        CONTENT_DIR / "house",
        None,
    ),
    (
        f"{WIKI_BASE}/wiki/Classes",
        CONTENT_DIR / "classes" / "index.md",
        CONTENT_DIR / "classes",
        None,
    ),
    (
        f"{WIKI_BASE}/wiki/Collarless",
        CONTENT_DIR / "classes" / "collarless.md",
        CONTENT_DIR / "classes",
        None,
    ),
    (
        f"{WIKI_BASE}/wiki/Fighter",
        CONTENT_DIR / "classes" / "fighter.md",
        CONTENT_DIR / "classes",
        None,
    ),
    (
        f"{WIKI_BASE}/wiki/Hunter",
        CONTENT_DIR / "classes" / "hunter.md",
        CONTENT_DIR / "classes",
        None,
    ),
    (
        f"{WIKI_BASE}/wiki/Mage",
        CONTENT_DIR / "classes" / "mage.md",
        CONTENT_DIR / "classes",
        None,
    ),
    (
        f"{WIKI_BASE}/wiki/Stats",
        CONTENT_DIR / "stats" / "index.md",
        CONTENT_DIR / "stats",
        None,
    ),
    (
        f"{WIKI_BASE}/wiki/Mutations",
        CONTENT_DIR / "mutations" / "index.md",
        CONTENT_DIR / "mutations",
        "mutations",
    ),
    (
        f"{WIKI_BASE}/wiki/Disorders",
        CONTENT_DIR / "disorders" / "index.md",
        CONTENT_DIR / "disorders",
        "disorders",
    ),
    (
        f"{WIKI_BASE}/wiki/Abilities",
        CONTENT_DIR / "abilities" / "index.md",
        CONTENT_DIR / "abilities",
        "abilities",
    ),
]

# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS abilities (
            name        TEXT,
            class       TEXT,
            type        TEXT,
            description TEXT,
            attributes  TEXT,
            icon_path   TEXT
        );
        CREATE TABLE IF NOT EXISTS mutations (
            name        TEXT,
            description TEXT,
            extra       TEXT
        );
        CREATE TABLE IF NOT EXISTS disorders (
            name        TEXT,
            description TEXT,
            extra       TEXT
        );
        """
    )
    conn.commit()


def clear_table(conn: sqlite3.Connection, table: str) -> None:
    conn.execute(f"DELETE FROM {table}")  # noqa: S608
    conn.commit()


# ---------------------------------------------------------------------------
# Image URL helpers
# ---------------------------------------------------------------------------


def normalise_image_url(src: str) -> str | None:
    """
    Convert a possibly root-relative, thumbnail, or hash-suffixed image URL
    into the canonical full-size absolute URL. Returns None if the src is
    clearly not an image we want.
    """
    if not src or src.startswith("data:"):
        return None

    # Make absolute
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = WIKI_BASE + src

    # Strip ?hash cache-buster
    parsed = urlparse(src)
    clean = urlunparse(parsed._replace(query=""))

    # Thumbnail pattern: /images/thumb/Original.png/NNpx-Original.png
    # → /images/Original.png
    thumb_match = re.match(
        r"^(https?://[^/]+)/images/thumb/(.+?)/\d+px-(.+)$", clean
    )
    if thumb_match:
        host = thumb_match.group(1)
        full_name = thumb_match.group(2)  # e.g. "Subdir/Original.png"
        clean = f"{host}/images/{full_name}"

    return clean


def image_filename(url: str) -> str:
    """Extract the filename from an image URL."""
    return Path(urlparse(url).path).name


# ---------------------------------------------------------------------------
# HTML → Markdown conversion
# ---------------------------------------------------------------------------


def strip_chrome(soup: Tag) -> None:
    """Remove wiki chrome from within the content tag."""
    for selector in [".toc", "#catlinks", ".mw-editsection", "div.shuffle__filters", "style"]:
        for el in soup.select(selector):
            el.decompose()


def rewrite_image_srcs(soup: Tag, images_dir: Path) -> dict[str, str]:
    """
    Replace all img src attributes with local paths.
    Returns mapping of {absolute_url: local_path_str}.
    """
    url_to_local: dict[str, str] = {}
    for img in soup.find_all("img"):
        src = img.get("src", "")
        abs_url = normalise_image_url(src)
        if not abs_url:
            img.decompose()
            continue
        fname = image_filename(abs_url)
        local_path = images_dir / fname
        img["src"] = f"images/{fname}"
        url_to_local[abs_url] = str(local_path)
    return url_to_local


SQLITE_NOTE_TEMPLATE = """> **Data table:** This content is stored in the `{table}` SQLite table.
> Query with `query_db("{table}", filters={{}})`.
> Columns: {columns}
"""


def extract_abilities_table(
    soup: Tag,
    conn: sqlite3.Connection,
    images_dir: Path,
) -> tuple[dict[str, str], str]:
    """
    Extract abilities from shuffle__item rows into SQLite.

    Cell layout per row (observed from live HTML):
      cell[0]: icon image (<td class="si__image">)
      cell[1]: ability name
      cell[2]: class name (redundant with data-class attr)
      cell[3]: type string (redundant with data-type attr)
      cell[4]: description text
      cell[5]: attributes / cost info

    Returns (image_url_map, sqlite_note).
    """
    clear_table(conn, "abilities")
    rows = soup.select("tr.shuffle__item")
    image_url_map: dict[str, str] = {}
    records: list[tuple] = []

    for row in rows:
        cls = row.get("data-class", "")
        typ = row.get("data-type", "")

        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue

        # cell[0] = icon cell
        icon_path_str = ""
        icon_img = cells[0].find("img") if cells else None
        if icon_img:
            abs_url = normalise_image_url(icon_img.get("src", ""))
            if abs_url:
                fname = image_filename(abs_url)
                local = images_dir / fname
                image_url_map[abs_url] = str(local)
                icon_path_str = f"images/{fname}"

        # cell[1] = name
        name = cells[1].get_text(strip=True) if len(cells) > 1 else ""
        if not name or name.lower() in ("name", "ability"):
            continue

        # cell[4] = description, cell[5] = attributes
        description = cells[4].get_text(strip=True) if len(cells) > 4 else ""
        attributes = cells[5].get_text(strip=True) if len(cells) > 5 else ""

        records.append((name, cls, typ, description, attributes, icon_path_str))

    conn.executemany(
        "INSERT INTO abilities (name, class, type, description, attributes, icon_path) VALUES (?,?,?,?,?,?)",
        records,
    )
    conn.commit()

    columns = "name, class, type, description, attributes, icon_path"
    note = SQLITE_NOTE_TEMPLATE.format(table="abilities", columns=columns)
    return image_url_map, note


def extract_generic_table(
    soup: Tag,
    table_name: str,
    conn: sqlite3.Connection,
) -> str:
    """
    Extract the largest wikitable (by row count) into SQLite.
    For mutations and disorders the schema is (name, description, extra).

    Mutations table layout (observed from live HTML):
      col[0]: icon image — skip
      col[1]: mutation type (Body Mutation, etc.)
      col[2]: title / name
      col[3]: description (brief stat summary)
      col[4]: effects (long text)
      col[5]: id

    Disorders table layout (observed from live HTML):
      row[0]: merged header "Disorders" — skip
      row[1]: column headers "Name / Description"
      data rows:
        col[0]: icon image — skip
        col[1]: name
        col[2]: description

    Returns sqlite_note string, or "" if no suitable table found.
    """
    # Find the largest wikitable
    all_tables = soup.find_all("table", class_="wikitable")
    if not all_tables:
        return ""

    wikitable = max(all_tables, key=lambda t: len(t.find_all("tr")))
    row_count = len(wikitable.find_all("tr"))
    if row_count <= 50:
        return ""  # Not large enough to warrant SQLite

    clear_table(conn, table_name)

    if table_name == "mutations":
        # Row layout: icon | type | title | description | effects | id
        # "Common" mutations have no title (cell[2] is blank) — use id as name
        # Skip header row[0]
        records: list[tuple] = []
        for tr in wikitable.find_all("tr")[1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) < 3:
                continue
            # cell[2]=name(title), cell[1]=type, cell[3]=description, cell[4]=effects, cell[5]=id
            mut_type = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            name_val = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            # Skip column-header row
            if name_val.lower() in ("title", "name"):
                continue
            desc_val = cells[3].get_text(strip=True) if len(cells) > 3 else ""
            effects_val = cells[4].get_text(strip=True) if len(cells) > 4 else ""
            mut_id = cells[5].get_text(strip=True) if len(cells) > 5 else ""
            # For common mutations that lack a title, use the mutation id as the name
            if not name_val:
                name_val = mut_id if mut_id else mut_type
            extra = f"type={mut_type} | effects={effects_val} | id={mut_id}"
            records.append((name_val, desc_val, extra))
        conn.executemany(
            f"INSERT INTO {table_name} (name, description, extra) VALUES (?,?,?)",  # noqa: S608
            records,
        )
        conn.commit()
        columns_str = "name, description, extra (type | effects | id)"

    elif table_name == "disorders":
        # Row layout: header rows first, then data: icon | name | description
        records = []
        data_started = False
        for tr in wikitable.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            texts = [c.get_text(strip=True) for c in cells]
            # Skip merged header rows and column-header rows
            if not data_started:
                # Data rows have 3 cells with icon (empty text), name, description
                if len(cells) == 3 and texts[0] == "" and texts[1] not in ("Name", ""):
                    data_started = True
                elif len(cells) == 2 and texts[0] not in ("Name", "Disorders", ""):
                    data_started = True
                else:
                    continue
            if len(cells) >= 2:
                # Determine layout: if 3 cells, cell[0]=icon, cell[1]=name, cell[2]=desc
                # If 2 cells, cell[0]=name, cell[1]=desc
                if len(cells) >= 3 and cells[0].get_text(strip=True) == "":
                    name_val = cells[1].get_text(strip=True)
                    desc_val = cells[2].get_text(strip=True) if len(cells) > 2 else ""
                else:
                    name_val = cells[0].get_text(strip=True)
                    desc_val = cells[1].get_text(strip=True)
                if not name_val or name_val.lower() in ("name",):
                    continue
                records.append((name_val, desc_val, ""))
        conn.executemany(
            f"INSERT INTO {table_name} (name, description, extra) VALUES (?,?,?)",  # noqa: S608
            records,
        )
        conn.commit()
        columns_str = "name, description, extra"

    else:
        # Generic fallback: read header row, insert all data rows as name/description/extra
        all_rows = wikitable.find_all("tr")
        headers = [th.get_text(strip=True) for th in all_rows[0].find_all(["th", "td"])]
        records = []
        for tr in all_rows[1:]:
            cells = tr.find_all(["td", "th"])
            if not cells:
                continue
            vals = [c.get_text(strip=True) for c in cells]
            name_val = vals[0] if vals else ""
            desc_val = vals[1] if len(vals) > 1 else ""
            extra = " | ".join(vals[2:]) if len(vals) > 2 else ""
            records.append((name_val, desc_val, extra))
        conn.executemany(
            f"INSERT INTO {table_name} (name, description, extra) VALUES (?,?,?)",  # noqa: S608
            records,
        )
        conn.commit()
        columns_str = ", ".join(headers) if headers else "name, description, extra"

    return SQLITE_NOTE_TEMPLATE.format(table=table_name, columns=columns_str)


# ---------------------------------------------------------------------------
# Page processing
# ---------------------------------------------------------------------------


def fetch_page(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def process_page(
    url: str,
    output_path: Path,
    section_dir: Path,
    sqlite_table: str | None,
    conn: sqlite3.Connection,
) -> dict[str, str]:
    """
    Fetch a page, convert to markdown, extract tables if needed.
    Returns image_url_map {abs_url: local_path}.
    """
    print(f"Fetching: {url}")
    html = fetch_page(url)

    soup = BeautifulSoup(html, "lxml")
    content = soup.select_one("#mw-content-text .mw-parser-output")
    if not content:
        print(f"  WARNING: no content found for {url}")
        content = soup.find("body") or soup

    strip_chrome(content)

    images_dir = section_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    image_url_map: dict[str, str] = {}
    sqlite_note = ""

    # Extract large tables to SQLite BEFORE markdown conversion
    if sqlite_table == "abilities":
        extra_images, sqlite_note = extract_abilities_table(content, conn, images_dir)
        image_url_map.update(extra_images)
        # Replace the shuffle block with a plain <p> note
        for shuffle_div in content.select("div.shuffle"):
            note_tag = BeautifulSoup(
                f"<p>{sqlite_note}</p>", "lxml"
            ).find("p")
            if note_tag:
                shuffle_div.replace_with(note_tag)
    elif sqlite_table in ("mutations", "disorders"):
        sqlite_note = extract_generic_table(content, sqlite_table, conn)
        if sqlite_note:
            # Find and replace the largest wikitable (the one that was extracted)
            all_tables = content.find_all("table", class_="wikitable")
            if all_tables:
                largest = max(all_tables, key=lambda t: len(t.find_all("tr")))
                note_tag = BeautifulSoup(
                    f"<p>{sqlite_note}</p>", "lxml"
                ).find("p")
                if note_tag:
                    largest.replace_with(note_tag)

    # Rewrite image srcs to local paths
    img_map = rewrite_image_srcs(content, images_dir)
    image_url_map.update(img_map)

    # Convert to markdown
    content_html = str(content)
    markdown_text = md(content_html, heading_style="ATX", bullets="-")

    # Prepend page title
    title = url.rstrip("/").split("/")[-1].replace("_", " ")
    markdown_text = f"# {title}\n\n{markdown_text}"

    output_path.write_text(markdown_text, encoding="utf-8")
    print(f"  Written: {output_path} ({len(markdown_text)} chars)")

    return image_url_map


# ---------------------------------------------------------------------------
# Async image downloader
# ---------------------------------------------------------------------------


async def download_image(
    session: aiohttp.ClientSession,
    sem: asyncio.Semaphore,
    url: str,
    dest: Path,
    counter: list[int],
    total: int,
) -> None:
    if dest.exists():
        counter[0] += 1
        return
    async with sem:
        try:
            async with session.get(url, headers=HEADERS) as resp:
                if resp.status == 200:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(await resp.read())
                else:
                    print(f"  WARN image {resp.status}: {url}")
        except Exception as exc:
            print(f"  WARN image error ({exc}): {url}")
        finally:
            counter[0] += 1
            if counter[0] % 25 == 0:
                print(f"  Images: {counter[0]}/{total} downloaded")


async def download_all_images(image_url_map: dict[str, str]) -> None:
    """Download all images concurrently."""
    if not image_url_map:
        print("No images to download.")
        return

    total = len(image_url_map)
    print(f"\nDownloading {total} images (concurrency={IMAGE_CONCURRENCY})…")
    sem = asyncio.Semaphore(IMAGE_CONCURRENCY)
    counter = [0]

    connector = aiohttp.TCPConnector(limit=IMAGE_CONCURRENCY)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            asyncio.create_task(
                download_image(session, sem, url, Path(dest), counter, total)
            )
            for url, dest in image_url_map.items()
        ]
        await asyncio.gather(*tasks)

    print(f"  Images complete: {counter[0]}/{total}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONTENT_DIR.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    all_images: dict[str, str] = {}

    for i, (url, output_path, section_dir, sqlite_table) in enumerate(PAGES):
        if i > 0:
            time.sleep(REQUEST_DELAY)
        try:
            img_map = process_page(url, output_path, section_dir, sqlite_table, conn)
            all_images.update(img_map)
        except Exception as exc:
            print(f"  ERROR processing {url}: {exc}")
            import traceback
            traceback.print_exc()

    conn.close()

    # Batch async image download
    asyncio.run(download_all_images(all_images))

    print("\nDone.")


if __name__ == "__main__":
    main()
