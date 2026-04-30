#!/usr/bin/env python3
"""
wt_dc_hierarchy.py — TRUE TF-HIERARCHY state machine (2026-04-21 rewrite).

User directive: "REWRITE THE ENTIRE wt_dc system SO IT UNDERSTANDS THE RELATIONSHIPS WITHIN TF's"
Core rule (user, verbatim):
    "if a small tf is at extreme levels the next one's delta gives the answer
     as to where this is going; if also at extreme the next one; up to W/M"

This is NOT parallel alignment. This is a per-bar state machine that climbs
the hierarchy until it finds a TF that's NOT at extreme — at which point that
TF's delta becomes the deciding answer.

───────────────────────────────────────────────────────────────────────────────
LONG EXIT cascade (we hold LONG, check for reversal):
  start: anchor = 0 (LTF)
  while anchor < K:
    if not at_upper[anchor]: cascade breaks — no exit signal from this side
    else: climb → check TF above
      if anchor+1 == K: top reached; if delta_bear[anchor] → EXIT (super-trend reversal)
      elif at_upper[anchor+1]: anchor += 1 (keep climbing)
      else: found "next TF that's not extreme"
        if delta_bear[anchor+1]: EXIT — the next-up TF is rolling over
        elif delta_bull[anchor+1]: HOLD — breakout confirmed by HTF
        else: HOLD — neutral
    break

LONG ENTRY cascade (setup for bounce):
  LTF at_lower is the setup. Climb up:
  if any HTF at_upper → REJECT (overhead resistance)
  else cascade: first TF not at lower, check delta
    delta_bull → ACCEPT (bounce confirmed by HTF)
    delta_bear → REJECT (HTF still falling against us)
    neutral → REJECT by default (no confirmation)

SHORT: mirror.

REENTRY flag: set on exit firing when HTF momentum is STILL with our original direction
(LTF exit + anchored HTF's delta still bull = premature exit, reenter on next LTF bounce).
───────────────────────────────────────────────────────────────────────────────

Also exposes DIVERGENCE detection: HTF delta FLIPS against LTF delta (bearish divergence
on LTF while HTF bullish, or vice versa). Strong reversal signal.
"""
from typing import Dict, List, Tuple

import numpy as np


TF_ORDER_CRYPTO = ("3m", "15m", "1h", "4h", "D")
TF_ORDER_TRADIER = ("5m", "15m", "1h", "4h", "D")


def _safe(npz: dict, key: str, n: int, default: float = 0.0) -> np.ndarray:
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


def _resolve_tfs(cfg) -> Tuple[str, ...]:
    mode = str(getattr(cfg, "MODE", "crypto"))
    use_w_m = bool(getattr(cfg, "HIER_USE_W_M", False))
    base = TF_ORDER_TRADIER if mode == "tradier" else TF_ORDER_CRYPTO
    if use_w_m:
        return tuple(list(base) + ["W", "M"])
    return base


