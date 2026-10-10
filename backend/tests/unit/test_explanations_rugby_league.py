"""Unit tests for AI explanations of rugby-league tips (nrl-expansion-12).

The rugby-league explanation path reuses the EXISTING OpenRouter
pipeline (``packages.shared.openrouter``) — import and wire, never
rewrite — with a per-sport prompt layer so the LLM speaks rugby league:

* the reduced factor set ONLY (elo, form, home_advantage, matchup) —
  the AFL-scrape models (weather_impact, injury_impact, player_form,
  value) are rejected at the registry AND never appear in any prompt
  evidence or generated output;
* SportConfig language: "Match" contests, "Round N" stages, competition
  display names (National Rugby League / NRL Women's Premiership /
  State of Origin), points scoring, and draws as a live outcome;
* cost discipline: explanations are cached under the rugby-league
  cache namespace so one tip pays for at most one OpenRouter call;
* graceful failure: a provider error can never break the tips — a
  single-tip generation surfaces the repo-standard ``BackendServiceError``,
  while the per-event batch degrades to tip-without-explanation;
* ADDITIVE: the AFL prompt paths stay byte-identical.

Structure mirrors the repo's testing conventions: pure prompt-building
tests with no DB, service tests over in-memory SQLite (aiosqlite)
against the REAL multisport tables, and API tests over a minimal
FastAPI app (the ``test_backtest_api_league.py`` builder pattern).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.exceptions import BackendServiceError
from packages.shared.models import (
    Competition,
    Event,
    EventParticipant,
    LeagueTip,
    Participant,
    Season,
    Sport,
)
from packages.shared.schemas.tips import (
    LeagueTipExplanationResponse,
    LeagueTipsResponse,
)
from packages.shared.services.explanation import (
    LeagueTipExplanationService,
    RugbyLeagueOpenRouterClient,
    build_league_match_context,
    format_league_prompt_context,
    league_fallback_explanation,
    rugby_league_system_prompt,
)
from packages.shared.services.league_heuristics import (
    RUGBY_LEAGUE_EXCLUDED_MODELS,
    RUGBY_LEAGUE_MODELS,
    CompletedEvent,
    compute_elo_ratings,
)

#: League key → canonical competition display name (the registry contract
#: pinned by test_backtest_api_league.py::TestRegistryContract).
COMPETITION_NAMES = {
    "nrl": "National Rugby League",
    "nrlw": "NRL Women's Premiership",
    "origin": "State of Origin",
}

#: AFL-scrape model names that must NEVER surface in rugby-league output.
_EXCLUDED_MARKERS = ("weather", "injur", "player_form", "value_impact")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(  # noqa: PLR0913 - test-data builder, explicitness wins
    event_id: int,
    *,
    home_participant_id: int,
    away_participant_id: int,
    home_score: Optional[int],
    away_score: Optional[int],
    starts_at: Optional[datetime],
    season_id: int = 1,
    home_side_id: int = 0,
    away_side_id: int = 0,
) -> CompletedEvent:
    """One ``CompletedEvent`` in pick-function shape (pure, no DB)."""
    return CompletedEvent(
        event_id=event_id,
        season_id=season_id,
        starts_at=starts_at,
        home_side_id=home_side_id or (1000 + event_id * 2),
        away_side_id=away_side_id or (1000 + event_id * 2 + 1),
        home_participant_id=home_participant_id,
        away_participant_id=away_participant_id,
        home_score=home_score,
        away_score=away_score,
    )


def _history(home_id: int = 1, away_id: int = 2) -> list[CompletedEvent]:
    """Six decided/drawn meetings between two sides, chronological.

    Results (home view): W, D, L, W, D, L — so over the last five the
    home side is 1 win, 2 draws, 2 losses and the away side 2-2-1, and
    the all-time head-to-head is 2-2 with 2 draws.
    """
    results = [(20, 10), (12, 12), (8, 16), (30, 0), (4, 4), (10, 20)]
    return [
        _event(
            i + 1,
            home_participant_id=home_id,
            away_participant_id=away_id,
            home_score=h,
            away_score=a,
            starts_at=datetime(2026, 3, 1 + i, 20, 0),
        )
        for i, (h, a) in enumerate(results)
    ]


class _MemoryCache:
    """Dict-backed stand-in for the Redis cache (no Redis in unit tests)."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        self.store[key] = value


# ---------------------------------------------------------------------------
# Prompt construction — the sport-aware system prompt
# ---------------------------------------------------------------------------


