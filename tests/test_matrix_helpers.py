"""
Tests for utils/matrix_helpers.py — percent-based matrix helpers.

Verifies:
- percent_to_weights / weights_to_percent (flat + nested)
- validate_percent_matrix (sum check with tolerance)
- auto_normalize_percent (scales to 100 %)
- is_old_scale / migrate_to_percent (backward compat)
- build_params_from_ui roundtrip (defaults survive migration)
"""
import pytest

from utils.matrix_helpers import (
    percent_to_weights,
    weights_to_percent,
    validate_percent_matrix,
    auto_normalize_percent,
    is_old_scale,
    migrate_to_percent,
)


# ── 1. percent_to_weights ─────────────────────────────────────────────

class TestPercentToWeights:
    def test_flat(self):
        assert percent_to_weights({"A": 50.0, "B": 25.0}) == {"A": 0.5, "B": 0.25}

    def test_nested(self):
        result = percent_to_weights({
            "alter_unter_30": {"Default": 12.0, "IT": 15.0},
            "alter_55_plus": {"Default": 2.0},
        })
        assert result["alter_unter_30"]["Default"] == pytest.approx(0.12)
        assert result["alter_unter_30"]["IT"] == pytest.approx(0.15)
        assert result["alter_55_plus"]["Default"] == pytest.approx(0.02)

    def test_empty(self):
        assert percent_to_weights({}) == {}

    def test_zero(self):
        assert percent_to_weights({"X": 0.0}) == {"X": 0.0}

    def test_hundred(self):
        assert percent_to_weights({"X": 100.0}) == {"X": 1.0}


# ── 2. weights_to_percent ─────────────────────────────────────────────

class TestWeightsToPercent:
    def test_flat(self):
        assert weights_to_percent({"A": 0.5, "B": 0.25}) == {"A": 50.0, "B": 25.0}

    def test_nested(self):
        result = weights_to_percent({
            "alter_unter_30": {"Default": 0.12},
        })
        assert result["alter_unter_30"]["Default"] == pytest.approx(12.0)

    def test_roundtrip(self):
        """percent → weights → percent is identity."""
        orig = {"A": 33.3, "B": 66.7}
        assert weights_to_percent(percent_to_weights(orig)) == pytest.approx(orig)


# ── 3. validate_percent_matrix ────────────────────────────────────────

class TestValidatePercentMatrix:
    def test_valid_sum(self):
        ok, msg = validate_percent_matrix({"A": 60.0, "B": 40.0})
        assert ok is True
        assert "100.0" in msg

    def test_valid_with_default(self):
        """Default key is excluded from sum."""
        ok, _ = validate_percent_matrix({"Default": 50.0, "A": 60.0, "B": 40.0})
        assert ok is True

    def test_invalid_sum(self):
        ok, msg = validate_percent_matrix({"A": 70.0, "B": 40.0})
        assert ok is False
        assert "110.0" in msg

    def test_within_tolerance(self):
        ok, _ = validate_percent_matrix({"A": 60.5, "B": 40.0}, tolerance=1.0)
        assert ok is True

    def test_empty(self):
        ok, msg = validate_percent_matrix({})
        assert ok is True
        assert msg == ""

    def test_only_default(self):
        ok, msg = validate_percent_matrix({"Default": 50.0})
        assert ok is True
        assert msg == ""


# ── 4. auto_normalize_percent ─────────────────────────────────────────

class TestAutoNormalizePercent:
    def test_scales_to_100(self):
        result = auto_normalize_percent({"A": 70.0, "B": 40.0})
        total = sum(result.values())
        assert total == pytest.approx(100.0)
        assert result["A"] == pytest.approx(70.0 / 110.0 * 100.0)
        assert result["B"] == pytest.approx(40.0 / 110.0 * 100.0)

    def test_preserves_default(self):
        result = auto_normalize_percent({"Default": 99.0, "A": 30.0, "B": 70.0})
        assert result["Default"] == 99.0
        assert result["A"] + result["B"] == pytest.approx(100.0)

    def test_already_100(self):
        orig = {"A": 60.0, "B": 40.0}
        result = auto_normalize_percent(orig)
        assert result == pytest.approx(orig)

    def test_all_zeros(self):
        """All zeros → cannot normalize, return as-is."""
        orig = {"A": 0.0, "B": 0.0}
        result = auto_normalize_percent(orig)
        assert result == orig

    def test_empty(self):
        assert auto_normalize_percent({}) == {}


# ── 5. is_old_scale ───────────────────────────────────────────────────

