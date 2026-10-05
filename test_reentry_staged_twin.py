"""Tests for vec_decisions/twin_reentry_staged.py (11 staged reentry/augment twins).

AGREEMENT STRATEGY (honest about what is proven how):
  * REENTRY_PULL1..4: PROVEN by EXECUTING the staged vec source. The harness
    below AST-extracts v12_quick_engine helpers and execs the verbatim staged
    slice v12:8266-8404 (compute_reentry_blocks through B_PULL4), then
    differentially compares per-bar staged masks vs twin-scalar results over
    randomized arrays incl. boundary values. Anchor assertions fail loudly if
    v12 shifts. v12 itself is NOT importable in this checkout (235
    vec_decisions modules exist only in the parent workspace), so slice-exec
    is the faithful alternative to import-and-call.
  * Other 7 switches: expectations HAND-DERIVED from cited live lines (live
    fns are async/process_position/heavy-import and not exec-able here).
    Boundaries pin every strict-vs-lax comparison to the cited line.
"""
import ast
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from vec_decisions import twin_reentry_staged as T

V12 = pathlib.Path(__file__).parent / "v12_quick_engine.py"
VLINES = V12.read_text().splitlines()


# ── staged-source exec harness ─────────────────────────────────────────────
def _load_staged_pulls():
    assert VLINES[8265].startswith("def compute_reentry_blocks("), "v12 slice anchor moved: 8266"
    assert "B_PULL1" in VLINES[8352], "v12 anchor moved: 8353"
    assert VLINES[8353].strip().startswith("if getattr(cfg, 'REENTRY_PULL1_ENABLED'"), "v12 anchor moved: 8354"
    assert "B_PULL2" in VLINES[8366], "v12 anchor moved: 8367"
    assert "B_PULL3" in VLINES[8379], "v12 anchor moved: 8380"
    assert "B_PULL4" in VLINES[8392], "v12 anchor moved: 8393"
    assert VLINES[8403].strip().startswith('blocks["B_PULL4"]'), "v12 anchor moved: 8404"
    assert VLINES[8404].strip() == "", "v12 slice end moved: 8405 not blank"
    ns = {"np": np, "numpy": np}
    wanted = {"_safe", "_safeb", "_ha_int", "_base_tf", "_base_safe", "_base_bool", "_base_ha"}
    mod = ast.parse(V12.read_text())
    found = set()
    for node in mod.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            found.add(node.name)
            exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), "<v12helpers>", "exec"), ns)
    assert found == wanted, f"helpers missing: {wanted - found}"
    body = "\n".join(VLINES[8265:8404]) + "\n    return dict(blocks)\n"
    exec(compile(body, "<staged_pulls>", "exec"), ns)
    return ns["compute_reentry_blocks"]


class _Cfg:
    BASE_TF = "15m"

    def __getattr__(self, name):
        if name.startswith("REENTRY_PULL"):
            return True
        return False


def _rand_arrays(rng, n):
    f = lambda lo, hi: rng.uniform(lo, hi, n)
    return {
        "close_15m": f(50, 150),
        "stoch_k_15m": f(0, 100), "stoch_d_15m": f(0, 100), "stoch_k_1h": f(0, 100),
        "wt1_15m": f(-60, 60), "wt2_15m": f(-60, 60),
        "wt1_1h": f(-60, 60), "wt2_1h": f(-60, 60),
        "wt1_4h": f(-60, 60), "wt2_4h": f(-60, 60),
        "wt1_D": f(-60, 60), "wt2_D": f(-60, 60),
        "wt_velocity_15m": f(-5, 5), "wt_velocity_1h": f(-5, 5),
        "wt_velocity_4h": f(-5, 5), "wt_velocity_D": f(-5, 5),
        "wt_bullish_15m": rng.random(n) < 0.5, "wt_bullish_1h": rng.random(n) < 0.5,
        "wt_bullish_4h": rng.random(n) < 0.5,
        "dc_high_4h": f(50, 150), "dc_low_4h": f(50, 150),
        "dc_high_1h": f(50, 150), "dc_low_1h": f(50, 150),
        "dc_high_15m": f(50, 150), "dc_low_15m": f(50, 150),
        "ha_15m": rng.choice([-1, 0, 1], n).astype(np.int8),
        "ha_1h": rng.choice([-1, 0, 1], n).astype(np.int8),
        "ha_4h": rng.choice([-1, 0, 1], n).astype(np.int8),
        "ha_D": rng.choice([-1, 0, 1], n).astype(np.int8),
        "bb_pct_b_1h": f(-0.2, 1.2), "rsi_1h": f(0, 100),
        "sma_200_1h": f(50, 150),
    }


