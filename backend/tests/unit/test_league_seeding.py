"""Rugby-league identity seeding tests (Phase 5.2, ADR 0001).

The identity set for rugby-league: the ``rugby-league`` sport row, the
three competitions (NRL / NRLW / State of Origin), the 17 NRL clubs plus
the two Origin representative teams as team ``Participant`` rows with
full-name aliases, and per-ground timezones built on the canonical
venue alias table.

NO-DB unit tests: the identity tables and resolution logic are pure;
the seed orchestration is exercised against mocked get-or-create
primitives (the AsyncMock pattern of ``test_competition_crud.py``), so
nothing here touches a database.  Idempotency contract: the seed only
composes get-or-create primitives, so a re-run issues the SAME call
sequence and duplicates nothing.
"""

from __future__ import annotations

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from packages.shared.crud.competitions import CompetitionCRUD
from packages.shared.crud.multisport import ParticipantResolver
from packages.shared.ingestion.league_seeding import (
    CLUBS,
    COMPETITIONS,
    MIN_SEASON,
    NICKNAME_ALIASES,
    ORIGIN_TEAMS,
    SPORT_DISPLAY_NAME,
    SPORT_ID,
    TEAMS,
    VENUE_TIMEZONES,
    UnknownTeamError,
    ensure_sport,
    resolve_nickname,
    season_years,
    seed_league_identity,
    venue_timezone,
)
from packages.shared.ingestion.nrl_provider import NrlProvider
from packages.shared.ingestion.venue_aliases import CANONICAL_VENUES

#: The feed nicknames, live-verified 2026-10-09 (see the session bundle).
EXPECTED_CLUB_NICKNAMES = (
    "Broncos",
    "Bulldogs",
    "Cowboys",
    "Dolphins",
    "Dragons",
    "Eels",
    "Knights",
    "Panthers",
    "Rabbitohs",
    "Raiders",
    "Roosters",
    "Sea Eagles",
    "Sharks",
    "Storm",
    "Titans",
    "Warriors",
    "Wests Tigers",
)


# ---------------------------------------------------------------------------
# Pure identity tables
# ---------------------------------------------------------------------------


class TestSportIdentity:
    def test_sport_id_and_display_name(self):
        assert SPORT_ID == "rugby-league"
        assert SPORT_DISPLAY_NAME == "Rugby League"

    def test_sport_id_matches_the_declared_sport_context(self):
        from packages.shared.sport_context import RUGBY_LEAGUE

        assert SPORT_ID == RUGBY_LEAGUE.sport_id
        assert SPORT_DISPLAY_NAME == RUGBY_LEAGUE.display_name


class TestCompetitions:
    def test_three_competitions_declared(self):
        assert set(COMPETITIONS) == {"nrl", "nrlw", "origin"}

    def test_nrl_and_nrlw_are_national_rounds_competitions(self):
        assert COMPETITIONS["nrl"].tier == "national"
        assert COMPETITIONS["nrl"].format == "rounds"
        assert COMPETITIONS["nrlw"].tier == "national"
        assert COMPETITIONS["nrlw"].format == "rounds"

    def test_origin_is_a_national_short_series(self):
        assert COMPETITIONS["origin"].tier == "national"

    def test_formats_satisfy_the_schema_check_constraint(self):
        # competitions.format: CHECK (format IN ('rounds', 'tournament'))
        for identity in COMPETITIONS.values():
            assert identity.format in {"rounds", "tournament"}
            assert identity.tier in {"national", "state", "local"}

    def test_every_competition_declares_a_timezone(self):
        for identity in COMPETITIONS.values():
            assert identity.timezone
        # The sport's cron anchor (bundle decision): NRL HQ, Brisbane.
        assert COMPETITIONS["nrl"].timezone == "Australia/Brisbane"

    def test_keys_match_the_provider_competition_slugs(self):
        """The seeded competitions and the feed slugs must stay in
        lockstep — a competition without a feed (or vice versa) is a
        configuration drift this test pins."""
        assert set(COMPETITIONS) == set(NrlProvider.COMPETITION_SLUGS)


class TestParticipants:
    def test_exactly_the_17_clubs_are_declared(self):
        assert [t.name for t in CLUBS] == list(EXPECTED_CLUB_NICKNAMES)

    def test_origin_adds_blues_and_maroons_only(self):
        assert [t.name for t in ORIGIN_TEAMS] == ["Blues", "Maroons"]
        assert len(TEAMS) == 19

    def test_canonical_names_are_the_feed_nicknames(self):
        """The participant name IS the name the feed sends, so the sync
        path resolves at the exact-name step and never falls into the
        AFL ``canonical_team()`` fallback (which owns 'Bulldogs' →
        'Western Bulldogs' — a cross-sport hijack)."""
        for team in TEAMS:
            assert resolve_nickname(team.name) == team.name

    def test_nrlw_reuses_the_club_identities(self):
        """One team set serves nrl and nrlw alike — no women's-suffixed
        duplicate participants."""
        names = [t.name for t in TEAMS]
        assert len(names) == len(set(names))
        assert not any("nrlw" in n.lower() or "women" in n.lower() for n in names)


