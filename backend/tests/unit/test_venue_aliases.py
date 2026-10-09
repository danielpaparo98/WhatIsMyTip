"""Venue alias table — sponsor drift normalization for rugby-league (Phase 5.2).

NRL venues are sponsor-branded and the branding DRIFTS across seasons
(PointsBet Stadium → Ocean Protect Stadium; Mt Smart Stadium → Go Media
Stadium → One NZ Stadium → Hnry Stadium — verified in the live feeds,
see ``.tmp/external-context/nrl-feed/fixturedownload-feed.md``).  The
alias table collapses every observed sponsor variant onto one canonical
ground name so backtests group rows of the same physical ground.
"""

from __future__ import annotations

import logging

import pytest

from packages.shared.ingestion.venue_aliases import (
    CANONICAL_VENUES,
    VENUE_ALIASES,
    resolve_venue,
)

_logger_name = "packages.shared.ingestion.venue_aliases"


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        # Sharks' home (Cronulla) — the drift pair verified live 2026-10-09.
        ("PointsBet Stadium", "Shark Park"),
        ("Ocean Protect Stadium", "Shark Park"),
        ("Southern Cross Group Stadium", "Shark Park"),
        # Warriors' home (Auckland) — four sponsor names for one ground.
        ("Mt Smart Stadium", "Mount Smart Stadium"),
        ("Go Media Stadium", "Mount Smart Stadium"),
        ("One NZ Stadium", "Mount Smart Stadium"),
        ("Hnry Stadium", "Mount Smart Stadium"),
        # Dragons' Kogarah ground.
        ("Jubilee Oval", "Jubilee Stadium"),
        ("Netstrata Jubilee Stadium", "Jubilee Stadium"),
        ("St George Venues Jubilee Stadium", "Jubilee Stadium"),
        # Knights' Newcastle ground (incl. the "sic" 2022 spelling).
        ("McDonald Jones Stadium", "Newcastle Stadium"),
        ("McDonalds Park", "Newcastle Stadium"),
        ("Hunter Stadium", "Newcastle Stadium"),
        # Sydney Olympic Park — Telstra/ANZ/Accor across the eras.
        ("Telstra Stadium", "Stadium Australia"),
        ("ANZ Stadium", "Stadium Australia"),
        ("Accor Stadium", "Stadium Australia"),
        # Moore Park.
        ("Allianz Stadium", "Sydney Football Stadium"),
        # Melbourne Storm.
        ("AAMI Park", "Melbourne Rectangular Stadium"),
        # Brisbane Broncos.
        ("Suncorp Stadium", "Lang Park"),
        # Penrith Panthers.
        ("Pepper Stadium", "Penrith Stadium"),
        ("BlueBet Stadium", "Penrith Stadium"),
        ("Polytec Stadium", "Penrith Stadium"),
        # Dolphins (Redcliffe).
        ("Moreton Daily Stadium", "Dolphin Stadium"),
        ("Kayo Stadium", "Dolphin Stadium"),
        # North Queensland Cowboys.
        ("1300SMILES Stadium", "North Queensland Stadium"),
        ("Queensland Country Bank Stadium", "North Queensland Stadium"),
        # Parramatta Eels.
        ("Bankwest Stadium", "Western Sydney Stadium"),
        ("CommBank Stadium", "Western Sydney Stadium"),
        # Canberra Raiders.
        ("GIO Stadium", "Canberra Stadium"),
        # Manly Sea Eagles (Brookvale).
        ("Lottoland", "Brookvale Oval"),
        ("4 Pines Park", "Brookvale Oval"),
        # Gold Coast Titans.
        ("Cbus Super Stadium", "Robina Stadium"),
        # Perth rectangular venue (distinct from Optus Stadium!).
        ("nib Stadium", "Perth Rectangular Stadium"),
        ("HBF Park", "Perth Rectangular Stadium"),
        # Perth major stadium (State of Origin host).
        ("Optus Stadium", "Perth Stadium"),
        # Minor/satellite sponsor-branded grounds.
        ("C.ex Coffs International Stadium", "Coffs International Stadium"),
        ("Industree Group Stadium", "Central Coast Stadium"),
    ],
)
def test_sponsor_alias_resolves_to_canonical_ground(alias: str, canonical: str):
    assert resolve_venue(alias) == canonical


def test_resolution_is_case_and_whitespace_insensitive():
    assert resolve_venue("  pointsbet stadium ") == "Shark Park"
    assert resolve_venue("CBUS SUPER STADIUM") == "Robina Stadium"
    assert resolve_venue("suncorp  stadium") == "Lang Park"


def test_canonical_names_resolve_to_themselves():
    for canonical in ("Shark Park", "Mount Smart Stadium", "Lang Park"):
        assert resolve_venue(canonical) == canonical


def test_every_declared_canonical_venue_self_resolves():
    """Canonical grounds must never be flagged as unknown backfill
    candidates, so each canonical maps to itself."""
    for canonical in CANONICAL_VENUES:
        assert resolve_venue(canonical) == canonical


def test_unknown_venue_passes_through_verbatim_and_is_logged(caplog):
    """Unknown venues are returned untouched (never mangled) and warned
    about once, so operators can backfill the alias table."""
    with caplog.at_level(logging.WARNING, logger=_logger_name):
        assert resolve_venue("Nowhere Park 2026") == "Nowhere Park 2026"
        assert any("Nowhere Park 2026" in r.message for r in caplog.records)


def test_unknown_venue_warning_is_logged_once_per_name(caplog):
    name = "One-off Ground Alpha"
    with caplog.at_level(logging.WARNING, logger=_logger_name):
        resolve_venue(name)
        resolve_venue(name)
        assert sum(1 for r in caplog.records if "One-off Ground Alpha" in r.message) == 1


def test_none_venue_resolves_to_none():
    assert resolve_venue(None) is None


def test_blank_venue_passes_through():
    assert resolve_venue("   ") == "   "


def test_alias_table_values_are_declared_canonicals():
    """Every alias target must be a canonical ground — no chains."""
    assert set(VENUE_ALIASES.values()) <= set(CANONICAL_VENUES)


def test_alias_table_round_trips_through_resolver():
    for alias, canonical in VENUE_ALIASES.items():
        assert resolve_venue(alias) == canonical
