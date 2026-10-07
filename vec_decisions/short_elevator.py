"""
vec_decisions/short_elevator.py — ELEVATOR-DOWN short strategy (NOT a mirrored long).
2026-05-30 USER MANDATE: "longs take the staircase up, shorts take the elevator down" — stop pretending a
short is an upside-down long.

WHY shorts differ (grounded in the volatility-asymmetry / leverage effect — see WebSearch 2026-05-30):
- Downmoves are WATERFALLS: fast, clustered, vol SPIKES on the way down (a 2% drop spikes vol ~2x more than a
  2% rise calms it). Forced deleveraging + panic-closing accelerate declines.
- Bear markets have violent COUNTER-TREND RALLIES (short squeezes) that shred a slow trend-follower — the exact
  whipsaw that made the mirrored-long short fail.
So the short engine must: (1) GATE on volatility EXPANSION (only short the waterfall, not the drift),
(2) ENTER FAST on a breakdown (Donchian-low break) OR fade a lower-high rejection (re-short the rally roll-over),
(3) COVER FAST into capitulation (oversold RSI / vol collapse / reclaim) — take the elevator profit before the
squeeze, (4) NEVER average down (no martingale; the elevator doesn't let you).

Shared scalar(live)+vec(backtest) core, parity-tested. Returns SHORT EXPOSURE weight >= 0 (caller shorts it).
Fields (NPZ + live dict): close, sma_200_<rtf>, sma_200_<ftf>, dc_low_<ftf>(+_prev), rsi_<ftf>. realized vol
is computed from close (short window vs baseline window).
"""
from __future__ import annotations
import numpy as np

DEFAULTS = dict(regime_tf="D", fast_tf="15m", vol_k=1.15, rsi_cover=25.0, size_cap=2.0,
                vol_win=48, vol_base_win=480, rise_lag=30, require_vol_expand=False,
                # FILTER->SIGNAL (USER 2026-05-31): relax_regime=False keeps the strict macro bear gate
                # (below FALLING regime-SMA — rare in a rally → only 0.04 trades/sym/day). True drops the
                # falling-regime requirement: bear := below the FAST SMA only, so the elevator shorts every
                # breakdown / rally-fade regardless of macro regime — the lever to lift shorts toward 1-8/day.
                relax_regime=False)


def _ffill_position(entry: np.ndarray, cover: np.ndarray) -> np.ndarray:
    """Vectorized state machine: short turns ON at entry, OFF at cover (cover wins ties). Forward-fill between."""
    n = len(entry)
    sig = np.full(n, np.nan)
    sig[entry] = 1.0
    sig[cover] = 0.0                              # cover takes priority over a same-bar entry
    have = ~np.isnan(sig)
    idx = np.where(have, np.arange(n), 0)
    idx = np.maximum.accumulate(idx)
    pos = sig[idx]
    pos[np.isnan(pos)] = 0.0
    return pos


def _realized_vol(close, win):
    n = len(close); ret = np.zeros(n); ret[1:] = np.diff(close) / close[:-1]
    c1 = np.concatenate([[0.0], np.cumsum(ret)]); c2 = np.concatenate([[0.0], np.cumsum(ret * ret)])
    i = np.arange(n); a = np.maximum(0, i - win + 1); cnt = (i - a + 1).astype(float)
    s1 = c1[i + 1] - c1[a]; s2 = c2[i + 1] - c2[a]
    return np.sqrt(np.maximum(s2 / cnt - (s1 / cnt) ** 2, 0.0))