def compute_per_tf_signals(npz: dict, n: int, cfg) -> Tuple[List[str], Dict[str, np.ndarray]]:
    """Per-TF masks. Returns ordered TF list + stacked masks keyed by signal type (K x n)."""
    tfs = _resolve_tfs(cfg)
    rz_top = float(getattr(cfg, "HIER_RZ_TOP_BB", 0.85))
    rz_bot = float(getattr(cfg, "HIER_RZ_BOT_BB", 0.15))
    dc_band_pct = float(getattr(cfg, "HIER_DC_BAND_PCT", 0.2)) / 100.0
    wt_delta_min = float(getattr(cfg, "HIER_WT_DELTA_MIN", 0.0))
    vel_min = float(getattr(cfg, "HIER_WT_VEL_MIN", 0.0))
    active = []
    up_list, lo_list, dbull_list, dbear_list = [], [], [], []
    wt_delta_list, wt_vel_list = [], []
    for tf in tfs:
        close = _safe(npz, f"close_{tf}", n)
        dc_hi = _safe(npz, f"dc_high_{tf}", n)
        dc_lo = _safe(npz, f"dc_low_{tf}", n)
        wt1 = _safe(npz, f"wt1_{tf}", n)
        wt2 = _safe(npz, f"wt2_{tf}", n)
        wt_vel = _safe(npz, f"wt_velocity_{tf}", n)
        bb_pctb = _safe(npz, f"bb_pct_b_{tf}", n, 0.5)
        if wt1.sum() == 0 and wt2.sum() == 0 and dc_hi.sum() == 0 and dc_lo.sum() == 0:
            continue
        dc_hi_prev = np.roll(dc_hi, 1); dc_hi_prev[0] = dc_hi[0]
        dc_lo_prev = np.roll(dc_lo, 1); dc_lo_prev[0] = dc_lo[0]
        at_upper_dc = (dc_hi_prev > 0) & (close >= dc_hi_prev * (1.0 - dc_band_pct))
        at_lower_dc = (dc_lo_prev > 0) & (close <= dc_lo_prev * (1.0 + dc_band_pct))
        at_upper_bb = bb_pctb >= rz_top
        at_lower_bb = bb_pctb <= rz_bot
        at_upper = at_upper_dc | at_upper_bb
        at_lower = at_lower_dc | at_lower_bb
        wt_delta = wt1 - wt2
        delta_bull = (wt_delta > wt_delta_min) & (wt_vel > vel_min)
        delta_bear = (wt_delta < -wt_delta_min) & (wt_vel < -vel_min)
        active.append(tf)
        up_list.append(at_upper)
        lo_list.append(at_lower)
        dbull_list.append(delta_bull)
        dbear_list.append(delta_bear)
        wt_delta_list.append(wt_delta)
        wt_vel_list.append(wt_vel)
    if not active:
        empty = np.zeros((0, n), dtype=bool)
        return [], {"at_upper": empty, "at_lower": empty, "delta_bull": empty,
                    "delta_bear": empty, "wt_delta": np.zeros((0, n)), "wt_vel": np.zeros((0, n))}
    return active, {
        "at_upper": np.stack(up_list),
        "at_lower": np.stack(lo_list),
        "delta_bull": np.stack(dbull_list),
        "delta_bear": np.stack(dbear_list),
        "wt_delta": np.stack(wt_delta_list),
        "wt_vel": np.stack(wt_vel_list),
    }


def _cascade_exit_long_vec(masks: Dict[str, np.ndarray]) -> Tuple[np.ndarray, np.ndarray]:
    """LONG exit cascade — vectorized implementation of the state machine.

    Returns (exit_mask, reentry_if_momentum_mask).

    Cascade rule:
      Find smallest k* in [0, K-1] such that at_upper[0..k*-1] all True AND at_upper[k*] False.
        i.e. k* = first TF that breaks the "all lower extreme" chain.
      If k* exists: delta_bear[k*] → EXIT.
      If all K TFs at upper (no k* found): delta_bear[K-1] → EXIT (super-trend reversal at top).
      Otherwise: no exit.

    reentry_if_momentum = EXIT fires AND the anchored TF's delta is still bullish
                         (means exit was at LTF rollover but HTF momentum intact — reenter on next setup).
    """
    at_upper = masks["at_upper"]     # (K, n)
    delta_bear = masks["delta_bear"]
    delta_bull = masks["delta_bull"]
    K, n = at_upper.shape
    if K == 0:
        return np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    exit_mask = np.zeros(n, dtype=bool)
    reentry_mask = np.zeros(n, dtype=bool)
    # cum_upper[k] = all TFs 0..k at upper
    cum_upper = np.cumprod(at_upper.astype(np.int8), axis=0).astype(bool)
    # For each k from 0 to K-1, cascade terminates at k iff:
    #   k == 0: at_upper[0] False → no cascade, skip
    #   k == K-1 and cum_upper[K-1]: all at upper → use delta_bear at top
    #   otherwise: cum_upper[k-1] True AND at_upper[k] False → terminate at k
    # But the exit rule says EXIT if delta_bear at the TERMINATING TF.
    # BUT user's rule: "small tf at extreme → next tf's delta gives the answer"
    # So if LTF extreme AND next NOT extreme → next TF's delta is the answer.
    # Terminating TF is where we STOP climbing; its delta is the answer.
    for k in range(1, K):
        # cascade requires LTF (index 0) at extreme → otherwise no cascade at all
        terminates = cum_upper[k-1] & ~at_upper[k]
        exit_here = terminates & delta_bear[k]
        exit_mask |= exit_here
        # reentry_if_momentum: if terminating TF's delta is still bullish AND a reversal flagged
        # elsewhere (e.g. divergence), we'd reenter. Simplified: exit + HTF delta still bull.
        # Here we set reentry flag when terminating TF has delta_bull (no exit fired there, but
        # we track it for the divergence cross-check below).
    # Super-trend reversal: all at upper + top TF delta bear
    all_upper = cum_upper[K-1]
    exit_mask |= (all_upper & delta_bear[K-1])
    # Divergence exit: LTF at upper AND LTF delta_bear AND any HTF delta_bull (classic divergence)
    ltf_upper_bear = at_upper[0] & delta_bear[0]
    if K >= 2:
        htf_bull_any = delta_bull[1:].any(axis=0)
        div_exit = ltf_upper_bear & htf_bull_any
        # Set reentry flag only for divergence exits (exit fired but HTF still bull → momentum intact)
        reentry_mask |= (div_exit & exit_mask)
    return exit_mask, reentry_mask


