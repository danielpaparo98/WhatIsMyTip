"""Unit tests for scrape_to_csv.py parsing and CSV helper functions.

Tests the metadata extraction, player stats parsing, and CSV I/O
without making any real HTTP requests.
"""

import csv
import os
import sys
import tempfile

import pytest

# Ensure backend-faas is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.scrape_to_csv import (
    _extract_match_metadata,
    _normalize_venue,
    _parse_player_stats,
    read_csv,
    write_csv,
)


# ---------------------------------------------------------------------------
# Fixtures — realistic AFL Tables HTML snippets
# ---------------------------------------------------------------------------


MATCH_PAGE_HTML = """<!DOCTYPE html>
<html>
<head>
<title>AFL Tables - Collingwood v Carlton - Sat, 15-Mar-2025 4:35 PM (3:35 PM) - Match Stats</title>
</head>
<body>
<p>Round 1, 2025 season. Collingwood 15.12.102 vs Carlton 10.8.68 at MCG, Sat 15-Mar-2025</p>

<table class="sortable">
<thead>
<tr><th colspan="25">Collingwood Match Statistics</th></tr>
<tr>
<th>#</th><th>Player</th><th>KI</th><th>MK</th><th>HB</th><th>DI</th>
<th>GL</th><th>BH</th><th>TK</th><th>HO</th><th>FF</th><th>FA</th>
<th>...</th>
</tr>
</thead>
<tbody>
<tr><td>1</td><td><a href="players/01A/Daicos_Nick.html">Daicos, Nick</a></td>
<td>25</td><td>8</td><td>15</td><td>40</td><td>2</td><td>1</td><td>5</td><td>0</td><td>3</td><td>2</td>
<td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
<tr><td>2</td><td><a href="players/01B/Moore_Darcy.html">Moore, Darcy</a></td>
<td>12</td><td>7</td><td>8</td><td>20</td><td>0</td><td>0</td><td>3</td><td>1</td><td>1</td><td>0</td>
<td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
<tr><td>3</td><td>Totals</td>
<td>200</td><td>80</td><td>150</td><td>350</td><td>15</td><td>12</td><td>60</td><td>30</td><td>15</td><td>15</td>
<td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
</tbody>
</table>

<table class="sortable">
<thead>
<tr><th colspan="25">Carlton Match Statistics</th></tr>
<tr>
<th>#</th><th>Player</th><th>KI</th><th>MK</th><th>HB</th><th>DI</th>
<th>GL</th><th>BH</th><th>TK</th><th>HO</th><th>FF</th><th>FA</th>
<th>...</th>
</tr>
</thead>
<tbody>
<tr><td>4</td><td><a href="players/02A/Cripps_Patrick.html">Cripps, Patrick</a></td>
<td>20</td><td>5</td><td>12</td><td>32</td><td>1</td><td>0</td><td>8</td><td>0</td><td>2</td><td>3</td>
<td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
<tr><td>5</td><td><a href="players/02B/Walsh_Sam.html">Walsh, Sam</a></td>
<td>18</td><td>6</td><td>14</td><td>32</td><td>0</td><td>1</td><td>4</td><td>0</td><td>1</td><td>2</td>
<td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
<tr><td>6</td><td>Totals</td>
<td>180</td><td>70</td><td>130</td><td>310</td><td>10</td><td>8</td><td>50</td><td>25</td><td>12</td><td>12</td>
<td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
</tbody>
</table>

<table class="sortable">
<thead><tr><th>Advanced Home</th></tr></thead>
<tbody><tr><td>ignored</td></tr></tbody>
</table>

<table class="sortable">
<thead><tr><th>Advanced Away</th></tr></thead>
<tbody><tr><td>ignored</td></tr></tbody>
</table>
</body>
</html>
"""

