"""
wt_dc_delta.py — WT/DC Delta Speed Module (LIVE version)
=========================================================
Computes bar-to-bar DELTA of ALL WT and DC metrics from the live indicators dict.
No NPZ loading. No z-score history. No RAM bloat.

Uses the raw deltas (current - prev) already available in the indicators dict.
Counts how many TFs have speed ramping in the same direction.

LIVE USAGE:
    from wt_dc_delta import DeltaTracker
    tracker = DeltaTracker()
    signal = tracker.update(symbol, indicators_dict, position_state)
"""
import numpy as np
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

TFS = ["3m", "15m", "1h", "4h", "D"]

# All WT float fields that have meaningful bar-to-bar deltas
WT_DELTA_FIELDS = [
    "wt1", "wt2", "wt_score", "wt_velocity", "wt_acceleration",
    "wt_percentile", "wt_zscore",
]
# DC float fields with _prev counterparts
DC_DELTA_FIELDS = ["dc_basis", "dc_high", "dc_low"]
# DC fields we compute delta ourselves (no _prev in live data)
DC_POSITION_FIELDS = ["dc_position", "dc_width"]

DEFAULT_CFG = {
    "tf_weights": {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5},
    # Entry: STRICT — 4+ TFs must have speed in same direction
    "entry_min_tf": 4,
    "entry_speed_threshold": 0.5,
    # Exit: LOOSE — 2 TFs speed dying is enough
    "exit_speed_decay_pct": 50.0,
    "exit_min_tf_lost": 2,
    "exit_min_hold": 4,
    # Pyramid
    "pyramid_min_tf": 4,
    "pyramid_price_tolerance": 0.02,
    "pyramid_qty_mult": 1.5,
    "pyramid_max": 8,
}


class DeltaSignal:
    __slots__ = [
        'bull_speed', 'bear_speed', 'bull_tf_count', 'bear_tf_count',
        'entry_long', 'entry_short', 'exit_long', 'exit_short',
        # Two-phase exit: phase 1 = pending (signal to exit, wait for better price),
        # phase 2 = exit_long/exit_short (execute now — bounce happened)
        'exit_pending_long', 'exit_pending_short',
        'pyramid_long', 'pyramid_short', 'post_consolidation',
        'tf_bull', 'tf_bear', 'total_fields',
        # Bar-to-bar acceleration flags (exposed for hedge entry/exit gates)
        'bull_accel', 'bear_accel',
        # Exit diagnostics — populated when exit_long/exit_short fires
        'tf_lost',
        # RED ZONE signals — structural level assessments
        'zone', 'zone_action', 'zone_reason', 'zone_legs_remaining',
        'bb_pctb_1h', 'bb_pctb_4h', 'dc_position_1h', 'dc_position_4h',
        'wt_nearing_cross', 'wt_cross_level_vs_prev',
        'temp_key_request',
        # Reentry flag: True if momentum still strong after exit (k_15m > d_15m)
        'reentry_if_momentum',
    ]
    def __init__(self):
        self.bull_speed = 0.0
        self.bear_speed = 0.0
        self.bull_tf_count = 0
        self.bear_tf_count = 0
        self.entry_long = False
        self.entry_short = False
        self.exit_long = False
        self.exit_short = False
        self.exit_pending_long = False
        self.exit_pending_short = False
        self.pyramid_long = False
        self.pyramid_short = False
        self.post_consolidation = False
        self.tf_bull = {}
        self.tf_bear = {}
        self.total_fields = 0
        self.bull_accel = False
        self.bear_accel = False
        self.tf_lost = ""  # populated by exit logic with diagnostic string
        # RED ZONE fields
        self.zone = "NEUTRAL"       # BASELINE / TOP / BOTTOM / TRANSIT
        self.zone_action = "HOLD"   # BUY / SELL / EXIT_LONG / EXIT_SHORT / HOLD
        self.temp_key_request = None  # {"symbol", "side", "reason", "max_age_s"} or None
        self.reentry_if_momentum = False  # True = k_15m still > d_15m after exit → reenter immediately
        self.zone_reason = ""
        self.zone_legs_remaining = 0.0  # 0-100: how much room left in current direction
        self.bb_pctb_1h = 0.5
        self.bb_pctb_4h = 0.5
        self.dc_position_1h = 0.5
        self.dc_position_4h = 0.5
        self.wt_nearing_cross = False
        self.wt_cross_level_vs_prev = 0.0  # positive = higher cross than last, negative = lower


class TempKeyManager:
    """Manages temporary tradeable_keys for counter-trend trades from red zone signals.
    Unlike hedges: can exist without an open opposite position, auto-expire on close or timeout.
    Unlike permanent keys: removed from tradeable_keys immediately on position closure."""
    def __init__(self, max_age_default=14400):  # 4h default max age
        self._keys = {}  # "account:SYMBOL_SIDE" -> {created_at, max_age_s, reason, zone, closed}
        self._max_age_default = max_age_default

    def request(self, account_key, symbol, side, reason, max_age_s=None):
        """Request a temporary key. Returns the full key string."""
        pk = f"{account_key}:{symbol}_{side}"
        if pk in self._keys and not self._keys[pk].get("closed"):
            return pk  # Already active
        import time
        self._keys[pk] = {
            "created_at": time.time(),
            "max_age_s": max_age_s or self._max_age_default,
            "reason": reason,
            "side": side,
            "symbol": symbol,
            "account": account_key,
            "closed": False,
        }
        logger.info(f"[TEMP_KEY] CREATED {pk}: {reason} (max_age={max_age_s or self._max_age_default}s)")
        return pk

    def on_position_closed(self, position_key):
        """Called when ANY position closes. If it was a temp key position, remove it."""
        if position_key in self._keys:
            self._keys[position_key]["closed"] = True
            logger.info(f"[TEMP_KEY] CLOSED {position_key}: auto-removing temp key")

    def get_active_keys(self, account_key=None):
        """Return list of active (non-expired, non-closed) temp keys."""
        import time
        now = time.time()
        active = []
        expired = []
        for pk, info in self._keys.items():
            if info.get("closed"):
                expired.append(pk)
                continue
            age = now - info["created_at"]
            if age > info["max_age_s"]:
                expired.append(pk)
                logger.info(f"[TEMP_KEY] EXPIRED {pk}: age={age:.0f}s > max={info['max_age_s']}s")
                continue
            if account_key and info.get("account") != account_key:
                continue
            active.append(pk)
        for pk in expired:
            del self._keys[pk]
        return active

    def is_temp_key(self, position_key):
        return position_key in self._keys and not self._keys[position_key].get("closed")

    def get_info(self, position_key):
        return self._keys.get(position_key)

    def get_all_symbols(self, account_key):
        """Return set of symbols with active temp keys for an account."""
        return {info["symbol"] for pk, info in self._keys.items()
                if info.get("account") == account_key and not info.get("closed")}


