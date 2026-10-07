"""
vec_decisions/htf_regime_scale.py — HTF-REGIME hold + scale-in-at-bottoms desired-weight core.
2026-05-30 USER MANDATE. Shared scalar (live) + numpy-vectorized (backtest) core so the two paths
cannot drift (same pattern as strategy_enhancements._pyramid_fires / higher_wt_cross).

WHAT IT COMPUTES — a desired position-size WEIGHT (>=0) for the current bar, given price vs the
200-SMA stack on each TF. The caller (live: ez_manage/tradier_manage; backtest: v8_vec_sweep) turns a
change in desired weight into OPEN / AUGMENT / REDUCE / CLOSE through execute_now (live) or the sim P&L
(backtest). It NEVER returns a target that adds to a net loser by itself — scale-in only grows weight
when MORE SMAs are reclaimed (price rising = trend continuation), which is the opposite of a martingale.

RULES (validated honest/net/no-lookahead 2026-05-30; ZEC 5-14x b&h on smooth-up, BTC kept flat to avoid
leverage ruin):
  htf_up = close > sma_200_<regime_tf>  AND  sma_200_<regime_tf> rising over `rise_lag` bars
  if scale_in and htf_up:  w = min(size_cap, 1.0 + add_mult * (#lower SMAs reclaimed among ladder_tfs))
  elif htf_up:             w = 1.0                      # ride flat (volatile-up: never lever the chop)
  else:                    w = 1.0 if close > sma_200_<exit_tf> else 0.0   # tight cut in chop/non-uptrend
  if vol_target > 0:       w *= min(1.0, vol_target / realized_vol)        # de-lever when vol high

All fields are present in the NPZ and the live indicator dict (per TF): sma_200_<tf>. realized_vol is a
rolling std of close returns the caller supplies (or pass None to disable vol-targeting for that bar).
Direction: LONG uses price>SMA; SHORT mirrors (price<SMA, falling SMA) — caller passes is_long.
"""
from __future__ import annotations
from typing import Optional, Sequence
import numpy as np

LADDER_TFS = ("15m", "1h", "4h")          # crypto default; stocks pass ("1h","4h","D") (no 3m/15m wt on stocks)
DEFAULTS = dict(regime_tf="D", exit_tf="15m", add_mult=0.75, size_cap=3.0,
                vol_target=0.0, rise_lag=30, scale_in=True,
                # RANGE MODE (USER 2026-05-30): in a low-ADX range, mean-revert (buy %b-low, sell %b-high)
                # instead of trend-following; only hand back to the staircase on a CONFIRMED breakout
                # (ADX expands above adx_trend AND price closes above the Donchian-high of range_tf).
                range_mode=False, range_tf="1h", adx_tf="4h", adx_range=20.0, adx_trend=25.0,
                range_buy=0.15, range_sell=0.85,
                # BB-WIDTH SIZING (USER 2026-05-30): scale position by Bollinger-band width vs its baseline —
                # NARROWING (squeeze, low conviction) => smaller; WIDENING (expansion/breakout) => larger.
                # This is the OPPOSITE of vol_target (risk-parity), so use one or the other, not both.
                bb_width_size=False, bb_width_tf="1h", bb_width_win=480, bb_width_lo=0.5, bb_width_hi=2.0,
                # EXPANSION OBLIGATION (USER 2026-05-30 = True): never OPEN into a narrowing/squeezing band —
                # an entry is only allowed when band width is EXPANDING (width > its rolling baseline) on
                # expansion_tf. Cuts the squeeze-churn (~halves trades, lifts Sharpe on churners). Per_sym
                # overridable False for clean low-trade trenders that the delay would hurt.
                require_bb_expansion=True, expansion_tf="1h", expansion_win=100,
                # RULE A (USER 2026-05-30, validated): %-distance size ladder. Enter when price >pct_l1 above
                # sma_200_15m AND wt1_15m turning up; HOLD while above sma_15m; size = ×1.5/×2/×3 by %-distance.
                # The 10× lever on trends (MU 0.15→0.31); per_sym trend-only (amplifies chop). Overrides the
                # SMA-reclaim ladder when on.
                # Parametric: enter when >pct_entry beyond sma_200_15m + wt1_15m turn-up; size = 1 + per_pct_mult
                # × (%-distance), capped at size_cap. gain_bonus_k>0 = WINNERS BONUS: size also scales with the
                # price gain since entry (so actual winners trade bigger money). HOLD while beyond the SMA.
                pct_ladder=False, pct_entry=1.0, per_pct_mult=0.5, gain_bonus_k=0.0,
                # PARITY-SAFE winners bonus: size × (1 + streak_bonus × win-rate of last streak_n closed trades).
                # Set at entry, constant through hold. Both sides. Replaces gain_bonus_k for live (parity-clean).
                streak_bonus=0.0, streak_n=10,
                # RULE B (USER 2026-05-30, validated WINNER): exit on struct_exit_tf lower-low+lower-high,
                # re-enter on the turn (higher-low+higher-high) via Donchian slope. Helps every name.
                struct_exit_tf=None,
                # FILTER->SIGNAL (USER 2026-05-31): require_wt_turn=True keeps wt1_15m turn-up as a HARD RULE-A
                # entry gate (current). False flips it to a SIGNAL — entry fires on %-distance alone (more
                # trades, faster re-entry after a Rule-B exit). Lever to lift trades/sym/day toward 1-8.
                require_wt_turn=True)


