"""Shared league-registry config (Phase 5 / Phase 5.2).

``LeagueConfig`` is the ONE frozen config shape every league registry
speaks: the state-league rollout (:mod:`.state_leagues`, Phase 5) and
the national expansion (:mod:`.national_leagues`, Phase 5.2) both
register entries with it, so the facade resolvers and the sync service
stay registry-agnostic.  Defining it once here keeps the registries
from drifting — the national registry joined needing ``tier``/``format``
(the ``competitions`` columns), which default to the state rollout's
values so every pre-existing entry is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from . import FeedProvider


@dataclass(frozen=True)
class LeagueConfig:
    name: str
    timezone: str
    provider_factory: Optional[Callable[[], FeedProvider]]
    #: "live" = provider ready; "live-same-tenant" = another config on
    #: a live platform; "pending-source" = source identified but not yet
    #: reverse-engineered; "source-unknown" = nothing probed yet (see
    #: :mod:`.state_leagues` for the source findings behind each).
    status: str
    source_note: str
    #: ``competitions.tier`` (model CHECK: national|state|local).
    #: Defaults to the state rollout's value; national entries override.
    tier: str = "state"
    #: ``competitions.format`` (model CHECK: rounds|tournament — e.g.
    #: origin registers as ``'tournament'``; see :mod:`.national_leagues`).
    #: Defaults to the state rollout's value.
    format: str = "rounds"
    #: ``competitions.sport_id`` — the sport this league's results belong
    #: to.  FCL-9 (post-review M-3): declared HERE so every consumer that
    #: needs a league's sport (league-model-set resolution, model-set
    #: grading, future sync wiring) reads the SAME registry the ingestion
    #: syncs from, instead of re-deriving it from registry-key membership
    #: and drifting the moment a second sport joins either registry.
    #: Defaults to the state rollout's value (all state leagues are
    #: Australian football); national entries override.
    sport_id: str = "afl"


__all__ = ["LeagueConfig"]
