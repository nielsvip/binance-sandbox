"""LR_BAND_LADDER ordinary-parity AUGMENT — vec twin of tradier_manage._ordinary_ladder_target (tradier_manage.py:1480-1625) used by evaluate_augment (21172-21200). [N4]
LIVE: only when LR_BAND_LADDER_ENABLED and LR_BAND_LADDER_ORDINARY_PARITY_ENABLED (both default False): per completed parent (D, 4h, 1h) an event
(pct_b = lrL_pct_b_TF, wt_cross BULL/BEAR, structure = higher-high/higher-low + stoch extreme turn) is resolved by the SHARED PURE CONTRACT
ordinary_ladder_contract.strongest_absolute_target (imported here: identical code, not a re-implementation): one strongest ABSOLUTE target notional,
each (tf, parent id) consumed once (`seen`), add = target - current notional capped at LR_BAND_LADDER_CAPACITY_USD. Returns BEFORE the profit/cooldown gates
and bypasses the opening buffer (live: `_in_buf and not parity`).
VECTOR inputs (NPZ, 15m rows, parent arrays broadcast): timestamp_TF = parent identity (source_close_ts), lrL_pct_b_TF, wt_cross_bull_TF/bear_TF, high_TF/low_TF/stoch_k_TF
and the PREVIOUS parent's values (value at the last row before the current parent started). availability_ts = row timestamp.
APPROXIMATION (honest): the NPZ parent arrays may reflect the FORMING parent on intra-parent rows, live reads the published completed snapshot; the consume-once `seen`
logic limits each parent to one decision but the pct_b/structure seen at the first row of a new parent can differ from live's completed snapshot."""
from __future__ import annotations
import numpy as np

TFS = ("D", "4h", "1h")


def _prev_parent(arr, tok):
    n = len(arr)
    change = np.concatenate(([True], tok[1:] != tok[:-1]))
    start = np.maximum.accumulate(np.where(change, np.arange(n), 0))
    return arr[np.maximum(start - 1, 0)]


def precompute(npz, n, safe):
    pre = {}
    for tf in TFS:
        tok = np.asarray(safe(npz, f"timestamp_{tf}", n), dtype=np.float64)
        d = {"tok": tok}
        for stem in ("high", "low", "stoch_k"):
            a = np.asarray(safe(npz, f"{stem}_{tf}", n), dtype=np.float64)
            d[stem] = a
            d[stem + "_prev"] = _prev_parent(a, tok)
        d["pct_b"] = np.asarray(safe(npz, f"lrL_pct_b_{tf}", n), dtype=np.float64)
        d["bull"] = np.asarray(safe(npz, f"wt_cross_bull_{tf}", n), dtype=np.float64) > 0.5
        d["bear"] = np.asarray(safe(npz, f"wt_cross_bear_{tf}", n), dtype=np.float64) > 0.5
        pre[tf] = d
    return pre


def target(cfg, is_long, i, pre, avail_ts, current_notional, seen):
    """-> TargetOrder | None (identical contract call as live)."""
    import ordinary_ladder_contract as C
    stoch_limit = float(getattr(cfg, "LR_BAND_LADDER_STOCH_EXTREME", 30.0))
    events = []
    for tf in TFS:
        d = pre[tf]
        high, hp, low, lp = d["high"][i], d["high_prev"][i], d["low"][i], d["low_prev"][i]
        st, sp = d["stoch_k"][i], d["stoch_k_prev"][i]
        structure = (high > hp > 0 and low > lp > 0 and st <= stoch_limit and st > sp) if is_long else (high < hp and high > 0 and low < lp and low > 0 and st >= 100.0 - stoch_limit and st < sp)
        cross = "BULL" if d["bull"][i] else ("BEAR" if d["bear"][i] else "NONE")
        events.append(C.CompletedParentEvent(timeframe=tf, source_close_ts=int(d["tok"][i]), availability_ts=int(avail_ts), pct_b=float(d["pct_b"][i]) if d["pct_b"][i] == d["pct_b"][i] else float("nan"), wt_cross=cross, structure=bool(structure)))
    bot, top = getattr(cfg, "LR_BAND_LADDER_TF_BOTTOM", {}) or {}, getattr(cfg, "LR_BAND_LADDER_TF_TOP", {}) or {}
    pairs = {tf: (float(bot.get(tf, getattr(cfg, "LR_BAND_LADDER_BOTTOM_MULT", 10.0))), float(top.get(tf, getattr(cfg, "LR_BAND_LADDER_TOP_MULT", 3.0)))) for tf in TFS}
    return C.strongest_absolute_target(events, side="LONG" if is_long else "SHORT", trigger=str(getattr(cfg, "LR_BAND_LADDER_TRIGGER", "union")), current_notional_usd=current_notional,
                                       pairs=pairs, mode=str(getattr(cfg, "LR_BAND_LADDER_MODE", "center_plateau")), base_unit_usd=float(getattr(cfg, "LR_BAND_LADDER_BASE_UNIT_USD", 2000.0)),
                                       capacity_usd=float(getattr(cfg, "LR_BAND_LADDER_CAPACITY_USD", 16000.0)), seen=seen)