def _rule_a_weight(ind, is_long, pct_entry, per_pct_mult, size_cap, gain_bonus_k, require_wt_turn=True):
    """RULE A (parametric): enter when price is >pct_entry % beyond sma_200_15m AND wt1_15m turning up; HOLD
    while beyond the SMA. Size = clip(1 + per_pct_mult × %-distance, 1, size_cap). gain_bonus_k>0 adds a WINNERS
    bonus: size also scales with price-gain-since-entry (winners trade bigger), all capped at size_cap."""
    close = np.asarray(ind["close"], float); n = len(close)
    cp = np.concatenate([[close[0]], close[:-1]])
    s = ind.get("sma_200_15m"); w15 = ind.get("wt1_15m")
    if s is None or w15 is None:
        return None
    s = np.asarray(s, float); sp = np.concatenate([[s[0]], s[:-1]])
    w15 = np.asarray(w15, float); w1 = np.concatenate([[w15[0]], w15[:-1]]); w2 = np.concatenate([[w1[0]], w1[:-1]])
    pct = np.where(sp > 0, (cp - sp) / sp * 100.0, -99.0) if is_long else np.where(sp > 0, (sp - cp) / sp * 100.0, -99.0)
    turn = (w1 > w2) if is_long else (w1 < w2)
    enter = (pct > pct_entry) & (turn if require_wt_turn else np.ones(n, dtype=bool))
    exit_ = (cp < sp) if is_long else (cp > sp)
    inpos = _ffill_pos(enter, exit_)
    size = np.clip(1.0 + per_pct_mult * np.maximum(pct, 0.0), 1.0, size_cap)
    if gain_bonus_k and gain_bonus_k > 0:
        instate = inpos > 0
        starts = instate & ~np.concatenate([[False], instate[:-1]])
        entry_px = np.where(starts, cp, np.nan)
        idx = np.maximum.accumulate(np.where(~np.isnan(entry_px), np.arange(n), 0))
        ep = entry_px[idx]
        gain_since = np.where(ep > 0, (cp / ep - 1.0) if is_long else (ep / cp - 1.0), 0.0)
        size = np.minimum(size * np.clip(1.0 + gain_bonus_k * np.maximum(gain_since, 0.0), 1.0, size_cap), size_cap)
    return inpos * size


def streak_bonus_mult(close, w, is_long, streak_n=10, streak_bonus=0.0, cap=5.0):
    """PARITY-SAFE winners bonus (USER 2026-05-30): size × (1 + streak_bonus × win-rate of the last `streak_n`
    CLOSED trades). Set at each entry, CONSTANT through the hold (no per-bar entry-price drift → windowed==full).
    Works both sides — trade returns are direction-adjusted. `w` is the (pre-bonus) weight array; returns the
    per-bar multiplier (1.0 outside positions)."""
    n = len(w)
    if streak_bonus <= 0:
        return np.ones(n)
    close = np.asarray(close, float)
    active = w > 0; d = np.diff(active.astype(int))
    starts = list(np.where(d == 1)[0] + 1); ends = list(np.where(d == -1)[0] + 1)
    if active[0]:
        starts = [0] + starts
    if active[-1]:
        ends = ends + [n]
    rr = []
    for s, e in zip(starts, ends):
        s0 = max(s, 1)
        r = (close[e - 1] / close[s0 - 1] - 1) if is_long else (close[s0 - 1] / close[e - 1] - 1)
        rr.append(r)
    rr = np.array(rr)
    mult = np.ones(n)
    for i, (s, e) in enumerate(zip(starts, ends)):
        prev = rr[max(0, i - streak_n):i]
        wr = float(np.mean(prev > 0)) if len(prev) else 0.0      # win-rate of last N closed trades (0 if none yet)
        mult[s:e] = min(max(1.0 + streak_bonus * wr, 1.0), cap)
    return mult


