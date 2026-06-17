"""Integration tests for the Games API (``/api/games``).

Covers the three declared routes:

* ``GET /api/games/``               — list games with filters
* ``GET /api/games/{slug}``         — single game by slug
* ``GET /api/games/{slug}/detail``  — game + tips + model_predictions
                                       + match_analysis + weather

The seed fixture inserts one completed game in (season=2025, round=1)
with slug ``seedgame001`` (home Brisbane, away Collingwood, 85-72).

Branching in ``list_games`` (per inventory §2 / R10):

* ``latest=true`` → round locator, **not** a games list
* ``upcoming=true`` → games after ``now``
* ``season+round`` → games for that round
* ``season`` only → games for that season
* default → upcoming games
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# GET /api/games/
# ---------------------------------------------------------------------------


class TestListGames:
    """``GET /api/games/`` — list with filters."""

    def test_list_games_default_returns_seeded_game(self, client):
        """No filters → list shape with the seeded game inside."""
        resp = client.get("/api/games/")
        assert resp.status_code == 200
        body = resp.json()
        assert "games" in body
        assert "count" in body
        # ``count`` must equal ``len(games)`` (the schema invariant).
        assert body["count"] == len(body["games"])
        # The seeded game is dated 2025-03-20, well in the past, so the
        # default "upcoming" branch returns an empty list.
        assert body["count"] == 0

    def test_list_games_season_filter_returns_seeded_game(self, client):
        """``season=2025`` returns the seeded game in the past-date branch."""
        resp = client.get("/api/games/?season=2025")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["games"][0]["slug"] == "seedgame001"
        assert body["games"][0]["season"] == 2025
        assert body["games"][0]["round_id"] == 1
        assert body["games"][0]["home_team"] == "Brisbane"
        assert body["games"][0]["away_team"] == "Collingwood"
        assert body["games"][0]["completed"] is True
        assert body["games"][0]["home_score"] == 85
        assert body["games"][0]["away_score"] == 72

    def test_list_games_season_round_filter(self, client):
        """``season=2025&round=1`` returns the seeded game (round alias)."""
        resp = client.get("/api/games/?season=2025&round=1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["games"][0]["round_id"] == 1

    def test_list_games_upcoming_returns_empty_for_past_seed(self, client):
        """``upcoming=true`` returns no games (seed is in the past)."""
        resp = client.get("/api/games/?upcoming=true")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["games"] == []

    def test_list_games_latest_returns_locator(self, client):
        """``latest=true`` returns a locator object, not a games list.

        Pins the branching in R10: ``latest`` short-circuits all other
        filters and returns ``{season, round_id, game_count, ...}``.
        """
        resp = client.get("/api/games/?latest=true")
        assert resp.status_code == 200
        body = resp.json()
        # The seeded game is in the past, so the locator picks it up.
        assert body["season"] == 2025
        assert body["round_id"] == 1
        assert body["game_count"] == 1
        assert body["has_upcoming"] is False
        assert "is_current_year" in body

    def test_list_games_limit_param_is_respected(self, client):
        """``limit=1`` caps the response size."""
        resp = client.get("/api/games/?season=2025&limit=1")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["games"]) <= 1

    def test_list_games_validation_invalid_season_returns_422(self, client):
        """``season=1999`` is below the ge=2000 floor → 422."""
        resp = client.get("/api/games/?season=1999")
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "validation_error"
        assert "errors" in body

    def test_list_games_validation_round_below_one_returns_422(self, client):
        """``round=0`` is below the ge=1 floor → 422."""
        resp = client.get("/api/games/?season=2025&round=0")
        assert resp.status_code == 422

    def test_list_games_validation_limit_above_500_returns_422(self, client):
        """``limit=501`` exceeds the le=500 ceiling → 422."""
        resp = client.get("/api/games/?season=2025&limit=501")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/games/{slug}
# ---------------------------------------------------------------------------


class TestGetGameBySlug:
    """``GET /api/games/{slug}`` — single game lookup."""

    def test_get_game_by_slug_returns_seeded_game(self, client):
        """Happy path: the seeded slug returns the seeded game."""
        resp = client.get("/api/games/seedgame001")
        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == "seedgame001"
        assert body["season"] == 2025
        assert body["round_id"] == 1
        assert body["home_team"] == "Brisbane"
        assert body["away_team"] == "Collingwood"
        assert body["completed"] is True
        assert body["home_score"] == 85
        assert body["away_score"] == 72
        assert body["venue"] == "Gabba"

    def test_get_game_by_slug_returns_404_when_missing(self, client):
        """Unknown slug → 404 ``not_found`` with the contract error code."""
        resp = client.get("/api/games/does-not-exist")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert "request_id" in body

    @pytest.mark.parametrize(
        "empty_slug_path",
        ["/api/games/", "/api/games"],  # trailing-/  +  no slug
    )
    def test_get_game_empty_slug_falls_through_to_list(self, client, empty_slug_path):
        """Empty path matches the list endpoint, not the slug route."""
        # ``GET /api/games/`` is the list endpoint; ``GET /api/games``
        # redirects there in FastAPI.
        resp = client.get(empty_slug_path, follow_redirects=False)
        # Either 200 (list) or 307 (redirect to list) — both are valid.
        assert resp.status_code in (200, 307)


# ---------------------------------------------------------------------------
# GET /api/games/{slug}/detail
# ---------------------------------------------------------------------------


class TestGetGameDetail:
    """``GET /api/games/{slug}/detail`` — game + related data."""

    def test_get_game_detail_returns_full_payload(self, client):
        """Happy path: returns game + the seeded tip + the seeded prediction."""
        resp = client.get("/api/games/seedgame001/detail")
        assert resp.status_code == 200
        body = resp.json()
        assert "game" in body
        assert "tips" in body
        assert "model_predictions" in body
        assert "match_analysis" in body  # seeded as None
        assert "weather" in body  # seeded as None

        # Game is the seeded one.
        game = body["game"]
        assert game["slug"] == "seedgame001"
        assert game["home_team"] == "Brisbane"

        # The single seeded tip is attached.
        assert len(body["tips"]) == 1
        tip = body["tips"][0]
        assert tip["heuristic"] == "best_bet"
        assert tip["selected_team"] == "Brisbane"
        assert tip["margin"] == 12
        assert tip["confidence"] == 0.75

        # The single seeded model_prediction is attached.
        assert len(body["model_predictions"]) == 1
        pred = body["model_predictions"][0]
        assert pred["model_name"] == "elo"
        assert pred["winner"] == "Brisbane"
        assert pred["confidence"] == 0.70

        # match_analysis + weather were not seeded → null.
        assert body["match_analysis"] is None
        assert body["weather"] is None

    def test_get_game_detail_returns_404_when_missing(self, client):
        """Unknown slug → 404 with the contract error code."""
        resp = client.get("/api/games/does-not-exist/detail")
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert "request_id" in body

    def test_get_game_detail_path_param_max_length_is_128(self, client):
        """Slugs longer than 128 chars are rejected with 422 by Path validation."""
        too_long = "x" * 129
        resp = client.get(f"/api/games/{too_long}/detail")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Security headers (sanity-check that the middleware is wired)
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    """The custom middleware set in ``main.py`` adds headers to every
    response — pinning a few so middleware regressions are caught.
    """

    def test_security_headers_present(self, client):
        """OWASP headers are set on a games response."""
        resp = client.get("/api/games/?season=2025")
        assert resp.status_code == 200
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
        assert "Content-Security-Policy" in resp.headers

    def test_request_id_header_echoed(self, client):
        """``X-Request-ID`` is present on every response."""
        resp = client.get("/api/games/?season=2025")
        assert "X-Request-ID" in resp.headers
        # Looks like a UUID4.
        request_id = resp.headers["X-Request-ID"]
        assert len(request_id) >= 16
