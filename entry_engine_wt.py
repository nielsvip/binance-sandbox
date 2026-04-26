"""WT (WaveTrend) Entry Engine — pure-function fire-on-alignment trigger.

Inverts the existing WT_LTF_GATE filter logic in ez_positions_quick.py:11210-11265.
Where the live filter REJECTS entries that lack alignment, this engine FIRES
entries when alignment is high-conviction.

Alignment rule (mirrors live filter at ez_positions_quick.py:11216-11224):
  LONG  TF aligned ⇔ wt1_<tf> >  wt2_<tf>
  SHORT TF aligned ⇔ wt1_<tf> <  wt2_<tf>

Score table:
  5/5 TFs aligned                              -> 1.0 FIRE
  4/5 TFs aligned                              -> 0.8 FIRE
  3/5 TFs aligned + velocity confirms          -> 0.6 FIRE
  3/5 without velocity confirm                 -> 0.6 NO FIRE
  <3/5 TFs aligned                             -> ratio  NO FIRE

Velocity confirms (at 3/5 boundary):
  LONG : at least one of wt_velocity_3m / _1h / _4h > 0
  SHORT: at least one of wt_velocity_3m / _1h / _4h < 0

Pure: no I/O, no Redis, no logger, no config reads.
"""
from typing import Tuple

_TFS = ("3m", "15m", "1h", "4h", "D")
_VEL_TFS = ("3m", "1h", "4h")


def _f(d: dict, key: str) -> float:
    """Safe float fetch — None / missing / non-numeric -> 0.0."""
    try:
        v = d.get(key, 0)
        if v is None:
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def should_fire_wt_entry(symbol: str, indicators: dict, side: str) -> Tuple[bool, str, float]:
    """Returns (fire, reason, score). Pure function. No I/O.

    Fires when WT alignment makes `side` high conviction.

    Args:
        symbol: e.g. "BTCUSDT" — passed through into reason for traceability.
        indicators: dict containing wt1_<tf>, wt2_<tf> for tf in {3m,15m,1h,4h,D}
                    and optionally wt_velocity_3m / _1h / _4h.
        side: "LONG" or "SHORT" (case-insensitive).

    Returns:
        (fire: bool, reason: str, score: float in [0.0, 1.0])
    """
    side_u = (side or "").upper()
    if side_u not in ("LONG", "SHORT"):
        return False, f"WT_ENTRY_BAD_SIDE_{side}", 0.0
    is_long = side_u == "LONG"
    aligned_flags = []
    for tf in _TFS:
        w1 = _f(indicators, f"wt1_{tf}")
        w2 = _f(indicators, f"wt2_{tf}")
        if is_long:
            aligned_flags.append(w1 > w2)
        else:
            aligned_flags.append(w1 < w2)
    n_aligned = sum(aligned_flags)
    aligned_str = "/".join(tf for tf, ok in zip(_TFS, aligned_flags) if ok) or "none"
    base_reason = f"WT_ENTRY_{symbol}_{side_u}_{n_aligned}of5[{aligned_str}]"
    if n_aligned == 5:
        return True, f"{base_reason}_FULL", 1.0
    if n_aligned == 4:
        return True, f"{base_reason}_STRONG", 0.8
    if n_aligned == 3:
        v3 = _f(indicators, "wt_velocity_3m")
        v1h = _f(indicators, "wt_velocity_1h")
        v4h = _f(indicators, "wt_velocity_4h")
        if is_long:
            vel_ok = (v3 > 0) or (v1h > 0) or (v4h > 0)
        else:
            vel_ok = (v3 < 0) or (v1h < 0) or (v4h < 0)
        if vel_ok:
            return True, f"{base_reason}_VEL_OK(v3={v3:.3f},v1h={v1h:.3f},v4h={v4h:.3f})", 0.6
        return False, f"{base_reason}_VEL_FAIL(v3={v3:.3f},v1h={v1h:.3f},v4h={v4h:.3f})", 0.6
    score = n_aligned / 5.0
    return False, f"{base_reason}_TOO_FEW", score


