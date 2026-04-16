"""
HTF Breakout Scalper (SCALP_V2)

A new scalp mechanism that ONLY fires when price has broken out of the higher
timeframe Donchian Channel — i.e., we only "scalp" when the HTF is already
moving in our direction. This replaces the legacy SCALP_MODE which lacked HTF
gating and produced too many noise trades.

ENTRY (fixed across all variants):
  LONG : current_price > dc_high_15m_prev AND current_price > dc_high_1h_prev
  SHORT: current_price < dc_low_15m_prev  AND current_price < dc_low_1h_prev
  (DC_HTF_REQUIRE_BOTH=False relaxes to "either 15m OR 1h")

EXIT VARIANTS (selected by config.SCALP_V2_VARIANT):
  V1_WT_CONFIRM         — exit on 3m WT cross against position
  V2_LH_LL_3M           — exit on first 3m lower-high (LONG) / higher-low (SHORT)
  V3_LH_LL_1M           — same as V2 but 1m if available, else 3m
  V4_HA_FLIP            — exit when 3m HA candle flips against position
  V5_BREAK_HIGH_REENTRY — exit on HA flip; track previous run high for re-entry
  V6_COMBINED_WT_HA     — exit only when WT cross AND HA flip both signal
  V7_TIGHT_TRAILING     — exit on first 3m close below previous 3m low
  V8_HTF_RECLAIM        — exit when price falls back below dc_high_15m

UNIVERSAL TECHNICAL STOP (applies to all variants):
  LONG : if current_price <= low_3m_prev  → EXIT (stop)
  SHORT: if current_price >= high_3m_prev → EXIT (stop)

POSITION TAGGING:
  Positions opened by V2 are tagged via the reason string starting with
  "SCALP_V2_OPEN_". The exit checker only fires for those positions; everything
  else flows through the existing exit pipeline. No new dataclass field needed.

ALL CALLERS PASS the live indicators dict, so this module never re-fetches data
or reimplements indicator math — it is pure decision logic over what the live
data manager already produced.
"""

from typing import Dict, Optional, Tuple
from datetime import datetime, timezone


# Position-key prefix used to identify V2-opened positions in the exit path.
SCALP_V2_REASON_PREFIX = "SCALP_V2_OPEN_"
SCALP_V2_EXIT_REASON_PREFIX = "SCALP_V2_EXIT_"

VARIANTS = (
    "V1_WT_CONFIRM",
    "V2_LH_LL_3M",
    "V3_LH_LL_1M",
    "V4_HA_FLIP",
    "V5_BREAK_HIGH_REENTRY",
    "V6_COMBINED_WT_HA",
    "V7_TIGHT_TRAILING",
    "V8_HTF_RECLAIM",
)


def _f(d: Dict, k: str, default: float = 0.0) -> float:
    v = d.get(k)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _is_v2_position(position) -> bool:
    """A V2-managed position is tagged via augment_reason starting with our prefix."""
    if not position:
        return False
    reason = str(getattr(position, "augment_reason", "") or "")
    return reason.startswith(SCALP_V2_REASON_PREFIX)


def _htf_breakout_ok(indicators: Dict, current_price: float, is_long: bool, tfs, require_all: bool) -> Tuple[bool, str]:
    """The fixed HTF DC entry gate. Checks each TF in `tfs` (e.g. ["15m","1h","4h"]).
    require_all=True → ALL listed TFs must show breakout. False → any one is enough.
    Returns (passed, detail)."""
    if not tfs:
        return False, "no_tfs"
    results = []
    detail_parts = [f"px={current_price:.6f}"]
    for tf in tfs:
        if is_long:
            level = _f(indicators, f"dc_high_{tf}_prev", _f(indicators, f"dc_high_{tf}", 0))
            ok = level > 0 and current_price > level
        else:
            level = _f(indicators, f"dc_low_{tf}_prev", _f(indicators, f"dc_low_{tf}", 0))
            ok = level > 0 and current_price < level
        results.append(ok)
        detail_parts.append(f"dc{tf}={level:.6f}({'✓' if ok else '✗'})")
    detail = " ".join(detail_parts)
    if require_all:
        return (all(results), detail)
    return (any(results), detail)