def _cascade_exit_short_vec(masks: Dict[str, np.ndarray]) -> Tuple[np.ndarray, np.ndarray]:
    """SHORT exit — mirror of LONG. Cascade at_lower extremes, exit on delta_bull."""
    at_lower = masks["at_lower"]
    delta_bull = masks["delta_bull"]
    delta_bear = masks["delta_bear"]
    K, n = at_lower.shape
    if K == 0:
        return np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    exit_mask = np.zeros(n, dtype=bool)
    reentry_mask = np.zeros(n, dtype=bool)
    cum_lower = np.cumprod(at_lower.astype(np.int8), axis=0).astype(bool)
    for k in range(1, K):
        terminates = cum_lower[k-1] & ~at_lower[k]
        exit_here = terminates & delta_bull[k]
        exit_mask |= exit_here
    all_lower = cum_lower[K-1]
    exit_mask |= (all_lower & delta_bull[K-1])
    ltf_lower_bull = at_lower[0] & delta_bull[0]
    if K >= 2:
        htf_bear_any = delta_bear[1:].any(axis=0)
        div_exit = ltf_lower_bull & htf_bear_any
        reentry_mask |= (div_exit & exit_mask)
    return exit_mask, reentry_mask


def _cascade_entry_long_vec(masks: Dict[str, np.ndarray]) -> np.ndarray:
    """LONG entry cascade.

    Setup: LTF at_lower (pullback to low extreme).
    Confirmation via cascade:
      - ANY HTF at_upper → REJECT (overhead resistance).
      - Else climb at_lower chain. First TF not at lower → its delta gives the answer:
          delta_bull → ACCEPT (HTF confirming bounce).
          else → REJECT (HTF not confirming).
      - All TFs at_lower (super-oversold): accept if top TF delta_bull, else reject.

    Also requires LTF delta_bull (LTF actually bouncing, not just sitting at extreme).
    """
    at_upper = masks["at_upper"]
    at_lower = masks["at_lower"]
    delta_bull = masks["delta_bull"]
    K, n = at_upper.shape
    if K == 0:
        return np.zeros(n, dtype=bool)
    ltf_setup = at_lower[0] & delta_bull[0]
    if K == 1:
        return ltf_setup
    htf_upper_any = at_upper[1:].any(axis=0)
    cum_lower = np.cumprod(at_lower.astype(np.int8), axis=0).astype(bool)
    # Find cascade terminator: first k>=1 where cum_lower[k-1] True AND at_lower[k] False → delta_bull[k] answer
    cascade_confirms = np.zeros(n, dtype=bool)
    for k in range(1, K):
        terminates = cum_lower[k-1] & ~at_lower[k]
        cascade_confirms |= (terminates & delta_bull[k])
    # Super-oversold: all at lower + top delta bull
    all_lower = cum_lower[K-1]
    cascade_confirms |= (all_lower & delta_bull[K-1])
    return ltf_setup & ~htf_upper_any & cascade_confirms


