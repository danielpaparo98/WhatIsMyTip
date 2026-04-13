import pytest
from app.squiggle.utils import parse_squiggle_complete


class TestParseSquiggleComplete:
    def test_complete_integer(self):
        assert parse_squiggle_complete(100) is True

    def test_incomplete_integer(self):
        assert parse_squiggle_complete(0) is False

    def test_complete_boolean_true(self):
        assert parse_squiggle_complete(True) is True

    def test_complete_boolean_false(self):
        assert parse_squiggle_complete(False) is False

    def test_complete_string_100(self):
        assert parse_squiggle_complete("100") is True

    def test_complete_string_true(self):
        assert parse_squiggle_complete("true") is True

    def test_complete_string_yes(self):
        assert parse_squiggle_complete("yes") is True

    def test_incomplete_string(self):
        assert parse_squiggle_complete("0") is False

    def test_none_value(self):
        assert parse_squiggle_complete(None) is False

    def test_random_string(self):
        assert parse_squiggle_complete("maybe") is False

    def test_incomplete_integer_50(self):
        assert parse_squiggle_complete(50) is False
