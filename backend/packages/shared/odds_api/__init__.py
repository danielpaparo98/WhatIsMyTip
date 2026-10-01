"""The Odds API integration (BT-ODDS).

Provides decimal head-to-head odds for AFL so backtests can settle tips
at real bookmaker prices instead of a constant even-money return.
"""

from .client import OddsAPIClient, odds_api_canonical_team, parse_event

__all__ = ["OddsAPIClient", "odds_api_canonical_team", "parse_event"]
