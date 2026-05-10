"""Vectorized-engine-validated entry gates for live wiring.

These boolean gates were validated on the publishable 56-sym × 1.17-yr crypto
universe via vec_trender_breakout.py + metrics_guard. Canonical CSV at
data/sweep_results/vec_trender_breakout_LONG_SHORT_PUBLISHABLE_v2_20260509T2345.csv

Live wiring intent (per user 2026-05-09):
    fin account: MR5_L (LONG) + MR3S_S (SHORT) — mean-reversion both sides
    men account: MOM5+TRENDER_l75 (LONG) + MOM4S_S (SHORT) — momentum both sides

Strategies fire on different bar conditions and never overlap (mean-rev requires
depressed dc_position_15m, momentum requires alignment) — both can run live in
parallel per account or split across accounts as user wires them.

VALIDATED PUBLISHABLE NUMBERS (56 syms × 1.17 yr, h in BASE_TF bars=3min):
    MR5_L     h=16  Sharpe 0.95  WR 94.9%  mean 1.35%  n=10712
    MR3S_S    h=16  Sharpe 1.05  WR 94.8%  mean 1.17%  n=11124
    MOM5+TRENDER_l75 LONG h=32  Sharpe 0.62  WR 74.1%  mean 4.21%  n=382
    MOM4S_S   h=16  Sharpe 0.78  WR 84.7%  mean 0.78%  n=25286

API:
    check_vec_gate(account, symbol, side, ind_data, klines_15m=None) -> (fires: bool, reason: str)
        ind_data: dict from latest_market_data.json (per-sym indicators)
        klines_15m: optional list of dicts with timestamp/close/volume; needed only for TRENDER
        Returns (True, "GATE_NAME") if any enabled gate matching (account, side) fires; else (False, reason).
"""
from __future__ import annotations
from typing import Tuple, Optional, List, Dict, Any
import math


def _f(d: dict, k: str, default=None):
    """Safe float fetch from indicators dict."""
    v = d.get(k, default)
    if v is None: return default
    try: return float(v)
    except (TypeError, ValueError): return default


def _b(d: dict, k: str, default=False):
    """Safe bool fetch from indicators dict — handles 0/1/'true'/'false'/native bool."""
    v = d.get(k)
    if v is None: return default
    if isinstance(v, bool): return v
    if isinstance(v, (int, float)): return v > 0
    if isinstance(v, str): return v.lower() in ("true", "1", "yes", "y")
    return default


def _wt_bullish(d: dict, tf: str) -> bool:
    """Live equivalent of NPZ field wt_bullish_<tf> = (wt1 > wt2). Defined in
    backtest_v8_precompute.py:344. latest_market_data.json doesn't store the boolean
    so we re-derive from wt1_<tf> and wt2_<tf>."""
    w1 = _f(d, f"wt1_{tf}")
    w2 = _f(d, f"wt2_{tf}")
    if w1 is None or w2 is None: return False
    return w1 > w2


# ── BOOLEAN GATE EVALUATORS ─────────────────────────────────────────────────────
# Mirror the conditions used by vec_mass_scan.build_crypto_conditions exactly.

def _mr5_long(ind: dict) -> bool:
    """MR5: dc_basis_crossover_1h AND dc_position_15m < 0.30 AND mfi_15m < 40 AND wt_bullish_1h."""
    return (
        _b(ind, "dc_basis_crossover_1h") and
        (_f(ind, "dc_position_15m", 0.5) < 0.30) and
        (_f(ind, "mfi_15m", 50) < 40) and
        _wt_bullish(ind, "1h")
    )


def _mr3s_short(ind: dict) -> bool:
    """MR3S: dc_basis_crossunder_1h AND dc_position_15m > 0.70 AND NOT wt_bullish_1h AND NOT wt_bullish_D."""
    return (
        _b(ind, "dc_basis_crossunder_1h") and
        (_f(ind, "dc_position_15m", 0.5) > 0.70) and
        (not _wt_bullish(ind, "1h")) and
        (not _wt_bullish(ind, "D"))
    )


def _mom5_long_baseline(ind: dict) -> bool:
    """MOM5: dc_basis_crossover_4h AND wt_all3 (1h&4h&D) AND price > sma_200_1h."""
    sma1h = _f(ind, "sma_200_1h", 0)
    px = _f(ind, "current_price", 0)
    return (
        _b(ind, "dc_basis_crossover_4h") and
        _wt_bullish(ind, "1h") and _wt_bullish(ind, "4h") and _wt_bullish(ind, "D") and
        (sma1h > 0 and px > 0 and px > sma1h)
    )


def _mom4s_short(ind: dict) -> bool:
    """MOM4S: dc_basis_crossunder_1h AND mfi_15m > 60 AND price < sma_200_1h AND NOT wt_bullish_1h."""
    sma1h = _f(ind, "sma_200_1h", 0)
    px = _f(ind, "current_price", 0)
    return (
        _b(ind, "dc_basis_crossunder_1h") and
        (_f(ind, "mfi_15m", 50) > 60) and
        (sma1h > 0 and px > 0 and px < sma1h) and
        (not _wt_bullish(ind, "1h"))
    )


