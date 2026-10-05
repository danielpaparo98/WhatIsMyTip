"""Unit tests for ``packages.shared.schemas.query`` heuristic allowlists.

boosted-tip-06: ``boosted_tip`` joins both regex Fields while
``best_bet`` is KEPT so historical ``tips`` rows stay queryable
(feature decision 3).  The exact pattern strings are asserted so the
two Fields cannot drift apart (or from the API-level pattern in
``app/api/tips.py``) — they must change in lockstep.
"""

import pytest
from pydantic import ValidationError
from pydantic.fields import FieldInfo

from packages.shared.schemas.query import (
    _VALID_HEURISTICS,
    TipsGameWithTipsQuery,
    TipsQuery,
)

# The canonical allowlist regex — kept here verbatim so a future edit
# that changes one side without the other breaks this test.
EXPECTED_PATTERN = r"^(best_bet|weighted_tip|yolo|boosted_tip)$"


def _field_pattern(field_info: FieldInfo) -> str | None:
    """Extract the ``pattern=`` constraint from a Pydantic v2 FieldInfo.

    Duck-typed on purpose: depending on the pydantic/annotated-types
    version the pattern constraint arrives as ``annotated_types.Pattern``
    or as pydantic's own general metadata object — both carry a
    ``.pattern`` string attribute, neither is a stable public class.
    """
    for meta in field_info.metadata:
        pattern = getattr(meta, "pattern", None)
        if isinstance(pattern, str):
            return pattern
    return None


# ---------------------------------------------------------------------------
# TipsQuery.heuristic  (GET /api/tips)
# ---------------------------------------------------------------------------


class TestTipsQueryHeuristic:
    """``TipsQuery.heuristic`` accepts all four heuristics, nothing else."""

    @pytest.mark.parametrize(
        "heuristic", ["best_bet", "weighted_tip", "yolo", "boosted_tip"]
    )
    def test_accepts_all_valid_heuristics(self, heuristic: str) -> None:
        q = TipsQuery.model_validate({"heuristic": heuristic})
        assert q.heuristic == heuristic

    def test_default_is_none(self) -> None:
        q = TipsQuery.model_validate({})
        assert q.heuristic is None

    def test_rejects_unknown_heuristic(self) -> None:
        with pytest.raises(ValidationError):
            TipsQuery.model_validate({"heuristic": "not_a_real_heuristic"})

    def test_rejects_unanchored_partial_match(self) -> None:
        """The regex is anchored — prefixes/extensions must not leak through."""
        with pytest.raises(ValidationError):
            TipsQuery.model_validate({"heuristic": "boosted_tip_extra"})


# ---------------------------------------------------------------------------
# TipsGameWithTipsQuery.heuristic  (GET /api/tips/games-with-tips)
# ---------------------------------------------------------------------------


class TestTipsGameWithTipsQueryHeuristic:
    """``TipsGameWithTipsQuery.heuristic`` accepts all four heuristics."""

    @pytest.mark.parametrize(
        "heuristic", ["best_bet", "weighted_tip", "yolo", "boosted_tip"]
    )
    def test_accepts_all_valid_heuristics(self, heuristic: str) -> None:
        q = TipsGameWithTipsQuery.model_validate(
            {"season": 2025, "round_id": 1, "heuristic": heuristic}
        )
        assert q.heuristic == heuristic

    def test_default_is_best_bet(self) -> None:
        """Historical contract: the default heuristic remains ``best_bet``."""
        q = TipsGameWithTipsQuery.model_validate({"season": 2025, "round_id": 1})
        assert q.heuristic == "best_bet"

    def test_rejects_unknown_heuristic(self) -> None:
        with pytest.raises(ValidationError):
            TipsGameWithTipsQuery.model_validate(
                {"season": 2025, "round_id": 1, "heuristic": "nope"}
            )


# ---------------------------------------------------------------------------
# Lockstep drift prevention
# ---------------------------------------------------------------------------


class TestPatternLockstep:
    """Both regex Fields must carry the exact same anchored pattern."""

    def test_valid_heuristics_set_exact(self) -> None:
        assert _VALID_HEURISTICS == {
            "best_bet",
            "weighted_tip",
            "yolo",
            "boosted_tip",
        }

    def test_tips_query_pattern_exact(self) -> None:
        assert _field_pattern(TipsQuery.model_fields["heuristic"]) == EXPECTED_PATTERN

    def test_games_with_tips_query_pattern_exact(self) -> None:
        assert (
            _field_pattern(TipsGameWithTipsQuery.model_fields["heuristic"])
            == EXPECTED_PATTERN
        )
