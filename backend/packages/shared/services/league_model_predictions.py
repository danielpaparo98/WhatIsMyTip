"""League model predictions — the numeric model breakdown (PARITY-1).

The league counterpart of the AFL orchestrator's ``model_predictions``
table: one row per ``(event_id, model_name)`` with the exact AFL
``model_predictions`` shape — ``winner`` (participant display name),
0..1 ``confidence`` and home-signed ``margin`` in points.

Six parity models, computed from the SAME chronological evidence
slices the ``league_tips`` picks walk uses (strict no-look-ahead):

* ``elo``            — logistic expected win probability of the higher
  rating (confidence) and rating-diff/10 (margin), mirroring
  ``models_ml/elo.py``.
* ``form``           — signed average scoring margin over each side's
  last :data:`FORM_WINDOW` events, mirroring ``models_ml/form.py``.
* ``home_advantage`` — the measured home-side win share across the
  competition's prior decided events, mirroring
  ``models_ml/home_advantage.py``'s venue-advantage logic.
* ``matchup``        — head-to-head win share + average H2H margin
  with recency weighting, mirroring ``models_ml/matchup.py`` (abstains
  with no decided meetings, like the AFL model).
* ``value``          — all-time win-rate differential, mirroring
  ``models_ml/value.py``'s constants exactly (clamp 0.5–0.9 / 1–80).
* ``weather_impact`` — same-venue similar-tier historical win rates
  over the weather-tagged prior events, mirroring
  ``models_ml/weather_impact.py`` (abstains without a weather row or
  a usable sample, like the AFL model).

Abstention contract: a model with no usable opinion writes NO row —
the same contract the AFL orchestrator applies to ``ABSTAINED``
results.  Persistence is diff-based (insert new, update changed,
untouched otherwise) so re-syncs don't churn.  The pass is invoked by
:meth:`LeagueHeuristicsService.generate_for_competition` — the same
generation entry point every sync and backfill already calls — so
deploying the parity storage immediately backfills every league's
model breakdown.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logger import get_logger
from ..models import (
    EventParticipant,
    LeagueEventWeather,
    LeagueModelPrediction,
    Participant,
)
from .league_heuristics import (
    ELO_START_RATING,
    FORM_WINDOW,
    CompletedEvent,
    LeagueHeuristicsService,
    compute_elo_ratings,
    compute_win_rates,
    head_to_head_wins,
    is_drawn,
    select_tip_seasons,
    sort_chronologically,
)

logger = get_logger(__name__)

#: Elo points per score point — mirrors ``models_ml/elo.py``'s margin
#: convention (``margin = elo_diff / 10``).
_ELO_PER_POINT = 10.0

#: Win-rate → margin scale, mirror of ``models_ml/value.py``.
_VALUE_MARGIN_SCALE = 150

#: Weather severity multipliers for margin calculation — mirrors
#: ``models_ml/weather_impact.py``.
_WEATHER_SEVERITY = {
    "good": 0.5,
    "moderate": 0.7,
    "challenging": 0.9,
    "poor": 1.2,
}

#: Minimum same-venue similar-condition events per side before a win
#: rate is trusted — mirrors ``models_ml/weather_impact.py``.
_WEATHER_MIN_SAMPLE = 3

#: H2H recency weighting — mirrors ``models_ml/matchup.py``'s decay
#: shape (recent meetings count more; nothing older than 10).
_H2H_MAX_WEIGHTED_GAMES = 10

_MIN_TIME = datetime.min


@dataclass(frozen=True)
class LeagueModelPredictionPick:
    """One model's numeric prediction for one event (persisted row)."""

    event_id: int
    model_name: str
    #: Participant display name; ``None`` never persists (abstain).
    winner: Optional[str]
    confidence: Optional[float]
    margin: Optional[int]


# ---------------------------------------------------------------------------
# Weather classification (mirror of WeatherImpactModel._classify_weather)
# ---------------------------------------------------------------------------


def classify_weather_tier(
    *,
    precipitation: Optional[float],
    wind_gusts: Optional[float],
    temperature: Optional[float],
) -> str:
    """Classify a weather snapshot into the four-tier scale."""
    score = 0.0
    if precipitation is not None:
        if precipitation > 5.0:
            score += 2.0
        elif precipitation > 1.0:
            score += 1.0
    if wind_gusts is not None:
        if wind_gusts > 50.0:
            score += 2.0
        elif wind_gusts > 35.0:
            score += 1.0
    if temperature is not None:
        if temperature > 35.0 or temperature < 10.0:
            score += 1.0
    if score >= 3.0:
        return "poor"
    if score >= 2.0:
        return "challenging"
    if score >= 1.0:
        return "moderate"
    return "good"