class DeltaTracker:
    def __init__(self, cfg=None):
        self.cfg = {**DEFAULT_CFG, **(cfg or {})}
        self._prev = defaultdict(dict)  # symbol -> {field: prev_value}
        self._max_speed = defaultdict(float)  # symbol -> max speed seen in current position
        self.temp_keys = TempKeyManager()  # Shared temp key manager

    def update(self, symbol, indicators, position_state=None):
        """
        Compute delta signal from current indicators vs previous bar.
        indicators: dict from get_hot_state() combined_data — has ALL WT+DC fields
        position_state: {side, max_speed, n_entries, last_entry_price} or None
        """
        cfg = self.cfg
        sig = DeltaSignal()
        prev = self._prev[symbol]
        tw = cfg["tf_weights"]
        _tfs = list(tw.keys()) if tw else TFS  # Use TFs from config weights (supports 5m for stocks)
        tf_bull = {tf: 0.0 for tf in _tfs}
        tf_bear = {tf: 0.0 for tf in _tfs}
        tf_count = {tf: 0 for tf in _tfs}
        total_fields = 0
        # Only update prev dict when bar actually changes (prevents delta=0 on repeated calls in same bar)
        _cur_bar_ts = _safe_float(indicators.get("_tick_ts")) or _safe_float(indicators.get("ts")) or 0
        _last_bar_ts = prev.get("_last_bar_ts", 0)
        _update_prev = _cur_bar_ts != _last_bar_ts
        prev["_last_bar_ts"] = _cur_bar_ts

        # Compute deltas for ALL WT fields across ALL TFs
        for tf in _tfs:
            for field in WT_DELTA_FIELDS:
                k = f"{field}_{tf}"
                cur = _safe_float(indicators.get(k))
                if cur is None:
                    continue
                # Use _prev from data if available, else our stored prev
                prev_k = f"{field}_prev_{tf}"  # some fields have this
                prv = _safe_float(indicators.get(prev_k))
                if prv is None:
                    prv = prev.get(k)
                if prv is not None:
                    delta = cur - prv
                    if delta > 0:
                        tf_bull[tf] += delta
                    else:
                        tf_bear[tf] += -delta
                    tf_count[tf] += 1
                    total_fields += 1
                if _update_prev:
                    prev[k] = cur

            # DC fields with _prev (uses indicator _prev, not our stored prev)
            for field in DC_DELTA_FIELDS:
                k = f"{field}_{tf}"
                k_prev = f"{field}_{tf}_prev"
                cur = _safe_float(indicators.get(k))
                prv = _safe_float(indicators.get(k_prev))
                if cur is not None and prv is not None:
                    delta = cur - prv
                    if delta > 0:
                        tf_bull[tf] += delta
                    else:
                        tf_bear[tf] += -delta
                    tf_count[tf] += 1
                    total_fields += 1

            # DC position/width — compute delta from our stored prev
            for field in DC_POSITION_FIELDS:
                k = f"{field}_{tf}"
                cur = _safe_float(indicators.get(k))
                if cur is not None:
                    prv = prev.get(k)
                    if prv is not None:
                        delta = cur - prv
                        if delta > 0:
                            tf_bull[tf] += delta
                        else:
                            tf_bear[tf] += -delta
                        tf_count[tf] += 1
                        total_fields += 1
                    if _update_prev:
                        prev[k] = cur

            # WT int fields that change meaningfully
            for field in ["wt_bullish", "wt_cross_bull", "wt_cross_bear", "wt_momentum_state"]:
                k = f"{field}_{tf}"
                cur = _safe_float(indicators.get(k))
                if cur is not None:
                    prv = prev.get(k)
                    if prv is not None:
                        delta = cur - prv
                        if delta > 0:
                            tf_bull[tf] += delta
                        else:
                            tf_bear[tf] += -delta
                        tf_count[tf] += 1
                        total_fields += 1
                    if _update_prev:
                        prev[k] = cur

        # Normalize per TF and compute weighted total
        total_bull = 0.0
        total_bear = 0.0
        bull_tf_count = 0
        bear_tf_count = 0
        speed_thresh = cfg.get("entry_speed_threshold", 0.5)

        for tf in _tfs:
            c = tf_count[tf]
            if c > 0:
                tf_bull[tf] /= c
                tf_bear[tf] /= c
            w = tw.get(tf, 1.0)
            total_bull += tf_bull[tf] * w
            total_bear += tf_bear[tf] * w
            if tf_bull[tf] > speed_thresh:
                bull_tf_count += 1
            if tf_bear[tf] > speed_thresh:
                bear_tf_count += 1

        sig.tf_bull = tf_bull
        sig.tf_bear = tf_bear
        sig.bull_speed = total_bull
        sig.bear_speed = total_bear
        sig.bull_tf_count = bull_tf_count
        sig.bear_tf_count = bear_tf_count
        sig.total_fields = total_fields

        # --- BAR-TO-BAR ACCELERATION (exposed for hedge entry/exit gates) ---
        # Computed on every update, unconditional of zone. Separate namespace from _rz_prev_* so existing BASELINE zone logic is untouched.
        _hedge_prev_bull = prev.get("_hedge_prev_total_bull", 0.0)
        _hedge_prev_bear = prev.get("_hedge_prev_total_bear", 0.0)
        sig.bull_accel = total_bull > _hedge_prev_bull * 1.05
        sig.bear_accel = total_bear > _hedge_prev_bear * 1.05
        if _update_prev:
            prev["_hedge_prev_total_bull"] = total_bull
            prev["_hedge_prev_total_bear"] = total_bear

        # --- DELTA SPEED is computed above (total_bull/bear, tf counts) ---
        # Entry decision is made in RED ZONE section below using these delta values + zone context.
        min_tf = cfg["entry_min_tf"]

        # ═══ EXIT: rate-of-change slowdown — REWRITTEN 2026-04-10 ═══
        # User rule: delta MUST measure how fast dc_position/wt1 rises per bar.
        # NO _min_meaningful_speed gate — if speed is low, that's the signal:
        # the position has STOPPED moving in our direction → exit it.
        # Combined with MFI: low volume + dead delta = guaranteed exit.
        # The key insight: detect SLOWDOWN (rate of speed change) BEFORE crossunder.
        if position_state:
            side = position_state.get("side", "")
            max_spd = self._max_speed[symbol]

            # Track per-bar speed delta (acceleration of speed itself)
            _prev_cur_spd_long = prev.get("_prev_cur_spd_long", 0.0)
            _prev_cur_spd_short = prev.get("_prev_cur_spd_short", 0.0)
            _prev_cur_spd_long_2 = prev.get("_prev_cur_spd_long_2", 0.0)
            _prev_cur_spd_short_2 = prev.get("_prev_cur_spd_short_2", 0.0)

            # MFI volume gate — exit when delta dies AND volume is gone
            # Use 3m MFI as primary (CRYPTO LTF), fall back to 15m
            _mfi_3m = _safe_float(indicators.get("mfi_3m", 50)) or 50.0
            _mfi_15m = _safe_float(indicators.get("mfi_15m", 50)) or 50.0
            _mfi_low = (_mfi_3m < 40 or _mfi_15m < 40)  # low MFI = no volume backing the move

            # User-configurable thresholds
            _zero_speed_threshold = cfg.get("exit_zero_speed_threshold", 0.5)  # speed below this = "dead"
            _slowdown_pct = cfg.get("exit_slowdown_pct", 30) / 100.0  # speed dropped X% from prev bar
            _decel_bars = cfg.get("exit_decel_bars", 2)  # require N consecutive bars of slowing

            # ═══ DOMINANT-TF EXIT (2026-04-10) ═══
            # Per backtest WHALE+exit sweep: replacing combined `total_bull/bear` with the
            # 3m-only z-speed pushes WR from 84% → 88.7% (smoke test on 4yr × 48 symbols).
            # Default OFF (`_dom_tf="ANY"` → use combined as before). Set `DELTA_EXIT_DOM_TF`
            # to "3m", "15m", or "1h" to fire exits on that TF's slowdown alone.
            _dom_tf = str(getattr(cfg, "exit_dominant_tf", None) or cfg.get("exit_dominant_tf", "ANY") if isinstance(cfg, dict) else "ANY")
            if _dom_tf in tf_bull and _dom_tf in tf_bear:
                _dom_bull = tf_bull[_dom_tf]
                _dom_bear = tf_bear[_dom_tf]
            else:
                _dom_bull = total_bull
                _dom_bear = total_bear

            # ═══ EXPLICIT WT/DC DIRECTION FALLBACK CHECKS — user rule 2026-04-10 ═══
            # User: "you can NEVER lose 5% on a long while wt1_3m keeps rising and delta
            # never slowed down and dc_position never went down. THOSE need to be the exit
            # fallbacks not a hard 5%". So check direction of wt1_3m and dc_position_3m
            # explicitly per LTF (3m, 15m, 1h for crypto).
            _wt1_3m_now = _safe_float(indicators.get("wt1_3m"))
            _wt1_15m_now = _safe_float(indicators.get("wt1_15m"))
            _wt1_1h_now = _safe_float(indicators.get("wt1_1h"))
            _dc_pos_3m_now = _safe_float(indicators.get("dc_position_3m"))
            _dc_pos_15m_now = _safe_float(indicators.get("dc_position_15m"))
            _wt1_3m_prev = prev.get("_wt1_3m_for_exit")
            _wt1_15m_prev = prev.get("_wt1_15m_for_exit")
            _wt1_1h_prev = prev.get("_wt1_1h_for_exit")
            _dc_pos_3m_prev = prev.get("_dc_pos_3m_for_exit")
            _dc_pos_15m_prev = prev.get("_dc_pos_15m_for_exit")

            if side in ("LONG", "L"):
                cur_spd = _dom_bull
                opp_spd = _dom_bear
                self._max_speed[symbol] = max(max_spd, cur_spd)
                max_spd = self._max_speed[symbol]

                # 1. SPEED DEAD: current speed near zero — position has stopped moving up
                speed_dead = cur_spd < _zero_speed_threshold
                # 2. SPEED DECELERATING: 2 consecutive bars of slowdown (rate of change negative)
                slowing_now = cur_spd < _prev_cur_spd_long * (1 - _slowdown_pct)
                slowing_prev = _prev_cur_spd_long < _prev_cur_spd_long_2 * (1 - _slowdown_pct) if _prev_cur_spd_long_2 > 0 else False
                decelerating = slowing_now and (slowing_prev if _decel_bars >= 2 else True)
                # 3. OPPOSING: bear pressure exceeds bull
                opposing = opp_spd > cur_spd
                # 4. PEAK DECAY: dropped X% from peak (relative measure for fast-moving positions)
                _decay_pct_cfg = cfg["exit_speed_decay_pct"]
                peak_decay = cur_spd < max_spd * (1 - _decay_pct_cfg / 100) if max_spd > 0 else False
                # 5. TF LOST: bull TF count below threshold
                tf_lost = bull_tf_count < cfg["exit_min_tf_lost"]
                # 6. WT1_3M FALLING — explicit fallback per user rule
                wt1_3m_falling = (_wt1_3m_prev is not None and _wt1_3m_now is not None and _wt1_3m_now < _wt1_3m_prev)
                wt1_15m_falling = (_wt1_15m_prev is not None and _wt1_15m_now is not None and _wt1_15m_now < _wt1_15m_prev)
                wt1_1h_falling = (_wt1_1h_prev is not None and _wt1_1h_now is not None and _wt1_1h_now < _wt1_1h_prev)
                # 7. DC_POSITION FALLING — explicit fallback
                dc_pos_3m_falling = (_dc_pos_3m_prev is not None and _dc_pos_3m_now is not None and _dc_pos_3m_now < _dc_pos_3m_prev)
                dc_pos_15m_falling = (_dc_pos_15m_prev is not None and _dc_pos_15m_now is not None and _dc_pos_15m_now < _dc_pos_15m_prev)

                # ═══ TWO-PHASE EXIT (2026-04-11) ═══
                # User: "when overbought, use previous 3m candle low as the SIGN to get out,
                # but then wait for the NEXT 3m candle top to actually get out!"
                #
                # Phase 1 (PENDING): overbought + price < prev_3m_low → flag exit_pending
                # Phase 2 (EXECUTE): if pending from prev bar AND this bar made a top
                #         (green candle or high > prev_high) → execute exit at the top
                #
                # This REPLACES delta weakness counting for overbought zones.
                # For non-overbought zones, the delta weakness logic still applies.
                _ltf_micro = cfg.get("rz_ltf_micro", "3m")
                _rz_two_phase = cfg.get("rz_two_phase_exit_enabled", True)
                _cur_price = _safe_float(indicators.get("current_price", indicators.get("close", 0)))
                _prev_3m_low = _safe_float(indicators.get(f"low_{_ltf_micro}_prev", indicators.get("low_3m_prev")))
                _prev_3m_high = _safe_float(indicators.get(f"high_{_ltf_micro}_prev", indicators.get("high_3m_prev")))
                _cur_3m_high = _safe_float(indicators.get(f"high_{_ltf_micro}", indicators.get("high_3m")))
                _cur_3m_close = _safe_float(indicators.get(f"close_{_ltf_micro}", indicators.get("close_3m")))
                _cur_3m_open = _safe_float(indicators.get(f"open_{_ltf_micro}", indicators.get("open_3m")))
                _tp_bb_1h = _safe_float(indicators.get("bb_pct_b_1h")) or 0.5
                _tp_dc_pos_1h = _safe_float(indicators.get("dc_position_1h")) or 0.5
                _tp_k_1h = _safe_float(indicators.get("stoch_k_1h")) or 50.0
                _overbought = _tp_bb_1h > 0.85 or _tp_dc_pos_1h > 0.85 or _tp_k_1h > 80
                _was_pending = prev.get("_exit_pending_long", False) if _rz_two_phase else False
                # Phase 2: was pending last bar → check if this bar made a local top (bounce)
                if _was_pending:
                    _bar_is_green = _cur_3m_close > _cur_3m_open if _cur_3m_close and _cur_3m_open else False
                    _bar_higher_high = _cur_3m_high > _prev_3m_high if _cur_3m_high and _prev_3m_high else False
                    if _bar_is_green or _bar_higher_high:
                        sig.exit_long = True
                        sig.tf_lost = f"TWO_PHASE_EXECUTE green={_bar_is_green} hh={_bar_higher_high} bb={_tp_bb_1h:.2f} dc={_tp_dc_pos_1h:.2f} k={_tp_k_1h:.0f}"
                    else:
                        sig.exit_pending_long = True  # still pending — no bounce yet
                # Phase 1: overbought + price broke prev candle low → set pending
                elif _rz_two_phase and _overbought and _cur_price and _prev_3m_low and _cur_price < _prev_3m_low:
                    sig.exit_pending_long = True
                    prev["_exit_pending_long"] = True
                # Non-overbought: use delta weakness (2-of-N + strong directional)
                if not sig.exit_long and not sig.exit_pending_long:
                    _weakness_count = sum([
                        speed_dead, decelerating, peak_decay, opposing, tf_lost, _mfi_low,
                        wt1_3m_falling, dc_pos_3m_falling
                    ])
                    _strong_directional = (wt1_15m_falling and wt1_3m_falling) or (dc_pos_15m_falling and wt1_15m_falling)
                    sig.exit_long = (_weakness_count >= 2) or _strong_directional
                if sig.exit_long:
                    _triggers = []
                    if speed_dead: _triggers.append("DEAD")
                    if decelerating: _triggers.append("DECEL")
                    if peak_decay: _triggers.append("PEAK")
                    if opposing: _triggers.append("OPP")
                    if tf_lost: _triggers.append("TFLOST")
                    if _mfi_low: _triggers.append("MFI")
                    if wt1_3m_falling: _triggers.append("WT1_3M_DOWN")
                    if dc_pos_3m_falling: _triggers.append("DC_POS_3M_DOWN")
                    if wt1_15m_falling: _triggers.append("WT1_15M_DOWN")
                    if dc_pos_15m_falling: _triggers.append("DC_POS_15M_DOWN")
                    sig.tf_lost = (f"{'|'.join(_triggers)} cur={cur_spd:.2f} max={max_spd:.2f} "
                                   f"mfi3m={_mfi_3m:.0f} mfi15m={_mfi_15m:.0f}")

                if _update_prev:
                    prev["_prev_cur_spd_long_2"] = _prev_cur_spd_long
                    prev["_prev_cur_spd_long"] = cur_spd

            elif side in ("SHORT", "S"):
                cur_spd = _dom_bear
                opp_spd = _dom_bull
                self._max_speed[symbol] = max(max_spd, cur_spd)
                max_spd = self._max_speed[symbol]

                speed_dead = cur_spd < _zero_speed_threshold
                slowing_now = cur_spd < _prev_cur_spd_short * (1 - _slowdown_pct)
                slowing_prev = _prev_cur_spd_short < _prev_cur_spd_short_2 * (1 - _slowdown_pct) if _prev_cur_spd_short_2 > 0 else False
                decelerating = slowing_now and (slowing_prev if _decel_bars >= 2 else True)
                opposing = opp_spd > cur_spd
                _decay_pct_cfg = cfg["exit_speed_decay_pct"]
                peak_decay = cur_spd < max_spd * (1 - _decay_pct_cfg / 100) if max_spd > 0 else False
                tf_lost = bear_tf_count < cfg["exit_min_tf_lost"]
                # SHORT direction fallbacks: wt1_3m RISING + dc_position_3m RISING = exit short
                wt1_3m_rising = (_wt1_3m_prev is not None and _wt1_3m_now is not None and _wt1_3m_now > _wt1_3m_prev)
                wt1_15m_rising = (_wt1_15m_prev is not None and _wt1_15m_now is not None and _wt1_15m_now > _wt1_15m_prev)
                wt1_1h_rising = (_wt1_1h_prev is not None and _wt1_1h_now is not None and _wt1_1h_now > _wt1_1h_prev)
                dc_pos_3m_rising = (_dc_pos_3m_prev is not None and _dc_pos_3m_now is not None and _dc_pos_3m_now > _dc_pos_3m_prev)
                dc_pos_15m_rising = (_dc_pos_15m_prev is not None and _dc_pos_15m_now is not None and _dc_pos_15m_now > _dc_pos_15m_prev)

                # ═══ TWO-PHASE SHORT EXIT (mirror of long) ═══
                _ltf_micro = cfg.get("rz_ltf_micro", "3m")
                _cur_price = _safe_float(indicators.get("current_price", indicators.get("close", 0)))
                _prev_3m_high = _safe_float(indicators.get(f"high_{_ltf_micro}_prev", indicators.get("high_3m_prev")))
                _prev_3m_low = _safe_float(indicators.get(f"low_{_ltf_micro}_prev", indicators.get("low_3m_prev")))
                _cur_3m_low = _safe_float(indicators.get(f"low_{_ltf_micro}", indicators.get("low_3m")))
                _cur_3m_close = _safe_float(indicators.get(f"close_{_ltf_micro}", indicators.get("close_3m")))
                _cur_3m_open = _safe_float(indicators.get(f"open_{_ltf_micro}", indicators.get("open_3m")))
                _tp_bb_1h_s = _safe_float(indicators.get("bb_pct_b_1h")) or 0.5
                _tp_dc_pos_1h_s = _safe_float(indicators.get("dc_position_1h")) or 0.5
                _tp_k_1h_s = _safe_float(indicators.get("stoch_k_1h")) or 50.0
                _oversold = _tp_bb_1h_s < 0.15 or _tp_dc_pos_1h_s < 0.15 or _tp_k_1h_s < 20
                _rz_two_phase_s = cfg.get("rz_two_phase_exit_enabled", True)
                _was_pending_s = prev.get("_exit_pending_short", False) if _rz_two_phase_s else False
                # Phase 2: was pending → check if this bar made a local bottom (bounce down)
                if _was_pending_s:
                    _bar_is_red = _cur_3m_close < _cur_3m_open if _cur_3m_close and _cur_3m_open else False
                    _bar_lower_low = _cur_3m_low < _prev_3m_low if _cur_3m_low and _prev_3m_low else False
                    if _bar_is_red or _bar_lower_low:
                        sig.exit_short = True
                        sig.tf_lost = f"TWO_PHASE_EXECUTE red={_bar_is_red} ll={_bar_lower_low} bb={_tp_bb_1h_s:.2f} dc={_tp_dc_pos_1h_s:.2f} k={_tp_k_1h_s:.0f}"
                    else:
                        sig.exit_pending_short = True
                # Phase 1: oversold + price broke above prev candle high → set pending
                elif _rz_two_phase_s and _oversold and _cur_price and _prev_3m_high and _cur_price > _prev_3m_high:
                    sig.exit_pending_short = True
                    prev["_exit_pending_short"] = True
                # Non-oversold: use delta weakness
                if not sig.exit_short and not sig.exit_pending_short:
                    _weakness_count = sum([
                        speed_dead, decelerating, peak_decay, opposing, tf_lost, _mfi_low,
                        wt1_3m_rising, dc_pos_3m_rising
                    ])
                    _strong_directional = (wt1_15m_rising and wt1_3m_rising) or (dc_pos_15m_rising and wt1_15m_rising)
                    sig.exit_short = (_weakness_count >= 2) or _strong_directional
                if sig.exit_short:
                    _triggers = []
                    if speed_dead: _triggers.append("DEAD")
                    if decelerating: _triggers.append("DECEL")
                    if peak_decay: _triggers.append("PEAK")
                    if opposing: _triggers.append("OPP")
                    if tf_lost: _triggers.append("TFLOST")
                    if _mfi_low: _triggers.append("MFI")
                    if wt1_3m_rising: _triggers.append("WT1_3M_UP")
                    if dc_pos_3m_rising: _triggers.append("DC_POS_3M_UP")
                    if wt1_15m_rising: _triggers.append("WT1_15M_UP")
                    if dc_pos_15m_rising: _triggers.append("DC_POS_15M_UP")
                    sig.tf_lost = (f"{'|'.join(_triggers)} cur={cur_spd:.2f} max={max_spd:.2f} "
                                   f"mfi3m={_mfi_3m:.0f} mfi15m={_mfi_15m:.0f}")

                if _update_prev:
                    prev["_prev_cur_spd_short_2"] = _prev_cur_spd_short
                    prev["_prev_cur_spd_short"] = cur_spd

            # ═══ HTF DIVERGENCE EXIT — the hidden treasure (2026-04-11) ═══
            # wt_divergence on 1h/4h/D detects structural momentum failure:
            # price makes new highs but WT doesn't follow = reversal imminent.
            # 2+ HTFs with bearish divergence for LONG = two-phase exit trigger.
            # This catches tops that delta speed/MFI miss because price is still rising.
            _rz_div_exit_enabled = cfg.get("rz_div_exit_enabled", True)
            _rz_zscore_exit_enabled = cfg.get("rz_zscore_exit_enabled", True)
            if _rz_div_exit_enabled and not sig.exit_long and not sig.exit_pending_long and side in ("LONG", "L"):
                _div_bear_count = 0
                for _dtf in ["1h", "4h", "D"]:
                    _div_val = int(_safe_float(indicators.get(f"wt_divergence_{_dtf}")) or 0)
                    if _div_val == -1:  # bearish divergence
                        _div_bear_count += 1
                if _div_bear_count >= 2:
                    sig.exit_pending_long = True
                    prev["_exit_pending_long"] = True
                    sig.tf_lost = f"HTF_BEAR_DIV_{_div_bear_count}TF"
            if _rz_div_exit_enabled and not sig.exit_short and not sig.exit_pending_short and side in ("SHORT", "S"):
                _div_bull_count = 0
                for _dtf in ["1h", "4h", "D"]:
                    _div_val = int(_safe_float(indicators.get(f"wt_divergence_{_dtf}")) or 0)
                    if _div_val == 1:  # bullish divergence
                        _div_bull_count += 1
                if _div_bull_count >= 2:
                    sig.exit_pending_short = True
                    prev["_exit_pending_short"] = True
                    sig.tf_lost = f"HTF_BULL_DIV_{_div_bull_count}TF"

            # ═══ WT ZSCORE EXTREME EXIT (2026-04-11) ═══
            # z > 2.0 on 4h = 97.7th percentile = severely overbought (self-calibrating per symbol)
            # Better than fixed BB thresholds because it adapts to each symbol's volatility.
            if _rz_zscore_exit_enabled and not sig.exit_long and not sig.exit_pending_long and side in ("LONG", "L"):
                _zs_4h = _safe_float(indicators.get("wt_zscore_4h")) or 0
                _zs_1h = _safe_float(indicators.get("wt_zscore_1h")) or 0
                if _zs_4h > 2.0 or (_zs_1h > 2.0 and _zs_4h > 1.5):
                    sig.exit_pending_long = True
                    prev["_exit_pending_long"] = True
                    sig.tf_lost = f"ZSCORE_OVERBOUGHT_1h={_zs_1h:.1f}_4h={_zs_4h:.1f}"
            if _rz_zscore_exit_enabled and not sig.exit_short and not sig.exit_pending_short and side in ("SHORT", "S"):
                _zs_4h = _safe_float(indicators.get("wt_zscore_4h")) or 0
                _zs_1h = _safe_float(indicators.get("wt_zscore_1h")) or 0
                if _zs_4h < -2.0 or (_zs_1h < -2.0 and _zs_4h < -1.5):
                    sig.exit_pending_short = True
                    prev["_exit_pending_short"] = True
                    sig.tf_lost = f"ZSCORE_OVERSOLD_1h={_zs_1h:.1f}_4h={_zs_4h:.1f}"

            # Clear pending exit state when executed or no longer valid
            if sig.exit_long:
                prev["_exit_pending_long"] = False
            elif not sig.exit_pending_long:
                prev["_exit_pending_long"] = False
            if sig.exit_short:
                prev["_exit_pending_short"] = False
            elif not sig.exit_pending_short:
                prev["_exit_pending_short"] = False
            # Store wt1/dc_position values for next bar's direction comparison
            if _update_prev:
                if _wt1_3m_now is not None: prev["_wt1_3m_for_exit"] = _wt1_3m_now
                if _wt1_15m_now is not None: prev["_wt1_15m_for_exit"] = _wt1_15m_now
                if _wt1_1h_now is not None: prev["_wt1_1h_for_exit"] = _wt1_1h_now
                if _dc_pos_3m_now is not None: prev["_dc_pos_3m_for_exit"] = _dc_pos_3m_now
                if _dc_pos_15m_now is not None: prev["_dc_pos_15m_for_exit"] = _dc_pos_15m_now

            # --- PYRAMID ---
            n_ent = position_state.get("n_entries", 1)
            last_price = position_state.get("last_entry_price", 0)
            cur_price = _safe_float(indicators.get("current_price", indicators.get("close", 0)))
            if n_ent < cfg["pyramid_max"] and last_price and cur_price and cur_price > 0:
                pyr_tol = cfg["pyramid_price_tolerance"]
                pyr_min_tf = cfg.get("pyramid_min_tf", min_tf)
                if side in ("LONG", "L"):
                    price_ok = cur_price <= last_price * (1 + pyr_tol)
                    tf_ok = bull_tf_count >= pyr_min_tf
                    sig.pyramid_long = price_ok and tf_ok and total_bull > total_bear
                elif side in ("SHORT", "S"):
                    price_ok = cur_price >= last_price * (1 - pyr_tol)
                    tf_ok = bear_tf_count >= pyr_min_tf
                    sig.pyramid_short = price_ok and tf_ok and total_bear > total_bull

        # ═══════════════════════════════════════════════════════════════
        # RED ZONE ASSESSMENT — uses ALL existing ez_indicators fields
        # ═══════════════════════════════════════════════════════════════
        try:
            self._run_redzone(sig, indicators, prev, cfg, position_state, total_bull, total_bear)
        except Exception as _rz_err:
            import traceback
            _tb = traceback.format_exc().split("\n")
            # Find the _run_redzone frame to pinpoint the line
            _loc = next((l for l in _tb if "_run_redzone" in l and "line" in l), "unknown")
            logger.warning(f"[RZ_ERROR] {type(_rz_err).__name__}: {_rz_err} at {_loc.strip()}")

        if not cfg.get("entry_enabled", True):
            sig.entry_long = False
            sig.entry_short = False
        self._prev[symbol] = prev
        return sig

    def _run_redzone(self, sig, indicators, prev, cfg, position_state, total_bull, total_bear):
        """RED ZONE assessment — separated so exceptions don't kill the main update()."""
        tw = cfg.get("tf_weights", {})
        _tfs = list(tw.keys()) if tw else TFS
        _rz_call_count = getattr(self, '_rz_calls', 0) + 1
        self._rz_calls = _rz_call_count
        if _rz_call_count <= 3 or _rz_call_count % 2000 == 0:
            _sample_keys = ["bb_pct_b_1h", "dc_position_1h", "wt1_1h", "wt2_1h", "wt_velocity_1h", "stoch_k_1h", "mfi_1h"]
            _sample = {k: indicators.get(k) for k in _sample_keys}
            logger.info(f"[RZ_DIAG] call={_rz_call_count} sample={_sample}")
        _bb_1h = _safe_float(indicators.get("bb_pct_b_1h")) or 0.5
        _bb_4h = _safe_float(indicators.get("bb_pct_b_4h")) or 0.5
        _dc_pos_1h = _safe_float(indicators.get("dc_position_1h")) or 0.5
        _dc_pos_4h = _safe_float(indicators.get("dc_position_4h")) or 0.5
        # WT: cross values, structure, momentum — ALL from ez_indicators
        _wt1_1h = _safe_float(indicators.get("wt1_1h")) or 0.0
        _wt2_1h = _safe_float(indicators.get("wt2_1h")) or 0.0
        _wt_vel_1h = _safe_float(indicators.get("wt_velocity_1h")) or 0.0
        _wt_accel_1h = _safe_float(indicators.get("wt_acceleration_1h")) or 0.0
        _wt_mom_1h = int(_safe_float(indicators.get("wt_momentum_state_1h")) or 0)
        _wt_cross_1h = int(_safe_float(indicators.get("wt_cross_1h")) or 0)
        _wt_cross_val_1h = _safe_float(indicators.get("wt_cross_value_1h")) or 0.0
        _wt_cross_prev_val_1h = _safe_float(indicators.get("wt_cross_prev_value_1h")) or 0.0
        _wt_cross_rising_1h = int(_safe_float(indicators.get("wt_cross_rising_1h")) or 0)
        _wt_cross_bars_1h = _safe_float(indicators.get("wt_cross_bars_ago_1h")) or 999
        _wt_peak_1h = _safe_float(indicators.get("wt_peak_1h")) or 0.0
        _wt_peak_prev_1h = _safe_float(indicators.get("wt_peak_prev_1h")) or 0.0
        _wt_trough_1h = _safe_float(indicators.get("wt_trough_1h")) or 0.0
        _wt_trough_prev_1h = _safe_float(indicators.get("wt_trough_prev_1h")) or 0.0
        _wt_peak_struct_1h = int(_safe_float(indicators.get("wt_peak_structure_1h")) or 0)
        _wt_trough_struct_1h = int(_safe_float(indicators.get("wt_trough_structure_1h")) or 0)
        _wt_div_1h = int(_safe_float(indicators.get("wt_divergence_1h")) or 0)
        _wt_div_str_1h = _safe_float(indicators.get("wt_divergence_strength_1h")) or 0.0
        _wt_pct_1h = _safe_float(indicators.get("wt_percentile_1h")) or 50.0
        _wt_extreme_1h = int(_safe_float(indicators.get("wt_extreme_1h")) or 0) == 1
        _wt_wave_1h = int(_safe_float(indicators.get("wt_wave_phase_1h")) or 0)
        # 4h WT structure
        _wt_mom_4h = int(_safe_float(indicators.get("wt_momentum_state_4h")) or 0)
        _wt_peak_struct_4h = int(_safe_float(indicators.get("wt_peak_structure_4h")) or 0)
        _wt_trough_struct_4h = int(_safe_float(indicators.get("wt_trough_structure_4h")) or 0)
        _wt_div_4h = int(_safe_float(indicators.get("wt_divergence_4h")) or 0)
        _wt_wave_4h = int(_safe_float(indicators.get("wt_wave_phase_4h")) or 0)
        # Stoch + MFI for "legs remaining"
        _k_1h = _safe_float(indicators.get("stoch_k_1h")) or 50.0
        _k_4h = _safe_float(indicators.get("stoch_k_4h")) or 50.0
        _mfi_1h = _safe_float(indicators.get("mfi_1h")) or 50.0
        _mfi_4h = _safe_float(indicators.get("mfi_4h")) or 50.0
        # ALL DC values per TF — full structural picture
        _dc = {}
        for _ztf in ["3m", "15m", "1h", "4h", "D"]:
            _dc[_ztf] = {
                "high": _safe_float(indicators.get(f"dc_high_{_ztf}")) or 0,
                "low": _safe_float(indicators.get(f"dc_low_{_ztf}")) or 0,
                "basis": _safe_float(indicators.get(f"dc_basis_{_ztf}")) or 0,
                "width": _safe_float(indicators.get(f"dc_width_{_ztf}")) or 0,
                "position": _safe_float(indicators.get(f"dc_position_{_ztf}")) or 0.5,
                "high_prev": _safe_float(indicators.get(f"dc_high_{_ztf}_prev")) or 0,
                "low_prev": _safe_float(indicators.get(f"dc_low_{_ztf}_prev")) or 0,
                "basis_prev": _safe_float(indicators.get(f"dc_basis_{_ztf}_prev")) or 0,
                "high_ant": _safe_float(indicators.get(f"dc_high_{_ztf}_ant")) or 0,
                "low_ant": _safe_float(indicators.get(f"dc_low_{_ztf}_ant")) or 0,
                "high4": _safe_float(indicators.get(f"dc_high4_{_ztf}")) or 0,
                "low4": _safe_float(indicators.get(f"dc_low4_{_ztf}")) or 0,
                "basis_co": bool(indicators.get(f"dc_basis_crossover_{_ztf}")),
                "basis_cu": bool(indicators.get(f"dc_basis_crossunder_{_ztf}")),
                "high_co": bool(indicators.get(f"dc_high_crossover_{_ztf}")),
                "high_cu": bool(indicators.get(f"dc_high_crossunder_{_ztf}")),
                "low_co": bool(indicators.get(f"dc_low_crossover_{_ztf}")),
                "low_cu": bool(indicators.get(f"dc_low_crossunder_{_ztf}")),
            }
        # BB values — auto-tuned σ per symbol per TF (natural S/R from touch points)
        _bb = {}
        for _ztf in TFS:
            _bb[_ztf] = {
                "upper": _safe_float(indicators.get(f"bb_upper_{_ztf}")) or 0,
                "lower": _safe_float(indicators.get(f"bb_lower_{_ztf}")) or 0,
                "mid": _safe_float(indicators.get(f"bb_mid_{_ztf}")) or 0,
                "pct_b": _safe_float(indicators.get(f"bb_pct_b_{_ztf}")) or 0.5,
                "width": _safe_float(indicators.get(f"bb_width_{_ztf}")) or 0,
                "mult": _safe_float(indicators.get(f"bb_mult_{_ztf}")) or 2.0,
                "touches": _safe_float(indicators.get(f"bb_touches_{_ztf}")) or 0,
            }
        _dc_high_1h = _dc["1h"]["high"]
        _dc_low_1h = _dc["1h"]["low"]
        _cur_price = _safe_float(indicators.get("current_price")) or 0
        sig.bb_pctb_1h = _bb_1h
        sig.bb_pctb_4h = _bb_4h
        sig.dc_position_1h = _dc_pos_1h
        sig.dc_position_4h = _dc_pos_4h
        sig.wt_nearing_cross = abs(_wt1_1h - _wt2_1h) < 5.0 and abs(_wt_vel_1h) > 1.0
        sig.wt_cross_level_vs_prev = _wt_cross_val_1h - _wt_cross_prev_val_1h if _wt_cross_prev_val_1h else 0.0
        # "Legs remaining": IMPULSE = full legs, EXHAUST = fading
        _impulse_up = _wt_mom_1h == 1 or _wt_mom_4h == 1
        _impulse_down = _wt_mom_1h == -1 or _wt_mom_4h == -1
        _exhaust_up = _wt_mom_1h == 2 and _wt_mom_4h == 2
        _exhaust_down = _wt_mom_1h == -2 and _wt_mom_4h == -2
        _bull_legs = 100.0 - max(_k_4h, _mfi_4h)
        _bear_legs = min(_k_4h, _mfi_4h)
        if _exhaust_up: _bull_legs *= 0.3   # Exhausting = much less room
        if _exhaust_down: _bear_legs *= 0.3
        # Structure: HH peaks + HL troughs = strong uptrend with legs
        _bullish_struct = _wt_peak_struct_1h == 1 and _wt_trough_struct_1h == 1  # HH + HL
        _bearish_struct = _wt_peak_struct_1h == -1 and _wt_trough_struct_1h == -1  # LH + LL
        # Divergence weakens conviction — now uses ALL available TFs (2026-04-11)
        _wt_div_D = int(_safe_float(indicators.get("wt_divergence_D")) or 0)
        _wt_div_15m = int(_safe_float(indicators.get("wt_divergence_15m")) or 0)
        _bull_div = _wt_div_1h == -1 or _wt_div_4h == -1 or _wt_div_D == -1  # BEAR divergence on ANY HTF
        _bear_div = _wt_div_1h == 1 or _wt_div_4h == 1 or _wt_div_D == 1  # BULL divergence on ANY HTF
        _bull_div_count = sum(1 for v in [_wt_div_1h, _wt_div_4h, _wt_div_D] if v == -1)
        _bear_div_count = sum(1 for v in [_wt_div_1h, _wt_div_4h, _wt_div_D] if v == 1)
        # ═══ NEW: wt_zscore for zone detection (self-calibrating, replaces fixed thresholds) ═══
        _zs_1h = _safe_float(indicators.get("wt_zscore_1h")) or 0
        _zs_4h = _safe_float(indicators.get("wt_zscore_4h")) or 0
        _rz_zscore_enabled = cfg.get("rz_zscore_zone_enabled", True)
        _rz_div_block_min = cfg.get("rz_div_block_min", 2)  # HTF div count to block entry
        _rz_two_phase_enabled = cfg.get("rz_two_phase_exit_enabled", True)
        _zscore_overbought = _rz_zscore_enabled and (_zs_4h > 2.0 or (_zs_1h > 2.0 and _zs_4h > 1.5))
        _zscore_oversold = _rz_zscore_enabled and (_zs_4h < -2.0 or (_zs_1h < -2.0 and _zs_4h < -1.5))
        # ═══ NEW: wt_signal for high-conviction entries ═══
        _wt_sig_bull = sum(1 for _stf in ["15m", "1h", "4h"] if int(_safe_float(indicators.get(f"wt_signal_{_stf}")) or 0) == 1)
        _wt_sig_bear = sum(1 for _stf in ["15m", "1h", "4h"] if int(_safe_float(indicators.get(f"wt_signal_{_stf}")) or 0) == -1)
        # ═══ NEW: dc_basis_crossover for breakout confirmation ═══
        _dc_cross_up = sum(1 for _ctf in _tfs if bool(indicators.get(f"dc_basis_crossover_{_ctf}")))
        _dc_cross_dn = sum(1 for _ctf in _tfs if bool(indicators.get(f"dc_basis_crossunder_{_ctf}")))
        # Wave expanding = trend growing, contracting = trend dying
        _expanding = _wt_wave_1h == 1  # EXPANDING
        _contracting = _wt_wave_1h == -1  # CONTRACTING
        # ─── ZONE DETECTION using DC + BB 2σ/2.5σ/3σ (all from ez_indicators, no derivation) ───
        # Zone detection uses auto-tuned BB (natural S/R) + DC levels
        _bb1h = _bb.get("1h", {}); _bb4h = _bb.get("4h", {}); _bb15m = _bb.get("15m", {})
        _bb_upper_1h = _bb1h.get("upper", 0)
        _bb_lower_1h = _bb1h.get("lower", 0)
        _bb_mid_1h = _bb1h.get("mid", 0)
        _above_bb = _cur_price > _bb_upper_1h > 0
        _below_bb = 0 < _cur_price < _bb_lower_1h if _bb_lower_1h > 0 else False
        _above_dc_high = _cur_price > _dc["1h"]["high"] > 0
        _below_dc_low = 0 < _cur_price < _dc["1h"]["low"] if _dc["1h"]["low"] > 0 else False
        _dc_basis_1h = _dc["1h"]["basis"]
        _band_range = _dc["1h"]["high"] - _dc["1h"]["low"] if _dc["1h"]["high"] > _dc["1h"]["low"] else 1
        _dist_to_basis = abs(_cur_price - _dc_basis_1h) / _band_range if _band_range > 0 else 1
        _rz_top_bb = cfg.get("rz_top_bb", 0.85)
        _rz_bot_bb = cfg.get("rz_bot_bb", 0.15)
        _rz_legs_min = cfg.get("rz_legs_min", 20.0)
        _rz_require_struct = cfg.get("rz_require_struct", False)
        _rz_k_exit = cfg.get("rz_k_exit", 90.0)
        _rz_mfi_exit = cfg.get("rz_mfi_exit", 85.0)
        _rz_entry_enabled = cfg.get("rz_entry_enabled", True)
        _rz_exit_enabled = cfg.get("rz_exit_enabled", True)
        # CFG-DRIVEN BASELINE TOLERANCE — crypto needs tight (0.03) due to high volatility,
        # stocks need looser (0.05-0.08) since intraday moves are smaller. Sweepable.
        _baseline_tol = cfg.get("rz_baseline_tol", 0.03)
        # CFG-DRIVEN LTF MICRO TF — crypto base is 3m, stocks base is 5m. Affects which
        # wt_velocity_{tf} field the bounce logic checks. Default 3m for back-compat.
        _ltf_micro = cfg.get("rz_ltf_micro", "3m")
        # Zone detection: use BOTH fixed BB thresholds AND self-calibrating zscore
        _near_top = _above_bb or _above_dc_high or _bb_1h > _rz_top_bb or _dc_pos_1h > _rz_top_bb or _zscore_overbought
        _near_bottom = _below_bb or _below_dc_low or _bb_1h < _rz_bot_bb or _dc_pos_1h < _rz_bot_bb or _zscore_oversold
        # ═══ BASELINE = PULLBACK TO MEAN + BOUNCE + HTF STILL ALIGNED ═══
        # Baseline is the retracement-to-basis setup, not just "in the middle".
        # Required: price touched/near a mean line AND now bouncing AND HTF direction still valid.
        # Without a bounce, there's no reason to enter baseline.
        _touched_basis = False
        _touched_basis_tf = None
        for _btf in _tfs:
            _b = _dc.get(_btf, {})
            _bb_info = _bb.get(_btf, {})
            _basis = _b.get("basis", 0)
            _mid = _bb_info.get("mid", 0)
            if _basis > 0 and _cur_price > 0 and abs(_cur_price - _basis) / _basis < _baseline_tol:
                _touched_basis = True
                _touched_basis_tf = _btf
                break
            if _mid > 0 and _cur_price > 0 and abs(_cur_price - _mid) / _mid < _baseline_tol:
                _touched_basis = True
                _touched_basis_tf = _btf + "_bb"
                break
        _near_baseline = _touched_basis and not _near_top and not _near_bottom
        # HTF alignment: block ONLY when HTF is AGGRESSIVELY bearish/bullish against the trade.
        # IMPULSE_DOWN (-1) is the reversal; EXHAUST_DOWN (-2) is the capitulation.
        # We only block when IMPULSE_DOWN on 4h (fresh trend change), not when everything is mildly bearish.
        _wt_mom_D = int(_safe_float(indicators.get("wt_momentum_state_D")) or 0)
        _htf_bull_ok = not (_wt_mom_4h == -1 and _wt_mom_D == -1)  # both fresh down = block long
        _htf_bear_ok = not (_wt_mom_4h == 1 and _wt_mom_D == 1)  # both fresh up = block short
        _rz_k_entry_max = cfg.get("rz_k_entry_max", 50.0)  # sweep: 40/50/60
        _rz_k_exit_thr = cfg.get("rz_k_exit", 80.0)  # sweep: 70/80/90
        if _near_baseline:
            sig.zone = "BASELINE"
            sig.zone_legs_remaining = max(_bull_legs, _bear_legs)
            # ═══ BASELINE = PULLBACK + BOUNCE ENTRY (both directions, asymmetric) ═══
            # LONG: price pulled back to baseline in an uptrend, now BOUNCING UP (3m + 1h vel flipping positive)
            #       AND HTF (4h + D) still bullish/neutral (not flipped bearish)
            # SHORT: price pulled back to baseline in a downtrend, now BOUNCING DOWN
            #        AND HTF (4h + D) still bearish/neutral
            # Without the bounce confirmation, do nothing — price at mean is not enough.
            # LTF micro velocity — fall back to _ltf_micro TF (3m for crypto, 5m for stocks)
            _wt_vel_3m = _safe_float(indicators.get(f"wt_velocity_{_ltf_micro}", indicators.get("wt_velocity_3m"))) or 0
            _wt_vel_15m = _safe_float(indicators.get("wt_velocity_15m")) or 0
            _k_3m = _safe_float(indicators.get(f"stoch_k_{_ltf_micro}", indicators.get("stoch_k_3m"))) or 50
            _k_15m = _safe_float(indicators.get("stoch_k_15m")) or 50
            # BOUNCE = velocity recently turned (vel > 0 AND bar-to-bar increase in bull_speed)
            _prev_total_bull = prev.get("_rz_prev_total_bull", 0.0)
            _prev_total_bear = prev.get("_rz_prev_total_bear", 0.0)
            _bull_accel = total_bull > _prev_total_bull * 1.1  # bull speed increasing
            _bear_accel = total_bear > _prev_total_bear * 1.1  # bear speed increasing
            # Always update (RZ runs once per update call, doesn't need bar-boundary guard)
            prev["_rz_prev_total_bull"] = total_bull
            prev["_rz_prev_total_bear"] = total_bear
            prev["_rz_zone"] = "BASELINE"
            # LONG bounce: bull delta dominant + ANY LTF velocity bullish + HTF not against
            _ltf_vel_bull = _wt_vel_3m > 0 or _wt_vel_15m > 0 or _wt_vel_1h > 0
            _long_bounce = (total_bull > total_bear and
                            _ltf_vel_bull and
                            _htf_bull_ok and _k_1h < _rz_k_exit_thr
                            and _bull_div_count < _rz_div_block_min)  # BLOCK if N+ HTF bearish divergence
            # SHORT bounce: bear delta dominant + ANY LTF velocity bearish + HTF not against
            _ltf_vel_bear = _wt_vel_3m < 0 or _wt_vel_15m < 0 or _wt_vel_1h < 0
            _short_bounce = (total_bear > total_bull and
                             _ltf_vel_bear and
                             _htf_bear_ok and _k_1h > (100 - _rz_k_exit_thr)
                             and _bear_div_count < _rz_div_block_min)  # BLOCK if N+ HTF bullish divergence
            if _rz_entry_enabled and _long_bounce:
                sig.zone_action = "BUY"
                # Boost reason with signal quality info
                _sig_tag = f"_sig={_wt_sig_bull}" if _wt_sig_bull > 0 else ""
                _dc_tag = f"_dcxo={_dc_cross_up}" if _dc_cross_up > 0 else ""
                _zs_tag = f"_zs1h={_zs_1h:.1f}" if abs(_zs_1h) > 1 else ""
                sig.zone_reason = f"BASELINE_BOUNCE_LONG_touched={_touched_basis_tf}_bull={total_bull:.2f}_bear={total_bear:.2f}_v3m={_wt_vel_3m:.1f}_v15m={_wt_vel_15m:.1f}_v1h={_wt_vel_1h:.1f}_htf_mom4h={_wt_mom_4h}_k={_k_1h:.0f}_bb={_bb_1h:.2f}_dc={_dc_pos_1h:.2f}{_sig_tag}{_dc_tag}{_zs_tag}"
                sig.entry_long = True
            elif _rz_entry_enabled and _short_bounce:
                sig.zone_action = "SELL"
                _sig_tag = f"_sig={_wt_sig_bear}" if _wt_sig_bear > 0 else ""
                _dc_tag = f"_dcxu={_dc_cross_dn}" if _dc_cross_dn > 0 else ""
                _zs_tag = f"_zs1h={_zs_1h:.1f}" if abs(_zs_1h) > 1 else ""
                sig.zone_reason = f"BASELINE_BOUNCE_SHORT_touched={_touched_basis_tf}_bear={total_bear:.2f}_bull={total_bull:.2f}_v3m={_wt_vel_3m:.1f}_v15m={_wt_vel_15m:.1f}_v1h={_wt_vel_1h:.1f}_htf_mom4h={_wt_mom_4h}_k={_k_1h:.0f}_bb={_bb_1h:.2f}_dc={_dc_pos_1h:.2f}{_sig_tag}{_dc_tag}{_zs_tag}"
                sig.entry_short = True
        elif _near_top:
            sig.zone = "TOP"
            sig.zone_legs_remaining = _bull_legs
            prev["_rz_zone"] = "TOP"
            prev["_rz_wt_bullish"] = _wt1_1h > _wt2_1h
            prev["_rz_vel_pos"] = _wt_vel_1h > 0
            _k_3m = _safe_float(indicators.get(f"stoch_k_{_ltf_micro}", indicators.get("stoch_k_3m"))) or 50
            _k_15m = _safe_float(indicators.get("stoch_k_15m")) or 50
            _wt_vel_3m = _safe_float(indicators.get(f"wt_velocity_{_ltf_micro}", indicators.get("wt_velocity_3m"))) or 0
            _dc_high_co_1h = _dc["1h"]["high_co"]
            _dc_high_1h_ant = _dc["1h"]["high_ant"]
            _broke_above = _dc_high_co_1h or _above_bb or _above_dc_high
            # === TOP IS SHORT-ONLY TERRITORY (longs are ultra-risky, short-lived breakouts) ===
            # Upward breakouts are short-lived. Only enter LONG on extremely tight stop.
            # The real money here is EXIT LONG and then SHORT the rejection.
            # T1: EXIT LONG if at top with any sign of exhaustion
            if _rz_exit_enabled and position_state and position_state.get("side") in ("LONG", "L"):
                if _exhaust_up or _bull_div or _wt_vel_1h < 0 or _k_1h > 90:
                    sig.zone_action = "EXIT_LONG"
                    sig.zone_reason = f"TOP_EXIT_LONG_exh={_exhaust_up}_div={_bull_div}_vel={_wt_vel_1h:.1f}_k={_k_1h:.0f}_bb={_bb_1h:.2f}"
                    sig.exit_long = True
            # T2: FAILED BREAKOUT — price crossed back below resistance = ultra tight stop triggered
            if _rz_exit_enabled and not _broke_above and _dc_pos_1h > 0.7 and _wt_vel_1h < 0:
                sig.zone_action = "EXIT_LONG"
                sig.zone_reason = f"TOP_FAILED_BREAKOUT_vel={_wt_vel_1h:.1f}_dc={_dc_pos_1h:.2f}_bb={_bb_1h:.2f}"
                sig.exit_long = True
            # T3: SHORT ENTRY on rejection — price at top, wt turning, 3m confirming
            if _rz_entry_enabled and not sig.exit_long:
                _top_rejection = (_wt_vel_1h < -1.0 or _exhaust_up) and _k_3m > 70 and _wt_vel_3m < 0
                if _top_rejection and _bear_legs > 15:
                    sig.zone_action = "SELL"
                    sig.zone_reason = f"TOP_REJECTION_SHORT_vel1h={_wt_vel_1h:.1f}_k3m={_k_3m:.0f}_vel3m={_wt_vel_3m:.1f}_exh={_exhaust_up}_div={_bull_div}_legs={_bear_legs:.0f}"
                    sig.entry_short = True
                    # TEMP KEY: counter-trend short on a symbol that may be LONG-only
                    # Expires on close or after 4h (top rejections can take time to play out)
                    sig.temp_key_request = {"side": "SHORT", "reason": "TOP_REJECTION", "max_age_s": 14400}
        elif _near_bottom:
            sig.zone = "BOTTOM"
            sig.zone_legs_remaining = _bear_legs
            prev["_rz_zone"] = "BOTTOM"
            prev["_rz_wt_bullish"] = _wt1_1h > _wt2_1h
            prev["_rz_vel_pos"] = _wt_vel_1h > 0
            _k_3m = _safe_float(indicators.get(f"stoch_k_{_ltf_micro}", indicators.get("stoch_k_3m"))) or 50
            _k_15m = _safe_float(indicators.get("stoch_k_15m")) or 50
            _wt_vel_3m = _safe_float(indicators.get(f"wt_velocity_{_ltf_micro}", indicators.get("wt_velocity_3m"))) or 0
            _dc_low_cu_1h = _dc["1h"]["low_cu"]
            _dc_low_1h_ant = _dc["1h"]["low_ant"]
            _dc_low4_1h = _dc["1h"]["low4"]
            _dc_low_15m = _dc["15m"]["low"]
            _broke_below = _dc_low_cu_1h or _below_bb or _below_dc_low
            # === BOTTOM IS THE MONEY ZONE — 3-phase short cycle ===
            # Phase 1: BREAKDOWN — shorts fall through hard, no retest. BACK UP THE TRUCK.
            # Check k_15m: if >20 it will keep falling. Do NOT go long until k_15m < 5.
            if _rz_entry_enabled and _broke_below and _impulse_down and _bear_legs > 15 and _k_15m > 20:
                _bb_mult = _bb1h.get("mult", 2.0)
                _severity = "EXTREME" if _bb_mult >= 2.5 else ("STRONG" if _bb_1h < 0.05 else "NORMAL")
                sig.zone_action = "SELL"
                sig.zone_reason = f"BREAKDOWN_TRUCK_{_severity}_dc_cu={_dc_low_cu_1h}_bb={_bb_1h:.2f}_k15m={_k_15m:.0f}(>20=keep_falling)_legs={_bear_legs:.0f}"
                sig.entry_short = True
            # Phase 2: BOUNCE — price hit next TF low (dc_low4 or 15m dc_low), k_15m < 5, HUGE bounce
            # EXIT SHORT here. This bounce is worthy of a LONG (but tight stop).
            elif _k_15m < 5 or (_dc_low4_1h > 0 and _cur_price <= _dc_low4_1h * 1.002 and _wt_vel_3m > 2.0):
                _at_15m_low = _dc_low_15m > 0 and _cur_price <= _dc_low_15m * 1.002
                if _rz_exit_enabled and position_state and position_state.get("side") in ("SHORT", "S"):
                    sig.zone_action = "EXIT_SHORT"
                    sig.zone_reason = f"BOTTOM_BOUNCE_EXIT_k15m={_k_15m:.0f}_dc4={_dc_low4_1h:.4f}_dc15m={_dc_low_15m:.4f}_vel3m={_wt_vel_3m:.1f}"
                    sig.exit_short = True
                # The HUGE bounce is a LONG opportunity (but only if k_15m was < 5)
                if _rz_entry_enabled and _k_15m < 5 and _wt_vel_3m > 2.0 and not sig.exit_short:
                    sig.zone_action = "BUY"
                    sig.zone_reason = f"BOTTOM_HUGE_BOUNCE_LONG_k15m={_k_15m:.0f}_vel3m={_wt_vel_3m:.1f}_dc_low15m={_at_15m_low}"
                    sig.entry_long = True
                    # TEMP KEY: this is a counter-trend long on a symbol that's normally SHORT-only
                    # Auto-expires on close or after 2h (bounces are short-lived)
                    sig.temp_key_request = {"side": "LONG", "reason": "BOTTOM_HUGE_BOUNCE", "max_age_s": 7200}
            # Phase 3: REJECTION AT OLD RED ZONE — price bounced, rallied to old breakdown level
            # Old support (dc_low_ant) is now resistance. 3m rejecting there = LOAD UP EVEN MORE.
            elif _rz_entry_enabled:
                _old_resistance = _dc_low_1h_ant
                _at_old_level = _old_resistance > 0 and _cur_price > 0 and abs(_cur_price - _old_resistance) / _old_resistance < 0.012
                _3m_rejecting = _k_3m > 60 and _wt_vel_3m < -1.0
                _htf_still_bearish = _wt1_1h < _wt2_1h or _dc_pos_1h < 0.4
                if _at_old_level and _3m_rejecting and _htf_still_bearish and _bear_legs > 10:
                    sig.zone_action = "SELL"
                    sig.zone_reason = f"REJECTION_OLD_REDZONE_LOAD_MORE_old={_old_resistance:.4f}_p={_cur_price:.4f}_k3m={_k_3m:.0f}_vel3m={_wt_vel_3m:.1f}_dc={_dc_pos_1h:.2f}"
                    sig.entry_short = True
                    sig.pyramid_short = True
                    # TEMP KEY for pyramid on counter-trend side — 2h max, tighter than initial
                    sig.temp_key_request = {"side": "SHORT", "reason": "REDZONE_REJECTION_LOAD", "max_age_s": 7200}
        if not _near_baseline and not _near_top and not _near_bottom:
            sig.zone = "TRANSIT"
            sig.zone_legs_remaining = _bull_legs if total_bull > total_bear else _bear_legs
            prev["_rz_zone"] = "TRANSIT"
            prev["_rz_wt_bullish"] = _wt1_1h > _wt2_1h
            prev["_rz_vel_pos"] = _wt_vel_1h > 0
        # ═══ SMART_RZ_EXIT v2 (2026-04-11) — slowdown-gated red zone exit ═══
        # NEVER sells into a rally. Waits for structural weakness THEN sells the retest.
        # Gate: k_15m>90 AND in red zone AND (MFI exhaustion OR ANY weakness):
        #   - delta decel (momentum dying)
        #   - low_3m < low_3m_prev (lower low = structure breaking)
        #   - high_3m < high_3m_prev (lower high = rally fading)
        #   - wt_velocity negative on 3m or 15m
        #   - dc_position falling on both 3m+15m
        #   - price < prev_3m_low (candle low break)
        # Sets exit_pending → Phase 2 sells at next bar high (the retest).
        # IMMEDIATE REENTRY if k_15m > d_15m still after exit (momentum continuing).
        if _rz_exit_enabled and position_state:
            side = position_state.get("side", "")
            _ltf_m = cfg.get("rz_ltf_micro", "3m")
            _rz_k_15m = _safe_float(indicators.get("stoch_k_15m")) or 50.0
            _rz_d_15m = _safe_float(indicators.get("stoch_d_15m")) or 50.0
            _rz_mfi_15m = _safe_float(indicators.get("mfi_15m")) or 50.0
            _rz_mfi_thr = cfg.get("rz_mfi_exit", 85.0)
            _rz_cur_price = _safe_float(indicators.get("current_price", indicators.get("close", 0)))
            _rz_prev_low = _safe_float(indicators.get(f"low_{_ltf_m}_prev", indicators.get("low_3m_prev")))
            _rz_prev_high = _safe_float(indicators.get(f"high_{_ltf_m}_prev", indicators.get("high_3m_prev")))
            _rz_speed_long = getattr(sig, 'bull_speed', 0)
            _rz_speed_short = getattr(sig, 'bear_speed', 0)
            _rz_prev_spd_l = prev.get("_rz_prev_bull_spd", _rz_speed_long)
            _rz_prev_spd_s = prev.get("_rz_prev_bear_spd", _rz_speed_short)
            _rz_decel_long = _rz_speed_long < _rz_prev_spd_l * 0.90 if _rz_prev_spd_l > 0.5 else False
            _rz_decel_short = _rz_speed_short < _rz_prev_spd_s * 0.90 if _rz_prev_spd_s > 0.5 else False
            prev["_rz_prev_bull_spd"] = _rz_speed_long
            prev["_rz_prev_bear_spd"] = _rz_speed_short
            # Slowdown signals available for both sides:
            _rz_wt_vel_3m = _safe_float(indicators.get("wt_velocity_3m")) or 0
            _rz_wt_vel_15m = _safe_float(indicators.get("wt_velocity_15m")) or 0
            _rz_dc_pos_3m = _safe_float(indicators.get("dc_position_3m")) or 0.5
            _rz_dc_pos_15m = _safe_float(indicators.get("dc_position_15m")) or 0.5
            _rz_prev_dc3 = prev.get("_rz_prev_dc_pos_3m", _rz_dc_pos_3m)
            _rz_prev_dc15 = prev.get("_rz_prev_dc_pos_15m", _rz_dc_pos_15m)
            prev["_rz_prev_dc_pos_3m"] = _rz_dc_pos_3m
            prev["_rz_prev_dc_pos_15m"] = _rz_dc_pos_15m
            # 3m structure: lower high / lower low (for longs), higher low / higher high (for shorts)
            _rz_high_3m = _safe_float(indicators.get(f"high_{_ltf_m}", indicators.get("high_3m")))
            _rz_high_3m_prev = _safe_float(indicators.get(f"high_{_ltf_m}_prev", indicators.get("high_3m_prev")))
            _rz_low_3m = _safe_float(indicators.get(f"low_{_ltf_m}", indicators.get("low_3m")))
            _rz_low_3m_prev = _safe_float(indicators.get(f"low_{_ltf_m}_prev", indicators.get("low_3m_prev")))
            if side in ("LONG", "L"):
                _rz_k_extreme = _rz_k_15m > 90
                _rz_in_zone = _near_top  # already computed: bb>0.85 or dc>0.85
                _rz_mfi_confirm = _rz_mfi_15m > _rz_mfi_thr
                _rz_low_break = _rz_cur_price > 0 and _rz_prev_low > 0 and _rz_cur_price < _rz_prev_low
                _rz_wt_slow = _rz_wt_vel_3m < -1.0 or _rz_wt_vel_15m < -1.0
                _rz_dc_falling = _rz_dc_pos_3m < _rz_prev_dc3 and _rz_dc_pos_15m < _rz_prev_dc15
                _rz_lower_high = _rz_high_3m > 0 and _rz_high_3m_prev > 0 and _rz_high_3m < _rz_high_3m_prev
                _rz_lower_low = _rz_low_3m > 0 and _rz_low_3m_prev > 0 and _rz_low_3m < _rz_low_3m_prev
                # ANY sign of weakness: delta decel, prev low break, wt slowing, dc falling, lower high/low on 3m
                _rz_weakness = _rz_decel_long or _rz_low_break or _rz_wt_slow or _rz_dc_falling or _rz_lower_high or _rz_lower_low
                # Gate: k_15m>90 AND in red zone AND (mfi exhaustion OR any weakness sign)
                if _rz_k_extreme and _rz_in_zone and (_rz_mfi_confirm or _rz_weakness):
                    sig.exit_pending_long = True
                    prev["_exit_pending_long"] = True
                    sig.reentry_if_momentum = _rz_k_15m > _rz_d_15m
                    sig.zone_action = "SMART_RZ_PENDING_LONG"
                    sig.zone_reason = f"SMART_RZ_EXIT_L_k15={_rz_k_15m:.0f}_mfi={_rz_mfi_15m:.0f}_decel={_rz_decel_long}_lowbrk={_rz_low_break}_wt={_rz_wt_slow}_dc={_rz_dc_falling}_lh={_rz_lower_high}_ll={_rz_lower_low}_re={sig.reentry_if_momentum}"
            elif side in ("SHORT", "S"):
                _rz_k_extreme_s = _rz_k_15m < 10
                _rz_in_zone_s = _near_bottom  # already computed: bb<0.15 or dc<0.15
                _rz_mfi_confirm_s = _rz_mfi_15m < (100 - _rz_mfi_thr)
                _rz_high_break = _rz_cur_price > 0 and _rz_prev_high > 0 and _rz_cur_price > _rz_prev_high
                _rz_wt_slow_s = _rz_wt_vel_3m > 1.0 or _rz_wt_vel_15m > 1.0
                _rz_dc_rising = _rz_dc_pos_3m > _rz_prev_dc3 and _rz_dc_pos_15m > _rz_prev_dc15
                _rz_higher_low = _rz_low_3m > 0 and _rz_low_3m_prev > 0 and _rz_low_3m > _rz_low_3m_prev
                _rz_higher_high = _rz_high_3m > 0 and _rz_high_3m_prev > 0 and _rz_high_3m > _rz_high_3m_prev
                _rz_weakness_s = _rz_decel_short or _rz_high_break or _rz_wt_slow_s or _rz_dc_rising or _rz_higher_low or _rz_higher_high
                if _rz_k_extreme_s and _rz_in_zone_s and (_rz_mfi_confirm_s or _rz_weakness_s):
                    sig.exit_pending_short = True
                    prev["_exit_pending_short"] = True
                    sig.reentry_if_momentum = _rz_k_15m < _rz_d_15m
                    sig.zone_action = "SMART_RZ_PENDING_SHORT"
                    sig.zone_reason = f"SMART_RZ_EXIT_S_k15={_rz_k_15m:.0f}_mfi={_rz_mfi_15m:.0f}_decel={_rz_decel_short}_highbrk={_rz_high_break}_wt={_rz_wt_slow_s}_dc={_rz_dc_rising}_hl={_rz_higher_low}_hh={_rz_higher_high}_re={sig.reentry_if_momentum}"

    def reset_position_state(self, symbol):
        self._max_speed[symbol] = 0.0


