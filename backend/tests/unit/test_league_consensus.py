"""Unit tests for the league consensus module (FCL-3).

Pure and table-driven — no database, mirroring test_league_heuristics.py:

* **pick_best_bet** — majority vote over a league's base-model picks for
  one event; vote tie → home side (FCL-3a); empty/zero-info → the
  deterministic home default; drawn event → draw-no-pick (NULL).
* **Numeric feature contract** — per-model features and their names are
  derived from the SAME model list (mirrors weighted_tip's
  ``feature_names_for`` discipline); prediction raises ValueError on a
  length mismatch so vector and names can never silently drift.
* **Linear combiner** — intercept + coefficients → home-signed margin →
  tip with ``home_margin_to_tip`` semantics; majority-vote fallback
  (conf 0.55) when no trained model exists.
* **Lazy heavy imports** — xgboost is imported INSIDE the artifact
  loader only (test_lazy_sklearn.py style: isolated subprocess), and the
  module never imports ``app.*`` at module load.
"""

from __future__ import annotations

import inspect
import subprocess
import sys
import textwrap
from datetime import datetime
from pathlib import Path

import pytest

from packages.shared.services.league_consensus import (
    HEURISTIC_BEST_BET,
    LEAGUE_FEATURE_SPECS,
    LeagueConsensusTip,
    build_league_feature_vector,
    league_boosted_tip,
    league_consensus_fallback,
    league_feature_names_for,
    league_linear_tip,
    league_margin_to_tip,
    league_predict_home_margin,
    load_league_boosted_model,
    pick_best_bet,
)
from packages.shared.services.league_heuristics import (
    LEAGUE_HEURISTICS,
    LEAGUE_MODELS,
    CompletedEvent,
    TipPick,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]  # backend/

_KICKOFF = datetime(2026, 4, 4, 14, 40)


def _event(
    event_id: int,
    *,
    season_id: int = 1,
    starts_at: datetime | None = _KICKOFF,
    home_side: int | None = 101,
    away_side: int | None = 102,
    home_pid: int | None = 11,
    away_pid: int | None = 12,
    home_score: int | None = None,
    away_score: int | None = None,
) -> CompletedEvent:
    """A completed-event snapshot; decided once both scores are given."""
    return CompletedEvent(
        event_id=event_id,
        season_id=season_id,
        starts_at=starts_at,
        home_side_id=home_side,
        away_side_id=away_side,
        home_participant_id=home_pid,
        away_participant_id=away_pid,
        home_score=home_score,
        away_score=away_score,
    )


def _pick(event_id: int, heuristic: str, side: int | None) -> TipPick:
    """One base-model pick snapshot (side ``None`` = draw-no-pick)."""
    return TipPick(event_id=event_id, heuristic=heuristic, selected_side_id=side)


# ---------------------------------------------------------------------------
# pick_best_bet — majority vote, tie → home (FCL-3a), draw → no pick.
# ---------------------------------------------------------------------------


class TestPickBestBet:
    def test_away_majority_picks_away(self) -> None:
        event = _event(5)
        picks = [
            _pick(5, "form", 102),
            _pick(5, "ladder", 101),
            _pick(5, "elo", 102),
        ]
        # FCL-14: the vote-share confidence (2/3 → 0.767) and the AFL
        # fallback's 6-point margin ride on the tip.
        assert pick_best_bet(event, picks) == TipPick(
            5, HEURISTIC_BEST_BET, 102, 0.767, 6
        )

    def test_home_majority_picks_home(self) -> None:
        event = _event(5)
        picks = [_pick(5, "form", 101), _pick(5, "ladder", 101), _pick(5, "elo", 102)]
        assert pick_best_bet(event, picks).selected_side_id == 101

    def test_vote_tie_goes_home(self) -> None:
        event = _event(5)
        picks = [_pick(5, "form", 101), _pick(5, "ladder", 102)]
        assert pick_best_bet(event, picks).selected_side_id == 101

    def test_empty_picks_default_home(self) -> None:
        # Zero information must be deterministic: the home side (FCL-3a —
        # the league counterpart of best_bet's alphabetically-first rule,
        # which needs team names the league pipeline does not have).
        assert pick_best_bet(_event(5), []).selected_side_id == 101

    def test_drawn_event_is_draw_no_pick(self) -> None:
        event = _event(5, home_score=80, away_score=80)
        picks = [_pick(5, "form", 101), _pick(5, "ladder", 102)]
        pick = pick_best_bet(event, picks)
        assert pick == TipPick(5, HEURISTIC_BEST_BET, None, None, None)

    def test_picks_for_other_events_are_ignored(self) -> None:
        event = _event(5)
        picks = [_pick(99, "form", 102)]
        assert pick_best_bet(event, picks).selected_side_id == 101

    def test_draw_no_pick_base_votes_do_not_count_as_home_votes(self) -> None:
        # A NULL base selection is "no pick", never a home vote.
        event = _event(5)
        picks = [_pick(5, "form", None), _pick(5, "ladder", 102)]
        assert pick_best_bet(event, picks).selected_side_id == 102


