"""twin_entry_ports_a.py — crypto-live scalar twins for entry-switch ports A.

Four flat-OPEN proposal families that already have a vector path
(v12_quick_engine.compute_reentry_blocks) and a stocks-live path
(tradier_manage process_position / _shared_direct_entry_claim), ported to
ez (crypto-live) semantics: per-sym knob reads via a caller-supplied
``get(knob, default)`` (hook passes ``_psym_get``), fail-open
(missing/garbage inputs -> no fire, never raise), default-inert
(``*_ENABLED`` defaults False -> no fire).

Signal cores are vec-exact (BACKTEST_BIBLE _43 parity: the scalar twin
must reproduce what the sweep evaluates). Stocks-live scoping that vec
does not model (SYMBOLS/SIDE gates) is preserved in the DEEP_TURN
evaluator for cross-venue consistency. Known stocks-live divergences
from vec are documented on each function, not copied.

Live-key mapping (ez ``_ind_z`` snapshot):
  stoch ``stoch_k_{tf}`` -> ``k_{tf}``, ``stoch_d_1h`` -> ``d_1h``;
  ``close_{tf}`` is replaced by the live current price (passed as
  ``price``), falling back to ``close_{tf}`` when present.
  TF alias: ``5m`` -> ``3m`` (live crypto publishes no ``dc_*_5m`` /
  ``k_5m``; same alias the TECHNICAL_DC twin uses).
"""
from __future__ import annotations

from math import isfinite
from typing import Any, Callable, Mapping


def _num(ind: Mapping[str, Any] | None, key: str, default: float = 0.0) -> float:
    try:
        v = float((ind or {}).get(key, default))
        return v if isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _present(ind: Mapping[str, Any] | None, key: str) -> bool:
    try:
        v = (ind or {}).get(key, None)
        return v is not None and isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _tf_live(tf: Any) -> str | None:
    t = str(tf or "").strip()
    if t == "" or t.upper() == "OFF":
        return None
    return "3m" if t == "5m" else t


def sym_side_allowed(symbol: str, side: str, symbols: Any, side_cfg: Any) -> bool:
    """Stocks-live scoping (tradier_manage.py:13133-13136, verbatim logic)."""
    try:
        syms = tuple(symbols or ())
    except TypeError:
        syms = ()
    if syms and str(symbol) not in {str(s) for s in syms}:
        return False
    want = str(side_cfg or "").upper()
    have = str(side or "").upper()
    return want == "BOTH" or want == have


def bounce_deep_turn_signal(
    ind: Mapping[str, Any] | None,
    is_long: bool,
    *,
    bounce_timeframe: Any = "5m",
    bounce_distance: Any = 0.015,
    deep_k4h: Any = 50.0,
    turn_k1h: Any = 40.0,
    price: float = 0.0,
) -> tuple[bool, str]:
    """Vec-exact twin of v12_quick_engine.py:8429-8449 (B_BOUNCE_DEEP_TURN).

    LONG:  k4h < 100-deep  AND  k1h rising below turn  AND  |px-dc_low|/dc_low <= dist
    SHORT: mirror on k4h > deep, k1h falling above 100-turn, dc_high leg.
    DIVERGENCE (not copied): stocks-live tradier_manage.py:13154/13158 ORs
    the bounce leg with ``close > low_tf`` / ``close < high_tf`` (true on
    ~every bar), which deletes the bounce leg live. The twin keeps the vec
    leg so scalar parity (_43) holds.
    """
    tf = _tf_live(bounce_timeframe)
    if tf is None:
        return False, ""
    try:
        dist = float(bounce_distance)
        deep = float(deep_k4h)
        turn = float(turn_k1h)
    except (TypeError, ValueError):
        return False, ""
    if not (isfinite(dist) and isfinite(deep) and isfinite(turn)):
        return False, ""
    k4h = _num(ind, "k_4h", 50.0)
    k1h = _num(ind, "k_1h", 50.0)
    k1h_prev = _num(ind, "k_1h_prev", k1h)
    px = price if price > 0 else _num(ind, f"close_{tf}", 0.0)
    dc_low = _num(ind, f"dc_low_{tf}", 0.0)
    dc_high = _num(ind, f"dc_high_{tf}", 0.0)
    if is_long:
        deep_ok = k4h < (100.0 - deep)
        turn_ok = k1h > k1h_prev and k1h < turn
        bounce_ok = dc_low > 0 and px > 0 and abs(px - dc_low) / max(dc_low, 1e-9) <= dist
    else:
        deep_ok = k4h > deep
        turn_ok = k1h < k1h_prev and k1h > (100.0 - turn)
        bounce_ok = dc_high > 0 and px > 0 and abs(dc_high - px) / max(dc_high, 1e-9) <= dist
    fire = bool(deep_ok and turn_ok and bounce_ok)
    detail = f"k4h={k4h:.0f}_k1h={k1h:.0f}" if fire else ""
    return fire, detail