def _safe_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


# Keep compute_npz_signals for backtest use
def compute_npz_signals(d, cfg=None):
    """Vectorized delta computation for NPZ backtest."""
    if cfg is None:
        cfg = DEFAULT_CFG
    keys = list(d.keys())
    wt_dc_keys = sorted([k for k in keys if "wt" in k or k.startswith("dc")])
    n = len(d["close"])
    tw = cfg.get("tf_weights", {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5})
    smooth = cfg.get("speed_smooth", 3)
    accel_lb = cfg.get("accel_lookback", 5)
    tf_bull = {tf: np.zeros(n) for tf in TFS}
    tf_bear = {tf: np.zeros(n) for tf in TFS}
    tf_c = {tf: 0 for tf in TFS}
    for k in wt_dc_keys:
        arr = d[k].astype(np.float64)
        prev_k = k + "_prev"
        if prev_k in d:
            delta = arr - d[prev_k].astype(np.float64)
        elif d[k].dtype in (np.float32, np.float64) and not k.endswith("_prev") and not k.endswith("_ant"):
            delta = np.empty(n); delta[0] = 0; delta[1:] = arr[1:] - arr[:-1]
        else:
            continue
        delta = np.nan_to_num(delta, 0)
        mt = None
        for tf in TFS:
            if f"_{tf}" in k: mt = tf; break
        if mt:
            tf_bull[mt] += np.maximum(delta, 0); tf_bear[mt] += np.maximum(-delta, 0); tf_c[mt] += 1
        else:
            for tf in TFS:
                tf_bull[tf] += np.maximum(delta, 0) * 0.2; tf_bear[tf] += np.maximum(-delta, 0) * 0.2; tf_c[tf] += 1
    for tf in TFS:
        if tf_c[tf] > 0: tf_bull[tf] /= tf_c[tf]; tf_bear[tf] /= tf_c[tf]
    if smooth > 1:
        kernel = np.ones(smooth) / smooth
        for tf in TFS: tf_bull[tf] = np.convolve(tf_bull[tf], kernel, mode="same"); tf_bear[tf] = np.convolve(tf_bear[tf], kernel, mode="same")
    tf_bull_z, tf_bear_z = {}, {}
    for tf in TFS:
        m, s = np.mean(tf_bull[tf]), np.std(tf_bull[tf])
        tf_bull_z[tf] = (tf_bull[tf] - m) / s if s > 1e-10 else np.zeros(n)
        m, s = np.mean(tf_bear[tf]), np.std(tf_bear[tf])
        tf_bear_z[tf] = (tf_bear[tf] - m) / s if s > 1e-10 else np.zeros(n)
    total_bull = sum(tf_bull_z[tf] * tw.get(tf, 1.0) for tf in TFS)
    total_bear = sum(tf_bear_z[tf] * tw.get(tf, 1.0) for tf in TFS)
    m, s = np.mean(total_bull), np.std(total_bull)
    total_bull_z = (total_bull - m) / s if s > 1e-10 else np.zeros(n)
    m, s = np.mean(total_bear), np.std(total_bear)
    total_bear_z = (total_bear - m) / s if s > 1e-10 else np.zeros(n)
    bull_accel = np.zeros(n); bear_accel = np.zeros(n)
    bull_accel[accel_lb:] = total_bull_z[accel_lb:] - total_bull_z[:-accel_lb]
    bear_accel[accel_lb:] = total_bear_z[accel_lb:] - total_bear_z[:-accel_lb]
    zt = cfg.get("tf_z_threshold", 1.0)
    bull_tf_count = np.zeros(n, dtype=np.int8); bear_tf_count = np.zeros(n, dtype=np.int8)
    for tf in TFS:
        bull_tf_count += (tf_bull_z[tf] > zt).astype(np.int8); bear_tf_count += (tf_bear_z[tf] > zt).astype(np.int8)
    entry_min_tf = cfg.get("entry_min_tf", 4); entry_z = cfg.get("entry_z_threshold", 2.0); entry_accel = cfg.get("entry_accel_threshold", 0.3)
    return {
        "total_bull_z": total_bull_z, "total_bear_z": total_bear_z,
        "bull_accel": bull_accel, "bear_accel": bear_accel,
        "bull_tf_count": bull_tf_count, "bear_tf_count": bear_tf_count,
        "entry_long": (total_bull_z > entry_z) & (bull_accel > entry_accel) & (bull_tf_count >= entry_min_tf),
        "entry_short": (total_bear_z > entry_z) & (bear_accel > entry_accel) & (bear_tf_count >= entry_min_tf),
        "tf_bull_z": tf_bull_z, "tf_bear_z": tf_bear_z,
    }