def _cascade_entry_short_vec(masks: Dict[str, np.ndarray]) -> np.ndarray:
    """SHORT entry — mirror."""
    at_upper = masks["at_upper"]
    at_lower = masks["at_lower"]
    delta_bear = masks["delta_bear"]
    K, n = at_upper.shape
    if K == 0:
        return np.zeros(n, dtype=bool)
    ltf_setup = at_upper[0] & delta_bear[0]
    if K == 1:
        return ltf_setup
    htf_lower_any = at_lower[1:].any(axis=0)
    cum_upper = np.cumprod(at_upper.astype(np.int8), axis=0).astype(bool)
    cascade_confirms = np.zeros(n, dtype=bool)
    for k in range(1, K):
        terminates = cum_upper[k-1] & ~at_upper[k]
        cascade_confirms |= (terminates & delta_bear[k])
    all_upper = cum_upper[K-1]
    cascade_confirms |= (all_upper & delta_bear[K-1])
    return ltf_setup & ~htf_lower_any & cascade_confirms


def compute_hierarchy_signals(npz: dict, n: int, is_long: bool, cfg) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Top-level: returns (entry_mask, exit_mask, reentry_if_momentum_mask) for given direction.

    reentry_if_momentum: bar-mask where exit fired but HTF momentum still with the position
    (indicates a premature LTF-level exit — caller can bypass reentry cooldowns on next setup).
    """
    tfs, masks = compute_per_tf_signals(npz, n, cfg)
    if not tfs:
        z = np.zeros(n, dtype=bool)
        return z, z, z
    if is_long:
        entry = _cascade_entry_long_vec(masks)
        exit_, reentry = _cascade_exit_long_vec(masks)
    else:
        entry = _cascade_entry_short_vec(masks)
        exit_, reentry = _cascade_exit_short_vec(masks)
    return entry, exit_, reentry


# ─────────────────────────────────────────────────────────────────────────────
# Backwards-compat shims: v8_quick_engine calls compute_hierarchy_signals() expecting
# a 2-tuple. Provide a wrapper that returns just (entry, exit).
# ─────────────────────────────────────────────────────────────────────────────

def compute_hierarchy_entry_exit(npz: dict, n: int, is_long: bool, cfg) -> Tuple[np.ndarray, np.ndarray]:
    """Back-compat: returns (entry, exit) only (drops reentry flag)."""
    e, x, _ = compute_hierarchy_signals(npz, n, is_long, cfg)
    return e, x


def compute_hierarchy_full(npz: dict, n: int, is_long: bool, cfg) -> Dict[str, np.ndarray]:
    """Rich output: entry, exit, reentry_if_momentum, plus per-TF diagnostics."""
    tfs, masks = compute_per_tf_signals(npz, n, cfg)
    if not tfs:
        z = np.zeros(n, dtype=bool)
        return {"tfs": [], "entry": z, "exit": z, "reentry": z}
    if is_long:
        entry = _cascade_entry_long_vec(masks)
        exit_, reentry = _cascade_exit_long_vec(masks)
    else:
        entry = _cascade_entry_short_vec(masks)
        exit_, reentry = _cascade_exit_short_vec(masks)
    return {
        "tfs": tfs,
        "entry": entry,
        "exit": exit_,
        "reentry": reentry,
        "at_upper": masks["at_upper"],
        "at_lower": masks["at_lower"],
        "delta_bull": masks["delta_bull"],
        "delta_bear": masks["delta_bear"],
    }


if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from v8_quick_engine import QuickConfig, load_npz
    stores = load_npz("crypto", ["BTCUSDC", "ETHUSDC"], "2022-01-01", "/Users/niels/Documents/binance/backtest_v8/indicators")
    cfg = QuickConfig()
    for sym, npz in stores.items():
        n = len(npz.get("timestamps", npz.get("timestamp_3m", [])))
        if n < 100:
            continue
        e_l, x_l, r_l = compute_hierarchy_signals(npz, n, True, cfg)
        e_s, x_s, r_s = compute_hierarchy_signals(npz, n, False, cfg)
        print(f"{sym}: n={n}")
        print(f"  LONG  entries={e_l.sum()}/{n} ({e_l.sum()/n*100:.2f}%) exits={x_l.sum()} reentries={r_l.sum()}")
        print(f"  SHORT entries={e_s.sum()}/{n} ({e_s.sum()/n*100:.2f}%) exits={x_s.sum()} reentries={r_s.sum()}")
