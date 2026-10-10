"""End-to-end national-league sync tests (Phase 5.2, subtask 05).

The Phase A exit gate: ``run_league_sync`` drives the REAL sync path —
registry config → identity prerequisites → ``NrlProvider`` (recorded
FixtureDownload payloads, injected fetch) → ``LocalCompetitionSyncService``
→ the generic multisport tables — for ALL THREE competitions (``nrl``,
``nrlw``, ``origin``), proven against in-memory SQLite (aiosqlite).
No live HTTP, no Postgres — the fetch is injected and the storage layer
is the real CRUD against a throwaway engine.

Documented mapping decisions pinned here:

* **Origin 3-match series semantics** — the feed's ``RoundNumber`` is
  the series GAME NUMBER (1–3) and maps to ``events.round_id`` unchanged,
  so "Game N of the series" is queryable via the same round-scoped
  surfaces a rounds competition uses.  ``Group`` is the constant series
  label (``"State of Origin"``) carrying no per-match information beyond
  the competition identity the rows already carry, so it is deliberately
  NOT mapped — the events schema has no group column.  The competition
  row registers ``format='tournament'`` (the CHECK constraint admits
  only ``'rounds'|'tournament'``).

* **Timezone boundary (ADR 0001)** — the provider emits tz-aware UTC in
  the DTO; the storage boundary converts to venue-local naive
  ``events.starts_at`` using the venue's own IANA zone
  (``league_seeding.VENUE_TIMEZONES`` — Mount Smart Stadium is
  ``Pacific/Auckland``, Perth's Optus Stadium is ``Australia/Perth``),
  falling back to the competition timezone only for grounds the venue
  table does not list (the Las Vegas opener).  Per-competition timezone
  alone is therefore NEVER the stored truth for a known ground; display
  and backtest windows interpret the stored naive value through the same
  venue zone table (``league_seeding.venue_timezone``).

* **Failure contract** — operational sync failures raise
  ``BackendServiceError`` with the repo-standard shape (``status_code``/
  ``code``/``message``/``details``); per-fixture failures never abort a
  pass — they are logged and returned on ``stats["errors"]``.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import BackendServiceError
from packages.shared.crud.multisport import EventCRUD
from packages.shared.ingestion import national_leagues
from packages.shared.ingestion.league_seeding import (
    SPORT_ID,
    TEAMS,
    venue_timezone,
)
from packages.shared.ingestion.national_leagues import run_league_sync
from packages.shared.ingestion.nrl_provider import NrlProvider
from packages.shared.ingestion.venue_aliases import resolve_venue
from packages.shared.models import (
    Competition,
    Event,
    EventParticipant,
    EventSourceRef,
    LeagueTip,
    Participant,
    Season,
    Sport,
    Team,
    TeamAlias,
)

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nrl"


def _payload(name: str):
    """One recorded FixtureDownload season payload (verified schema)."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixtures: in-memory SQLite + recorded-feed provider injection
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    """Fresh in-memory SQLite session over the generic multisport
    schema — the whole sync path runs REAL against it."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for table in (
            Sport.__table__,
            Competition.__table__,
            Season.__table__,
            Participant.__table__,
            Team.__table__,
            TeamAlias.__table__,
            Event.__table__,
            EventParticipant.__table__,
            EventSourceRef.__table__,
            LeagueTip.__table__,
        ):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def recorded_feed(monkeypatch):
    """Inject recorded payloads behind the registry's provider factory.

    ``register(competition, payloads)`` wires an ``NrlProvider`` whose
    fetch serves ``payloads`` (slug → JSON list) from memory — no HTTP.
    The provider gets its OWN cache so tests never touch the shared
    process-wide one.
    """
    providers: dict = {}

    def register(competition: str, payloads: dict):
        async def fetch_json(url: str):
            slug = url.rsplit("/", 1)[-1]
            if slug not in payloads:
                raise FileNotFoundError(f"no recorded payload for {slug!r}")
            return payloads[slug]

        providers[competition] = NrlProvider(
            competition=competition, fetch_json=fetch_json, cache={}
        )

    def _factory(competition: str):
        return providers[competition]  # unregistered key → loud KeyError

    monkeypatch.setattr(national_leagues, "_nrl", _factory)
    return register


# ---------------------------------------------------------------------------
# Query helpers (read assertions straight off the generic tables)
# ---------------------------------------------------------------------------


async def _events_with_refs(db, source: str) -> dict[int, Event]:
    """Events joined to their source refs, keyed by external id."""
    rows = (
        await db.execute(
            select(EventSourceRef.external_id, Event)
            .join(Event, Event.id == EventSourceRef.event_id)
            .where(EventSourceRef.source == source)
        )
    ).all()
    return {int(external_id): event for external_id, event in rows}


async def _sides(db, event_id: int) -> dict[str, EventParticipant]:
    rows = (
        await db.execute(
            select(EventParticipant).where(EventParticipant.event_id == event_id)
        )
    ).scalars().all()
    return {row.side: row for row in rows}


async def _scalar(db, stmt) -> object:
    return (await db.execute(stmt)).scalar()


# ---------------------------------------------------------------------------
# nrl end-to-end
# ---------------------------------------------------------------------------


class TestNrlEndToEnd:
    """The men's premiership season lands on the generic tables."""

    async def test_season_syncs_end_to_end(self, db, recorded_feed):
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        stats = await run_league_sync(db, "nrl", 2026)

        assert stats["league"] == "nrl"
        assert stats["status"] == "success"
        assert stats["fixtures_synced"] == 5
        assert stats["errors"] == []
        assert stats["competition_id"] is not None
        assert stats["season_id"] is not None

    async def test_identity_rows_registered(self, db, recorded_feed):
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        stats = await run_league_sync(db, "nrl", 2026)

        sport = await _scalar(
            db, select(Sport).where(Sport.id == SPORT_ID)
        )
        assert sport is not None
        assert sport.display_name == "Rugby League"

        competition = await _scalar(
            db, select(Competition).where(Competition.id == stats["competition_id"])
        )
        assert competition.sport_id == SPORT_ID
        assert competition.name == "National Rugby League"
        assert competition.tier == "national"
        assert competition.format == "rounds"
        assert competition.timezone == "Australia/Brisbane"

        season = await _scalar(
            db, select(Season).where(Season.id == stats["season_id"])
        )
        assert season.label == "2026"
        assert season.is_current is True

    async def test_all_five_fixtures_stored_with_source_refs(self, db, recorded_feed):
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        await run_league_sync(db, "nrl", 2026)

        events = await _events_with_refs(db, "fixturedownload-nrl")
        assert set(events) == {2026001, 2026002, 2026003, 2026004, 2026005}

        # Result rows on event_participants — match 1 (Las Vegas opener).
        sides = await _sides(db, events[2026001].id)
        assert sides["home"].score == 28
        assert sides["away"].score == 18
        assert sides["home"].is_winner is True
        assert sides["away"].is_winner is False

    async def test_completed_flag_derived_from_score_presence(self, db, recorded_feed):
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        await run_league_sync(db, "nrl", 2026)

        events = await _events_with_refs(db, "fixturedownload-nrl")

        # Pre-match (null scores): scheduled, not completed, no scores.
        upcoming = events[2026003]
        assert upcoming.completed is False
        assert upcoming.status == "scheduled"
        sides = await _sides(db, upcoming.id)
        assert sides["home"].score is None
        assert sides["away"].score is None

        # Draw (level scores, null Winner): completed, NO winner side —
        # rugby league has draws.
        draw = events[2026004]
        assert draw.completed is True
        assert draw.status == "completed"
        sides = await _sides(db, draw.id)
        assert sides["home"].score == 20
        assert sides["away"].score == 20
        assert sides["home"].is_winner is None
        assert sides["away"].is_winner is None

        # Decided games carry the winner on the right side.
        sides = await _sides(db, events[2026002].id)  # Sharks 14 - Panthers 20
        assert sides["home"].is_winner is False
        assert sides["away"].is_winner is True

    async def test_rounds_and_finals_round_numbers_preserved(self, db, recorded_feed):
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        await run_league_sync(db, "nrl", 2026)

        events = await _events_with_refs(db, "fixturedownload-nrl")
        assert events[2026001].round_id == 1
        assert events[2026003].round_id == 2
        assert events[2026005].round_id == 31  # the grand final round

    async def test_league_tips_generated_for_completed_events(self, db, recorded_feed):
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        stats = await run_league_sync(db, "nrl", 2026)

        assert stats["league_tips"]["status"] == "success"
        tip_count = await _scalar(db, select(func.count(LeagueTip.id)))
        # 4 completed events (draw included — draw-no-pick tips) × 4
        # rugby-league models (elo, form, home_advantage, matchup).
        assert tip_count == 16


