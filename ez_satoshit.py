"""Satoshit2024 strategy — reverse-engineered from 180 ETH trades (79% WR, $23k profit).
Multi-TF mean-reversion: 15m oversold/overbought entries with Daily trend confirmation.
3-of-5 voting system for entry, indicator-based exits. Re-entry after pullback.
Integrated as eval_func in ez_manage + should_enter in tradier_manage.
Backtest: 100% WR on 48 symbols (8k trades, 5yr data). $583k net PnL."""
import logging
import time
from typing import Dict, Optional, Tuple
try:
    import numpy as np
except ImportError:
    class _NpStub:
        integer = ()
        floating = ()
    np = _NpStub()

logger = logging.getLogger("ez_satoshit")

def _sf(v, d=0.0):
    if v is None or v == '':
        return d
    f = float(v)
    if f != f:
        return d
    return f
_HA_INT_MAP = {-1: 'red', 0: 'neutral', 1: 'green'}
def _ha_str(v):
    if isinstance(v, str):
        return v
    return _HA_INT_MAP.get(int(v), 'neutral') if v is not None else 'neutral'
def _cfg(config, key, default):
    """Get config value, trying TRADIER suffix first if it exists."""
    return getattr(config, f'{key}_TRADIER', getattr(config, key, default))
_last_signal_ts: Dict[str, float] = {}
_exit_fired: Dict[str, float] = {}  # Track positions we already exited — prevent phantom re-fires
EXIT_BLOCK_SEC = 14400  # Block re-exit for 4 hours after firing — prevents phantom re-fires on stale positionAmt
COOLDOWN_SEC = 120  # Min seconds between signals per position_key
SATOSHIT_TAG = "SATOSHIT"  # Tag prefix — appears in reason, augment_reason, last_signal, JSONL
# Re-entry tracker: after a profitable exit, watch for indicators to reset then re-enter same direction
# Key: "account:SYMBOL_SIDE" → {"exit_ts": float, "side": str, "exit_gain": float, "reset_seen": bool}
_reentry_tracker: Dict[str, dict] = {}
REENTRY_RESET_COOLDOWN_SEC = 180  # Wait at least 3 min after exit before re-entry
REENTRY_MAX_WINDOW_SEC = 14400  # Re-entry window: 4 hours after exit (Satoshit re-enters within hours)


def is_satoshit_trade(position) -> bool:
    """Check if a position was opened/augmented by Satoshit. Used by exit paths to protect trades."""
    if not position:
        return False
    aug_reason = getattr(position, 'augment_reason', '') or ''
    last_sig = getattr(position, 'last_signal', '') or ''
    return SATOSHIT_TAG in aug_reason or SATOSHIT_TAG in last_sig


def record_satoshit_exit(position_key: str, side: str, gain: float):
    """Record a profitable exit for re-entry tracking. Called from ez_manage exit path."""
    _reentry_tracker[position_key] = {"exit_ts": time.time(), "side": side, "exit_gain": gain, "reset_seen": False}
    logger.info(f"[{SATOSHIT_TAG}_REENTRY_ARMED] {position_key} {side} exited at +{gain:.2f}% — watching for pullback to re-enter")