MATCH_PAGE_VENUE_HEADER_HTML = """<!DOCTYPE html>
<html>
<head>
<title>AFL Tables - Sydney v Carlton - Thu, 5-Mar-2026 7:30 PM (6:30 PM) - Match Stats</title>
</head>
<body>
<pre>
Venue: S.C.G.  Date: Thu 5-Mar-2026  Attendance: 35,221  →
</pre>

<table class="sortable">
<thead>
<tr><th colspan="25">Sydney Match Statistics</th></tr>
<tr>
<th>#</th><th>Player</th><th>KI</th><th>MK</th><th>HB</th><th>DI</th>
<th>GL</th><th>BH</th><th>TK</th><th>HO</th><th>FF</th><th>FA</th>
<th>...</th>
</tr>
</thead>
<tbody>
<tr><td>1</td><td><a href="players/01A/Mills_Callum.html">Mills, Callum</a></td>
<td>20</td><td>6</td><td>10</td><td>30</td><td>1</td><td>0</td><td>4</td><td>0</td><td>2</td><td>1</td>
<td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
</tbody>
</table>

<table class="sortable">
<thead>
<tr><th colspan="25">Carlton Match Statistics</th></tr>
<tr>
<th>#</th><th>Player</th><th>KI</th><th>MK</th><th>HB</th><th>DI</th>
<th>GL</th><th>BH</th><th>TK</th><th>HO</th><th>FF</th><th>FA</th>
<th>...</th>
</tr>
</thead>
<tbody>
<tr><td>2</td><td><a href="players/02A/Cripps_Patrick.html">Cripps, Patrick</a></td>
<td>18</td><td>5</td><td>12</td><td>30</td><td>0</td><td>1</td><td>6</td><td>0</td><td>3</td><td>2</td>
<td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
</tbody>
</table>
</body>
</html>
"""

MATCH_PAGE_VENUE_LINK_HTML = """<!DOCTYPE html>
<html>
<head>
<title>AFL Tables - Geelong v Hawthorn - Sat, 8-Mar-2025 1:45 PM (12:45 PM) - Match Stats</title>
</head>
<body>
<p>Round 2, 2025.</p>
<p>Some info here with <a href="../../../venues/kp.html">Kardinia Park</a> as the ground.</p>

<table class="sortable">
<thead><tr><th>Geelong Match Statistics</th></tr></thead>
<tbody>
<tr><td>1</td><td><a>Player, Test</a></td>
<td>5</td><td>3</td><td>2</td><td>7</td><td>1</td><td>0</td><td>1</td><td>0</td><td>0</td><td>1</td>
<td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
</tbody>
</table>
<table class="sortable">
<thead><tr><th>Hawthorn Match Statistics</th></tr></thead>
<tbody>
<tr><td>2</td><td><a>Other, Player</a></td>
<td>8</td><td>4</td><td>6</td><td>14</td><td>2</td><td>1</td><td>2</td><td>3</td><td>1</td><td>0</td>
<td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
</tbody>
</table>
</body>
</html>
"""