# ---------------------------------------------------------------------------
# origin — the 3-match mid-year series
# ---------------------------------------------------------------------------


class TestOriginSeriesSemantics:
    """Short-series mapping: RoundNumber is the series game number;
    the constant Group label is deliberately not stored."""

    async def test_three_match_series_syncs_as_tournament(self, db, recorded_feed):
        recorded_feed(
            "origin", {"state-of-origin-2026": _payload("state-of-origin-2026.json")}
        )

        stats = await run_league_sync(db, "origin", 2026)

        assert stats["league"] == "origin"
        assert stats["fixtures_synced"] == 3
        assert stats["errors"] == []

        competition = await _scalar(
            db, select(Competition).where(Competition.id == stats["competition_id"])
        )
        assert competition.name == "State of Origin"
        # A 3-match series, not a round-robin season — the schema CHECK
        # admits only 'rounds'|'tournament', so the series registers as
        # a tournament.
        assert competition.format == "tournament"
        assert competition.tier == "national"

    async def test_round_id_is_the_series_game_number(self, db, recorded_feed):
        """Game 1/2/3 of the series is queryable through the SAME
        round-scoped surfaces a rounds competition uses."""
        recorded_feed(
            "origin", {"state-of-origin-2026": _payload("state-of-origin-2026.json")}
        )

        await run_league_sync(db, "origin", 2026)

        events = await _events_with_refs(db, "fixturedownload-origin")
        assert sorted(event.round_id for event in events.values()) == [1, 2, 3]

        # The three games sit on distinct dates at distinct grounds —
        # no natural-key collisions within the series.
        starts = sorted(event.starts_at for event in events.values())
        assert len(set(starts)) == 3

    async def test_series_results_lands(self, db, recorded_feed):
        recorded_feed(
            "origin", {"state-of-origin-2026": _payload("state-of-origin-2026.json")}
        )

        await run_league_sync(db, "origin", 2026)

        events = await _events_with_refs(db, "fixturedownload-origin")
        game1_sides = await _sides(db, events[2026001].id)  # Maroons 20 - Blues 18
        assert game1_sides["home"].is_winner is True
        game3_sides = await _sides(db, events[2026003].id)  # Maroons 14 - Blues 22
        assert game3_sides["away"].is_winner is True

        # All three games completed: the series was played out.
        assert all(event.completed for event in events.values())

    async def test_series_participants_are_the_two_rep_teams(self, db, recorded_feed):
        recorded_feed(
            "origin", {"state-of-origin-2026": _payload("state-of-origin-2026.json")}
        )

        await run_league_sync(db, "origin", 2026)

        names = set(
            (
                await db.execute(
                    select(Participant.name).where(Participant.sport_id == SPORT_ID)
                )
            ).scalars()
        )
        assert {"Blues", "Maroons"} <= names