# ---------------------------------------------------------------------------
# Feature-name contract — names and vector derive from ONE model list.
# ---------------------------------------------------------------------------


class TestLeagueFeatureNames:
    def test_names_follow_the_model_list_order(self) -> None:
        models = ("home_advantage", "form", "ladder", "elo", "matchup")
        assert league_feature_names_for(models) == [
            "home_advantage_home_constant",
            "form_wins_diff",
            "ladder_wins_diff",
            "ladder_pct_diff",
            "elo_rating_diff",
            "matchup_h2h_wins_diff",
        ]

    def test_default_set_is_the_d3_trio_contract(self) -> None:
        assert league_feature_names_for(LEAGUE_HEURISTICS) == [
            "home_advantage_home_constant",
            "form_wins_diff",
            "ladder_wins_diff",
            "ladder_pct_diff",
        ]

    def test_unknown_model_raises(self) -> None:
        with pytest.raises(ValueError, match="weather_impact"):
            league_feature_names_for(("weather_impact",))

    def test_registry_covers_exactly_the_league_models(self) -> None:
        # Fail-fast invariant enforced at import: a league model without a
        # feature spec (or a spec without a model) cannot happen silently.
        assert set(LEAGUE_FEATURE_SPECS) == set(LEAGUE_MODELS)


class TestBuildLeagueFeatureVector:
    def test_prior_and_season_slices_route_like_the_pick_models(self) -> None:
        # Spans-seasons models (form, elo, matchup) read the PRIOR slice;
        # season-scoped ladder reads the SEASON slice.  The two slices
        # deliberately disagree so a mis-routed feature flips its sign.
        prior = [
            _event(
                1, season_id=2, home_pid=11, away_pid=12, home_score=90, away_score=70
            )
        ]  # 11 beat 12 last season
        season = [
            _event(
                2, season_id=1, home_pid=12, away_pid=11, home_score=80, away_score=60
            )
        ]  # 12 beat 11 this season
        target = _event(3, season_id=1)  # 11 (home) vs 12 (away)
        models = ("home_advantage", "form", "ladder", "elo", "matchup")

        vector = build_league_feature_vector(target, prior, season, models)

        assert vector == [
            1.0,  # home_advantage_home_constant
            1.0,  # form_wins_diff: prior 11 beat 12 → +1 (season would say −1)
            -1.0,  # ladder_wins_diff: season 12 beat 11 → −1 (prior would say +1)
            pytest.approx(-58.3333, abs=1e-3),  # ladder_pct_diff: 75.0 − 133.333
            20.0,  # elo_rating_diff: prior 1510 − 1490 (season would say −20)
            1.0,  # matchup_h2h_wins_diff: prior 11 beat 12 → +1
        ]

    def test_empty_history_is_the_neutral_vector(self) -> None:
        vector = build_league_feature_vector(_event(1), [], [], LEAGUE_HEURISTICS)
        assert vector == [1.0, 0.0, 0.0, 0.0]

    def test_missing_participants_are_neutral_except_home_constant(self) -> None:
        event = _event(1, home_pid=None)
        models = ("home_advantage", "form", "ladder", "elo", "matchup")
        assert build_league_feature_vector(event, [], [], models) == [
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        ]

    def test_infinite_ladder_percentage_gets_a_finite_sentinel(self) -> None:
        # 11 kept a clean sheet: percentage is inf — inf features would
        # poison a linear fit, so the sentinel (999.0) stands in.  The
        # prior slice doubles as the season slice (ladder reads the
        # season-scoped one).
        prior = [
            _event(1, home_pid=11, away_pid=21, home_score=90, away_score=0),
        ]
        vector = build_league_feature_vector(
            _event(2, home_pid=11, away_pid=12), prior, prior, LEAGUE_HEURISTICS
        )
        assert vector == [1.0, 1.0, 1.0, 999.0]

    def test_two_clean_sheets_give_a_zero_percentage_diff(self) -> None:
        # inf − inf would be nan; both sentinels cancel to 0.0 instead.
        # (A nan would also fail this == comparison, pinning finiteness.)
        prior = [
            _event(1, home_pid=11, away_pid=21, home_score=90, away_score=0),
            _event(2, home_pid=12, away_pid=22, home_score=50, away_score=0),
        ]
        vector = build_league_feature_vector(
            _event(3, home_pid=11, away_pid=12), prior, prior, LEAGUE_HEURISTICS
        )
        assert vector == [1.0, 0.0, 0.0, 0.0]

    def test_unknown_model_raises(self) -> None:
        with pytest.raises(ValueError, match="weather_impact"):
            build_league_feature_vector(_event(1), [], [], ("weather_impact",))


