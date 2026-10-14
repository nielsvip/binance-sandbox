"""USER 2026-10-14 re-entry triggers: exit-price break / dc4_15m break / higher low (vv short) — all 3 can be on."""
import vec_decisions.reentry_triggers as R

ON = lambda k, d: True
OFF = lambda k, d: False


def test_exit_price_break_long():
    assert R.fire(ON, True, 100.5, 99.0, 100.0, 0, 0, 99.5, 100.6, 98.0, 5, 3, 1) == "REENTRY_EXIT_PRICE_BREAK"


def test_dc4_15m_break_long():
    assert R.fire(ON, True, 101.2, 100.8, 110.0, 101.0, 101.0, 100.9, 101.3, 99.0, 5, 3, 1) == "REENTRY_DC4_15M_BREAK"


def test_higher_low_long_and_lower_high_short():
    assert R.fire(ON, True, 100.2, 100.0, 110.0, 105.0, 105.0, 99.5, 100.4, 99.0, 5, 3, 1) == "REENTRY_HIGHER_LOW"
    assert R.fire(ON, False, 99.8, 100.0, 90.0, 95.0, 95.0, 99.6, 100.5, 101.0, 3, 5, -1) == "REENTRY_LOWER_HIGH"


def test_wt_against_blocks_all():
    assert R.fire(ON, True, 100.5, 99.0, 100.0, 0, 0, 99.5, 100.6, 98.0, 3, 5, -1) == ""


def test_switches_off_never_fire():
    assert R.fire(OFF, True, 100.5, 99.0, 100.0, 0, 0, 99.5, 100.6, 98.0, 5, 3, 1) == ""
    assert R.any_enabled(OFF) is False


def test_each_switch_independent():
    only = lambda name: (lambda k, d: k == name)
    args = (True, 100.5, 99.0, 100.0, 0, 0, 99.5, 100.6, 98.0, 5, 3, 1)
    assert R.fire(only("REENTRY_ON_EXIT_PRICE_BREAK_ENABLED"), *args) == "REENTRY_EXIT_PRICE_BREAK"
    assert R.fire(only("REENTRY_ON_DC4_15M_BREAK_ENABLED"), *args) == ""
    assert R.fire(only("REENTRY_ON_HIGHER_LOW_ENABLED"), *args) == "REENTRY_HIGHER_LOW"
