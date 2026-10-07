"""test_livewire_venue_wiring — connection pins for inline live blocks.

The RSI2/COOLDOWN/DC blocks are inline in the giant live managers (not unit
callable without risky refactors), so these pins assert the knob read, the
vec-matching default, and the action wiring exist in the live path. They guard
the exact regression mode observed in the 2026-10-06 audit (template-swept
knob silently unwired live). Shared-core behavior (BB exits, FH confirm leg)
is covered behaviorally in test_livewire_bb_band_exits.py and
test_livewire_fh_filter_tf.py.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EZ = (ROOT / "ez_manage.py").read_text()
TR = (ROOT / "tradier_manage.py").read_text()


def test_rsi2_ez_reads_both_thresholds_with_vec_defaults():
    assert '"TRADIER_RSI2_EXIT_THRESHOLD_LONG", 90.0' in EZ
    assert '"TRADIER_RSI2_EXIT_THRESHOLD_SHORT", 10.0' in EZ
    assert "RSI2_EXIT_LIVE_CLOSED" in EZ
    assert '"rsi_2_3m", "rsi_2_15m", "rsi_2_1h"' in EZ


def test_cooldown_ez_bars_gate():
    assert '"COOLDOWN_BARS", 0' in EZ
    assert "(_cd_age_m / 15.0) < _cd_bars" in EZ
    assert "[COOLDOWN_BARS]" in EZ


def test_dc_tr_breakout_block():
    assert "DC_BREAKOUT_TF_EXPANDED" in TR
    assert "'DC_BREAKOUT_TF', '1h'" in TR
    assert "_dc_adx > 25" in TR
    assert "TRADIER_DC_BREAKOUT_" in TR
    assert 'bool(_cfg_auto(\'DC_BREAKOUT_ENTRY_ENABLED\', False))' in TR


def test_fh_tr_resolves_via_per_sym_get():
    assert "fh_momentum_fires(lambda _k, _d: _cfg_auto(_k, _d)" in TR


def test_bb_tr_wiring_pin():
    assert "BB_BAND_EXIT_LIVE_" in TR
    assert "bb_band_exits as _bbx_exits_tr" in TR
    assert "_bbx_exits_tr(lambda _k, _d: _cfg(" in TR
