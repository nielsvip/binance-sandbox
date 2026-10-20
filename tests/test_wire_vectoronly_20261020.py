"""2026-10-20 wire-vectoronly: proves the live READ exists for the FILTER_TF switches wired tonight and that live HONOURS the value.

Scope (the only switches this file claims):
  crypto  EXIT_TOP_FADE_FILTER_TF / CANDLE_PATTERN_STOPS_FILTER_TF  -> ez_manage.py exit_confirm gate (vec v12:22145-22148 `_xc_ok`)
  stocks  PEAK_GIVEBACK_BE_EROSION_FILTER_TF / BREAKEVEN_GAIN_EROSION_FILTER_TF -> tradier_manage.py erosion gate (vec v12:21629-21631)
Every predicate is also checked against the vector's own vec_decisions/generic_filter_tf._cond, so a drift in either side fails here.
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tradier_filter_tf_twins as T  # noqa: E402
from vec_decisions import generic_filter_tf as G  # noqa: E402


def ind(d):
    return lambda k: d.get(k)


def _vec_cond(kind, tf, is_long, keys, close=0.0):
    n = 1
    npz = {k: np.asarray([v], dtype=np.float64) for k, v in keys.items()}

    def safe(z, key, m, default=0.0):
        v = z.get(key)
        return v if v is not None else np.full(m, default, dtype=np.float64)

    return bool(G._cond(kind, npz, n, tf, is_long, np.asarray([close], dtype=np.float64), safe)[0])


# ─────────────────────────── EXIT_TOP_FADE_FILTER_TF (exit_confirm) ───────────────────────────
def test_exit_top_fade_off_is_inert():
    assert T.exit_confirm_block(ind({"wt1_15m": 70.0, "_lc_wt1_15m_prev": 80.0}), True, "TECHNICAL_DC_BREAK", None, None) is None


def test_exit_top_fade_blocks_when_not_fading():
    v = T.exit_confirm_block(ind({"wt1_15m": 10.0, "_lc_wt1_15m_prev": 5.0}), True, "TECHNICAL_DC_BREAK", "15m", None)
    assert v == "EXIT_TOP_FADE_FILTER_TF_15m_NO_CONFIRM"


def test_exit_top_fade_allows_when_fading_from_top():
    assert T.exit_confirm_block(ind({"wt1_15m": 70.0, "_lc_wt1_15m_prev": 80.0}), True, "TECHNICAL_DC_BREAK", "15m", None) is None


def test_exit_top_fade_tf_value_is_honoured():
    d = {"wt1_15m": 70.0, "_lc_wt1_15m_prev": 80.0, "wt1_4h": 10.0, "_lc_wt1_4h_prev": 5.0}
    assert T.exit_confirm_block(ind(d), True, "TECHNICAL_DC_BREAK", "15m", None) is None
    assert T.exit_confirm_block(ind(d), True, "TECHNICAL_DC_BREAK", "4h", None) is not None


def test_exit_top_fade_loss_exit_bypasses():
    assert T.exit_confirm_block(ind({"wt1_15m": 10.0, "_lc_wt1_15m_prev": 5.0}), True, "TECHNICAL_DC_BREAK", "15m", None, -1.5) is None


def test_exit_confirm_only_gates_exit_sig_reasons():
    assert T.exit_confirm_block(ind({"wt1_15m": 10.0, "_lc_wt1_15m_prev": 5.0}), True, "PEAK_GIVEBACK", "15m", None) is None


@pytest.mark.parametrize("is_long,w1,w1p", [(True, 70.0, 80.0), (True, 10.0, 5.0), (False, -70.0, -80.0), (False, -10.0, -5.0)])
def test_top_fade_matches_vector(is_long, w1, w1p):
    live = T.top_fade_confirm(ind({"wt1_15m": w1, "_lc_wt1_15m_prev": w1p}), is_long, "15m")
    n = 2
    npz = {"wt1_15m": np.asarray([w1p, w1], dtype=np.float64)}

    def safe(z, key, m, default=0.0):
        v = z.get(key)
        return v if v is not None else np.full(m, default, dtype=np.float64)

    vec = bool(G._cond("wt_top_fade", npz, n, "15m", is_long, np.zeros(n), safe)[1])
    assert live == vec


# ────────────────────── CANDLE_PATTERN_STOPS_FILTER_TF (exit_confirm) ──────────────────────
def test_candle_pattern_stops_off_is_inert():
    assert T.exit_confirm_block(ind({"bar_pattern_15m": 1}), True, "TECHNICAL_DC_BREAK", None, None) is None


def test_candle_pattern_stops_blocks_on_bullish_pattern_for_long():
    v = T.exit_confirm_block(ind({"bar_pattern_15m": 1}), True, "TECHNICAL_DC_BREAK", None, "15m")
    assert v == "CANDLE_PATTERN_STOPS_FILTER_TF_15m_NO_CONFIRM"


def test_candle_pattern_stops_allows_on_bearish_pattern_for_long():
    assert T.exit_confirm_block(ind({"bar_pattern_15m": 2}), True, "TECHNICAL_DC_BREAK", None, "15m") is None


@pytest.mark.parametrize("is_long,code", [(True, 1), (True, 2), (False, 1), (False, 2), (True, 0)])
def test_candle_against_matches_vector(is_long, code):
    live = T.candle_against_confirm(ind({"bar_pattern_15m": code}), is_long, "15m")
    assert live == _vec_cond("bar_pattern_against", "15m", is_long, {"bar_pattern_15m": float(code)})


# ─────────── PEAK_GIVEBACK_BE_EROSION_FILTER_TF / BREAKEVEN_GAIN_EROSION_FILTER_TF ───────────
def test_erosion_confirm_off_is_inert():
    assert T.erosion_confirm_block(ind({"wt1_15m": 50.0, "wt2_15m": 10.0}), True, None, None) is None


def test_erosion_confirm_peak_giveback_blocks_when_momentum_with_position():
    v = T.erosion_confirm_block(ind({"wt1_15m": 50.0, "wt2_15m": 10.0}), True, "15m", None)
    assert v == "PEAK_GIVEBACK_BE_EROSION_FILTER_TF_15m_NO_CONFIRM"


def test_erosion_confirm_peak_giveback_allows_when_momentum_against():
    assert T.erosion_confirm_block(ind({"wt1_15m": 10.0, "wt2_15m": 50.0}), True, "15m", None) is None


def test_erosion_confirm_breakeven_leg_is_separate():
    v = T.erosion_confirm_block(ind({"wt1_1h": 50.0, "wt2_1h": 10.0}), True, None, "1h")
    assert v == "BREAKEVEN_GAIN_EROSION_FILTER_TF_1h_NO_CONFIRM"


def test_erosion_confirm_tf_value_is_honoured():
    d = {"wt1_15m": 10.0, "wt2_15m": 50.0, "wt1_4h": 50.0, "wt2_4h": 10.0}
    assert T.erosion_confirm_block(ind(d), True, "15m", None) is None
    assert T.erosion_confirm_block(ind(d), True, "4h", None) is not None


def test_erosion_confirm_master_gate_mirrors_vec_jsn2():
    d = {"wt1_15m": 50.0, "wt2_15m": 10.0}
    assert T.erosion_confirm_block(ind(d), True, "15m", None, True) is not None
    assert T.erosion_confirm_block(ind(d), True, "15m", None, False) is None


@pytest.mark.parametrize("is_long,w1,w2", [(True, 10.0, 50.0), (True, 50.0, 10.0), (False, 10.0, 50.0), (False, 50.0, 10.0), (True, 0.0, 0.0)])
def test_wt_against_matches_vector(is_long, w1, w2):
    live = T.wt_against_confirm(ind({"wt1_15m": w1, "wt2_15m": w2}), is_long, "15m")
    assert live == _vec_cond("wt_against", "15m", is_long, {"wt1_15m": w1, "wt2_15m": w2})


# ───────────────────── the live READ itself, judged by the repo's own scanner ─────────────────────
def _bible_scanner():
    spec = importlib.util.spec_from_file_location("_bsb_test", ROOT / "tools" / "build_switch_bible.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _live_reads(bsb, venue, name):
    closures = {v: bsb.import_closure(ROOT / f, block=tuple(Path(o).stem for k, o in bsb.LIVE_ROOTS.items() if k != v)) for v, f in bsb.LIVE_ROOTS.items()}
    for v in closures:
        closures[v] = sorted(set(closures[v]) | set(bsb.shared_vec_modules(closures[v])) | set(bsb.live_pkg_modules(closures[v])))
    out = []
    for f in closures[venue]:
        fs = bsb.FileScan(ROOT / f, bsb.CONFIG_CLASSES.get(f))
        for e in fs.events.get(name, []):
            if e["kind"] == "read" and e["flag"] == "live":
                out.append(f"{f}:{e['line']}({e['func']})")
    return out


@pytest.mark.parametrize("venue,name", [
    ("crypto", "EXIT_TOP_FADE_FILTER_TF"),
    ("crypto", "CANDLE_PATTERN_STOPS_FILTER_TF"),
    ("stocks", "PEAK_GIVEBACK_BE_EROSION_FILTER_TF"),
    ("stocks", "BREAKEVEN_GAIN_EROSION_FILTER_TF"),
])
def test_switch_has_a_live_read_and_left_vector_only(venue, name):
    reads = _live_reads(_bible_scanner(), venue, name)
    assert reads, f"{name} ({venue}) has no live read -> still VECTOR_ONLY"


def test_crypto_exit_confirm_read_is_in_process_position():
    reads = _live_reads(_bible_scanner(), "crypto", "EXIT_TOP_FADE_FILTER_TF")
    assert any("ez_manage.py" in r and "process_position" in r for r in reads), reads


def test_stocks_erosion_read_is_in_the_exit_evaluator():
    reads = _live_reads(_bible_scanner(), "stocks", "PEAK_GIVEBACK_BE_EROSION_FILTER_TF")
    assert any("tradier_manage.py" in r and "<module>" not in r for r in reads), reads
