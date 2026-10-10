"""Historical backfill loader tests (Phase 5.2, subtask 10).

``run_nrl_historic_load`` backfills seasons 2017+ for ALL THREE
rugby-league competitions through the SAME path as the live sync:
registry config → ``run_league_sync`` → ``NrlProvider`` (recorded
FixtureDownload payloads, injected fetch) → ``FixtureDTO`` →
``LocalCompetitionSyncService`` → the generic multisport tables.  No
parallel schema, no CSV parser — the JSON feed serves history, so
backtest rows and live rows are one and the same shape.

What is pinned here, against recorded season payloads (in-memory
SQLite, no live HTTP, no Postgres):

* **All three competitions, multi-season** — ``nrl`` (2017 + 2022 +
  2026 samples), ``nrlw`` and ``origin`` land as competition/season/
  event rows with source refs, identical to a live-synced season.

* **Venue alias normalization AT LOAD** — the Sharks' Cronulla ground
  appears as "Southern Cross Group Stadium" (2017), "PointsBet Stadium"
  (2022) and "Ocean Protect Stadium" (2026) in the recorded payloads;
  all three backfilled eras must land on ONE canonical venue
  ("Shark Park"), which is the property backtests group on.

* **Idempotent re-load** — a second backfill upserts in place: no
  duplicate events, sides or source refs; unchanged events do not bump
  ``sync_version``.

* **Loud refusals** — unknown sources (Kaggle etc.), pre-2017 seasons
  (deep history out of scope), unknown competitions and empty requests
  all raise ``ValueError`` BEFORE any pass runs; nothing loads.

* **Failure tolerance + hygiene** — one missing season payload records
  the failure and the sweep continues (``status="partial"``); historical
  seasons are never marked current.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.shared.config import settings
from packages.shared.ingestion import national_leagues
from packages.shared.ingestion.league_seeding import (
    MIN_SEASON,
    TEAMS,
    season_years,
)
from packages.shared.ingestion.nrl_provider import NrlProvider
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
from packages.shared.services.nrl_historic_load import (
    APPROVED_COMPETITIONS,
    SUPPORTED_SOURCE,
    backfill_seasons,
    run_nrl_historic_load,
)

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "nrl"


def _payload(name: str):
    """One recorded FixtureDownload season payload (verified schema)."""
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fixtures: in-memory SQLite + recorded-feed provider injection
# (same pattern as test_national_league_sync — the loader drives the
# REAL registry runner with recorded payloads behind the factory).
# ---------------------------------------------------------------------------


@pytest.fixture
async def db():
    """Fresh in-memory SQLite session over the generic multisport
    schema — the whole backfill path runs REAL against it."""
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
    Each competition gets ONE provider with its OWN cache, so passes
    within a backfill reuse the fetch and tests never touch the shared
    process-wide cache.
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


async def _events_with_refs(db, source: str, *, season_id: int | None = None):
    """Events joined to their source refs, keyed by external id.

    Scoped to ONE season when ``season_id`` is given — ``MatchNumber``
    restarts every season, so cross-season keying by external id alone
    would collide.
    """
    stmt = (
        select(EventSourceRef.external_id, Event)
        .join(Event, Event.id == EventSourceRef.event_id)
        .where(EventSourceRef.source == source)
    )
    if season_id is not None:
        stmt = stmt.where(Event.season_id == season_id)
    rows = (await db.execute(stmt)).all()
    return {int(external_id): event for external_id, event in rows}


async def _scalar(db, stmt) -> object:
    return (await db.execute(stmt)).scalar()


async def _event_count(db) -> int:
    return int(await _scalar(db, select(func.count(Event.id))))


async def _all_source_refs(db) -> set[str]:
    rows = (await db.execute(select(EventSourceRef.source))).scalars()
    return {str(row) for row in rows}


async def _season_ids_by_label(db, competition_name: str) -> dict[str, int]:
    """Season rows of one competition, keyed by label (e.g. "2022")."""
    rows = (
        await db.execute(
            select(Season.label, Season.id)
            .join(Competition, Competition.id == Season.competition_id)
            .where(Competition.name == competition_name)
        )
    ).all()
    return {str(label): int(season_id) for label, season_id in rows}


# ---------------------------------------------------------------------------
# Loud refusals — unknown sources, pre-2017 seasons, unknown competitions
# ---------------------------------------------------------------------------


class TestLoudRefusals:
    """The loader only loads sanctioned history, and says no loudly."""

    async def test_unknown_source_is_refused_loudly(self, db, recorded_feed):
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        with pytest.raises(ValueError) as excinfo:
            await run_nrl_historic_load(db, source="kaggle")

        message = str(excinfo.value)
        assert "kaggle" in message.lower()
        assert SUPPORTED_SOURCE in message  # the one sanctioned source
        assert await _event_count(db) == 0, "refusal precedes any pass"
        assert await _all_source_refs(db) == set()

    async def test_kaggle_deep_history_is_out_of_scope_by_name(
        self, db, recorded_feed
    ):
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        with pytest.raises(ValueError) as excinfo:
            await run_nrl_historic_load(db, source="kaggle")

        assert "out of scope" in str(excinfo.value)

    async def test_nrl_dot_com_fallback_is_refused(self, db):
        """The nrl.com fallback is documented as NOT built — refusing is
        the contract (see docs/data-loading.md)."""
        with pytest.raises(ValueError) as excinfo:
            await run_nrl_historic_load(db, source="nrl.com")

        assert "nrl.com" in str(excinfo.value)
        assert await _event_count(db) == 0

    async def test_pre_2017_seasons_are_refused(self, db, recorded_feed):
        """Deep history (Kaggle 1990+) is out of scope: anything before
        the first supported FixtureDownload season fails loudly."""
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        with pytest.raises(ValueError) as excinfo:
            await run_nrl_historic_load(db, seasons=[1990, 2022])

        message = str(excinfo.value)
        assert "1990" in message
        assert str(MIN_SEASON) in message
        assert await _event_count(db) == 0

    async def test_through_year_before_2017_is_refused(self, db):
        with pytest.raises(ValueError):
            await run_nrl_historic_load(db, through_year=2016)

        assert await _event_count(db) == 0

    async def test_unknown_competition_is_refused(self, db, recorded_feed):
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        with pytest.raises(ValueError) as excinfo:
            await run_nrl_historic_load(
                db, competitions=["nrl", "super-league"]
            )

        message = str(excinfo.value)
        assert "super-league" in message
        for approved in APPROVED_COMPETITIONS:
            assert approved in message
        assert await _event_count(db) == 0

    async def test_empty_requests_are_refused(self, db):
        with pytest.raises(ValueError):
            await run_nrl_historic_load(db, competitions=[])
        with pytest.raises(ValueError):
            await run_nrl_historic_load(db, seasons=[])

        assert await _event_count(db) == 0

    async def test_all_passes_failing_reports_failed_not_success(
        self, db, recorded_feed
    ):
        recorded_feed("origin", {})  # registered but empty → every pass 404s

        stats = await run_nrl_historic_load(
            db, competitions=["origin"], seasons=[2026]
        )

        assert stats["status"] == "failed"
        assert stats["passes_synced"] == 0
        assert stats["passes_failed"] == 1


# ---------------------------------------------------------------------------
# Season window — 2017 … current
# ---------------------------------------------------------------------------


class TestSeasonWindow:
    def test_window_starts_at_2017(self):
        assert backfill_seasons(2020) == [2017, 2018, 2019, 2020]

    def test_window_defaults_to_the_configured_current_season(self):
        assert backfill_seasons() == season_years(settings.current_season)

    def test_supported_source_is_the_fixturedownload_feed(self):
        assert SUPPORTED_SOURCE == "fixturedownload"


# ---------------------------------------------------------------------------
# All three competitions, same generic tables as live sync
# ---------------------------------------------------------------------------


class TestBackfillAllCompetitions:
    async def test_all_three_competitions_backfill_end_to_end(
        self, db, recorded_feed
    ):
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})
        recorded_feed("nrlw", {"nrlw-2026": _payload("nrlw-2026.json")})
        recorded_feed(
            "origin", {"state-of-origin-2026": _payload("state-of-origin-2026.json")}
        )

        stats = await run_nrl_historic_load(db, seasons=[2026])

        assert stats["status"] == "success"
        assert stats["fixtures_synced"] == 10  # 5 nrl + 2 nrlw + 3 origin
        assert stats["errors"] == []

        nrl_events = await _events_with_refs(db, "fixturedownload-nrl")
        nrlw_events = await _events_with_refs(db, "fixturedownload-nrlw")
        origin_events = await _events_with_refs(db, "fixturedownload-origin")
        assert set(nrl_events) == {2026001, 2026002, 2026003, 2026004, 2026005}
        assert set(nrlw_events) == {2026001, 2026002}
        assert set(origin_events) == {2026001, 2026002, 2026003}

    async def test_multi_season_backfill_for_one_competition(
        self, db, recorded_feed
    ):
        recorded_feed(
            "nrl",
            {
                "nrl-2017": _payload("nrl-2017.json"),
                "nrl-2022": _payload("nrl-2022.json"),
            },
        )

        stats = await run_nrl_historic_load(
            db, competitions=["nrl"], seasons=[2022, 2017]
        )

        assert stats["status"] == "success"
        assert stats["seasons"] == [2017, 2022]  # sorted, deduped
        assert set(stats["results"]["nrl"]) == {2017, 2022}

        # Both seasons are distinct Season rows of ONE competition, each
        # holding exactly its sample's fixture count.
        season_ids = await _season_ids_by_label(db, "National Rugby League")
        assert set(season_ids) == {"2017", "2022"}
        assert len(
            await _events_with_refs(
                db, "fixturedownload-nrl", season_id=season_ids["2017"]
            )
        ) == 2
        assert len(
            await _events_with_refs(
                db, "fixturedownload-nrl", season_id=season_ids["2022"]
            )
        ) == 4

        assert await _event_count(db) == 6

    async def test_aggregate_stats_shape(self, db, recorded_feed):
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})
        recorded_feed("nrlw", {"nrlw-2026": _payload("nrlw-2026.json")})

        stats = await run_nrl_historic_load(
            db, competitions=["nrl", "nrlw"], seasons=[2026]
        )

        assert stats["source"] == SUPPORTED_SOURCE
        assert stats["competitions"] == ["nrl", "nrlw"]
        assert stats["seasons"] == [2026]
        assert stats["passes_attempted"] == 2
        assert stats["passes_synced"] == 2
        assert stats["passes_failed"] == 0
        assert stats["fixtures_synced"] == 7
        assert stats["errors"] == []
        assert stats["results"]["nrl"][2026]["fixtures_synced"] == 5
        assert stats["results"]["nrlw"][2026]["fixtures_synced"] == 2


class TestSamePathAsLiveSync:
    """The backfill is the live runner, looped — same stats shape, same
    participant path, same tables."""

    async def test_pass_stats_come_from_the_live_sync_runner(
        self, db, recorded_feed
    ):
        recorded_feed(
            "origin", {"state-of-origin-2026": _payload("state-of-origin-2026.json")}
        )

        stats = await run_nrl_historic_load(db, competitions=["origin"], seasons=[2026])

        pass_stats = stats["results"]["origin"][2026]
        assert pass_stats["league"] == "origin"  # run_league_sync's own marker
        assert pass_stats["status"] == "success"
        assert pass_stats["competition_id"] is not None
        assert pass_stats["season_id"] is not None

    async def test_participants_resolve_through_the_seeded_identity_set(
        self, db, recorded_feed
    ):
        """Same participant path as live sync: the runner seeds the 19
        canonical teams and feed nicknames resolve exactly — the AFL
        fallback never hijacks ('Bulldogs' ≠ 'Western Bulldogs')."""
        recorded_feed(
            "nrl",
            {
                "nrl-2017": _payload("nrl-2017.json"),
                "nrl-2022": _payload("nrl-2022.json"),
            },
        )

        await run_nrl_historic_load(
            db, competitions=["nrl"], seasons=[2017, 2022]
        )

        names = set(
            (
                await db.execute(
                    select(Participant.name).where(
                        Participant.sport_id == "rugby-league",
                        Participant.kind == "team",
                    )
                )
            ).scalars()
        )
        assert names == {team.name for team in TEAMS}

    async def test_results_backfill_via_scores_lands_like_live_sync(
        self, db, recorded_feed
    ):
        recorded_feed("nrl", {"nrl-2017": _payload("nrl-2017.json")})

        await run_nrl_historic_load(db, competitions=["nrl"], seasons=[2017])

        season_ids = await _season_ids_by_label(db, "National Rugby League")
        events = await _events_with_refs(
            db, "fixturedownload-nrl", season_id=season_ids["2017"]
        )
        assert all(event.completed for event in events.values())  # history


# ---------------------------------------------------------------------------
# Venue alias normalization AT LOAD — the backtest-stability contract
# ---------------------------------------------------------------------------


class TestVenueNormalizationAtLoad:
    async def test_sharks_ground_is_one_canonical_venue_across_eras(
        self, db, recorded_feed
    ):
        """Cronulla appears as Southern Cross Group (2017), PointsBet
        (2022) and Ocean Protect (2026) in the recorded payloads — the
        backfill must collapse all three onto 'Shark Park' so backtest
        grouping is venue-stable across sponsor drift."""
        recorded_feed(
            "nrl",
            {
                "nrl-2017": _payload("nrl-2017.json"),
                "nrl-2022": _payload("nrl-2022.json"),
                "nrl-2026": _payload("nrl-2026.json"),
            },
        )

        await run_nrl_historic_load(
            db, competitions=["nrl"], seasons=[2017, 2022, 2026]
        )

        shark_park = (
            (
                await db.execute(
                    select(Event).where(Event.venue == "Shark Park")
                )
            )
            .scalars()
            .all()
        )
        assert len(shark_park) == 3, "one Sharks home game per era"
        season_ids = set(
            (await _season_ids_by_label(db, "National Rugby League")).values()
        )
        assert {event.season_id for event in shark_park} == season_ids

        # No sponsor-branded variant survives anywhere in history.
        venues = set(
            (
                await db.execute(select(Event.venue).where(Event.venue.is_not(None)))
            ).scalars()
        )
        assert venues.isdisjoint(
            {
                "Southern Cross Group Stadium",
                "PointsBet Stadium",
                "Ocean Protect Stadium",
            }
        )

    async def test_2017_era_venues_land_canonical(self, db, recorded_feed):
        """Every 2017-era sponsor name maps through the alias table at
        load — including the Origin grounds (Suncorp, ANZ)."""
        recorded_feed("nrl", {"nrl-2017": _payload("nrl-2017.json")})
        recorded_feed(
            "origin", {"state-of-origin-2017": _payload("state-of-origin-2017.json")}
        )

        await run_nrl_historic_load(
            db, competitions=["nrl", "origin"], seasons=[2017]
        )

        nrl_season_ids = await _season_ids_by_label(db, "National Rugby League")
        nrl_events = await _events_with_refs(
            db, "fixturedownload-nrl", season_id=nrl_season_ids["2017"]
        )
        assert nrl_events[2017001].venue == "Shark Park"  # Southern Cross Group
        assert nrl_events[2017002].venue == "North Queensland Stadium"  # 1300SMILES

        origin_season_ids = await _season_ids_by_label(db, "State of Origin")
        origin_events = await _events_with_refs(
            db, "fixturedownload-origin", season_id=origin_season_ids["2017"]
        )
        assert origin_events[2017001].venue == "Lang Park"  # Suncorp Stadium
        assert origin_events[2017002].venue == "Stadium Australia"  # ANZ Stadium


# ---------------------------------------------------------------------------
# Idempotent re-load
# ---------------------------------------------------------------------------


class TestIdempotentReload:
    async def test_reload_upserts_without_duplicates(self, db, recorded_feed):
        payloads = {
            "nrl-2017": _payload("nrl-2017.json"),
            "nrl-2022": _payload("nrl-2022.json"),
        }
        recorded_feed("nrl", payloads)

        first = await run_nrl_historic_load(
            db, competitions=["nrl"], seasons=[2017, 2022]
        )
        second = await run_nrl_historic_load(
            db, competitions=["nrl"], seasons=[2017, 2022]
        )

        assert first["status"] == "success"
        assert second["status"] == "success"
        assert second["fixtures_synced"] == 6, "re-load upserts every fixture"

        assert await _event_count(db) == 6, "no duplicate events"
        side_count = await _scalar(db, select(func.count(EventParticipant.id)))
        assert side_count == 12  # two sides per event, not four
        ref_count = await _scalar(db, select(func.count(EventSourceRef.id)))
        assert ref_count == 6

        # Unchanged events update in place — sync_version does not churn
        # (checked PER SEASON: the provider's external id is the season
        # composite ``season*1000 + MatchNumber``, so refs never collide
        # across seasons; the helper stays season-scoped for clarity).
        season_ids = await _season_ids_by_label(db, "National Rugby League")
        for season_id in season_ids.values():
            season_events = await _events_with_refs(
                db, "fixturedownload-nrl", season_id=season_id
            )
            assert all(
                event.sync_version == 1 for event in season_events.values()
            )

    async def test_reload_leaves_tips_deduplicated(self, db, recorded_feed):
        recorded_feed("nrl", {"nrl-2026": _payload("nrl-2026.json")})

        await run_nrl_historic_load(db, competitions=["nrl"], seasons=[2026])
        tip_count_after_first = await _scalar(
            db, select(func.count(LeagueTip.id))
        )
        second = await run_nrl_historic_load(
            db, competitions=["nrl"], seasons=[2026]
        )

        tip_count_after_second = await _scalar(
            db, select(func.count(LeagueTip.id))
        )
        assert tip_count_after_second == tip_count_after_first == 16
        assert second["results"]["nrl"][2026]["league_tips"]["tips_inserted"] == 0


# ---------------------------------------------------------------------------
# Failure tolerance — one dead pass never aborts the sweep
# ---------------------------------------------------------------------------


class TestFailureTolerance:
    async def test_missing_season_payload_does_not_abort_the_backfill(
        self, db, recorded_feed
    ):
        """NRLW predates 2017 on the feed — a season with no recorded
        payload fails its pass and is REPORTED, while the rest of the
        sweep completes."""
        recorded_feed("nrlw", {"nrlw-2026": _payload("nrlw-2026.json")})

        stats = await run_nrl_historic_load(
            db, competitions=["nrlw"], seasons=[2017, 2026]
        )

        assert stats["status"] == "partial"
        assert stats["passes_synced"] == 1
        assert stats["passes_failed"] == 1
        assert len(stats["errors"]) == 1
        assert "nrlw" in stats["errors"][0]
        assert "2017" in stats["errors"][0]
        assert set(stats["results"]["nrlw"]) == {2026}
        assert stats["fixtures_synced"] == 2


# ---------------------------------------------------------------------------
# Historical hygiene — backfilled seasons are never 'current'
# ---------------------------------------------------------------------------


class TestHistoricalSeasonsNotCurrent:
    async def test_backfilled_seasons_are_not_marked_current(
        self, db, recorded_feed
    ):
        recorded_feed(
            "nrl",
            {
                "nrl-2017": _payload("nrl-2017.json"),
                "nrl-2022": _payload("nrl-2022.json"),
            },
        )

        await run_nrl_historic_load(
            db, competitions=["nrl"], seasons=[2017, 2022]
        )

        rows = (
            await db.execute(
                select(Season)
                .join(Competition, Competition.id == Season.competition_id)
                .where(Competition.name == "National Rugby League")
            )
        ).scalars()
        assert all(season.is_current is False for season in rows)
