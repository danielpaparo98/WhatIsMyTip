"""Integration tests for the Tips API (``/api/tips``).

Covers the four declared routes:

* ``GET  /api/tips/``                       — list tips with filters
* ``GET  /api/tips/games-with-tips``        — games-with-tips for a round
* ``GET  /api/tips/{heuristic}``            — tips for one heuristic
* ``POST /api/tips/generate``               — public tip generation
                                              (intentionally public, rate
                                              limited to 10/min per IP)

The seed fixture inserts one ``best_bet`` tip for game id 1.

Special cases (from the inventory):

* ``POST /api/tips/generate`` is **intentionally public** (any caller
  may trigger tip generation for a season/round that has no tips yet).
  The only protection is the per-IP rate limit (10 req/min) declared on
  the route.  No ``X-API-Key`` is read or required.
* ``POST /api/tips/generate`` writes to the DB and may call OpenRouter
  for AI explanations — so its 200 happy-path test stubs
  ``TipGenerationService`` (matching the unit-test pattern in
  ``test_app_api_tips.py``).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# GET /api/tips/
# ---------------------------------------------------------------------------


class TestListTips:
    """``GET /api/tips/`` — list tips with filters."""

    def test_list_tips_default_returns_best_bet(self, client):
        """No filters → default heuristic ``best_bet`` (returns the seed tip)."""
        resp = client.get("/api/tips/")
        assert resp.status_code == 200
        body = resp.json()
        assert "tips" in body
        assert "count" in body
        assert body["count"] == len(body["tips"])
        # The seed has one ``best_bet`` tip → default branch returns it.
        assert body["count"] == 1
        tip = body["tips"][0]
        assert tip["heuristic"] == "best_bet"
        assert tip["selected_team"] == "Brisbane"
        assert tip["margin"] == 12
        assert tip["confidence"] == 0.75
        assert tip["explanation"] == "Brisbane strong at home"
        assert tip["game_id"] == 1

    def test_list_tips_season_round_returns_seeded_tip(self, client):
        """``season=2025&round=1`` returns the seeded tip via get_by_round."""
        resp = client.get("/api/tips/?season=2025&round=1")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["tips"][0]["game_id"] == 1

    def test_list_tips_heuristic_filter_returns_seeded_tip(self, client):
        """``heuristic=best_bet`` returns the seeded tip."""
        resp = client.get("/api/tips/?heuristic=best_bet")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["tips"][0]["heuristic"] == "best_bet"

    def test_list_tips_heuristic_filter_other_returns_empty(self, client):
        """``heuristic=yolo`` returns no tips (seed only has best_bet)."""
        resp = client.get("/api/tips/?heuristic=yolo")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 0
        assert body["tips"] == []

    @pytest.mark.parametrize(
        "bad_heuristic",
        ["not_a_heuristic", "BestBet", "BOGUS", "best-bet"],
    )
    def test_list_tips_invalid_heuristic_returns_422(self, client, bad_heuristic):
        """``heuristic`` is regex-validated against the allow-list."""
        resp = client.get(f"/api/tips/?heuristic={bad_heuristic}")
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "validation_error"

    def test_list_tips_invalid_season_returns_422(self, client):
        """``season=1999`` is below the ge=2000 floor."""
        resp = client.get("/api/tips/?season=1999")
        assert resp.status_code == 422

    def test_list_tips_invalid_round_returns_422(self, client):
        """``round=0`` is below the ge=1 floor."""
        resp = client.get("/api/tips/?season=2025&round=0")
        assert resp.status_code == 422

    def test_list_tips_limit_above_max_returns_422(self, client):
        """``limit=501`` exceeds the le=500 ceiling."""
        resp = client.get("/api/tips/?limit=501")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/tips/games-with-tips
# ---------------------------------------------------------------------------


class TestGamesWithTips:
    """``GET /api/tips/games-with-tips`` — games + their tips for a round."""

    def test_games_with_tips_returns_seeded_payload(self, client):
        """Happy path: 1 game + 1 best_bet tip attached."""
        resp = client.get(
            "/api/tips/games-with-tips?season=2025&round=1&heuristic=best_bet"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "games" in body
        assert "count" in body
        assert body["count"] == 1
        assert len(body["games"]) == 1

        game = body["games"][0]
        assert game["season"] == 2025
        assert game["round_id"] == 1
        assert game["home_team"] == "Brisbane"
        assert game["away_team"] == "Collingwood"
        assert game["completed"] is True
        assert game["home_score"] == 85
        assert game["away_score"] == 72
        assert game["slug"] == "seedgame001"

        # The tip is attached because we filtered by heuristic=best_bet.
        assert game["tip"] is not None
        assert game["tip"]["heuristic"] == "best_bet"
        assert game["tip"]["selected_team"] == "Brisbane"

        # model_predictions is always attached.
        assert isinstance(game["model_predictions"], list)

    def test_games_with_tips_no_tip_when_heuristic_mismatches(self, client):
        """``heuristic=yolo`` finds the game but no tip is attached."""
        resp = client.get(
            "/api/tips/games-with-tips?season=2025&round=1&heuristic=yolo"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        # No yolo tip in the seed → ``tip`` is null.
        assert body["games"][0]["tip"] is None

    def test_games_with_tips_empty_round_returns_empty(self, client):
        """Round that has no games → 200 with empty list (not 404, see R10)."""
        resp = client.get(
            "/api/tips/games-with-tips?season=2025&round=99&heuristic=best_bet"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"games": [], "count": 0}

    @pytest.mark.parametrize("missing", ["season", "round"])
    def test_games_with_tips_missing_required_param_returns_422(
        self, client, missing
    ):
        """Both ``season`` and ``round`` are required (422 when missing)."""
        params = {"season": 2025, "round": 1}
        params.pop(missing)
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        resp = client.get(f"/api/tips/games-with-tips?{qs}")
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "validation_error"

    def test_games_with_tips_invalid_heuristic_returns_422(self, client):
        """``heuristic=bogus`` is rejected by the regex pattern."""
        resp = client.get(
            "/api/tips/games-with-tips?season=2025&round=1&heuristic=bogus"
        )
        assert resp.status_code == 422

    def test_games_with_tips_invalid_season_returns_422(self, client):
        """``season=1999`` is below the ge=2000 floor."""
        resp = client.get("/api/tips/games-with-tips?season=1999&round=1")
        assert resp.status_code == 422

    def test_games_with_tips_invalid_round_returns_422(self, client):
        """``round=0`` is below the ge=1 floor."""
        resp = client.get("/api/tips/games-with-tips?season=2025&round=0")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/tips/{heuristic}
# ---------------------------------------------------------------------------


class TestTipsByHeuristic:
    """``GET /api/tips/{heuristic}`` — tips for one heuristic."""

    @pytest.mark.parametrize(
        "heuristic",
        ["best_bet", "yolo", "weighted_tip"],
    )
    def test_tips_by_heuristic_valid_returns_200(self, client, heuristic):
        """Each allowed heuristic returns a list (possibly empty)."""
        resp = client.get(f"/api/tips/{heuristic}")
        assert resp.status_code == 200
        body = resp.json()
        assert "tips" in body
        assert "count" in body
        assert body["count"] == len(body["tips"])
        for tip in body["tips"]:
            assert tip["heuristic"] == heuristic

    def test_tips_by_heuristic_best_bet_returns_seeded_tip(self, client):
        """``best_bet`` returns the seeded tip."""
        resp = client.get("/api/tips/best_bet")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["tips"][0]["selected_team"] == "Brisbane"

    @pytest.mark.parametrize(
        "bogus", ["not_a_heuristic", "BEST_BET", "best-bet", "unknown"]
    )
    def test_tips_by_heuristic_invalid_returns_422(self, client, bogus):
        """Path-level pattern rejects any heuristic not in the allow-list."""
        resp = client.get(f"/api/tips/{bogus}")
        assert resp.status_code == 422

    def test_tips_by_heuristic_limit_above_max_returns_422(self, client):
        """``limit=501`` exceeds the le=500 ceiling."""
        resp = client.get("/api/tips/best_bet?limit=501")
        assert resp.status_code == 422

    def test_tips_by_heuristic_limit_param_is_respected(self, client):
        """``limit=1`` caps the response size."""
        resp = client.get("/api/tips/best_bet?limit=1")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["tips"]) <= 1


# ---------------------------------------------------------------------------
# POST /api/tips/generate  (public — intentionally no auth, R1 reverted)
# ---------------------------------------------------------------------------


class TestGenerateTipsPublic:
    """``POST /api/tips/generate`` — public endpoint contract.

    The endpoint is intentionally public (no ``X-API-Key`` required).
    Any caller may trigger tip generation for a season/round that has
    no tips yet.  These tests pin the public contract: hitting the
    route without any header must return 200, and a stray
    ``X-API-Key`` header is silently ignored.
    """

    def test_generate_tips_does_not_require_auth(self, client):
        """Hitting the route with no headers at all → 200 (public).

        This is the explicit lock-in test for the public design.  If
        someone re-adds ``require_admin_key`` to the route in the
        future, this test will start returning 401 and the regression
        will be caught immediately.
        """
        with patch("app.api.tips.TipGenerationService") as mock_service_cls:
            mock_service = mock_service_cls.return_value
            mock_service.generate_for_round = AsyncMock(
                return_value={
                    "games_processed": 1,
                    "tips_created": 3,
                    "tips_skipped": 0,
                    "tips_updated": 0,
                    "errors": [],
                }
            )

            resp = client.post(
                "/api/tips/generate",
                json={"season": 2025, "round_id": 1},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["season"] == 2025
        assert body["round_id"] == 1
        mock_service.generate_for_round.assert_awaited_once()

    def test_generate_tips_ignores_invalid_x_api_key_header(self, client):
        """A garbage ``X-API-Key`` header is silently ignored → 200.

        Companion to ``test_generate_tips_does_not_require_auth``:
        even if a client (correctly or incorrectly) sends an
        ``X-API-Key`` header, the endpoint must not require it and
        must not 401.
        """
        with patch("app.api.tips.TipGenerationService") as mock_service_cls:
            mock_service = mock_service_cls.return_value
            mock_service.generate_for_round = AsyncMock(
                return_value={
                    "games_processed": 1,
                    "tips_created": 3,
                    "tips_skipped": 0,
                    "tips_updated": 0,
                    "errors": [],
                }
            )

            resp = client.post(
                "/api/tips/generate",
                json={"season": 2025, "round_id": 1},
                headers={"X-API-Key": "garbage-value-the-server-should-ignore"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        mock_service.generate_for_round.assert_awaited_once()


class TestGenerateTipsValidation:
    """``POST /api/tips/generate`` — request-body validation contract."""

    def test_generate_tips_empty_body_returns_422(self, client):
        """Missing required ``season`` field → 422 ``validation_error``."""
        resp = client.post(
            "/api/tips/generate",
            json={},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "validation_error"

    def test_generate_tips_bad_heuristic_returns_422(self, client):
        """Unknown heuristic in the ``heuristics`` list → 422 ``invalid_heuristics``.

        Distinct from the global ``validation_error`` code: this comes
        from the route's explicit handler check.
        """
        resp = client.post(
            "/api/tips/generate",
            json={
                "season": 2025,
                "round_id": 1,
                "heuristics": ["best_bet", "NOT_A_HEURISTIC"],
            },
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "invalid_heuristics"
        assert "NOT_A_HEURISTIC" in body["message"]

    def test_generate_tips_season_below_2000_returns_404(self, client):
        """``season=1999`` is below the implicit ``ge=2000`` floor.

        Note: ``TipGenerateRequest.season`` doesn't declare a
        ``ge=2000`` Pydantic constraint, so the value isn't rejected at
        the validation layer.  Instead the route proceeds to the DB,
        finds no games for season 1999 round 1, and returns 404
        ``not_found``.  The test pins the actual contract.
        """
        resp = client.post(
            "/api/tips/generate",
            json={"season": 1999, "round_id": 1},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert "1999" in body["message"]


class TestGenerateTipsRoundId:
    """``POST /api/tips/generate`` — ``round_id`` re-validation (line 287
    of tips.py): the route handler re-validates ``round_id is not None``
    because Pydantic only marks ``season`` as required.
    """

    def test_generate_tips_without_round_id_returns_422(self, client):
        """``round_id`` is None → 422 ``validation_error``."""
        resp = client.post(
            "/api/tips/generate",
            json={"season": 2025},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "validation_error"
        assert "round_id" in body["message"]

    def test_generate_tips_round_id_zero_returns_404(self, client):
        """``round_id=0`` finds no games \u2192 404 (route doesn't reject 0 at validation).

        ``TipGenerateRequest.round_id`` is ``Optional[int]`` with no
        ``ge=1`` constraint, so Pydantic accepts 0.  The route's
        internal ``if round_id is None`` check doesn't catch 0, and
        ``GameCRUD.get_by_round(2025, 0)`` returns no games \u2192 404.
        """
        resp = client.post(
            "/api/tips/generate",
            json={"season": 2025, "round_id": 0},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"


class TestGenerateTipsNotFound:
    """``POST /api/tips/generate`` — 404 when no games exist for the round."""

    def test_generate_tips_no_games_for_round_returns_404(self, client):
        """``round_id=99`` has no games → 404 ``not_found``."""
        resp = client.post(
            "/api/tips/generate",
            json={"season": 2025, "round_id": 99},
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["code"] == "not_found"
        assert "request_id" in body


class TestGenerateTipsSuccess:
    """``POST /api/tips/generate`` — 200 happy-path with stubbed service.

    The real service would invoke OpenRouter for AI explanations; we
    stub ``TipGenerationService.generate_for_round`` so the test stays
    fast and deterministic, matching the unit-test pattern at
    ``test_app_api_tips.py::TestGenerateTips``.  The endpoint is public,
    so these tests intentionally send **no** ``X-API-Key`` header.
    """

    def test_generate_tips_returns_200_with_contract_shape(self, client):
        """Happy path: 200 with the documented ``{status, season,
        round_id, games_processed, tips_created, tips_skipped,
        tips_updated, errors}`` shape."""
        fake_stats = {
            "games_processed": 1,
            "tips_created": 3,
            "tips_skipped": 0,
            "tips_updated": 0,
            "errors": [],
        }

        with patch("app.api.tips.TipGenerationService") as mock_service_cls:
            mock_service = mock_service_cls.return_value
            mock_service.generate_for_round = AsyncMock(return_value=fake_stats)

            resp = client.post(
                "/api/tips/generate",
                json={"season": 2025, "round_id": 1},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["season"] == 2025
        assert body["round_id"] == 1
        assert body["games_processed"] == 1
        assert body["tips_created"] == 3
        assert body["tips_skipped"] == 0
        assert body["tips_updated"] == 0
        assert body["errors"] == []

    def test_generate_tips_with_heuristics_filter(self, client):
        """``heuristics=[best_bet]`` is passed through to the service."""
        with patch("app.api.tips.TipGenerationService") as mock_service_cls:
            mock_service = mock_service_cls.return_value
            mock_service.generate_for_round = AsyncMock(
                return_value={
                    "games_processed": 1,
                    "tips_created": 1,
                    "tips_skipped": 0,
                    "tips_updated": 0,
                    "errors": [],
                }
            )

            resp = client.post(
                "/api/tips/generate",
                json={
                    "season": 2025,
                    "round_id": 1,
                    "heuristics": ["best_bet"],
                    "regenerate": True,
                },
            )

        assert resp.status_code == 200
        # The service was called with the right kwargs.
        mock_service.generate_for_round.assert_awaited_once()
        call_args = mock_service.generate_for_round.call_args
        assert call_args.kwargs["season"] == 2025
        assert call_args.kwargs["round_id"] == 1
        assert call_args.kwargs["regenerate"] is True

    def test_generate_tips_response_includes_request_id(self, client):
        """The 200 response also includes the request_id (for tracing)."""
        with patch("app.api.tips.TipGenerationService") as mock_service_cls:
            mock_service = mock_service_cls.return_value
            mock_service.generate_for_round = AsyncMock(
                return_value={
                    "games_processed": 0,
                    "tips_created": 0,
                    "tips_skipped": 0,
                    "tips_updated": 0,
                    "errors": [],
                }
            )

            resp = client.post(
                "/api/tips/generate",
                json={"season": 2025, "round_id": 1},
            )

        assert resp.status_code == 200
        assert "X-Request-ID" in resp.headers
