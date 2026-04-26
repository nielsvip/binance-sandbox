"""DC-channel breakout entry engine — pure-function fire-on-breakout.

Inverts the rejection logic in ez_positions_quick.py / ez_manage.py: instead of
blocking entries that hit DC extremes, this FIRES them. High frequency, no
cooldowns. Caller is responsible for sizing, side allocation, and risk.

Entry rules (LONG; SHORT mirrored on dc_low_*):
  - price > dc_high_3m  -> 0.6 (3m breakout)
  - price > dc_high_15m -> 0.8 (15m breakout)
  - price > dc_high_1h  -> 1.0 (1h breakout)
  - price retests dc_basis_15m from above with green ha_3m -> 0.7
  - price within 0.5% of dc_low_4h AND wt1_3m > wt2_3m  -> 0.6 (bounce)
Score = MAX of triggered conditions. Fire if score >= 0.6.
"""
from __future__ import annotations
FIRE_THRESHOLD = 0.6
RETEST_TOL_PCT = 0.30
BOUNCE_TOL_PCT = 0.50

def _f(d, k, default=0.0):
    try:
        v = d.get(k, default)
        if v is None or v is False: return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)

def should_fire_dc_entry(symbol, indicators, side):
    """Pure-function DC breakout/retest/bounce detector.

    Args:
        symbol: ticker (logging only, no side-effects).
        indicators: dict with current_price, dc_high_*, dc_low_*, dc_basis_*,
                    ha_3m, wt1_3m, wt2_3m at TFs 3m/15m/1h/4h.
        side: "LONG" or "SHORT".

    Returns:
        (fire: bool, reason: str, score: float)
    """
    if not isinstance(indicators, dict):
        return False, "NO_INDICATORS", 0.0
    side = (side or "").upper()
    if side not in ("LONG", "SHORT"):
        return False, f"BAD_SIDE({side})", 0.0
    price = _f(indicators, 'current_price') or _f(indicators, 'price')
    if price <= 0:
        return False, "NO_PRICE", 0.0
    dc_high_3m  = _f(indicators, 'dc_high_3m')
    dc_high_15m = _f(indicators, 'dc_high_15m')
    dc_high_1h  = _f(indicators, 'dc_high_1h')
    dc_low_3m   = _f(indicators, 'dc_low_3m')
    dc_low_15m  = _f(indicators, 'dc_low_15m')
    dc_low_1h   = _f(indicators, 'dc_low_1h')
    dc_low_4h   = _f(indicators, 'dc_low_4h')
    dc_high_4h  = _f(indicators, 'dc_high_4h')
    dc_basis_15m = _f(indicators, 'dc_basis_15m')
    ha_3m = str(indicators.get('ha_3m', 'neutral')).lower()
    wt1_3m = _f(indicators, 'wt1_3m')
    wt2_3m = _f(indicators, 'wt2_3m')
    triggers = []
    if side == "LONG":
        if dc_high_3m > 0 and price > dc_high_3m:
            triggers.append((0.6, f"DC_BRK_3M(>{dc_high_3m:.4f})"))
        if dc_high_15m > 0 and price > dc_high_15m:
            triggers.append((0.8, f"DC_BRK_15M(>{dc_high_15m:.4f})"))
        if dc_high_1h > 0 and price > dc_high_1h:
            triggers.append((1.0, f"DC_BRK_1H(>{dc_high_1h:.4f})"))
        if dc_basis_15m > 0 and ha_3m == 'green':
            dist_pct = abs(price - dc_basis_15m) / dc_basis_15m * 100.0
            if price >= dc_basis_15m and dist_pct <= RETEST_TOL_PCT:
                triggers.append((0.7, f"DC_RETEST_BASIS_15M({dist_pct:.2f}%)"))
        if dc_low_4h > 0 and wt1_3m > wt2_3m:
            dist_pct = abs(price - dc_low_4h) / dc_low_4h * 100.0
            if dist_pct <= BOUNCE_TOL_PCT:
                triggers.append((0.6, f"DC_BOUNCE_LOW_4H({dist_pct:.2f}%)"))
    else:
        if dc_low_3m > 0 and price < dc_low_3m:
            triggers.append((0.6, f"DC_BRK_3M(<{dc_low_3m:.4f})"))
        if dc_low_15m > 0 and price < dc_low_15m:
            triggers.append((0.8, f"DC_BRK_15M(<{dc_low_15m:.4f})"))
        if dc_low_1h > 0 and price < dc_low_1h:
            triggers.append((1.0, f"DC_BRK_1H(<{dc_low_1h:.4f})"))
        if dc_basis_15m > 0 and ha_3m == 'red':
            dist_pct = abs(price - dc_basis_15m) / dc_basis_15m * 100.0
            if price <= dc_basis_15m and dist_pct <= RETEST_TOL_PCT:
                triggers.append((0.7, f"DC_RETEST_BASIS_15M({dist_pct:.2f}%)"))
        if dc_high_4h > 0 and wt1_3m < wt2_3m:
            dist_pct = abs(price - dc_high_4h) / dc_high_4h * 100.0
            if dist_pct <= BOUNCE_TOL_PCT:
                triggers.append((0.6, f"DC_REJECT_HIGH_4H({dist_pct:.2f}%)"))
    if not triggers:
        return False, f"NO_TRIGGER_{side}", 0.0
    triggers.sort(key=lambda x: x[0], reverse=True)
    best_score, best_reason = triggers[0]
    fire = best_score >= FIRE_THRESHOLD
    return fire, best_reason, best_score