def short_weight_vec(nd: dict, regime_tf=DEFAULTS["regime_tf"], fast_tf=DEFAULTS["fast_tf"],
                     vol_k=DEFAULTS["vol_k"], rsi_cover=DEFAULTS["rsi_cover"], size_cap=DEFAULTS["size_cap"],
                     vol_win=DEFAULTS["vol_win"], vol_base_win=DEFAULTS["vol_base_win"],
                     rise_lag=DEFAULTS["rise_lag"], require_vol_expand=DEFAULTS["require_vol_expand"],
                     relax_regime=DEFAULTS["relax_regime"]) -> np.ndarray:
    """Vectorized short exposure weight (>=0). All decision inputs use the PRIOR bar (shift 1) — no look-ahead."""
    close = np.asarray(nd["close"], float); n = len(close)
    cp = np.concatenate([[close[0]], close[:-1]])
    def prev(key):
        a = nd.get(key)
        if a is None:
            return None
        a = np.asarray(a, float)
        return np.concatenate([[a[0]], a[:-1]])
    smaR = prev(f"sma_200_{regime_tf}"); smaF = prev(f"sma_200_{fast_tf}")
    dclow = prev(f"dc_low_{fast_tf}"); rsiF = prev(f"rsi_{fast_tf}")
    if smaR is None or smaF is None or dclow is None or rsiF is None:
        return np.zeros(n)
    rlag = np.concatenate([np.repeat(smaR[:rise_lag].mean(), rise_lag), smaR[:-rise_lag]]) if rise_lag < n else smaR
    if relax_regime:
        bear = (smaF > 0) & (cp < smaF)                            # FILTER->SIGNAL: below FAST SMA only (any regime)
    else:
        bear = (smaR > 0) & (cp < smaR) & (smaR < rlag)             # macro down-regime: below falling D-SMA
    rv = _realized_vol(close, vol_win); rvb = _realized_vol(close, vol_base_win)
    rvb = np.where(rvb < 1e-9, rv, rvb)
    vol_expand = rv > (rvb * vol_k)                                 # waterfall flag (size booster, not a hard gate)
    cpp = np.concatenate([[cp[0]], cp[:-1]]); smaFp = np.concatenate([[smaF[0]], smaF[:-1]])
    breakdown = cp < dclow                                          # new lower low / Donchian break (fast)
    rally_fade = (cpp >= smaFp) & (cp < smaF)                       # bounce rolled over: cross down thru fast SMA
    # ELEVATOR entry: in the bear regime, short every fresh breakdown OR rally-rejection. vol_expand is a SIZE
    # booster below (not a gate) so the book actually trades; require_vol_expand=True restores the strict gate.
    trig = breakdown | rally_fade
    enter = bear & trig & (vol_expand if require_vol_expand else True)
    capitulation = rsiF < rsi_cover                                 # oversold panic -> cover fast
    reclaim = cp > smaF                                             # bounce taking hold -> cover
    vol_collapse = rv < (rvb * 0.9)                                 # waterfall done -> cover
    cover = capitulation | reclaim | vol_collapse | (~bear)
    pos = _ffill_position(enter, cover)
    # size up modestly when vol is expanding hard (the elevator), capped; never average down (flat 'pos' size)
    boost = np.clip(rv / (rvb + 1e-12), 1.0, size_cap)
    return pos * np.minimum(size_cap, boost)


def short_weight_scalar(state: dict, bar: dict, regime_tf=DEFAULTS["regime_tf"], fast_tf=DEFAULTS["fast_tf"],
                        vol_k=DEFAULTS["vol_k"], rsi_cover=DEFAULTS["rsi_cover"], size_cap=DEFAULTS["size_cap"],
                        require_vol_expand=DEFAULTS["require_vol_expand"]) -> float:
    """LIVE per-bar form. `bar` holds PRIOR-bar values: cp, cpp, smaR, smaR_lag, smaF, smaF_prev, dclow, rsiF,
    rv, rvb. `state` persists {'short': bool}. Returns short exposure weight >=0. Same logic as vec."""
    cp = bar["cp"]; smaR = bar["smaR"]; smaF = bar["smaF"]; dclow = bar["dclow"]; rsiF = bar["rsiF"]
    rv = bar["rv"]; rvb = bar["rvb"] if bar["rvb"] > 1e-9 else bar["rv"]
    bear = smaR > 0 and cp < smaR and smaR < bar["smaR_lag"]
    vol_expand = rv > rvb * vol_k
    breakdown = cp < dclow
    rally_fade = bar["cpp"] >= bar["smaF_prev"] and cp < smaF
    enter = bear and (breakdown or rally_fade) and (vol_expand if require_vol_expand else True)
    cover = (rsiF < rsi_cover) or (cp > smaF) or (rv < rvb * 0.9) or (not bear)
    if cover:
        state["short"] = False
    elif enter:
        state["short"] = True
    if not state.get("short"):
        return 0.0
    boost = min(size_cap, max(1.0, rv / (rvb + 1e-12)))
    return boost