# ---------------------------------------------------------------------------
# Nickname → participant resolution
# ---------------------------------------------------------------------------


class TestNicknameResolution:
    @pytest.mark.parametrize(
        ("nickname", "full_name"),
        [
            ("Broncos", "Brisbane Broncos"),
            ("Bulldogs", "Canterbury-Bankstown Bulldogs"),
            ("Cowboys", "North Queensland Cowboys"),
            ("Dolphins", "Redcliffe Dolphins"),
            ("Dragons", "St George Illawarra Dragons"),
            ("Eels", "Parramatta Eels"),
            ("Knights", "Newcastle Knights"),
            ("Panthers", "Penrith Panthers"),
            ("Rabbitohs", "South Sydney Rabbitohs"),
            ("Raiders", "Canberra Raiders"),
            ("Roosters", "Sydney Roosters"),
            ("Sea Eagles", "Manly Warringah Sea Eagles"),
            ("Sharks", "Cronulla-Sutherland Sharks"),
            ("Storm", "Melbourne Storm"),
            ("Titans", "Gold Coast Titans"),
            ("Warriors", "New Zealand Warriors"),
            ("Wests Tigers", None),
        ],
    )
    def test_nickname_and_full_club_name_resolve_to_one_participant(
        self, nickname: str, full_name
    ):
        assert resolve_nickname(nickname) == nickname
        if full_name is not None:
            assert resolve_nickname(full_name) == nickname

    def test_origin_full_names_resolve(self):
        assert resolve_nickname("NSW Blues") == "Blues"
        assert resolve_nickname("New South Wales Blues") == "Blues"
        assert resolve_nickname("QLD Maroons") == "Maroons"
        assert resolve_nickname("Queensland Maroons") == "Maroons"

    def test_resolution_ignores_case_and_whitespace(self):
        assert resolve_nickname("  broncos ") == "Broncos"
        assert resolve_nickname("SOUTH SYDNEY RABBITOHS") == "Rabbitohs"

    def test_bulldogs_never_resolves_to_the_afl_club(self):
        """teams.py's AFL alias map owns 'Bulldogs' → 'Western
        Bulldogs'; rugby-league resolution is scoped to its own table
        and the AFL name must fail loudly here."""
        assert resolve_nickname("Bulldogs") == "Bulldogs"
        with pytest.raises(UnknownTeamError):
            resolve_nickname("Western Bulldogs")

    @pytest.mark.parametrize(
        "unknown",
        [
            "Carlton",  # AFL — must not bleed across sports
            "Fremantle",  # AFL
            "Tigers",  # ambiguous short form — no silent match
            "Unknown FC",
            "",
            "   ",
        ],
    )
    def test_unknown_or_ambiguous_names_fail_loudly(self, unknown: str):
        with pytest.raises(UnknownTeamError):
            resolve_nickname(unknown)

    def test_none_name_fails_loudly(self):
        with pytest.raises(UnknownTeamError):
            resolve_nickname(None)

    def test_failure_message_names_the_offender(self):
        with pytest.raises(UnknownTeamError, match="Carlton"):
            resolve_nickname("Carlton")

    def test_unknown_team_error_is_a_value_error(self):
        # Callers may catch the conventional base class; the dedicated
        # type just makes the loud failure catchable precisely.
        assert issubclass(UnknownTeamError, ValueError)

    def test_alias_table_has_no_ambiguous_entries(self):
        owners: dict = {}
        for team in TEAMS:
            for name in (team.name, *team.aliases):
                owners.setdefault(name.lower(), set()).add(team.name)
        assert all(len(owners_set) == 1 for owners_set in owners.values())

    def test_every_declared_alias_is_in_the_public_map(self):
        for team in TEAMS:
            for name in (team.name, *team.aliases):
                assert NICKNAME_ALIASES[name] == team.name


# ---------------------------------------------------------------------------
# Venue timezones (canonical grounds from the venue alias table)
# ---------------------------------------------------------------------------


