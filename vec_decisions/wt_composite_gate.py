"""wt_composite_gate — vector twin of the HARD block in ez_positions_quick.AdvancedSignalRater.rate() lines 2493-2512 (N1/004):
  active iff WT_COMPOSITE_HTF_GATE and (WT_COMPOSITE_SCORING_ENABLED or WT_COMPOSITE_SCORING_ENABLED_TRADIER)  [live: _wt_htf_gate]
  entry blocked (WAIT) when  side_alignment < 3  OR  side_composite < WT_COMPOSITE_ENTRY_BLOCK (default -20)
  side_alignment = wt_bull_alignment (long) / wt_bear_alignment (short): count of the 5 WT TFs [3m,15m,1h,4h,D] with wt1>wt2 (ez_indicators._inject_wt_composite 4176-4185);
  side_composite = wt_composite_long / wt_composite_short.
3m NOTE: the NPZ array wt_bull_alignment was built by the precompute with the base-TF stand-in for the 3m member (no 3m data in the backtest system, user order) — documented, not corrected here.
Returns a boolean BLOCK mask (True = blocked) or None when the gate is inactive."""
import numpy as np


def block_mask(npz, n, is_long, cfg, _safe):
    if not bool(getattr(cfg, "WT_COMPOSITE_HTF_GATE", False)):
        return None
    if not (bool(getattr(cfg, "WT_COMPOSITE_SCORING_ENABLED", False)) or bool(getattr(cfg, "WT_COMPOSITE_SCORING_ENABLED_TRADIER", False))):
        return None
    align = _safe(npz, "wt_bull_alignment" if is_long else "wt_bear_alignment", n, 0.0)
    comp = _safe(npz, "wt_composite_long" if is_long else "wt_composite_short", n, 0.0)
    blk = float(getattr(cfg, "WT_COMPOSITE_ENTRY_BLOCK", -20.0))
    return (np.asarray(align) < 3) | (np.asarray(comp) < blk)