# ---------------------------------------------------------------------------
# nrlw — reuses club identities; separate competition/season rows
# ---------------------------------------------------------------------------


class TestNrlwEndToEnd:
    async def test_nrlw_season_syncs_end_to_end(self, db, recorded_feed):
        recorded_feed("nrlw", {"nrlw-2026": _payload("nrlw-2026.json")})

        stats = await run_league_sync(db, "nrlw", 2026)

        assert stats["league"] == "nrlw"
        assert stats["fixtures_synced"] == 2
        competition = await _scalar(
            db, select(Competition).where(Competition.id == stats["competition_id"])
        )
        assert competition.name == "NRL Women's Premiership"
        assert competition.format == "rounds"

        events = await _events_with_refs(db, "fixturedownload-nrlw")
        assert set(events) == {2026001, 2026002}
        assert events[2026001].completed is True
        assert events[2026002].completed is False

    async def test_nrlw_shares_club_identity_with_nrl(self, db, recorded_feed):
        """NRLW reuses the club identities — one ``Broncos`` participant
        row serves both competitions (same sport), while the events stay
        scoped to each competition's own season."""
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})
        recorded_feed("nrlw", {"nrlw-2026": _payload("nrlw-2026.json")})

        await run_league_sync(db, "nrl", 2026)
        await run_league_sync(db, "nrlw", 2026)

        broncos = (
            await db.execute(
                select(Participant).where(
                    Participant.sport_id == SPORT_ID, Participant.name == "Broncos"
                )
            )
        ).scalar_one_or_none()
        assert broncos is not None, "exactly one Broncos participant"

        nrl_events = await _events_with_refs(db, "fixturedownload-nrl")
        nrlw_events = await _events_with_refs(db, "fixturedownload-nrlw")
        assert len(nrl_events) == 5
        assert len(nrlw_events) == 2

        nrl_season_id = nrl_events[2026001].season_id
        nrlw_season_id = nrlw_events[2026001].season_id
        assert nrl_season_id != nrlw_season_id


