"""Contract test: scrape_injuries() must write CSVs that load_csv_to_db can read.

Phase 7 surfaced a cross-script bug:
  - ``scripts.scrape_to_csv.scrape_injuries()`` wrote the column name
    ``"injury"`` to ``injuries.csv``.
  - ``scripts.load_csv_to_db.load_injuries()`` reads
    ``row["injury_type"]`` (matching the ``Injury.injury_type`` column).
  - Net effect: every loaded injury had an empty ``injury_type`` which
    broke the ``uq_injuries_player_injury`` unique constraint on
    ``(player_name, injury_type)`` -- subsequent injuries for the same
    player collided on the empty string.

This test pins the contract so the column names cannot drift again.
"""

from __future__ import annotations

import csv
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure backend is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.scrape_to_csv import scrape_injuries


class TestScrapeInjuriesContract:
    """Contract: scrape_injuries() must write ``injury_type`` column."""

    @pytest.mark.asyncio
    async def test_writes_injury_type_column_to_match_loader(self, tmp_path):
        """The CSV header must include ``injury_type`` (matching the loader
        and the ``Injury.injury_type`` ORM column) -- not ``injury``.
        """
        fake_injuries = [
            {
                "team": "Collingwood",
                "player": "Daicos, Nick",
                "injury": "Hamstring",
                "return_timeline": "Test",
            },
            {
                "team": "Brisbane",
                "player": "Neale, Lachie",
                "injury": "Calf",
                "return_timeline": "2 weeks",
            },
        ]

        with patch("scripts.scrape_to_csv.FootyWireClient") as MockClient:
            instance = MagicMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=None)
            instance.get_injury_list = AsyncMock(return_value=fake_injuries)
            MockClient.return_value = instance

            count = await scrape_injuries(str(tmp_path), verbose=False)

        assert count == 2
        csv_path = tmp_path / "injuries.csv"
        assert csv_path.exists()

        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fieldnames = reader.fieldnames or []

        # The contract: the loader reads row["injury_type"]; the model
        # column is injury_type.  If we regress to "injury" the unique
        # constraint uq_injuries_player_injury on (player_name, injury_type)
        # will collide on empty strings.
        assert "injury_type" in fieldnames, (
            f"Scraper wrote columns {fieldnames!r} but loader expects 'injury_type'"
        )
        assert rows[0]["injury_type"] == "Hamstring"
        assert rows[0]["player_name"] == "Daicos, Nick"
        assert rows[0]["team"] == "Collingwood"
        assert rows[1]["injury_type"] == "Calf"
        assert rows[1]["player_name"] == "Neale, Lachie"
        assert rows[1]["return_timeline"] == "2 weeks"

    @pytest.mark.asyncio
    async def test_writes_scraped_at_and_source_columns(self, tmp_path):
        """The scraper must also preserve the ``source`` and ``scraped_at``
        audit columns -- the loader uses ``source='footywire'`` to tag the
        record and ``scraped_at`` to update on upsert.
        """
        fake_injuries = [
            {
                "team": "Essendon",
                "player": "Merrett, Zach",
                "injury": "Knee",
                "return_timeline": "TBC",
            },
        ]

        with patch("scripts.scrape_to_csv.FootyWireClient") as MockClient:
            instance = MagicMock()
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=None)
            instance.get_injury_list = AsyncMock(return_value=fake_injuries)
            MockClient.return_value = instance

            await scrape_injuries(str(tmp_path), verbose=False)

        with open(tmp_path / "injuries.csv", "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert rows[0]["source"] == "footywire"
        assert "scraped_at" in rows[0]
        assert rows[0]["scraped_at"]  # non-empty ISO timestamp
