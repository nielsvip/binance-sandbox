#!/usr/bin/env python3
"""v15_perf_sizing — performance-based position sizing (DAILY_OPTIMIZATION_PLAN.md).

Pure functions, no I/O, unit-tested. Maps a sym_side's gain% to a quantity multiplier and rounds to a
tradeable size per venue. The backtest gain% is size-normalized (crypto invariant; stocks pinned to a
reference); THIS layer scales the actual live position by performance and does NOT feed back into gain%.

Operator rules (2026-09-29):
- multiplier: negative gain -> 0.1x..1x (more negative -> toward 0.1x); positive gain -> 1x..5x.
- crypto: fractional qty (no rounding, no skip).
- stocks (whole shares): gain>0 -> min 1 share, conservative FLOOR (0.5->1 via min, 1.5->1, 2->2);
  gain<=0 -> FLOOR, and if <1 share (scaled size can't afford one share) -> SKIP.
- pos_cap/neg_cap = the gain% mapping to the 5x / 0.1x extreme (tunable; default 10%).
"""
from math import floor


def qty_multiplier(gain_pct, pos_cap=10.0, neg_cap=10.0):
    """gain_pct (percent) -> multiplier. 0->1x, +pos_cap->5x, -neg_cap->0.1x, clamped."""
    g = float(gain_pct)
    if g >= 0:
        return min(5.0, 1.0 + 4.0 * min(g, pos_cap) / pos_cap)
    return max(0.1, 1.0 - 0.9 * min(-g, neg_cap) / neg_cap)


def stock_shares(gain_pct, ref_size_usd, price, pos_cap=10.0, neg_cap=10.0):
    """Return (shares:int, reason). 0 shares => skip."""
    if not price or price <= 0:
        return 0, "no_price"
    mult = qty_multiplier(gain_pct, pos_cap, neg_cap)
    scaled_shares = mult * float(ref_size_usd) / float(price)
    if gain_pct > 0:
        return max(1, floor(scaled_shares)), f"min1_pos mult={mult:.3f}"
    s = floor(scaled_shares)
    if s < 1:
        return 0, f"skip_below_1_share mult={mult:.3f} scaled={scaled_shares:.3f}"
    return s, f"floor mult={mult:.3f}"


def crypto_qty(gain_pct, ref_size_usd, price, pos_cap=10.0, neg_cap=10.0):
    """Return (qty:float, reason). Fractional; no skip (any size tradeable)."""
    if not price or price <= 0:
        return 0.0, "no_price"
    mult = qty_multiplier(gain_pct, pos_cap, neg_cap)
    return mult * float(ref_size_usd) / float(price), f"crypto mult={mult:.3f}"


def _selftest():
    ok = True
    # multiplier mapping
    cases = [(0, 1.0), (10, 5.0), (5, 3.0), (20, 5.0), (-10, 0.1), (-5, 0.55), (-20, 0.1)]
    for g, want in cases:
        got = round(qty_multiplier(g), 4)
        flag = "" if abs(got - want) < 1e-6 else "  <-- MISMATCH"
        if flag:
            ok = False
        print(f"mult(gain={g:>4}) = {got}  (want {want}){flag}")
    print("--- stock rounding (ref=200usd conventions; price chosen so scaled_shares hits the example) ---")
    # scaled_shares = mult*ref/price; craft price so pos-gain scaled lands on 0.5/1.5/2.0
    # gain>0 => mult>=1; use gain=0 (mult=1) and ref/price to set scaled directly
    for scaled_target, want_sh in [(0.5, 1), (1.5, 1), (2.0, 2)]:
        price = 100.0
        ref = scaled_target * price  # mult=1 at gain slightly>0
        sh, why = stock_shares(0.0001, ref, price)
        flag = "" if sh == want_sh else "  <-- MISMATCH"
        if flag:
            ok = False
        print(f"stock scaled={scaled_target} gain>0 -> {sh} shares (want {want_sh}) [{why}]{flag}")
    # neg gain, scaled < 1 share -> skip
    sh, why = stock_shares(-8.0, 100.0, 100.0)  # mult~0.28, scaled~0.28 share -> skip
    print(f"stock neg tiny -> {sh} shares [{why}] (want 0/skip){'' if sh==0 else '  <-- MISMATCH'}")
    if sh != 0:
        ok = False
    # crypto fractional, neg gain still trades
    q, why = crypto_qty(-8.0, 100.0, 0.5)
    print(f"crypto neg -> qty={q:.4f} [{why}] (want >0, fractional)")
    if not (q > 0):
        ok = False
    print("SELFTEST", "PASS" if ok else "FAIL")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