# ---------------------------------------------------------------------------
# Timezone boundary (ADR 0001)
# ---------------------------------------------------------------------------


class TestTimezoneBoundary:
    """tz-aware UTC in the DTO → venue-local naive at the storage
    boundary; the venue zone table — not the competition timezone —
    decides known grounds."""

    async def test_warriors_match_stored_auckland_local(self, db, recorded_feed):
        """The point of the venue table: Go Media Stadium is Mount Smart
        Stadium, Pacific/Auckland — 06:00Z is 19:00 in Auckland (NZDT),
        NOT 16:00 Brisbane."""
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        await run_league_sync(db, "nrl", 2026)

        events = await _events_with_refs(db, "fixturedownload-nrl")
        warriors = events[2026003]
        assert warriors.venue == "Mount Smart Stadium"
        assert warriors.starts_at == datetime(2026, 3, 8, 19, 0)
        assert warriors.starts_at.tzinfo is None

    async def test_perth_origin_match_stored_perth_local(self, db, recorded_feed):
        """A Perth-hosted Origin game is 18:05 AWST — the competition's
        Brisbane timezone alone would have stored 20:05."""
        recorded_feed(
            "origin", {"state-of-origin-2026": _payload("state-of-origin-2026.json")}
        )

        await run_league_sync(db, "origin", 2026)

        events = await _events_with_refs(db, "fixturedownload-origin")
        perth_game = events[2026003]
        assert perth_game.venue == "Perth Stadium"  # Optus Stadium, canonical
        assert perth_game.starts_at == datetime(2026, 7, 15, 18, 5)

    async def test_unknown_venue_falls_back_to_competition_timezone(
        self, db, recorded_feed
    ):
        """The Las Vegas opener is not in the venue table — its stored
        window falls back to the competition timezone (Brisbane, no DST:
        02:15Z → 12:15), and the venue passes through verbatim."""
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        await run_league_sync(db, "nrl", 2026)

        events = await _events_with_refs(db, "fixturedownload-nrl")
        vegas = events[2026001]
        assert vegas.venue == "Allegiant Stadium"  # unknown → verbatim
        assert vegas.starts_at == datetime(2026, 3, 1, 12, 15)

    async def test_dst_edge_venue_uses_the_venue_zone(self, db, recorded_feed):
        """A Sydney ground on the October DST transition day converts
        through Australia/Sydney (venue zone), not the competition zone.
        Expected value computed via zoneinfo — the assertion pins WHICH
        zone was chosen and that the result is naive."""
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        await run_league_sync(db, "nrl", 2026)

        events = await _events_with_refs(db, "fixturedownload-nrl")
        gf = events[2026005]
        assert gf.venue == "Stadium Australia"  # Accor Stadium, canonical
        expected = (
            datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)
            .astimezone(ZoneInfo("Australia/Sydney"))
            .replace(tzinfo=None)
        )
        assert gf.starts_at == expected

    async def test_every_stored_starts_at_is_naive(self, db, recorded_feed):
        """The ``events.starts_at`` convention (models.multisport):
        venue-local NAIVE — no tz-aware value ever reaches storage."""
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        await run_league_sync(db, "nrl", 2026)

        events = await _events_with_refs(db, "fixturedownload-nrl")
        assert all(event.starts_at.tzinfo is None for event in events.values())

    def test_venue_zone_table_is_the_documented_window_source(self):
        """Display/backtest windows read the venue's zone from the same
        table the storage boundary used — Warriors = Pacific/Auckland
        via venue data, never per-competition timezone alone."""
        assert venue_timezone("Mount Smart Stadium") == "Pacific/Auckland"
        assert venue_timezone("Go Media Stadium") == "Pacific/Auckland"
        assert venue_timezone("Perth Stadium") == "Australia/Perth"
        assert venue_timezone("Lang Park") == "Australia/Brisbane"
        assert venue_timezone("Allegiant Stadium") is None  # → competition tz


