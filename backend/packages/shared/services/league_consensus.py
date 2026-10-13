"""League consensus — the derived per-league heuristics (FCL-3).

The league-pipeline counterpart of the AFL ``best_bet`` /
``weighted_tip`` / ``boosted_tip`` trio: every league's model set
automatically yields all three derived heuristics, with no per-league
code — the model list alone decides the feature contract:

* ``best_bet`` (:func:`pick_best_bet`) — pure majority vote over the
  league's base-model picks.  Available instantly on sync; no training.
* ``weighted_tip`` (:func:`league_linear_tip`) — a learned linear
  combiner over numeric per-model features, predicting the
  **home-signed margin** (mirrors System A's weighted_tip).
* ``boosted_tip`` (:func:`league_boosted_tip`) — an XGBoost combiner
  over the SAME feature contract, served from persisted artifact bytes.

Design decisions (FCL-3):

* **FCL-3a — the league tie-break is the home side.**  The AFL
  majority-vote rules break ties on the alphabetically first team, a
  rule that needs team *names*.  League picks are ``event_participants``
  side ids, so the deterministic home/away-neutral counterpart is the
  HOME side — the same convention every league pick function already
  uses (form/ladder/elo/matchup all tie → home).  Empty/zero-info
  votes resolve to the same home default; a drawn event is always
  draw-no-pick (NULL selection), never a fabricated tip.
* **FCL-3b — one feature contract, lazy heavy imports (BT-1a/BT-1b
  precedent).**  Feature values and their names are ALWAYS derived
  from the same model list via :func:`league_feature_names_for` /
  :func:`build_league_feature_vector`, and prediction raises
  :class:`ValueError` on a length mismatch — so a vector and its names
  can never silently drift apart.  The primitives are IMPORTED from
  :mod:`packages.shared.services.league_heuristics`, never copied.
  xgboost is imported INSIDE :func:`load_league_boosted_model` only —
  importing this module must stay memory-cheap for every always-on API
  worker (pinned by ``tests/unit/test_league_consensus.py`` in the
  ``test_lazy_sklearn`` style).
* **FCL-3c — bytes are the storage format.**  The per-league retrain
  persists ``booster.save_raw(raw_format="json")`` bytes; this module
  deserializes them per call (or accepts a preloaded model — BT-2 — so
  a TTL-caching caller never pays deserialization per tip).
* **FCL-3d — graceful pre-training behaviour.**  Before a league's
  first training run both combiners fall back to the majority vote
  (:func:`league_consensus_fallback`), so all three derived heuristics
  produce tips from day one and a missing model never crashes tip
  generation.

Everything here is PURE: frozen snapshots and plain functions over
them — no DB, no ORM, no look-ahead (the caller owns the
chronological slicing, exactly as for the pick functions), and no
``app.*`` import at module load.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from .league_heuristics import (
    ELO_START_RATING,
    HEURISTIC_ELO,
    HEURISTIC_FORM,
    HEURISTIC_HOME_ADVANTAGE,
    HEURISTIC_LADDER,
    HEURISTIC_MATCHUP,
    LEAGUE_HEURISTICS,
    LEAGUE_MODELS,
    CompletedEvent,
    Standing,
    TipPick,
    compute_elo_ratings,
    compute_standings,
    head_to_head_wins,
    is_drawn,
    wins_in_last_n,
)

#: The best-bet consensus heuristic's name on ``league_tips``.
HEURISTIC_BEST_BET = "best_bet"

#: Stand-in for an infinite ladder percentage (nothing conceded yet):
#: dominant but finite, so features stay ML-safe for the weekly fit.
LADDER_PCT_INFINITY_SENTINEL = 999.0

#: The majority-vote fallback's fixed confidence (mirrors
#: ``weighted_tip_fallback``) and margin — league base picks carry no
#: margins of their own, so the AFL fallback's no-margin default (6)
#: is always the honest value here.
FALLBACK_CONFIDENCE = 0.55
FALLBACK_MARGIN = 6


# ---------------------------------------------------------------------------
# Pure domain snapshot — the combiners' tip shape.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LeagueConsensusTip:
    """One derived heuristic's tip for one event.

    The league analogue of the AFL ``(winner, confidence, margin)``
    prediction tuple, with the side id (``event_participants.id``)
    standing in for the team name.  ``selected_side_id is None`` means
    draw-no-pick; confidence/margin are advisory for that case.
    """

    selected_side_id: Optional[int]
    confidence: float
    margin: int


# ---------------------------------------------------------------------------
# Majority vote (FCL-3a) — shared by best_bet and the combiner fallback.
# ---------------------------------------------------------------------------


def _majority_vote_side(
    event: CompletedEvent, base_picks: Sequence[TipPick]
) -> Optional[int]:
    """The side with strictly more votes; a tie (or zero-info) → home.

    Only this event's non-NULL picks vote — draw-no-pick selections and
    rows for other events are ignored, so a NULL base pick can never be
    miscounted as a home vote.  With ``away_votes == home_votes`` (a
    split) or no votes at all, the home side wins — the deterministic
    FCL-3a tie-break.
    """
    home, away = event.home_side_id, event.away_side_id
    votes = [
        pick.selected_side_id
        for pick in base_picks
        if pick.event_id == event.event_id and pick.selected_side_id is not None
    ]
    home_votes = votes.count(home) if home is not None else 0
    away_votes = votes.count(away) if away is not None else 0
    return away if away_votes > home_votes else home


def pick_best_bet(
    event: CompletedEvent, base_picks: Sequence[TipPick]
) -> TipPick:
    """The league ``best_bet`` tip: majority vote over the base picks.

    The consensus over whatever model set the league resolves to — the
    side with strictly more votes wins; a vote tie goes to the home
    side (FCL-3a); empty/zero-info input resolves to the same home
    default; a drawn event is draw-no-pick (NULL selection), matching
    the base models' own draw discipline.
    """
    if is_drawn(event):
        return TipPick(event.event_id, HEURISTIC_BEST_BET, None)
    return TipPick(
        event.event_id, HEURISTIC_BEST_BET, _majority_vote_side(event, base_picks)
    )


# ---------------------------------------------------------------------------
# Numeric feature contract (FCL-3b) — one registry, names and values
# derived from the same model list.
# ---------------------------------------------------------------------------


def _home_constant_features(
    _event: CompletedEvent, _history: Sequence[CompletedEvent]
) -> tuple[float, ...]:
    """Home advantage as a constant regressor (the intercept's co-runner)."""
    return (1.0,)


def _form_wins_diff_features(
    event: CompletedEvent, prior_history: Sequence[CompletedEvent]
) -> tuple[float, ...]:
    """Home minus away wins across each side's last five completed events."""
    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None:
        return (0.0,)
    diff = wins_in_last_n(home, prior_history) - wins_in_last_n(away, prior_history)
    return (float(diff),)


def _elo_rating_diff_features(
    event: CompletedEvent, prior_history: Sequence[CompletedEvent]
) -> tuple[float, ...]:
    """Home minus away result-derived Elo rating (start 1500, K=20)."""
    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None:
        return (0.0,)
    ratings = compute_elo_ratings(prior_history)
    return (
        ratings.get(home, ELO_START_RATING) - ratings.get(away, ELO_START_RATING),
    )


def _finite_percentage(percentage: float) -> float:
    """A clean-sheet percentage (inf) becomes the finite sentinel.

    ``inf`` features (and worse, ``inf - inf == nan``) would poison the
    weekly linear/boosted fit; the sentinel is far above any real AFL
    ladder percentage while keeping equal clean sheets at a 0.0 diff.
    """
    if math.isinf(percentage):
        return LADDER_PCT_INFINITY_SENTINEL
    return percentage


def _ladder_features(
    event: CompletedEvent, season_history: Sequence[CompletedEvent]
) -> tuple[float, ...]:
    """Season-to-date ladder as two features: wins diff and percentage diff.

    Reads the SAME-season prefix only (standings never cross seasons —
    the same scoping the ``ladder`` pick applies).
    """
    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None:
        return (0.0, 0.0)
    table = compute_standings(season_history)
    home_row = table.get(home, Standing(home, 0, 0, 0))
    away_row = table.get(away, Standing(away, 0, 0, 0))
    return (
        float(home_row.wins - away_row.wins),
        _finite_percentage(home_row.percentage)
        - _finite_percentage(away_row.percentage),
    )


def _h2h_wins_diff_features(
    event: CompletedEvent, prior_history: Sequence[CompletedEvent]
) -> tuple[float, ...]:
    """Home minus away decided head-to-head wins (venue-blind)."""
    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None:
        return (0.0,)
    wins_home, wins_away = head_to_head_wins(home, away, prior_history)
    return (float(wins_home - wins_away),)


@dataclass(frozen=True)
class LeagueFeatureSpec:
    """One league model's numeric encoding.

    ``names`` and ``build`` are declared together so the name contract
    and the produced values can never be authored apart; the import-time
    validation below fails fast if a builder's arity drifts from its
    declared names.
    """

    model: str
    names: tuple[str, ...]
    build: Callable[[CompletedEvent, Sequence[CompletedEvent]], tuple[float, ...]]


#: Per-model feature registry — keyed by the SAME names as
#: :data:`packages.shared.services.league_heuristics.LEAGUE_MODELS`.
#: Adding a league model means adding a spec here (or import fails —
#: the repo's fail-fast registration idiom).
LEAGUE_FEATURE_SPECS: dict[str, LeagueFeatureSpec] = {
    spec.model: spec
    for spec in (
        LeagueFeatureSpec(
            HEURISTIC_HOME_ADVANTAGE,
            ("home_advantage_home_constant",),
            _home_constant_features,
        ),
        LeagueFeatureSpec(
            HEURISTIC_FORM,
            ("form_wins_diff",),
            _form_wins_diff_features,
        ),
        LeagueFeatureSpec(
            HEURISTIC_LADDER,
            ("ladder_wins_diff", "ladder_pct_diff"),
            _ladder_features,
        ),
        LeagueFeatureSpec(
            HEURISTIC_ELO,
            ("elo_rating_diff",),
            _elo_rating_diff_features,
        ),
        LeagueFeatureSpec(
            HEURISTIC_MATCHUP,
            ("matchup_h2h_wins_diff",),
            _h2h_wins_diff_features,
        ),
    )
}


def _validate_feature_registry() -> None:
    """Fail fast when the feature registry and the model registry drift.

    Mirrors ``league_heuristics._validate_registry``: every league model
    must have a feature spec and vice versa, and each spec's builder
    must produce exactly as many values as it declares names.
    """
    unknown = [model for model in LEAGUE_FEATURE_SPECS if model not in LEAGUE_MODELS]
    missing = [model for model in LEAGUE_MODELS if model not in LEAGUE_FEATURE_SPECS]
    if unknown or missing:
        raise ValueError(
            "League feature registry out of sync with LEAGUE_MODELS: "
            f"unknown models {unknown}, missing feature specs {missing}"
        )
    probe = CompletedEvent(
        event_id=0,
        season_id=0,
        starts_at=None,
        home_side_id=1,
        away_side_id=2,
        home_participant_id=3,
        away_participant_id=4,
        home_score=None,
        away_score=None,
    )
    for model, spec in LEAGUE_FEATURE_SPECS.items():
        produced = spec.build(probe, ())
        if len(produced) != len(spec.names):
            raise ValueError(
                f"Feature spec for {model!r} produces {len(produced)} values "
                f"but declares {len(spec.names)} names"
            )


_validate_feature_registry()


def _require_feature_spec(model: str) -> LeagueFeatureSpec:
    """The feature spec for ``model``; ValueError for an unknown name."""
    spec = LEAGUE_FEATURE_SPECS.get(model)
    if spec is None:
        raise ValueError(
            f"Unknown league model {model!r} — the league feature contract "
            f"covers: {sorted(LEAGUE_FEATURE_SPECS)}"
        )
    return spec


def league_feature_names_for(model_names: Sequence[str]) -> list[str]:
    """Flatten per-model feature names in ``model_names`` order.

    The single source of the naming contract (mirrors weighted_tip's
    ``feature_names_for``): the vector built from one model list and the
    names zipped against it are always produced from the SAME list —
    see :func:`build_league_feature_vector` and
    :func:`league_predict_home_margin`.
    """
    names: list[str] = []
    for model in model_names:
        names.extend(_require_feature_spec(model).names)
    return names


def build_league_feature_vector(
    event: CompletedEvent,
    prior_history: Sequence[CompletedEvent],
    season_history: Sequence[CompletedEvent],
    model_names: Sequence[str] = LEAGUE_HEURISTICS,
) -> list[float]:
    """The ordered numeric feature vector for one event.

    ``model_names`` (default: the D3 trio) selects and orders the
    features.  Each model reads the slice its pick function reads —
    spanning models (form, elo, matchup) the cross-season
    ``prior_history``; season-scoped ladder the ``season_history`` —
    so the learned models see exactly the evidence the base picks saw
    (no look-ahead; the caller owns the chronological slicing).
    Pure and side-effect-free.
    """
    features: list[float] = []
    for model in model_names:
        spec = _require_feature_spec(model)
        history = (
            prior_history if LEAGUE_MODELS[model].spans_seasons else season_history
        )
        features.extend(spec.build(event, history))
    return features


# ---------------------------------------------------------------------------
# Pure linear combiner — the league weighted_tip (FCL-3).
# ---------------------------------------------------------------------------


def league_predict_home_margin(
    features: Sequence[float],
    intercept: float,
    coefficients: Mapping[str, float],
    feature_names: Optional[Sequence[str]] = None,
) -> float:
    """Linear combination: ``intercept + sum(coef[name] * value)``.

    ``coefficients`` is keyed by feature name (as stored on
    ``model_versions``/``model_coefficients``); a name without an entry
    contributes ``0.0``.  Pass ``feature_names`` whenever the vector was
    built from a non-default ``model_names`` list — the names and the
    vector must come from the same list.  Raises :class:`ValueError` on
    a length mismatch instead of silently truncating the zip and
    mis-weighting the prediction.  Pure function.
    """
    names = (
        league_feature_names_for(LEAGUE_HEURISTICS)
        if feature_names is None
        else feature_names
    )
    if len(features) != len(names):
        raise ValueError(
            f"features/names length mismatch: got {len(features)} features "
            f"but {len(names)} feature names — the vector and the names "
            "must come from the same model list"
        )
    total = float(intercept)
    for name, value in zip(names, features):
        total += float(coefficients.get(name, 0.0)) * float(value)
    return total


def league_margin_to_tip(event: CompletedEvent, y_pred: float) -> LeagueConsensusTip:
    """Map a home-signed margin to a tip — ``home_margin_to_tip`` semantics.

    * side = home when ``y_pred >= 0`` else away
    * margin = ``max(1, round(abs(y_pred)))``
    * confidence = ``clamp(0.50 + abs(y_pred) * 0.015, 0.50, 0.95)``

    Exactly the AFL weighted_tip mapping, with the side id standing in
    for the team name.  Deterministic and pure.
    """
    selected = event.home_side_id if y_pred >= 0 else event.away_side_id
    margin = max(1, int(round(abs(y_pred))))
    confidence = round(min(0.95, max(0.50, 0.50 + abs(y_pred) * 0.015)), 3)
    return LeagueConsensusTip(selected, confidence, margin)


def league_linear_tip(
    event: CompletedEvent,
    prior_history: Sequence[CompletedEvent],
    season_history: Sequence[CompletedEvent],
    *,
    intercept: float,
    coefficients: Mapping[str, float],
    model_names: Sequence[str] = LEAGUE_HEURISTICS,
) -> LeagueConsensusTip:
    """Compose features → margin → tip for the learned-linear path.

    The feature vector and the coefficient-name order are ALWAYS derived
    from the same ``model_names`` list, so they can never drift apart.
    A drawn event is draw-no-pick (the D3 discipline the base picks
    follow) — the predicted margin is not fabricated into a side.
    """
    if is_drawn(event):
        return LeagueConsensusTip(None, 0.50, 0)
    names = league_feature_names_for(model_names)
    features = build_league_feature_vector(
        event, prior_history, season_history, model_names
    )
    y_pred = league_predict_home_margin(
        features, intercept, coefficients, feature_names=names
    )
    return league_margin_to_tip(event, y_pred)


# ---------------------------------------------------------------------------
# Majority-vote fallback (FCL-3d) — the pre-training behaviour of BOTH
# combiners (mirrors weighted_tip_fallback; tie → home per FCL-3a).
# ---------------------------------------------------------------------------


def league_consensus_fallback(
    event: CompletedEvent, base_picks: Sequence[TipPick]
) -> LeagueConsensusTip:
    """Majority-vote tip used before a league's first training run.

    The league counterpart of ``weighted_tip_fallback``: the strictly
    more-voted side wins, a vote tie and empty input resolve to the
    home side (FCL-3a), confidence is fixed at 0.55 and the margin at
    the AFL fallback's no-margin default (6) — league base picks carry
    no margins to average.  A drawn event is draw-no-pick.
    """
    if is_drawn(event):
        return LeagueConsensusTip(None, FALLBACK_CONFIDENCE, 0)
    return LeagueConsensusTip(
        _majority_vote_side(event, base_picks), FALLBACK_CONFIDENCE, FALLBACK_MARGIN
    )


# ---------------------------------------------------------------------------
# Boosted combiner contract (FCL-3b/FCL-3c) — same features, XGBoost.
# ---------------------------------------------------------------------------


def load_league_boosted_model(model_bytes: bytes, artifact_format: str = "json") -> Any:
    """Deserialize XGBoost artifact bytes into a predict-capable regressor.

    FCL-3b: the ``import xgboost`` is deliberately INSIDE this function —
    importing this module must stay memory-cheap for every always-on API
    worker; only callers that actually hold an artifact pay for it.

    ``model_bytes`` is the output of ``save_raw(raw_format="json")`` as
    persisted for the league's boosted combiner.  ``artifact_format``
    mirrors the stored tag; XGBoost's ``load_model`` detects the buffer
    format from the bytes themselves, so the tag is advisory metadata
    kept in parity with the DB column (FCL-3c, BT-1c precedent).
    Returns the loaded ``XGBRegressor`` (typed ``Any`` to avoid
    importing xgboost for annotations).
    """
    import xgboost as xgb  # FCL-3b: lazy — never at module top level

    reg = xgb.XGBRegressor()
    # xgboost raises TypeError("Unknown file type") on plain ``bytes``
    # (BT-1c finding) — normalise to bytearray, the one chokepoint
    # every league artifact load goes through.
    reg.load_model(bytearray(model_bytes))
    return reg


def league_predict_home_margin_boosted(
    model_or_bytes: Any,
    features: Sequence[float],
    feature_names: Optional[Sequence[str]] = None,
    artifact_format: str = "json",
) -> float:
    """Predict the home-signed margin from a boosted model + one feature row.

    ``model_or_bytes`` is additive (BT-2): either the raw artifact bytes
    (deserialized here via :func:`load_league_boosted_model`) OR an
    already-deserialized, predict-capable model — so a TTL-caching
    caller never pays deserialization per tip.

    ``features`` must be built with :func:`build_league_feature_vector`.
    Pass ``feature_names`` whenever the vector was built from a
    non-default ``model_names`` list.  Raises :class:`ValueError` on a
    length mismatch BEFORE the xgboost import, so bad input fails loud
    and cheap.  Pure and stateless: caching belongs to the caller.
    """
    names = (
        league_feature_names_for(LEAGUE_HEURISTICS)
        if feature_names is None
        else feature_names
    )
    if len(features) != len(names):
        raise ValueError(
            f"features/names length mismatch: got {len(features)} features "
            f"but {len(names)} feature names — the vector and the names "
            "must come from the same model list"
        )
    if isinstance(model_or_bytes, (bytes, bytearray, memoryview)):
        model = load_league_boosted_model(bytes(model_or_bytes), artifact_format)
    else:
        model = model_or_bytes
    prediction = model.predict([list(features)])
    return float(prediction[0])


def league_boosted_tip(
    event: CompletedEvent,
    prior_history: Sequence[CompletedEvent],
    season_history: Sequence[CompletedEvent],
    model_or_bytes: Any,
    *,
    model_names: Sequence[str] = LEAGUE_HEURISTICS,
    artifact_format: str = "json",
) -> LeagueConsensusTip:
    """Compose features → boosted margin → tip for the learned-boosted path.

    Shares the linear path's feature contract and tip mapping exactly —
    only the margin predictor differs.  A drawn event is draw-no-pick.
    """
    if is_drawn(event):
        return LeagueConsensusTip(None, 0.50, 0)
    names = league_feature_names_for(model_names)
    features = build_league_feature_vector(
        event, prior_history, season_history, model_names
    )
    y_pred = league_predict_home_margin_boosted(
        model_or_bytes, features, feature_names=names, artifact_format=artifact_format
    )
    return league_margin_to_tip(event, y_pred)
