"""Wire-push contract + gap firing tests (2026-10-05, market-open parity guard).

A. CONTRACT: every switch merged by the wire push (all hook_spec_*.json) must
   still be read in its target file — directly (literal in target) or
   twin-mediated (literal in a vec_decisions twin + target references that
   twin). Catches silent unwiring/fleet overwrites before market open.
B. CONFIG: P0 config.py fields pinned with parity values.
C. FIRING: twin functions that had zero test coverage get minimal fire/no-fire
   assertions (default-inert + flipped behavior).
"""
import glob
import json
import pathlib
import re

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).parent
SRC = {}


def src(f):
    if f not in SRC:
        SRC[f] = (ROOT / f).read_text()
    return SRC[f]


def twin_with_literal(n):
    for tf in glob.glob(str(ROOT / "vec_decisions" / "twin_*.py")):
        if n in open(tf).read():
            return pathlib.Path(tf).stem
    return None


def spec_hooks():
    out = []
    for sf in sorted(glob.glob(str(ROOT / "hook_spec_*.json"))):
        s = json.loads(open(sf).read())
        items = s if isinstance(s, list) else s.get("hooks", s.get("entries", []))
        for x in items:
            if not isinstance(x, dict):
                continue
            if x.get("needs_decision") or x.get("decision") or x.get("decision_required"):
                continue
            f = x.get("file") or x.get("target")
            sw = x.get("switches") or x.get("switch") or x.get("name")
            if not f or not sw:
                continue
            if isinstance(f, list):
                f = f[0]
            names = sw if isinstance(sw, list) else [sw]
            for n in names:
                if isinstance(n, str) and not n.startswith("_") and n != "IMPORT":
                    out.append((n, f, pathlib.Path(sf).name))
    return out


HOOKS = spec_hooks()
assert len(HOOKS) > 100, f"spec harvest too small: {len(HOOKS)}"


@pytest.mark.parametrize("switch,target,spec", HOOKS)
def test_hook_present(switch, target, spec):
    body = src(target)
    if switch in body:
        return
    twin = twin_with_literal(switch)
    assert twin is not None, f"{switch} ({spec}): in neither {target} nor any twin"
    assert twin in body, f"{switch} ({spec}): twin-mediated but {target} never references {twin}"


BATCH_PINS = {
    "TECHNICAL_DC_STOP_TF": ["vec_decisions/dc_channel_exits.py"],
    "TECHNICAL_DC_TARGET_TF": ["vec_decisions/dc_channel_exits.py"],
    "TECHNICAL_DC_STOP_BUFFER_PCT": ["vec_decisions/dc_channel_exits.py"],
    "TECHNICAL_DC_TARGET_BUFFER_PCT": ["vec_decisions/dc_channel_exits.py"],
    "NOLOSS_ENABLED": ["vec_decisions/noloss_hold.py"],
    "STOP_LOSS_ENABLED": ["vec_decisions/noloss_hold.py"],
    "STOP_LOSS_PCT": ["vec_decisions/noloss_hold.py"],
    "DC_RECOVERY_EXIT_ENABLED": ["ez_manage.py", "vec_decisions/noloss_hold.py"],
    "NOLOSS_BYPASS_WT_5OF5_ENABLED": ["tradier_manage.py", "vec_decisions/noloss_hold.py"],
    "NOLOSS_BYPASS_WT_5OF5_MIN_TFS": ["tradier_manage.py", "vec_decisions/noloss_hold.py"],
    "BB_EXIT_AT_LOSS_TF": ["vec_decisions/bb_stoch_exits.py"],
    "BB_PROFIT_TAKE_TF": ["vec_decisions/bb_stoch_exits.py"],
    "STOCH_CROSS_3M_EXIT_ENABLED": ["vec_decisions/bb_stoch_exits.py"],
}


@pytest.mark.parametrize("switch,files", sorted(BATCH_PINS.items()))
def test_batch_pins(switch, files):
    for f in files:
        assert switch in src(f), f"batch switch {switch} missing from {f}"


CONFIG_PINS = {
    "WT_DC_ENABLED": "True",
    "WT_DC_DC_POS_MIN": "0.20",
    "WT_DC_FINAL_SCORE_MAX": "0.40",
    "WT_DC_K5M_HARD_ENABLED": "False",
    "WT_DC_K5M_MIN_SHORT_HARD": "20.0",
}