# ---------------------------------------------------------------------------
# Pure per-model numeric predictions.  Every function receives the same
# (event, prior_history) slices the pick functions read, plus the side
# NAMES the row stores.  ``None`` return = abstain (no row persisted).
# ---------------------------------------------------------------------------


def _decided_result(event: CompletedEvent) -> Optional[str]:
    """``'home'``/``'away'``/``None`` for a completed, decided event."""
    if not event.completed:
        return None
    if event.home_score is None or event.away_score is None:
        return None
    if event.home_score == event.away_score:
        return None
    return "home" if event.home_score > event.away_score else "away"


def predict_elo_numbers(
    event: CompletedEvent,
    prior_results: Sequence[CompletedEvent],
    *,
    home_name: str,
    away_name: str,
) -> Optional[LeagueModelPredictionPick]:
    """Elo: logistic win probability + rating-diff/10 margin."""
    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None:
        return None
    ratings = compute_elo_ratings(prior_results)
    r_home = ratings.get(home, ELO_START_RATING)
    r_away = ratings.get(away, ELO_START_RATING)
    diff = r_home - r_away
    expected_home = 1.0 / (1.0 + 10.0 ** (-diff / 400.0))
    if diff >= 0:
        winner = home_name
        confidence = expected_home
        margin = int(abs(diff) / _ELO_PER_POINT)
    else:
        winner = away_name
        confidence = 1.0 - expected_home
        margin = int(abs(diff) / _ELO_PER_POINT)
    return LeagueModelPredictionPick(
        event.event_id,
        "elo",
        winner,
        max(0.5, min(0.95, confidence)),
        max(1, min(100, margin)),
    )


def predict_form_numbers(
    event: CompletedEvent,
    prior_results: Sequence[CompletedEvent],
    *,
    home_name: str,
    away_name: str,
) -> Optional[LeagueModelPredictionPick]:
    """Form: signed average scoring margin over each side's last window."""

    def avg_margin(participant_id: int) -> Optional[float]:
        involved = [
            e
            for e in prior_results
            if participant_id in (e.home_participant_id, e.away_participant_id)
        ]
        recent = involved[-FORM_WINDOW:]
        margins = []
        for e in recent:
            if e.home_score is None or e.away_score is None:
                continue
            if e.home_participant_id == participant_id:
                margins.append(e.home_score - e.away_score)
            else:
                margins.append(e.away_score - e.home_score)
        if not margins:
            return None
        return sum(margins) / len(margins)

    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None:
        return None
    home_margin = avg_margin(home)
    away_margin = avg_margin(away)
    if home_margin is None and away_margin is None:
        return None
    home_margin = home_margin if home_margin is not None else 0.0
    away_margin = away_margin if away_margin is not None else 0.0
    combined = home_margin - away_margin
    if combined >= 0:
        winner = home_name
    else:
        winner = away_name
    confidence = 0.5 + min(abs(combined) / 50.0, 0.45)
    margin = max(1, min(100, int(abs(combined))))
    return LeagueModelPredictionPick(
        event.event_id, "form", winner, max(0.5, min(0.95, confidence)), margin
    )


def predict_home_advantage_numbers(
    event: CompletedEvent,
    prior_results: Sequence[CompletedEvent],
    *,
    home_name: str,
    away_name: str,
) -> Optional[LeagueModelPredictionPick]:
    """Home advantage: measured home-side win share across prior events.

    Mirror of the AFL model's venue-advantage logic with the
    competition-wide home win share standing in for the venue-specific
    rate (state-league venues are too sparsely visited for per-venue
    rates to clear the sample floor).
    """
    decided = [_decided_result(e) for e in prior_results]
    decided = [d for d in decided if d is not None]
    if len(decided) < 3:
        return None
    home_share = decided.count("home") / len(decided)
    if home_share >= 0.5:
        winner = home_name
        confidence = home_share
        margin = int((home_share - 0.5) * 200)
    else:
        winner = away_name
        confidence = 1.0 - home_share
        margin = int((0.5 - home_share) * 200)
    return LeagueModelPredictionPick(
        event.event_id,
        "home_advantage",
        winner,
        max(0.5, min(0.85, confidence)),
        max(1, min(60, margin)),
    )


