"""twin_entries_dead_a.py — SHARED vec+live predicates for 12 ENTRY/EXIT switches.

Family entries-dead-A (2026-10-04 wiring mandate). ALL 12 were DEAD/DEAD:
  FUNDING_CROWD_ENTRY_ENABLED / FUNDING_CROWD_ENTRY_Z  (ENTRY_CONFIRMATION_GATES)
  FUNDING_CROWD_EXIT_ENABLED  / FUNDING_CROWD_EXIT_Z   (EXIT_VELOCITY)
  OI_SURGE_ENTRY_ENABLED / OI_SURGE_ENTRY_PCT          (ENTRY_CONFIRMATION_GATES)
  OI_SURGE_EXIT_ENABLED  / OI_SURGE_EXIT_PCT           (EXIT_VELOCITY)
  RSI2_XTREME_ENTRY_ENABLED / RSI2_XTREME_ENTRY_TF     (ENTRY_CONFIRMATION_GATES)
  RSI2_XTREME_EXIT_ENABLED  / RSI2_XTREME_EXIT_TF      (EXIT_VELOCITY)

Semantics (contrarian crowd fade; precedents cited):
  FUNDING long entry:  z <= -Z (crowded short -> squeeze up). Precedent: live
    FUNDING_GATE rejects NEW LONG when funding stretched positive
    (ez_positions_quick OI/funding gate) — entries fade the crowd.
  FUNDING short entry: z >= +Z. FUNDING long exit: z >= +Z (euphoria exit).
    FUNDING short exit: z <= -Z.
  OI_SURGE entry/exit: oi_change_1h_pct >= PCT, both sides (conviction flow).
  RSI2 entry: LONG rsi2 <= 10, SHORT rsi2 >= 90. Precedent: tradier live
    RSI2_ENTRY_THRESHOLD=10.0 (tradier_manage RSI2_ENTRY block).
  RSI2 exit: LONG rsi2 >= 90 (exit into strength), SHORT rsi2 <= 10.
  RSI2 thresholds 10/90 are fixed — no threshold switch exists in TEMPLATE.
  FUNDING z window is 21 distinct 8h observations (~7d); fixed, documented.

Data keys (REAL keys only; missing -> honest non-binding, never fabricated):
  funding vec:  funding_rate (crypto NPZ alias; fallback funding_rate_15m/5m/3m).
  funding live: ind funding_rate (+ same fallbacks) or market.data; history =
    data/funding_cache/{SYM}.json (the SAME cache backtest_v8_precompute reads).
  oi vec:  oi_change_1h_pct (crypto NPZ alias; fallback oi_change_1h_15m/5m/3m).
  oi live: ind/market.data oi_change_1h_pct (ez_market_data.open_interest_loop).
  rsi2: probe rsi_2_{tf}, rsi2_{tf}, rsi_2_npz_{tf} in order, vec and live.
    Binds: crypto 1h (rsi_2_1h), crypto 15m (rsi2_15m), stocks 1h/4h
    (rsi_2_1h/rsi_2_4h). TF=D and crypto-4h/stocks-15m have NO NPZ key ->
    honest non-binding (mask all-False / scalar False).
  Stocks have zero-filled funding/OI NPZ keys, so funding+OI are venue-gated
    (cfg.MODE == 'tradier' / is_crypto=False -> skip), NOT key-gated.

Known live/vec deltas (measured, not assumed): live funding z needs the
funding_cache file (absent -> live inert while vec binds); live crypto 15m
RSI2 needs rsi_2_npz_15m in the snapshot (absent -> live inert while vec
binds via rsi2_15m); vec strips the pre-cache leading-zero run, live cache
has no such run — first ~7d of a fresh cache may disagree.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import numpy as np

RSI2_OVERSOLD = 10.0
RSI2_OVERBOUGHT = 90.0
FUNDING_Z_WINDOW = 21
FUNDING_Z_MIN_OBS = 5
FUNDING_STRIDE_BARS = 32

SWITCHES = (
    "FUNDING_CROWD_ENTRY_ENABLED", "FUNDING_CROWD_ENTRY_Z",
    "FUNDING_CROWD_EXIT_ENABLED", "FUNDING_CROWD_EXIT_Z",
    "OI_SURGE_ENTRY_ENABLED", "OI_SURGE_ENTRY_PCT",
    "OI_SURGE_EXIT_ENABLED", "OI_SURGE_EXIT_PCT",
    "RSI2_XTREME_ENTRY_ENABLED", "RSI2_XTREME_ENTRY_TF",
    "RSI2_XTREME_EXIT_ENABLED", "RSI2_XTREME_EXIT_TF",
)

_FUNDING_KEYS = ("funding_rate", "funding_rate_15m", "funding_rate_5m", "funding_rate_3m")
_OI_KEYS = ("oi_change_1h_pct", "oi_change_1h_15m", "oi_change_1h_5m", "oi_change_1h_3m")


def _cfg_get(cfg, key, default):
    try:
        return getattr(cfg, key, default)
    except Exception:
        return default


def _g(get, key, default):
    try:
        return get(key, default)
    except Exception:
        return default


def _num(v, default):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _is_tradier(cfg):
    try:
        return str(getattr(cfg, "MODE", "crypto") or "crypto").lower() == "tradier"
    except Exception:
        return False


def _as_n(a, n, default=0.0):
    v = np.asarray(a, dtype=float).ravel()
    if v.size >= n:
        return v[:n]
    out = np.full(n, default, dtype=float)
    out[:v.size] = v
    return out


def _present_key(mapping, keys):
    try:
        for k in keys:
            if k in mapping:
                return k
    except Exception:
        pass
    return None


def funding_z_last(values):
    """Pure z of the last observation vs trailing window. None if undefined.

    Consecutive-equal values are DISTINCT prints (flat funding for days then
    a spike is exactly the crowded-break signal) — never deduped. Needs >=
    FUNDING_Z_MIN_OBS finite obs and nonzero variance.
    """
    try:
        raw = list((values or [])[-FUNDING_Z_WINDOW:])
    except Exception:
        return None
    v = []
    for x in raw:
        try:
            f = float(x)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            v.append(f)
    if len(v) < FUNDING_Z_MIN_OBS:
        return None
    mean = sum(v) / len(v)
    var = sum((x - mean) ** 2 for x in v) / len(v)
    if var <= 1e-24:
        return None
    return (v[-1] - mean) / math.sqrt(var)


def funding_z_series(fr):
    """Per-bar causal funding z, one observation per 8h print. NaN = undefined.

    NPZ funding is forward-filled per 15m bar; prints land every 8h = 32
    bars, so bar i observes fr[i], fr[i-32], ... (causal stride sampling;
    phase-free since values are constant within a print span). Live observes
    trailing cache prints + current through the SAME funding_z_last.
    Strips the leading pre-cache zero run (backtest_v8_precompute marks
    pre-cache bars 0.0 — missing-data markers, not real 0 fundings).
    Non-8h funding intervals (some symbols print 4h/1h) only change the
    window span, never correctness.
    """
    f = np.asarray(fr, dtype=float).ravel()
    n = f.size
    out = np.full(n, np.nan, dtype=float)
    if n == 0:
        return out
    start = 0
    while start < n and f[start] == 0.0:
        start += 1
    if start >= n:
        return out
    f = np.where(np.isfinite(f), f, 0.0)
    idx = np.arange(n)[:, None] - (FUNDING_STRIDE_BARS * np.arange(FUNDING_Z_WINDOW)[None, :])
    valid = idx >= start
    vals = np.where(valid, f[np.where(valid, idx, 0)], 0.0)
    counts = valid.sum(axis=1)
    ok = counts >= FUNDING_Z_MIN_OBS
    mean = np.zeros(n)
    mean[ok] = vals[ok].sum(axis=1) / counts[ok]
    var = np.zeros(n)
    var[ok] = ((vals[ok] - mean[ok][:, None]) ** 2 * valid[ok]).sum(axis=1) / counts[ok]
    fire = ok & (var > 1e-24)
    out[fire] = (f[fire] - mean[fire]) / np.sqrt(var[fire])
    return out


def rsi2_key_for(mapping, tf):
    """First present RSI2 key for tf, else None (honest non-binding)."""
    t = str(tf or "").strip().lower()
    if not t:
        return None
    return _present_key(mapping, (f"rsi_2_{t}", f"rsi2_{t}", f"rsi_2_npz_{t}"))


def _funding_arr(npz, n, _safe):
    k = _present_key(npz, _FUNDING_KEYS)
    if k is None:
        return None
    return _as_n(_safe(npz, k, n, 0.0), n)


def _oi_arr(npz, n, _safe):
    k = _present_key(npz, _OI_KEYS)
    if k is None:
        return None
    return _as_n(_safe(npz, k, n, 0.0), n)


def _entry_funding_mask(npz, n, is_long, cfg, _safe):
    if _is_tradier(cfg):
        return None
    if not bool(_cfg_get(cfg, "FUNDING_CROWD_ENTRY_ENABLED", False)):
        return None
    zthr = abs(_num(_cfg_get(cfg, "FUNDING_CROWD_ENTRY_Z", 2.0), 2.0))
    if zthr <= 0:
        return None
    fr = _funding_arr(npz, n, _safe)
    if fr is None:
        return np.zeros(n, dtype=bool)
    z = funding_z_series(fr)
    fin = np.isfinite(z)
    if is_long:
        return fin & (z <= -zthr)
    return fin & (z >= zthr)


def _entry_oi_mask(npz, n, is_long, cfg, _safe):
    if _is_tradier(cfg):
        return None
    if not bool(_cfg_get(cfg, "OI_SURGE_ENTRY_ENABLED", False)):
        return None
    pct = _num(_cfg_get(cfg, "OI_SURGE_ENTRY_PCT", 3.0), 3.0)
    if pct <= 0:
        return None
    oi = _oi_arr(npz, n, _safe)
    if oi is None:
        return np.zeros(n, dtype=bool)
    return np.isfinite(oi) & (oi >= pct)


def _entry_rsi2_mask(npz, n, is_long, cfg, _safe):
    if not bool(_cfg_get(cfg, "RSI2_XTREME_ENTRY_ENABLED", False)):
        return None
    tf = str(_cfg_get(cfg, "RSI2_XTREME_ENTRY_TF", "1h") or "1h").strip().lower()
    key = rsi2_key_for(npz, tf)
    if key is None:
        return np.zeros(n, dtype=bool)
    r = _as_n(_safe(npz, key, n, 50.0), n)
    fin = np.isfinite(r)
    if is_long:
        return fin & (r <= RSI2_OVERSOLD)
    return fin & (r >= RSI2_OVERBOUGHT)


def _exit_funding_mask(npz, n, is_long, cfg, _safe):
    if _is_tradier(cfg):
        return None
    if not bool(_cfg_get(cfg, "FUNDING_CROWD_EXIT_ENABLED", False)):
        return None
    zthr = abs(_num(_cfg_get(cfg, "FUNDING_CROWD_EXIT_Z", 2.0), 2.0))
    if zthr <= 0:
        return None
    fr = _funding_arr(npz, n, _safe)
    if fr is None:
        return np.zeros(n, dtype=bool)
    z = funding_z_series(fr)
    fin = np.isfinite(z)
    if is_long:
        return fin & (z >= zthr)
    return fin & (z <= -zthr)


def _exit_oi_mask(npz, n, is_long, cfg, _safe):
    if _is_tradier(cfg):
        return None
    if not bool(_cfg_get(cfg, "OI_SURGE_EXIT_ENABLED", False)):
        return None
    pct = _num(_cfg_get(cfg, "OI_SURGE_EXIT_PCT", 3.0), 3.0)
    if pct <= 0:
        return None
    oi = _oi_arr(npz, n, _safe)
    if oi is None:
        return np.zeros(n, dtype=bool)
    return np.isfinite(oi) & (oi >= pct)


def _exit_rsi2_mask(npz, n, is_long, cfg, _safe):
    if not bool(_cfg_get(cfg, "RSI2_XTREME_EXIT_ENABLED", False)):
        return None
    tf = str(_cfg_get(cfg, "RSI2_XTREME_EXIT_TF", "1h") or "1h").strip().lower()
    key = rsi2_key_for(npz, tf)
    if key is None:
        return np.zeros(n, dtype=bool)
    r = _as_n(_safe(npz, key, n, 50.0), n)
    fin = np.isfinite(r)
    if is_long:
        return fin & (r >= RSI2_OVERBOUGHT)
    return fin & (r <= RSI2_OVERSOLD)


def _or_parts(parts, n):
    armed = [p for p in parts if p is not None]
    if not armed:
        return None
    out = np.zeros(n, dtype=bool)
    for p in armed:
        v = np.asarray(p, dtype=bool).ravel()
        m = min(n, v.size)
        out[:m] |= v[:m]
    return out


def entry_mask(npz, n, is_long, cfg, _safe):
    """OR of armed entry families; None when nothing armed (default-inert)."""
    return _or_parts((_entry_funding_mask(npz, n, is_long, cfg, _safe), _entry_oi_mask(npz, n, is_long, cfg, _safe), _entry_rsi2_mask(npz, n, is_long, cfg, _safe)), n)


def exit_mask(npz, n, is_long, cfg, _safe):
    """OR of armed exit families; None when nothing armed (default-inert)."""
    return _or_parts((_exit_funding_mask(npz, n, is_long, cfg, _safe), _exit_oi_mask(npz, n, is_long, cfg, _safe), _exit_rsi2_mask(npz, n, is_long, cfg, _safe)), n)


def _sym_key(symbol):
    s = str(symbol or "").upper()
    if ":" in s:
        s = s.split(":")[-1]
    return s.replace("_LONG", "").replace("_SHORT", "").strip()


def _funding_cache_history(sym):
    try:
        from config import BASE_PATH
        p = Path(BASE_PATH) / "data" / "funding_cache" / f"{sym}.json"
        recs = json.loads(p.read_text())
    except Exception:
        return None
    try:
        out = []
        for r in recs[-(FUNDING_Z_WINDOW - 1):]:
            f = float(r.get("fundingRate"))
            if math.isfinite(f):
                out.append(f)
        return out or None
    except Exception:
        return None


def _live_num(ind, market_data, sym, keys):
    for src in (ind, (market_data or {}).get(sym) if isinstance(market_data, dict) else None):
        if not isinstance(src, dict):
            continue
        for k in keys:
            if k in src and src[k] is not None:
                try:
                    f = float(src[k])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(f):
                    return f
    return None


def _live_funding_z(ind, market_data, sym, funding_history):
    cur = _live_num(ind, market_data, sym, _FUNDING_KEYS)
    if cur is None:
        return None
    hist = list(funding_history) if funding_history is not None else (_funding_cache_history(sym) or [])
    series = hist[-(FUNDING_Z_WINDOW - 1):] + [cur]
    return funding_z_last(series)


def check_entry_proposal(get, is_long, ind, symbol="", market_data=None, funding_history=None, is_crypto=True):
    """Live scalar entry twin. (fire, reason); fail-open False on missing data."""
    ind = ind or {}
    sym = _sym_key(symbol)
    if is_crypto:
        if bool(_g(get, "FUNDING_CROWD_ENTRY_ENABLED", False)):
            zthr = abs(_num(_g(get, "FUNDING_CROWD_ENTRY_Z", 2.0), 2.0))
            if zthr > 0:
                z = _live_funding_z(ind, market_data, sym, funding_history)
                if z is not None and ((is_long and z <= -zthr) or ((not is_long) and z >= zthr)):
                    return True, f"FUNDING_CROWD_ENTRY_{'LONG' if is_long else 'SHORT'}_z{z:+.2f}_thr{zthr:g}"
        if bool(_g(get, "OI_SURGE_ENTRY_ENABLED", False)):
            pct = _num(_g(get, "OI_SURGE_ENTRY_PCT", 3.0), 3.0)
            if pct > 0:
                oi = _live_num(ind, market_data, sym, _OI_KEYS)
                if oi is not None and oi >= pct:
                    return True, f"OI_SURGE_ENTRY_{'LONG' if is_long else 'SHORT'}_oi{oi:+.2f}_thr{pct:g}"
    if bool(_g(get, "RSI2_XTREME_ENTRY_ENABLED", False)):
        tf = str(_g(get, "RSI2_XTREME_ENTRY_TF", "1h") or "1h").strip().lower()
        r = _live_num(ind, market_data, sym, (f"rsi_2_{tf}", f"rsi2_{tf}", f"rsi_2_npz_{tf}"))
        if r is not None and ((is_long and r <= RSI2_OVERSOLD) or ((not is_long) and r >= RSI2_OVERBOUGHT)):
            return True, f"RSI2_XTREME_ENTRY_{'LONG' if is_long else 'SHORT'}_{tf}_{r:.1f}"
    return False, ""


def check_exit(get, is_long, ind, symbol="", market_data=None, funding_history=None, is_crypto=True):
    """Live scalar exit twin. (fire, reason); fail-open False on missing data."""
    ind = ind or {}
    sym = _sym_key(symbol)
    if is_crypto:
        if bool(_g(get, "FUNDING_CROWD_EXIT_ENABLED", False)):
            zthr = abs(_num(_g(get, "FUNDING_CROWD_EXIT_Z", 2.0), 2.0))
            if zthr > 0:
                z = _live_funding_z(ind, market_data, sym, funding_history)
                if z is not None and ((is_long and z >= zthr) or ((not is_long) and z <= -zthr)):
                    return True, f"FUNDING_CROWD_EXIT_{'LONG' if is_long else 'SHORT'}_z{z:+.2f}_thr{zthr:g}"
        if bool(_g(get, "OI_SURGE_EXIT_ENABLED", False)):
            pct = _num(_g(get, "OI_SURGE_EXIT_PCT", 3.0), 3.0)
            if pct > 0:
                oi = _live_num(ind, market_data, sym, _OI_KEYS)
                if oi is not None and oi >= pct:
                    return True, f"OI_SURGE_EXIT_{'LONG' if is_long else 'SHORT'}_oi{oi:+.2f}_thr{pct:g}"
    if bool(_g(get, "RSI2_XTREME_EXIT_ENABLED", False)):
        tf = str(_g(get, "RSI2_XTREME_EXIT_TF", "1h") or "1h").strip().lower()
        r = _live_num(ind, market_data, sym, (f"rsi_2_{tf}", f"rsi2_{tf}", f"rsi_2_npz_{tf}"))
        if r is not None and ((is_long and r >= RSI2_OVERBOUGHT) or ((not is_long) and r <= RSI2_OVERSOLD)):
            return True, f"RSI2_XTREME_EXIT_{'LONG' if is_long else 'SHORT'}_{tf}_{r:.1f}"
    return False, ""