# ---------------------------------------------------------------------------
# Pure linear combiner — margin math + tip mapping (home_margin_to_tip
# semantics with side ids instead of team names).
# ---------------------------------------------------------------------------


class TestLeaguePredictHomeMargin:
    def test_linear_combination(self) -> None:
        y = league_predict_home_margin(
            [1.0, 2.0],
            intercept=1.5,
            coefficients={
                "home_advantage_home_constant": 3.0,
                "form_wins_diff": 0.5,
            },
            feature_names=["home_advantage_home_constant", "form_wins_diff"],
        )
        assert y == pytest.approx(5.5)

    def test_missing_coefficient_is_treated_as_zero(self) -> None:
        y = league_predict_home_margin(
            [1.0, 2.0], intercept=1.5, coefficients={}, feature_names=["a", "b"]
        )
        assert y == pytest.approx(1.5)

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="length mismatch"):
            league_predict_home_margin(
                [1.0], intercept=0.0, coefficients={}, feature_names=["a", "b"]
            )


class TestLeagueMarginToTip:
    def test_positive_margin_tips_home(self) -> None:
        tip = league_margin_to_tip(_event(5), 10.0)
        assert tip == LeagueConsensusTip(101, 0.65, 10)

    def test_negative_margin_tips_away(self) -> None:
        tip = league_margin_to_tip(_event(5), -7.2)
        assert tip == LeagueConsensusTip(102, 0.608, 7)

    def test_zero_margin_tips_home_at_the_floor(self) -> None:
        # y_pred >= 0 → home (same rule as home_margin_to_tip); margin
        # floors at 1 and confidence at 0.50.
        tip = league_margin_to_tip(_event(5), 0.0)
        assert tip == LeagueConsensusTip(101, 0.50, 1)

    def test_confidence_saturates_at_095(self) -> None:
        assert league_margin_to_tip(_event(5), 100.0).confidence == 0.95
        assert league_margin_to_tip(_event(5), -100.0).confidence == 0.95
        assert league_margin_to_tip(_event(5), -100.0).margin == 100

    def test_sub_one_margin_floors_at_one(self) -> None:
        tip = league_margin_to_tip(_event(5), 0.4)
        assert tip == LeagueConsensusTip(101, 0.506, 1)


class TestLeagueLinearTip:
    def test_composes_features_margin_and_tip(self) -> None:
        prior = [_event(1, home_pid=11, away_pid=21, home_score=90, away_score=70)]
        event = _event(2)
        # Only form_wins_diff is weighted: the vector's form diff is +1.0
        # (11 won its last event, 12 did not play) → y = 10.0 → home, 0.65, 10.
        tip = league_linear_tip(
            event, prior, [], intercept=0.0, coefficients={"form_wins_diff": 10.0}
        )
        assert tip == LeagueConsensusTip(101, 0.65, 10)

    def test_drawn_event_is_draw_no_pick(self) -> None:
        event = _event(9, home_score=80, away_score=80)
        tip = league_linear_tip(event, [], [], intercept=1.0, coefficients={})
        assert tip == LeagueConsensusTip(None, 0.50, 0)


# ---------------------------------------------------------------------------
# Majority-vote fallback — the pre-training behaviour of BOTH combiners
# (mirrors weighted_tip_fallback; tie → home per FCL-3a).
# ---------------------------------------------------------------------------


class TestLeagueConsensusFallback:
    def test_majority_side_with_fixed_confidence(self) -> None:
        event = _event(5)
        picks = [_pick(5, "form", 102), _pick(5, "ladder", 101), _pick(5, "elo", 102)]
        assert league_consensus_fallback(event, picks) == LeagueConsensusTip(
            102, 0.55, 6
        )

    def test_vote_tie_goes_home(self) -> None:
        event = _event(5)
        picks = [_pick(5, "form", 101), _pick(5, "ladder", 102)]
        assert league_consensus_fallback(event, picks).selected_side_id == 101

    def test_empty_picks_default_home(self) -> None:
        assert league_consensus_fallback(_event(5), []) == LeagueConsensusTip(
            101, 0.55, 6
        )

    def test_drawn_event_is_draw_no_pick(self) -> None:
        event = _event(5, home_score=80, away_score=80)
        assert league_consensus_fallback(event, []) == LeagueConsensusTip(
            None, 0.55, 0
        )


