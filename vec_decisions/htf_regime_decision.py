"""
vec_decisions/htf_regime_decision.py — TANDEM glue: ONE decision path for backtest_v8_engine, the live
scripts (ez_manage/tradier_manage/ez_positions_quick), and per_sym. USER 2026-05-30 "everything runs in tandem".

Reads the per_sym tandem config (data/_diagnostic/htf_regime_persym.json, keyed "SYMBOL_SIDE") that holds each
tradeable key's winning variant cfg. LONG keys use the staircase core (htf_regime_scale); SHORT keys use the
elevator core (short_elevator). Both are parity-proven scalar==vec, so:
  - BACKTEST (engine + harness) calls `simulate_key_vec()` — the vectorized path.
  - LIVE (per tick) calls `target_weight_scalar()` — the per-bar path (identical logic).
A key trades under this strategy ONLY if it is in the config (i.e. passed the gate) AND config.HTF_REGIME_ENABLED.
"""
from __future__ import annotations
import os, json
import numpy as np
from vec_decisions import htf_regime_scale as H
from vec_decisions import short_elevator as SE

_CACHE = {}


def load_persym_config(path):
    """Load + cache the tandem per_sym config keyed by 'SYMBOL_SIDE'."""
    mt = os.path.getmtime(path) if os.path.exists(path) else 0
    if path not in _CACHE or _CACHE[path][0] != mt:
        cfg = json.load(open(path)) if os.path.exists(path) else {}
        _CACHE[path] = (mt, cfg)
    return _CACHE[path][1]


def key_cfg(cfg_map, symbol, side):
    """Return the per-key cfg dict (or None if the key is not tradeable under this strategy)."""
    return cfg_map.get(f"{symbol}_{side}")


def _ladder_for(mode):
    return ("1h", "4h", "D") if mode == "tradier" else ("15m", "1h", "4h")


def _rollstd(close, win):
    n = len(close); ret = np.zeros(n); ret[1:] = np.diff(close) / close[:-1]
    c1 = np.concatenate([[0.0], np.cumsum(ret)]); c2 = np.concatenate([[0.0], np.cumsum(ret * ret)])
    i = np.arange(n); a = np.maximum(0, i - win + 1); cnt = (i - a + 1).astype(float)
    s1 = c1[i + 1] - c1[a]; s2 = c2[i + 1] - c2[a]
    return np.sqrt(np.maximum(s2 / cnt - (s1 / cnt) ** 2, 0.0))


def desired_weight_array(nd, side, cfg, mode):
    """BACKTEST path: per-bar desired weight array for a key (>=0). LONG=staircase, SHORT=elevator.
    Same cores the live tick path uses. `nd` is the loaded NPZ dict. Returns weight aligned to bars."""
    c = cfg["cfg"]
    if side == "LONG":
        rv = _rollstd(np.asarray(nd["close"], float), 96) if c.get("vol_target", 0) > 0 else None
        return H.desired_weight_vec(nd, True, regime_tf=c["regime_tf"], exit_tf=c["exit_tf"],
                                    add_mult=c["add_mult"], size_cap=c["size_cap"], vol_target=c["vol_target"],
                                    scale_in=c["scale_in"], ladder_tfs=_ladder_for(mode), realized_vol=rv)
    return SE.short_weight_vec(nd, regime_tf=c["regime_tf"], fast_tf=c.get("fast_tf", "15m"),
                               vol_k=c["vol_k"], rsi_cover=c["rsi_cover"], size_cap=c["size_cap"])


def simulate_key_vec(nd, side, cfg, mode, round_trip_cost):
    """Shared BACKTEST simulation for one key. Returns dict(trade_returns, gain, weight) NET of round-trip cost.
    backtest_v8_engine calls this when HTF_REGIME_ENABLED (so engine == harness, byte-for-byte same cores)."""
    close = np.asarray(nd["close"], float)
    w = desired_weight_array(nd, side, cfg, mode)
    direction = 1 if side == "LONG" else -1
    cm = round_trip_cost
    n = len(close); ret = np.zeros(n); ret[1:] = np.diff(close) / close[:-1]
    dw = np.abs(np.diff(w, prepend=w[0]))
    f = np.clip((1.0 + w * direction * ret) * (1.0 - cm * dw), 1e-9, None)
    logf = np.log(f); cum = np.concatenate([[0.0], np.cumsum(logf)])
    active = w > 0; d = np.diff(active.astype(int))
    starts = np.where(d == 1)[0] + 1; ends = np.where(d == -1)[0] + 1
    if active[0]:
        starts = np.r_[0, starts]
    if active[-1]:
        ends = np.r_[ends, n]
    trades = [float(np.exp(cum[e] - cum[s]) - 1.0) for s, e in zip(starts, ends) if e > s]
    return dict(trade_returns=trades, gain=float(np.exp(logf.sum()) - 1.0), weight=w)


def target_weight_scalar(side, cfg, snapshot, state):
    """LIVE per-tick path: desired weight (>=0) for THIS bar. `snapshot` holds PRIOR-bar values the core needs;
    `state` persists across ticks ({'short': bool} for elevator). Parity-identical to desired_weight_array."""
    c = cfg["cfg"]
    if side == "LONG":
        return H.desired_weight_scalar(snapshot["cp"], snapshot["smas"], snapshot["smas_lag"],
                                       snapshot.get("rv"), True, regime_tf=c["regime_tf"], exit_tf=c["exit_tf"],
                                       add_mult=c["add_mult"], size_cap=c["size_cap"], vol_target=c["vol_target"],
                                       ladder_tfs=tuple(snapshot.get("ladder", ("15m", "1h", "4h"))),
                                       scale_in=c["scale_in"])
    return SE.short_weight_scalar(state, snapshot, regime_tf=c["regime_tf"], fast_tf=c.get("fast_tf", "15m"),
                                  vol_k=c["vol_k"], rsi_cover=c["rsi_cover"], size_cap=c["size_cap"])


def weight_to_action(current_qty, target_qty, min_lot=0.0, tol=1e-9):
    """Translate a (current -> target) position size into a live order action. Used by execute_now caller.
    target_qty is the strategy's desired exposure (already side-signed magnitude). Never averages a loser down
    on its own — that is enforced by the cores (weight only grows on continuation), not here."""
    delta = target_qty - current_qty
    if target_qty <= tol and current_qty > tol:
        return ("CLOSE", current_qty)
    if abs(delta) <= max(min_lot, tol):
        return ("HOLD", 0.0)
    if current_qty <= tol and target_qty > tol:
        return ("OPEN", target_qty)
    if delta > 0:
        return ("AUGMENT", delta)
    return ("REDUCE", -delta)
