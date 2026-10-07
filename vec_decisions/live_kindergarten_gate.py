"""live_kindergarten_gate (Agent C, batch b1, 2026-10-01) — numpy twin of ez_manage._kindergarten_ema_gate (ez_manage.py 344-400), the KINDERGARTEN HTF trend gate
that LIVE CRYPTO applies as a HARD BLOCK on technical-indicator entries (check_entry_alignment 495-498: `if not _kg_ok and _is_crypto_live: return False`).

Live semantics (per call, per bar here): iterate TFs D, 4h, 1h, 15m; the FIRST TF with ema_200_{tf} and sma_200_{tf} both present and non-zero decides:
  LONG  ok = price > ema_200 AND price > sma_200 AND (ema_9_above_21 if present else True) AND (cross50 if computable else True)
  SHORT ok = price < ema_200 AND price < sma_200 AND (NOT ema_9_above_21 if present else True) AND (NOT cross50 if computable else True)
  price == 0 -> allow; no TF with data -> allow.  cross50 = ema_21 > sma_50 (or ema_50 > sma_50): NPZ has NO ema_21_{tf}/sma_50_{tf} -> cross50 is 'not computable' -> True
  (documented data gap, live_gaps.json LG-06). Gate only active when KINDERGARTEN_EMA_GATE_ENABLED (live crypto default False).
The stocks live KG (tradier_manage._v12_kindergarten_entry_allowed) is an ADDITIVE entry claim, not a block — the vec additive KG stays for stocks."""
import numpy as np


def kg_block_mask(npz, n, is_long, close, safe, cfg=None):
    price = np.asarray(close, dtype=float)
    block = np.zeros(n, dtype=bool)
    undecided = np.ones(n, dtype=bool)
    _tf1 = str(getattr(cfg, "KINDERGARTEN_FILTER_TF", "OFF") or "OFF").strip() if cfg is not None else "OFF"
    for tf in ((_tf1,) if _tf1 in ("D", "4h", "1h", "15m") else ("D", "4h", "1h", "15m")):
        ema = np.asarray(safe(npz, f"ema_200_{tf}", n, 0.0), dtype=float)
        sma = np.asarray(safe(npz, f"sma_200_{tf}", n, 0.0), dtype=float)
        ab_raw = np.asarray(safe(npz, f"ema_9_above_21_{tf}", n, np.nan), dtype=float)
        valid = (ema != 0) & (sma != 0) & undecided
        nop = valid & (price == 0)          # live: price==0 -> allow and stop
        undecided = undecided & ~nop
        valid = valid & ~nop
        have_ab = ~np.isnan(ab_raw)
        ab_true = np.where(have_ab, ab_raw > 0.5, True)
        if is_long:
            ok = (price > ema) & (price > sma) & ab_true
        else:
            ok = (price < ema) & (price < sma) & np.where(have_ab, ~(ab_raw > 0.5), True)
        block = np.where(valid, ~ok, block)
        undecided = undecided & ~valid
    return block