_BOUNDARY = [20.0, -35.0, 70.0, 80.0, 35.0, 30.0, 1.0, 0.0, 35.0, 65.0,
             -15.0, 15.0, 0.15, 0.85, 0.98, 1.01, 0.99, 1.02]


def _twin_get_factory(npz, cfg_on=True):
    k = npz["stoch_k_15m"]
    kp = np.roll(k, 1); kp[0] = k[0]
    kp2 = np.roll(k, 2); kp2[:2] = k[:2]
    table = {
        "stoch_k_3m": k, "stoch_d_3m": npz["stoch_d_15m"],
        "stoch_k_3m_prev": kp, "stoch_k_3m_prev2": kp2,
        "wt1_3m": npz["wt1_15m"], "wt2_3m": npz["wt2_15m"],
        "wt_velocity_3m": npz["wt_velocity_15m"], "wt1_15m": npz["wt1_15m"],
        "stoch_k_1h": npz["stoch_k_1h"],
        "wt1_1h": npz["wt1_1h"], "wt2_1h": npz["wt2_1h"],
        "wt1_4h": npz["wt1_4h"], "wt2_4h": npz["wt2_4h"],
        "wt1_D": npz["wt1_D"], "wt2_D": npz["wt2_D"],
        "wt_velocity_4h": npz["wt_velocity_4h"], "wt_velocity_D": npz["wt_velocity_D"],
        "rsi_1h": npz["rsi_1h"], "ha_D": npz["ha_D"], "ha_4h": npz["ha_4h"],
        "sma_200_1h": npz["sma_200_1h"], "bb_pct_b_1h": npz["bb_pct_b_1h"],
        "REENTRY_PULL1_ENABLED": cfg_on, "REENTRY_PULL2_ENABLED": cfg_on,
        "REENTRY_PULL3_ENABLED": cfg_on, "REENTRY_PULL4_ENABLED": cfg_on,
    }
    return table.get


@pytest.mark.parametrize("is_long", [True, False])
def test_staged_pull_agreement_randomized(is_long):
    staged = _load_staged_pulls()
    rng = np.random.default_rng(20261004 + int(is_long))
    n = 400
    for trial in range(6):
        npz = _rand_arrays(rng, n)
        if trial % 2 == 1:
            for j, b in enumerate(_BOUNDARY):
                npz["stoch_k_15m"][j] = b
                npz["wt1_15m"][j] = b - 40.0
                npz["rsi_1h"][j] = b
        blocks = staged(npz, n, is_long, _Cfg())
        get = _twin_get_factory(npz)
        for num in (1, 2, 3, 4):
            fn = getattr(T, f"reentry_pull{num}_fires")
            if num == 2:
                got = fn(get, is_long, npz["close_15m"])
            else:
                got = fn(get, is_long)
            assert got is not None
            want = blocks[f"B_PULL{num}"]
            assert np.asarray(got, dtype=bool).tolist() == want.tolist(), f"PULL{num} long={is_long} trial={trial}"


def test_staged_pull_disabled_inert():
    staged = _load_staged_pulls()
    rng = np.random.default_rng(7)
    n = 64
    npz = _rand_arrays(rng, n)
    get = _twin_get_factory(npz, cfg_on=False)
    assert T.reentry_pull1_fires(get, True) is None
    assert T.reentry_pull2_fires(get, False, 100.0) is None
    assert T.reentry_pull3_fires(get, True) is None
    assert T.reentry_pull4_fires(get, False) is None


