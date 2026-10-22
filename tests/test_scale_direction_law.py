"""SCALE_DIRECTION law: a PARTIAL scale points the way price is moving; exits are never touched.

USER 2026-10-22: "the reduce mechanism is broken ... reducing when it should be augmenting (price rising)
but not reducing when the price is falling (vv shorts)". Measured over 391 published boards before wiring:
12,936 REDUCE acts vs 747 AUGMENT, 85.3% of reduces while the position was in profit, and the adverse-side
family (peak-giveback / breakeven) only 123 of 12,936. tools/scale_direction_census.py reproduces it.

Default OFF, so the engine stays byte-identical until a board promotes the switch (USER 2026-10-17:
a switch flips on v15_pilot results, never by hand).
"""
import dataclasses
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import vec_decisions.scale_direction as SD  # noqa: E402


class _Cfg:
    pass


def _cfg(**kw):
    c = _Cfg()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_defaults_are_off_and_symmetric():
    enabled, lookback, band, block_red, block_aug = SD.params(_Cfg())
    assert enabled is False, "the law must ship OFF — the pilot flips it, not a hand edit"
    assert (lookback, band, block_red, block_aug) == (4, 0.0, True, True)


def test_off_allows_everything():
    c = _Cfg()
    for kind in ("REDUCE", "AUGMENT"):
        for is_long in (True, False):
            assert SD.scale_allowed(c, kind, is_long, 102.0, 100.0)[0]
            assert SD.scale_allowed(c, kind, is_long, 98.0, 100.0)[0]


def test_long_reduce_refused_while_price_rises_augment_allowed():
    c = _cfg(SCALE_DIRECTION_LAW_ENABLED=True)
    ok, why = SD.scale_allowed(c, "REDUCE", True, 102.0, 100.0)
    assert not ok and "augment territory" in why
    assert SD.scale_allowed(c, "AUGMENT", True, 102.0, 100.0)[0]


def test_long_augment_refused_while_price_falls_reduce_allowed():
    c = _cfg(SCALE_DIRECTION_LAW_ENABLED=True)
    ok, why = SD.scale_allowed(c, "AUGMENT", True, 98.0, 100.0)
    assert not ok and "reduce territory" in why
    assert SD.scale_allowed(c, "REDUCE", True, 98.0, 100.0)[0]


def test_short_is_the_mirror():
    """vv shorts: a FALLING price is favourable, so that is where a short must augment, not reduce."""
    c = _cfg(SCALE_DIRECTION_LAW_ENABLED=True)
    assert not SD.scale_allowed(c, "REDUCE", False, 98.0, 100.0)[0]
    assert SD.scale_allowed(c, "AUGMENT", False, 98.0, 100.0)[0]
    assert SD.scale_allowed(c, "REDUCE", False, 102.0, 100.0)[0]
    assert not SD.scale_allowed(c, "AUGMENT", False, 102.0, 100.0)[0]


def test_exit_to_flat_is_never_blocked():
    """Stops, channel exits and reduce-to-flat are exits — BACKTEST_BIBLE §15, exits_never_blocked."""
    c = _cfg(SCALE_DIRECTION_LAW_ENABLED=True)
    assert SD.scale_allowed(c, "REDUCE", True, 102.0, 100.0, to_flat=True)[0]
    assert SD.scale_allowed(c, "REDUCE", False, 98.0, 100.0, to_flat=True)[0]


def test_band_is_a_deadzone_on_both_sides():
    c = _cfg(SCALE_DIRECTION_LAW_ENABLED=True, SCALE_DIRECTION_BAND_PCT=1.0)
    assert SD.scale_allowed(c, "REDUCE", True, 100.5, 100.0)[0], "inside the band: no opinion"
    assert not SD.scale_allowed(c, "REDUCE", True, 101.5, 100.0)[0]
    assert SD.scale_allowed(c, "AUGMENT", True, 99.5, 100.0)[0]
    assert not SD.scale_allowed(c, "AUGMENT", True, 98.5, 100.0)[0]


