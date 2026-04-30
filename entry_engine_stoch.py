"""
entry_engine_stoch.py — Pure-function stoch K/D entry engine.

FIRE-ON-ALIGNMENT: inverts the K-zone / age-gate REJECTION filters from
ez_manage.py + ez_positions_quick.py into an EMISSION engine. When stoch
K/D align across 3m/15m/1h/4h with a 3m crossover, fire.

Aggressive defaults — no cooldowns, no min-gap, no min-trades floor.
Target: HIGH frequency. ~2000× more trade opportunities than current
filter-and-block approach.

Field convention (live indicators dict):
    stoch_k_3m, stoch_d_3m, k_3m_prev   (3m base TF)
    stoch_k_15m, stoch_d_15m
    stoch_k_1h,  stoch_d_1h
    stoch_k_4h,  stoch_d_4h

Side: "LONG" or "SHORT".

Score band → fire decision:
    1.0  4/4 TFs aligned + 3m K-cross-D       → FIRE  (STRONG)
    0.7  3/4 TFs aligned + 3m K-cross-D       → FIRE  (GOOD)
    0.5  2/4 TFs aligned + extreme OS/OB      → FIRE  (CONTRARIAN)
    else                                       → NO FIRE
"""
from __future__ import annotations

MID = 50.0
OS_THRESHOLD = 20.0
OB_THRESHOLD = 80.0


def _sf(v, default: float = 50.0) -> float:
    """Safe float coerce — mirrors ez_manage._sf usage."""
    try:
        if v is None:
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def should_fire_stoch_entry(symbol: str, indicators: dict, side: str) -> tuple:
    """Pure function. Returns (fire: bool, reason: str, score: float).

    Aggressive: no cooldown, no debouncing — caller decides spacing.
    Score is the calibration handle for downstream sizing.
    """
    is_long = side.upper() == "LONG"
    k_3m = _sf(indicators.get("stoch_k_3m"))
    d_3m = _sf(indicators.get("stoch_d_3m"))
    k_3m_prev = _sf(indicators.get("k_3m_prev"), k_3m)
    k_15m = _sf(indicators.get("stoch_k_15m"))
    d_15m = _sf(indicators.get("stoch_d_15m"))
    k_1h = _sf(indicators.get("stoch_k_1h"))
    d_1h = _sf(indicators.get("stoch_d_1h"))
    k_4h = _sf(indicators.get("stoch_k_4h"))
    d_4h = _sf(indicators.get("stoch_d_4h"))
    if is_long:
        crossover = (k_3m > d_3m) and (k_3m_prev <= _sf(indicators.get("d_3m_prev"), d_3m))
        tf_aligned = [k_3m > MID, k_15m > MID, k_1h > MID, k_4h > MID]
        extreme = k_3m < OS_THRESHOLD
        kd_3m_dir = k_3m > d_3m
        kd_15m_dir = k_15m > d_15m
    else:
        crossover = (k_3m < d_3m) and (k_3m_prev >= _sf(indicators.get("d_3m_prev"), d_3m))
        tf_aligned = [k_3m < MID, k_15m < MID, k_1h < MID, k_4h < MID]
        extreme = k_3m > OB_THRESHOLD
        kd_3m_dir = k_3m < d_3m
        kd_15m_dir = k_15m < d_15m
    aligned_count = sum(tf_aligned)
    bias = "_OS" if (is_long and extreme) else ("_OB" if (not is_long and extreme) else "")
    tf_str = f"{aligned_count}/4_TFs"
    cross_str = "CROSS" if crossover else "noCROSS"
    base = f"{symbol}_{side}_{tf_str}_{cross_str}{bias}_k3={k_3m:.0f}_d3={d_3m:.0f}_k15={k_15m:.0f}_k1h={k_1h:.0f}_k4h={k_4h:.0f}"
    if aligned_count == 4 and crossover and kd_3m_dir and kd_15m_dir:
        return True, f"FIRE_STRONG_{base}", 1.0
    if aligned_count >= 3 and crossover and kd_3m_dir:
        return True, f"FIRE_GOOD_{base}", 0.7
    if aligned_count >= 2 and extreme and kd_3m_dir:
        return True, f"FIRE_CONTRARIAN_{base}", 0.5
    return False, f"NO_FIRE_{base}", 0.0


