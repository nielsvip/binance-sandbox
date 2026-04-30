"""HTF / SMA200 / Heiken-Ashi pure-function entry engine.

Inverts the rejection logic in ez_positions_quick.py (`_daily_bull` / `_daily_bear`,
HTF_DIRECTION_GATE) into a FIRE-ON-ALIGNMENT entry score. Aggressive defaults: HIGH
frequency, no symbol blacklists, no cooldowns. Caller decides sizing/throttle.

Score weights (LONG; SHORT is mirrored):
  price > sma_200_D * 1.01                       -> +0.3
  dc_basis_D rising vs dc_basis_D_ant            -> +0.2
  ha_4h == 'green'                               -> +0.2
  ha_D  == 'green'                               -> +0.2
  stoch_k_4h > 50                                -> +0.1
  ---------------------------------------------------
  total possible                                  = 1.0

Thresholds:
  >= 0.5 -> fire
  >= 0.8 -> high conviction (caller may upsize)

Indicator dict matches keys produced by ez_market_data / ez_indicators (sma_200_D,
dc_basis_D, dc_basis_D_ant, ha_4h, ha_D, stoch_k_4h, price OR current_price).
"""
from __future__ import annotations
from typing import Tuple


_FIRE_THRESHOLD = 0.5
_HIGH_CONVICTION_THRESHOLD = 0.8


def _f(x, default: float = 0.0) -> float:
    try:
        if x is None: return default
        return float(x)
    except (TypeError, ValueError):
        return default


def _s(x, default: str = "") -> str:
    if x is None: return default
    try: return str(x).lower()
    except Exception: return default


def should_fire_htf_entry(symbol: str, indicators: dict, side: str) -> Tuple[bool, str, float]:
    """Returns (fire, reason, score).

    fire   - True iff score >= 0.5.
    reason - human-readable contributions string (always populated, even when no fire).
    score  - float in [0.0, 1.0]; >= 0.8 => high conviction.
    """
    if not isinstance(indicators, dict) or not indicators:
        return False, "NO_INDICATORS", 0.0
    side_u = (side or "").upper()
    if side_u not in ("LONG", "SHORT"):
        return False, f"BAD_SIDE_{side}", 0.0
    is_long = side_u == "LONG"
    price = _f(indicators.get("price") or indicators.get("current_price") or indicators.get("close"))
    sma_d = _f(indicators.get("sma_200_D"))
    dcb_d = _f(indicators.get("dc_basis_D"))
    dcb_d_ant = _f(indicators.get("dc_basis_D_ant"))
    ha_4h = _s(indicators.get("ha_4h"))
    ha_d = _s(indicators.get("ha_D"))
    k_4h = _f(indicators.get("stoch_k_4h") or indicators.get("k_4h"))
    score = 0.0
    parts = []
    if sma_d > 0 and price > 0:
        if is_long and price > sma_d * 1.01:
            score += 0.3; parts.append("SMA200D_BULL+0.3")
        elif (not is_long) and price < sma_d * 0.99:
            score += 0.3; parts.append("SMA200D_BEAR+0.3")
    if dcb_d > 0 and dcb_d_ant > 0:
        if is_long and dcb_d > dcb_d_ant:
            score += 0.2; parts.append("DC_BASIS_D_RISE+0.2")
        elif (not is_long) and dcb_d < dcb_d_ant:
            score += 0.2; parts.append("DC_BASIS_D_FALL+0.2")
    if is_long and ha_4h == "green":
        score += 0.2; parts.append("HA_4H_GREEN+0.2")
    elif (not is_long) and ha_4h == "red":
        score += 0.2; parts.append("HA_4H_RED+0.2")
    if is_long and ha_d == "green":
        score += 0.2; parts.append("HA_D_GREEN+0.2")
    elif (not is_long) and ha_d == "red":
        score += 0.2; parts.append("HA_D_RED+0.2")
    if k_4h > 0:
        if is_long and k_4h > 50:
            score += 0.1; parts.append(f"K4H_BULL({k_4h:.0f})+0.1")
        elif (not is_long) and k_4h < 50:
            score += 0.1; parts.append(f"K4H_BEAR({k_4h:.0f})+0.1")
    score = round(score, 4)
    fire = score >= _FIRE_THRESHOLD
    tier = "HIGH_CONVICTION" if score >= _HIGH_CONVICTION_THRESHOLD else ("FIRE" if fire else "NO_FIRE")
    reason = f"{symbol}|{side_u}|{tier}|score={score:.2f}|" + ",".join(parts) if parts else f"{symbol}|{side_u}|{tier}|score={score:.2f}|NONE"
    return fire, reason, score


# ------------------------------------------------------------------
# Self-tests
# ------------------------------------------------------------------
if __name__ == "__main__":
    bull_htf = {
        "price": 105.0, "sma_200_D": 100.0,
        "dc_basis_D": 102.0, "dc_basis_D_ant": 101.0,
        "ha_4h": "green", "ha_D": "green", "stoch_k_4h": 72.0,
    }
    bear_htf = {
        "price": 95.0, "sma_200_D": 100.0,
        "dc_basis_D": 98.0, "dc_basis_D_ant": 99.5,
        "ha_4h": "red", "ha_D": "red", "stoch_k_4h": 28.0,
    }
    mixed_htf = {
        # genuinely conflicting: price barely above SMA, dc flat, HA neutral both, k 50
        "price": 100.2, "sma_200_D": 100.0,
        "dc_basis_D": 100.0, "dc_basis_D_ant": 100.0,
        "ha_4h": "neutral", "ha_D": "neutral", "stoch_k_4h": 50.0,
    }
    tests = [
        ("BULL_LONG_FIRES",  "BTCUSDC", bull_htf,  "LONG",  True,  0.8),
        ("BULL_SHORT_NOFIRE","BTCUSDC", bull_htf,  "SHORT", False, 0.0),
        ("BEAR_SHORT_FIRES", "ETHUSDC", bear_htf,  "SHORT", True,  0.8),
        ("BEAR_LONG_NOFIRE", "ETHUSDC", bear_htf,  "LONG",  False, 0.0),
        ("MIXED_LONG_NOFIRE","SOLUSDC", mixed_htf, "LONG",  False, 0.5),
        ("MIXED_SHORT_NOFIRE","SOLUSDC",mixed_htf, "SHORT", False, 0.5),
    ]
    fail = 0
    for name, sym, ind, side, want_fire, min_or_max in tests:
        fire, reason, score = should_fire_htf_entry(sym, ind, side)
        if want_fire:
            ok = fire and score >= min_or_max
        else:
            ok = (not fire)
        flag = "OK " if ok else "FAIL"
        if not ok: fail += 1
        print(f"[{flag}] {name:22s} fire={fire!s:5s} score={score:.2f}  {reason}")
    print("---")
    print(f"RESULT: {'PASS' if fail == 0 else f'FAIL ({fail} test(s))'}")
