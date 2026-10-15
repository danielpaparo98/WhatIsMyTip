"""League grand-final pre-match reports (PARITY-1).

The league counterpart of :mod:`.match_report`: during grand-final
week every league's decider gets the SAME structured
:class:`GrandFinalReport` payload the AFL page renders, stored on
``league_match_reports`` and served by ``GET /api/events/{slug}/report``
— the exact endpoint contract the AFL site uses.

Detection (:func:`is_league_grand_final`): the event sits in its
season's maximum ``round_id`` of a fixture carrying at least
:data:`LEAGUE_MIN_ROUNDS_FOR_GRAND_FINAL_DETECTION` distinct rounds —
the same complete-fixture heuristic as the AFL's
``models_ml.neutral.is_grand_final``, scaled to league reality: league
fixtures sync as FULL season slates (FixtureDownload / Sportix / PlayHQ
ship every round at once), so the floor is set by the shortest real
competition — Origin's three-game series — not the AFL's 27 rounds.

Assembly: the DETERMINISTIC sections come from the same league
evidence every other parity surface reads — standings, form, model
predictions, derived tips, weather — and the EDITORIAL sections
(headline, executive summary, season narratives, keys to the game,
x-factor, talking points) are written by the sport-aware OpenRouter
client in ONE strict-JSON call, degrading to deterministic text (or a
null x-factor — a section the frontend hides) when the LLM layer is
unavailable.  ``key_players`` / ``injury_watch`` are EMPTY lists: the
league pipeline has no player-level data source, the report schema
requires the fields, and the frontend component already renders empty
player/injury sections as absent.

Generation must NEVER break the sweep — every failure is logged and
``None`` returned (the match_report contract, mirrored).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..logger import get_logger
from ..models import (
    Competition,
    Event,
    EventParticipant,
    LeagueEventWeather,
    LeagueMatchReport,
    LeagueModelPrediction,
    LeagueTip,
    Participant,
    Season,
)
from ..schemas.match_report import GrandFinalReport
from .explanation import (
    RugbyLeagueOpenRouterClient,
    build_league_match_context,
)
from .league_heuristics import (
    CompletedEvent,
    LeagueHeuristicsService,
    compute_standings,
    compute_win_rates,
    is_drawn,
    sort_chronologically,
)

logger = get_logger(__name__)

_REPORT_TYPE = "grand_final_pre_match"

#: The shortest real league season is Origin's three-game series; league
#: fixtures arrive as full-season slates, so three distinct rounds is a
#: safe complete-fixture floor (the AFL's 25 guards a season loaded
#: round-by-round — a failure mode league feeds do not have).
LEAGUE_MIN_ROUNDS_FOR_GRAND_FINAL_DETECTION = 3

#: Rounds of recent form quoted in the finals path.
_FINALS_PATH_ROUNDS = 4

_MIN_TIME = datetime.min


async def is_league_grand_final(db: AsyncSession, *, event_id: int) -> bool:
    """True when the event is its season's last round of a complete fixture.

    The league twin of ``models_ml.neutral.is_grand_final`` — the single
    implementation the sweep and the API gate share.
    """
    row = (
        await db.execute(
            select(Event.round_id, Event.season_id).where(Event.id == event_id)
        )
    ).first()
    if row is None:
        return False
    round_id, season_id = row
    if round_id is None:
        return False
    max_round, distinct_rounds = (
        await db.execute(
            select(
                func.max(Event.round_id),
                func.count(func.distinct(Event.round_id)),
            ).where(
                Event.season_id == season_id,
                # C-4 (code review): a cancelled/void phantom round must
                # not inflate the distinct-round floor or set a wrong
                # max — only playable rounds count.
                Event.status.in_(("scheduled", "completed")),
            )
        )
    ).one()
    return bool(
        max_round is not None
        and int(round_id) == int(max_round)
        and int(distinct_rounds or 0)
        >= LEAGUE_MIN_ROUNDS_FOR_GRAND_FINAL_DETECTION
    )


# ---------------------------------------------------------------------------
# Deterministic section builders — every input is league evidence.
# ---------------------------------------------------------------------------


def _result_phrase(
    side_name: str,
    side_score: int,
    opp_name: str,
    opp_score: int,
) -> str:
    """One result line for the finals path."""
    if side_score > opp_score:
        return f"defeated {opp_name} by {side_score - opp_score} points"
    if side_score < opp_score:
        return f"lost to {opp_name} by {opp_score - side_score} points"
    return f"drew with {opp_name}"


def _winner_pid(event: CompletedEvent) -> Optional[int]:
    if event.home_score is None or event.away_score is None:
        return None
    if event.home_score == event.away_score:
        return None
    return (
        event.home_participant_id
        if event.home_score > event.away_score
        else event.away_participant_id
    )


def _side_finals_path(
    season_events: list[CompletedEvent],
    participant_id: int,
    side_name: str,
    names_by_pid: dict[int, str],
    max_round: Optional[int],
) -> list[str]:
    """Round-labelled results across the season's last completed rounds.

    Honest round-based labels — league feeds do not carry AFL-style
    finals labels, so the path reads ``Round N: <result>`` for each of
    the side's completed events in the season's last four rounds.
    """
    if max_round is None:
        return []
    involved = [
        e
        for e in season_events
        if e.completed
        and participant_id in (e.home_participant_id, e.away_participant_id)
    ]
    window = set(
        sorted(
            (
                int(e.round_id)
                for e in involved
                if e.round_id is not None and int(e.round_id) <= max_round
            ),
            reverse=True,
        )[:_FINALS_PATH_ROUNDS]
    )
    path: list[str] = []
    for event in sort_chronologically(involved):
        if event.round_id is None or int(event.round_id) not in window:
            continue
        if event.home_score is None or event.away_score is None:
            continue
        is_home = event.home_participant_id == participant_id
        side_score = event.home_score if is_home else event.away_score
        opp_score = event.away_score if is_home else event.home_score
        opp_pid = event.away_participant_id if is_home else event.home_participant_id
        opp_name = (
            names_by_pid.get(int(opp_pid), "the opponent")
            if opp_pid is not None
            else "the opponent"
        )
        path.append(
            f"Round {int(event.round_id)}: "
            f"{_result_phrase(side_name, side_score, opp_name, opp_score)}"
        )
    return path


def _season_narrative(
    side_name: str,
    standings: dict[int, Any],
    participant_id: int,
    wins: int,
    draws: int,
    losses: int,
    ladder_position: Optional[int],
) -> str:
    """Deterministic season narrative for one finalist."""
    row = standings.get(participant_id)
    pct = f"{row.percentage:.0f}%" if row is not None and row.points_against else "—"
    ladder = (
        f" They finished the season ranked #{ladder_position}."
        if ladder_position is not None
        else ""
    )
    return (
        f"{side_name} closed the season with {wins}-{draws}-{losses} "
        f"(W-D-L) at {pct} of points for-and-against.{ladder}"
    )


def _context_lines(match_context: dict[str, Any]) -> list[str]:
    """Render the cross-season context into compact evidence lines."""
    lines: list[str] = []
    elo = match_context.get("elo")
    if elo:
        lines.append(
            f"Elo ratings: {elo['home']} vs {elo['away']} "
            f"({abs(elo['diff'])}-point edge to the "
            f"{'home' if elo['diff'] >= 0 else 'away'} side)"
        )
    form = match_context.get("form")
    if form:
        h, a = form.get("home", {}), form.get("away", {})
        lines.append(
            f"Recent form: home {h.get('wins', 0)}-{h.get('draws', 0)}-"
            f"{h.get('losses', 0)} vs away {a.get('wins', 0)}-"
            f"{a.get('draws', 0)}-{a.get('losses', 0)} (W-D-L)"
        )
    h2h = match_context.get("head_to_head")
    if h2h:
        lines.append(
            f"Head-to-head: {h2h['home_wins']}-{h2h['away_wins']} "
            f"with {h2h['draws']} drawn from {h2h['games']} meetings"
        )
    return lines


# ---------------------------------------------------------------------------
# Sport-aware report client — ONE strict-JSON editorial call.
# ---------------------------------------------------------------------------

_REPORT_SYSTEM_PROMPT = (
    "You are an expert rugby league analyst writing the PRE-MATCH grand "
    "final report for a tipping website. The report is a PREDICTION of "
    "what is anticipated to happen — never a result recap. Ground every "
    "judgement in the supplied data and stay honest about uncertainty; a "
    "drawn result is possible in rugby league.\n\n"
    "GF-CONTENT RULES (strict):\n"
    "- NEVER invent statistics, injuries or facts that are not in the "
    "supplied context.\n"
    "- The grand final is played at a NEUTRAL venue — never imply either "
    "side has home-ground advantage.\n"
    "- HEADLINE: at most 8 words, an editorial hook only.\n"
    "- Return ONLY valid JSON matching the requested keys — no markdown "
    "fences, no commentary."
)


def _report_user_prompt(payload: dict[str, Any]) -> str:
    """The compact evidence pack for the editorial call (defensive reads —
    a partially-populated payload degrades to empty lines, never a KeyError)."""
    lines = [
        f"Competition: {payload.get('competition_name', 'the competition')}",
        (
            f"Grand Final: {payload.get('home_name', 'the home side')} vs "
            f"{payload.get('away_name', 'the away side')} "
            f"at {payload.get('venue') or 'a venue to be confirmed'}"
            f" — Round {payload.get('round_id')}, season "
            f"{payload.get('season_label')}"
        ),
        "",
        "Season evidence:",
    ]
    lines.extend(payload.get("evidence_lines") or [])
    lines.append("")
    lines.append(
        "Return ONLY this JSON with every key filled "
        "(x_factor may be null when the data does not support one):\n"
        "{\n"
        '  "headline": string,\n'
        '  "executive_summary": string (2-3 sentences),\n'
        f'  "home_narrative": string (the {payload.get("home_name", "home")} '
        "season story),\n"
        f'  "away_narrative": string (the {payload.get("away_name", "away")} '
        "season story),\n"
        '  "keys_to_the_game": array of 3-4 short strings,\n'
        '  "x_factor": string or null,\n'
        '  "talking_points": array of 4-5 short strings\n'
        "}"
    )
    return "\n".join(lines)


class LeagueReportOpenRouterClient(RugbyLeagueOpenRouterClient):
    """OpenRouter client producing the report's editorial JSON block."""

    async def generate_report_sections(
        self, payload: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """One strict-JSON editorial call; ``None`` on any failure.

        No key, a provider error, malformed JSON or a missing required
        key all return ``None`` — the caller's deterministic fallbacks
        take over (the match_report never-fabricate contract, applied
        per-field instead of per-report).
        """
        from ..config import settings

        if not settings.openrouter_api_key or self.client is None:
            return None
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _REPORT_SYSTEM_PROMPT},
                    {"role": "user", "content": _report_user_prompt(payload)},
                ],
                max_tokens=1500,
                temperature=0.4,
            )
            content = self._extract_content(response)
            if not content:
                return None
            # Tolerate a fenced block around the JSON object.
            fenced = re.search(r"\{.*\}", content, re.DOTALL)
            parsed = json.loads(fenced.group(0) if fenced else content)
            if not isinstance(parsed, dict):
                return None
            required = (
                "headline",
                "executive_summary",
                "home_narrative",
                "away_narrative",
                "keys_to_the_game",
                "talking_points",
            )
            if any(not parsed.get(key) for key in required):
                return None
            return parsed
        except Exception as e:  # noqa: BLE001 — editorial layer is best-effort
            logger.warning(
                "league GF report: editorial call failed: %s", e, exc_info=True
            )
            return None


