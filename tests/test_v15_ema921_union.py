"""USER 2026-10-12: EMA_9_21_FILTER_TFS + EMA_9_21_TIMEFRAME are CO-EQUAL per-TF checks
(union, TFS order first, TIMEFRAME appended when new) — never fallback-discarded —
wired identically in live, vec, and backtest; E921 supersedes the SMA-kindergarten gate.

Direct-import tests for the three stdlib/numpy-only twins + scalar<->vec parity on
synthetic bars + source-pattern pins for the live/vec/backtest call sites (those
modules are too heavy to import here).
"""
import numpy as np
import pytest

from vec_decisions import kg_entry_gate as KEG
from vec_decisions import live_kindergarten_stocks as LKS
import tradier_filter_tf_twins as TFT

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EZ_SRC = (ROOT / "ez_manage.py").read_text()
V12_SRC = (ROOT / "v12_quick_engine.py").read_text()
BT_SRC = (ROOT / "backtest_v12_engine.py").read_text()


def _get(d):
    return lambda k, dv=None: d.get(k, dv)


# ---- canonical union ----

def test_union_defaults_inert():
    assert KEG.eff_921_tfs(_get({})) == ["1h"]
    assert KEG.eff_921_tfs(_get({"EMA_9_21_FILTER_TFS": "1h", "EMA_9_21_TIMEFRAME": "1h"})) == ["1h"]


def test_union_both_orders_and_dedupes():
    g = _get({"EMA_9_21_FILTER_TFS": "4h,D", "EMA_9_21_TIMEFRAME": "1h"})
    assert KEG.eff_921_tfs(g) == ["4h", "D", "1h"]
    g = _get({"EMA_9_21_FILTER_TFS": "1h,D", "EMA_9_21_TIMEFRAME": "D"})
    assert KEG.eff_921_tfs(g) == ["1h", "D"]


def test_union_timeframe_only_counts():
    g = _get({"EMA_9_21_FILTER_TFS": "", "EMA_9_21_TIMEFRAME": "D"})
    assert KEG.eff_921_tfs(g) == ["D"]


# ---- scalar gate honors each TF ----

def test_scalar_timeframe_flip_moves():
    base = {"EMA_9_21_FILTER_ENABLED": True, "EMA_9_21_FILTER_TFS": "1h",
            "KINDERGARTEN_CUMULATIVE_MIN_TFS": 2, "KINDERGARTEN_STRICT_TFS": ""}
    ind = {"ema_9_above_21_1h": 1.0, "ema_9_above_21_D": 0.0}
    ok_both, n, c = KEG.ema921_pass(_get(dict(base, EMA_9_21_TIMEFRAME="D")), ind, True)
    assert (ok_both, n, c) == (False, 1, 2)
    ok_same, n, c = KEG.ema921_pass(_get(dict(base, EMA_9_21_TIMEFRAME="1h")), ind, True)
    assert (ok_same, n, c) == (True, 1, 1)


def test_scalar_tfs_flip_moves():
    base = {"EMA_9_21_FILTER_ENABLED": True, "EMA_9_21_TIMEFRAME": "1h",
            "KINDERGARTEN_CUMULATIVE_MIN_TFS": 2, "KINDERGARTEN_STRICT_TFS": ""}
    ind = {"ema_9_above_21_1h": 1.0, "ema_9_above_21_D": 0.0}
    ok, n, c = KEG.ema921_pass(_get(dict(base, EMA_9_21_FILTER_TFS="1h")), ind, True)
    assert (ok, n, c) == (True, 1, 1)
    ok, n, c = KEG.ema921_pass(_get(dict(base, EMA_9_21_FILTER_TFS="D")), ind, True)
    assert (ok, n, c) == (False, 1, 2)


# ---- scalar <-> vec parity (shared predicate) ----

def _synth_npz():
    return {
        "ema_9_above_21_1h": np.array([1.0, 0.0, 1.0, 0.0, 1.0, 0.0]),
        "ema_9_above_21_D": np.array([1.0, 1.0, 0.0, 0.0, 1.0, 0.0]),
        "ema_9_above_21_15m": np.array([0.0, 1.0, 1.0, 0.0, 0.0, 1.0]),
    }


class _Cfg:
    def __init__(self, d):
        self.__dict__.update(d)


def _safe(npz, k, n, default):
    return npz[k] if k in npz else np.full(n, default, dtype=float)