# ---------------------------------------------------------------------------
# Idempotency + results backfill
# ---------------------------------------------------------------------------


class TestIdempotentRerun:
    async def test_rerun_upserts_without_duplicates(self, db, recorded_feed):
        payloads = {"nrl-2026": _payload("nrl-2026.json")}
        recorded_feed("nrl", payloads)

        first = await run_league_sync(db, "nrl", 2026)
        second = await run_league_sync(db, "nrl", 2026)

        assert first["status"] == "success"
        assert second["status"] == "success"
        assert second["fixtures_synced"] == 5

        events = await _events_with_refs(db, "fixturedownload-nrl")
        assert set(events) == {
            2026001,
            2026002,
            2026003,
            2026004,
            2026005,
        }, "no duplicate events"

        event_count = await _scalar(db, select(func.count(Event.id)))
        assert event_count == 5
        side_count = await _scalar(db, select(func.count(EventParticipant.id)))
        assert side_count == 10  # two sides per event, not four
        ref_count = await _scalar(db, select(func.count(EventSourceRef.id)))
        assert ref_count == 5

        # Unchanged events update in place — sync_version does not churn.
        assert all(event.sync_version == 1 for event in events.values())

    async def test_rerun_regenerates_tips_without_duplicates(self, db, recorded_feed):
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        first = await run_league_sync(db, "nrl", 2026)
        second = await run_league_sync(db, "nrl", 2026)

        assert first["league_tips"]["tips_inserted"] == 16
        assert second["league_tips"]["tips_inserted"] == 0
        assert second["league_tips"]["tips_updated"] == 0
        tip_count = await _scalar(db, select(func.count(LeagueTip.id)))
        assert tip_count == 16