def bounce_donchian_signal(
    ind: Mapping[str, Any] | None,
    is_long: bool,
    *,
    timeframe: Any = "5m",
    distance: Any = 0.008,
    recovery_only: Any = False,
    price: float = 0.0,
) -> tuple[bool, str]:
    """Vec-exact twin of v12_quick_engine.py:8450-8465 (B_BOUNCE_DONCHIAN).

    LONG:  |px-dc_low|/dc_low <= dist  AND  (not recovery_only or 20<k<50)
    SHORT: mirror on dc_high, recovery zone 50<k<80.
    Recovery k: ``k_{tf}`` for tf in 5m/15m/1h/4h else ``k_3m`` (vec reads
    ``stoch_k_{tf}``/``k_3m``). NOTE: the CONFIRMATION knob (none/stoch5/
    stoch15/two-of-two) is read-but-unused by stocks-live process_position
    (tradier_manage.py:13172) and unmodeled by vec; only the completed-
    candle contract path consumes it. The twin stays vec-exact (ignores
    CONFIRMATION) so scalar parity holds.
    """
    tf = _tf_live(timeframe)
    if tf is None:
        return False, ""
    try:
        dist = float(distance)
    except (TypeError, ValueError):
        return False, ""
    if not isfinite(dist):
        return False, ""
    recov = bool(recovery_only)
    px = price if price > 0 else _num(ind, f"close_{tf}", 0.0)
    dc_low = _num(ind, f"dc_low_{tf}", 0.0)
    dc_high = _num(ind, f"dc_high_{tf}", 0.0)
    tfk = tf if tf in ("3m", "15m", "1h", "4h") else "3m"
    kk = _num(ind, f"k_{tfk}", _num(ind, "k_3m", 50.0))
    if is_long:
        near = dc_low > 0 and px > 0 and abs(px - dc_low) / max(dc_low, 1e-9) <= dist
        recov_ok = (kk > 20.0 and kk < 50.0) if recov else True
    else:
        near = dc_high > 0 and px > 0 and abs(dc_high - px) / max(dc_high, 1e-9) <= dist
        recov_ok = (kk < 80.0 and kk > 50.0) if recov else True
    fire = bool(near and recov_ok)
    return fire, f"{timeframe}_dist={dist:.3f}" if fire else ""


_HHHL_TFS = ("1h", "4h", "D")