def _trender_l75(klines_15m: Optional[List[Dict[str, Any]]], ind: dict) -> bool:
    """TRENDER_l75r40q50d30: lin >= 0.75 AND ret_24h >= 4% AND ret_4h >= 0 AND ret_1h >= 0
    AND qv_24h_usd >= $50M AND dd_long_24h_ratio <= 0.30.
    Requires 96+ 15m klines; returns False if klines unavailable.
    """
    if not klines_15m or len(klines_15m) < 96: return False
    try:
        closes = [float(k["close"]) for k in klines_15m[-96:]]
        vols = [float(k.get("volume", 0)) for k in klines_15m[-96:]]
    except (KeyError, ValueError, TypeError):
        return False
    last = closes[-1]
    if last <= 0: return False
    # ret_1h: 4 bars back; ret_4h: 16 bars back; ret_24h: 96 bars back
    if closes[-5] <= 0 or closes[-17] <= 0 or closes[0] <= 0: return False
    ret_1h = last / closes[-5] - 1.0
    ret_4h = last / closes[-17] - 1.0
    ret_24h = last / closes[0] - 1.0
    if ret_24h < 0.04 or ret_4h < 0 or ret_1h < 0: return False
    # qv_24h_usd ≈ sum(volume × close) over 96 15m bars
    qv = sum(v * c for v, c in zip(vols, closes))
    if qv < 50_000_000: return False
    # dd_long_24h
    peak = max(closes)
    dd = (peak - last) / peak if peak > 0 else 0
    dd_ratio = dd / max(ret_24h, 0.01)
    if dd_ratio > 0.30: return False
    # Linearity proxy: |Pearson r| of close vs index over 96 bars
    n = 96
    x_mean = (n - 1) / 2.0
    x_var = sum((i - x_mean) ** 2 for i in range(n))
    y_mean = sum(closes) / n
    num = sum((closes[i] - y_mean) * (i - x_mean) for i in range(n))
    den = math.sqrt(sum((c - y_mean) ** 2 for c in closes) * x_var)
    lin = abs(num / den) if den > 0 else 0.0
    return lin >= 0.75


# ── PUBLIC API ─────────────────────────────────────────────────────────────────

def check_vec_gate(
    account: str, symbol: str, side: str,
    ind_data: dict, klines_15m: Optional[List[Dict[str, Any]]] = None,
    config_obj: Any = None,
) -> Tuple[bool, str]:
    """Returns (fires, reason) for vec-engine-validated entry gates.

    side: "LONG" or "SHORT"
    Returns (True, "MR5_L_FIRED") if any enabled (account, side) gate fires.
    Returns (False, "no_gate_enabled") if no gate is enabled for this (account, side).
    Returns (False, "no_gate_fired:<accounts_checked>") if gates enabled but none fire.
    """
    if config_obj is None:
        try:
            from config import Config
            config_obj = Config()
        except Exception:
            return (False, "config_unavailable")
    cfg = config_obj
    # Gate registry: each entry is (config_enabled_attr, config_accounts_attr, side, evaluator, name)
    gates = [
        ("MR5_L_GATE_ENABLED",          "MR5_L_ACCOUNTS",           "LONG",  lambda ind, k: _mr5_long(ind),                          "MR5_L"),
        ("MR3S_S_GATE_ENABLED",         "MR3S_S_ACCOUNTS",          "SHORT", lambda ind, k: _mr3s_short(ind),                        "MR3S_S"),
        ("MOM5_TRENDER_L_GATE_ENABLED", "MOM5_TRENDER_L_ACCOUNTS",  "LONG",  lambda ind, k: _mom5_long_baseline(ind) and _trender_l75(k, ind), "MOM5_TRENDER_L"),
        ("MOM4S_S_GATE_ENABLED",        "MOM4S_S_ACCOUNTS",         "SHORT", lambda ind, k: _mom4s_short(ind),                       "MOM4S_S"),
    ]
    checked = []
    fired = []
    for enabled_attr, accounts_attr, gate_side, evaluator, name in gates:
        if gate_side != side: continue
        if not getattr(cfg, enabled_attr, False): continue
        accounts = getattr(cfg, accounts_attr, [])
        if account not in accounts: continue
        checked.append(name)
        try:
            if evaluator(ind_data, klines_15m):
                fired.append(name)
        except Exception as e:
            # Defensive: a bad indicator dict shouldn't kill the entry pipeline
            return (False, f"gate_error:{name}:{e.__class__.__name__}")
    if not checked:
        return (True, "no_gate_enabled")  # Default-allow when no gates enabled (back-compat)
    if fired:
        return (True, f"GATE_FIRED:{'+'.join(fired)}")
    return (False, f"no_gate_fired:checked={','.join(checked)}")


def shadow_log_evaluation(
    account: str, symbol: str, side: str,
    ind_data: dict, klines_15m: Optional[List[Dict[str, Any]]] = None,
    config_obj: Any = None,
) -> Dict[str, bool]:
    """Evaluate ALL gates regardless of enabled state — returns per-gate fire/no-fire dict.
    Use for shadow-log observability without affecting live entry decisions.
    """
    out = {}
    try:
        out["MR5_L"] = _mr5_long(ind_data) if side == "LONG" else False
        out["MR3S_S"] = _mr3s_short(ind_data) if side == "SHORT" else False
        out["MOM5_baseline_L"] = _mom5_long_baseline(ind_data) if side == "LONG" else False
        out["MOM5_TRENDER_L"] = (out["MOM5_baseline_L"] and _trender_l75(klines_15m, ind_data)) if side == "LONG" else False
        out["MOM4S_S"] = _mom4s_short(ind_data) if side == "SHORT" else False
    except Exception:
        pass
    return out
