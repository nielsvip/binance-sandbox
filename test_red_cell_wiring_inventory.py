"""Regression checks for paths that were formerly red/no-live-read cells."""

from tools.build_matrix_interdependency_manual import (
    direct_config_read_sites,
    registry_rows,
)


REPAIRED = {
    "BB_PULLBACK_GATE_LONG_MAX",
    "DELTA_EXIT_DC_FLOOR",
    "EXIT_BOUNCE_TOP_ENABLED",
    "EXIT_GAIN_EROSION_ENABLED",
    "EXIT_TREND_REVERSAL_ENABLED",
}


def test_repaired_red_paths_are_direct_per_symbol_live_reads():
    sites = direct_config_read_sites()
    registry = registry_rows()
    for param in REPAIRED:
        assert sites[param]["live_decision"], param
        assert registry[param]["per_sym"], param