class TestVenueTimezones:
    def test_table_covers_exactly_the_canonical_grounds(self):
        """Every canonical ground carries a timezone and the table has
        no strays — import-time validation enforces this too."""
        assert set(VENUE_TIMEZONES) == set(CANONICAL_VENUES)

    def test_the_warriors_ground_is_pacific_auckland(self):
        # Warriors matches are the reason per-competition timezone
        # alone is insufficient (bundle decision).
        assert VENUE_TIMEZONES["Mount Smart Stadium"] == "Pacific/Auckland"

    @pytest.mark.parametrize(
        ("sponsor_alias", "expected"),
        [
            ("Go Media Stadium", "Pacific/Auckland"),
            ("Mt Smart Stadium", "Pacific/Auckland"),
            ("One NZ Stadium", "Pacific/Auckland"),
            ("PointsBet Stadium", "Australia/Sydney"),
            ("Ocean Protect Stadium", "Australia/Sydney"),
            ("Suncorp Stadium", "Australia/Brisbane"),
            ("AAMI Park", "Australia/Melbourne"),
            ("Optus Stadium", "Australia/Perth"),
            ("HBF Park", "Australia/Perth"),
        ],
    )
    def test_sponsor_aliases_carry_the_ground_timezone(
        self, sponsor_alias: str, expected: str
    ):
        assert venue_timezone(sponsor_alias) == expected

    def test_canonical_ground_resolves_directly(self):
        assert venue_timezone("Mount Smart Stadium") == "Pacific/Auckland"
        assert venue_timezone("Lang Park") == "Australia/Brisbane"

    def test_unknown_venue_has_no_timezone(self):
        assert venue_timezone("Nowhere Park 2026") is None

    def test_blank_and_none_have_no_timezone(self):
        assert venue_timezone(None) is None
        assert venue_timezone("   ") is None

    def test_every_timezone_is_a_valid_iana_zone(self):
        for tz_name in VENUE_TIMEZONES.values():
            ZoneInfo(tz_name)  # raises ZoneInfoNotFoundError when invalid


# ---------------------------------------------------------------------------
# Supported seasons
# ---------------------------------------------------------------------------


class TestSeasonYears:
    def test_first_supported_season_is_2017(self):
        assert MIN_SEASON == 2017

    def test_years_run_from_2017_through_the_given_year_inclusive(self):
        assert season_years(2020) == [2017, 2018, 2019, 2020]

    def test_through_year_before_the_minimum_fails_loudly(self):
        with pytest.raises(ValueError, match="2017"):
            season_years(2016)


# ---------------------------------------------------------------------------
# Seed orchestration (mocked get-or-create primitives — no DB)
# ---------------------------------------------------------------------------


def _result(scalar_return=None):
    r = MagicMock()
    r.scalar_one_or_none.return_value = scalar_return
    return r


def _db():
    """AsyncSession mock: sport lookup misses by default; add() records."""
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=_result(scalar_return=None))
    added: list = []
    db.add = MagicMock(side_effect=lambda obj: added.append(obj))
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db, added


class RecordingMocks:
    """Fresh call recorders + patched ensure_* primitives per run."""

    def __init__(self):
        self.comp_calls: list = []
        self.season_calls: list = []
        self.team_calls: list = []

    async def _ensure_competition(self, db, **kwargs):
        self.comp_calls.append(kwargs)
        return SimpleNamespace(id=100 + len(self.comp_calls), **kwargs)

    async def _ensure_season(self, db, **kwargs):
        self.season_calls.append(kwargs)
        return SimpleNamespace(id=200 + len(self.season_calls), **kwargs)

    async def _ensure_team(self, name, **kwargs):
        self.team_calls.append((name, kwargs))
        return SimpleNamespace(id=300 + len(self.team_calls), name=name, **kwargs)

    def patch(self):
        return [
            patch.object(
                CompetitionCRUD,
                "ensure_competition",
                AsyncMock(side_effect=self._ensure_competition),
            ),
            patch.object(
                CompetitionCRUD,
                "ensure_season",
                AsyncMock(side_effect=self._ensure_season),
            ),
            patch.object(
                ParticipantResolver,
                "ensure_team",
                AsyncMock(side_effect=self._ensure_team),
            ),
        ]

    def apply(self) -> ExitStack:
        """Enter every patch on a fresh stack — use as ``with mocks.apply():``."""
        stack = ExitStack()
        for p in self.patch():
            stack.enter_context(p)
        return stack

    def snapshot(self):
        return (self.comp_calls, self.season_calls, self.team_calls)


class TestEnsureSport:
    async def test_creates_the_sport_row_when_missing(self):
        db, added = _db()
        sport = await ensure_sport(db)
        assert sport.id == SPORT_ID
        assert sport.display_name == SPORT_DISPLAY_NAME
        assert added == [sport]
        db.flush.assert_awaited_once()

    async def test_returns_the_existing_sport_without_adding(self):
        db, added = _db()
        existing = SimpleNamespace(id=SPORT_ID, display_name=SPORT_DISPLAY_NAME)
        db.execute = AsyncMock(return_value=_result(scalar_return=existing))
        sport = await ensure_sport(db)
        assert sport is existing
        assert added == []