# ── constraint self-checks ────────────────────────────────────────────────
def test_getter_param_named_get_and_literal_keys():
    src = pathlib.Path(T.__file__).read_text()
    assert "and False" not in src and "or True" not in src
    mod = ast.parse(src)
    for node in ast.walk(mod):
        if isinstance(node, ast.FunctionDef) and node.name.endswith(("_fires", "_mult", "_qty", "_score", "_frac")):
            assert node.args.args[0].arg == "get", node.name
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "get":
                    assert sub.args and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str), f"non-literal get() key in {node.name}"
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id in ("_f", "_b", "_s"):
                    assert len(sub.args) >= 2 and isinstance(sub.args[1], ast.Constant) and isinstance(sub.args[1].value, str), f"non-literal fetch key in {node.name}"


# ── inert defaults ────────────────────────────────────────────────────────
def test_inert_defaults():
    g = {}.get
    assert T.reentry_pull1_fires(g, True) is None
    assert T.reentry_pull2_fires(g, True, 100.0) is None
    assert T.reentry_pull3_fires(g, False) is None
    assert T.reentry_pull4_fires(g, False) is None
    assert T.bb_breakout_fires(g, True, 100.0) is None
    assert T.dc_hopeless_exit_fires(g, True, 100.0, 9999.0) is None
    assert T.wt_4h_vel_exit_fires(g, True, 9999.0, 5.0) is None
    assert T.augment_fallback_reduce_frac(g) == 0.0
    assert T.breakout_leash_reentry_mult(g) == 1.5
    assert T.bb_breakout_score(g) == 20
    assert T.augment_fallback_fires(g, 1.0, 1.0) is False


# ── PULL boundaries (scalar, hand-pinned to v12:8349-8399) ────────────────
def _pull1_base():
    return {"REENTRY_PULL1_ENABLED": True, "wt1_1h": 10.0, "wt2_1h": 5.0,
            "wt1_4h": 8.0, "wt2_4h": 2.0, "wt1_D": 6.0, "wt2_D": 1.0,
            "ha_D": 1, "stoch_k_1h": 50.0, "stoch_k_3m": 10.0,
            "stoch_k_3m_prev": 8.0, "stoch_k_3m_prev2": 9.0, "wt1_3m": -40.0,
            "wt_velocity_3m": 2.0}


def test_pull1_long_fire_and_boundaries():
    assert T.reentry_pull1_fires(_pull1_base().get, True) is True
    for k, v in [("stoch_k_3m", 20.0), ("wt1_3m", -35.0), ("stoch_k_1h", 70.0),
                 ("wt_velocity_3m", 0.0), ("stoch_k_3m_prev", 10.0)]:
        d = _pull1_base(); d[k] = v
        assert T.reentry_pull1_fires(d.get, True) is False, k
    d = _pull1_base(); d["ha_D"] = "green"
    assert T.reentry_pull1_fires(d.get, True) is True
    d = _pull1_base(); d["ha_D"] = "red"
    assert T.reentry_pull1_fires(d.get, True) is False


def test_pull1_short_mirror():
    d = {"REENTRY_PULL1_ENABLED": True, "wt1_1h": -10.0, "wt2_1h": -5.0,
         "wt1_4h": -8.0, "wt2_4h": -2.0, "wt1_D": -6.0, "wt2_D": -1.0,
         "ha_D": -1, "stoch_k_1h": 50.0, "stoch_k_3m": 90.0,
         "stoch_k_3m_prev": 92.0, "stoch_k_3m_prev2": 91.0, "wt1_3m": 40.0,
         "wt_velocity_3m": -2.0}
    assert T.reentry_pull1_fires(d.get, False) is True
    d["stoch_k_1h"] = 30.0
    assert T.reentry_pull1_fires(d.get, False) is False