@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("strict", ["", "D"])
def test_shared_scalar_vec_parity(is_long, strict):
    d = {"EMA_9_21_FILTER_ENABLED": True, "EMA_9_21_FILTER_TFS": "1h,D",
         "EMA_9_21_TIMEFRAME": "15m", "KINDERGARTEN_CUMULATIVE_MIN_TFS": 2,
         "KINDERGARTEN_STRICT_TFS": strict}
    npz, n = _synth_npz(), 6
    mask = KEG.ema921_pass_vec(npz, n, is_long, _Cfg(d), _safe)
    assert mask is not None
    for i in range(n):
        ind = {k: float(v[i]) for k, v in npz.items()}
        ok, _, _ = KEG.ema921_pass(_get(d), ind, is_long)
        assert bool(mask[i]) is bool(ok), f"bar {i} long={is_long} strict={strict!r}"


# ---- stocks twins parity (vec b6 <-> live scalar) ----

def _stocks_cfg(cum):
    return {"EMA_9_21_FILTER_ENABLED": True, "KINDERGARTEN_EMA_GATE_ENABLED": False,
            "KG_STOCKS_LIVE_GATE": True, "KG_STOCKS_HARD_VETO_ENABLED": True,
            "KINDERGARTEN_CUMULATIVE_MODE": cum, "EMA_9_21_FILTER_TFS": "1h,D",
            "EMA_9_21_TIMEFRAME": "15m", "KINDERGARTEN_CUMULATIVE_MIN_TFS": 0,
            "EMA_9_21_FILTER_MIN_TFS": 2, "KINDERGARTEN_STRICT_TFS": ""}


@pytest.mark.parametrize("is_long", [True, False])
def test_stocks_twins_parity_cum(is_long):
    d = _stocks_cfg(True)
    npz, n = _synth_npz(), 6
    close = np.full(n, 100.0)
    mask = LKS.pass_mask(npz, n, is_long, _Cfg(d), close, _safe)
    assert mask is not None
    for i in range(n):
        ind = {k: float(v[i]) for k, v in npz.items()}
        veto = TFT.kg_stocks_block(lambda k, dv=None: d.get(k, dv), ind.get, 100.0, is_long)
        assert (veto is None) is bool(mask[i]), f"bar {i} long={is_long}: {veto} vs {mask[i]}"


@pytest.mark.parametrize("is_long", [True, False])
def test_stocks_twins_parity_legacy(is_long):
    d = _stocks_cfg(False)
    npz, n = _synth_npz(), 6
    close = np.full(n, 100.0)
    mask = LKS.pass_mask(npz, n, is_long, _Cfg(d), close, _safe)
    assert mask is not None
    for i in range(n):
        ind = {k: float(v[i]) for k, v in npz.items()}
        veto = TFT.kg_stocks_block(lambda k, dv=None: d.get(k, dv), ind.get, 100.0, is_long)
        assert (veto is None) is bool(mask[i]), f"bar {i} long={is_long}: {veto} vs {mask[i]}"


def test_stocks_legacy_union_blocks_on_either_tf():
    d = _stocks_cfg(False)
    veto = TFT.kg_stocks_block(lambda k, dv=None: d.get(k, dv),
                               {"ema_9_above_21_1h": 1.0, "ema_9_above_21_D": 0.0,
                                "ema_9_above_21_15m": 1.0}.get, 100.0, True)
    assert veto is not None and "LEGACY_D_BLOCK" in veto
    veto = TFT.kg_stocks_block(lambda k, dv=None: d.get(k, dv),
                               {"ema_9_above_21_1h": 1.0, "ema_9_above_21_D": 1.0,
                                "ema_9_above_21_15m": 1.0}.get, 100.0, True)
    assert veto is None


# ---- call-site pins (source patterns; modules too heavy to import) ----

def test_ez_gates_independent_both_sites():
    # USER 2026-10-12 correction: KG + E921 are INDEPENDENT switches — both apply, never elif.
    assert "KG_SUPERSEDED_BY_E921" not in EZ_SRC
    assert "INDEPENDENT switches" in EZ_SRC
    i = EZ_SRC.index("if _kgx_on or _e921_on:")
    seg = EZ_SRC[i:i + 2500]
    assert "if _kgx_on:" in seg and "if _e921_on:" in seg and "elif _kgx_on:" not in seg


def test_vec_gates_independent_three_sites_and_legacy_union():
    assert "SUPERSEDE" not in V12_SRC
    assert V12_SRC.count("INDEPENDENT switches") == 3
    assert "_tf1_921" in V12_SRC  # legacy block union


def test_backtest_stub_gate_present():
    assert "USER 2026-10-12 (FLT2 twin)" in BT_SRC
    assert "BLOCKED_EMA_9_21_FILTER_" in BT_SRC
    assert "BLOCKED_KINDERGARTEN_EMA_GATE_" in BT_SRC
    assert "ema921_pass as _g_e921_pass" in BT_SRC