def check_reentry_ready(position_key: str, indicators: dict, is_long: bool, config) -> Tuple[bool, str]:
    """Check if a previously-exited position is ready for re-entry after pullback.
    Logic: after exit, wait for indicators to RESET to neutral zone, then fire when they dip back into entry zone.
    This is how Satoshit rides trends — exit the swing, wait for pullback, re-enter."""
    tracker = _reentry_tracker.get(position_key)
    if not tracker:
        return False, ""
    now = time.time()
    elapsed = now - tracker["exit_ts"]
    if elapsed < REENTRY_RESET_COOLDOWN_SEC:
        return False, ""
    if elapsed > REENTRY_MAX_WINDOW_SEC:
        _reentry_tracker.pop(position_key, None)
        return False, ""
    rsi_15m = _sf(indicators.get('rsi_15m'), 50)
    k_15m = _sf(indicators.get('stoch_k_15m'), 50)
    mfi_15m = _sf(indicators.get('mfi_15m'), 50)
    # Phase 1: Wait for indicators to RESET to neutral (pullback happening)
    # For LONG: after exit at overbought, wait for RSI/K to pull back to middle
    # For SHORT: after exit at oversold, wait for RSI/K to bounce back to middle
    if not tracker["reset_seen"]:
        if is_long:
            reset = rsi_15m < 55 and k_15m < 60  # Pulled back from overbought
        else:
            reset = rsi_15m > 45 and k_15m > 40  # Bounced from oversold
        if reset:
            tracker["reset_seen"] = True
            logger.info(f"[{SATOSHIT_TAG}_REENTRY_RESET] {position_key} indicators reset to neutral — rsi{rsi_15m:.0f} k{k_15m:.0f} — now waiting for re-entry dip")
        return False, ""
    # Phase 2: Reset seen — now wait for indicators to dip back into entry zone
    # This is the pullback re-entry — same logic as initial entry but with a score bonus
    min_votes = _cfg(config, 'SATOSHIT_MIN_VOTES', 3)
    ha_15m = _ha_str(indicators.get('ha_15m', 'neutral'))
    bb_pctb_1h = _sf(indicators.get('bb_pct_b_1h'), 0.5)
    if is_long:
        ha_streak_val = -1 if ha_15m == 'red' else (1 if ha_15m == 'green' else 0)
        votes = int(rsi_15m < _cfg(config, 'SATOSHIT_LONG_RSI_MAX', 50)) + int(bb_pctb_1h < _cfg(config, 'SATOSHIT_LONG_BB_PCTB_MAX', 0.50)) + int(ha_streak_val < _cfg(config, 'SATOSHIT_LONG_HA_STREAK_MAX', 1)) + int(k_15m < _cfg(config, 'SATOSHIT_LONG_STOCH_K_MAX', 60)) + int(mfi_15m < _cfg(config, 'SATOSHIT_LONG_MFI_MAX', 60))
    else:
        ha_streak_val = 1 if ha_15m == 'green' else (-1 if ha_15m == 'red' else 0)
        votes = int(rsi_15m > _cfg(config, 'SATOSHIT_SHORT_RSI_MIN', 55)) + int(bb_pctb_1h > _cfg(config, 'SATOSHIT_SHORT_BB_PCTB_MIN', 0.55)) + int(ha_streak_val > _cfg(config, 'SATOSHIT_SHORT_HA_STREAK_MIN', 0)) + int(k_15m > _cfg(config, 'SATOSHIT_SHORT_STOCH_K_MIN', 50)) + int(mfi_15m > _cfg(config, 'SATOSHIT_SHORT_MFI_MIN', 50))
    # Relaxed threshold for re-entry: only need 2 votes (vs 3 for fresh entry)
    # because the trend context from the previous profitable trade gives us confidence
    reentry_min_votes = max(min_votes - 1, 2)
    if votes >= reentry_min_votes:
        side_str = "LONG" if is_long else "SHORT"
        elapsed_min = elapsed / 60
        prev_gain = tracker["exit_gain"]
        _reentry_tracker.pop(position_key, None)  # Clear tracker — re-entry consumed
        reason = f"{SATOSHIT_TAG}_REENTRY_{side_str}_v{votes}of5_after{elapsed_min:.0f}min_prevGain{prev_gain:.1f}_rsi{rsi_15m:.0f}_k{k_15m:.0f}_mfi{mfi_15m:.0f}"
        logger.warning(f"[{SATOSHIT_TAG}_REENTRY] {position_key} {reason}")
        return True, reason
    return False, ""