def test_pull2_bands_and_thresholds():
    d = {"REENTRY_PULL2_ENABLED": True, "wt_velocity_D": 2.0, "wt_velocity_4h": 1.0,
         "ha_D": 1, "ha_4h": 0, "sma_200_1h": 100.0, "stoch_k_3m": 20.0,
         "stoch_d_3m": 15.0, "wt_velocity_3m": 1.0}
    assert T.reentry_pull2_fires(d.get, True, 100.0) is True
    assert T.reentry_pull2_fires(d.get, True, 101.0) is False
    assert T.reentry_pull2_fires(d.get, True, 98.0) is False
    d["wt_velocity_D"] = 1.0
    assert T.reentry_pull2_fires(d.get, True, 100.0) is False
    d["wt_velocity_D"] = 2.0; d["stoch_k_3m"] = 35.0
    assert T.reentry_pull2_fires(d.get, True, 100.0) is False
    d["stoch_k_3m"] = 20.0; d["sma_200_1h"] = 0.0
    assert T.reentry_pull2_fires(d.get, True, 100.0) is False


def test_pull2_short_mirror():
    d = {"REENTRY_PULL2_ENABLED": True, "wt_velocity_D": -2.0, "wt_velocity_4h": -1.0,
         "ha_D": -1, "ha_4h": 0, "sma_200_1h": 100.0, "stoch_k_3m": 80.0,
         "stoch_d_3m": 85.0, "wt_velocity_3m": -1.0}
    assert T.reentry_pull2_fires(d.get, False, 100.0) is True
    assert T.reentry_pull2_fires(d.get, False, 99.0) is False
    assert T.reentry_pull2_fires(d.get, False, 102.0) is False


def test_pull3_bb_and_cross():
    d = {"REENTRY_PULL3_ENABLED": True, "wt1_1h": 5.0, "wt2_1h": 1.0,
         "wt1_4h": 4.0, "wt2_4h": 0.0, "ha_D": 1, "bb_pct_b_1h": 0.10,
         "stoch_k_3m": 25.0, "stoch_k_3m_prev": 20.0, "stoch_d_3m": 22.0}
    assert T.reentry_pull3_fires(d.get, True) is True
    d["bb_pct_b_1h"] = 0.15
    assert T.reentry_pull3_fires(d.get, True) is False
    d["bb_pct_b_1h"] = 0.10; d["stoch_k_3m"] = 30.0
    assert T.reentry_pull3_fires(d.get, True) is False
    d["stoch_k_3m"] = 25.0; d["stoch_k_3m_prev"] = 22.0
    assert T.reentry_pull3_fires(d.get, True) is True


def test_pull3_short_mirror():
    d = {"REENTRY_PULL3_ENABLED": True, "wt1_1h": -5.0, "wt2_1h": -1.0,
         "wt1_4h": -4.0, "wt2_4h": 0.0, "ha_D": -1, "bb_pct_b_1h": 0.90,
         "stoch_k_3m": 75.0, "stoch_k_3m_prev": 80.0, "stoch_d_3m": 78.0}
    assert T.reentry_pull3_fires(d.get, False) is True
    d["bb_pct_b_1h"] = 0.85
    assert T.reentry_pull3_fires(d.get, False) is False


def test_pull4_rsi_and_bounce():
    d = {"REENTRY_PULL4_ENABLED": True, "rsi_1h": 30.0, "ha_4h": 1, "ha_D": 1,
         "wt_velocity_3m": 1.0, "wt1_3m": -20.0, "wt1_15m": -50.0}
    assert T.reentry_pull4_fires(d.get, True) is True
    d["rsi_1h"] = 35.0
    assert T.reentry_pull4_fires(d.get, True) is False
    d["rsi_1h"] = 30.0; d["wt1_3m"] = -15.0
    assert T.reentry_pull4_fires(d.get, True) is False
    d["wt1_3m"] = -35.0
    assert T.reentry_pull4_fires(d.get, True) is False


def test_pull4_short_mirror():
    d = {"REENTRY_PULL4_ENABLED": True, "rsi_1h": 70.0, "ha_4h": -1, "ha_D": -1,
         "wt_velocity_3m": -1.0, "wt1_3m": 20.0, "wt1_15m": 50.0}
    assert T.reentry_pull4_fires(d.get, False) is True
    d["wt1_3m"] = 15.0
    assert T.reentry_pull4_fires(d.get, False) is False


