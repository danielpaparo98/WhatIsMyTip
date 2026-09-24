"""PlayHQProvider tests â€” driven by the real NWFL 2026 fixture payload
captured live from api.playhq.com/graphql (grade
``c2721398`` = NWFL Premier League Senior Men, captured verbatim
2026-09-24):

* ``nwfl_fixture_2026.json`` â€” the complete real ``discoverGradeFixture``
  response (20 rounds â€” 16 home-and-away + 4 finals â€” 70 games, all
  FINAL), verbatim.

Variants the captured season does not exercise (byes, future games
without results, provisional teams, date-only scheduling, goals/behinds
fallbacks) are covered with shape-identical synthesized rounds built on
the real payload's field names â€” the iSports-fixture approach.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict

import pytest

from packages.shared.ingestion.playhq_provider import PlayHQProvider

_FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures", "playhq")
_NWFL_FIXTURE_PATH = os.path.join(_FIXTURES, "nwfl_fixture_2026.json")

# Real payloads (captured live 2026-09-24): the org â†’ competitions â†’
# season â†’ grades chain for the NWFL.  Note the NWFL organisation id
# happens to equal its routing code â€” the SFL's does NOT (code
# cc453fd4 â†’ id c81f5b0c), which is why the provider always resolves
# the id through discoverOrganisation.
_ORG_PAYLOAD: Dict[str, Any] = {
    "data": {
        "discoverOrganisation": {
            "id": "abb4e230",
            "type": "ASSOCIATION",
            "name": "North West Football League of Tasmania (NWFL)",
            "email": "kathy.brotherton@afl.com.au",
            "websiteUrl": "https://www.nwfl.com.au/",
        }
    }
}

_COMPETITIONS_PAYLOAD: Dict[str, Any] = {
    "data": {
        "discoverCompetitions": [
            {
                "id": "cafae1a9",
                "name": "North West Football League of Tasmania",
                "seasons": [
                    {
                        "id": "fe2594f3",
                        "name": "2026",
                        "startDate": "2025-10-01",
                        "endDate": "2026-09-30",
                        "status": {"name": "Active", "value": "ACTIVE"},
                    },
                    {
                        "id": "d0332cfc",
                        "name": "2025",
                        "startDate": "2024-11-01",
                        "endDate": "2025-10-31",
                        "status": {"name": "Completed", "value": "COMPLETED"},
                    },
                ],
            }
        ]
    }
}

_SEASON_PAYLOAD: Dict[str, Any] = {
    "data": {
        "discoverSeason": {
            "id": "fe2594f3",
            "name": "2026",
            "status": {"name": "Active", "value": "ACTIVE"},
            "grades": [
                {"id": "c2721398", "name": "NWFL Premier League Senior Men"},
                {
                    "id": "8d3142db",
                    "name": "Terry White Chemmart NWFL Premier League Senior Women",
                },
                {"id": "cc066704", "name": "Real Mates RSAC NWFL Colts"},
            ],
        }
    }
}


def _load_nwfl_fixture() -> Dict[str, Any]:
    with open(_NWFL_FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _make_provider(**kwargs) -> tuple[PlayHQProvider, list]:
    """Provider with a recording fake GraphQL transport wired to the
    real captured payloads."""
    calls: list = []
    fixture_payload = kwargs.pop("_fixture_payload", _load_nwfl_fixture())

    async def fake_query(query: str, variables: dict | None = None):
        calls.append((query, variables or {}))
        if "discoverOrganisation" in query:
            return _ORG_PAYLOAD
        if "discoverCompetitions" in query:
            return _COMPETITIONS_PAYLOAD
        if "discoverSeason" in query:
            return _SEASON_PAYLOAD
        if "discoverGradeFixture" in query:
            return fixture_payload
        raise AssertionError(f"unexpected query: {query[:80]}")

    provider = PlayHQProvider(
        organisation_routing_code="abb4e230",
        grade_ids={"NWFL Premier League Senior Men": "c2721398"},
        query=fake_query,
        **kwargs,
    )
    return provider, calls


def _synthetic_payload(rounds: list[dict]) -> Dict[str, Any]:
    """Wrap synthesized rounds in the real response envelope."""
    return {"data": {"discoverGradeFixture": rounds}}


def _stat(side: Dict[str, Any] | None, value: str) -> int | None:
    for entry in (side or {}).get("statistics") or []:
        if entry.get("type", {}).get("value") == value:
            return entry.get("count")
    return None


class TestPlayHQNWFL2026:
    @pytest.mark.asyncio
    async def test_parses_the_full_captured_season(self):
        provider, calls = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        assert len(fixtures) == 70
        # The chain ran in order: org â†’ competitions â†’ season â†’ fixture,
        # each queried exactly once.
        assert len(calls) == 4
        root_names = (
            "discoverOrganisation",
            "discoverCompetitions",
            "discoverSeason",
            "discoverGradeFixture",
        )
        roots = [next(w for w in root_names if w in q) for q, _ in calls]
        assert roots == [
            "discoverOrganisation",
            "discoverCompetitions",
            "discoverSeason",
            "discoverGradeFixture",
        ]
        assert calls[0][1] == {"code": "abb4e230"}
        assert calls[1][1] == {"organisationID": "abb4e230"}
        assert calls[2][1] == {"id": "fe2594f3"}
        assert calls[3][1] == {"gradeID": "c2721398"}

    @pytest.mark.asyncio
    async def test_round_1_opener_maps_correctly(self):
        """Game a5d73967 is verbatim in the captured payload."""
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        dto = next(f for f in fixtures if f.external_id == "a5d73967")
        assert dto.source == "playhq"
        assert dto.season == 2026
        assert dto.round_id == 1
        assert dto.home_participant == "East Devonport"
        assert dto.away_participant == "Circular Head"
        assert dto.home_score == 38
        assert dto.away_score == 117
        assert dto.venue == "Girdlestone Park"
        assert dto.starts_at == datetime(2026, 4, 11, 14, 0)
        assert dto.completed is True

    @pytest.mark.asyncio
    async def test_grand_final_maps_past_the_home_and_away_rounds(self):
        """Finals must land past any plausible H&A length (16 here)."""
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        dto = next(f for f in fixtures if f.external_id == "412ef059")
        assert dto.round_id == 104
        assert dto.home_participant == "Devonport"
        assert dto.away_participant == "Ulverstone"
        assert dto.home_score == 74
        assert dto.away_score == 60
        assert dto.completed is True

    @pytest.mark.asyncio
    async def test_finals_round_numbering_covers_every_finals_round(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        finals_rounds = {f.round_id for f in fixtures if f.round_id >= 100}
        assert finals_rounds == {101, 102, 103, 104}

    @pytest.mark.asyncio
    async def test_every_captured_game_is_completed_with_scores(self):
        """The 2026 NWFL season is finished â€” all 70 games FINAL."""
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        assert len(fixtures) == 70
        assert all(f.completed for f in fixtures)
        assert all(f.home_score is not None and f.away_score is not None for f in fixtures)

    @pytest.mark.asyncio
    async def test_round_ids_cover_sixteen_home_and_away_rounds(self):
        provider, _ = _make_provider()
        fixtures = await provider.get_fixtures(2026)

        assert {f.round_id for f in fixtures if f.round_id < 100} == set(range(1, 17))


class TestPayloadVariants:
    """Shape-identical synthesized rounds for variants the verbatim
    NWFL capture does not contain (its byes array is empty, every game
    is FINAL, and every game carries date + time)."""

    def _round(self, **overrides) -> dict:
        round_ = {
            "id": "dae82d3e",
            "name": "Round 1",
            "provisionalDates": [],
            "isFinalsRound": False,
            "grade": {"type": "REGULAR_SEASON", "hideScores": False},
            "byes": [],
            "games": [],
        }
        round_.update(overrides)
        return round_

    def _game(self, **overrides) -> dict:
        game = {
            "id": "deadbeef",
            "alias": None,
            "pool": None,
            "away": {"id": "138c5782", "name": "Circular Head"},
            "home": {"id": "1430fd20", "name": "East Devonport"},
            "result": {
                "winner": {"name": "Team B", "value": "AWAY"},
                "outcome": {"name": "Team A won by points", "value": "AWAY_TEAM_WON_BY_SCORE"},
                "home": {
                    "outcome": {"name": "Loss", "value": "LOST"},
                    "statistics": [
                        {"count": 6, "type": {"value": "TOTAL_GOALS"}},
                        {"count": 2, "type": {"value": "TOTAL_BEHINDS"}},
                        {"count": 38, "type": {"value": "TOTAL_SCORE"}},
                    ],
                },
                "away": {
                    "outcome": {"name": "Win", "value": "WON"},
                    "statistics": [
                        {"count": 17, "type": {"value": "TOTAL_GOALS"}},
                        {"count": 15, "type": {"value": "TOTAL_BEHINDS"}},
                        {"count": 117, "type": {"value": "TOTAL_SCORE"}},
                    ],
                },
            },
            "status": {"name": "Final", "value": "FINAL"},
            "date": "2026-04-11",
            "dates": ["2026-04-11"],
            "allocation": {
                "time": "14:00:00",
                "dateTimeList": [{"date": "2026-04-11", "time": "14:00:00"}],
                "court": {
                    "id": "court-1",
                    "name": "Girdlestone Park 1",
                    "venue": {"id": "v1", "name": "Girdlestone Park"},
                },
            },
            "isStale": False,
            "gameType": {"name": "Australian Rules", "value": "AUSTRALIAN_RULES"},
        }
        game.update(overrides)
        return game

    @pytest.mark.asyncio
    async def test_byes_never_become_fixtures(self):
        """Byes travel in the round's ``byes`` array, not as games â€”
        and a game with a missing side (TBC) is skipped too."""
        bye_round = self._round(
            byes=[{"id": "team-penguin", "name": "Penguin"}],
            games=[self._game()],
        )
        provider, _ = _make_provider(_fixture_payload=_synthetic_payload([bye_round]))
        fixtures = await provider.get_fixtures(2026)

        assert len(fixtures) == 1
        assert "Penguin" not in {f.home_participant for f in fixtures}
        assert "Penguin" not in {f.away_participant for f in fixtures}

    @pytest.mark.asyncio
    async def test_game_with_a_missing_side_is_skipped(self):
        tbc_round = self._round(games=[self._game(away=None)])
        provider, _ = _make_provider(_fixture_payload=_synthetic_payload([tbc_round]))
        fixtures = await provider.get_fixtures(2026)

        assert fixtures == []

    @pytest.mark.asyncio
    async def test_future_game_without_result_is_incomplete(self):
        future = self._round(
            name="Round 2",
            games=[
                self._game(
                    id="future01",
                    result=None,
                    status={"name": "Scheduled", "value": "SCHEDULED"},
                )
            ],
        )
        provider, _ = _make_provider(_fixture_payload=_synthetic_payload([future]))
        fixtures = await provider.get_fixtures(2026)

        assert len(fixtures) == 1
        assert fixtures[0].completed is False
        assert fixtures[0].home_score is None
        assert fixtures[0].away_score is None
        assert fixtures[0].round_id == 2

    @pytest.mark.asyncio
    async def test_scores_fall_back_to_goals_and_behinds(self):
        """No TOTAL_SCORE statistic â†’ compute goals Ã— 6 + behinds."""
        game = self._game(
            id="gbonly01",
            result={
                "winner": {"name": "Team A", "value": "HOME"},
                "outcome": {"name": "Team A won by points", "value": "HOME_TEAM_WON_BY_SCORE"},
                "home": {
                    "outcome": {"name": "Win", "value": "WON"},
                    "statistics": [
                        {"count": 10, "type": {"value": "TOTAL_GOALS"}},
                        {"count": 14, "type": {"value": "TOTAL_BEHINDS"}},
                    ],
                },
                "away": {
                    "outcome": {"name": "Loss", "value": "LOST"},
                    "statistics": [
                        {"count": 10, "type": {"value": "TOTAL_GOALS"}},
                        {"count": 0, "type": {"value": "TOTAL_BEHINDS"}},
                    ],
                },
            },
        )
        provider, _ = _make_provider(
            _fixture_payload=_synthetic_payload([self._round(games=[game])])
        )
        fixtures = await provider.get_fixtures(2026)

        assert fixtures[0].home_score == 74  # 10.14
        assert fixtures[0].away_score == 60  # 10.0
        assert fixtures[0].completed is True

    @pytest.mark.asyncio
    async def test_date_without_time_maps_to_midnight(self):
        game = self._game(
            id="dateonly1",
            allocation={
                "time": None,
                "dateTimeList": [],
                "court": {"name": "Girdlestone Park 1", "venue": None},
            },
        )
        provider, _ = _make_provider(
            _fixture_payload=_synthetic_payload([self._round(games=[game])])
        )
        fixtures = await provider.get_fixtures(2026)

        assert fixtures[0].starts_at == datetime(2026, 4, 11, 0, 0)
        # Venue falls back from court.venue.name to the court name.
        assert fixtures[0].venue == "Girdlestone Park 1"

    @pytest.mark.asyncio
    async def test_game_without_any_date_maps_to_none(self):
        game = self._game(id="nodate001", date=None, dates=[], allocation=None)
        provider, _ = _make_provider(
            _fixture_payload=_synthetic_payload([self._round(games=[game])])
        )
        fixtures = await provider.get_fixtures(2026)

        assert fixtures[0].starts_at is None

    @pytest.mark.asyncio
    async def test_provisional_team_name_still_maps(self):
        """Finals placeholders carry ProvisionalTeam (name only, no id)."""
        game = self._game(
            id="provfinal",
            home={"name": "TBC"},
        )
        finals_round = self._round(
            name="Preliminary Final",
            isFinalsRound=True,
            games=[game],
        )
        provider, _ = _make_provider(_fixture_payload=_synthetic_payload([finals_round]))
        fixtures = await provider.get_fixtures(2026)

        assert fixtures[0].home_participant == "TBC"
        assert fixtures[0].round_id == 103

    @pytest.mark.asyncio
    async def test_unnamed_finals_round_falls_back_to_finals_order(self):
        """A finals round the name map doesn't know still lands past
        the H&A rounds, numbered by its position among finals rounds."""
        game = self._game(id="oddfinal")
        odd = self._round(name="Finals Week 3", isFinalsRound=True, games=[game])
        provider, _ = _make_provider(_fixture_payload=_synthetic_payload([odd]))
        fixtures = await provider.get_fixtures(2026)

        assert fixtures[0].round_id == 101


class TestGradeSelection:
    @pytest.mark.asyncio
    async def test_grade_name_selector_resolves_the_exact_grade(self):
        provider, calls = _make_provider()
        provider.grade_ids = {}
        provider._grade_name = "NWFL Premier League Senior Men"

        fixtures = await provider.get_fixtures(2026)

        assert len(fixtures) == 70
        assert calls[3][1] == {"gradeID": "c2721398"}

    @pytest.mark.asyncio
    async def test_grade_name_selector_matches_by_substring(self):
        provider, calls = _make_provider()
        provider.grade_ids = {}
        provider._grade_name = "Premier League Senior Men"

        await provider.get_fixtures(2026)

        assert calls[3][1] == {"gradeID": "c2721398"}

    @pytest.mark.asyncio
    async def test_unknown_grade_name_raises(self):
        provider, _ = _make_provider()
        provider.grade_ids = {}
        provider._grade_name = "Under 9s Auskick"

        with pytest.raises(ValueError, match="Under 9s Auskick"):
            await provider.get_fixtures(2026)

    @pytest.mark.asyncio
    async def test_no_grade_configuration_raises(self):
        provider, _ = _make_provider()
        provider.grade_ids = {}
        provider._grade_name = None

        with pytest.raises(ValueError, match="grade"):
            await provider.get_fixtures(2026)


class TestPlayHQPlumbing:
    def test_sport_and_source_identity(self):
        provider, _ = _make_provider()
        assert provider.sport_id == "afl"
        assert provider.source == "playhq"

    @pytest.mark.asyncio
    async def test_unknown_season_raises(self):
        provider, _ = _make_provider()
        with pytest.raises(ValueError, match="1999"):
            await provider.get_fixtures(1999)

    @pytest.mark.asyncio
    async def test_get_fixture_is_not_offered(self):
        provider, _ = _make_provider()
        with pytest.raises(NotImplementedError):
            await provider.get_fixture("a5d73967")

    def test_default_headers_carry_the_preflight_requirements(self):
        provider = PlayHQProvider(
            organisation_routing_code="abb4e230",
            grade_ids={"NWFL Premier League Senior Men": "c2721398"},
        )
        headers = provider._headers()
        assert headers["Origin"] == "https://www.playhq.com"
        assert headers["Referer"] == "https://www.playhq.com/"
        assert headers["Apollo-Require-Preflight"] == "true"
        assert headers["tenant"] == "afl"
        assert headers["Content-Type"] == "application/json"
        assert "Authorization" not in headers

    def test_graphql_errors_surface_as_runtime_error(self):
        async def failing_query(query: str, variables: dict | None = None):
            return {"errors": [{"message": "grade not found"}]}

        provider = PlayHQProvider(
            organisation_routing_code="abb4e230",
            grade_ids={"NWFL Premier League Senior Men": "c2721398"},
            query=failing_query,
        )
        with pytest.raises(RuntimeError, match="grade not found"):
            asyncio.run(provider._discover_grade_fixture("c2721398"))

    def test_constructor_requires_a_grade_selection(self):
        with pytest.raises(ValueError, match="grade"):
            PlayHQProvider(organisation_routing_code="abb4e230")


class TestPlayHQRegistryWiring:
    """The two Tasmanian PlayHQ leagues wired live (mirrors
    test_state_leagues.py::TestLiveWiring)."""

    def test_tasmanian_leagues_are_live(self):
        from packages.shared.ingestion.state_leagues import STATE_LEAGUES

        for key, routing_code, grade_id in (
            ("nwfl", "abb4e230", "c2721398"),
            ("sfl", "cc453fd4", "879a3be0"),
        ):
            config = STATE_LEAGUES[key]
            assert config.status == "live", key
            assert config.provider_factory is not None, key
            provider = config.provider_factory()
            assert isinstance(provider, PlayHQProvider), key
            assert provider.organisation_routing_code == routing_code, key
            assert grade_id in provider.grade_ids.values(), key
            assert provider.source == "playhq", key
            assert (
                "playhq.com GraphQL (open, public discover API)"
                in config.source_note
            ), key

    def test_timezones_and_names_kept_per_league(self):
        from packages.shared.ingestion.state_leagues import STATE_LEAGUES

        assert STATE_LEAGUES["nwfl"].name == "North West Football League"
        assert STATE_LEAGUES["sfl"].name == "Southern Football League"
        assert STATE_LEAGUES["nwfl"].timezone == "Australia/Hobart"
        assert STATE_LEAGUES["sfl"].timezone == "Australia/Hobart"
