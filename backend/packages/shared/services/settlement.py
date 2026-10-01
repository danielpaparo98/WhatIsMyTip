"""Profit settlement for backtests (BT-ODDS, 2026-10 review).

Historical behaviour settled every tip at even money (``±STAKE``), which
assumes a constant return regardless of the pick's actual price.  Real
head-to-head markets price favourites far below $2.00 (typically
$1.20–$1.70 in the AFL) and underdogs above it, so even-money settlement
systematically overstated profits for favourite-picking heuristics and
understated them for underdog pickers.

This module is the single settlement kernel used by every backtest path:

* **Real odds where available.**  Games with an ingested ``game_odds``
  snapshot (The Odds API) settle at the snapshot's decimal price for the
  tipped side.
* **Representative fallback price.**  Games without a snapshot — the
  entire historical ledger before odds ingestion began — settle at
  ``FALLBACK_DECIMAL_ODDS`` ($1.90), a typical ~5% overround price,
  rather than the bookmaker-margin-free $2.00 even-money assumption.
* **Draws are a push.**  A drawn AFL game has no winner; standard
  two-way head-to-head markets refund the stake, so settlement is $0
  (previously a draw was charged as a full loss).

Frontends should surface the ``odds_coverage`` field alongside profit so
users can see what share of a season's tips were settled at real prices.
"""

from __future__ import annotations

# Stake placed on every tip, in dollars.
STAKE_PER_GAME = 10.0

# Decimal price used to settle games without a real odds snapshot.
# $1.90 models a representative AFL head-to-head market with a ~5%
# bookmaker overround — deliberately NOT the margin-free $2.00.
FALLBACK_DECIMAL_ODDS = 1.90

# Where the fallback came from — reported by the API so clients can
# label profit honestly.
FALLBACK_PRICE_LABEL = "representative $1.90"


def is_valid_decimal_odds(price: float | None) -> bool:
    """True when ``price`` is a usable decimal-odds value (> 1.0)."""
    return price is not None and price > 1.0


def tipped_side_price(
    *,
    selected_team: str | None,
    home_team: str | None,
    away_team: str | None,
    home_odds: float | None,
    away_odds: float | None,
) -> float | None:
    """Decimal price for the tipped side from an odds snapshot.

    Returns ``None`` when there is no usable price for the side that was
    tipped (missing snapshot, or the snapshot lacks that side's price) —
    callers then settle at the fallback price.
    """
    if selected_team is None:
        return None
    if selected_team == home_team:
        return home_odds if is_valid_decimal_odds(home_odds) else None
    if selected_team == away_team:
        return away_odds if is_valid_decimal_odds(away_odds) else None
    return None


def settle_stake(
    *,
    is_correct: bool,
    is_draw: bool,
    decimal_odds: float | None,
) -> float:
    """Settle one ``STAKE_PER_GAME`` bet and return the profit.

    Args:
        is_correct: Whether the tip matched the game's actual winner.
        is_draw: Whether the game ended in a draw (no winner).  Draws
            push — the stake is refunded regardless of correctness.
        decimal_odds: The decimal price for the tipped side, or ``None``
            to settle at :data:`FALLBACK_DECIMAL_ODDS`.

    Returns:
        Profit in dollars: ``stake × (price − 1)`` for a winning bet,
        ``−stake`` for a losing bet, ``0`` for a push.
    """
    if is_draw:
        return 0.0
    # mypy narrows through the direct comparison — a TypeGuard on
    # ``is_valid_decimal_odds`` would work too, but keep it simple.
    price = (
        decimal_odds
        if decimal_odds is not None and decimal_odds > 1.0
        else FALLBACK_DECIMAL_ODDS
    )
    if is_correct:
        return STAKE_PER_GAME * (price - 1.0)
    return -STAKE_PER_GAME


__all__ = [
    "FALLBACK_DECIMAL_ODDS",
    "FALLBACK_PRICE_LABEL",
    "STAKE_PER_GAME",
    "is_valid_decimal_odds",
    "settle_stake",
    "tipped_side_price",
]