class TestResultsBackfill:
    async def test_score_backfill_flips_completion_in_place(self, db, recorded_feed):
        """The daily-refresh cadence: fixtures sync first, results land
        on later runs — the SAME events update, nothing duplicates, and
        the completed flag follows score presence."""
        payloads = {"state-of-origin-2026": copy.deepcopy(
            _payload("state-of-origin-2026.json")
        )}
        # Game 3 not yet played.
        payloads["state-of-origin-2026"][2]["HomeTeamScore"] = None
        payloads["state-of-origin-2026"][2]["AwayTeamScore"] = None
        payloads["state-of-origin-2026"][2]["Winner"] = None
        recorded_feed("origin", payloads)

        first = await run_league_sync(db, "origin", 2026)
        events = await _events_with_refs(db, "fixturedownload-origin")
        assert events[2026003].completed is False
        ids_before = {event.id for event in events.values()}

        # The feed refreshes with the result.
        payloads["state-of-origin-2026"][2] = _payload(
            "state-of-origin-2026.json"
        )[2]
        second = await run_league_sync(db, "origin", 2026)

        assert second["fixtures_synced"] == 3
        events = await _events_with_refs(db, "fixturedownload-origin")
        assert {event.id for event in events.values()} == ids_before

        backfilled = events[2026003]
        assert backfilled.completed is True
        assert backfilled.status == "completed"
        sides = await _sides(db, backfilled.id)
        assert sides["home"].score == 14
        assert sides["away"].score == 22
        assert sides["away"].is_winner is True  # Blues take the series

        assert first["league_tips"]["tips_inserted"] == 8  # two completed × 4
        assert second["league_tips"]["tips_inserted"] == 4

    async def test_tip_for_backfilled_result_appears(self, db, recorded_feed):
        payloads = {"state-of-origin-2026": copy.deepcopy(
            _payload("state-of-origin-2026.json")
        )}
        payloads["state-of-origin-2026"][2]["HomeTeamScore"] = None
        payloads["state-of-origin-2026"][2]["AwayTeamScore"] = None
        payloads["state-of-origin-2026"][2]["Winner"] = None
        recorded_feed("origin", payloads)

        await run_league_sync(db, "origin", 2026)
        assert (
            await _scalar(db, select(func.count(LeagueTip.id)))
        ) == 8  # two completed games × 4

        payloads["state-of-origin-2026"][2] = _payload(
            "state-of-origin-2026.json"
        )[2]
        stats = await run_league_sync(db, "origin", 2026)

        assert stats["league_tips"]["tips_inserted"] == 4
        assert (
            await _scalar(db, select(func.count(LeagueTip.id)))
        ) == 12  # all three games now tipped (3 × 4 models)


# ---------------------------------------------------------------------------
# Failure contract
# ---------------------------------------------------------------------------


class TestPartialFailureLogging:
    async def test_one_bad_fixture_never_aborts_the_pass(
        self, db, recorded_feed, monkeypatch
    ):
        """A fixture-level failure is logged and returned on
        stats['errors'] — never swallowed, never raised — and the runner
        still reports the pass as a (partial) success.

        Note the documented service semantics: the pass runs in ONE
        transaction, so the failed fixture's rollback discards the
        pass's uncommitted rows; the contract under test here is the
        REPORTING (stats carry the failure), not persistence."""
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        real_upsert = EventCRUD.upsert_fixture

        async def flaky_upsert(db_session, **kwargs):
            if kwargs["fixture"].external_id == 2026005:  # the last fixture
                raise RuntimeError("simulated upsert failure")
            return await real_upsert(db_session, **kwargs)

        monkeypatch.setattr(EventCRUD, "upsert_fixture", flaky_upsert)

        stats = await run_league_sync(db, "nrl", 2026)  # must NOT raise

        assert stats["status"] == "success"
        assert stats["fixtures_synced"] == 4
        assert len(stats["errors"]) == 1
        assert "fixture 2026005" in stats["errors"][0]
        assert "simulated upsert failure" in stats["errors"][0]


