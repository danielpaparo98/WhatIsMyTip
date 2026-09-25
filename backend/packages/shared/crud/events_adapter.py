"""Adapter: event rows → legacy ``GameResponse`` field set (ADR 0001).

Part of the ``/api/games`` deprecation-window increment: lets the
legacy games route serve events-table competitions by mapping an
:class:`~packages.shared.schemas.events.EventResponse`-shaped dict (as
returned by :class:`~packages.shared.crud.events.EventsCRUD`) onto the
legacy ``GameResponse`` field set.

The events tables store sides as ``event_participants`` rows
(home/away/``n/a``), have no ``squiggle_id`` column and no ``source``
column, and carry the season as a competition-relative label string —
the adapter folds all of that into the legacy shape:

* participants are flattened to ``home_team``/``away_team`` (plus
  scores); ``n/a``-side rows are ignored, and an event with no usable
  home/away side at all maps to ``None`` (the router skips it — a
  race/field event is not a legacy fixture);
* ``squiggle_id`` is always ``None`` (deprecated provider id);
* ``source`` is ``"events"`` — the events tables have no provider
  column, so the row's origin is the events pipeline itself;
* ``season`` is ``int(label)`` (0 when the label is not a plain year);
* ``round_id`` falls back to 0 for round-less events, mirroring the
  legacy create path's ``round_id or 0``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

#: ``source`` value for rows served from the 0010 events tables.
EVENTS_SOURCE = "events"

__all__ = ["EVENTS_SOURCE", "event_to_game_response"]


def _season_label_to_int(label: Any) -> int:
    """``'2026'`` → ``2026``; non-year labels (``'2026-27'``) → 0."""
    try:
        return int(label)
    except (TypeError, ValueError):
        return 0


def _side(
    participants: Optional[List[Dict[str, Any]]], side: str
) -> Optional[Dict[str, Any]]:
    """The first participant row for ``side`` (``'home'``/``'away'``,
    never matched against ``'n/a'`` rows), or ``None`` when absent."""
    for participant in participants or []:
        if participant.get("side") == side:
            return participant
    return None


def event_to_game_response(evt: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map an :class:`EventResponse`-shaped dict to the legacy
    ``GameResponse`` field set (as a plain dict).

    Returns ``None`` when the event has neither a home nor an away side
    (``n/a``-side race/field events) — such events cannot be expressed
    as a legacy fixture and are skipped by the router.
    """
    participants = evt.get("participants") or []
    home = _side(participants, "home")
    away = _side(participants, "away")
    if home is None and away is None:
        return None

    return {
        "id": evt["id"],
        "slug": evt["slug"],
        # DEPRECATED (P3-2 / ADR 0001): provider-specific id — events
        # rows have no Squiggle id.
        "squiggle_id": None,
        "source": EVENTS_SOURCE,
        "round_id": evt.get("round_id") or 0,
        "season": _season_label_to_int(evt.get("season")),
        "home_team": home["participant_name"] if home else None,
        "away_team": away["participant_name"] if away else None,
        "home_score": home.get("score") if home else None,
        "away_score": away.get("score") if away else None,
        "venue": evt.get("venue"),
        "date": evt.get("starts_at"),
        "completed": bool(evt.get("completed")),
    }