MATCH_PAGE_NO_VENUE_HTML = """<!DOCTYPE html>
<html>
<head>
<title>AFL Tables - Brisbane v Gold Coast - Sun, 23-Mar-2025 3:20 PM (2:20 PM) - Match Stats</title>
</head>
<body>
<p>Some text without venue info.</p>
<table class="sortable">
<thead><tr><th>Brisbane Match Statistics</th></tr></thead>
<tbody>
<tr><td>1</td><td><a>Player, Test</a></td>
<td>5</td><td>3</td><td>2</td><td>7</td><td>1</td><td>0</td><td>1</td><td>0</td><td>0</td><td>1</td>
<td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
</tbody>
</table>
<table class="sortable">
<thead><tr><th>Gold Coast Match Statistics</th></tr></thead>
<tbody>
<tr><td>2</td><td><a>Other, Player</a></td>
<td>8</td><td>4</td><td>6</td><td>14</td><td>2</td><td>1</td><td>2</td><td>3</td><td>1</td><td>0</td>
<td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
</tbody>
</table>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Tests for CSV helpers
# ---------------------------------------------------------------------------


class TestWriteCsv:
    """Tests for write_csv()."""

    def test_writes_rows_to_csv(self, tmp_path):
        rows = [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]
        filepath = str(tmp_path / "test.csv")
        write_csv(filepath, rows)

        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            result = list(reader)

        assert len(result) == 2
        assert result[0]["name"] == "Alice"
        assert result[1]["age"] == "25"

    def test_empty_rows_does_not_create_file(self, tmp_path):
        filepath = str(tmp_path / "empty.csv")
        write_csv(filepath, [])

        assert not os.path.exists(filepath)

    def test_custom_fieldnames(self, tmp_path):
        rows = [{"name": "Alice", "age": 30, "extra": "hidden"}]
        filepath = str(tmp_path / "test.csv")
        write_csv(filepath, rows, fieldnames=["name", "age"])

        with open(filepath, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            result = list(reader)

        assert "extra" not in result[0]
        assert result[0]["name"] == "Alice"


class TestReadCsv:
    """Tests for read_csv()."""

    def test_reads_existing_csv(self, tmp_path):
        filepath = str(tmp_path / "test.csv")
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["name", "value"])
            writer.writeheader()
            writer.writerow({"name": "test", "value": "42"})

        result = read_csv(filepath)
        assert len(result) == 1
        assert result[0]["name"] == "test"

    def test_returns_empty_for_missing_file(self, tmp_path):
        result = read_csv(str(tmp_path / "nonexistent.csv"))
        assert result == []


# ---------------------------------------------------------------------------
# Tests for _extract_match_metadata
# ---------------------------------------------------------------------------


class TestExtractMatchMetadata:
    """Tests for _extract_match_metadata()."""

    def test_extracts_teams_and_date_from_title(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(MATCH_PAGE_HTML, "lxml")
        result = _extract_match_metadata(soup)

        assert result["home_team"] == "Collingwood"
        assert result["away_team"] == "Carlton"
        assert result["match_date"] == "2025-03-15"

    def test_extracts_venue_from_page_text(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(MATCH_PAGE_HTML, "lxml")
        result = _extract_match_metadata(soup)

        assert result["venue"] == "MCG"

    def test_missing_venue_returns_none(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(MATCH_PAGE_NO_VENUE_HTML, "lxml")
        result = _extract_match_metadata(soup)

        assert result["venue"] is None
        assert result["home_team"] == "Brisbane"
        assert result["away_team"] == "Gold Coast"
        assert result["match_date"] == "2025-03-23"

    def test_malformed_title_returns_none_fields(self):
        from bs4 import BeautifulSoup

        html = "<html><head><title>Some random title</title></head><body></body></html>"
        soup = BeautifulSoup(html, "lxml")
        result = _extract_match_metadata(soup)

        assert result["home_team"] is None
        assert result["away_team"] is None
        assert result["match_date"] is None

    def test_extracts_various_venues(self):
        from bs4 import BeautifulSoup

        for venue in ["MCG", "Marvel Stadium", "Adelaide Oval", "Optus Stadium",
                       "Gabba", "SCG", "GMHBA Stadium", "People First Stadium",
                       "UTAS Stadium", "Manuka Oval"]:
            html = f'<html><head><title>AFL Tables - TeamA v TeamB - Sat, 1-Mar-2025 3:00 PM - Match Stats</title></head><body><p>at {venue}, Sat 1-Mar-2025</p></body></html>'
            soup = BeautifulSoup(html, "lxml")
            result = _extract_match_metadata(soup)
            assert result["venue"] == venue, f"Expected {venue}, got {result['venue']}"

    def test_extracts_venue_from_venue_header_pattern(self):
        """Method 1: 'Venue: XXX' pattern from real AFL Tables pages."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(MATCH_PAGE_VENUE_HEADER_HTML, "lxml")
        result = _extract_match_metadata(soup)

        assert result["venue"] == "SCG"  # S.C.G. → SCG via _normalize_venue
        assert result["home_team"] == "Sydney"
        assert result["away_team"] == "Carlton"
        assert result["match_date"] == "2026-03-05"

    def test_extracts_venue_from_venue_link(self):
        """Method 2: venue link <a href='venues/...'>XXX</a>."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(MATCH_PAGE_VENUE_LINK_HTML, "lxml")
        result = _extract_match_metadata(soup)

        assert result["venue"] == "GMHBA Stadium"  # Kardinia Park → GMHBA Stadium
        assert result["home_team"] == "Geelong"
        assert result["away_team"] == "Hawthorn"


# ---------------------------------------------------------------------------
# Tests for _normalize_venue
# ---------------------------------------------------------------------------


class TestNormalizeVenue:
    """Tests for _normalize_venue()."""

    def test_dotted_abbreviations(self):
        assert _normalize_venue("S.C.G.") == "SCG"
        assert _normalize_venue("M.C.G.") == "MCG"

    def test_canonical_names_passthrough(self):
        for name in ["MCG", "Marvel Stadium", "Adelaide Oval", "Optus Stadium",
                      "Gabba", "SCG", "GMHBA Stadium", "People First Stadium",
                      "UTAS Stadium", "Manuka Oval"]:
            assert _normalize_venue(name) == name

    def test_historical_aliases(self):
        assert _normalize_venue("Etihad Stadium") == "Marvel Stadium"
        assert _normalize_venue("Docklands Stadium") == "Marvel Stadium"
        assert _normalize_venue("Perth Stadium") == "Optus Stadium"
        assert _normalize_venue("Metricon Stadium") == "People First Stadium"
        assert _normalize_venue("Carrara") == "People First Stadium"
        assert _normalize_venue("Kardinia Park") == "GMHBA Stadium"
        assert _normalize_venue("York Park") == "UTAS Stadium"
        assert _normalize_venue("Aurora Stadium") == "UTAS Stadium"

    def test_unknown_venue_returns_stripped_input(self):
        assert _normalize_venue("Some Unknown Ground") == "Some Unknown Ground"

    def test_strips_whitespace(self):
        assert _normalize_venue("  MCG  ") == "MCG"

    def test_strips_arrow_character(self):
        """AFL Tables pages sometimes have → after the venue name."""
        assert _normalize_venue("M.C.G.\u2192") == "MCG"


# ---------------------------------------------------------------------------
# Tests for _parse_player_stats
# ---------------------------------------------------------------------------


class TestParsePlayerStats:
    """Tests for _parse_player_stats()."""

    def test_parses_home_and_away_players(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(MATCH_PAGE_HTML, "lxml")
        result = _parse_player_stats(soup)

        assert len(result["home_players"]) == 2  # Excludes Totals row
        assert len(result["away_players"]) == 2

    def test_player_data_fields(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(MATCH_PAGE_HTML, "lxml")
        result = _parse_player_stats(soup)

        daicos = result["home_players"][0]
        assert daicos["name"] == "Daicos, Nick"
        assert daicos["kicks"] == 25
        assert daicos["marks"] == 8
        assert daicos["handballs"] == 15
        assert daicos["disposals"] == 40
        assert daicos["goals"] == 2
        assert daicos["behinds"] == 1
        assert daicos["tackles"] == 5
        assert daicos["hitouts"] == 0
        assert daicos["frees_for"] == 3
        assert daicos["frees_against"] == 2

    def test_excludes_totals_row(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(MATCH_PAGE_HTML, "lxml")
        result = _parse_player_stats(soup)

        names = [p["name"] for p in result["home_players"]]
        assert "Totals" not in names

    def test_away_team_players(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(MATCH_PAGE_HTML, "lxml")
        result = _parse_player_stats(soup)

        cripps = result["away_players"][0]
        assert cripps["name"] == "Cripps, Patrick"
        assert cripps["disposals"] == 32

    def test_only_uses_first_two_tables(self):
        """Should only parse first 2 sortable tables (basic stats)."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(MATCH_PAGE_HTML, "lxml")
        result = _parse_player_stats(soup)

        # 4 tables in HTML, but only first 2 should be parsed
        assert len(result["home_players"]) == 2
        assert len(result["away_players"]) == 2

    def test_empty_page_returns_empty(self):
        from bs4 import BeautifulSoup

        soup = BeautifulSoup("<html><body></body></html>", "lxml")
        result = _parse_player_stats(soup)

        assert result["home_players"] == []
        assert result["away_players"] == []

    def test_player_name_from_cell_text_when_no_anchor(self):
        """Player name should work even without an anchor tag."""
        from bs4 import BeautifulSoup

        html = """<table class="sortable">
        <thead><tr><th>#</th><th>Player</th><th>KI</th><th>MK</th><th>HB</th><th>DI</th>
        <th>GL</th><th>BH</th><th>TK</th><th>HO</th><th>FF</th><th>FA</th>
        <th>a</th><th>b</th><th>c</th><th>d</th><th>e</th><th>f</th><th>g</th>
        <th>h</th><th>i</th><th>j</th><th>k</th><th>l</th><th>m</th></tr></thead>
        <tbody>
        <tr><td>1</td><td>NoLink, Player</td>
        <td>5</td><td>3</td><td>2</td><td>7</td><td>0</td><td>0</td><td>1</td><td>0</td><td>0</td><td>0</td>
        <td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td>
        <td>0</td><td>0</td><td>0</td><td>0</td><td>0</td></tr>
        </tbody></table>"""
        soup = BeautifulSoup(html, "lxml")
        result = _parse_player_stats(soup)

        assert len(result["home_players"]) == 1
        assert result["home_players"][0]["name"] == "NoLink, Player"


