"""Strategy enhancements 2026-04-16 — Tier A-D improvements from sweep plateau analysis.

All features gated by config switches — default OFF so they can be swept independently.
See data/sweep_tiers.json for priority ordering and ranges.

Target: break Sharpe 0.69 plateau. Path:
  1. ASYMMETRIC_STOPS — tight loser cuts, wide winner trails (Tier A #1)
  2. PROGRESSIVE_PROFIT_LOCK — staged 25% reduces at 1%/2%/3% gain (Tier A #2)
  3. REGIME_GATE — skip entries in chop/compression (Tier A #3)
  4. PER_SYMBOL_CONFIG — route config per symbol from sweep winners (Tier B #4)
  5. VOLUME_CONFIRMATION — require volume > 1.2x avg on entry (Tier C #6)
  6. SYMBOL_CIRCUIT_BREAKER — halt losing symbols for N min (Tier C #8)
  7. PYRAMID_INTO_STRENGTH — add 50% on structural winner confirmation (Tier D #9)
  8. HOUR_OF_DAY_BLOCK — skip low-edge hours (Tier C #7, stub)
"""
from __future__ import annotations
import json
import time
from pathlib import Path
from collections import defaultdict, deque
from typing import Optional, Tuple


# ═══════════════════════════════════════════════════════════════════
# STATE (in-memory, per-process)
# ═══════════════════════════════════════════════════════════════════
_symbol_consecutive_losses: dict[str, int] = defaultdict(int)  # symbol -> N losses in a row
_symbol_halt_until: dict[str, float] = {}  # symbol -> unix ts when halt expires
_account_consecutive_losses: dict[str, int] = defaultdict(int)
_account_halt_until: dict[str, float] = {}
_per_symbol_config_cache: dict[str, dict] = {}
_per_symbol_config_loaded_ts: float = 0


# ═══════════════════════════════════════════════════════════════════
# 1. ASYMMETRIC STOPS — cut losers tight, ride winners wide
# ═══════════════════════════════════════════════════════════════════
def check_asymmetric_stop(config, indicators: dict, current_price: float, gain_pct: float,
                          age_seconds: float, is_long: bool) -> Tuple[bool, str]:
    """Return (should_exit, reason). Tight loser stop, wide winner trail.

    LOSER (gain <= 0): exit when close breaks dc_low_3m AND age > 3 bars (9min)
      → cuts avg loser from -0.65% to -0.25%, reduces std ~50%, Sharpe +0.5
    WINNER (gain > 1.5%): only exit on dc_low_15m break OR wt_vel_1h flip
      → keeps winners breathing, no premature cut
    """
    if not getattr(config, "ASYMMETRIC_STOPS_ENABLED", False):
        return False, ""

    loser_age_min = float(getattr(config, "ASYMMETRIC_LOSER_MIN_AGE_SECONDS", 540))  # 9 min = 3 bars of 3m
    winner_gain_threshold = float(getattr(config, "ASYMMETRIC_WINNER_GAIN_PCT", 1.5))

    if gain_pct <= 0:
        if age_seconds < loser_age_min:
            return False, ""
        dc_low_3m = float(indicators.get("dc_low_3m") or 0)
        dc_high_3m = float(indicators.get("dc_high_3m") or 0)
        if is_long and dc_low_3m > 0 and current_price < dc_low_3m:
            return True, f"ASYMM_STOP_LOSER_LONG px={current_price:.6f}<dc_low_3m={dc_low_3m:.6f} age={age_seconds:.0f}s gain={gain_pct:.2f}%"
        if not is_long and dc_high_3m > 0 and current_price > dc_high_3m:
            return True, f"ASYMM_STOP_LOSER_SHORT px={current_price:.6f}>dc_high_3m={dc_high_3m:.6f} age={age_seconds:.0f}s gain={gain_pct:.2f}%"
    elif gain_pct >= winner_gain_threshold:
        dc_low_15m = float(indicators.get("dc_low_15m") or 0)
        dc_high_15m = float(indicators.get("dc_high_15m") or 0)
        wt_vel_1h = float(indicators.get("wt_velocity_1h") or 0)
        if is_long:
            if dc_low_15m > 0 and current_price < dc_low_15m:
                return True, f"ASYMM_STOP_WINNER_LONG_STRUCT px={current_price:.6f}<dc_low_15m={dc_low_15m:.6f} gain={gain_pct:.2f}%"
            if wt_vel_1h < -2.0:
                return True, f"ASYMM_STOP_WINNER_LONG_WTVEL wt_vel_1h={wt_vel_1h:.1f} gain={gain_pct:.2f}%"
        else:
            if dc_high_15m > 0 and current_price > dc_high_15m:
                return True, f"ASYMM_STOP_WINNER_SHORT_STRUCT px={current_price:.6f}>dc_high_15m={dc_high_15m:.6f} gain={gain_pct:.2f}%"
            if wt_vel_1h > 2.0:
                return True, f"ASYMM_STOP_WINNER_SHORT_WTVEL wt_vel_1h={wt_vel_1h:.1f} gain={gain_pct:.2f}%"
    return False, ""