# ---------------------------------------------------------------------------
# Lazy heavy imports (test_lazy_sklearn.py style) — xgboost must only be
# importable inside the artifact loader, and the module must never touch
# app.* at module load.
# ---------------------------------------------------------------------------

# Heavy ML libraries that must NOT be loaded by importing the module
# (FCL-3b — mirrors the S3/BT-1b discipline).
_HEAVY_LIBS = ("sklearn", "scipy", "numpy", "xgboost", "shap")


def _assert_import_loads_nothing_forbidden(
    import_stmts: str, forbidden: tuple[str, ...]
) -> None:
    """Run *import_stmts* in a fresh interpreter; assert no forbidden module loads."""
    code = textwrap.dedent(
        f"""
        import sys
        {import_stmts}
        offenders = sorted(
            mod
            for mod in sys.modules
            if any(mod == prefix or mod.startswith(prefix + ".") for prefix in {forbidden!r})
        )
        assert not offenders, (
            "Importing the league consensus module loaded forbidden modules "
            f"at module load time: {{offenders}}"
        )
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(BACKEND_DIR),
        timeout=120,
    )
    assert result.returncode == 0, (
        "Isolated import check failed — a forbidden module was loaded at "
        "import time (or the import itself errored):\n"
        f"--- code ---\n{code}"
        f"--- stdout ---\n{result.stdout}"
        f"--- stderr ---\n{result.stderr}"
    )


def test_importing_league_consensus_does_not_load_heavy_libs() -> None:
    _assert_import_loads_nothing_forbidden(
        "import packages.shared.services.league_consensus", _HEAVY_LIBS
    )


def test_importing_league_consensus_does_not_import_app() -> None:
    # Deferred app imports convention: packages.shared stays free of
    # app.* at module load (the app imports shared, never the reverse).
    _assert_import_loads_nothing_forbidden(
        "import packages.shared.services.league_consensus", ("app",)
    )


def test_xgboost_import_is_deferred_into_the_loader_body() -> None:
    import packages.shared.services.league_consensus as league_consensus_module

    loader_source = inspect.getsource(load_league_boosted_model)
    assert "import xgboost" in loader_source, (
        "load_league_boosted_model must import xgboost INSIDE its body "
        "(FCL-3b), not rely on a module-level import"
    )
    module_source = inspect.getsource(league_consensus_module)
    top_level_xgb = [
        line
        for line in module_source.splitlines()
        if line.startswith("import xgboost") or line.startswith("from xgboost")
    ]
    assert not top_level_xgb, (
        f"module top level must not import xgboost, found: {top_level_xgb}"
    )


def test_load_league_boosted_model_round_trips_artifact_bytes() -> None:
    pytest.importorskip("xgboost")
    import numpy as np
    import xgboost as xgb

    X = np.array([[1.0], [2.0], [3.0], [4.0]])
    y = np.array([1.0, 2.0, 3.0, 4.0])
    reg = xgb.XGBRegressor(n_estimators=2, max_depth=1)
    reg.fit(X, y)
    artifact = reg.get_booster().save_raw(raw_format="json")

    loaded = load_league_boosted_model(artifact)
    expected = float(reg.predict(np.array([[3.0]]))[0])
    got = float(loaded.predict(np.array([[3.0]]))[0])
    assert got == pytest.approx(expected)


def test_league_boosted_tip_composes_features_and_artifact_bytes() -> None:
    pytest.importorskip("xgboost")
    import numpy as np
    import xgboost as xgb

    prior = [_event(1, home_pid=11, away_pid=21, home_score=90, away_score=70)]
    event = _event(2)
    row = build_league_feature_vector(event, prior, [], LEAGUE_HEURISTICS)
    X = np.array([row] * 4, dtype=float)
    y = np.array([12.0, -8.0, 5.0, -3.0])
    reg = xgb.XGBRegressor(n_estimators=2, max_depth=1)
    reg.fit(X, y)
    artifact = reg.get_booster().save_raw(raw_format="json")

    tip = league_boosted_tip(event, prior, [], artifact)

    assert tip.selected_side_id in (101, 102)
    assert tip.margin >= 1
    assert 0.50 <= tip.confidence <= 0.95


def test_league_boosted_tip_drawn_event_is_draw_no_pick() -> None:
    # No xgboost needed: the drawn branch must return BEFORE any
    # artifact deserialization is attempted.
    event = _event(9, home_score=80, away_score=80)
    tip = league_boosted_tip(event, [], [], b"not-really-a-model")
    assert tip == LeagueConsensusTip(None, 0.50, 0)