def stoch_hhhl_signal(
    ind: Mapping[str, Any] | None,
    is_long: bool,
    *,
    tfs: Any = ("1h",),
    min_confirming_tfs: Any = 1,
    stoch_threshold: Any = 20.0,
) -> tuple[bool, tuple[str, ...]]:
    """Vec-exact twin of v12_quick_engine.py:8508-8536 (B_STOCH_HHHL_DIRECT).

    Per TF (1h/4h/D only): LONG hh+hl, k<=thr, k rising; SHORT ll+lh,
    k>=100-thr, k falling. Fires when confirming votes >= required.
    DIVERGENCE (not copied): stocks-live consumes this only via the
    causal contract (stoch_hhhl_contract.py), which adds episode-start
    gating (false->true transition only). Crypto proposes only while
    flat, so a level fire IS the episode start; no extra state needed.
    """
    try:
        raw = tuple(tfs or ())
    except TypeError:
        return False, ()
    enabled = tuple(tf for tf in (str(x) for x in raw) if tf in _HHHL_TFS)
    try:
        req = int(float(min_confirming_tfs))
        thr = float(stoch_threshold)
    except (TypeError, ValueError):
        return False, ()
    if not enabled or not 1 <= req <= len(enabled):
        return False, ()
    if not isfinite(thr):
        return False, ()
    active: list[str] = []
    for tf in enabled:
        high = _num(ind, f"high_{tf}", 0.0)
        high_prev = _num(ind, f"high_{tf}_prev", 0.0)
        low = _num(ind, f"low_{tf}", 0.0)
        low_prev = _num(ind, f"low_{tf}_prev", 0.0)
        kk = _num(ind, f"k_{tf}", 50.0)
        kk_prev = _num(ind, f"k_{tf}_prev", 50.0)
        if is_long:
            ok = high > high_prev and low > low_prev and kk <= thr and kk > kk_prev
        else:
            ok = high < high_prev and low < low_prev and kk >= 100.0 - thr and kk < kk_prev
        if ok:
            active.append(tf)
    fire = len(active) >= req
    return fire, tuple(active) if fire else ()


def stoch_parent_signal(
    ind: Mapping[str, Any] | None,
    is_long: bool,
    *,
    family: Any = "ENTRY_1H_TURN_UP",
    threshold: Any = 40.0,
    turn_definition: Any = "rising-vs-prior",
) -> tuple[bool, str]:
    """Vec-exact twin of v12_quick_engine.py:8541-8569 (B_STOCH_PARENT_DIRECT).

    ENTRY_1H_TURN_UP: zone + turn; rising-vs-prior -> zone&rising,
    cross-d -> zone&(k-vs-d), anything else -> union (vec ``else`` branch).
    ENTRY_4H_DEEP_VALUE: zone only (k4h < th LONG / > 100-th SHORT).
    Unknown family -> no fire (vec writes no block either).
    DIVERGENCE (not copied): the stocks-live contract
    (stoch_parent_contract.py) makes rising-vs-prior an event pulse
    (new-parent only); vec and this twin use level state, which is the
    episode start under flat-only proposal.
    """
    fam = str(family or "")
    try:
        th = float(threshold)
    except (TypeError, ValueError):
        return False, ""
    if not isfinite(th):
        return False, ""
    if fam == "ENTRY_1H_TURN_UP":
        kk = _num(ind, "k_1h", 50.0)
        kk_prev = _num(ind, "k_1h_prev", 50.0)
        dd = _num(ind, "d_1h", 50.0)
        if is_long:
            zone = kk < th
            rising = kk > kk_prev
            cross = kk > dd
        else:
            zone = kk > 100.0 - th
            rising = kk < kk_prev
            cross = kk < dd
        tdef = str(turn_definition or "")
        if tdef == "rising-vs-prior":
            fire = bool(zone and rising)
        elif tdef == "cross-d":
            fire = bool(zone and cross)
        else:
            fire = bool((zone and rising) or (zone and cross))
        return fire, fam if fire else ""
    if fam == "ENTRY_4H_DEEP_VALUE":
        if not _present(ind, "k_4h"):
            return False, ""
        k4h = _num(ind, "k_4h", 50.0)
        fire = bool(k4h < th) if is_long else bool(k4h > 100.0 - th)
        return fire, fam if fire else ""
    return False, ""


Get = Callable[[str, Any], Any]