# ---------------------------------------------------------------------------
# Unit tests — run as `python3 entry_engine_stoch.py`
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 72)
    print("entry_engine_stoch — unit tests")
    print("=" * 72)
    tests_passed = 0
    tests_total = 0

    # Test 1: LONG strong — 4/4 above mid + 3m K crosses up D
    tests_total += 1
    long_strong = {
        "stoch_k_3m": 62, "stoch_d_3m": 55, "k_3m_prev": 48, "d_3m_prev": 52,
        "stoch_k_15m": 65, "stoch_d_15m": 60,
        "stoch_k_1h": 58, "stoch_d_1h": 54,
        "stoch_k_4h": 70, "stoch_d_4h": 65,
    }
    fire, reason, score = should_fire_stoch_entry("BTCUSDC", long_strong, "LONG")
    ok = fire and score == 1.0
    print(f"[{'PASS' if ok else 'FAIL'}] Test 1 LONG STRONG: fire={fire} score={score} reason={reason}")
    tests_passed += int(ok)

    # Test 2: SHORT good — 3/4 below mid + 3m K crosses down D
    tests_total += 1
    short_good = {
        "stoch_k_3m": 38, "stoch_d_3m": 45, "k_3m_prev": 52, "d_3m_prev": 48,
        "stoch_k_15m": 35, "stoch_d_15m": 40,
        "stoch_k_1h": 42, "stoch_d_1h": 47,
        "stoch_k_4h": 58, "stoch_d_4h": 55,  # 4h NOT aligned
    }
    fire, reason, score = should_fire_stoch_entry("ETHUSDC", short_good, "SHORT")
    ok = fire and score == 0.7
    print(f"[{'PASS' if ok else 'FAIL'}] Test 2 SHORT GOOD: fire={fire} score={score} reason={reason}")
    tests_passed += int(ok)

    # Test 3: LONG contrarian — extreme oversold + 2/4 + KD up
    tests_total += 1
    long_contrarian = {
        "stoch_k_3m": 12, "stoch_d_3m": 8, "k_3m_prev": 10, "d_3m_prev": 9,
        "stoch_k_15m": 22, "stoch_d_15m": 25,  # below mid
        "stoch_k_1h": 55, "stoch_d_1h": 50,    # above mid (aligned for LONG)
        "stoch_k_4h": 60, "stoch_d_4h": 58,    # above mid (aligned for LONG)
    }
    fire, reason, score = should_fire_stoch_entry("SOLUSDC", long_contrarian, "LONG")
    ok = fire and score == 0.5
    print(f"[{'PASS' if ok else 'FAIL'}] Test 3 LONG CONTRARIAN: fire={fire} score={score} reason={reason}")
    tests_passed += int(ok)

    # Test 4: NO-FIRE — mixed garbage, no alignment, no crossover
    tests_total += 1
    mixed = {
        "stoch_k_3m": 55, "stoch_d_3m": 50, "k_3m_prev": 56, "d_3m_prev": 51,
        "stoch_k_15m": 45, "stoch_d_15m": 50,
        "stoch_k_1h": 52, "stoch_d_1h": 51,
        "stoch_k_4h": 48, "stoch_d_4h": 49,
    }
    fire, reason, score = should_fire_stoch_entry("DOGEUSDT", mixed, "LONG")
    ok = (not fire) and score == 0.0
    print(f"[{'PASS' if ok else 'FAIL'}] Test 4 NO-FIRE MIXED: fire={fire} score={score} reason={reason}")
    tests_passed += int(ok)

    print("=" * 72)
    print(f"{tests_passed}/{tests_total} tests passed")
    print("=" * 72)