class TestRugbyLeagueSystemPrompt:
    """The system prompt speaks rugby league for the given competition."""

    @pytest.mark.parametrize("league,competition_name", COMPETITION_NAMES.items())
    def test_names_the_competition(self, league, competition_name):
        prompt = rugby_league_system_prompt(competition_name)
        assert competition_name in prompt

    def test_names_the_sport(self):
        prompt = rugby_league_system_prompt(COMPETITION_NAMES["nrl"])
        assert "rugby league" in prompt.lower()

    def test_uses_sportconfig_nouns_match_and_round(self):
        prompt = rugby_league_system_prompt(COMPETITION_NAMES["nrl"])
        assert "match" in prompt.lower()
        assert "round" in prompt.lower()

    def test_treats_draws_as_a_live_outcome(self):
        prompt = rugby_league_system_prompt(COMPETITION_NAMES["nrl"])
        assert "draw" in prompt.lower()

    def test_scores_in_points(self):
        prompt = rugby_league_system_prompt(COMPETITION_NAMES["nrl"])
        assert "points" in prompt.lower()

    def test_names_only_the_reduced_factor_set(self):
        prompt = rugby_league_system_prompt(COMPETITION_NAMES["nrl"])
        for signal in ("elo", "form", "home advantage", "head-to-head"):
            assert signal in prompt.lower()

    def test_no_afl_jargon_and_no_excluded_models_named(self):
        prompt = rugby_league_system_prompt(COMPETITION_NAMES["nrl"]).lower()
        for jargon in ("afl", "behind", "bounce", "goal"):
            assert jargon not in prompt, f"AFL jargon {jargon!r} leaked into prompt"
        for marker in _EXCLUDED_MARKERS:
            assert marker not in prompt, (
                f"excluded model marker {marker!r} leaked into prompt"
            )


# ---------------------------------------------------------------------------
# Prompt construction — the reduced-set evidence builder (pure)
# ---------------------------------------------------------------------------


class TestBuildLeagueMatchContext:
    """Evidence comes from the SAME pure functions that made the pick."""

    def test_contains_only_reduced_factor_set_keys(self):
        evidence = build_league_match_context(
            _history(), home_participant_id=1, away_participant_id=2
        )
        assert set(evidence) <= {"elo", "form", "head_to_head", "home_advantage"}

    def test_excluded_models_never_appear_as_evidence(self):
        evidence = build_league_match_context(
            _history(), home_participant_id=1, away_participant_id=2
        )
        for excluded in RUGBY_LEAGUE_EXCLUDED_MODELS:
            assert excluded not in evidence
        for marker in ("weather", "injuries", "injury"):
            assert marker not in evidence

    def test_elo_section_matches_the_result_derived_ratings(self):
        history = _history()
        evidence = build_league_match_context(
            history, home_participant_id=1, away_participant_id=2
        )
        expected = compute_elo_ratings(history)
        elo = evidence["elo"]
        assert elo["home"] == round(expected[1])
        assert elo["away"] == round(expected[2])
        assert elo["diff"] == round(expected[1]) - round(expected[2])

    def test_form_counts_wins_draws_losses_over_the_window(self):
        evidence = build_league_match_context(
            _history(), home_participant_id=1, away_participant_id=2
        )
        home_form = evidence["form"]["home"]
        away_form = evidence["form"]["away"]
        # Last five: home 1W-2D-2L, away 2W-2D-1L (draws are NOT losses).
        assert home_form == {"games": 5, "wins": 1, "draws": 2, "losses": 2}
        assert away_form == {"games": 5, "wins": 2, "draws": 2, "losses": 1}

    def test_form_window_is_capped_at_five_matches(self):
        history = _history() + [
            _event(
                7,
                home_participant_id=1,
                away_participant_id=2,
                home_score=50,
                away_score=0,
                starts_at=datetime(2026, 3, 20, 20, 0),
            )
        ]
        evidence = build_league_match_context(
            history, home_participant_id=1, away_participant_id=2
        )
        assert evidence["form"]["home"]["games"] == 5

    def test_head_to_head_is_pair_scoped_and_draw_aware(self):
        evidence = build_league_match_context(
            _history(), home_participant_id=1, away_participant_id=2
        )
        assert evidence["head_to_head"] == {
            "games": 6,
            "home_wins": 2,
            "away_wins": 2,
            "draws": 2,
        }

    def test_home_advantage_is_registered_for_the_sport(self):
        evidence = build_league_match_context(
            _history(), home_participant_id=1, away_participant_id=2
        )
        assert evidence["home_advantage"] is True

    def test_empty_history_yields_no_sections_and_no_crash(self):
        evidence = build_league_match_context(
            [], home_participant_id=1, away_participant_id=2
        )
        assert "elo" not in evidence
        assert "form" not in evidence
        assert "head_to_head" not in evidence


# ---------------------------------------------------------------------------
# Prompt construction — the user-context formatter (pure)
# ---------------------------------------------------------------------------