if __name__ == "__main__":
    print("=" * 70)
    print("entry_engine_wt.py — unit tests")
    print("=" * 70)
    long_full = {
        "wt1_3m": 10, "wt2_3m": 5,
        "wt1_15m": 12, "wt2_15m": 8,
        "wt1_1h": 20, "wt2_1h": 15,
        "wt1_4h": 30, "wt2_4h": 25,
        "wt1_D": 40, "wt2_D": 35,
        "wt_velocity_3m": 1.5, "wt_velocity_1h": 0.8, "wt_velocity_4h": 0.4,
    }
    fire, reason, score = should_fire_wt_entry("BTCUSDT", long_full, "LONG")
    print(f"\n[TEST 1] CLEAR LONG (5/5 aligned)")
    print(f"  fire={fire} score={score} reason={reason}")
    assert fire is True, "5/5 LONG must fire"
    assert score == 1.0, f"5/5 score must be 1.0, got {score}"
    short_strong = {
        "wt1_3m": 5, "wt2_3m": 10,
        "wt1_15m": 8, "wt2_15m": 12,
        "wt1_1h": 15, "wt2_1h": 20,
        "wt1_4h": 25, "wt2_4h": 30,
        "wt1_D": 40, "wt2_D": 35,
        "wt_velocity_3m": -0.9, "wt_velocity_1h": -0.4, "wt_velocity_4h": -0.2,
    }
    fire, reason, score = should_fire_wt_entry("ETHUSDT", short_strong, "SHORT")
    print(f"\n[TEST 2] CLEAR SHORT (4/5 aligned: 3m,15m,1h,4h vs D against)")
    print(f"  fire={fire} score={score} reason={reason}")
    assert fire is True, "4/5 SHORT must fire"
    assert score == 0.8, f"4/5 score must be 0.8, got {score}"
    mixed = {
        "wt1_3m": 10, "wt2_3m": 5,
        "wt1_15m": 8, "wt2_15m": 12,
        "wt1_1h": 20, "wt2_1h": 15,
        "wt1_4h": 25, "wt2_4h": 30,
        "wt1_D": 35, "wt2_D": 40,
        "wt_velocity_3m": 0.1, "wt_velocity_1h": 0.05, "wt_velocity_4h": 0.02,
    }
    fire, reason, score = should_fire_wt_entry("SOLUSDT", mixed, "LONG")
    print(f"\n[TEST 3] MIXED (2/5 LONG aligned: 3m,1h)")
    print(f"  fire={fire} score={score} reason={reason}")
    assert fire is False, "2/5 must NOT fire"
    assert score == 0.4, f"2/5 score must be 0.4, got {score}"
    boundary = {
        "wt1_3m": 10, "wt2_3m": 5,
        "wt1_15m": 12, "wt2_15m": 8,
        "wt1_1h": 20, "wt2_1h": 15,
        "wt1_4h": 25, "wt2_4h": 30,
        "wt1_D": 35, "wt2_D": 40,
        "wt_velocity_3m": 1.2, "wt_velocity_1h": -0.1, "wt_velocity_4h": -0.2,
    }
    fire, reason, score = should_fire_wt_entry("AVAXUSDT", boundary, "LONG")
    print(f"\n[TEST 4] BOUNDARY 3/5 LONG with positive 3m velocity")
    print(f"  fire={fire} score={score} reason={reason}")
    assert fire is True, "3/5 with vel_ok must fire"
    assert score == 0.6, f"3/5 score must be 0.6, got {score}"
    boundary_no_vel = dict(boundary)
    boundary_no_vel["wt_velocity_3m"] = -0.5
    boundary_no_vel["wt_velocity_1h"] = -0.5
    boundary_no_vel["wt_velocity_4h"] = -0.5
    fire, reason, score = should_fire_wt_entry("AVAXUSDT", boundary_no_vel, "LONG")
    print(f"\n[TEST 5] BOUNDARY 3/5 LONG with all-negative velocity (vel fail)")
    print(f"  fire={fire} score={score} reason={reason}")
    assert fire is False, "3/5 LONG with all-negative vel must NOT fire"
    assert score == 0.6, f"3/5 score must be 0.6, got {score}"
    fire, reason, score = should_fire_wt_entry("BTC", {}, "BAD")
    print(f"\n[TEST 6] Bad side string")
    print(f"  fire={fire} score={score} reason={reason}")
    assert fire is False
    print("\n" + "=" * 70)
    print("ALL TESTS PASSED")
    print("=" * 70)