class TestSeedLeagueIdentity:
    async def test_seeds_sport_three_competitions_seasons_and_teams(self):
        db, _added = _db()
        mocks = RecordingMocks()
        with mocks.apply():
            stats = await seed_league_identity(db, through_year=2018)

        # Competitions: exactly the 3 declared identities, in key order.
        assert [c["name"] for c in mocks.comp_calls] == [
            "National Rugby League",
            "NRL Women's Premiership",
            "State of Origin",
        ]
        assert all(c["sport_id"] == SPORT_ID for c in mocks.comp_calls)
        assert [c["format"] for c in mocks.comp_calls] == [
            "rounds",
            "rounds",
            "tournament",
        ]
        assert [c["tier"] for c in mocks.comp_calls] == ["national"] * 3

        # Seasons: every competition × 2017..2018; only the latest year
        # of each competition is current.
        assert len(mocks.season_calls) == 6
        assert [s["competition_id"] for s in mocks.season_calls] == [
            101, 101, 102, 102, 103, 103,
        ]
        assert [s["label"] for s in mocks.season_calls] == [
            "2017", "2018", "2017", "2018", "2017", "2018",
        ]
        assert [s["is_current"] for s in mocks.season_calls] == [
            False, True, False, True, False, True,
        ]

        # Participants: the 19 team identities, in declaration order,
        # with their full-name aliases and NO invented identity data
        # (logo/colours stay NULL until a source supplies them).
        assert [name for name, _ in mocks.team_calls] == [t.name for t in TEAMS]
        assert len(mocks.team_calls) == 19
        for name, kwargs in mocks.team_calls:
            assert set(kwargs) == {"aliases"}
        aliases_by_name = {name: kwargs["aliases"] for name, kwargs in mocks.team_calls}
        assert aliases_by_name["Broncos"] == ("Brisbane Broncos",)
        assert aliases_by_name["Warriors"] == ("New Zealand Warriors", "NZ Warriors")
        assert aliases_by_name["Blues"] == (
            "NSW Blues",
            "New South Wales Blues",
            "New South Wales",
        )

        # The pass is committed and reported.
        db.commit.assert_awaited_once()
        assert stats["status"] == "success"
        assert stats["sport_id"] == SPORT_ID
        assert stats["competitions"] == ["nrl", "nrlw", "origin"]
        assert stats["season_years"] == [2017, 2018]
        assert stats["seasons_ensured"] == 6
        assert stats["participants_ensured"] == 19

    async def test_reseed_issues_an_identical_get_or_create_sequence(self):
        """Idempotency by construction: two runs issue byte-identical
        get-or-create calls; combined with the idempotent primitives
        (ensure_*), a re-run mutates nothing and duplicates nothing."""
        snapshots = []
        for _ in range(2):
            db, added = _db()
            mocks = RecordingMocks()
            with mocks.apply():
                await seed_league_identity(db, through_year=2026)
            # Exactly one row ever reaches db.add directly: the Sport
            # (everything else flows through the get-or-create
            # primitives, recorded above as calls).
            assert len(added) == 1
            snapshots.append(repr(mocks.snapshot()))
        assert snapshots[0] == snapshots[1]

    async def test_reseed_with_existing_rows_adds_nothing_new(self):
        db, added = _db()
        # Sport already on file → ensure_sport returns it, adds nothing.
        existing_sport = SimpleNamespace(id=SPORT_ID, display_name=SPORT_DISPLAY_NAME)
        db.execute = AsyncMock(return_value=_result(scalar_return=existing_sport))
        mocks = RecordingMocks()
        with mocks.apply():
            stats = await seed_league_identity(db, through_year=2026)
        assert stats["status"] == "success"
        assert added == []
        # The same ensure_* calls still run (they are get-or-create
        # no-ops against existing rows) — the sequence is unchanged.
        assert len(mocks.comp_calls) == 3
        assert len(mocks.team_calls) == 19

    async def test_default_through_year_comes_from_settings(self):
        db, _added = _db()
        mocks = RecordingMocks()
        fake_settings = SimpleNamespace(current_season=2018)
        with mocks.apply(), patch(
            "packages.shared.ingestion.league_seeding.settings", fake_settings
        ):
            stats = await seed_league_identity(db)
        assert stats["season_years"] == [2017, 2018]

    async def test_through_year_below_the_minimum_is_rejected(self):
        db, _added = _db()
        with pytest.raises(ValueError, match="2017"):
            await seed_league_identity(db, through_year=2016)