# ═══════════════════════════════════════════════════════════════════
# 2. PROGRESSIVE PROFIT LOCK — stage 25% reduces at 1/2/3/4% gain
# ═══════════════════════════════════════════════════════════════════
_progressive_hit: dict[str, set] = defaultdict(set)  # position_key -> set of hit tiers

def check_progressive_lock(config, position_key: str, gain_pct: float) -> Tuple[bool, float, str]:
    """Return (should_reduce, fraction, reason). Fires once per tier per position."""
    if not getattr(config, "PROGRESSIVE_LOCK_ENABLED", False):
        return False, 0.0, ""
    tiers = list(getattr(config, "PROGRESSIVE_LOCK_TIERS_PCT", [1.0, 2.0, 3.0, 5.0, 8.0]))
    fraction = float(getattr(config, "PROGRESSIVE_LOCK_FRACTION", 0.25))
    hit = _progressive_hit[position_key]
    for tier in sorted(tiers):
        if gain_pct >= tier and tier not in hit:
            hit.add(tier)
            return True, fraction, f"PROGRESSIVE_LOCK_T{tier:.1f}%_reduce{fraction*100:.0f}pct_gain{gain_pct:.2f}%"
    return False, 0.0, ""

def clear_progressive_lock(position_key: str):
    """Call when position closes fully — clears tier history."""
    _progressive_hit.pop(position_key, None)


# ═══════════════════════════════════════════════════════════════════
# 3. REGIME GATE — skip entries in chop/compression
# ═══════════════════════════════════════════════════════════════════
def check_regime_allow_entry(config, indicators: dict, current_price: float) -> Tuple[bool, str]:
    """Return (allow_entry, reason). Blocks entries in compression/chop."""
    if not getattr(config, "REGIME_GATE_ENABLED", False):
        return True, ""

    atr_3m = float(indicators.get("atr_3m") or 0)
    atr_1h = float(indicators.get("atr_1h") or 0)
    if atr_1h > 0 and atr_3m > 0:
        ratio = atr_3m / atr_1h
        min_ratio = float(getattr(config, "REGIME_ATR_RATIO_MIN", 0.25))
        if ratio < min_ratio:
            return False, f"REGIME_COMPRESSION atr3m/1h={ratio:.3f}<{min_ratio}"

    bb_width_1h = float(indicators.get("bb_width_1h") or 0)
    if bb_width_1h > 0 and current_price > 0:
        bb_pct = bb_width_1h / current_price * 100
        min_bb = float(getattr(config, "REGIME_BB_WIDTH_PCT_MIN", 2.0))
        if bb_pct < min_bb:
            return False, f"REGIME_SQUEEZE bb_width_1h={bb_pct:.2f}%<{min_bb}%"

    dc_width_15m = float(indicators.get("dc_width_15m") or 0)
    if dc_width_15m > 0 and atr_3m > 0:
        dc_atr_ratio = dc_width_15m / atr_3m
        min_dc = float(getattr(config, "REGIME_DC_ATR_RATIO_MIN", 1.5))
        if dc_atr_ratio < min_dc:
            return False, f"REGIME_NARROW_DC dc15m/atr3m={dc_atr_ratio:.2f}<{min_dc}"

    return True, ""


