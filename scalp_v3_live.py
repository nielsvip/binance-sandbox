"""
scalp_v3_live.py — Live-side V3 scalp entry/exit using data already in live metrics dict.

Live limitations vs backtest:
- 1m bar OHLC not in metrics dict → cannot do 1m HH/HL pattern. Use k_1m as K-threshold gate.
- 3m bar OHLC IS available via high_3m / low_3m / high_3m_prev / low_3m_prev.
- 15m high/low IS available; prev NOT in the standard unpacking → use K-only for 15m.
- All K values across TFs are available.

So live V3 operates in "3M_ONLY entry + 1M/3M K-exit" mode. Best live-safe sweep variant:
  tf=3M_ONLY exit=ANY side=LONG_ONLY vol=1.0 k3m=40 hold=15 bounce=K15M_ONLY
  → +71.9% total / n=172 / WR 25.6% on mover shortlist (25h window).

Structure mirrors htf_breakout_scalper.check_scalp_v2_entry / _exit — takes indicators + price
+ position + config and returns a decision dict or None.

2026-04-22: user authorized live flip with pos_min_qty cap.
"""
from __future__ import annotations
import time
from typing import Dict, Optional


def _sf(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except Exception:
        return default


def check_scalp_v3_live_entry(symbol: str, position_key: str, indicators: Dict, price: float,
                              position, account_key: str, config) -> Optional[Dict]:
    """Returns {'side': 'LONG'|'SHORT', 'reason': str} on fire, else None.
    No entry if already holding position. Caller handles position size + execute_trade_wrapper."""
    if not indicators: return None
    if price <= 0: return None
    # Skip if any existing exposure
    if position is not None:
        _amt = abs(_sf(getattr(position, 'positionAmt', 0), 0))
        if _amt > 0: return None
    # ---- Pull indicator values ----
    k_1m = _sf(indicators.get('stoch_k_1m', indicators.get('k_1m', 50)), 50)
    k_3m = _sf(indicators.get('stoch_k_3m', indicators.get('k_3m', 50)), 50)
    k_3m_prev = _sf(indicators.get('k_3m_prev', 50), 50)
    k_15m = _sf(indicators.get('stoch_k_15m', 50), 50)
    k_15m_prev = _sf(indicators.get('stoch_k_15m_prev', 50), 50)
    k_1h = _sf(indicators.get('stoch_k_1h', 50), 50)
    k_4h = _sf(indicators.get('stoch_k_4h', 50), 50)
    high_3m = _sf(indicators.get('high_3m', 0), 0)
    low_3m = _sf(indicators.get('low_3m', 0), 0)
    high_3m_prev = _sf(indicators.get('high_3m_prev', 0), 0)
    low_3m_prev = _sf(indicators.get('low_3m_prev', 0), 0)
    dc_high_15m = _sf(indicators.get('dc_high_15m', 0), 0)
    if high_3m_prev <= 0 or low_3m_prev <= 0: return None  # not enough data
    # ---- Thresholds from config (winning-sweep params as defaults) ----
    k_1m_max = int(getattr(config, 'SCALP_V3_ENTRY_K_1M_MAX', 40))
    k_3m_max = int(getattr(config, 'SCALP_V3_ENTRY_K_3M_MAX', 40))
    k_15m_max = int(getattr(config, 'SCALP_V3_ENTRY_K_15M_MAX', 65))
    k_1h_max = int(getattr(config, 'SCALP_V3_ENTRY_K_1H_MAX', 80))
    k_4h_max = int(getattr(config, 'SCALP_V3_ENTRY_K_4H_MAX', 85))
    # ---- Try LONG ----
    long_ok = True; long_reason = ""
    if k_1h >= k_1h_max: long_ok = False; long_reason = f"K_1H={k_1h:.0f}_cap={k_1h_max}"
    elif k_4h >= k_4h_max: long_ok = False; long_reason = f"K_4H={k_4h:.0f}_cap={k_4h_max}"
    elif k_15m >= k_15m_max: long_ok = False; long_reason = f"K_15M={k_15m:.0f}_cap={k_15m_max}"
    elif k_3m >= k_3m_max: long_ok = False; long_reason = f"K_3M={k_3m:.0f}_max={k_3m_max}"
    elif not (high_3m > high_3m_prev and low_3m > low_3m_prev):
        long_ok = False; long_reason = "3M_BAR_NOT_HH_HL"
    # ---- Try SHORT (requires recent dump — proxy: price well below dc_high_15m) ----
    short_ok = True; short_reason = ""
    short_require_dump = bool(getattr(config, 'SCALP_V3_SHORT_REQUIRE_RECENT_DUMP', True))
    dump_pct = float(getattr(config, 'SCALP_V3_SHORT_RECENT_DUMP_PCT', 3.0))
    # Mirror of LONG K checks but inverted (overbought zone)
    k_1h_short_min = 100 - k_1h_max
    k_4h_short_min = 100 - k_4h_max
    k_15m_short_min = 100 - k_15m_max
    k_3m_short_min = 100 - k_3m_max
    if k_1h <= k_1h_short_min: short_ok = False; short_reason = f"K_1H={k_1h:.0f}_min={k_1h_short_min}"
    elif k_4h <= k_4h_short_min: short_ok = False; short_reason = f"K_4H={k_4h:.0f}_min={k_4h_short_min}"
    elif k_15m <= k_15m_short_min: short_ok = False; short_reason = f"K_15M={k_15m:.0f}_min={k_15m_short_min}"
    elif k_3m <= k_3m_short_min: short_ok = False; short_reason = f"K_3M={k_3m:.0f}_min={k_3m_short_min}"
    elif not (high_3m < high_3m_prev and low_3m < low_3m_prev):
        short_ok = False; short_reason = "3M_BAR_NOT_LL_LH"
    elif short_require_dump and dc_high_15m > 0:
        # Proxy for "fall off cliff then bounce to dc_basis" — price must be well below recent 15m high
        if price > dc_high_15m * (1.0 - dump_pct / 100.0):
            short_ok = False; short_reason = f"NOT_BELOW_15M_HIGH_by{dump_pct}pct"
    # ---- Return the firing decision (LONG preferred if both fire — rare) ----
    if long_ok:
        return {"side": "LONG", "reason": f"SCALP_V3_OPEN_LONG_3MBAR_k1m{k_1m:.0f}_k3m{k_3m:.0f}_k15m{k_15m:.0f}_k1h{k_1h:.0f}"}
    if short_ok:
        return {"side": "SHORT", "reason": f"SCALP_V3_OPEN_SHORT_3MBAR_k1m{k_1m:.0f}_k3m{k_3m:.0f}_k15m{k_15m:.0f}_k1h{k_1h:.0f}"}
    return None


def check_scalp_v3_live_exit(position_key: str, indicators: Dict, price: float,
                             position, config) -> Optional[Dict]:
    """Returns {'reason': str} on fire, else None. Caller closes via execute_trade_wrapper."""
    if not indicators: return None
    if position is None: return None
    _amt = abs(_sf(getattr(position, 'positionAmt', 0), 0))
    if _amt <= 0: return None
    _reason = str(getattr(position, 'augment_reason', '') or '')
    if not _reason.startswith('SCALP_V3_OPEN_'): return None  # not our position
    side = 'LONG' if _reason.find('_LONG_') >= 0 else 'SHORT' if _reason.find('_SHORT_') >= 0 else None
    if side is None: return None
    entry_price = _sf(getattr(position, 'entry_price', 0), 0)
    opened_at = _sf(getattr(position, 'opened_at', 0), 0)
    if entry_price <= 0 or price <= 0: return None
    # ---- Stall-out ----
    now_ts = time.time()
    age_sec = now_ts - opened_at if opened_at > 0 else 0
    gain_pct = ((price - entry_price) / entry_price * 100.0) if side == 'LONG' else ((entry_price - price) / entry_price * 100.0)
    max_hold_min = float(getattr(config, 'SCALP_V3_MAX_HOLD_MIN', 15.0))
    stall_gain = float(getattr(config, 'SCALP_V3_STALL_GAIN_MAX_PCT', -0.1))
    if age_sec > max_hold_min * 60.0 and gain_pct <= stall_gain:
        return {"reason": f"SCALP_V3_CLOSE_STALL_{side}_age{age_sec/60:.1f}m_g{gain_pct:+.2f}%"}
    # ---- Indicator reads ----
    k_1m = _sf(indicators.get('stoch_k_1m', indicators.get('k_1m', 50)), 50)
    k_3m = _sf(indicators.get('stoch_k_3m', indicators.get('k_3m', 50)), 50)
    k_15m = _sf(indicators.get('stoch_k_15m', 50), 50)
    high_3m = _sf(indicators.get('high_3m', 0), 0)
    low_3m = _sf(indicators.get('low_3m', 0), 0)
    high_3m_prev = _sf(indicators.get('high_3m_prev', 0), 0)
    low_3m_prev = _sf(indicators.get('low_3m_prev', 0), 0)
    if high_3m_prev <= 0 or low_3m_prev <= 0: return None
    # ---- Thresholds ----
    exit_k_3m_min = int(getattr(config, 'SCALP_V3_EXIT_3M_K_MIN', 95))
    exit_k_15m_min = int(getattr(config, 'SCALP_V3_EXIT_15M_K_MIN', 95))
    exit_k_1m_min = int(getattr(config, 'SCALP_V3_EXIT_1M_K_MIN', 98))
    # ---- 3M bar+K exit ----
    if side == 'LONG':
        if k_3m > exit_k_3m_min and (low_3m < low_3m_prev or high_3m < high_3m_prev):
            return {"reason": f"SCALP_V3_CLOSE_3MBAR_LONG_k3m{k_3m:.0f}_g{gain_pct:+.2f}%"}
        # 15m K-only hard flip (no 15m_prev bar data in metrics, so K-only)
        if k_15m > exit_k_15m_min:
            return {"reason": f"SCALP_V3_CLOSE_15M_K_LONG_k15m{k_15m:.0f}_g{gain_pct:+.2f}%"}
        # 1m K-only fast exit (no 1m bar pattern live)
        if k_1m > exit_k_1m_min:
            return {"reason": f"SCALP_V3_CLOSE_1M_K_LONG_k1m{k_1m:.0f}_g{gain_pct:+.2f}%"}
    else:  # SHORT (mirror)
        if k_3m < (100 - exit_k_3m_min) and (low_3m > low_3m_prev or high_3m > high_3m_prev):
            return {"reason": f"SCALP_V3_CLOSE_3MBAR_SHORT_k3m{k_3m:.0f}_g{gain_pct:+.2f}%"}
        if k_15m < (100 - exit_k_15m_min):
            return {"reason": f"SCALP_V3_CLOSE_15M_K_SHORT_k15m{k_15m:.0f}_g{gain_pct:+.2f}%"}
        if k_1m < (100 - exit_k_1m_min):
            return {"reason": f"SCALP_V3_CLOSE_1M_K_SHORT_k1m{k_1m:.0f}_g{gain_pct:+.2f}%"}
    return None