def predict_matchup_numbers(
    event: CompletedEvent,
    prior_results: Sequence[CompletedEvent],
    *,
    home_name: str,
    away_name: str,
) -> Optional[LeagueModelPredictionPick]:
    """Matchup: H2H win share + average margin; abstains with no meetings."""
    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None:
        return None
    meetings = [
        e
        for e in prior_results
        if e.home_participant_id is not None
        and e.away_participant_id is not None
        and {e.home_participant_id, e.away_participant_id} == {home, away}
        and _decided_result(e) is not None
    ]
    if not meetings:
        return None
    wins_home, wins_away = head_to_head_wins(home, away, meetings)
    # Recency-weighted average margin (most recent heaviest), positive
    # = home-favoured — mirrors models_ml/matchup.py.
    recent = meetings[-_H2H_MAX_WEIGHTED_GAMES:]
    weighted_margin = 0.0
    total_weight = 0.0
    for idx, e in enumerate(recent):
        weight = idx + 1
        margin = (e.home_score or 0) - (e.away_score or 0)
        if e.home_participant_id != home:
            margin = -margin
        weighted_margin += margin * weight
        total_weight += weight
    avg_margin = weighted_margin / max(total_weight, 1.0)
    win_share = wins_home / max(wins_home + wins_away, 1) if (wins_home + wins_away) else 0.5
    if wins_home >= wins_away:
        winner = home_name
        combined = (win_share - 0.5) + (avg_margin / 100.0)
    else:
        winner = away_name
        combined = (0.5 - win_share) + (-avg_margin / 100.0)
    confidence = 0.50 + min(abs(combined) * 0.8, 0.45)
    margin = max(1, min(100, int(abs(avg_margin) * 0.6)))
    return LeagueModelPredictionPick(
        event.event_id,
        "matchup",
        winner,
        max(0.50, min(0.95, confidence)),
        margin,
    )


def predict_value_numbers(
    event: CompletedEvent,
    prior_results: Sequence[CompletedEvent],
    *,
    home_name: str,
    away_name: str,
) -> Optional[LeagueModelPredictionPick]:
    """Value: all-time win-rate differential — AFL ValueModel constants."""
    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None:
        return None
    rates = compute_win_rates(prior_results)
    home_rate = rates.get(home, 0.5)
    away_rate = rates.get(away, 0.5)
    if home_rate > away_rate:
        winner = home_name
        confidence = (home_rate - away_rate) + 0.5
        margin = int((home_rate - away_rate) * _VALUE_MARGIN_SCALE)
    else:
        winner = away_name
        confidence = (away_rate - home_rate) + 0.5
        margin = int((away_rate - home_rate) * _VALUE_MARGIN_SCALE)
    return LeagueModelPredictionPick(
        event.event_id,
        "value",
        winner,
        max(0.5, min(0.9, confidence)),
        max(1, min(80, margin)),
    )


def predict_weather_impact_numbers(
    event: CompletedEvent,
    prior_results: Sequence[CompletedEvent],
    *,
    home_name: str,
    away_name: str,
    weather: Optional[Mapping[str, Any]],
    weather_by_event: Mapping[int, Mapping[str, Any]],
) -> Optional[LeagueModelPredictionPick]:
    """Weather impact: same-venue similar-tier win rates (abstain-prone).

    Mirror of ``models_ml/weather_impact.py``: without a weather row for
    the event, or when NEITHER side has a usable same-venue
    similar-tier sample (>= :data:`_WEATHER_MIN_SAMPLE`), the model
    abstains.  One-sided samples substitute 0.5 for the missing side so
    the informed side still drives the pick.
    """
    home, away = event.home_participant_id, event.away_participant_id
    if home is None or away is None or weather is None:
        return None
    tier = classify_weather_tier(
        precipitation=weather.get("precipitation"),
        wind_gusts=weather.get("wind_gusts"),
        temperature=weather.get("temperature"),
    )

    def historical_win_rate(participant_id: int) -> Optional[float]:
        similar = [
            e
            for e in prior_results
            if weather_by_event.get(e.event_id) is not None
            and e.venue == weather.get("venue")
            and classify_weather_tier(
                precipitation=weather_by_event[e.event_id].get("precipitation"),
                wind_gusts=weather_by_event[e.event_id].get("wind_gusts"),
                temperature=weather_by_event[e.event_id].get("temperature"),
            )
            == tier
            and participant_id in (e.home_participant_id, e.away_participant_id)
            and _decided_result(e) is not None
        ]
        if len(similar) < _WEATHER_MIN_SAMPLE:
            return None
        wins = sum(
            1
            for e in similar
            if (
                "home"
                if (e.home_score or 0) > (e.away_score or 0)
                else "away"
            )
            == ("home" if e.home_participant_id == participant_id else "away")
        )
        return wins / len(similar)

    home_wr = historical_win_rate(home)
    away_wr = historical_win_rate(away)
    if home_wr is None and away_wr is None:
        return None
    home_wr = 0.5 if home_wr is None else home_wr
    away_wr = 0.5 if away_wr is None else away_wr
    diff = home_wr - away_wr
    winner = home_name if diff > 0 else away_name
    confidence = 0.50 + min(abs(diff) * 0.4, 0.45)
    severity = _WEATHER_SEVERITY.get(tier, 0.5)
    margin = max(1, min(100, int(abs(diff) * 100 * severity)))
    return LeagueModelPredictionPick(
        event.event_id,
        "weather_impact",
        winner,
        max(0.50, min(0.95, confidence)),
        margin,
    )