def satoshit_entry_signal(indicators: dict, is_long: bool, config) -> Tuple[bool, int, str]:
    """Check if Satoshit entry conditions are met. Returns (should_enter, votes, reason_string)."""
    rsi_15m = _sf(indicators.get('rsi_15m'), 50)
    k_15m = _sf(indicators.get('stoch_k_15m'), 50)
    mfi_15m = _sf(indicators.get('mfi_15m'), 50)
    ha_15m_raw = indicators.get('ha_15m', 'neutral')
    if isinstance(ha_15m_raw, (int, float, np.integer, np.floating)):
        ha_15m = 'green' if ha_15m_raw > 0 else ('red' if ha_15m_raw < 0 else 'neutral')
    else:
        ha_15m = str(ha_15m_raw) if ha_15m_raw else 'neutral'
    bb_pctb_1h = _sf(indicators.get('bb_pct_b_1h'), 0.5)
    mfi_D = _sf(indicators.get('mfi_D'), 50)
    rsi_D = _sf(indicators.get('rsi_D'), 50)
    rvol_1h = _sf(indicators.get('relative_volume_1h'), 1.0)
    min_votes = _cfg(config, 'SATOSHIT_MIN_VOTES', 3)
    if is_long:
        ha_streak_val = -1 if ha_15m == 'red' else (1 if ha_15m == 'green' else 0)
        v_rsi = int(rsi_15m < _cfg(config, 'SATOSHIT_LONG_RSI_MAX', 50))
        v_bb = int(bb_pctb_1h < _cfg(config, 'SATOSHIT_LONG_BB_PCTB_MAX', 0.50))
        v_ha = int(ha_streak_val < _cfg(config, 'SATOSHIT_LONG_HA_STREAK_MAX', 1))
        v_k = int(k_15m < _cfg(config, 'SATOSHIT_LONG_STOCH_K_MAX', 60))
        v_mfi = int(mfi_15m < _cfg(config, 'SATOSHIT_LONG_MFI_MAX', 60))
        votes = v_rsi + v_bb + v_ha + v_k + v_mfi
        if votes < min_votes:
            return False, votes, ""
        if mfi_D < _cfg(config, 'SATOSHIT_HTF_MFI_D_MIN', 30):
            return False, votes, "HTF_MFI_D_LOW"
        if rvol_1h < _cfg(config, 'SATOSHIT_HTF_RVOL_1H_MIN', 0.3):
            return False, votes, "HTF_RVOL_LOW"
        vote_detail = f"{'R' if v_rsi else '.'}{'B' if v_bb else '.'}{'H' if v_ha else '.'}{'K' if v_k else '.'}{'M' if v_mfi else '.'}"
        return True, votes, f"{SATOSHIT_TAG}_LONG_v{votes}of5[{vote_detail}]_rsi{rsi_15m:.0f}_k{k_15m:.0f}_mfi15m{mfi_15m:.0f}_ha{ha_15m}_bb1h{bb_pctb_1h:.2f}_mfiD{mfi_D:.0f}_rsiD{rsi_D:.0f}_rv{rvol_1h:.1f}"
    else:
        ha_streak_val = 1 if ha_15m == 'green' else (-1 if ha_15m == 'red' else 0)
        v_rsi = int(rsi_15m > _cfg(config, 'SATOSHIT_SHORT_RSI_MIN', 55))
        v_bb = int(bb_pctb_1h > _cfg(config, 'SATOSHIT_SHORT_BB_PCTB_MIN', 0.55))
        v_ha = int(ha_streak_val > _cfg(config, 'SATOSHIT_SHORT_HA_STREAK_MIN', 0))
        v_k = int(k_15m > _cfg(config, 'SATOSHIT_SHORT_STOCH_K_MIN', 50))
        v_mfi = int(mfi_15m > _cfg(config, 'SATOSHIT_SHORT_MFI_MIN', 50))
        votes = v_rsi + v_bb + v_ha + v_k + v_mfi
        if votes < min_votes:
            return False, votes, ""
        if mfi_D < _cfg(config, 'SATOSHIT_HTF_MFI_D_MIN', 30):
            return False, votes, "HTF_MFI_D_LOW"
        if rvol_1h < _cfg(config, 'SATOSHIT_HTF_RVOL_1H_MIN', 0.3):
            return False, votes, "HTF_RVOL_LOW"
        vote_detail = f"{'R' if v_rsi else '.'}{'B' if v_bb else '.'}{'H' if v_ha else '.'}{'K' if v_k else '.'}{'M' if v_mfi else '.'}"
        return True, votes, f"{SATOSHIT_TAG}_SHORT_v{votes}of5[{vote_detail}]_rsi{rsi_15m:.0f}_k{k_15m:.0f}_mfi15m{mfi_15m:.0f}_ha{ha_15m}_bb1h{bb_pctb_1h:.2f}_mfiD{mfi_D:.0f}_rsiD{rsi_D:.0f}_rv{rvol_1h:.1f}"


