import pytest

from rcp.components.popups.keypad import Keypad


class TestBuildTitle:
    def test_no_limits(self):
        assert Keypad.build_title(42) == "Old Value: 42"

    def test_min_only(self):
        title = Keypad.build_title(50, min_value=10)
        assert "Old Value: 50" in title
        assert "Min: 10" in title
        assert "Max" not in title

    def test_max_only(self):
        title = Keypad.build_title(50, max_value=100)
        assert "Old Value: 50" in title
        assert "Max: 100" in title
        assert "Min" not in title

    def test_both_limits(self):
        title = Keypad.build_title(50, min_value=10, max_value=100)
        assert "Old Value: 50" in title
        assert "Min: 10" in title
        assert "Max: 100" in title

    def test_zero_limits(self):
        title = Keypad.build_title(50, min_value=0, max_value=0)
        assert "Min: 0" in title
        assert "Max: 0" in title

    def test_negative_limits(self):
        title = Keypad.build_title(0, min_value=-100, max_value=-10)
        assert "Min: -100" in title
        assert "Max: -10" in title

    def test_float_limits(self):
        title = Keypad.build_title(5.0, min_value=1.5, max_value=9.9)
        assert "Min: 1.5" in title
        assert "Max: 9.9" in title


class TestParseValue:
    def test_integer_text(self):
        assert Keypad.parse_value("42") == 42
        assert isinstance(Keypad.parse_value("42"), int)

    def test_float_text(self):
        assert Keypad.parse_value("3.14") == pytest.approx(3.14)
        assert isinstance(Keypad.parse_value("3.14"), float)

    def test_negative_integer(self):
        assert Keypad.parse_value("-10") == -10

    def test_negative_float(self):
        assert Keypad.parse_value("-2.5") == pytest.approx(-2.5)

    def test_integer_flag_truncates_float(self):
        assert Keypad.parse_value("3.9", integer=True) == 3
        assert isinstance(Keypad.parse_value("3.9", integer=True), int)

    def test_integer_flag_keeps_integer(self):
        assert Keypad.parse_value("42", integer=True) == 42

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            Keypad.parse_value("")

    def test_non_numeric_raises(self):
        with pytest.raises(ValueError):
            Keypad.parse_value("abc")


class TestValidateValue:
    # No limits
    def test_no_limits_any_value(self):
        assert Keypad.validate_value(999) is True
        assert Keypad.validate_value(-999) is True
        assert Keypad.validate_value(0) is True

    # Min only
    def test_min_only_above(self):
        assert Keypad.validate_value(50, min_value=10) is True

    def test_min_only_at_boundary(self):
        assert Keypad.validate_value(10, min_value=10) is True

    def test_min_only_below(self):
        assert Keypad.validate_value(5, min_value=10) is False

    def test_min_only_allows_any_above(self):
        assert Keypad.validate_value(999999, min_value=0) is True

    # Max only
    def test_max_only_below(self):
        assert Keypad.validate_value(50, max_value=100) is True

    def test_max_only_at_boundary(self):
        assert Keypad.validate_value(100, max_value=100) is True

    def test_max_only_above(self):
        assert Keypad.validate_value(150, max_value=100) is False

    def test_max_only_allows_any_below(self):
        assert Keypad.validate_value(-999, max_value=100) is True

    # Both limits
    def test_both_within_range(self):
        assert Keypad.validate_value(50, min_value=10, max_value=100) is True

    def test_both_at_min(self):
        assert Keypad.validate_value(10, min_value=10, max_value=100) is True

    def test_both_at_max(self):
        assert Keypad.validate_value(100, min_value=10, max_value=100) is True

    def test_both_below_min(self):
        assert Keypad.validate_value(5, min_value=10, max_value=100) is False

    def test_both_above_max(self):
        assert Keypad.validate_value(150, min_value=10, max_value=100) is False

    # Negative ranges
    def test_negative_range(self):
        assert Keypad.validate_value(-25, min_value=-50, max_value=50) is True

    def test_negative_below_negative_min(self):
        assert Keypad.validate_value(-60, min_value=-50, max_value=50) is False

    # Float values
    def test_float_within_range(self):
        assert Keypad.validate_value(5.5, min_value=0, max_value=10) is True

    def test_float_below_min(self):
        assert Keypad.validate_value(0.5, min_value=1.0) is False

    def test_float_above_max(self):
        assert Keypad.validate_value(10.1, max_value=10.0) is False

    def test_float_at_boundaries(self):
        assert Keypad.validate_value(1.0, min_value=1.0, max_value=10.0) is True
        assert Keypad.validate_value(10.0, min_value=1.0, max_value=10.0) is True

    # Zero as boundary
    def test_zero_min(self):
        assert Keypad.validate_value(0, min_value=0) is True
        assert Keypad.validate_value(-1, min_value=0) is False

    def test_zero_max(self):
        assert Keypad.validate_value(0, max_value=0) is True
        assert Keypad.validate_value(1, max_value=0) is False