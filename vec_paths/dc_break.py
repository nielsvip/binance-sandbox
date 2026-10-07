"""
vec_paths/dc_break.py — Vectorized DC_BREAK_HIGH / DC_BREAK_LOW entry path.

Mirrors tradier_manage._check_dc_break() exactly:
  For each TF in ['5m', '15m']:
    dc_high_x = dc_high_crossover_{tf}  (binary flag in NPZ)
    above     = price > dc_high_{tf} * (1 + buf)
    LONG fires if (dc_high_x OR above)
                 AND (not stoch_filter OR stoch_k_15m <= K_EXHAUSTED_LONG)
                 AND (not require_expansion OR dc_high_1h > dc_high_1h_ant)
    SHORT mirror: dc_low_x OR below, k_15m >= K_EXHAUSTED_SHORT, expansion check
  Returns the WINNING signal (highest size_mult).

Source: tradier_manage.py:14624 (_check_dc_break)
        tradier_manage.py:14658 (_scan_dc_entries)
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Dict, Optional

if TYPE_CHECKING:
    from vec_engine_v1 import _NPZStore, VecConfig

_TF_MULTS = [("5m", 1.0), ("15m", 1.5)]


def check_dc_break_entry(
    store: "_NPZStore",
    bar_idx: int,
    cfg: "VecConfig",
) -> Optional[Dict]:
    """Return the best DC_BREAK signal or None.

    Return dict:
      {
        'side':      'LONG' | 'SHORT',
        'tf':        '5m' | '15m',
        'size_mult': float,
        'reason':    str,
      }
    Returns None when no signal fires.

    Reads NPZ fields (all pre-computed, same names as tradier_indicators):
      dc_high_{tf}, dc_low_{tf}
      dc_high_crossover_{tf}, dc_low_crossunder_{tf}
      dc_basis_{tf}
      dc_high_1h, dc_high_1h_ant
      dc_low_1h, dc_low_1h_ant
      stoch_k_15m
      close  (= current_price proxy)
    """
    if not getattr(cfg, "TRADIER_DC_DAYTRADE_ENABLED", False):
        return None

    price = store.f("close", bar_idx, 0.0)
    if price <= 0:
        return None

    buf = getattr(cfg, "TRADIER_DC_DAYTRADE_BUFFER", 0.001)
    require_expansion = getattr(cfg, "TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION", True)
    stoch_filter = getattr(cfg, "TRADIER_DC_DAYTRADE_STOCH_FILTER", True)
    k_15m = store.f("stoch_k_15m", bar_idx, 50.0)
    k_exh_long = getattr(cfg, "TRADIER_DC_DAYTRADE_K_EXHAUSTED_LONG", 85)
    k_exh_short = getattr(cfg, "TRADIER_DC_DAYTRADE_K_EXHAUSTED_SHORT", 15)

    dc_high_1h = store.f("dc_high_1h", bar_idx, 0.0)
    dc_high_1h_ant = store.f("dc_high_1h_ant", bar_idx, dc_high_1h)
    dc_low_1h = store.f("dc_low_1h", bar_idx, 0.0)
    dc_low_1h_ant = store.f("dc_low_1h_ant", bar_idx, dc_low_1h)

    candidates = []

    for tf, size_mult in _TF_MULTS:
        dc_high = store.f(f"dc_high_{tf}", bar_idx, 0.0)
        dc_low = store.f(f"dc_low_{tf}", bar_idx, 0.0)
        dc_high_x = store.b(f"dc_high_crossover_{tf}", bar_idx, False)
        dc_low_x = store.b(f"dc_low_crossunder_{tf}", bar_idx, False)

        above = dc_high > 0 and price > dc_high * (1.0 + buf)
        below = dc_low > 0 and price < dc_low * (1.0 - buf)

        if dc_high_x or above:
            if stoch_filter and k_15m > k_exh_long:
                pass
            elif require_expansion and dc_high_1h <= dc_high_1h_ant:
                pass
            else:
                candidates.append({
                    "side": "LONG",
                    "tf": tf,
                    "size_mult": size_mult,
                    "reason": f"DC_BREAK_HIGH_{tf.upper()}",
                })

        if dc_low_x or below:
            if stoch_filter and k_15m < k_exh_short:
                pass
            elif require_expansion and dc_low_1h >= dc_low_1h_ant:
                pass
            else:
                candidates.append({
                    "side": "SHORT",
                    "tf": tf,
                    "size_mult": size_mult,
                    "reason": f"DC_BREAK_LOW_{tf.upper()}",
                })

    if not candidates:
        return None

    return max(candidates, key=lambda s: s["size_mult"])