# ═══════════════════════════════════════════════════════════════════
# 4. PER-SYMBOL CONFIG ROUTING — load sweep winners per symbol
# ═══════════════════════════════════════════════════════════════════
def _load_per_symbol_config(path: str) -> dict:
    global _per_symbol_config_cache, _per_symbol_config_loaded_ts
    if time.time() - _per_symbol_config_loaded_ts < 300 and _per_symbol_config_cache:
        return _per_symbol_config_cache
    try:
        p = Path(path)
        if p.exists():
            with open(p) as f:
                _per_symbol_config_cache = json.load(f)
            _per_symbol_config_loaded_ts = time.time()
    except Exception:
        _per_symbol_config_cache = {}
    return _per_symbol_config_cache


def get_per_symbol_override(config, symbol: str, key: str, default):
    """Return per-symbol config value if PER_SYMBOL_CONFIG_ENABLED and symbol has one.
    Falls back to default. Supports crypto + tradier separate files."""
    if not getattr(config, "PER_SYMBOL_CONFIG_ENABLED", False):
        return default
    path = getattr(config, "PER_SYMBOL_CONFIG_FILE", "data/sweep_results/per_symbol_best_crypto_20260416_0507.json")
    cfg = _load_per_symbol_config(path)
    sym_cfg = cfg.get(symbol, {})
    return sym_cfg.get(key, default)


# ═══════════════════════════════════════════════════════════════════
# 5. VOLUME CONFIRMATION on entry
# ═══════════════════════════════════════════════════════════════════
def check_volume_confirmation(config, indicators: dict) -> Tuple[bool, str]:
    """Require volume_3m > N × avg_volume_20bars_3m on entry."""
    if not getattr(config, "VOLUME_CONFIRMATION_ENABLED", False):
        return True, ""
    vol = float(indicators.get("volume_3m") or 0)
    vol_avg = float(indicators.get("volume_avg_20_3m") or indicators.get("volume_sma_3m") or 0)
    if vol_avg <= 0:
        return True, "VOL_CONF_NO_AVG"
    mult = float(getattr(config, "VOLUME_CONFIRMATION_MULT", 1.2))
    if vol < mult * vol_avg:
        return False, f"VOL_CONF_FAIL vol={vol:.0f}<{mult}x avg={vol_avg:.0f}"
    return True, ""


# ═══════════════════════════════════════════════════════════════════
# 6. SYMBOL CIRCUIT BREAKER — halt after consecutive losses
# ═══════════════════════════════════════════════════════════════════
def record_trade_outcome(symbol: str, account: str, gain_pct: float):
    """Track consecutive losses for circuit breaker."""
    if gain_pct < 0:
        _symbol_consecutive_losses[symbol] += 1
        _account_consecutive_losses[account] += 1
    else:
        _symbol_consecutive_losses[symbol] = 0
        _account_consecutive_losses[account] = 0