class TestFormatLeaguePromptContext:
    """The user prompt names competition, Round/Match nouns and the pick."""

    def _format(self, *, picked: Optional[str] = "Knights", **overrides) -> str:
        kwargs: dict[str, Any] = {
            "competition_name": COMPETITION_NAMES["nrl"],
            "round_id": 7,
            "home_team": "Broncos",
            "away_team": "Knights",
            "venue": "Suncorp Stadium",
            "kickoff": "2026-04-10T20:00:00",
            "heuristic": "elo",
            "picked": picked,
            "evidence": build_league_match_context(
                _history(), home_participant_id=1, away_participant_id=2
            ),
        }
        kwargs.update(overrides)
        return format_league_prompt_context(**kwargs)

    def test_names_competition_round_and_match(self):
        context = self._format()
        assert COMPETITION_NAMES["nrl"] in context
        assert "Round 7" in context
        assert "Match" in context

    def test_names_teams_venue_and_kickoff(self):
        context = self._format()
        assert "Broncos" in context
        assert "Knights" in context
        assert "Suncorp Stadium" in context
        assert "2026-04-10T20:00:00" in context

    def test_names_the_model_and_the_pick(self):
        context = self._format(picked="Knights", heuristic="elo")
        assert "elo" in context
        assert "Pick: Knights" in context

    def test_draw_pick_is_expressed_in_rugby_league_terms(self):
        context = self._format(picked=None)
        assert "draw" in context.lower()

    def test_renders_each_reduced_factor_line(self):
        context = self._format()
        assert "Elo ratings:" in context
        assert "Recent form (last 5 matches):" in context
        assert "Head-to-head" in context
        assert "Home advantage: Broncos" in context

    def test_scoring_unit_is_points_and_draws_acknowledged(self):
        context = self._format()
        assert "points" in context.lower()
        assert "draw" in context.lower()

    def test_omits_evidence_lines_without_data(self):
        context = self._format(evidence={})
        assert "Elo ratings:" not in context
        assert "Recent form" not in context
        assert "Head-to-head" not in context

    def test_no_excluded_models_or_afl_jargon_anywhere(self):
        context = self._format().lower()
        for marker in _EXCLUDED_MARKERS:
            assert marker not in context
        for jargon in ("afl", "behind", "bounce"):
            assert jargon not in context


# ---------------------------------------------------------------------------
# Deterministic fallback (no-API-key / provider-failure degradation)
# ---------------------------------------------------------------------------


class TestLeagueFallbackExplanation:
    """The template fallback speaks league and never names excluded models."""

    def _fallback(self, heuristic: str, picked: Optional[str] = "Broncos") -> str:
        return league_fallback_explanation(
            competition_name=COMPETITION_NAMES["nrl"],
            home_team="Broncos",
            away_team="Knights",
            heuristic=heuristic,
            picked=picked,
            evidence=build_league_match_context(
                _history(), home_participant_id=1, away_participant_id=2
            ),
        )

    @pytest.mark.parametrize("heuristic", sorted(RUGBY_LEAGUE_MODELS))
    def test_every_reduced_model_gets_a_pick_aware_fallback(self, heuristic):
        text = self._fallback(heuristic)
        assert "Broncos" in text
        assert len(text) > 0

    def test_draw_fallback_mentions_the_draw(self):
        text = self._fallback("elo", picked=None)
        assert "draw" in text.lower()

    def test_elo_fallback_cites_the_rating_edge(self):
        text = self._fallback("elo")
        assert "Elo" in text
        assert "point" in text.lower()

    def test_form_fallback_cites_the_record(self):
        text = self._fallback("form")
        assert "form" in text.lower()

    def test_matchup_fallback_cites_head_to_head(self):
        text = self._fallback("matchup")
        assert "head-to-head" in text.lower()

    def test_home_advantage_fallback_cites_the_home_side(self):
        text = self._fallback("home_advantage")
        assert "home" in text.lower()

    def test_never_mentions_excluded_models_or_afl_jargon(self):
        for heuristic in RUGBY_LEAGUE_MODELS:
            text = self._fallback(heuristic).lower()
            for marker in _EXCLUDED_MARKERS:
                assert marker not in text
            for jargon in ("afl", "behind", "bounce"):
                assert jargon not in text

    def test_empty_evidence_still_produces_text(self):
        text = league_fallback_explanation(
            competition_name=COMPETITION_NAMES["nrl"],
            home_team="Broncos",
            away_team="Knights",
            heuristic="elo",
            picked="Broncos",
            evidence={},
        )
        assert "Broncos" in text


# ---------------------------------------------------------------------------
# The OpenRouter client subclass — reuse, not rewrite
# ---------------------------------------------------------------------------


