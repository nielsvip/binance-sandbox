"""test_livewire_bb_band_exits — shared BB band exit core contract + live wiring pins.

Covers the 2026-10-06 venue-separation connection of BB_EXIT_AT_LOSS_TF
(SL 15m) in ez_manage + tradier_manage via vec_decisions.bb_stoch_exits.
"""
import re
from pathlib import Path

import pytest

from vec_decisions.bb_stoch_exits import bb_band_exits

ROOT = Path(__file__).resolve().parents[1]


def _get(store):
    return lambda k, d: store.get(k, d)


def _ind(pct=0.5, lo=0.0, up=0.0, tf="15m"):
    return {f"bb_pct_b_{tf}": pct, f"bb_lower_{tf}": lo, f"bb_upper_{tf}": up}


def test_loss_long_fires_on_lower_touch():
    fire, rsn = bb_band_exits(_get({"BB_EXIT_AT_LOSS_TF": "15m"}), True, _ind(0.03, 99.0, 101.0))
    assert fire is True
    assert rsn == "BB_LOSS_15m"


def test_loss_long_quiet_midband():
    fire, _ = bb_band_exits(_get({"BB_EXIT_AT_LOSS_TF": "15m"}), True, _ind(0.5, 99.0, 101.0))
    assert fire is False


def test_loss_long_fail_closed_without_band():
    fire, _ = bb_band_exits(_get({"BB_EXIT_AT_LOSS_TF": "15m"}), True, _ind(0.03, 0.0, 0.0))
    assert fire is False


def test_loss_short_fires_on_upper_touch():
    fire, rsn = bb_band_exits(_get({"BB_EXIT_AT_LOSS_TF": "1h"}), False, _ind(0.97, 99.0, 101.0, "1h"))
    assert fire is True
    assert rsn == "BB_LOSS_1h"


def test_take_long_fires_on_upper_touch():
    fire, rsn = bb_band_exits(_get({"BB_PROFIT_TAKE_TF": "4h"}), True, _ind(0.97, 99.0, 101.0, "4h"))
    assert fire is True
    assert rsn == "BB_TAKE_4h"


def test_off_inert():
    fire, rsn = bb_band_exits(_get({"BB_EXIT_AT_LOSS_TF": "OFF", "BB_PROFIT_TAKE_TF": "OFF"}), True, _ind(0.01, 99.0, 101.0))
    assert (fire, rsn) == (False, "")


def test_3m_normalizes_to_15m():
    fire, rsn = bb_band_exits(_get({"BB_EXIT_AT_LOSS_TF": "3m"}), True, _ind(0.03, 99.0, 101.0))
    assert fire is True
    assert rsn == "BB_LOSS_15m"


def test_invalid_tf_skipped():
    fire, _ = bb_band_exits(_get({"BB_EXIT_AT_LOSS_TF": "2h"}), True, _ind(0.01, 99.0, 101.0))
    assert fire is False


@pytest.mark.parametrize("manager", ["ez_manage.py", "tradier_manage.py"])
def test_live_wiring_pin(manager):
    src = (ROOT / manager).read_text()
    assert "bb_band_exits" in src, f"{manager} does not call the shared BB core"