def _rule_b_gate(ind, tf, w, is_long):
    """RULE B: exit on tf lower-low+lower-high (Donchian slope down), re-enter on the turn (slope up)."""
    dl = ind.get(f"dc_low_{tf}"); dh = ind.get(f"dc_high_{tf}")
    if dl is None or dh is None:
        return w
    dl = np.asarray(dl, float); dh = np.asarray(dh, float)
    dlp = np.concatenate([[dl[0]], dl[:-1]]); dhp = np.concatenate([[dh[0]], dh[:-1]])
    if is_long:
        down = (dl < dlp) & (dh < dhp); up = (dl > dlp) & (dh > dhp)        # LL+LH exit, HL+HH re-enter
    else:
        down = (dl > dlp) & (dh > dhp); up = (dl < dlp) & (dh < dhp)        # mirror for short
    sig = np.full(len(w), np.nan); sig[down] = 0.0; sig[up] = 1.0
    idx = np.maximum.accumulate(np.where(~np.isnan(sig), np.arange(len(w)), 0))
    allow = sig[idx]; allow[np.isnan(allow)] = 1.0
    return np.where(allow > 0, w, 0.0)


def _expansion_allowed(ind, tf, win, w):
    """Per-bar bool: an OPEN (run start of w>0) is allowed only if band width is EXPANDING (prior-bar width >
    its rolling mean). Once a run is allowed it stays allowed until it ends. Vectorized, no look-ahead."""
    close = np.asarray(ind["close"], float); n = len(close)
    hi = ind.get(f"bb_high_{tf}"); lo = ind.get(f"bb_low_{tf}")
    if hi is not None and lo is not None:
        wd = (np.asarray(hi, float) - np.asarray(lo, float)) / np.maximum(close, 1e-9)
    else:
        atr = ind.get(f"atr_{tf}")
        if atr is None:
            return np.ones(n, bool)                                    # field missing => don't block
        wd = np.asarray(atr, float) / np.maximum(close, 1e-9)
    wdp = np.concatenate([[wd[0]], wd[:-1]])                           # prior bar
    base = np.concatenate([[0.0], np.cumsum(wdp)])
    i = np.arange(n); a = np.maximum(0, i - win + 1); cnt = (i - a + 1).astype(float)
    expanding = wdp > (base[i + 1] - base[a]) / cnt
    raw_in = w > 0
    prev_in = np.concatenate([[False], raw_in[:-1]])
    starts = raw_in & ~prev_in
    sig = np.full(n, np.nan)
    sig[~raw_in] = 0.0
    sig[starts] = expanding[starts].astype(float)                      # allowed iff expanding at the open
    have = ~np.isnan(sig); idx = np.where(have, np.arange(n), 0)
    idx = np.maximum.accumulate(idx); allowed = sig[idx]; allowed[np.isnan(allowed)] = 0.0
    return allowed > 0


def _bb_width_mult(ind, tf, win, lo, hi):
    """Per-bar size multiplier from BB width relative to its rolling-median baseline (prior bar, no look-ahead).
    width = (bb_high-bb_low)/close; falls back to atr/close if explicit bands for `tf` are absent. Returns array."""
    close = np.asarray(ind["close"], float); n = len(close)
    hi_a = ind.get(f"bb_high_{tf}"); lo_a = ind.get(f"bb_low_{tf}")
    if hi_a is not None and lo_a is not None:
        width = (np.asarray(hi_a, float) - np.asarray(lo_a, float)) / np.maximum(close, 1e-9)
    else:
        atr = ind.get(f"atr_{tf}")
        if atr is None:
            return np.ones(n)
        width = np.asarray(atr, float) / np.maximum(close, 1e-9)
    wp = np.concatenate([[width[0]], width[:-1]])                       # prior bar
    base = np.concatenate([[0.0], np.cumsum(wp)])
    i = np.arange(n); a = np.maximum(0, i - win + 1); cnt = (i - a + 1).astype(float)
    med = (base[i + 1] - base[a]) / cnt                                # rolling MEAN as baseline (cheap)
    ratio = np.where(med > 1e-12, wp / med, 1.0)
    return np.clip(ratio, lo, hi)


def _ffill_pos(enter, exit_):
    """Vectorized state machine: position ON at `enter`, OFF at `exit_` (exit wins ties), ffill between."""
    n = len(enter); sig = np.full(n, np.nan)
    sig[enter] = 1.0; sig[exit_] = 0.0
    have = ~np.isnan(sig); idx = np.where(have, np.arange(n), 0)
    idx = np.maximum.accumulate(idx); pos = sig[idx]; pos[np.isnan(pos)] = 0.0
    return pos