def _fallback_editorial(
    home_name: str,
    away_name: str,
    evidence_lines: list[str],
    prediction: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic editorial block — honest, evidence-quoted text."""
    top = evidence_lines[:3]
    keys = [
        line
        for line in (
            *top,
            "Whoever handles the big-stage nerves best lifts the trophy.",
        )
        if line
    ]
    return {
        "headline": f"{home_name} and {away_name} meet for the trophy",
        "executive_summary": (
            f"The season decider pits {home_name} against {away_name}. "
            + (" ".join(top) + " " if top else "")
            + f"The verdict leans to {prediction['winner']} by "
            f"{prediction['margin']} points."
        ),
        "home_narrative": (
            f"{home_name} arrive at the decider as one of the season's "
            "two surviving sides."
        ),
        "away_narrative": (
            f"{away_name} arrive at the decider as one of the season's "
            "two surviving sides."
        ),
        "keys_to_the_game": keys
        or ["The season decider — both sides have earned their place."],
        "x_factor": None,
        "talking_points": [
            f"{prediction['winner']} carry the models' verdict into the decider.",
            "A drawn result at full-time remains a live outcome in rugby league.",
            "Grand-final experience — or the lack of it — shapes the closing stages.",
        ],
    }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class LeagueMatchReportService:
    """Generate and store the league grand-final pre-match report."""

    @staticmethod
    async def is_grand_final(db: AsyncSession, *, event_id: int) -> bool:
        """The shared detection gate (sweep + API, single implementation)."""
        return await is_league_grand_final(db, event_id=event_id)

    @staticmethod
    async def get_report(
        db: AsyncSession, *, event_id: int
    ) -> Optional[LeagueMatchReport]:
        """The stored report row, or ``None``."""
        return (
            await db.execute(
                select(LeagueMatchReport).where(
                    LeagueMatchReport.event_id == event_id
                )
            )
        ).scalar_one_or_none()

    async def generate_and_store_report(
        self, db: AsyncSession, *, event_id: int
    ) -> Optional[dict[str, Any]]:
        """Generate the GF report and store it; returns the payload dict.

        ``None`` when the event is not an upcoming grand final with both
        sides, or a report already exists, or generation failed.  Never
        raises (the sweep's best-effort contract).
        """
        try:
            return await self._generate(db, event_id=event_id)
        except Exception as e:  # noqa: BLE001 — never break the sweep
            logger.error(
                "league GF report: generation failed for event %s: %s",
                event_id,
                e,
                exc_info=True,
            )
            # F-1 (code review): a failed statement/commit leaves the
            # session ABORTED — roll back so the sweep's remaining items
            # survive (the FCL-11 / _refresh_league_tips discipline).
            try:
                await db.rollback()
            except Exception:  # noqa: BLE001
                pass
            return None

    # -- internals ---------------------------------------------------------

    async def _generate(
        self, db: AsyncSession, *, event_id: int
    ) -> Optional[dict[str, Any]]:
        # Gate 1: grand final only.
        if not await is_league_grand_final(db, event_id=event_id):
            return None

        row = (
            await db.execute(
                select(
                    Event,
                    Season.label,
                    Season.id,
                    Season.competition_id,
                )
                .join(Season, Event.season_id == Season.id)
                .where(Event.id == event_id)
            )
        ).first()
        if row is None:
            return None
        event, season_label, season_id, competition_id = row

        # Gate 2: PRE-match only.
        if event.completed:
            return None

        # Gate 3: skip when a report is already stored.
        if await self.get_report(db, event_id=event_id) is not None:
            return None

        sides = await self._fetch_sides(db, event_id)
        home, away = sides.get("home"), sides.get("away")
        if not home or not away:
            return None
        home_name, away_name = home["name"], away["name"]

        # The season's evidence: every completed event of THIS season.
        season_events = await LeagueHeuristicsService._fetch_completed_events(
            db, [int(season_id)]
        )
        season_events = [e for e in season_events if e.completed]
        max_round = max(
            (int(e.round_id) for e in season_events if e.round_id is not None),
            default=None,
        )
        names_by_pid = await self._participant_names(db, season_events)

        # Deterministic narratives + finals paths + season evidence.
        standings = compute_standings(season_events)
        ladder_order = sorted(
            standings.values(),
            key=lambda r: (r.wins, r.percentage),
            reverse=True,
        )
        ladder_position = {
            r.participant_id: i + 1 for i, r in enumerate(ladder_order)
        }
        narratives: dict[str, str] = {}
        finals_paths: dict[str, list[str]] = {}
        evidence_lines: list[str] = []
        season_form: dict[str, str] = {}
        for label, side in (("home", home), ("away", away)):
            pid = int(side["participant_id"])
            involved = [
                e
                for e in season_events
                if pid in (e.home_participant_id, e.away_participant_id)
            ]
            wins = sum(1 for e in involved if _winner_pid(e) == pid)
            draws = sum(1 for e in involved if is_drawn(e))
            losses = len(involved) - wins - draws
            narratives[label] = _season_narrative(
                side["name"],
                standings,
                pid,
                wins,
                draws,
                losses,
                ladder_position.get(pid),
            )
            finals_paths[label] = _side_finals_path(
                season_events, pid, side["name"], names_by_pid, max_round
            )
            season_form[label] = f"{wins}-{draws}-{losses}"
            rate = compute_win_rates(season_events).get(pid)
            if rate is not None:
                evidence_lines.append(
                    f"{side['name']}: season win rate {rate:.0%} "
                    f"({wins}-{draws}-{losses}), ranked "
                    f"#{ladder_position.get(pid, '?')}"
                )

        # Cross-season context (Elo / H2H / form) from the shared builder —
        # strictly PRIOR evidence, the same no-look-ahead slice everywhere.
        competition_season_ids = [
            int(sid)
            for sid in (
                await db.execute(
                    select(Season.id).where(
                        Season.competition_id == int(competition_id)
                    )
                )
            ).scalars()
        ]
        prior_all = await LeagueHeuristicsService._fetch_completed_events(
            db, competition_season_ids
        )
        cutoff = (event.starts_at or _MIN_TIME, int(event_id))
        strictly_prior = [
            e
            for e in sort_chronologically(prior_all)
            if (e.starts_at or _MIN_TIME, e.event_id) < cutoff
        ]
        match_context = build_league_match_context(
            strictly_prior,
            home_participant_id=int(home["participant_id"]),
            away_participant_id=int(away["participant_id"]),
        )
        evidence_lines.extend(_context_lines(match_context))

        # Model consensus + the verdict.
        model_predictions = await self._fetch_model_predictions(db, event_id)
        # C-3 (code review): a partial write can leave a NULL-winner row
        # — abstentions never count toward the tally.
        decided_predictions = [
            p for p in model_predictions if p[1] is not None
        ]
        picking_home = sum(
            1 for _, w, _, _ in decided_predictions if w == home_name
        )
        picking_away = sum(
            1 for _, w, _, _ in decided_predictions if w == away_name
        )
        total = len(decided_predictions)
        if total:
            leader = home_name if picking_home >= picking_away else away_name
            consensus_summary = (
                f"{max(picking_home, picking_away)} of {total} models pick "
                f"{leader} for the decider."
            )
        else:
            consensus_summary = (
                "The models have not reached a consensus for this decider."
            )
        consensus = {
            "summary": consensus_summary,
            "models_picking_home": picking_home,
            "models_picking_away": picking_away,
            "season_accuracy_note": await self._season_accuracy_note(
                db, int(season_id)
            ),
        }
        prediction = await self._build_prediction(
            db, event_id, sides, home_name, away_name, decided_predictions
        )

        # Weather (optional section — null hides it).
        weather_impact = await self._weather_impact(
            db, event_id, home_name, away_name
        )

        # Editorial call (best-effort) with deterministic fallbacks.
        # F-3 (code review): the per-report client is closed in a finally
        # — one report per league per season, but the pool still should
        # not outlive the call.
        competition_name = await self._competition_name(
            db, int(competition_id)
        )
        report_client = LeagueReportOpenRouterClient(competition_name)
        try:
            editorial = await report_client.generate_report_sections(
                {
                    "competition_name": competition_name,
                    "home_name": home_name,
                    "away_name": away_name,
                    "venue": event.venue,
                    "round_id": event.round_id,
                    "season_label": season_label,
                    "evidence_lines": evidence_lines,
                }
            )
        finally:
            await report_client.close()
        if editorial is None:
            editorial = _fallback_editorial(
                home_name, away_name, evidence_lines, prediction
            )

        report = GrandFinalReport(
            headline=str(editorial["headline"])[:200],
            executive_summary=str(editorial["executive_summary"]),
            season_story={
                "home": {
                    "team": home_name,
                    "narrative": str(editorial["home_narrative"]),
                    "finals_path": finals_paths["home"],
                },
                "away": {
                    "team": away_name,
                    "narrative": str(editorial["away_narrative"]),
                    "finals_path": finals_paths["away"],
                },
            },
            keys_to_the_game=[str(k) for k in editorial["keys_to_the_game"]][:6],
            key_players={"home": [], "away": []},
            injury_watch={"home": [], "away": []},
            model_consensus=consensus,
            weather_impact=weather_impact,
            x_factor=(
                str(editorial["x_factor"]) if editorial.get("x_factor") else None
            ),
            prediction=prediction,
            talking_points=[str(t) for t in editorial["talking_points"]][:6],
        )

        db.add(
            LeagueMatchReport(
                event_id=event_id,
                report_type=_REPORT_TYPE,
                report=report.model_dump(mode="json"),
            )
        )
        await db.commit()
        logger.info(
            "league GF report: stored report for event %s (%s vs %s)",
            event_id,
            home_name,
            away_name,
        )
        return report.model_dump(mode="json")

    # -- helpers -----------------------------------------------------------

    @staticmethod
    async def _fetch_sides(
        db: AsyncSession, event_id: int
    ) -> dict[str, dict[str, Any]]:
        rows = (
            await db.execute(
                select(EventParticipant, Participant.name)
                .join(
                    Participant,
                    EventParticipant.participant_id == Participant.id,
                )
                .where(
                    EventParticipant.event_id == event_id,
                    EventParticipant.side.in_(("home", "away")),
                )
            )
        ).all()
        sides: dict[str, dict[str, Any]] = {}
        for side_row, name in rows:
            sides[str(side_row.side)] = {
                "side_id": int(side_row.id),
                "participant_id": int(side_row.participant_id),
                "name": str(name),
            }
        return sides

    @staticmethod
    async def _participant_names(
        db: AsyncSession, season_events: list[CompletedEvent]
    ) -> dict[int, str]:
        """participant_id → display name for every side in the season."""
        pids = {
            pid
            for e in season_events
            for pid in (e.home_participant_id, e.away_participant_id)
            if pid is not None
        }
        if not pids:
            return {}
        rows = (
            await db.execute(
                select(Participant.id, Participant.name).where(
                    Participant.id.in_(pids)
                )
            )
        ).all()
        return {int(r[0]): str(r[1]) for r in rows}

    @staticmethod
    async def _fetch_model_predictions(
        db: AsyncSession, event_id: int
    ) -> list[tuple[str, Optional[str], Optional[float], Optional[int]]]:
        rows = (
            await db.execute(
                select(
                    LeagueModelPrediction.model_name,
                    LeagueModelPrediction.winner,
                    LeagueModelPrediction.confidence,
                    LeagueModelPrediction.margin,
                ).where(LeagueModelPrediction.event_id == event_id)
            )
        ).all()
        return [(str(r[0]), r[1], r[2], r[3]) for r in rows]

    @staticmethod
    async def _season_accuracy_note(
        db: AsyncSession, season_id: int
    ) -> str:
        from .league_backtest import LeagueBacktestService

        graded = await LeagueBacktestService._fetch_graded_model_predictions(
            db, season_id
        )
        by_model: dict[str, list[int]] = {}
        for g in graded:
            tally = by_model.setdefault(g.model_name, [0, 0])
            tally[0] += 1
            if g.is_correct:
                tally[1] += 1
        best = max(
            (
                (correct / made, made)
                for made, correct in by_model.values()
                if made
            ),
            default=None,
        )
        if best is None:
            return (
                "Season model accuracy is not yet graded for this "
                "competition."
            )
        return (
            f"The strongest model this season graded at {best[0]:.0%} "
            f"across {best[1]} graded predictions."
        )

    @staticmethod
    async def _build_prediction(
        db: AsyncSession,
        event_id: int,
        sides: dict[str, dict[str, Any]],
        home_name: str,
        away_name: str,
        model_predictions: list[
            tuple[str, Optional[str], Optional[float], Optional[int]]
        ],
    ) -> dict[str, Any]:
        """The verdict: the learned combiner's tip, else the model majority."""
        rows = (
            await db.execute(
                select(LeagueTip).where(
                    LeagueTip.event_id == event_id,
                    LeagueTip.heuristic.in_(
                        ("boosted_tip", "weighted_tip", "best_bet")
                    ),
                )
            )
        ).scalars()
        tips = {str(t.heuristic): t for t in rows}
        chosen = (
            tips.get("boosted_tip")
            or tips.get("weighted_tip")
            or tips.get("best_bet")
        )
        if chosen is not None and chosen.selected_participant_id is not None:
            picked_name = next(
                (
                    side["name"]
                    for side in sides.values()
                    if side["side_id"] == int(chosen.selected_participant_id)
                ),
                None,
            )
            if picked_name is not None:
                return {
                    "winner": picked_name,
                    "margin": int(chosen.margin or 1),
                    "confidence": float(chosen.confidence or 0.5),
                }
        # Majority of the models (ties → the nominally-home side, the
        # same canonical discipline the pick functions use).
        picking_home = sum(
            1 for _, w, _, _ in model_predictions if w == home_name
        )
        picking_away = sum(
            1 for _, w, _, _ in model_predictions if w == away_name
        )
        winner = home_name if picking_home >= picking_away else away_name
        margins = [int(m) for _, _, _, m in model_predictions if m]
        return {
            "winner": winner,
            "margin": max(1, round(sum(margins) / len(margins)))
            if margins
            else 1,
            "confidence": 0.5,
        }

    @staticmethod
    async def _weather_impact(
        db: AsyncSession, event_id: int, home_name: str, away_name: str
    ) -> Optional[str]:
        weather = (
            await db.execute(
                select(LeagueEventWeather).where(
                    LeagueEventWeather.event_id == event_id
                )
            )
        ).scalar_one_or_none()
        if weather is None:
            return None
        bits: list[str] = []
        if weather.temperature is not None:
            bits.append(f"{weather.temperature:.0f}°C")
        if weather.precipitation is not None:
            bits.append(f"{weather.precipitation:.1f}mm of rain")
        if weather.wind_gusts is not None:
            bits.append(f"gusts to {weather.wind_gusts:.0f}km/h")
        if not bits:
            return None
        return (
            f"Conditions at {weather.venue or 'the venue'}: "
            f"{', '.join(bits)} — expect the weather to influence the "
            f"flow of the {home_name} vs {away_name} decider."
        )

    @staticmethod
    async def _competition_name(db: AsyncSession, competition_id: int) -> str:
        name = (
            await db.execute(
                select(Competition.name).where(Competition.id == competition_id)
            )
        ).scalar_one_or_none()
        return str(name) if name else "the competition"
