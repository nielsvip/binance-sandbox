"""Build the Tradier V20 gate state from the canonical causal vector replay."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path


def completed_bar_state(ss, overrides, raw, anchor, bar_ts, decision_ts, engine_root=None):
    """Return the canonical V20 state for one completed, open-labeled bar.

    The caller supplies a completed prefix, its causal rolling anchor, the
    feature bar's open timestamp, and the separate decision-clock timestamp.
    No state or intent files are read or written here.
    """
    import numpy as np
    from tools import vec_live_executor as executor
    from tools.npz_cutoffs import is_rth_bar_start

    symbol, side = executor.split_ss(ss)
    bar_ts = executor._sec(bar_ts)
    decision_ts = executor._sec(decision_ts)
    result, npz = executor.run_engine(ss, overrides, anchor, end_ts=bar_ts, raw=raw)
    base = executor.base_prepared(ss) or {}
    base_size = float(((base.get("base_cfg_dict") or {}).get("START_POSITION_SIZE")) or 0.0)
    if npz is None:
        n_bars = 0
    else:
        n_bars = len(np.asarray(npz.get("timestamps", [])))
    engine_valid = executor.replay_computed(result)
    status, why = "OK", ""
    if not is_rth_bar_start(bar_ts):
        status, why = "OFF_SESSION_BAR", "completed stock bar starts outside the NYSE regular session"
    elif npz is None:
        status, why = "ENGINE_ERROR", str(result.get("invalid_reason") or "NPZ window build failed")
    elif result.get("execution_ledger") is None:
        status, why = "ENGINE_ERROR", str(result.get("invalid_reason") or "execution ledger missing")
    elif n_bars < executor.MIN_BARS:
        status, why = "INSUFFICIENT_BARS", f"{n_bars} < {executor.MIN_BARS} bars from anchor"
    elif not engine_valid:
        status, why = "INVALID_30D_REPLAY", str(result.get("invalid_reason") or "unverified 30D replay")
    raw_events, target, consistent, consistency_why = (
        ([], {"side": "unknown"}, False, "")
        if status != "OK"
        else executor.decide(ss, result, npz, bar_ts, side, base_size)
    )
    if status == "OK" and not consistent:
        status, why = "INCONSISTENT", consistency_why
    state_age_sec = float(decision_ts) - (float(bar_ts) + float(executor.BAR_SEC))
    if status == "OK" and not 0.0 <= state_age_sec <= float(executor.STALE_SEC):
        status, why = "STALE", f"completed bar age {state_age_sec:.1f}s is outside [0,{executor.STALE_SEC}]s"
    root = Path(engine_root) if engine_root is not None else Path(executor.ROOT)
    state = {
        "ss": ss,
        "mode": "live",
        "account": "trb",
        "status": status,
        "actionable": status == "OK",
        "why": why,
        "bar_ts": bar_ts,
        "bar_iso": dt.datetime.fromtimestamp(bar_ts, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "decision_ts": decision_ts,
        "state_age_sec": state_age_sec,
        "target_state": target,
        "events_this_bar": raw_events,
        "engine_md5": executor.engine_md5()["combined"],
        "v12_quick_engine_md5": hashlib.md5((root / "v12_quick_engine.py").read_bytes()).hexdigest(),
        "set_md5": executor.set_md5(overrides),
        "anchor_ts": float(anchor["anchor_ts"]),
        "window_policy": anchor.get("window_policy"),
        "bars_from_anchor": n_bars,
        "engine_valid": engine_valid,
        "performance_valid": result.get("valid") is True,
        "performance_invalid_reason": result.get("invalid_reason") if result.get("valid") is not True else None,
        "engine_invalid_reason": result.get("invalid_reason"),
    }
    return state