class TestSyncFailureRaisesBackendServiceError:
    async def test_feed_failure_raises_repo_standard_error(self, db, recorded_feed):
        recorded_feed("nrl", {})  # no payload on file → fetch blows up

        with pytest.raises(BackendServiceError) as excinfo:
            await run_league_sync(db, "nrl", 2026)

        error = excinfo.value
        assert error.status_code == 502
        assert error.code == "league_sync_failed"
        assert "National Rugby League" in error.message
        assert "nrl-2026" in error.message  # the underlying cause bubbles
        assert error.details == {"league": "nrl", "season": 2026}
        assert isinstance(error.__cause__, FileNotFoundError)

    async def test_unknown_league_raises_repo_standard_error(self, db):
        with pytest.raises(BackendServiceError) as excinfo:
            await run_league_sync(db, "afl-mens", 2026)

        error = excinfo.value
        assert error.status_code == 400
        assert error.code == "unknown_league"
        assert "afl-mens" in error.message
        assert "nrl" in error.message  # available keys listed
        assert error.details == {"league": "afl-mens", "season": 2026}


# ---------------------------------------------------------------------------
# Participant resolution — the anti-hijack contract
# ---------------------------------------------------------------------------


class TestParticipantResolution:
    async def test_feed_nicknames_resolve_exactly_not_via_afl_fallback(
        self, db, recorded_feed
    ):
        """The runner seeds every canonical nickname (plus full-name
        aliases) BEFORE the pass, so feed names resolve at
        ``ParticipantResolver``'s exact-name step and its transitional
        AFL ``canonical_team()`` fallback never gets a chance to remap
        a rugby-league name under an AFL one.  The 2022 payload
        exercises the collision-prone names ('Bulldogs', 'Sea Eagles')."""
        recorded_feed("nrl", {"nrl-2022": _payload("nrl-2022.json")})

        await run_league_sync(db, "nrl", 2022)

        names = set(
            (
                await db.execute(
                    select(Participant.name).where(
                        Participant.sport_id == SPORT_ID,
                        Participant.kind == "team",
                    )
                )
            ).scalars()
        )
        assert "Bulldogs" in names
        assert "Western Bulldogs" not in names
        assert "Sea Eagles" in names

        # The full identity set is seeded (17 clubs + Blues + Maroons).
        assert names == {team.name for team in TEAMS}

    async def test_full_club_names_resolve_via_aliases(self, db, recorded_feed):
        recorded_feed("nrl", {"nrl-2022": _payload("nrl-2022.json")})

        await run_league_sync(db, "nrl", 2022)

        # 'Canterbury-Bankstown Bulldogs' is a seeded alias of 'Bulldogs'.
        bulldogs = (
            await db.execute(
                select(Participant)
                .join(TeamAlias, TeamAlias.team_participant_id == Participant.id)
                .where(
                    Participant.sport_id == SPORT_ID,
                    TeamAlias.alias == "Canterbury-Bankstown Bulldogs",
                )
            )
        ).scalar_one_or_none()
        assert bulldogs is not None
        assert bulldogs.name == "Bulldogs"

    async def test_venue_canonicalization_survives_storage(self, db, recorded_feed):
        """Sponsor-branded Location strings collapse onto canonical
        grounds BEFORE storage (the provider resolves the alias table),
        so the persisted ``events.venue`` value is the canonical ground
        backtests can group on."""
        recorded_feed("nrl", {"nrl-2022": _payload("nrl-2022.json")})

        await run_league_sync(db, "nrl", 2022)

        events = await _events_with_refs(db, "fixturedownload-nrl")
        assert events[2022001].venue == resolve_venue("PointsBet Stadium")
        assert events[2022001].venue == "Shark Park"
        assert events[2022002].venue == "Mount Smart Stadium"
        assert events[2022004].venue == "Jubilee Stadium"