class TestRugbyLeagueOpenRouterClient:
    def test_is_an_openrouter_client_subclass(self):
        from packages.shared.openrouter import OpenRouterClient

        assert issubclass(RugbyLeagueOpenRouterClient, OpenRouterClient)

    def test_system_prompt_is_sport_aware_per_competition(self):
        client = RugbyLeagueOpenRouterClient(COMPETITION_NAMES["origin"])
        prompt = client._get_system_prompt()
        assert COMPETITION_NAMES["origin"] in prompt
        assert "rugby league" in prompt.lower()

    def test_prompt_context_is_sport_aware(self):
        client = RugbyLeagueOpenRouterClient(COMPETITION_NAMES["nrl"])
        context = client._build_prompt_context(
            game={
                "home_team": "Broncos",
                "away_team": "Knights",
                "venue": "Suncorp Stadium",
                "date": "2026-04-10T20:00:00",
                "round_id": 7,
            },
            prediction={"winner": "Broncos", "margin": None, "confidence": None},
            heuristic="elo",
            model_predictions=None,
            match_context=build_league_match_context(
                _history(), home_participant_id=1, away_participant_id=2
            ),
        )
        assert COMPETITION_NAMES["nrl"] in context
        assert "Round 7" in context
        assert "Broncos" in context

    async def test_missing_api_key_degrades_to_league_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from packages.shared.config import settings

        monkeypatch.setattr(settings, "openrouter_api_key", "")
        client = RugbyLeagueOpenRouterClient(COMPETITION_NAMES["nrl"])
        text = await client.generate_explanation(
            game={"home_team": "Broncos", "away_team": "Knights"},
            prediction={"winner": "Broncos", "margin": None, "confidence": None},
            heuristic="elo",
            match_context={},
        )
        assert "Broncos" in text

    async def test_llm_response_flows_through_the_inherited_path(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from packages.shared.config import settings

        monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
        client = RugbyLeagueOpenRouterClient(COMPETITION_NAMES["nrl"])

        sent: dict[str, Any] = {}

        class _Message:
            content = "The Elo gap says Broncos."

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]

        class _Completions:
            async def create(self, **kwargs):
                sent.update(kwargs)
                return _Response()

        class _Chat:
            completions = _Completions()

        class _FakeAsyncOpenAI:
            chat = _Chat()

        client.client = _FakeAsyncOpenAI()
        text = await client.generate_explanation(
            game={"home_team": "Broncos", "away_team": "Knights"},
            prediction={"winner": "Broncos", "margin": None, "confidence": None},
            heuristic="elo",
            match_context={},
        )

        assert text == "The Elo gap says Broncos."
        # The inherited call signature (model/token budget) is untouched.
        assert sent["model"] == settings.openrouter_model
        assert sent["max_tokens"] == 300
        # ...and the system message it sent is the rugby-league one.
        assert COMPETITION_NAMES["nrl"] in sent["messages"][0]["content"]

    async def test_provider_exception_degrades_to_fallback_never_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from packages.shared.config import settings

        monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
        client = RugbyLeagueOpenRouterClient(COMPETITION_NAMES["nrl"])

        class _BoomCompletions:
            async def create(self, **kwargs):
                raise RuntimeError("OpenRouter 500")

        class _BoomChat:
            completions = _BoomCompletions()

        class _BoomClient:
            chat = _BoomChat()

        client.client = _BoomClient()
        text = await client.generate_explanation(
            game={"home_team": "Broncos", "away_team": "Knights"},
            prediction={"winner": "Broncos", "margin": None, "confidence": None},
            heuristic="form",
            match_context={},
        )
        assert "Broncos" in text  # fallback, not a raise


class TestAFLPathsByteIdentical:
    """ADDITIVE guarantee: the AFL prompt machinery is untouched."""

    def test_afl_system_prompt_unchanged(self):
        base = object.__new__(RugbyLeagueOpenRouterClient.__mro__[1])
        prompt = base._get_system_prompt()
        assert prompt.startswith("You are an AFL footy tipping expert")
        assert "rugby league" not in prompt.lower()

    def test_afl_prompt_context_unchanged(self):
        base = object.__new__(RugbyLeagueOpenRouterClient.__mro__[1])
        context = base._build_prompt_context(
            game={
                "home_team": "Brisbane",
                "away_team": "Collingwood",
                "venue": "Gabba",
            },
            prediction={"winner": "Brisbane", "margin": 12, "confidence": 0.75},
            heuristic="best_bet",
            model_predictions=None,
            match_context=None,
        )
        assert "to win by 12 points" in context
        assert "Interpret this tip:" in context

    def test_legacy_tips_allowlist_unchanged(self):
        from app.api.tips import VALID_HEURISTICS

        assert VALID_HEURISTICS == [
            "best_bet",
            "weighted_tip",
            "yolo",
            "boosted_tip",
        ]


