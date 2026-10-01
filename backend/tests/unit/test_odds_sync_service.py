"""Unit tests for the odds sync service (BT-ODDS).

The daily job fetches AFL head-to-head odds from The Odds API, matches
each event to an upcoming ``games`` row via canonical team names and
kick-off day, and upserts the latest snapshot into ``game_odds``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from packages.shared.services.odds_sync import (
    match_events_to_games,
    run_odds_sync,
)


def _game(game_id: int, home: str, away: str, kickoff: datetime):
    g = MagicMock()
    g.id = game_id
    g.home_team = home
    g.away_team = away
    g.date = kickoff
    g.completed = False
    return g


def _mock_db_with_savepoint():
    """An AsyncMock session whose ``begin_nested()`` behaves like
    SQLAlchemy's: a *synchronous* call returning an async context
    manager (M-2 savepoint isolation)."""
    db = AsyncMock()
    nested = AsyncMock()
    nested.__aenter__ = AsyncMock(return_value=MagicMock())
    nested.__aexit__ = AsyncMock(return_value=False)
    db.begin_nested = MagicMock(return_value=nested)
    return db


def _event(home: str, away: str, kickoff: datetime, home_odds=1.55, away_odds=2.45):
    return {
        "external_id": f"evt-{home}-{away}".replace(" ", ""),
        "commence_time": kickoff,
        "home_team": home,
        "away_team": away,
        "home_odds": home_odds,
        "away_odds": away_odds,
        "bookmaker": "consensus:au (3 books)",
        "captured_at": datetime.now(timezone.utc),
    }


class TestMatchEventsToGames:
    def test_matches_on_canonical_names_and_day(self):
        kickoff = datetime(2026, 4, 4, 5, 20, tzinfo=timezone.utc)
        games = [_game(7, "Sydney", "West Coast", kickoff)]
        events = [_event("Sydney Swans", "West Coast Eagles", kickoff)]

        matched, unmatched = match_events_to_games(events, games)

        assert len(matched) == 1
        game, event = matched[0]
        assert game.id == 7
        assert event["home_odds"] == pytest.approx(1.55)
        assert unmatched == []

    def test_unmatched_event_is_reported(self):
        kickoff = datetime(2026, 4, 4, 5, 20, tzinfo=timezone.utc)
        games = [_game(7, "Sydney", "West Coast", kickoff)]
        events = [_event("Carlton", "Collingwood", kickoff)]

        matched, unmatched = match_events_to_games(events, games)

        assert matched == []
        assert len(unmatched) == 1

    def test_same_teams_different_day_does_not_match(self):
        """A rematch in another round must not steal the wrong game."""
        kickoff = datetime(2026, 4, 4, 5, 20, tzinfo=timezone.utc)
        games = [_game(7, "Sydney", "West Coast", kickoff)]
        events = [_event("Sydney Swans", "West Coast Eagles", kickoff + timedelta(days=9))]

        matched, unmatched = match_events_to_games(events, games)

        assert matched == []
        assert len(unmatched) == 1

    def test_nearest_day_within_tolerance_wins(self):
        """A 23:40 AWST game is 15:40 UTC the same day — but feed rounding
        to the adjacent day within the 1-day tolerance still matches."""
        kickoff = datetime(2026, 4, 4, 14, 40, tzinfo=timezone.utc)
        games = [_game(7, "Sydney", "West Coast", kickoff)]
        events = [_event("Sydney Swans", "West Coast Eagles", kickoff + timedelta(hours=2))]

        matched, _unmatched = match_events_to_games(events, games)

        assert len(matched) == 1

    def test_reverse_home_away_does_not_match(self):
        """Home/away sides matter — a reversed fixture is a different game."""
        kickoff = datetime(2026, 4, 4, 5, 20, tzinfo=timezone.utc)
        games = [_game(7, "Sydney", "West Coast", kickoff)]
        events = [_event("West Coast Eagles", "Sydney Swans", kickoff)]

        matched, unmatched = match_events_to_games(events, games)

        assert matched == []
        assert len(unmatched) == 1


class TestRunOddsSync:
    @pytest.mark.asyncio
    async def test_skips_cleanly_without_api_key(self):
        db = AsyncMock()
        with patch("packages.shared.services.odds_sync.settings") as mock_settings:
            mock_settings.odds_api_key = ""
            result = await run_odds_sync(db)

        assert result["skipped"] is True
        assert "odds_api_key" in result["reason"]
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_upserts_matched_odds_and_commits(self):
        db = _mock_db_with_savepoint()
        kickoff = datetime(2026, 4, 4, 5, 20, tzinfo=timezone.utc)
        game = _game(7, "Sydney", "West Coast", kickoff)

        games_result = MagicMock()
        games_result.scalars.return_value.all.return_value = [game]
        db.execute = AsyncMock(return_value=games_result)

        event = _event("Sydney Swans", "West Coast Eagles", kickoff)

        with (
            patch("packages.shared.services.odds_sync.settings") as mock_settings,
            patch("packages.shared.services.odds_sync.OddsAPIClient") as mock_client_cls,
            patch("packages.shared.services.odds_sync.GameOddsCRUD") as mock_crud,
        ):
            mock_settings.odds_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.get_afl_head_to_head_odds = AsyncMock(return_value=[event])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            mock_crud.upsert = AsyncMock()

            result = await run_odds_sync(db)

        assert result["skipped"] is False
        assert result["events_fetched"] == 1
        assert result["games_matched"] == 1
        assert result["games_updated"] == 1
        assert result["unmatched"] == 0
        mock_crud.upsert.assert_awaited_once()
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_failure_does_not_abort_batch(self):
        """M-2: one bad upsert must not lose the rest of the snapshot
        batch — each write runs inside its own SAVEPOINT."""
        db = _mock_db_with_savepoint()
        kickoff = datetime(2026, 4, 4, 5, 20, tzinfo=timezone.utc)
        games = [
            _game(1, "Sydney", "West Coast", kickoff),
            _game(2, "Carlton", "Collingwood", kickoff),
        ]
        events = [
            _event("Sydney Swans", "West Coast Eagles", kickoff),
            _event("Carlton", "Collingwood", kickoff),
        ]

        games_result = MagicMock()
        games_result.scalars.return_value.all.return_value = games
        db.execute = AsyncMock(return_value=games_result)

        with (
            patch("packages.shared.services.odds_sync.settings") as mock_settings,
            patch("packages.shared.services.odds_sync.OddsAPIClient") as mock_client_cls,
            patch("packages.shared.services.odds_sync.GameOddsCRUD") as mock_crud,
        ):
            mock_settings.odds_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.get_afl_head_to_head_odds = AsyncMock(return_value=events)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            async def upsert_side_effect(db, **kwargs):
                if kwargs["game_id"] == 1:
                    raise RuntimeError("db write failed")
                return MagicMock()

            mock_crud.upsert = AsyncMock(side_effect=upsert_side_effect)

            result = await run_odds_sync(db)

        assert result["games_matched"] == 2
        assert result["games_updated"] == 1
        assert result["errors"] == 1

    @pytest.mark.asyncio
    async def test_api_failure_raises_for_retry(self):
        """HTTP failures propagate so BaseJob's retry/backoff applies."""
        db = AsyncMock()
        with (
            patch("packages.shared.services.odds_sync.settings") as mock_settings,
            patch("packages.shared.services.odds_sync.OddsAPIClient") as mock_client_cls,
        ):
            mock_settings.odds_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.get_afl_head_to_head_odds = AsyncMock(
                side_effect=RuntimeError("upstream 502")
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(RuntimeError):
                await run_odds_sync(db)

    @pytest.mark.asyncio
    async def test_binds_naive_datetimes_only(self):
        """B-1 regression: ``games.date`` is a naive TIMESTAMP WITHOUT
        TIME ZONE column — binding tz-aware datetimes makes asyncpg
        raise (see the GameCRUD timezone regression test).  Every
        datetime bound by the upcoming-games query must be naive."""
        db = AsyncMock()
        captured = []
        games_result = MagicMock()
        games_result.scalars.return_value.all.return_value = []

        async def fake_execute(stmt):
            captured.append(stmt)
            return games_result

        db.execute = AsyncMock(side_effect=fake_execute)

        with (
            patch("packages.shared.services.odds_sync.settings") as mock_settings,
            patch("packages.shared.services.odds_sync.OddsAPIClient") as mock_client_cls,
        ):
            mock_settings.odds_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.get_afl_head_to_head_odds = AsyncMock(return_value=[])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await run_odds_sync(db)

        assert captured, "the upcoming-games query must have run"
        params = captured[0].compile().params
        assert params, "expected bound datetime parameters"
        for value in params.values():
            if isinstance(value, datetime):
                assert value.tzinfo is None, (
                    "tz-aware datetime bound against a naive column — "
                    "asyncpg will raise on every production run"
                )

    @pytest.mark.asyncio
    async def test_both_null_prices_skip_upsert(self):
        """m-3: an event with no usable prices on either side must not
        overwrite a previously-good snapshot with NULLs."""
        db = _mock_db_with_savepoint()
        kickoff = datetime(2026, 4, 4, 5, 20, tzinfo=timezone.utc)
        game = _game(7, "Sydney", "West Coast", kickoff)

        games_result = MagicMock()
        games_result.scalars.return_value.all.return_value = [game]
        db.execute = AsyncMock(return_value=games_result)

        event = _event("Sydney Swans", "West Coast Eagles", kickoff)
        event["home_odds"] = None
        event["away_odds"] = None

        with (
            patch("packages.shared.services.odds_sync.settings") as mock_settings,
            patch("packages.shared.services.odds_sync.OddsAPIClient") as mock_client_cls,
            patch("packages.shared.services.odds_sync.GameOddsCRUD") as mock_crud,
        ):
            mock_settings.odds_api_key = "test-key"
            mock_client = AsyncMock()
            mock_client.get_afl_head_to_head_odds = AsyncMock(return_value=[event])
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client
            mock_crud.upsert = AsyncMock()

            result = await run_odds_sync(db)

        assert result["games_matched"] == 1
        assert result["games_updated"] == 0
        assert result["games_skipped_no_prices"] == 1
        mock_crud.upsert.assert_not_awaited()
        db.commit.assert_awaited_once()