def test_ha_forms():
    assert T.reentry_pull3_fires({"REENTRY_PULL3_ENABLED": True, "wt1_1h": 5.0, "wt2_1h": 1.0, "wt1_4h": 4.0, "wt2_4h": 0.0, "ha_D": "GREEN", "bb_pct_b_1h": 0.1, "stoch_k_3m": 25.0, "stoch_k_3m_prev": 20.0, "stoch_d_3m": 22.0}.get, True) is True
    assert T.reentry_pull3_fires({"REENTRY_PULL3_ENABLED": True, "wt1_1h": 5.0, "wt2_1h": 1.0, "wt1_4h": 4.0, "wt2_4h": 0.0, "ha_D": "neutral", "bb_pct_b_1h": 0.1, "stoch_k_3m": 25.0, "stoch_k_3m_prev": 20.0, "stoch_d_3m": 22.0}.get, True) is False


# ── BREAKOUT_LEASH (transcribed from ez_reentry.py:723,745-747) ───────────
def test_breakout_leash_mult():
    assert T.breakout_leash_reentry_mult({"BREAKOUT_LEASH_REENTRY_MULT": 2.0}.get) == 2.0
    assert T.breakout_leash_reentry_mult({}.get) == 1.5
    assert T.breakout_leash_reentry_qty({"BREAKOUT_LEASH_REENTRY_MULT": 1.5}.get, 10.0) == 15.0
    assert T.breakout_leash_reentry_qty({"BREAKOUT_LEASH_REENTRY_MULT": 0}.get, 10.0) == 0.0


# ── BB_BREAKOUT (transcribed from ez_positions_quick.py:4508-4517) ────────
def _bb_base():
    return {"BB_BREAKOUT_ENABLED": True, "BB_BREAKOUT_TF": "1h",
            "bb_pct_b_1h": 1.2, "sma_200_1h": 100.0, "adx_1h": 30.0}


def test_bb_long_fire_and_boundaries():
    assert T.bb_breakout_fires(_bb_base().get, True, 101.0) is True
    d = _bb_base(); d["adx_1h"] = 25.0
    assert T.bb_breakout_fires(d.get, True, 101.0) is False
    d = _bb_base(); d["sma_200_1h"] = 0.0
    assert T.bb_breakout_fires(d.get, True, 101.0) is False
    d = _bb_base(); d["bb_pct_b_1h"] = 1.0
    assert T.bb_breakout_fires(d.get, True, 101.0) is False
    assert T.bb_breakout_fires(_bb_base().get, True, 100.0) is False
    d = _bb_base(); d["BB_BREAKOUT_TF"] = "OFF"
    assert T.bb_breakout_fires(d.get, True, 101.0) is None


def test_bb_short_and_tf_select():
    d = {"BB_BREAKOUT_ENABLED": True, "BB_BREAKOUT_TF": "15m",
         "bb_pct_b_15m": -0.2, "sma_200_15m": 100.0, "adx_15m": 40.0}
    assert T.bb_breakout_fires(d.get, False, 99.0) is True
    assert T.bb_breakout_fires(d.get, False, 100.0) is False
    d["BB_BREAKOUT_TF"] = "4h"
    assert T.bb_breakout_fires(d.get, False, 99.0) is False
    assert T.bb_breakout_score({"BB_BREAKOUT_SCORE": "25"}.get) == 25


# ── DC_HOPELESS (transcribed from ez_manage.py:50170-50200) ───────────────
def test_dc_hopeless():
    d = {"DC_HOPELESS_EXIT_ENABLED": True, "dc_high_4h": 110.0, "dc_low_4h": 90.0}
    assert T.dc_hopeless_exit_fires(d.get, True, 111.0, 901.0) is True
    assert T.dc_hopeless_exit_fires(d.get, True, 110.0, 901.0) is False
    assert T.dc_hopeless_exit_fires(d.get, True, 111.0, 900.0) is False
    assert T.dc_hopeless_exit_fires(d.get, False, 89.0, 5000.0) is True
    assert T.dc_hopeless_exit_fires(d.get, False, 90.0, 5000.0) is False
    z = {"DC_HOPELESS_EXIT_ENABLED": True, "dc_high_4h": 0.0, "dc_low_4h": 90.0}
    assert T.dc_hopeless_exit_fires(z.get, True, 111.0, 5000.0) is False
    c = dict(d); c["DC_HOPELESS_EXIT_MIN_AGE_S"] = 60.0
    assert T.dc_hopeless_exit_fires(c.get, True, 111.0, 61.0) is True


