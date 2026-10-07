"""test_livewire_fh_filter_tf — FH_MOMENTUM_FILTER_TF threading contract + wiring pins.

Covers the 2026-10-06 venue-separation threading of FH_MOMENTUM_FILTER_TF
(SL 4h, else 15m) into the FH DC-confirm leg: twin core
(vec_decisions.twin_gates_sizing_a.fh_momentum_fires), vec-inline v12 block,
and the ez first-hour daemon confirm read.
"""
from pathlib import Path

from vec_decisions.twin_gates_sizing_a import fh_momentum_fires

ROOT = Path(__file__).resolve().parents[1]
NOW = 13 * 60 + 30 + 10


def _get(store):
    return lambda k, d: store.get(k, d)


def test_default_uses_15m_confirm():
    ok, _ = fh_momentum_fires(_get({}), {"dc_position_15m": 0.2, "mfi_1h": 60}, True, NOW, 101.0, 100.0)
    assert ok is True


def test_default_blocks_on_15m():
    ok, _ = fh_momentum_fires(_get({}), {"dc_position_15m": 0.9, "mfi_1h": 60}, True, NOW, 101.0, 100.0)
    assert ok is False


def test_tf_4h_honored_over_15m():
    ind = {"dc_position_15m": 0.9, "dc_position_4h": 0.2, "mfi_1h": 60}
    ok, _ = fh_momentum_fires(_get({"FH_MOMENTUM_FILTER_TF": "4h"}), ind, True, NOW, 101.0, 100.0)
    assert ok is True


def test_tf_4h_blocks():
    ind = {"dc_position_15m": 0.2, "dc_position_4h": 0.9, "mfi_1h": 60}
    ok, _ = fh_momentum_fires(_get({"FH_MOMENTUM_FILTER_TF": "4h"}), ind, True, NOW, 101.0, 100.0)
    assert ok is False


def test_off_skips_confirm():
    ind = {"dc_position_15m": 0.9, "mfi_1h": 60}
    ok, _ = fh_momentum_fires(_get({"FH_MOMENTUM_FILTER_TF": "OFF"}), ind, True, NOW, 101.0, 100.0)
    assert ok is True


def test_short_side_4h():
    ind = {"dc_position_4h": 0.8, "mfi_1h": 40}
    ok, _ = fh_momentum_fires(_get({"FH_MOMENTUM_FILTER_TF": "4h"}), ind, False, NOW, 99.0, 100.0)
    assert ok is True


def test_vec_inline_wiring_pin():
    src = (ROOT / "v12_quick_engine.py").read_text()
    assert "FH_MOMENTUM_FILTER_TF" in src


def test_ez_daemon_wiring_pin():
    src = (ROOT / "ez_manage.py").read_text()
    assert "FH_MOMENTUM_FILTER_TF" in src