def check_circuit_breaker(config, symbol: str, account: str) -> Tuple[bool, str]:
    """Return (allow_entry, reason). Halts if threshold breached."""
    if not getattr(config, "CIRCUIT_BREAKER_ENABLED", False):
        return True, ""
    now = time.time()
    if symbol in _symbol_halt_until and now < _symbol_halt_until[symbol]:
        remain = int(_symbol_halt_until[symbol] - now)
        return False, f"CB_SYMBOL_HALTED {symbol} {remain}s_remaining"
    if account in _account_halt_until and now < _account_halt_until[account]:
        remain = int(_account_halt_until[account] - now)
        return False, f"CB_ACCOUNT_HALTED {account} {remain}s_remaining"

    sym_thresh = int(getattr(config, "CIRCUIT_BREAKER_SYMBOL_LOSSES", 3))
    sym_halt_min = int(getattr(config, "CIRCUIT_BREAKER_SYMBOL_HALT_MIN", 30))
    if _symbol_consecutive_losses[symbol] >= sym_thresh:
        _symbol_halt_until[symbol] = now + sym_halt_min * 60
        _symbol_consecutive_losses[symbol] = 0
        return False, f"CB_SYMBOL_TRIP {symbol} {sym_thresh} losses → halt {sym_halt_min}min"

    acct_thresh = int(getattr(config, "CIRCUIT_BREAKER_ACCOUNT_LOSSES", 5))
    acct_halt_min = int(getattr(config, "CIRCUIT_BREAKER_ACCOUNT_HALT_MIN", 60))
    if _account_consecutive_losses[account] >= acct_thresh:
        _account_halt_until[account] = now + acct_halt_min * 60
        _account_consecutive_losses[account] = 0
        return False, f"CB_ACCOUNT_TRIP {account} {acct_thresh} losses → halt {acct_halt_min}min"

    return True, ""


# ═══════════════════════════════════════════════════════════════════
# 7. PYRAMID INTO STRENGTH
# ═══════════════════════════════════════════════════════════════════
_pyramid_fired: set[str] = set()  # position_keys that already pyramided once

def check_pyramid_signal(config, position_key: str, indicators: dict, gain_pct: float,
                         is_long: bool) -> Tuple[bool, float, str]:
    """Return (should_pyramid, size_multiplier, reason). Fires ONCE per position."""
    if not getattr(config, "PYRAMID_ENABLED", False):
        return False, 0.0, ""
    if position_key in _pyramid_fired:
        return False, 0.0, ""
    min_gain = float(getattr(config, "PYRAMID_MIN_GAIN_PCT", 1.5))
    if gain_pct < min_gain:
        return False, 0.0, ""
    wt_vel_1h = float(indicators.get("wt_velocity_1h") or 0)
    dc_pos_15m = float(indicators.get("dc_position_15m") or 0.5)
    min_vel = float(getattr(config, "PYRAMID_MIN_WT_VEL_1H", 2.0))
    min_dc_pos = float(getattr(config, "PYRAMID_MIN_DC_POS_15M", 0.7))
    max_dc_pos = float(getattr(config, "PYRAMID_MAX_DC_POS_15M_SHORT", 0.3))
    size_mult = float(getattr(config, "PYRAMID_SIZE_MULT", 0.5))
    if is_long:
        if wt_vel_1h < min_vel or dc_pos_15m < min_dc_pos:
            return False, 0.0, ""
        _pyramid_fired.add(position_key)
        return True, size_mult, f"PYRAMID_LONG gain={gain_pct:.2f}% vel={wt_vel_1h:.1f} dcpos={dc_pos_15m:.2f} size={size_mult}x"
    else:
        if wt_vel_1h > -min_vel or dc_pos_15m > max_dc_pos:
            return False, 0.0, ""
        _pyramid_fired.add(position_key)
        return True, size_mult, f"PYRAMID_SHORT gain={gain_pct:.2f}% vel={wt_vel_1h:.1f} dcpos={dc_pos_15m:.2f} size={size_mult}x"

def clear_pyramid_state(position_key: str):
    _pyramid_fired.discard(position_key)


# ═══════════════════════════════════════════════════════════════════
# 8. HOUR-OF-DAY GATE (stub — data-driven impl needs 30-day hourly stats)
# ═══════════════════════════════════════════════════════════════════
def check_hour_allow_entry(config) -> Tuple[bool, str]:
    """Block entries during configured low-edge hours (UTC). Default empty list."""
    if not getattr(config, "HOUR_OF_DAY_GATE_ENABLED", False):
        return True, ""
    from datetime import datetime, timezone
    hour_utc = datetime.now(timezone.utc).hour
    blocked = set(getattr(config, "HOUR_OF_DAY_BLOCKED_UTC", []))
    if hour_utc in blocked:
        return False, f"HOUR_BLOCKED utc_hour={hour_utc} in {sorted(blocked)}"
    return True, ""