# ---------------------------------------------------------------------------
# Tests for _parse_player_stats column mapping
# ---------------------------------------------------------------------------


class TestColumnMapping:
    """Verify the column index mapping matches real AFL Tables HTML."""

    def test_correct_column_indices(self):
        """Real AFL Tables has: #, Player, KI, MK, HB, DI, GL, BH, TK, HO, FF, FA."""
        from bs4 import BeautifulSoup

        # Build a minimal HTML with known values at each column
        cells = [
            "99",           # 0: jumper #
            "Test, Player", # 1: Player
            "10",           # 2: KI (kicks)
            "5",            # 3: MK (marks)
            "8",            # 4: HB (handballs)
            "18",           # 5: DI (disposals)
            "3",            # 6: GL (goals)
            "1",            # 7: BH (behinds)
            "4",            # 8: TK (tackles)
            "2",            # 9: HO (hitouts)
            "1",            # 10: FF (frees for)
            "2",            # 11: FA (frees against)
        ]
        # Pad to 25 columns
        cells += ["0"] * 13

        cell_html = "".join(f"<td>{c}</td>" for c in cells)
        html = f"""<table class="sortable">
        <thead><tr><th>#</th><th>Player</th></tr></thead>
        <tbody><tr>{cell_html}</tr></tbody>
        </table>"""

        soup = BeautifulSoup(html, "lxml")
        result = _parse_player_stats(soup)

        assert len(result["home_players"]) == 1
        player = result["home_players"][0]
        assert player["name"] == "Test, Player"
        assert player["kicks"] == 10
        assert player["marks"] == 5
        assert player["handballs"] == 8
        assert player["disposals"] == 18
        assert player["goals"] == 3
        assert player["behinds"] == 1
        assert player["tackles"] == 4
        assert player["hitouts"] == 2
        assert player["frees_for"] == 1
        assert player["frees_against"] == 2