# ---------------------------------------------------------------------------
# The explanation service over the REAL multisport tables (aiosqlite)
# ---------------------------------------------------------------------------


async def _seed_rugby_league(db) -> dict[str, int]:
    """Seed one rugby-league competition with history + an upcoming match."""
    db.add(Sport(id="rugby-league", display_name="Rugby League"))
    competition = Competition(
        sport_id="rugby-league",
        name=COMPETITION_NAMES["nrl"],
        tier="national",
        format="rounds",
        timezone="Australia/Brisbane",
    )
    db.add(competition)
    await db.flush()

    season = Season(competition_id=competition.id, label="2026", is_current=True)
    db.add(season)
    await db.flush()

    broncos = Participant(sport_id="rugby-league", kind="team", name="Broncos")
    knights = Participant(sport_id="rugby-league", kind="team", name="Knights")
    db.add_all([broncos, knights])
    await db.flush()

    # Six completed meetings (home view: W, D, L, W, D, L), then one
    # upcoming match in Round 7 carrying the league tips.
    results = [(20, 10), (12, 12), (8, 16), (30, 0), (4, 4), (10, 20)]
    for i, (home_score, away_score) in enumerate(results):
        event = Event(
            season_id=season.id,
            event_type="match",
            round_id=i + 1,
            venue="Suncorp Stadium",
            starts_at=datetime(2026, 3, 1 + i, 20, 0),
            status="completed",
            completed=True,
            slug=f"r{i + 1}bkn",
        )
        db.add(event)
        await db.flush()
        home_side = EventParticipant(
            event_id=event.id,
            participant_id=broncos.id,
            side="home",
            score=home_score,
        )
        away_side = EventParticipant(
            event_id=event.id,
            participant_id=knights.id,
            side="away",
            score=away_score,
        )
        db.add_all([home_side, away_side])
        await db.flush()

    upcoming = Event(
        season_id=season.id,
        event_type="match",
        round_id=7,
        venue="Suncorp Stadium",
        starts_at=datetime(2026, 4, 10, 20, 0),
        status="scheduled",
        completed=False,
        slug="r7bkn",
    )
    db.add(upcoming)
    await db.flush()
    upcoming_home = EventParticipant(
        event_id=upcoming.id, participant_id=broncos.id, side="home", score=None
    )
    upcoming_away = EventParticipant(
        event_id=upcoming.id, participant_id=knights.id, side="away", score=None
    )
    db.add_all([upcoming_home, upcoming_away])
    await db.flush()

    picks = {
        "elo": upcoming_home.id,
        "form": upcoming_away.id,
        "home_advantage": upcoming_home.id,
        "matchup": upcoming_home.id,
    }
    for heuristic, side_id in picks.items():
        db.add(
            LeagueTip(
                event_id=upcoming.id,
                heuristic=heuristic,
                selected_participant_id=side_id,
                competition_id=competition.id,
                season_id=season.id,
            )
        )
    await db.commit()

    return {
        "competition_id": competition.id,
        "season_id": season.id,
        "event_id": upcoming.id,
        "broncos_id": broncos.id,
        "knights_id": knights.id,
    }


