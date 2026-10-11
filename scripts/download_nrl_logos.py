"""Download official NRL / Origin club crests into frontend/public/logos/nrl.

Mirrors ``download_logos.py`` (the AFL bootstrap): each club's crest is
the primary image declared on its Wikipedia article infobox (a non-free
fair-use file hosted by en.wikipedia) — resolved via the MediaWiki API
(wikitext → file name → imageinfo URL) and downloaded to
``frontend/public/logos/nrl/<key>.<ext>``.

Team keys match ``league_seeding`` feed nicknames (Broncos, Bulldogs, …)
so the frontend static map and the DB ``teams.logo_url`` seed both key
off the same names.  Origin teams use their representative-team
articles (Blues, Maroons).

Usage:
    cd backend && uv run python ../scripts/download_nrl_logos.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = {"User-Agent": "WhatIsMyTip/1.0 (contact@whatismytip.com)"}
OUT_DIR = Path(__file__).resolve().parent.parent / "frontend" / "public" / "logos" / "nrl"

#: feed nickname (league_seeding.TEAMS) → Wikipedia article title.
ARTICLES: dict[str, str] = {
    "Broncos": "Brisbane Broncos",
    "Bulldogs": "Canterbury-Bankstown Bulldogs",
    "Cowboys": "North Queensland Cowboys",
    "Dolphins": "Redcliffe Dolphins",
    "Dragons": "St. George Illawarra Dragons",
    "Eels": "Parramatta Eels",
    "Knights": "Newcastle Knights",
    "Panthers": "Penrith Panthers",
    "Rabbitohs": "South Sydney Rabbitohs",
    "Raiders": "Canberra Raiders",
    "Roosters": "Sydney Roosters",
    "Sea Eagles": "Manly Warringah Sea Eagles",
    "Sharks": "Cronulla-Sutherland Sharks",
    "Storm": "Melbourne Storm",
    "Titans": "Gold Coast Titans",
    "Warriors": "New Zealand Warriors",
    "Wests Tigers": "Wests Tigers",
    "Blues": "New South Wales rugby league team",
    "Maroons": "Queensland rugby league team",
}

IMAGE_PARAM = re.compile(
    r"^\|\s*(?:image|logo|crest|badge)[a-z_]*\s*=\s*"
    r"(?:\[\[\s*(?:Image|File):)?\s*"
    r"([A-Za-z0-9_\-. ]+?\.(?:svg|SVG|png|PNG|gif|jpg|jpeg))",
    re.I | re.M,
)
FALLBACK_FILE = re.compile(
    r"\[\[\s*(?:Image|File):([^\]|]+(?:logo|crest|badge)[^\]|]*)", re.I
)


def fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(request, timeout=30).read().decode(
        "utf-8", "replace"
    )


def fetch_json(url: str):
    return json.loads(fetch_text(url))


def wikitext_for(article: str) -> str:
    params = {
        "action": "query",
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
        "titles": article,
    }
    data = fetch_json(
        "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    )
    page = data["query"]["pages"][0]
    if "revisions" not in page:
        raise LookupError(f"no Wikipedia article: {article!r}")
    return page["revisions"][0]["slots"]["main"]["content"]


def crest_file_name(article: str, wikitext: str) -> str:
    match = IMAGE_PARAM.search(wikitext) or FALLBACK_FILE.search(
        wikitext[:6000]
    )
    if not match:
        raise LookupError(f"no crest file declared on {article!r}")
    return match.group(1).strip()


def file_url(file_name: str) -> tuple[str, int, int]:
    params = {
        "action": "query",
        "prop": "imageinfo",
        "iiprop": "url|size",
        "format": "json",
        "formatversion": "2",
        "titles": "File:" + file_name,
    }
    data = fetch_json(
        "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)
    )
    page = data["query"]["pages"][0]
    info = page.get("imageinfo", [{}])[0]
    url = info.get("url")
    if not url:
        raise LookupError(f"no imageinfo URL for File:{file_name}")
    return url, int(info.get("width") or 0), int(info.get("height") or 0)


def download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers=UA)
    raw = urllib.request.urlopen(request, timeout=60).read()
    target.write_bytes(raw)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    for key, article in ARTICLES.items():
        try:
            wikitext = wikitext_for(article)
            file_name = crest_file_name(article, wikitext)
            url, width, height = file_url(file_name)
            suffix = Path(urllib.parse.unquote(file_name)).suffix or ".png"
            target = OUT_DIR / f"{key.lower().replace(' ', '_')}{suffix}"
            download(url, target)
            print(
                f"OK  {key:12s} -> {target.name} "
                f"({target.stat().st_size:>7,} B, {width}x{height})"
            )
        except Exception as exc:  # noqa: BLE001 — report every club
            failures.append(key)
            print(f"FAIL {key:12s} {type(exc).__name__}: {exc}")
        time.sleep(0.5)  # politeness: Wikipedia is fine with modest rates
    if failures:
        print(f"\n{len(failures)} failed: {failures}")
        return 1
    print(f"\nAll {len(ARTICLES)} crests downloaded to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