def evaluate_bounce_deep_turn(get: Get, symbol: str, side: str, ind: Mapping[str, Any] | None, price: float) -> tuple[bool, str]:
    """Hook-level evaluator: knob reads (config.py defaults) + signal."""
    try:
        if not bool(get("ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_ENABLED", False)):
            return False, ""
        if not sym_side_allowed(symbol, side, get("ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SYMBOLS", ("WDAY",)), get("ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_SIDE", "SHORT")):
            return False, ""
        is_long = str(side).upper() == "LONG"
        fire, detail = bounce_deep_turn_signal(
            ind, is_long,
            bounce_timeframe=get("ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_TIMEFRAME", "5m"),
            bounce_distance=get("ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_BOUNCE_DISTANCE", 0.015),
            deep_k4h=get("ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_DEEP_K4H", 50.0),
            turn_k1h=get("ENTRY_BOUNCE_DEEP_TURN_COMPOSITE_V1_TURN_K1H", 40.0),
            price=price,
        )
        if not fire:
            return False, ""
        return True, f"ENTRY_BOUNCE_DEEP_TURN_V1_{'L' if is_long else 'S'}_{detail}"
    except Exception:
        return False, ""


def evaluate_bounce_donchian(get: Get, symbol: str, side: str, ind: Mapping[str, Any] | None, price: float) -> tuple[bool, str]:
    """Hook-level evaluator: knob reads (config.py defaults) + signal."""
    try:
        if not bool(get("ENTRY_BOUNCE_DONCHIAN_DIRECT_ENABLED", False)):
            return False, ""
        is_long = str(side).upper() == "LONG"
        fire, detail = bounce_donchian_signal(
            ind, is_long,
            timeframe=get("ENTRY_BOUNCE_DONCHIAN_DIRECT_TIMEFRAME", "5m"),
            distance=get("ENTRY_BOUNCE_DONCHIAN_DIRECT_DISTANCE", 0.008),
            recovery_only=get("ENTRY_BOUNCE_DONCHIAN_DIRECT_RECOVERY_ONLY", False),
            price=price,
        )
        if not fire:
            return False, ""
        return True, f"ENTRY_BOUNCE_DONCHIAN_{'L' if is_long else 'S'}_{detail}"
    except Exception:
        return False, ""


def evaluate_stoch_hhhl(get: Get, symbol: str, side: str, ind: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Hook-level evaluator: knob reads (config.py defaults) + signal."""
    try:
        if not bool(get("ENTRY_STOCH_HHHL_DIRECT_ENABLED", False)):
            return False, ""
        is_long = str(side).upper() == "LONG"
        fire, active = stoch_hhhl_signal(
            ind, is_long,
            tfs=get("ENTRY_STOCH_HHHL_DIRECT_TFS", ["1h"]),
            min_confirming_tfs=get("ENTRY_STOCH_HHHL_DIRECT_MIN_CONFIRMING_TFS", 1),
            stoch_threshold=get("ENTRY_STOCH_HHHL_DIRECT_STOCH_THRESHOLD", 20.0),
        )
        if not fire:
            return False, ""
        return True, f"STOCH_HHHL_DIRECT_{'+'.join(active)}"
    except Exception:
        return False, ""


def evaluate_stoch_parent(get: Get, symbol: str, side: str, ind: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Hook-level evaluator: knob reads (config.py defaults) + signal."""
    try:
        if not bool(get("ENTRY_STOCH_PARENT_DIRECT_ENABLED", False)):
            return False, ""
        is_long = str(side).upper() == "LONG"
        fire, fam = stoch_parent_signal(
            ind, is_long,
            family=get("ENTRY_STOCH_PARENT_DIRECT_FAMILY", "ENTRY_1H_TURN_UP"),
            threshold=get("ENTRY_STOCH_PARENT_DIRECT_THRESHOLD", 40.0),
            turn_definition=get("ENTRY_STOCH_PARENT_DIRECT_TURN_DEFINITION", "rising-vs-prior"),
        )
        if not fire:
            return False, ""
        return True, f"STOCH_PARENT_DIRECT_{fam}"
    except Exception:
        return False, ""
