"""Download official club crests for the state leagues into
frontend/public/logos/{league}/ and regenerate
frontend/lib/leagueCrests.ts.

Per league (season defaults to the current one):
  1. Club names come from the league's OWN platform via its
     FeedProvider fixtures (source of truth): Sportix (wafl/waflw),
     isports (qafl/qaflw), AFL v2 (vfl/vflw/sanfl/aflw), PlayHQ
     (nwfl/sfl).
  2. Crest per club — first match wins:
       a. platform metadata (Sportix / isports ``get_team_metadata``);
       b. Wikipedia — search "{name} Football Club", take the article's
          infobox crest (non-free fair-use, en.wikipedia);
     misses are REPORTED, never guessed.
  3. Writes {league}/{slug}.{ext} and regenerates the typed crest
     module the frontend imports (prerender-safe static data).

Usage:
    cd backend && uv run python ../scripts/download_league_logos.py \
        [--only wafl,sanfl] [--season 2026]
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

UA = {"User-Agent": "WhatIsMyTip/1.0 (contact@whatismytip.com)"}
REPO = Path(__file__).resolve().parent.parent
PUBLIC = REPO / "frontend" / "public" / "logos"
CRESTS_MODULE = REPO / "frontend" / "lib" / "leagueCrests.ts"
WIKI_API = "https://en.wikipedia.org/w/api.php"


# ---------------------------------------------------------------------------
# HTTP + Wikipedia helpers (pipeline proven by download_nrl_logos.py)
# ---------------------------------------------------------------------------


def _http_json(url: str) -> Any:
    request = urllib.request.Request(url, headers=UA)
    return json.loads(urllib.request.urlopen(request, timeout=30).read())


def _http_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(request, timeout=60).read()


#: Platform feed names that don't search well as-is → the club's
#: Wikipedia article name.
SEARCH_ALIASES: Dict[str, str] = {
    "GWS GIANTS": "Greater Western Sydney Giants",
    "Gold Coast SUNS": "Gold Coast Football Club",
    "Footscray Bulldogs": "Western Bulldogs",
    "Southport Sharks": "Southport Football Club",
    "North Melbourne Werribee": "North Melbourne Football Club",
}

#: PlayHQ squad-suffix noise ("Brighton  Senior Men") — the article is
#: about the CLUB.
SUFFIX_RE = re.compile(
    r"\s+(senior\s+men|senior\s+women|reserves|womens|women)$", re.I
)


def search_name(club_name: str) -> str:
    name = SEARCH_ALIASES.get(club_name, club_name)
    name = re.sub(r"\s+", " ", name).strip()
    name = SUFFIX_RE.sub("", name)
    return name


def wiki_find_crest(club_name: str) -> Optional[str]:
    """Search Wikipedia for the club and return its infobox crest URL."""
    base = search_name(club_name)
    for query in (f"{base} Football Club", f"{base} FC", base):
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": "5",
            "format": "json",
            "formatversion": "2",
        }
        data = _http_json(f"{WIKI_API}?{urllib.parse.urlencode(params)}")
        for hit in data.get("query", {}).get("search", []):
            title = hit["title"]
            lowered = title.lower()
            base_lower = base.lower()
            # Accept club articles whose title carries a football marker
            # OR simply starts with the club name — several AFL clubs'
            # articles are titled without "Football Club" ("Western
            # Bulldogs", "Greater Western Sydney Giants").
            if (
                "football club" in lowered
                or "football team" in lowered
                or lowered == base_lower
                or lowered.startswith(base_lower)
            ):
                crest = _wiki_infobox_crest(title)
                if crest:
                    return crest
        time.sleep(0.3)
    return None


def _wiki_infobox_crest(article_title: str) -> Optional[str]:
    params = {
        "action": "query",
        "prop": "revisions",
        "rvprop": "content",
        "rvslots": "main",
        "format": "json",
        "formatversion": "2",
        "redirects": "1",
        "titles": article_title,
    }
    data = _http_json(f"{WIKI_API}?{urllib.parse.urlencode(params)}")
    page = data["query"]["pages"][0]
    if "revisions" not in page:
        return None
    wikitext = page["revisions"][0]["slots"]["main"]["content"]
    # The infobox always precedes the first section heading.  Scanning
    # that region (instead of brace-matching the Infobox template) is
    # immune to nested {{templates}} inside the infobox, which truncate
    # any regex over its braces.
    block = wikitext.split("\n==")[0]
    param = re.search(
        r"^\|\s*(?:image|logo|crest|badge)[a-z_]*\s*=\s*"
        r"(?:\[\[\s*(?:Image|File):)?\s*"
        # Filenames carry parentheses, en-dashes and spaces
        # ("Collingwood Football Club Logo (2017–present).svg") —
        # stop at wikilink/pipe/equals/newline boundaries instead of
        # whitelisting characters.
        r"([^\[\]|=\n]+?\.(?:svg|SVG|png|PNG|gif|jpg|jpeg))",
        block,
        re.I | re.M,
    )
    if not param:
        return None
    file_name = param.group(1).strip()
    info = _http_json(
        f"{WIKI_API}?{urllib.parse.urlencode({
            'action': 'query',
            'prop': 'imageinfo',
            'iiprop': 'url',
            'format': 'json',
            'formatversion': '2',
            'titles': 'File:' + file_name,
        })}"
    )
    return info["query"]["pages"][0].get("imageinfo", [{}])[0].get("url")


# ---------------------------------------------------------------------------
# Per-league collection — names + platform logos via FeedProviders
# ---------------------------------------------------------------------------


async def collect(league_key: str, season: int) -> Tuple[List[str], Dict[str, str]]:
    """Club names (from fixtures) + platform logo URLs (if the provider
    exposes crest metadata)."""
    from packages.shared.ingestion.state_leagues import get_league

    provider = get_league(league_key).provider_factory()
    fixtures = await provider.get_fixtures(season)
    names: set = set()
    for fixture in fixtures:
        if fixture.home_participant:
            names.add(fixture.home_participant)
        if fixture.away_participant:
            names.add(fixture.away_participant)
    logos: Dict[str, str] = {}
    getter = getattr(provider, "get_team_metadata", None)
    if callable(getter):
        try:
            metadata = await getter(season)
            for name, meta in metadata.items():
                url = (meta or {}).get("logo_url")
                if url:
                    logos[name] = url
        except Exception as exc:  # noqa: BLE001 — crests are best-effort
            print(f"  {league_key}: platform metadata failed: {exc}")
    return sorted(names), logos


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def crest_url_for(name: str, platform_logos: Dict[str, str]) -> Optional[str]:
    if name in platform_logos:
        return platform_logos[name]
    lowered = {k.lower(): v for k, v in platform_logos.items()}
    return lowered.get(name.lower())


def run_league(league_key: str, season: int) -> Tuple[Dict[str, str], List[str]]:
    """Returns (club→path mapping, failures)."""
    names, platform_logos = asyncio.run(collect(league_key, season))
    print(f"== {league_key}: {len(names)} clubs, {len(platform_logos)} platform crests")
    out_dir = PUBLIC / league_key
    out_dir.mkdir(parents=True, exist_ok=True)

    mapping: Dict[str, str] = {}
    failures: List[str] = []
    for name in names:
        slug = slugify(name)
        existing = sorted(out_dir.glob(f"{slug}.*"))
        url = crest_url_for(name, platform_logos)
        try:
            if url:
                raw = _http_bytes(url)
                ext = ".png" if url.lower().endswith(".png") else Path(url).suffix or ".png"
                target = out_dir / f"{slug}{ext}"
                target.write_bytes(raw)
            elif existing:
                target = existing[0]  # already downloaded — keep
            else:
                url = wiki_find_crest(name)
                if not url:
                    raise LookupError("no crest source")
                raw = _http_bytes(url)
                ext = Path(urllib.parse.unquote(url.split("?")[0])).suffix or ".png"
                target = out_dir / f"{slug}{ext}"
                target.write_bytes(raw)
            mapping[name] = f"/logos/{league_key}/{target.name}"
            print(f"  OK  {name} -> {target.name}")
        except Exception as exc:  # noqa: BLE001 — report, never guess
            failures.append(name)
            print(f"  FAIL {name}: {type(exc).__name__} {exc}")
        time.sleep(0.4)
    return mapping, failures


def regenerate_module(all_mappings: Dict[str, Dict[str, str]]) -> None:
    """Write frontend/lib/leagueCrests.ts — a committed, prerender-safe
    static module.  Merges with the existing module so a ``--only``
    run never drops previously generated leagues."""
    for league, mapping in all_mappings.items():
        if league not in all_mappings:
            continue
    existing = parse_existing_module()
    merged: Dict[str, Dict[str, str]] = {**existing}
    for league, mapping in all_mappings.items():
        merged.setdefault(league, {}).update(mapping)

    lines = [
        "// GENERATED by scripts/download_league_logos.py — do not edit.",
        "// Official club crests per league (platform metadata + Wikipedia",
        "// infobox images; see the script for sources).  Keys are the",
        "// platform's club names; values are public asset paths.",
        "export const LEAGUE_CRESTS: Record<string, Record<string, string>> = {",
    ]
    for league in sorted(merged):
        lines.append(f"  {league}: {{")
        for club in sorted(merged[league]):
            lines.append(f"    '{club}': '{merged[league][club]}',")
        lines.append("  },")
    lines.append("}")
    lines.append("")
    CRESTS_MODULE.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {CRESTS_MODULE}")


def parse_existing_module() -> Dict[str, Dict[str, str]]:
    if not CRESTS_MODULE.is_file():
        return {}
    text = CRESTS_MODULE.read_text(encoding="utf-8")
    result: Dict[str, Dict[str, str]] = {}
    for league_match in re.finditer(r"  (\w+): \{(.*?)\n  \},", text, re.S):
        league, body = league_match.group(1), league_match.group(2)
        result.setdefault(league, {})
        for quoted, plain, value in re.findall(
            r"(?:'([^']+)'|([A-Za-z][\w ]*?))\s*:\s*'([^']+)'", body
        ):
            result[league][quoted or plain] = value
    return result


def main(argv: List[str]) -> int:
    only: Optional[set] = None
    season = 2026
    args = list(argv)
    if "--only" in args:
        idx = args.index("--only")
        only = set(args[idx + 1].split(","))
        del args[idx : idx + 2]
    if "--season" in args:
        idx = args.index("--season")
        season = int(args[idx + 1])
        del args[idx : idx + 2]

    leagues = sorted(only) if only else ["wafl", "waflw", "qafl", "qaflw", "vfl", "vflw", "sanfl", "nwfl", "sfl"]

    all_mappings: Dict[str, Dict[str, str]] = {}
    total_failures: List[str] = []
    for league_key in leagues:
        mapping, failures = run_league(league_key, season)
        all_mappings[league_key] = mapping
        total_failures.extend(f"{league_key}:{club}" for club in failures)

    regenerate_module(all_mappings)
    if total_failures:
        print(f"\n{len(total_failures)} clubs without crests: {total_failures}")
        return 1
    print("\nAll clubs covered.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