@pytest.fixture
async def db():
    """Fresh in-memory SQLite session over the generic multisport schema."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for table in (
            Sport.__table__,
            Competition.__table__,
            Season.__table__,
            Participant.__table__,
            Event.__table__,
            EventParticipant.__table__,
            LeagueTip.__table__,
        ):
            await conn.run_sync(lambda sync_conn, t=table: t.create(sync_conn))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def memory_cache() -> _MemoryCache:
    return _MemoryCache()


def _fake_client(text: str = "The signals say Broncos.") -> AsyncMock:
    client = AsyncMock()
    client.generate_explanation = AsyncMock(return_value=text)
    return client


class TestLeagueTipExplanationService:
    async def test_generates_and_returns_the_explanation(
        self, db, memory_cache
    ):
        ids = await _seed_rugby_league(db)
        service = LeagueTipExplanationService(
            client=_fake_client(), cache=memory_cache
        )
        text = await service.generate_explanation_for_tip(
            db,
            competition_id=ids["competition_id"],
            event_id=ids["event_id"],
            heuristic="elo",
        )
        assert text == "The signals say Broncos."

    async def test_second_call_is_a_cache_hit_not_a_second_llm_call(
        self, db, memory_cache
    ):
        ids = await _seed_rugby_league(db)
        client = _fake_client()
        service = LeagueTipExplanationService(client=client, cache=memory_cache)
        await service.generate_explanation_for_tip(
            db,
            competition_id=ids["competition_id"],
            event_id=ids["event_id"],
            heuristic="elo",
        )
        # A BRAND-NEW service (fresh process simulation) hits the cache.
        second_client = _fake_client("SHOULD NOT BE RETURNED")
        second = LeagueTipExplanationService(
            client=second_client, cache=memory_cache
        )
        text = await second.generate_explanation_for_tip(
            db,
            competition_id=ids["competition_id"],
            event_id=ids["event_id"],
            heuristic="elo",
        )
        assert text == "The signals say Broncos."
        second_client.generate_explanation.assert_not_called()

    async def test_cache_key_lives_in_the_rugby_league_namespace(
        self, db, memory_cache
    ):
        ids = await _seed_rugby_league(db)
        service = LeagueTipExplanationService(
            client=_fake_client(), cache=memory_cache
        )
        await service.generate_explanation_for_tip(
            db,
            competition_id=ids["competition_id"],
            event_id=ids["event_id"],
            heuristic="form",
        )
        assert any(
            key.startswith("wimt:rugby-league:") for key in memory_cache.store
        )

    async def test_excluded_model_is_rejected_with_repo_standard_shape(
        self, db, memory_cache
    ):
        ids = await _seed_rugby_league(db)
        service = LeagueTipExplanationService(
            client=_fake_client(), cache=memory_cache
        )
        for excluded in sorted(RUGBY_LEAGUE_EXCLUDED_MODELS):
            with pytest.raises(BackendServiceError) as excinfo:
                await service.generate_explanation_for_tip(
                    db,
                    competition_id=ids["competition_id"],
                    event_id=ids["event_id"],
                    heuristic=excluded,
                )
            assert excinfo.value.status_code == 400
            assert excinfo.value.code == "model_unavailable_for_sport"
            assert excinfo.value.details["model"] == excluded
            assert excinfo.value.details["sport_id"] == "rugby-league"
            assert sorted(excinfo.value.details["available"]) == sorted(
                RUGBY_LEAGUE_MODELS
            )

    async def test_unknown_model_is_rejected_as_invalid(self, db, memory_cache):
        ids = await _seed_rugby_league(db)
        service = LeagueTipExplanationService(
            client=_fake_client(), cache=memory_cache
        )
        with pytest.raises(BackendServiceError) as excinfo:
            await service.generate_explanation_for_tip(
                db,
                competition_id=ids["competition_id"],
                event_id=ids["event_id"],
                heuristic="not-a-model",
            )
        assert excinfo.value.status_code == 422
        assert excinfo.value.code == "invalid_heuristic"

    async def test_unknown_competition_is_404(self, db, memory_cache):
        ids = await _seed_rugby_league(db)
        service = LeagueTipExplanationService(
            client=_fake_client(), cache=memory_cache
        )
        with pytest.raises(BackendServiceError) as excinfo:
            await service.generate_explanation_for_tip(
                db,
                competition_id=999999,
                event_id=ids["event_id"],
                heuristic="elo",
            )
        assert excinfo.value.status_code == 404
        assert excinfo.value.code == "not_found"

    async def test_unknown_event_is_404(self, db, memory_cache):
        ids = await _seed_rugby_league(db)
        service = LeagueTipExplanationService(
            client=_fake_client(), cache=memory_cache
        )
        with pytest.raises(BackendServiceError) as excinfo:
            await service.generate_explanation_for_tip(
                db,
                competition_id=ids["competition_id"],
                event_id=999999,
                heuristic="elo",
            )
        assert excinfo.value.status_code == 404
        assert excinfo.value.code == "not_found"

    async def test_event_from_another_competition_is_404(self, db, memory_cache):
        ids = await _seed_rugby_league(db)
        # A second competition in the same sport owning its own event.
        other = Competition(
            sport_id="rugby-league",
            name=COMPETITION_NAMES["origin"],
            tier="national",
            format="tournament",
            timezone="Australia/Brisbane",
        )
        db.add(other)
        await db.flush()
        other_season = Season(competition_id=other.id, label="2026")
        db.add(other_season)
        await db.flush()
        stray = Event(
            season_id=other_season.id,
            event_type="match",
            round_id=1,
            venue="Suncorp Stadium",
            starts_at=datetime(2026, 4, 10, 20, 0),
            status="scheduled",
            completed=False,
            slug="soogame1",
        )
        db.add(stray)
        await db.commit()

        service = LeagueTipExplanationService(
            client=_fake_client(), cache=memory_cache
        )
        with pytest.raises(BackendServiceError) as excinfo:
            await service.generate_explanation_for_tip(
                db,
                competition_id=ids["competition_id"],
                event_id=stray.id,
                heuristic="elo",
            )
        assert excinfo.value.status_code == 404

    async def test_missing_tip_is_404(self, db, memory_cache):
        ids = await _seed_rugby_league(db)
        service = LeagueTipExplanationService(
            client=_fake_client(), cache=memory_cache
        )
        # 'ladder' is a registered league model but NOT in the reduced
        # rugby-league set, and no such tip row exists either way.
        with pytest.raises(BackendServiceError) as excinfo:
            await service.generate_explanation_for_tip(
                db,
                competition_id=ids["competition_id"],
                event_id=ids["event_id"],
                heuristic="ladder",
            )
        assert excinfo.value.status_code in (400, 404)

    async def test_provider_catastrophe_raises_repo_standard_error(
        self, db, memory_cache
    ):
        ids = await _seed_rugby_league(db)
        boom = AsyncMock()
        boom.generate_explanation = AsyncMock(side_effect=RuntimeError("boom"))
        service = LeagueTipExplanationService(client=boom, cache=memory_cache)
        with pytest.raises(BackendServiceError) as excinfo:
            await service.generate_explanation_for_tip(
                db,
                competition_id=ids["competition_id"],
                event_id=ids["event_id"],
                heuristic="elo",
            )
        assert excinfo.value.status_code == 502
        assert excinfo.value.code == "explanation_generation_failed"

    async def test_batch_explanations_cover_the_full_reduced_set(
        self, db, memory_cache
    ):
        ids = await _seed_rugby_league(db)
        service = LeagueTipExplanationService(
            client=_fake_client(), cache=memory_cache
        )
        rows = await service.explanations_for_event(db, event_id=ids["event_id"])
        assert sorted(row["heuristic"] for row in rows) == sorted(
            RUGBY_LEAGUE_MODELS
        )
        assert all(row["explanation"] for row in rows)

    async def test_batch_names_the_picked_side(self, db, memory_cache):
        ids = await _seed_rugby_league(db)
        service = LeagueTipExplanationService(
            client=_fake_client(), cache=memory_cache
        )
        rows = {
            row["heuristic"]: row
            for row in await service.explanations_for_event(
                db, event_id=ids["event_id"]
            )
        }
        assert rows["elo"]["picked"] == "Broncos"
        assert rows["form"]["picked"] == "Knights"

    async def test_partial_failure_degrades_to_tip_without_explanation(
        self, db, memory_cache
    ):
        ids = await _seed_rugby_league(db)
        client = AsyncMock()

        async def _flaky(**kwargs):
            if kwargs.get("heuristic") == "elo":
                raise RuntimeError("provider down")
            return "Fine here."

        client.generate_explanation = AsyncMock(side_effect=_flaky)
        service = LeagueTipExplanationService(client=client, cache=memory_cache)
        rows = await service.explanations_for_event(db, event_id=ids["event_id"])
        by_heuristic = {row["heuristic"]: row for row in rows}
        # The failed one degrades to None...
        assert by_heuristic["elo"]["explanation"] is None
        assert by_heuristic["elo"]["picked"] == "Broncos"
        # ...and every other tip still gets its explanation.
        assert by_heuristic["form"]["explanation"] == "Fine here."
        assert by_heuristic["matchup"]["explanation"] == "Fine here."
        assert by_heuristic["home_advantage"]["explanation"] == "Fine here."

    async def test_unknown_event_batch_returns_empty_never_raises(
        self, db, memory_cache
    ):
        service = LeagueTipExplanationService(
            client=_fake_client(), cache=memory_cache
        )
        assert await service.explanations_for_event(db, event_id=999999) == []


# ---------------------------------------------------------------------------
# The additive tips endpoint
# ---------------------------------------------------------------------------


def _build_app_with_tips_router():
    """Minimal FastAPI app with the tips router + repo-standard handlers."""
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    from app.api.tips import router

    app = FastAPI()
    app.include_router(router, prefix="/api/tips")

    @app.exception_handler(BackendServiceError)
    async def _backend_error_handler(_request, exc: BackendServiceError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
                "request_id": "test-request-id",
            },
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(_request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "Invalid request",
                "errors": exc.errors(),
                "request_id": "test-request-id",
            },
        )

    return app


def _override_db(app, mock_session: AsyncSession) -> None:
    from app.core import db_deps

    async def _override():
        yield mock_session

    app.dependency_overrides[db_deps.get_db] = _override


class TestLeagueTipsEndpoint:
    """GET /api/tips/league?league=nrl&slug=... — additive, no shape change."""

    def _seed_event_payload(self) -> dict[str, Any]:
        return {
            "id": 77,
            "slug": "r7bkn",
            "round_id": 7,
            "venue": "Suncorp Stadium",
            "starts_at": datetime(2026, 4, 10, 20, 0),
            "status": "scheduled",
            "completed": False,
            "competition": COMPETITION_NAMES["nrl"],
            "season": "2026",
            "participants": [],
        }

    def test_happy_path_returns_league_tips_with_explanations(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from unittest.mock import patch

        rows = [
            {"heuristic": "elo", "picked": "Broncos", "explanation": "Elo edge."},
            {"heuristic": "form", "picked": "Knights", "explanation": None},
        ]
        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        with patch(
            "app.api.tips.EventsCRUD.get_by_slug_with_participants",
            new=AsyncMock(return_value=self._seed_event_payload()),
        ), patch("app.api.tips.LeagueTipExplanationService") as service_cls:
            service_cls.return_value.explanations_for_event = AsyncMock(
                return_value=rows
            )
            client = TestClient(app)
            resp = client.get("/api/tips/league?league=nrl&slug=r7bkn")

        assert resp.status_code == 200
        body = resp.json()
        assert body["league"] == "nrl"
        assert body["event"] == "r7bkn"
        assert body["competition"] == COMPETITION_NAMES["nrl"]
        assert body["count"] == 2
        assert body["tips"][0] == {
            "heuristic": "elo",
            "picked": "Broncos",
            "explanation": "Elo edge.",
        }
        assert body["tips"][1]["explanation"] is None

    @pytest.mark.parametrize("league", ["nrl", "nrlw", "origin"])
    def test_all_three_rugby_league_keys_are_valid(
        self, monkeypatch: pytest.MonkeyPatch, league
    ):
        from unittest.mock import patch

        # The slug must resolve to an event of THIS league's competition.
        payload = self._seed_event_payload()
        payload["competition"] = COMPETITION_NAMES[league]
        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        with patch(
            "app.api.tips.EventsCRUD.get_by_slug_with_participants",
            new=AsyncMock(return_value=payload),
        ), patch("app.api.tips.LeagueTipExplanationService") as service_cls:
            service_cls.return_value.explanations_for_event = AsyncMock(
                return_value=[]
            )
            client = TestClient(app)
            resp = client.get(f"/api/tips/league?league={league}&slug=r7bkn")

        assert resp.status_code == 200
        assert resp.json()["competition"] == COMPETITION_NAMES[league]

    def test_unknown_league_is_404_without_touching_the_service(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from unittest.mock import patch

        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        with patch("app.api.tips.LeagueTipExplanationService") as service_cls, patch(
            "app.api.tips.EventsCRUD"
        ) as events_crud:
            client = TestClient(app)
            resp = client.get("/api/tips/league?league=not-a-league&slug=r7bkn")

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"
        service_cls.return_value.explanations_for_event.assert_not_called()
        events_crud.get_by_slug_with_participants.assert_not_called()

    def test_unknown_slug_is_404(self, monkeypatch: pytest.MonkeyPatch):
        from unittest.mock import patch

        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        with patch(
            "app.api.tips.EventsCRUD.get_by_slug_with_participants",
            new=AsyncMock(return_value=None),
        ), patch("app.api.tips.LeagueTipExplanationService") as service_cls:
            client = TestClient(app)
            resp = client.get("/api/tips/league?league=nrl&slug=missing")

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"
        service_cls.return_value.explanations_for_event.assert_not_called()

    def test_slug_from_another_league_is_404(self, monkeypatch: pytest.MonkeyPatch):
        from unittest.mock import patch

        payload = self._seed_event_payload()
        payload["competition"] = COMPETITION_NAMES["origin"]
        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        with patch(
            "app.api.tips.EventsCRUD.get_by_slug_with_participants",
            new=AsyncMock(return_value=payload),
        ), patch("app.api.tips.LeagueTipExplanationService") as service_cls:
            client = TestClient(app)
            resp = client.get("/api/tips/league?league=nrl&slug=r7bkn")

        assert resp.status_code == 404
        assert resp.json()["code"] == "not_found"
        service_cls.return_value.explanations_for_event.assert_not_called()

    def test_missing_league_param_is_422(self):
        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/tips/league?slug=r7bkn")
        assert resp.status_code == 422

    def test_missing_slug_param_is_422(self):
        app = _build_app_with_tips_router()
        _override_db(app, AsyncMock(spec=AsyncSession))
        client = TestClient(app)
        resp = client.get("/api/tips/league?league=nrl")
        assert resp.status_code == 422


class TestLeagueTipSchemas:
    """The response schemas are additive members of the tips schema module."""

    def test_league_tip_explanation_response_defaults(self):
        row = LeagueTipExplanationResponse(heuristic="elo")
        assert row.picked is None
        assert row.explanation is None

    def test_league_tips_response_shape(self):
        payload = LeagueTipsResponse(
            league="nrl",
            event="r7bkn",
            competition=COMPETITION_NAMES["nrl"],
            tips=[LeagueTipExplanationResponse(heuristic="elo", picked="Broncos")],
        )
        assert payload.count == 0  # count is derived server-side, not trusted
        assert payload.tips[0].heuristic == "elo"