def check_scalp_v2_entry(symbol: str, position_key: str, indicators: Dict, current_price: float,
                         position, account_key: str, config) -> Optional[Dict]:
    """Decide whether to OPEN a new V2 scalp on this side.

    Returns {action, reason, side} or None.

    Caller is responsible for queueing the trade via execute_trade_wrapper with
    the returned reason — this function only decides, it does not place orders.
    """
    if not getattr(config, "SCALP_MODE", False):
        return None
    # Live restriction: only accounts in SCALP_ACCOUNTS may scalp
    if account_key not in getattr(config, "SCALP_ACCOUNTS", []):
        return None
    if not position_key:
        return None

    is_long = position_key.endswith("_LONG")
    if not is_long and not position_key.endswith("_SHORT"):
        return None

    # Empty side only — augments are handled by the normal augment path
    pos_amt = abs(_f({"_": getattr(position, "positionAmt", 0) if position else 0}, "_", 0))
    if pos_amt > 0:
        return None

    if current_price <= 0:
        return None

    # 2026-04-16: entry mode switchable. Default "breakout" (live, unchanged).
    # "pullback" variant written but NOT YET backtested per-account — DO NOT SET DEFAULT
    # until backtested on inf (the only SCALP_ACCOUNT) and forward-tested.
    entry_mode = str(getattr(config, "SCALP_V2_ENTRY_MODE", "breakout"))

    if entry_mode == "pullback":
        ok, detail = _htf_pullback_ok(indicators, current_price, is_long)
    else:  # "breakout" — legacy, confirmed losing (PF 0.9, Sharpe -0.02 in scalp_v2_fast_backtest)
        tfs = list(getattr(config, "SCALP_V2_DC_HTF_LIST", ["15m", "1h"]))
        require_all = bool(getattr(config, "SCALP_V2_DC_HTF_REQUIRE_ALL", True))
        ok, detail = _htf_breakout_ok(indicators, current_price, is_long, tfs, require_all)

    if not ok:
        return None

    variant = str(getattr(config, "SCALP_V2_VARIANT", "V1_WT_CONFIRM"))
    return {
        "action": "OPEN",
        "side": "LONG" if is_long else "SHORT",
        "reason": f"{SCALP_V2_REASON_PREFIX}{variant}_{entry_mode}_{detail}",
    }