# ── WT_4H_VEL (transcribed from ez_manage.py:50087-50142) ─────────────────
def _wt_base():
    return {"WT_4H_VEL_EXIT_ENABLED": True, "wt_velocity_4h": -3.0,
            "k_3m": 85.0, "k_15m": 50.0}


def test_wt_4h_vel_long():
    assert T.wt_4h_vel_exit_fires(_wt_base().get, True, 361.0, 0.10) is True
    d = _wt_base(); d["wt_velocity_4h"] = -2.0
    assert T.wt_4h_vel_exit_fires(d.get, True, 361.0, 0.10) is False
    assert T.wt_4h_vel_exit_fires(_wt_base().get, True, 360.0, 0.10) is False
    assert T.wt_4h_vel_exit_fires(_wt_base().get, True, 361.0, 0.09) is False
    d = _wt_base(); d["k_3m"] = 50.0; d["k_15m"] = 79.0
    assert T.wt_4h_vel_exit_fires(d.get, True, 361.0, 1.0) is False
    d["k_15m"] = 80.0
    assert T.wt_4h_vel_exit_fires(d.get, True, 361.0, 1.0) is True
    d = _wt_base(); d["WT_4H_VEL_EXIT_REQUIRE_PROFIT"] = False
    assert T.wt_4h_vel_exit_fires(d.get, True, 361.0, -5.0) is True
    d = _wt_base(); d["WT_4H_VEL_EXIT_REQUIRE_K_EXTREME"] = False
    d["k_3m"] = 50.0; d["k_15m"] = 50.0
    assert T.wt_4h_vel_exit_fires(d.get, True, 361.0, 1.0) is True


def test_wt_4h_vel_short():
    d = {"WT_4H_VEL_EXIT_ENABLED": True, "wt_velocity_4h": 3.0,
         "k_3m": 50.0, "k_15m": 15.0}
    assert T.wt_4h_vel_exit_fires(d.get, False, 361.0, 1.0) is True
    d["wt_velocity_4h"] = 2.0
    assert T.wt_4h_vel_exit_fires(d.get, False, 361.0, 1.0) is False
    d["wt_velocity_4h"] = 3.0; d["WT_4H_VEL_EXIT_SHORT_VEL_MIN"] = 4.0
    assert T.wt_4h_vel_exit_fires(d.get, False, 361.0, 1.0) is False


# ── AUGMENT_FALLBACK (semantics: config_tradier.py:3567-3569) ─────────────
def test_augment_fallback():
    assert T.augment_fallback_fires({}.get, 0.5, 1.5) is True
    assert T.augment_fallback_fires({}.get, 0.5, 1.49) is False
    assert T.augment_fallback_fires({"AUGMENT_FALLBACK_GAIN_PCT": 2.0}.get, 0.0, 2.0) is True
    assert T.augment_fallback_fires({"AUGMENT_FALLBACK_GAIN_PCT": 2.0}.get, 0.01, 2.0) is False
    assert T.augment_fallback_reduce_frac({}.get) == 0.0
    assert T.augment_fallback_reduce_frac({"AUGMENT_FALLBACK_REDUCE_ENABLED": True}.get) == 0.5
    assert T.augment_fallback_reduce_frac({"AUGMENT_FALLBACK_REDUCE_ENABLED": "true", "AUGMENT_FALLBACK_REDUCE_PCT": 0.25}.get) == 0.25
    assert T.augment_fallback_reduce_frac({"AUGMENT_FALLBACK_REDUCE_ENABLED": False, "AUGMENT_FALLBACK_REDUCE_PCT": 0.9}.get) == 0.0


def test_plain_dict_get_compatible():
    ind = dict(_pull1_base())
    assert T.reentry_pull1_fires(ind.get, True) is True
    assert T.bb_breakout_fires(dict(_bb_base()).get, True, 101.0) is True
    assert math.isclose(T.breakout_leash_reentry_qty({}.get, 4.0), 6.0)