@pytest.mark.parametrize("key,val", sorted(CONFIG_PINS.items()))
def test_config_pins(key, val):
    m = re.search(rf"^    {key}:[^=]+=\s*(\S+)", src("config.py"), re.M)
    assert m, f"{key} missing from config.py"
    assert m.group(1).rstrip(",") == val, f"{key}={m.group(1)} != {val}"


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def G(d):
    return lambda k, default=None: d.get(k, default)


# ── exits_dead gaps ───────────────────────────────────────────────────
from vec_decisions import twin_exits_dead as TED


def test_bbkc_entry_tf():
    assert TED.bbkc_entry_tf(G({})) == ""
    assert TED.bbkc_entry_tf(G({"BBKC_ENTRY_ENABLED": True})) == "1h"
    assert TED.bbkc_entry_tf(G({"BBKC_ENTRY_ENABLED": True, "BBKC_ENTRY_TF": "4h"})) == "4h"


def test_bbkc_exit_tf():
    assert TED.bbkc_exit_tf(G({})) == ""
    assert TED.bbkc_exit_tf(G({"BBKC_EXIT_ENABLED": True, "BBKC_EXIT_TF": "15m"})) == "15m"


def test_bbkc_vec_bands():
    n = 8
    npz = {"ema_20_1h": np.full(n, 100.0), "atr_1h": np.full(n, 2.0), "bb_upper_1h": np.full(n, 104.0), "bb_lower_1h": np.full(n, 96.0)}
    ku, km, kl, bu, bl, ok = TED.bbkc_vec_bands(npz, n, "1h", lambda z, k, m, d: np.asarray(z.get(k, [d] * m)[:m], dtype=float))
    assert ok.all() and abs(km[0] - 100.0) < 1e-9 and abs(ku[0] - 103.0) < 1e-9 and abs(kl[0] - 97.0) < 1e-9


def test_wick_entry_exit_tf():
    assert TED.wick_entry_tf(G({})) == ""
    assert TED.wick_entry_tf(G({"WICK_REJECT_ENTRY_ENABLED": True})) == "1h"
    assert TED.wick_exit_tf(G({})) == ""
    assert TED.wick_exit_tf(G({"WICK_REJECT_EXIT_ENABLED": True, "WICK_REJECT_EXIT_TF": "D"})) == "D"


def test_wick_frac_vec_scalar_agree():
    h = np.array([10.0, 9.0])
    l = np.array([8.0, 8.5])
    o = np.array([9.5, 8.7])
    c = np.array([8.5, 8.8])
    up, lo = TED.wick_frac_vec(h, l, o, c)
    for i in range(2):
        su, sl = TED.wick_frac_scalar(float(h[i]), float(l[i]), float(o[i]), float(c[i]))
        assert abs(up[i] - su) < 1e-9 and abs(lo[i] - sl) < 1e-9
    assert TED.wick_frac_scalar(9.0, 9.0, 9.0, 9.0) == (0.0, 0.0)


def test_mu_symbols():
    assert TED.mu_symbols(G({})) == {"MU"}
    assert TED.mu_symbols(G({"MU_CORRECTION_SYMBOLS": "mu, aapl"})) == {"MU", "AAPL"}


# ── vec_special gaps ──────────────────────────────────────────────────
from vec_decisions import twin_vec_special as TVS


def test_dc_breakout_tf():
    assert TVS.dc_breakout_tf(Cfg()) == "1h"
    assert TVS.dc_breakout_tf(Cfg(DC_BREAKOUT_TF="4h")) == "4h"
    assert TVS.dc_breakout_tf(Cfg(DC_BREAKOUT_TF="4h", DC_BREAKOUT_TF_EXPANDED="D")) == "D"


def test_dc_breakout_entry_mask():
    n = 6
    base = {"dc_high_1h": np.full(n, 100.0), "dc_low_1h": np.full(n, 90.0), "adx_1h": np.full(n, 30.0)}
    assert TVS.dc_breakout_entry_mask(base, n, True, Cfg()) is None
    m = TVS.dc_breakout_entry_mask(base, n, True, Cfg(DC_BREAKOUT_ENTRY_ENABLED=True), close=np.full(n, 101.0))
    assert m.all()
    m2 = TVS.dc_breakout_entry_mask(base, n, True, Cfg(DC_BREAKOUT_ENTRY_ENABLED=True), close=np.full(n, 95.0))
    assert not m2.any()