#: The parity model set in canonical (persistence) order — the five
#: result-derived models plus the weather-conditioned one.
PARITY_MODEL_PREDICTIONS: tuple[str, ...] = (
    "elo",
    "form",
    "home_advantage",
    "value",
    "weather_impact",
    "matchup",
)


def compute_event_model_predictions(
    event: CompletedEvent,
    prior_history: Sequence[CompletedEvent],
    *,
    side_names: Mapping[int, str],
    weather_by_event: Mapping[int, Mapping[str, Any]],
) -> list[LeagueModelPredictionPick]:
    """All six parity models' numeric predictions for one event.

    ``prior_history`` must contain only events BEFORE this one.  Events
    with unknown side names, undecided completed events (draws) and
    scheduled fixtures without names are skipped; individual models
    abstain per their own contracts (no row persisted).
    """
    home_side = event.home_side_id
    away_side = event.away_side_id
    if home_side is None or away_side is None:
        return []
    home_name = side_names.get(home_side)
    away_name = side_names.get(away_side)
    if not home_name or not away_name:
        return []
    if is_drawn(event):
        return []  # a drawn result has no numeric winner to store
    weather = weather_by_event.get(event.event_id)
    predictions: list[LeagueModelPredictionPick] = []
    for predict in (
        predict_elo_numbers,
        predict_form_numbers,
        predict_home_advantage_numbers,
        predict_value_numbers,
    ):
        pick = predict(event, prior_history, home_name=home_name, away_name=away_name)
        if pick is not None:
            predictions.append(pick)
    matchup = predict_matchup_numbers(
        event, prior_history, home_name=home_name, away_name=away_name
    )
    if matchup is not None:
        predictions.append(matchup)
    weather_pick = predict_weather_impact_numbers(
        event,
        prior_history,
        home_name=home_name,
        away_name=away_name,
        weather=weather,
        weather_by_event=weather_by_event,
    )
    if weather_pick is not None:
        predictions.append(weather_pick)
    return predictions


def compute_all_model_predictions(
    events: Sequence[CompletedEvent],
    *,
    side_names: Mapping[int, str],
    weather_by_event: Mapping[int, Mapping[str, Any]],
) -> list[LeagueModelPredictionPick]:
    """Numeric predictions for every event in ONE chronological walk.

    Scheduled fixtures (``completed=False``) are predicted too — forward
    model cards, the AFL behaviour — each reading only the completed
    events that start no later than the fixture.
    """
    history = sort_chronologically(events)
    predictions: list[LeagueModelPredictionPick] = []
    for index, event in enumerate(history):
        if event.completed:
            # C-1 (code review): the positional slice mixes scheduled
            # snapshots into the evidence — a NULL-starts_at (sorts
            # first) or postponed fixture would leak its 0-0 placeholder
            # into every completed event's Elo/form evidence.  Mirror
            # the tips walk (compute_all_picks_and_derived): completed
            # events read COMPLETED evidence only.
            prior = [e for e in history[:index] if e.completed]
        else:
            prior = [
                e
                for e in history
                if e.completed
                and (e.starts_at or _MIN_TIME) <= (event.starts_at or _MIN_TIME)
                and e.event_id != event.event_id
            ]
        predictions.extend(
            compute_event_model_predictions(
                event,
                prior,
                side_names=side_names,
                weather_by_event=weather_by_event,
            )
        )
    return predictions


# ---------------------------------------------------------------------------
# DB layer
# ---------------------------------------------------------------------------