def _htf_pullback_ok(indicators, current_price, is_long):
    """PULLBACK-to-trend entry (2026-04-16 user priority).

    LONG: Fundamentally rising ticker (HTF trend up) pulled back to support →
    price near 15m DC low, stoch oversold + turning up, 1h WT bouncing.

    SHORT: Fundamentally falling ticker rallied to resistance →
    price near 15m DC high, stoch overbought + turning down, 1h WT rolling over.

    Enters at TEMP BOTTOM/TOP not END of move.
    """
    dc_high_15m = _f(indicators, "dc_high_15m"); dc_low_15m = _f(indicators, "dc_low_15m")
    dc_high_1h = _f(indicators, "dc_high_1h"); dc_low_1h = _f(indicators, "dc_low_1h")
    k_3m = _f(indicators, "stoch_k_3m", 50); k_3m_prev = _f(indicators, "stoch_k_3m_prev", k_3m)
    d_3m = _f(indicators, "stoch_d_3m", 50)
    wt1_1h = _f(indicators, "wt1_1h"); wt2_1h = _f(indicators, "wt2_1h")
    wt1_4h = _f(indicators, "wt1_4h"); wt2_4h = _f(indicators, "wt2_4h")
    wt_vel_3m = _f(indicators, "wt_velocity_3m", 0)
    wt_vel_1h = _f(indicators, "wt_velocity_1h", 0)

    if dc_high_15m <= 0 or dc_low_15m <= 0:
        return False, "NO_DC_15M"

    dc_range = max(dc_high_15m - dc_low_15m, 1e-9)
    dc_pos_15m = (current_price - dc_low_15m) / dc_range  # 0=at low, 1=at high

    if is_long:
        # HTF must be trending UP (fundamentally rising)
        htf_up = (wt1_1h > wt2_1h) and (wt1_4h > wt2_4h) and (wt_vel_1h > -1.0)
        if not htf_up:
            return False, f"HTF_NOT_UP wt1h={wt1_1h:.1f}/{wt2_1h:.1f} wt4h={wt1_4h:.1f}/{wt2_4h:.1f}"
        # Price pulled back to lower quarter of 15m DC
        at_bottom = dc_pos_15m < 0.25
        if not at_bottom:
            return False, f"NOT_AT_BOTTOM dc_pos={dc_pos_15m:.2f}"
        # Stoch deep oversold AND turning up
        stoch_bounce = (k_3m < 30) and (k_3m > k_3m_prev) and (k_3m > d_3m)
        if not stoch_bounce:
            return False, f"NO_STOCH_BOUNCE k3m={k_3m:.0f}/{k_3m_prev:.0f}/d={d_3m:.0f}"
        # 3m velocity bouncing from negative
        vel_turning = wt_vel_3m > -0.5
        if not vel_turning:
            return False, f"VEL_STILL_DROPPING vel3m={wt_vel_3m:.2f}"
        return True, f"PULLBACK_LONG dc_pos={dc_pos_15m:.2f}_k3m={k_3m:.0f}_v3m={wt_vel_3m:.1f}_wt1h={wt1_1h:.1f}>{wt2_1h:.1f}"
    else:
        htf_down = (wt1_1h < wt2_1h) and (wt1_4h < wt2_4h) and (wt_vel_1h < 1.0)
        if not htf_down:
            return False, f"HTF_NOT_DOWN"
        at_top = dc_pos_15m > 0.75
        if not at_top:
            return False, f"NOT_AT_TOP dc_pos={dc_pos_15m:.2f}"
        stoch_roll = (k_3m > 70) and (k_3m < k_3m_prev) and (k_3m < d_3m)
        if not stoch_roll:
            return False, f"NO_STOCH_ROLL k3m={k_3m:.0f}"
        vel_turning = wt_vel_3m < 0.5
        if not vel_turning:
            return False, f"VEL_STILL_RISING vel3m={wt_vel_3m:.2f}"
        return True, f"PULLBACK_SHORT dc_pos={dc_pos_15m:.2f}_k3m={k_3m:.0f}_v3m={wt_vel_3m:.1f}"


def _exit_v1_wt_confirm(indicators: Dict, is_long: bool) -> Tuple[bool, str]:
    wt1_3m = _f(indicators, "wt1_3m"); wt2_3m = _f(indicators, "wt2_3m")
    wt1_3m_prev = _f(indicators, "wt1_3m_prev", wt1_3m); wt2_3m_prev = _f(indicators, "wt2_3m_prev", wt2_3m)
    cross_3m = str(indicators.get("wt_cross_3m", ""))
    if is_long:
        crossed_bear = (wt1_3m_prev > wt2_3m_prev) and (wt1_3m < wt2_3m)
        if cross_3m == "BEAR" or crossed_bear:
            return True, f"WT_CROSS_BEAR_3m wt1={wt1_3m:.1f}<wt2={wt2_3m:.1f}"
    else:
        crossed_bull = (wt1_3m_prev < wt2_3m_prev) and (wt1_3m > wt2_3m)
        if cross_3m == "BULL" or crossed_bull:
            return True, f"WT_CROSS_BULL_3m wt1={wt1_3m:.1f}>wt2={wt2_3m:.1f}"
    return False, ""


def _exit_lh_ll(indicators: Dict, is_long: bool, tf: str) -> Tuple[bool, str]:
    high = _f(indicators, f"high_{tf}"); high_prev = _f(indicators, f"high_{tf}_prev")
    low = _f(indicators, f"low_{tf}"); low_prev = _f(indicators, f"low_{tf}_prev")
    if high <= 0 or high_prev <= 0 or low <= 0 or low_prev <= 0:
        return False, ""
    if is_long:
        if high < high_prev and low <= low_prev:
            return True, f"LH_{tf} h={high:.6f}<{high_prev:.6f}"
    else:
        if low > low_prev and high >= high_prev:
            return True, f"HL_{tf} l={low:.6f}>{low_prev:.6f}"
    return False, ""