class TestIsOldScale:
    def test_old_scale(self):
        """All values ≤ 1.0 → old format."""
        assert is_old_scale({"Default": 0.05, "IT": 0.10}) is True

    def test_new_scale(self):
        """Any value > 1.0 → new format."""
        assert is_old_scale({"Default": 5.0, "IT": 10.0}) is False

    def test_mixed(self):
        """Mixed: 0.5 and 50 → new format (50 > 1)."""
        assert is_old_scale({"A": 0.5, "B": 50.0}) is False

    def test_nested_old(self):
        """Nested quit_matrix in old format."""
        mat = {
            "alter_unter_30": {"Default": 0.12},
            "alter_55_plus": {"Default": 0.02},
        }
        assert is_old_scale(mat) is True

    def test_nested_new(self):
        mat = {
            "alter_unter_30": {"Default": 12.0},
            "alter_55_plus": {"Default": 2.0},
        }
        assert is_old_scale(mat) is False

    def test_empty(self):
        assert is_old_scale({}) is False

    def test_all_zeros(self):
        """All zeros → no positive value → not old scale."""
        assert is_old_scale({"A": 0.0, "B": 0.0}) is False

    def test_value_exactly_one(self):
        """1.0 is ≤ 1.0 → old scale."""
        assert is_old_scale({"X": 1.0}) is True


# ── 6. migrate_to_percent ─────────────────────────────────────────────

class TestMigrateToPercent:
    def test_migrates_old(self):
        result = migrate_to_percent({"Default": 0.05, "IT": 0.10})
        assert result == {"Default": 5.0, "IT": 10.0}

    def test_keeps_new(self):
        orig = {"Default": 5.0, "IT": 10.0}
        result = migrate_to_percent(orig)
        assert result == orig

    def test_nested_migration(self):
        mat = {
            "alter_unter_30": {"Default": 0.12, "IT": 0.15},
            "alter_55_plus": {"Default": 0.02},
        }
        result = migrate_to_percent(mat)
        assert result["alter_unter_30"]["Default"] == pytest.approx(12.0)
        assert result["alter_unter_30"]["IT"] == pytest.approx(15.0)
        assert result["alter_55_plus"]["Default"] == pytest.approx(2.0)

    def test_empty(self):
        assert migrate_to_percent({}) == {}

    def test_does_not_mutate(self):
        """Original dict is not modified."""
        orig = {"A": 0.5}
        migrate_to_percent(orig)
        assert orig == {"A": 0.5}


# ── 7. build_params_from_ui roundtrip ─────────────────────────────────

class TestBuildParamsRoundtrip:
    """Verify that default_params() values survive the migration roundtrip."""

    def test_atz_defaults_survive(self):
        from abgaenge.params import default_params, build_params_from_ui
        # Simulate: UI doesn't touch atz_matrix → defaults flow through
        params = build_params_from_ui({})
        assert params["atz"]["atz_matrix"]["Default"] == pytest.approx(0.05)

    def test_quit_defaults_survive(self):
        from abgaenge.params import default_params, build_params_from_ui
        params = build_params_from_ui({})
        assert params["quit"]["quit_matrix"]["alter_unter_30"]["Default"] == pytest.approx(0.12)
        assert params["quit"]["quit_matrix"]["alter_55_plus"]["Default"] == pytest.approx(0.02)

    def test_percent_ui_values_convert(self):
        from abgaenge.params import build_params_from_ui
        ui_state = {
            "atz": {
                "new_atz_rate": 0.08,
                "atz_matrix": {"Default": 8.0, "IT": 12.0},
            },
        }
        params = build_params_from_ui(ui_state)
        assert params["atz"]["atz_matrix"]["Default"] == pytest.approx(0.08)
        assert params["atz"]["atz_matrix"]["IT"] == pytest.approx(0.12)

    def test_quit_percent_ui_values_convert(self):
        from abgaenge.params import build_params_from_ui
        ui_state = {
            "quit": {
                "quit_rate_base": 0.05,
                "quit_matrix": {
                    "alter_unter_30": {"Default": 15.0, "IT": 20.0},
                    "alter_30_45": {"Default": 10.0},
                    "alter_45_55": {"Default": 5.0},
                    "alter_55_plus": {"Default": 2.0},
                },
            },
        }
        params = build_params_from_ui(ui_state)
        assert params["quit"]["quit_matrix"]["alter_unter_30"]["Default"] == pytest.approx(0.15)
        assert params["quit"]["quit_matrix"]["alter_unter_30"]["IT"] == pytest.approx(0.20)
        assert params["quit"]["quit_matrix"]["alter_55_plus"]["Default"] == pytest.approx(0.02)