def _above(close: float, sma: float, is_long: bool) -> bool:
    if sma is None or sma <= 0:
        return False
    return (close > sma) if is_long else (close < sma)


def _rising(sma_now: float, sma_lag: float, is_long: bool) -> bool:
    if sma_now is None or sma_lag is None or sma_now <= 0 or sma_lag <= 0:
        return False
    return (sma_now > sma_lag) if is_long else (sma_now < sma_lag)


def desired_weight_scalar(close, smas: dict, smas_lag: dict, realized_vol, is_long: bool,
                          regime_tf=DEFAULTS["regime_tf"], exit_tf=DEFAULTS["exit_tf"],
                          add_mult=DEFAULTS["add_mult"], size_cap=DEFAULTS["size_cap"],
                          vol_target=DEFAULTS["vol_target"], ladder_tfs: Sequence[str] = LADDER_TFS,
                          scale_in=DEFAULTS["scale_in"]) -> float:
    """LIVE per-bar form. smas/smas_lag are {tf: sma_200_<tf> value} for the prior bar (no look-ahead).
    realized_vol = rolling std of returns (or None to skip vol-target). Returns desired weight >= 0."""
    htf_up = _above(close, smas.get(regime_tf), is_long) and _rising(smas.get(regime_tf), smas_lag.get(regime_tf), is_long)
    if scale_in and htf_up:
        reclaimed = sum(1 for tf in ladder_tfs if _above(close, smas.get(tf), is_long))
        w = min(size_cap, 1.0 + add_mult * reclaimed)
    elif htf_up:
        w = 1.0
    else:
        w = 1.0 if _above(close, smas.get(exit_tf), is_long) else 0.0
    if vol_target and vol_target > 0 and realized_vol and realized_vol > 1e-9:
        w *= min(1.0, vol_target / realized_vol)
    return float(max(0.0, w))