def _exit_ha_flip(indicators: Dict, is_long: bool) -> Tuple[bool, str]:
    ha = indicators.get("ha_3m")
    # numeric (V8 npz: -1/0/1) or string ('red'/'green')
    if isinstance(ha, (int, float)):
        is_red = ha == -1
        is_green = ha == 1
    else:
        s = str(ha or "").lower()
        is_red = s == "red"
        is_green = s == "green"
    if is_long and is_red:
        return True, f"HA_3m_RED"
    if (not is_long) and is_green:
        return True, f"HA_3m_GREEN"
    return False, ""


def _exit_tight_trailing(indicators: Dict, current_price: float, is_long: bool) -> Tuple[bool, str]:
    low_prev = _f(indicators, "low_3m_prev")
    high_prev = _f(indicators, "high_3m_prev")
    close_3m = _f(indicators, "close_3m", current_price)
    if is_long and low_prev > 0 and close_3m < low_prev:
        return True, f"TRAIL close_3m={close_3m:.6f}<low_3m_prev={low_prev:.6f}"
    if (not is_long) and high_prev > 0 and close_3m > high_prev:
        return True, f"TRAIL close_3m={close_3m:.6f}>high_3m_prev={high_prev:.6f}"
    return False, ""


def _exit_htf_reclaim(indicators: Dict, current_price: float, is_long: bool, tfs) -> Tuple[bool, str]:
    """Exit when price falls back below the FIRST (smallest) TF in the breakout list.
    Larger TFs are intentionally NOT used here — the entry breakout was on the smaller
    TF, so the reclaim signal also fires on the smaller TF for symmetry."""
    if not tfs:
        tfs = ["15m"]
    tf = tfs[0]
    if is_long:
        level = _f(indicators, f"dc_high_{tf}_prev", _f(indicators, f"dc_high_{tf}"))
        if level > 0 and current_price <= level:
            return True, f"HTF_RECLAIM_LONG px={current_price:.6f}<=dc_high_{tf}={level:.6f}"
    else:
        level = _f(indicators, f"dc_low_{tf}_prev", _f(indicators, f"dc_low_{tf}"))
        if level > 0 and current_price >= level:
            return True, f"HTF_RECLAIM_SHORT px={current_price:.6f}>=dc_low_{tf}={level:.6f}"
    return False, ""


def _exit_redzone(indicators: Dict, is_long: bool, k_threshold: int = 90) -> Tuple[bool, str]:
    """Exit when stoch K crosses back from extreme zone (overbought→reversal for LONG, oversold→reversal for SHORT)."""
    k_3m = _f(indicators, "stoch_k_3m", 50)
    k_3m_prev = _f(indicators, "k_3m_prev", _f(indicators, "stoch_k_3m_prev", k_3m))
    if is_long and k_3m_prev >= k_threshold and k_3m < k_threshold:
        return True, f"REDZONE_K{k_threshold} k={k_3m:.0f}<{k_threshold} prev={k_3m_prev:.0f}"
    mirror = 100 - k_threshold
    if not is_long and k_3m_prev <= mirror and k_3m > mirror:
        return True, f"REDZONE_K{k_threshold} k={k_3m:.0f}>{mirror} prev={k_3m_prev:.0f}"
    return False, ""


def _universal_technical_stop(indicators: Dict, current_price: float, is_long: bool) -> Tuple[bool, str]:
    """Hard technical stop applied across ALL variants — low_3m_prev for LONG,
    high_3m_prev for SHORT. The user explicitly chose this over a percentage stop."""
    low_prev = _f(indicators, "low_3m_prev")
    high_prev = _f(indicators, "high_3m_prev")
    if is_long and low_prev > 0 and current_price <= low_prev:
        return True, f"STOP_LOW_3M_PREV px={current_price:.6f}<={low_prev:.6f}"
    if (not is_long) and high_prev > 0 and current_price >= high_prev:
        return True, f"STOP_HIGH_3M_PREV px={current_price:.6f}>={high_prev:.6f}"
    return False, ""


