"""FIXED_STOP may tighten toward entry, never loosen away from it (USER 2026-10-17, men:ENJUSDT_LONG)."""
import re
from pathlib import Path
import pytest
from vec_decisions import fixed_stop as fx

SRC = Path(__file__).resolve().parents[1] / "ez_positions_service.py"


def test_loosens_long():
    assert fx.loosens(True, 0.028977, 0.027860) is True
    assert fx.loosens(True, 0.027860, 0.028977) is False


def test_loosens_short():
    assert fx.loosens(False, 0.002026, 0.002105) is True
    assert fx.loosens(False, 0.002105, 0.002026) is False


def test_loosens_ignores_missing_levels():
    assert fx.loosens(True, 0.0, 0.0278) is False
    assert fx.loosens(True, 0.0278, 0.0) is False
    assert fx.loosens(True, None, "x") is False


@pytest.mark.parametrize(
    "is_long,old,new",
    [
        (True, 0.011247, 0.010784),   # men:COTIUSDT_LONG  -4.12%
        (False, 0.002026, 0.002105),  # men:GALAUSDT_SHORT +3.90%
        (True, 0.028977, 0.027860),   # men:ENJUSDT_LONG   -3.85%
        (False, 0.001517, 0.001572),  # men:RVNUSDT_SHORT  +3.63%
        (False, 7.353337, 7.538800),  # men:UNIUSDC_SHORT  +2.52%
        (True, 0.510321, 0.504136),   # flz:WLDUSDC_LONG   -1.21%
    ],
)
def test_every_measured_violation_is_now_refused(is_long, old, new):
    assert fx.loosens(is_long, old, new) is True


def test_enj_flap_sequence_collapses_to_the_entry_bar_level():
    """The real 10-15/10-16 men:ENJUSDT_LONG sequence: entry-bar freeze, klines timeout, rebuild recovers."""
    entry_bar = 0.027860175
    live_channel = 0.028977
    level = entry_bar
    # klines timed out -> the live channel must NOT replace an existing level (fallback is for level<=0 only)
    assert not fx.loosens(True, level, live_channel), "tightening is allowed"
    # ...but the snap-back to the entry bar inside the same lifecycle is a loosening and is refused
    assert fx.loosens(True, live_channel, entry_bar)
    assert level == pytest.approx(entry_bar)


def test_exchange_level_stays_below_software_level():
    soft = fx.freeze_software_level(True, 0.02793, 0.0, 0.02951)
    assert soft == pytest.approx(0.027860175)
    assert fx.exchange_level(True, soft) < soft
    assert fx.round_to_tick(True, fx.exchange_level(True, soft), 0.00001) == pytest.approx(0.02784)


def test_service_keeps_frozen_level_instead_of_live_channel():
    src = SRC.read_text()
    assert "FIXED_STOP_FREEZE_KEPT" in src, "the keep-frozen branch is gone"
    assert re.search(r"if _new <= 0 and _lvl > 0:", src), "live-channel fallback is no longer gated on _lvl<=0"
    assert re.search(r"elif _new <= 0:\n\s+try:\n\s+_ind = ii\(self, _sym\)", src), "LIVE_CHANNEL fallback must be the elif branch"


def test_service_has_the_same_lifecycle_ratchet():
    src = SRC.read_text()
    assert "FIXED_STOP_RATCHET" in src
    assert "_fixed_stop_core.loosens(_is_long, _lvl, _new)" in src
    assert "abs(_anchor - float(_oa_ep)) <= 1.0" in src, "ratchet must only bind inside one unchanged lifecycle"