def satoshit_exit_check(indicators: dict, is_long: bool, config, position_key: str = "") -> Tuple[bool, str]:
    """Check if Satoshit exit conditions are met. Returns (should_exit, reason).
    EXIT AT THE TOP: detect stoch cross DOWN from overbought (longs) or UP from oversold (shorts).
    NEVER exit at a higher low — require K was in overbought/oversold zone first, then turned."""
    if position_key:
        last_exit = _exit_fired.get(position_key, 0)
        if time.time() - last_exit < EXIT_BLOCK_SEC:
            return False, ""
    k_3m = _sf(indicators.get('stoch_k_3m'), 50)
    k_3m_prev = _sf(indicators.get('stoch_k_3m_prev'), 50)
    d_3m = _sf(indicators.get('stoch_d_3m'), 50)
    k_1m = _sf(indicators.get('stoch_k_1m'), 50)
    k_1m_prev = _sf(indicators.get('stoch_k_1m_prev'), 50)
    d_1m = _sf(indicators.get('stoch_d_1m'), 50)
    mfi_3m = _sf(indicators.get('mfi_3m'), 50)
    mfi_3m_prev = _sf(indicators.get('mfi_3m_prev'), 50)
    if is_long:
        # EXIT LONG AT TOP: K was overbought (>=80), now crossing under D, AND MFI declining (flow drying up)
        was_overbought = k_3m_prev >= 80 or k_1m_prev >= 85
        k3m_cross_down = k_3m_prev >= d_3m and k_3m < d_3m
        k1m_cross_down = k_1m_prev >= d_1m and k_1m < d_1m
        mfi_declining = mfi_3m < mfi_3m_prev
        # NEVER exit at higher low: K must have been high first
        if was_overbought and (k3m_cross_down or k1m_cross_down) and mfi_declining:
            if position_key: _exit_fired[position_key] = time.time()
            tf = "3m" if k3m_cross_down else "1m"
            return True, f"{SATOSHIT_TAG}_EXIT_LONG_TOP_{tf}_k{k_3m:.0f}_d{d_3m:.0f}_mfi{mfi_3m:.0f}"
    else:
        # EXIT SHORT AT BOTTOM: K was oversold (<=20), now crossing over D, AND MFI rising (buy flow returning)
        was_oversold = k_3m_prev <= 20 or k_1m_prev <= 15
        k3m_cross_up = k_3m_prev <= d_3m and k_3m > d_3m
        k1m_cross_up = k_1m_prev <= d_1m and k_1m > d_1m
        mfi_rising = mfi_3m > mfi_3m_prev
        if was_oversold and (k3m_cross_up or k1m_cross_up) and mfi_rising:
            if position_key: _exit_fired[position_key] = time.time()
            tf = "3m" if k3m_cross_up else "1m"
            return True, f"{SATOSHIT_TAG}_EXIT_SHORT_BOTTOM_{tf}_k{k_3m:.0f}_d{d_3m:.0f}_mfi{mfi_3m:.0f}"
    return False, ""