def check_scalp_v2_exit(position_key: str, indicators: Dict, current_price: float,
                        position, config) -> Optional[Dict]:
    """Decide whether to CLOSE a V2-managed scalp.

    Only fires if the position was opened by V2 (tagged via augment_reason).
    Returns {action, reason} or None.
    """
    if not getattr(config, "SCALP_MODE", False):
        return None
    if not _is_v2_position(position):
        return None
    if not position_key or current_price <= 0:
        return None

    is_long = position_key.endswith("_LONG")

    # 1. Universal technical stop FIRST (cannot be bypassed by any variant)
    stop, stop_detail = _universal_technical_stop(indicators, current_price, is_long)
    if stop:
        return {"action": "QUICK_CLOSE", "reason": f"{SCALP_V2_EXIT_REASON_PREFIX}STOP_{stop_detail}"}

    # 2. Variant-specific exit
    variant = str(getattr(config, "SCALP_V2_VARIANT", "V1_WT_CONFIRM"))
    exit_fired, detail = False, ""
    if variant == "V1_WT_CONFIRM":
        exit_fired, detail = _exit_v1_wt_confirm(indicators, is_long)
    elif variant == "V2_LH_LL_3M":
        exit_fired, detail = _exit_lh_ll(indicators, is_long, "3m")
    elif variant == "V3_LH_LL_1M":
        exit_fired, detail = _exit_lh_ll(indicators, is_long, "1m")
        if not exit_fired:  # 1m may not exist in backtest — fall back to 3m
            exit_fired, detail = _exit_lh_ll(indicators, is_long, "3m")
    elif variant == "V4_HA_FLIP":
        exit_fired, detail = _exit_ha_flip(indicators, is_long)
    elif variant == "V5_BREAK_HIGH_REENTRY":
        exit_fired, detail = _exit_ha_flip(indicators, is_long)
    elif variant == "V6_COMBINED_WT_HA":
        wt_e, wt_d = _exit_v1_wt_confirm(indicators, is_long)
        ha_e, ha_d = _exit_ha_flip(indicators, is_long)
        if wt_e and ha_e:
            exit_fired, detail = True, f"{wt_d}+{ha_d}"
    elif variant == "V7_TIGHT_TRAILING":
        exit_fired, detail = _exit_tight_trailing(indicators, current_price, is_long)
    elif variant == "V8_HTF_RECLAIM":
        _v8_tfs = list(getattr(config, "SCALP_V2_DC_HTF_LIST", ["15m", "1h"]))
        exit_fired, detail = _exit_htf_reclaim(indicators, current_price, is_long, _v8_tfs)

    # 2b. Secondary exit layers — fire if primary variant didn't, each independently toggleable
    if not exit_fired and getattr(config, "SCALP_V2_REDZONE_EXIT", False):
        exit_fired, detail = _exit_redzone(indicators, is_long, int(getattr(config, "SCALP_V2_REDZONE_K_THRESHOLD", 90)))
    if not exit_fired and getattr(config, "SCALP_V2_LH_LL_EXIT", False):
        _lh_tf = str(getattr(config, "SCALP_V2_LH_LL_TF", "15m"))
        exit_fired, detail = _exit_lh_ll(indicators, is_long, _lh_tf)
        if exit_fired:
            detail = f"LH_LL_{_lh_tf}_{detail}"

    # 3. Max-hold safety net
    if not exit_fired:
        max_hold_min = float(getattr(config, "SCALP_V2_MAX_HOLD_MINUTES", 15.0))
        opened_at = getattr(position, "opened_at", None)
        if opened_at:
            try:
                if isinstance(opened_at, str):
                    opened_at = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                if hasattr(opened_at, "timestamp"):
                    age_min = (datetime.now(timezone.utc) - opened_at).total_seconds() / 60.0
                    if age_min >= max_hold_min:
                        exit_fired, detail = True, f"MAX_HOLD age={age_min:.0f}m≥{max_hold_min:.0f}m"
            except Exception:
                pass

    if exit_fired:
        return {"action": "QUICK_CLOSE", "reason": f"{SCALP_V2_EXIT_REASON_PREFIX}{variant}_{detail}"}
    return None