class LeagueModelPredictionService:
    """Generate/refresh ``league_model_predictions`` for one competition."""

    async def generate_for_competition(
        self, db: AsyncSession, *, competition_id: int
    ) -> dict[str, Any]:
        """Compute and persist the competition's model predictions."""
        seasons = await LeagueHeuristicsService._fetch_seasons(
            db, competition_id
        )
        selected = select_tip_seasons(seasons)
        if not selected:
            return {
                "competition_id": competition_id,
                "predictions_inserted": 0,
                "predictions_updated": 0,
            }
        season_ids = [s.id for s in selected]
        events = await LeagueHeuristicsService._fetch_completed_events(
            db, season_ids
        )
        scheduled = await LeagueHeuristicsService._fetch_scheduled_events(
            db, season_ids
        )
        all_events = [*events, *scheduled]
        side_names = await self._fetch_side_names(
            db, [e.event_id for e in all_events]
        )
        weather_by_event = await self._fetch_weather(
            db, [e.event_id for e in all_events]
        )
        picks = compute_all_model_predictions(
            all_events,
            side_names=side_names,
            weather_by_event=weather_by_event,
        )
        inserted, updated = await self._upsert_predictions(db, picks)
        await db.flush()
        summary = {
            "competition_id": competition_id,
            "events_considered": len(all_events),
            "predictions_inserted": inserted,
            "predictions_updated": updated,
        }
        logger.info(
            "league model predictions: competition %s: %s",
            competition_id,
            summary,
        )
        return summary

    @staticmethod
    async def _fetch_side_names(
        db: AsyncSession, event_ids: Sequence[int]
    ) -> dict[int, str]:
        """``event_participants.id`` → participant display name."""
        ids = [int(eid) for eid in event_ids]
        if not ids:
            return {}
        rows = (
            await db.execute(
                select(EventParticipant.id, Participant.name).join(
                    Participant,
                    EventParticipant.participant_id == Participant.id,
                ).where(EventParticipant.event_id.in_(ids))
            )
        ).all()
        return {int(row[0]): str(row[1]) for row in rows}

    @staticmethod
    async def _fetch_weather(
        db: AsyncSession, event_ids: Sequence[int]
    ) -> dict[int, dict[str, Any]]:
        """``event_id`` → the weather snapshot's prediction-relevant fields."""
        ids = [int(eid) for eid in event_ids]
        if not ids:
            return {}
        rows = (
            await db.execute(
                select(
                    LeagueEventWeather.event_id,
                    LeagueEventWeather.venue,
                    LeagueEventWeather.temperature,
                    LeagueEventWeather.precipitation,
                    LeagueEventWeather.wind_gusts,
                ).where(LeagueEventWeather.event_id.in_(ids))
            )
        ).all()
        return {
            int(row[0]): {
                "venue": row[1],
                "temperature": row[2],
                "precipitation": row[3],
                "wind_gusts": row[4],
            }
            for row in rows
        }

    async def _upsert_predictions(
        self,
        db: AsyncSession,
        picks: Sequence[LeagueModelPredictionPick],
    ) -> tuple[int, int]:
        """Diff-based idempotent upsert; returns (inserted, updated)."""
        event_ids = [p.event_id for p in picks]
        existing: dict[tuple[int, str], tuple[Optional[str], Optional[float], Optional[int]]] = {}
        ids = list({int(eid) for eid in event_ids})
        if ids:
            rows = (
                await db.execute(
                    select(
                        LeagueModelPrediction.event_id,
                        LeagueModelPrediction.model_name,
                        LeagueModelPrediction.winner,
                        LeagueModelPrediction.confidence,
                        LeagueModelPrediction.margin,
                    ).where(LeagueModelPrediction.event_id.in_(ids))
                )
            ).all()
            existing = {
                (int(r[0]), str(r[1])): (r[2], r[3], r[4]) for r in rows
            }
        inserted = 0
        updated = 0
        for pick in picks:
            key = (pick.event_id, pick.model_name)
            current = existing.get(key)
            if current is None:
                db.add(
                    LeagueModelPrediction(
                        event_id=pick.event_id,
                        model_name=pick.model_name,
                        winner=pick.winner,
                        confidence=pick.confidence,
                        margin=pick.margin,
                    )
                )
                inserted += 1
            elif current != (pick.winner, pick.confidence, pick.margin):
                await self._update_prediction(db, pick)
                updated += 1
        return inserted, updated

    @staticmethod
    async def _update_prediction(
        db: AsyncSession, pick: LeagueModelPredictionPick
    ) -> bool:
        row = (
            await db.execute(
                select(LeagueModelPrediction).where(
                    LeagueModelPrediction.event_id == pick.event_id,
                    LeagueModelPrediction.model_name == pick.model_name,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        row.winner = pick.winner
        row.confidence = pick.confidence
        row.margin = pick.margin
        return True


async def generate_league_model_predictions(
    db: AsyncSession, *, competition_id: int
) -> dict[str, Any]:
    """Module-level entry point — the generation-pipeline hook target."""
    return await LeagueModelPredictionService().generate_for_competition(
        db, competition_id=competition_id
    )