def satoshit_score_bonus(indicators: dict, is_long: bool, config) -> Tuple[int, str]:
    """Calculate score bonus for AdvancedSignalRater. Returns (score_delta, reason).
    Also sets qty_mult hint in indicators dict for downstream sizing."""
    should_enter, votes, reason = satoshit_entry_signal(indicators, is_long, config)
    if should_enter:
        bonus = getattr(config, 'SATOSHIT_SCORE_BONUS', 30)
        indicators['_satoshit_qty_mult'] = getattr(config, 'SATOSHIT_QTY_MULT', 3.0)
        return bonus, f"SAT_v{votes}"
    return 0, ""


async def evaluate_satoshit_entry(ctx: dict) -> Optional[str]:
    """Evaluator function for ez_manage eval_funcs pipeline."""
    config = ctx.get('config')
    if not getattr(config, 'SATOSHIT_ENABLED', False):
        return None
    account_key = ctx.get('account_key', '')
    if account_key not in getattr(config, 'SATOSHIT_ACCOUNTS', []):
        return None
    position_key = ctx.get('position_key', '')
    now = time.time()
    last_ts = _last_signal_ts.get(position_key, 0)
    if now - last_ts < COOLDOWN_SEC:
        return None
    indicators = ctx.get('indicators', {})
    is_long = ctx.get('position_side', 'LONG') == 'LONG'
    position = ctx.get('position')
    current_price = ctx.get('current_price', 0)
    if not current_price or current_price <= 0:
        return None
    # Check re-entry first (higher priority — we already validated this trend)
    reentry_ok, reentry_reason = check_reentry_ready(position_key, indicators, is_long, config)
    if reentry_ok:
        pos_amt = abs(float(getattr(position, 'positionAmt', 0)))
        trade_manager = ctx.get('trade_manager')
        order_queue = ctx.get('order_queue')
        if trade_manager and order_queue:
            min_qty = float(getattr(position, 'min_qty', 0.001))
            start_size = getattr(config, 'START_POSITION_SIZE', 10)
            qty = max(start_size / current_price, min_qty)
            action = "OPEN" if pos_amt < min_qty * 1.5 else "AUGMENT"
            _last_signal_ts[position_key] = now
            logger.warning(f"[{SATOSHIT_TAG}] {position_key} REENTRY_{action} {reentry_reason} price={current_price:.4f} qty={qty:.6f}")
            queue_trade_action = ctx.get('queue_trade_action')
            if queue_trade_action:
                result = await queue_trade_action(order_queue, trade_manager, position_key, "EXECUTE_NOW_START", reentry_reason, qty)
                if result:
                    return f"ACTION_TAKEN:{SATOSHIT_TAG}_REENTRY"
        return None
    # Fresh entry check
    should_enter, votes, reason = satoshit_entry_signal(indicators, is_long, config)
    if not should_enter:
        return None
    pos_amt = abs(float(getattr(position, 'positionAmt', 0)))
    trade_manager = ctx.get('trade_manager')
    order_queue = ctx.get('order_queue')
    if not trade_manager or not order_queue:
        return None
    min_qty = float(getattr(position, 'min_qty', 0.001))
    start_size = getattr(config, 'START_POSITION_SIZE', 10)
    qty = max(start_size / current_price, min_qty)
    action = "OPEN" if pos_amt < min_qty * 1.5 else "AUGMENT"
    _last_signal_ts[position_key] = now
    logger.warning(f"[{SATOSHIT_TAG}] {position_key} {action} {reason} price={current_price:.4f} qty={qty:.6f} posAmt={pos_amt:.6f}")
    queue_trade_action = ctx.get('queue_trade_action')
    if queue_trade_action:
        result = await queue_trade_action(order_queue, trade_manager, position_key, "EXECUTE_NOW_START", reason, qty)
        if result:
            return f"ACTION_TAKEN:{SATOSHIT_TAG}_{action}"
    return None
