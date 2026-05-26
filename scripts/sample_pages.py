"""Fetch 3 Mewgenics wiki pages and save raw HTML to _samples/raw/."""

from pathlib import Path

import requests

PAGES = {
    "house": "https://mewgenics.wiki.gg/wiki/House",
    "classes": "https://mewgenics.wiki.gg/wiki/Classes",
    "abilities": "https://mewgenics.wiki.gg/wiki/Abilities",
}

OUT_DIR = Path(__file__).parent.parent / "_samples" / "raw"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; MewgenicsRefBot/0.1; research scraper)"
    )
}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update(HEADERS)

    for name, url in PAGES.items():
        out_path = OUT_DIR / f"{name}.html"
        print(f"Fetching {url} ...", flush=True)
        resp = session.get(url, timeout=30)
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
        size_kb = out_path.stat().st_size / 1024
        print(f"  -> {out_path.name}  ({size_kb:.1f} KB)")

    print("\nDone.")


if __name__ == "__main__":
    main()
