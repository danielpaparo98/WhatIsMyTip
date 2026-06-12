"""Unit tests for load_csv_to_db.py helper functions.

Focus: timezone-aware date conversion, team name matching, and date matching logic.
"""

import os
import sys
from datetime import date, datetime, timezone

# Ensure backend-faas is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.load_csv_to_db import (
    _canonical_team,
    _date_match,
    _to_aus_date,
    read_csv,
)

try:
    from zoneinfo import ZoneInfo  # noqa: F401
except ImportError:
    pass  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Tests for _to_aus_date
# ---------------------------------------------------------------------------


class TestToAusDate:
    """Tests for _to_aus_date() — UTC datetime → Australian local date."""

    def test_none_returns_none(self):
        assert _to_aus_date(None) is None

    def test_utc_morning_is_same_aus_date(self):
        """A game at 08:30 UTC = 19:30 AEDT — same calendar date (Mar 5)."""
        dt = datetime(2026, 3, 5, 8, 30, 0, tzinfo=timezone.utc)
        assert _to_aus_date(dt) == date(2026, 3, 5)

    def test_utc_afternoon_is_same_aus_date(self):
        """A game at 03:45 UTC = 14:45 AEDT — same calendar date."""
        dt = datetime(2026, 3, 12, 3, 45, 0, tzinfo=timezone.utc)
        assert _to_aus_date(dt) == date(2026, 3, 12)

    def test_utc_evening_is_next_day_aus(self):
        """A game at 22:00 UTC on Mar 5 = 09:00 AEDT on Mar 6 — same date.
        Actually 22:00 UTC = 09:00 AEDT next day — different date!"""
        dt = datetime(2026, 3, 5, 22, 0, 0, tzinfo=timezone.utc)
        # UTC+11: Mar 5 22:00 + 11h = Mar 6 09:00 AEDT
        assert _to_aus_date(dt) == date(2026, 3, 6)

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetime (no tzinfo) should be treated as UTC."""
        dt = datetime(2026, 3, 5, 8, 30, 0)  # naive
        assert _to_aus_date(dt) == date(2026, 3, 5)

    def test_naive_datetime_evening_shift(self):
        """Naive datetime at 22:00 treated as UTC → next day in AEDT."""
        dt = datetime(2026, 3, 5, 22, 0, 0)  # naive
        assert _to_aus_date(dt) == date(2026, 3, 6)

    def test_date_object_passthrough(self):
        """A plain date object should be returned as-is."""
        d = date(2026, 3, 5)
        assert _to_aus_date(d) == date(2026, 3, 5)

    def test_aedt_summer_time(self):
        """During AEDT (UTC+11): 06:00 UTC = 17:00 AEDT same day."""
        dt = datetime(2026, 1, 15, 6, 0, 0, tzinfo=timezone.utc)
        assert _to_aus_date(dt) == date(2026, 1, 15)

    def test_aest_winter_time(self):
        """During AEST (UTC+10): 06:00 UTC = 16:00 AEST same day."""
        # July is AEST (no daylight saving)
        dt = datetime(2026, 7, 15, 6, 0, 0, tzinfo=timezone.utc)
        assert _to_aus_date(dt) == date(2026, 7, 15)

    def test_midnight_utc_is_11am_aedt(self):
        """Midnight UTC = 11:00 AEDT same day."""
        dt = datetime(2026, 3, 15, 0, 0, 0, tzinfo=timezone.utc)
        assert _to_aus_date(dt) == date(2026, 3, 15)


# ---------------------------------------------------------------------------
# Tests for _date_match
# ---------------------------------------------------------------------------


class TestDateMatch:
    """Tests for _date_match() — date comparison with tolerance."""

    def test_same_date_matches(self):
        d = date(2026, 3, 5)
        assert _date_match(d, d) is True

    def test_one_day_apart_matches_default_tolerance(self):
        """Default tolerance is 1 day."""
        d1 = date(2026, 3, 5)
        d2 = date(2026, 3, 6)
        assert _date_match(d1, d2) is True

    def test_two_days_apart_fails_default_tolerance(self):
        d1 = date(2026, 3, 5)
        d2 = date(2026, 3, 7)
        assert _date_match(d1, d2) is False

    def test_none_returns_false(self):
        assert _date_match(None, date(2026, 3, 5)) is False
        assert _date_match(date(2026, 3, 5), None) is False
        assert _date_match(None, None) is False

    def test_custom_tolerance(self):
        d1 = date(2026, 3, 5)
        d2 = date(2026, 3, 8)
        assert _date_match(d1, d2, tolerance_days=3) is True
        assert _date_match(d1, d2, tolerance_days=2) is False

    def test_negative_direction(self):
        d1 = date(2026, 3, 6)
        d2 = date(2026, 3, 5)
        assert _date_match(d1, d2) is True


# ---------------------------------------------------------------------------
# Tests for _canonical_team
# ---------------------------------------------------------------------------


class TestCanonicalTeam:
    """Tests for _canonical_team() — team name alias mapping."""

    def test_squiggle_name_passthrough(self):
        assert _canonical_team("Brisbane") == "Brisbane"

    def test_afltables_full_name(self):
        assert _canonical_team("Brisbane Lions") == "Brisbane"

    def test_western_bulldogs(self):
        assert _canonical_team("Western Bulldogs") == "Bulldogs"
        assert _canonical_team("Bulldogs") == "Bulldogs"
        assert _canonical_team("Footscray") == "Bulldogs"

    def test_gws_variants(self):
        assert _canonical_team("Greater Western Sydney") == "Giants"
        assert _canonical_team("GWS") == "Giants"
        assert _canonical_team("GWS Giants") == "Giants"
        assert _canonical_team("Giants") == "Giants"

    def test_west_coast(self):
        assert _canonical_team("West Coast") == "WestCoast"
        assert _canonical_team("West Coast Eagles") == "WestCoast"
        assert _canonical_team("WestCoast") == "WestCoast"

    def test_gold_coast(self):
        assert _canonical_team("Gold Coast") == "GoldCoast"
        assert _canonical_team("Gold Coast Suns") == "GoldCoast"
        assert _canonical_team("GoldCoast") == "GoldCoast"

    def test_port_adelaide(self):
        assert _canonical_team("Port Adelaide") == "PortAdelaide"
        assert _canonical_team("Port Power") == "PortAdelaide"

    def test_north_melbourne(self):
        assert _canonical_team("North Melbourne") == "NorthMelbourne"
        assert _canonical_team("Kangaroos") == "NorthMelbourne"

    def test_case_insensitive(self):
        assert _canonical_team("BRISBANE LIONS") == "Brisbane"
        assert _canonical_team("western bulldogs") == "Bulldogs"

    def test_whitespace_stripped(self):
        assert _canonical_team("  Brisbane Lions  ") == "Brisbane"

    def test_unknown_team_returns_stripped(self):
        assert _canonical_team("Unknown FC") == "Unknown FC"


# ---------------------------------------------------------------------------
# Tests for read_csv
# ---------------------------------------------------------------------------


class TestReadCsv:
    """Tests for read_csv() helper."""

    def test_nonexistent_file_returns_empty(self, tmp_path):
        result = read_csv(str(tmp_path / "nonexistent.csv"))
        assert result == []

    def test_reads_csv_file(self, tmp_path):
        filepath = str(tmp_path / "test.csv")
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            f.write("name,value\n")
            f.write("alice,1\n")
            f.write("bob,2\n")
        result = read_csv(filepath)
        assert len(result) == 2
        assert result[0]["name"] == "alice"
        assert result[1]["value"] == "2"