def _test():
    """Inline tests — run via `python entry_engine_dc.py`."""
    # Test 1: clean 1h breakout LONG -> highest tier 1.0
    ind1 = {'current_price': 110.0, 'dc_high_3m': 100.0, 'dc_high_15m': 105.0, 'dc_high_1h': 108.0, 'dc_low_4h': 80.0, 'wt1_3m': 5.0, 'wt2_3m': 4.0}
    fire, reason, score = should_fire_dc_entry("BTCUSDT", ind1, "LONG")
    assert fire is True, f"T1 fire={fire}"
    assert score == 1.0, f"T1 score={score}"
    assert "DC_BRK_1H" in reason, f"T1 reason={reason}"
    print(f"T1 LONG 1h-breakout: fire={fire} score={score} reason={reason}")
    # Test 2: SHORT 3m breakdown only -> 0.6
    ind2 = {'current_price': 90.0, 'dc_low_3m': 95.0, 'dc_low_15m': 80.0, 'dc_low_1h': 70.0, 'dc_high_4h': 200.0, 'wt1_3m': 1.0, 'wt2_3m': 2.0}
    fire2, reason2, score2 = should_fire_dc_entry("ETHUSDT", ind2, "SHORT")
    assert fire2 is True, f"T2 fire={fire2}"
    assert score2 == 0.6, f"T2 score={score2}"
    print(f"T2 SHORT 3m-breakdown: fire={fire2} score={score2} reason={reason2}")
    # Test 3: no trigger inside channel -> NO_TRIGGER
    ind3 = {'current_price': 100.0, 'dc_high_3m': 110.0, 'dc_high_15m': 115.0, 'dc_high_1h': 120.0, 'dc_low_3m': 90.0, 'dc_low_15m': 85.0, 'dc_low_1h': 80.0, 'dc_low_4h': 50.0, 'dc_basis_15m': 200.0, 'wt1_3m': 1.0, 'wt2_3m': 2.0}
    fire3, reason3, score3 = should_fire_dc_entry("XRPUSDT", ind3, "LONG")
    assert fire3 is False, f"T3 fire={fire3}"
    assert score3 == 0.0, f"T3 score={score3}"
    print(f"T3 LONG no-trigger: fire={fire3} score={score3} reason={reason3}")
    # Test 4: LONG bounce off dc_low_4h with wt accelerating
    ind4 = {'current_price': 100.2, 'dc_high_3m': 110.0, 'dc_high_15m': 115.0, 'dc_high_1h': 120.0, 'dc_low_4h': 100.0, 'wt1_3m': 5.0, 'wt2_3m': 3.0}
    fire4, reason4, score4 = should_fire_dc_entry("SOLUSDT", ind4, "LONG")
    assert fire4 is True, f"T4 fire={fire4}"
    assert "BOUNCE" in reason4, f"T4 reason={reason4}"
    print(f"T4 LONG bounce-low-4h: fire={fire4} score={score4} reason={reason4}")
    print("ALL TESTS PASSED")

if __name__ == "__main__":
    _test()
