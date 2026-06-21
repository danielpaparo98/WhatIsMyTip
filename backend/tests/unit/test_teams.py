"""Unit tests for the canonical AFL team-name normaliser.

``canonical_team()`` is the single source of truth that maps every known
alias (Squiggle, AFL Tables, human-friendly forms) to the compact
canonical form used by the logo map, the ELO cache and the model layer.
These tests guard the exact alias→canonical contract.
"""

from __future__ import annotations

import pytest

from packages.shared.teams import TEAM_NAME_SETS, canonical_team


class TestCanonicalTeam:
    @pytest.mark.parametrize(
        "alias,expected",
        [
            # Canonical-in, canonical-out (identity).
            ("Adelaide", "Adelaide"),
            ("Brisbane", "Brisbane"),
            ("Bulldogs", "Bulldogs"),
            # Compound Squiggle names that previously broke logos.
            ("Western Bulldogs", "Bulldogs"),
            ("Footscray", "Bulldogs"),
            ("GWS", "Giants"),
            ("Greater Western Sydney", "Giants"),
            ("GWS Giants", "Giants"),
            ("Gold Coast", "GoldCoast"),
            ("Gold Coast Suns", "GoldCoast"),
            ("North Melbourne", "NorthMelbourne"),
            ("Kangaroos", "NorthMelbourne"),
            ("Port Adelaide", "PortAdelaide"),
            ("Port Power", "PortAdelaide"),
            ("St Kilda", "StKilda"),
            ("West Coast", "WestCoast"),
            ("West Coast Eagles", "WestCoast"),
            # Human-friendly variants from AFL Tables.
            ("Brisbane Lions", "Brisbane"),
            ("Sydney Swans", "Sydney"),
            ("Adelaide Crows", "Adelaide"),
            ("Fremantle Dockers", "Fremantle"),
        ],
    )
    def test_aliases_resolve_to_canonical(self, alias: str, expected: str):
        assert canonical_team(alias) == expected

    def test_case_insensitive(self):
        assert canonical_team("western bulldogs") == "Bulldogs"
        assert canonical_team("GWS") == "Giants"
        assert canonical_team("gws") == "Giants"
        assert canonical_team("ST KILDA") == "StKilda"

    def test_whitespace_tolerant(self):
        assert canonical_team("  Western Bulldogs  ") == "Bulldogs"
        assert canonical_team("\tGold Coast\n") == "GoldCoast"

    def test_unknown_name_returned_unchanged(self):
        # Unknown teams must not be mangled — they pass through stripped.
        assert canonical_team("Tasmania Devils") == "Tasmania Devils"

    def test_empty_and_none_return_empty_string(self):
        assert canonical_team("") == ""
        assert canonical_team(None) == ""

    def test_all_eighteen_teams_have_a_canonical_key(self):
        # AFL has 18 clubs; every one must be representable canonically.
        assert len(TEAM_NAME_SETS) == 18

    @pytest.mark.parametrize(
        "canonical", sorted(TEAM_NAME_SETS.keys()),
    )
    def test_each_canonical_name_self_maps(self, canonical: str):
        assert canonical_team(canonical) == canonical

    def test_every_alias_is_covered(self):
        """Every alias declared in TEAM_NAME_SETS must resolve back to
        its own canonical key (catches typos in the reverse map)."""
        for canonical, aliases in TEAM_NAME_SETS.items():
            for alias in aliases:
                assert canonical_team(alias) == canonical, (
                    f"alias {alias!r} did not resolve to {canonical!r}"
                )