def desired_weight_vec(ind: dict, is_long: bool, regime_tf=DEFAULTS["regime_tf"], exit_tf=DEFAULTS["exit_tf"],
                       add_mult=DEFAULTS["add_mult"], size_cap=DEFAULTS["size_cap"],
                       vol_target=DEFAULTS["vol_target"], rise_lag=DEFAULTS["rise_lag"],
                       ladder_tfs: Sequence[str] = LADDER_TFS, scale_in=DEFAULTS["scale_in"],
                       realized_vol: Optional[np.ndarray] = None,
                       range_mode=DEFAULTS["range_mode"], range_tf=DEFAULTS["range_tf"], adx_tf=DEFAULTS["adx_tf"],
                       adx_range=DEFAULTS["adx_range"], adx_trend=DEFAULTS["adx_trend"],
                       range_buy=DEFAULTS["range_buy"], range_sell=DEFAULTS["range_sell"],
                       bb_width_size=DEFAULTS["bb_width_size"], bb_width_tf=DEFAULTS["bb_width_tf"],
                       bb_width_win=DEFAULTS["bb_width_win"], bb_width_lo=DEFAULTS["bb_width_lo"],
                       bb_width_hi=DEFAULTS["bb_width_hi"], require_bb_expansion=DEFAULTS["require_bb_expansion"],
                       expansion_tf=DEFAULTS["expansion_tf"], expansion_win=DEFAULTS["expansion_win"],
                       pct_ladder=DEFAULTS["pct_ladder"], pct_entry=DEFAULTS["pct_entry"],
                       per_pct_mult=DEFAULTS["per_pct_mult"], gain_bonus_k=DEFAULTS["gain_bonus_k"],
                       streak_bonus=DEFAULTS["streak_bonus"], streak_n=DEFAULTS["streak_n"],
                       struct_exit_tf=DEFAULTS["struct_exit_tf"], require_wt_turn=DEFAULTS["require_wt_turn"]) -> np.ndarray:
    """BACKTEST array form. ind has 'close' + 'sma_200_<tf>'. Uses PRIOR bar (shift 1) for every decision
    input → no look-ahead. Returns desired-weight array aligned to bars (weight[i] applies entering bar i)."""
    close = np.asarray(ind["close"], float)
    n = len(close)
    cp = np.concatenate([[close[0]], close[:-1]])                       # prior-bar close
    def sma_prev(tf):
        s = ind.get(f"sma_200_{tf}")
        if s is None:
            return None
        s = np.asarray(s, float)
        return np.concatenate([[s[0]], s[:-1]])
    reg = sma_prev(regime_tf); ext = sma_prev(exit_tf)
    if reg is None or ext is None:
        return np.zeros(n)
    reg_lag = np.concatenate([reg[:rise_lag].mean().repeat(rise_lag) if rise_lag < n else reg, reg[:-rise_lag]]) if rise_lag < n else reg
    if is_long:
        htf_up = (reg > 0) & (cp > reg) & (reg > reg_lag)
    else:
        htf_up = (reg > 0) & (cp < reg) & (reg < reg_lag)
    # ladder reclaim count
    reclaimed = np.zeros(n)
    for tf in ladder_tfs:
        s = sma_prev(tf)
        if s is None:
            continue
        reclaimed += ((s > 0) & (cp > s)) if is_long else ((s > 0) & (cp < s))
    w_scale = np.minimum(size_cap, 1.0 + add_mult * reclaimed)
    w_ride = np.ones(n)
    above_exit = ((ext > 0) & (cp > ext)) if is_long else ((ext > 0) & (cp < ext))
    w_chop = np.where(above_exit, 1.0, 0.0)
    if scale_in:
        w = np.where(htf_up, w_scale, w_chop)
    else:
        w = np.where(htf_up, w_ride, w_chop)
    if pct_ladder:                                                         # RULE A overrides the staircase weight
        wa = _rule_a_weight(ind, is_long, pct_entry, per_pct_mult, size_cap, gain_bonus_k, require_wt_turn=require_wt_turn)
        if wa is not None:
            w = wa
    if vol_target and vol_target > 0 and realized_vol is not None:
        rv = np.asarray(realized_vol, float)
        rv = np.where(rv < 1e-9, vol_target, rv)
        w = w * np.minimum(1.0, vol_target / rv)
    if range_mode:
        # RANGE: low ADX => mean-revert (buy %b-low / sell %b-high) instead of trend-riding. Hand back to the
        # trend weight only on a CONFIRMED breakout (ADX>=adx_trend AND price beyond the range_tf Donchian).
        def _prev(key):
            a = ind.get(key)
            if a is None:
                return None
            a = np.asarray(a, float)
            return np.concatenate([[a[0]], a[:-1]])
        adx = _prev(f"adx_{adx_tf}"); bbp = _prev(f"bb_pct_{range_tf}")
        dch = _prev(f"dc_high_{range_tf}"); dcl = _prev(f"dc_low_{range_tf}")
        dch_ant = _prev(f"dc_high_{range_tf}_ant"); dcl_ant = _prev(f"dc_low_{range_tf}_ant")
        if adx is not None and bbp is not None and dch is not None and dcl is not None:
            ranging = adx < adx_range
            # DONCHIAN SLOPE (USER 2026-05-30): a falling lower band = lows DESCENDING = NOT a range, it's a
            # downtrend (falling knife) — block long mean-rev there; a rising upper band = highs ascending =
            # uptrend — block short mean-rev there. True range = the relevant band is flat/holding.
            lows_falling = (dcl_ant > dcl) if dcl_ant is not None else np.zeros(n, bool)
            highs_rising = (dch > dch_ant) if dch_ant is not None else np.zeros(n, bool)
            if is_long:
                breakout = (adx >= adx_trend) & (cp > dch)              # confirmed up-breakout
                enter_mr = ranging & (bbp <= range_buy) & (~lows_falling)   # bite bottom ONLY if floor holds
                exit_mr = (bbp >= range_sell) | (~ranging) | lows_falling   # sell top / range ends / floor breaks
            else:
                breakout = (adx >= adx_trend) & (cp < dcl)              # confirmed down-breakout
                enter_mr = ranging & (bbp >= range_sell) & (~highs_rising)  # short top ONLY if ceiling holds
                exit_mr = (bbp <= range_buy) | (~ranging) | highs_rising    # cover bottom / range ends / ceiling breaks
            w_mr = _ffill_pos(enter_mr, exit_mr)
            w = np.where(ranging & ~breakout, w_mr, w)                  # range bars => mean-revert; else trend
    if bb_width_size:
        w = w * _bb_width_mult(ind, bb_width_tf, bb_width_win, bb_width_lo, bb_width_hi)
    if require_bb_expansion:
        w = np.where(_expansion_allowed(ind, expansion_tf, expansion_win, w), w, 0.0)
    if struct_exit_tf:                                                     # RULE B: 3m/5m LL+LH exit / turn re-enter
        w = _rule_b_gate(ind, struct_exit_tf, w, is_long)
    if streak_bonus and streak_bonus > 0:                                  # PARITY-SAFE winners bonus
        w = w * streak_bonus_mult(close, w, is_long, streak_n, streak_bonus, size_cap)
    return np.maximum(0.0, w)