def test_delta_pyramid_mask_vec():
    assert TVS.delta_pyramid_mask_vec(4, True, Cfg(DELTA_ENGINE_ENABLED=False), cur_px=1.0, last_px=1.0) is None
    assert TVS.delta_pyramid_mask_vec(4, True, Cfg(DELTA_ENGINE_ENABLED=True)) is None
    m = TVS.delta_pyramid_mask_vec(4, True, Cfg(DELTA_ENGINE_ENABLED=True), cur_px=100.0, last_px=99.0)
    assert m is not None and bool(m) is True
    m2 = TVS.delta_pyramid_mask_vec(4, True, Cfg(DELTA_ENGINE_ENABLED=True), cur_px=110.0, last_px=100.0)
    assert bool(m2) is False


def test_delta_pyramid_adds_allowed():
    assert TVS.delta_pyramid_adds_allowed(Cfg(), 0) is True
    assert TVS.delta_pyramid_adds_allowed(Cfg(), 7) is True
    assert TVS.delta_pyramid_adds_allowed(Cfg(), 8) is False
    assert TVS.delta_pyramid_adds_allowed(Cfg(DELTA_PYRAMID_MAX=2), 2) is False
    assert TVS.delta_pyramid_adds_allowed(Cfg(DELTA_PYRAMID_MAX=2), 1) is True


def test_r3_htf_flip_mask():
    n = 5
    npz = {"close": np.full(n, 100.0), "dc_basis_D": np.full(n, 100.0)}
    assert TVS.r3_htf_flip_mask(npz, n, True, Cfg()) is None
    m = TVS.r3_htf_flip_mask(npz, n, True, Cfg(R3_HTF_FLIP_EXIT_ENABLED=True))
    assert m is None or len(m) == n


def test_htf_wt_churn_mask():
    n = 5
    npz = {"wt1_1h": np.full(n, 1.0), "wt2_1h": np.full(n, 0.0), "wt1_15m": np.full(n, 0.0), "wt2_15m": np.full(n, 0.0), "wt1_4h": np.full(n, 0.0), "wt2_4h": np.full(n, 0.0)}
    assert TVS.htf_wt_churn_mask(npz, n, True, Cfg(HTF_WT_CHURN_REENTRY_ENABLED=False)) is None
    m = TVS.htf_wt_churn_mask(npz, n, True, Cfg(HTF_WT_CHURN_REENTRY_ENABLED=True))
    assert m is not None and m.all()


def test_rsi_t55_key_series_gate():
    assert TVS.rsi_t55_key(Cfg()) == "rsi_10_D"
    assert TVS.rsi_t55_key(Cfg(RSI_ENTRY_PERIOD_TRADIER=14)) == "rsi_14_D"
    n = 4
    vals, present = TVS.rsi_t55_series({"rsi_D": np.full(n, 30.0)}, n, Cfg())
    assert present and (vals == 30.0).all()
    g = TVS.rsi_entry_gate_mask({"rsi_D": np.full(n, 30.0)}, n, True, Cfg())
    assert g.all()
    g2 = TVS.rsi_entry_gate_mask({"rsi_D": np.full(n, 30.0)}, n, False, Cfg())
    assert not g2.any()
    assert TVS.rsi_entry_gate_mask({}, n, True, Cfg()).all()


# ── p0_crypto_a gaps ──────────────────────────────────────────────────
from vec_decisions import twin_p0_crypto_a as TP0A


def test_validated_gates_apply():
    assert TP0A.validated_gates_apply(Cfg()) is False
    assert TP0A.validated_gates_apply(Cfg(BACKTEST_VALIDATED_GATES_TRADIER=True)) is False
    assert TP0A.validated_gates_apply(Cfg(BACKTEST_VALIDATED_GATES_TRADIER=True, MODE="tradier")) is True


def test_vec_delta_gate_allows():
    for which in ("open", "reentry", "augment"):
        assert TP0A.vec_delta_gate_allows(Cfg(), which) is True
    d = {"VEC_HONOR_DEAD_LIVE_DELTA_GATES": True, "DELTA_GATE_OPEN": False}
    assert TP0A.vec_delta_gate_allows(Cfg(**d), "open") is False
    assert TP0A.vec_delta_gate_allows(Cfg(**d), "reentry") is True


# ── yellow_filters helpers ────────────────────────────────────────────
from vec_decisions import twin_yellow_filters as TYF


def test_normalize_tf_blanket():
    assert TYF.normalize_tf(" 15m ") == "15m"
    assert TYF.normalize_tf(None) == ""
    assert TYF.blanket_scan_tfs("4h") == ("4h",)
    assert TYF.blanket_scan_tfs("OFF") == ("15m", "1h", "4h", "D")