def test_each_half_can_be_disabled_on_its_own():
    c = _cfg(SCALE_DIRECTION_LAW_ENABLED=True, SCALE_DIRECTION_BLOCK_REDUCE_ON_FAVOURABLE=False)
    assert SD.scale_allowed(c, "REDUCE", True, 102.0, 100.0)[0]
    assert not SD.scale_allowed(c, "AUGMENT", True, 98.0, 100.0)[0]
    c = _cfg(SCALE_DIRECTION_LAW_ENABLED=True, SCALE_DIRECTION_BLOCK_AUGMENT_ON_ADVERSE=False)
    assert not SD.scale_allowed(c, "REDUCE", True, 102.0, 100.0)[0]
    assert SD.scale_allowed(c, "AUGMENT", True, 98.0, 100.0)[0]


def test_fails_open_on_unusable_prices_and_unknown_kinds():
    c = _cfg(SCALE_DIRECTION_LAW_ENABLED=True)
    assert SD.scale_allowed(c, "REDUCE", True, 0.0, 100.0)[0]
    assert SD.scale_allowed(c, "REDUCE", True, 102.0, 0.0)[0]
    assert SD.scale_allowed(c, "CLOSE", True, 102.0, 100.0)[0]


def test_unparseable_knobs_fall_back_instead_of_raising():
    bad = _cfg(SCALE_DIRECTION_LAW_ENABLED=True, SCALE_DIRECTION_LOOKBACK_BARS="x", SCALE_DIRECTION_BAND_PCT="y")
    assert SD.params(bad)[1:3] == (4, 0.0)
    assert SD.scale_allowed(bad, "REDUCE", True, 102.0, 100.0)[0] is False  # still the law, on its defaults


def test_ref_index_clamps_to_the_start_of_the_series():
    assert SD.ref_index(0, 4) == 0 and SD.ref_index(2, 4) == 0 and SD.ref_index(10, 4) == 6
    assert SD.ref_index(10, 0) == 9, "a zero lookback still looks one bar back"


def test_move_pct_signs():
    assert SD.move_pct(True, 110.0, 100.0) == 10.0
    assert SD.move_pct(False, 110.0, 100.0) == -10.0
    assert SD.move_pct(False, 90.0, 100.0) == 10.0


def test_every_switch_is_a_field_of_all_three_configs():
    """A switch missing from QuickConfig is silently dropped by the sweep (evaluate_prepared skips
    non-fields); missing from Config/TradierConfig means live cannot resolve it per sym_side."""
    import config as C
    import config_tradier as CT
    import v12_quick_engine as V

    keys = ["SCALE_DIRECTION_LAW_ENABLED", "SCALE_DIRECTION_LOOKBACK_BARS", "SCALE_DIRECTION_BAND_PCT",
            "SCALE_DIRECTION_BLOCK_REDUCE_ON_FAVOURABLE", "SCALE_DIRECTION_BLOCK_AUGMENT_ON_ADVERSE",
            "SCALE_DIRECTION_EXEMPT_EXIT_CHAIN"]
    for cls in (V.QuickConfig, C.Config, CT.TradierConfig):
        names = {f.name for f in dataclasses.fields(cls)}
        missing = [k for k in keys if k not in names]
        assert not missing, f"{cls.__name__} missing {missing}"
    q, c, t = V.QuickConfig(), C.Config(), CT.TradierConfig()
    for k in keys:
        assert getattr(q, k) == getattr(c, k) == getattr(t, k), f"{k} default differs across configs"
    assert getattr(q, "SCALE_DIRECTION_LAW_ENABLED") is False


def test_engine_calls_the_predicate_at_every_partial_scaling_site():
    """§7.2: the law lives in vec_decisions and is CALLED by the engine — never re-derived inline."""
    src = (ROOT / "v12_quick_engine.py").read_text()
    assert "import vec_decisions.scale_direction" in src
    assert "def _sd_ok(" in src, "the per-bar gate closure must exist"
    assert src.count("_sd_ok(") >= 8, f"expected a gate at every partial scaling site, found {src.count('_sd_ok(')}"
    assert 'vec_decisions.scale_direction.scale_allowed(' in src
    for marker in ('_sd_ok("AUGMENT")', '_sd_ok("REDUCE")'):
        assert marker in src, marker
