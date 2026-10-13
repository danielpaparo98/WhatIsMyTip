"""Per-league retrain service (FCL-5): linear + boosted per competition.

The league-pipeline counterpart of the AFL retrain pair
(:mod:`packages.shared.services.model_retrain` +
:mod:`packages.shared.services.boosted_retrain`): the weekly cron also
fits EVERY competition's own ``weighted_tip`` linear combiner and
``boosted_tip`` XGBoost combiner on that competition's completed-event
history (``events``/``event_participants`` — never the legacy ``games``
tables), so each league's learned heuristics see only its own results.
All leagues are first-class (FCL design point 4): the competition list
comes from the events tables themselves — every competition with
completed-event history is trained, whether or not it appears in
:data:`packages.shared.services.league_heuristics.LEAGUE_MODEL_SETS`.

Design decisions (FCL-5):

* **FCL-5a — one dataset per competition, two models.**  Training rows
  are gathered ONCE per competition and feed BOTH fits (the BT-1
  decision-4 reuse pattern).  Features come from
  :func:`packages.shared.services.league_consensus.build_league_feature_vector`
  — the EXACT contract the runtime combiners predict with, for the
  league's resolved model set (league → sport → default) — and the
  target is the home-signed margin ``home_score - away_score``
  (mirrors System A).  Drawn events are valid rows (target ``0.0``).
* **FCL-5b — no look-ahead, by construction.**  Each row's evidence is
  the chronological prefix strictly BEFORE that event — cross-season
  for spanning models (form, elo, matchup), same-season for ladder —
  the exact slicing discipline of
  :func:`packages.shared.services.league_heuristics.compute_all_picks`.
  Pinned by tests that truncate the history and mutate a LATER result:
  every earlier row must stay byte-identical.
* **FCL-5c — league-scale skip gate.**  League histories are one to two
  orders of magnitude smaller than the AFL ``games`` corpus (a 6-8 team
  state league produces roughly 50-80 completed events per season), so
  System A's 100-row gate would leave most leagues untrained forever.
  The gate is :data:`MIN_LEAGUE_TRAINING_ROWS` = 30 — still ≥ 5
  observations per feature for the at-most-6-feature contract — and is
  reachable within a league's FIRST season, so a synced league becomes
  learned within its first year.  Below the gate nothing is written:
  the currently-active scoped versions stay untouched and the runtime
  combiners keep their majority-vote fallback (FCL-3d).
* **FCL-5d — lazy heavy imports (S3 / BT-1 precedent).**  numpy,
  scikit-learn, xgboost and shap are imported INSIDE
  :func:`run_league_retrain`, strictly PAST the skip gate — a skipped
  league never loads them, and the always-on API workers (whose
  scheduler imports the cron module, which imports this module) never
  pay their resident-memory cost at import time.  This module also
  imports NO ``app.*`` at module load (the app imports shared, never
  the reverse).  Pinned by
  ``tests/unit/test_league_retrain_service.py``.
* **FCL-5e — scoped storage, global rows untouchable.**  Both versions
  persist via :func:`packages.shared.crud.model_versions.create_model_version`
  with ``competition_id`` set, so promotion deactivates ONLY that
  competition's rows — the legacy NULL-scope AFL actives are never
  touched (pinned by test).  Version numbers are allocated across ALL
  scopes for the model name: the pre-existing
  ``uq_model_versions_name_version`` constraint keeps ``(model_name,
  version)`` unique table-wide, so a per-scope counter would hand the
  SAME number to two different leagues — see
  :func:`_next_version_number`.
* **FCL-5f — never fail the weekly job.**  :func:`run_all_league_retrains`
  isolates each competition: one league's failure is logged and
  reported in its result dict while the remaining leagues still train,
  and the cron wrapper catches a failure of the pass itself — the
  existing linear/boosted summary keys stay untouched either way.

The SHAP flow mirrors ``services/boosted_retrain.py`` exactly (mean
|SHAP| persisted as coefficient rows, base value on the version row,
additivity asserted as a runtime check that survives ``python -O``).
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ..crud.model_versions import create_model_version
from ..logger import get_logger
from ..models import Competition, Event, EventParticipant, ModelVersion, Season
from .league_consensus import build_league_feature_vector, league_feature_names_for
from .league_heuristics import (
    LEAGUE_HEURISTICS,
    CompletedEvent,
    league_model_set_for_competition,
    sort_chronologically,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

#: Name under which the league linear combiner versions are stored (the
#: System A name, scoped by ``model_versions.competition_id`` — a league
#: row never promotes a global row, FCL-5e).
WEIGHTED_TIP_MODEL_NAME: str = "weighted_tip"

#: Name under which the league boosted combiner versions are stored
#: (same scoping as :data:`WEIGHTED_TIP_MODEL_NAME`).
BOOSTED_TIP_MODEL_NAME: str = "boosted_tip"

#: Minimum usable training rows before a league's models are (re)fit
#: (FCL-5c).  League histories are one to two orders of magnitude
#: smaller than the AFL ``games`` corpus — a 6-8 team state league
#: produces roughly 50-80 completed events per season — so System A's
#: 100-row gate would keep most leagues untrained indefinitely.  30
#: rows is reachable from a league's first season while still giving
#: the at-most-6-feature contract ≥ 5 observations per feature; below
#: it the currently-active scoped versions stay untouched and the
#: runtime combiners serve the majority-vote fallback (FCL-3d).
MIN_LEAGUE_TRAINING_ROWS: int = 30

#: Deterministic baseline XGBRegressor parameters for the league
#: boosted combiner.  Mirrors the System A baseline (``BOOSTED_PARAMS``)
#: but with a proportionally smaller ensemble: league histories are
#: 1-2 orders of magnitude smaller than the AFL corpus, and 400 trees
#: over a 30-row fit would memorise it.  ``random_state=42`` pins the
#: subsample/colsample draws so identical data reproduces the model
#: byte-for-byte; ``params`` on :func:`run_league_retrain` is the
#: tuning hook (full replacement, no merging).
LEAGUE_BOOSTED_PARAMS: dict[str, Any] = {
    "n_estimators": 150,
    "max_depth": 3,
    "learning_rate": 0.08,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "tree_method": "hist",
    "eval_metric": "rmse",
    "random_state": 42,
    "n_jobs": 4,
}

#: A training row is ``(feature_vector, target_signed_home_margin)``
#: built from one usable completed event (FCL-5a).
LeagueTrainingRow = tuple[list[float], float]


# ---------------------------------------------------------------------------
# Data gathering (DB access — patched out in unit tests)
# ---------------------------------------------------------------------------


async def _fetch_competition(
    session: AsyncSession, competition_id: int
) -> Optional[tuple[str, str]]:
    """The competition's ``(name, sport_id)``, or ``None`` without a row.

    The pair feeds :func:`league_model_set_for_competition` so training
    uses the SAME feature contract the runtime combiners resolve for the
    league (league → sport → default).  A missing row resolves through
    the default set rather than failing the pass.
    """
    row = (
        await session.execute(
            select(Competition.name, Competition.sport_id).where(
                Competition.id == competition_id
            )
        )
    ).first()
    if row is None:
        return None
    return str(row[0]), str(row[1])


async def _fetch_completed_events(
    session: AsyncSession, competition_id: int
) -> list[CompletedEvent]:
    """ALL of the competition's completed events with both sides.

    Full history (every season) — league datasets are small, so the
    retrain takes every usable row it can get; the feature builders
    scope ladder evidence to the event's own season internally
    (FCL-5b).  Mirrors ``LeagueHeuristicsService._fetch_completed_events``
    but scoped via ``Season.competition_id`` instead of a season list.
    """
    home = aliased(EventParticipant)
    away = aliased(EventParticipant)
    rows = (
        await session.execute(
            select(
                Event.id,
                Event.season_id,
                Event.starts_at,
                home.id,
                away.id,
                home.participant_id,
                away.participant_id,
                home.score,
                away.score,
            )
            .join(Season, Season.id == Event.season_id)
            .join(home, and_(home.event_id == Event.id, home.side == "home"))
            .join(away, and_(away.event_id == Event.id, away.side == "away"))
            .where(
                Season.competition_id == competition_id,
                Event.completed.is_(True),
            )
        )
    ).all()
    return [
        CompletedEvent(
            event_id=int(row[0]),
            season_id=int(row[1]),
            starts_at=row[2],
            home_side_id=int(row[3]),
            away_side_id=int(row[4]),
            home_participant_id=int(row[5]),
            away_participant_id=int(row[6]),
            home_score=row[7],
            away_score=row[8],
        )
        for row in rows
    ]


async def _competitions_with_history(session: AsyncSession) -> list[int]:
    """Distinct competition ids with at least one usable completed event.

    Usable = completed AND both a home and an away side (the same shape
    a training row needs).  Deliberately unrestricted by
    :data:`LEAGUE_MODEL_SETS` — all leagues are first-class (FCL-5):
    discovery is registration-free, driven by the data alone.
    """
    home = aliased(EventParticipant)
    away = aliased(EventParticipant)
    rows = (
        await session.execute(
            select(Season.competition_id)
            .join(Event, Event.season_id == Season.id)
            .join(home, and_(home.event_id == Event.id, home.side == "home"))
            .join(away, and_(away.event_id == Event.id, away.side == "away"))
            .where(Event.completed.is_(True))
            .distinct()
            .order_by(Season.competition_id)
        )
    ).all()
    return [int(row[0]) for row in rows]


# ---------------------------------------------------------------------------
# Pure training-row builder — no DB, no look-ahead (FCL-5a/5b).
# ---------------------------------------------------------------------------


def build_league_training_rows(
    events: Sequence[CompletedEvent],
    *,
    model_names: Sequence[str] = LEAGUE_HEURISTICS,
) -> list[LeagueTrainingRow]:
    """One ``(features, home-signed margin)`` row per usable event.

    Usable = completed with both participants and both scores (undecided
    and side-less events contribute NO row but DO remain evidence for
    later events — the pick functions already treat them as non-wins).
    Evidence for each row is the chronological prefix strictly before
    it: spanning models (form, elo, matchup) read the cross-season
    prefix, season-scoped ladder the same-season prefix — the exact
    slicing of :func:`packages.shared.services.league_heuristics.
    compute_all_picks`, so the learned models see precisely what the
    base picks saw (FCL-5b: no look-ahead).  A drawn event is a valid
    row (target ``0.0``).  ``model_names`` selects and orders the
    feature contract — pass the league's resolved set so the fit matrix
    matches the runtime combiners' contract.  Pure function.
    """
    history = sort_chronologically(events)
    rows: list[LeagueTrainingRow] = []
    for index, event in enumerate(history):
        if (
            event.home_participant_id is None
            or event.away_participant_id is None
            or event.home_score is None
            or event.away_score is None
        ):
            continue
        prior = history[:index]
        same_season = [e for e in prior if e.season_id == event.season_id]
        features = build_league_feature_vector(event, prior, same_season, model_names)
        target = float(event.home_score - event.away_score)  # signed home margin
        rows.append((features, target))
    return rows


# ---------------------------------------------------------------------------
# Version allocation (FCL-5e) — the cross-scope counter.
# ---------------------------------------------------------------------------


async def _next_version_number(session: AsyncSession, model_name: str) -> int:
    """Next free version for ``model_name`` across ALL scopes.

    The pre-existing ``uq_model_versions_name_version`` constraint keeps
    ``(model_name, version)`` unique across the WHOLE table — NULL scope
    and every competition alike (see ``crud.model_versions.next_version_number``'s
    caveat).  A per-scope counter is therefore unsafe for the legacy
    model names the league retrain reuses: two leagues would each see
    "no rows in MY scope" and both allocate version 1, and the second
    insert would violate the constraint.  The only collision-free
    allocation is ``max(version)`` over every row with this model name,
    regardless of scope — which is exactly what this counter takes.
    """
    result = await session.execute(
        select(func.max(ModelVersion.version)).where(
            ModelVersion.model_name == model_name
        )
    )
    return (result.scalar() or 0) + 1


# ---------------------------------------------------------------------------
# Fit + persist (heavy imports strictly past the skip gate — FCL-5d).
# ---------------------------------------------------------------------------


async def run_league_retrain(
    session: AsyncSession,
    *,
    competition_id: int,
    params: Optional[dict[str, Any]] = None,
) -> dict[str, object]:
    """Gather one competition's rows, fit BOTH combiners, persist scoped.

    Args:
        session: Active :class:`AsyncSession`.  Persistence goes through
            :func:`packages.shared.crud.model_versions.create_model_version`
            (single transaction per version, scoped promotion).
        competition_id: The competition to (re)train.  ALL of its
            completed-event history is used (league datasets are small).
        params: Optional full replacement for :data:`LEAGUE_BOOSTED_PARAMS`
            (the tuning hook — no merging is performed).  ``None`` uses
            the deterministic baseline.

    Returns:
        A summary dict.  ``status`` is ``"trained"`` (with
        ``competition_id``, ``model_names``, ``training_rows`` and a
        ``weighted_tip`` / ``boosted_tip`` sub-dict per model) or
        ``"skipped"`` with ``reason ==
        "insufficient_training_rows"`` when fewer than
        :data:`MIN_LEAGUE_TRAINING_ROWS` usable rows exist — in which
        case the currently-active scoped versions are left untouched
        (FCL-5c) and no heavy ML library is ever imported (FCL-5d).
    """
    competition = await _fetch_competition(session, competition_id)
    model_names = league_model_set_for_competition(
        competition[0] if competition else None,
        competition[1] if competition else None,
    )
    events = await _fetch_completed_events(session, competition_id)
    rows = build_league_training_rows(events, model_names=model_names)
    logger.info(
        "league-retrain competition=%s gathered %d usable training rows "
        "(min_required=%d, models=%s)",
        competition_id,
        len(rows),
        MIN_LEAGUE_TRAINING_ROWS,
        list(model_names),
    )

    if len(rows) < MIN_LEAGUE_TRAINING_ROWS:
        logger.warning(
            "league-retrain competition=%s skipped: only %d rows (< %d); "
            "keeping currently-active scoped versions",
            competition_id,
            len(rows),
            MIN_LEAGUE_TRAINING_ROWS,
        )
        return {
            "status": "skipped",
            "reason": "insufficient_training_rows",
            "competition_id": competition_id,
            "rows": len(rows),
            "min_required": MIN_LEAGUE_TRAINING_ROWS,
        }

    # Heavy ML deps are imported lazily (FCL-5d, S3/BT-1 precedent) so the
    # always-on API workers never pay the numpy + sklearn + xgboost + shap
    # resident-memory cost.  Only a competition that passed the skip gate
    # (above) ever reaches this line — a skipped league never loads them.
    import numpy as np
    import shap
    import xgboost as xgb
    from sklearn.linear_model import LinearRegression
    from sklearn.metrics import mean_absolute_error, r2_score

    feature_names = league_feature_names_for(model_names)
    used_params: dict[str, Any] = dict(
        LEAGUE_BOOSTED_PARAMS if params is None else params
    )

    X = np.array([features for features, _ in rows], dtype=float)  # noqa: N806 — ML design-matrix convention (mirrors model_retrain.py)
    y = np.array([target for _, target in rows], dtype=float)

    # --- linear combiner (the league weighted_tip) ------------------------
    # LinearRegression is deterministic, so two fits on identical data
    # yield identical intercept/coefficients.
    linear = LinearRegression()
    linear.fit(X, y)
    linear_preds = linear.predict(X)
    linear_metrics: dict[str, float] = {
        "r2": float(r2_score(y, linear_preds)),
        "mae": float(mean_absolute_error(y, linear_preds)),
    }
    intercept = float(linear.intercept_)
    coefficients: dict[str, float] = {
        name: float(coef) for name, coef in zip(feature_names, linear.coef_)
    }

    linear_version = await _next_version_number(session, WEIGHTED_TIP_MODEL_NAME)
    linear_mv = await create_model_version(
        session,
        model_name=WEIGHTED_TIP_MODEL_NAME,
        version=linear_version,
        intercept=intercept,
        training_rows=len(rows),
        metrics=linear_metrics,
        coefficients=coefficients,
        set_active=True,
        competition_id=competition_id,  # FCL-5e: scoped promotion ONLY
    )

    # --- boosted combiner (the league boosted_tip) ------------------------
    # XGBoost with a fixed random_state is deterministic on identical data.
    boosted = xgb.XGBRegressor(**used_params)
    boosted.fit(X, y)
    boosted_preds = boosted.predict(X)
    boosted_metrics: dict[str, float] = {
        "r2": float(r2_score(y, boosted_preds)),
        "mae": float(mean_absolute_error(y, boosted_preds)),
    }

    # Global SHAP explanation: mean |SHAP| per feature becomes the
    # persisted "coefficients" (BT-1 overload), the TreeExplainer base
    # value rides on the version row (FCL-3c storage contract).
    explainer = shap.TreeExplainer(boosted)
    sv = explainer.shap_values(X)  # (n_rows, n_features)
    importance = np.abs(sv).mean(axis=0)
    base = float(np.asarray(explainer.expected_value).ravel()[0])

    shap_importance: dict[str, float] = {
        name: float(value) for name, value in zip(feature_names, importance)
    }
    boosted_metrics["shap_base_value"] = base

    # Additivity sanity (Tree SHAP is exact for gbtree): prediction_i ==
    # base + sum_j shap_values[i, j].  Tolerance 1e-4, not machine
    # epsilon: shap_values are float32 accumulated over the features, so
    # ~1e-5 deviations are expected.  Deliberately a raise, not an
    # assert: this is the only runtime check that the artifact,
    # shap_base_value and SHAP-importance rows all describe the SAME
    # model — it must survive ``python -O``.  The cron's per-league
    # try/except turns the raise into the documented error result.
    max_deviation = float(np.abs(base + sv.sum(axis=1) - boosted_preds).max())
    if max_deviation > 1e-4:
        raise RuntimeError(
            f"SHAP additivity violated for league {BOOSTED_TIP_MODEL_NAME} "
            f"(competition {competition_id}): "
            f"max |base + sum(shap) - prediction| = {max_deviation:.3e}"
        )

    model_bytes: bytes = boosted.get_booster().save_raw(raw_format="json")

    boosted_version = await _next_version_number(session, BOOSTED_TIP_MODEL_NAME)
    boosted_mv = await create_model_version(
        session,
        model_name=BOOSTED_TIP_MODEL_NAME,
        version=boosted_version,
        intercept=0.0,  # the ensemble has no intercept; base value plays that role
        training_rows=len(rows),
        metrics=boosted_metrics,
        coefficients=shap_importance,
        artifact=model_bytes,
        artifact_format="json",
        shap_base_value=base,
        set_active=True,
        competition_id=competition_id,  # FCL-5e: scoped promotion ONLY
    )

    logger.info(
        "league-retrain competition=%s trained %s v%d + %s v%d "
        "training_rows=%d linear r2=%.4f mae=%.4f boosted r2=%.4f mae=%.4f "
        "shap_base=%.4f active=True (scoped)",
        competition_id,
        WEIGHTED_TIP_MODEL_NAME,
        linear_version,
        BOOSTED_TIP_MODEL_NAME,
        boosted_version,
        len(rows),
        linear_metrics["r2"],
        linear_metrics["mae"],
        boosted_metrics["r2"],
        boosted_metrics["mae"],
        base,
    )

    return {
        "status": "trained",
        "competition_id": competition_id,
        "model_names": list(model_names),
        "training_rows": len(rows),
        "weighted_tip": {
            "version": linear_version,
            "model_version_id": linear_mv.id,
            "intercept": intercept,
            "metrics": linear_metrics,
        },
        "boosted_tip": {
            "version": boosted_version,
            "model_version_id": boosted_mv.id,
            "metrics": boosted_metrics,
            "shap_importance": shap_importance,
            "params_used": used_params,
        },
    }


async def run_all_league_retrains(session: AsyncSession) -> dict[str, object]:
    """Retrain EVERY competition with completed-event history (FCL-5f).

    Discovers competitions from the events tables alone (all leagues are
    first-class — no ``LEAGUE_MODEL_SETS`` restriction) and trains each
    one via :func:`run_league_retrain`.  A competition's failure is
    logged and reported as ``{"status": "error", "reason": ...}`` in
    ``results`` while the remaining competitions still train — the pass
    itself never raises, so one bad league can never take down the
    weekly job (FCL-5f).  Committed versions of earlier competitions
    stay committed (partial-success semantics).

    Returns:
        ``{"competitions": <count discovered>, "results": {<str(id)>:
        <per-competition summary>}}`` — the cron nests this verbatim
        under its ``"leagues"`` key.
    """
    competition_ids = await _competitions_with_history(session)
    results: dict[str, dict[str, object]] = {}
    for competition_id in competition_ids:
        try:
            results[str(competition_id)] = await run_league_retrain(
                session, competition_id=competition_id
            )
        except Exception as exc:  # noqa: BLE001 — one league never fails the pass (FCL-5f)
            logger.exception(
                "league-retrain competition=%s failed; continuing with the "
                "remaining leagues: %r",
                competition_id,
                exc,
            )
            results[str(competition_id)] = {
                "status": "error",
                "reason": str(exc),
            }
    logger.info(
        "league-retrain pass finished: %d competitions with history, "
        "%d trained, %d errored",
        len(competition_ids),
        sum(1 for r in results.values() if r.get("status") == "trained"),
        sum(1 for r in results.values() if r.get("status") == "error"),
    )
    return {"competitions": len(competition_ids), "results": results}
