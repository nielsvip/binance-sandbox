import os
from pathlib import Path

import numpy as np
import pytest

import v12_quick_engine as V
from vec_decisions.generic_filter_tf import _cond
from vec_decisions.htf_causal_align import align_store
from vec_decisions.tradier_reentry_final_filter import allows_stock_reentry


def _mask(npz, tf, is_long):
    n = len(npz["timestamps"])

    def safe(data, key, size, default=0.0):
        if key in data:
            return np.asarray(data[key], dtype=float)
        return np.full(size, default, dtype=float)

    return _cond(
        "wt_cross_side",
        npz,
        n,
        tf,
        is_long,
        np.asarray(npz["close"], dtype=float),
        safe,
    )


@pytest.mark.parametrize(
    "is_long,wt1,wt2,passes",
    [(True, 2.0, 1.0, True), (True, 1.0, 2.0, False), (False, 1.0, 2.0, True), (False, 2.0, 1.0, False)],
)
def test_active_circuit_filter_gates_tradier_reentry_both_sides(is_long, wt1, wt2, passes):
    mask = np.asarray([wt1 > wt2 if is_long else wt1 < wt2])
    assert allows_stock_reentry(True, True, True, False, False, "1h", mask, 0) is passes
    assert allows_stock_reentry(True, False, True, False, False, "1h", mask, 0)
    assert allows_stock_reentry(True, True, False, False, False, "1h", mask, 0)
    assert allows_stock_reentry(True, True, True, True, False, "1h", mask, 0)
    assert allows_stock_reentry(True, True, True, False, True, "1h", mask, 0)


def test_filter_off_and_owner_inactive_leave_reentry_unchanged():
    assert allows_stock_reentry(True, True, True, False, False, "OFF", None, 0)
    assert allows_stock_reentry(True, True, True, False, False, "15m", np.asarray([False]), 0)
    # A missing native snapshot value is fail-open, matching the live final gate.
    assert allows_stock_reentry(True, True, True, False, False, "1h", None, 0)


FROZEN_AAPL = Path(
    os.environ.get(
        "TRADIER_FINAL_FILTER_NPZ",
        "/tmp/tradier_full_candidate/indicators/AAPL.npz",
    )
)


@pytest.mark.skipif(not FROZEN_AAPL.is_file(), reason="frozen AAPL candidate NPZ unavailable")
def test_frozen_aapl_completed_hour_bar_blocks_long_reentry(monkeypatch):
    import tools.vec_live_executor as live_vec
    import vec_decisions.tradier_reentry_final_filter as final_filter

    with np.load(FROZEN_AAPL, allow_pickle=True) as data:
        raw = {key: np.asarray(data[key]) for key in data.files}
    anchor = live_vec.canonical_anchor("AAPL_LONG", raw["timestamps"])
    _, overrides = live_vec.load_sets()["AAPL_LONG"]
    assert str(overrides.get("CIRCUIT_SHARPE_GATES_FILTER_TF", "OFF")).lower() == "1h"
    calls = []
    original = final_filter.allows_stock_reentry

    def capture(*args):
        allowed = original(*args)
        mask_value = None if args[6] is None else bool(args[6][args[7]])
        calls.append((args[7], args[5], mask_value, allowed))
        return allowed

    monkeypatch.setattr(final_filter, "allows_stock_reentry", capture)
    result, canonical = live_vec.run_engine(
        "AAPL_LONG", overrides, anchor, raw=raw
    )
    assert result["valid"] is True
    assert calls
    target_ts = 1792420200
    target = int(np.flatnonzero(np.asarray(canonical["timestamps"]) == target_ts)[0])
    store = align_store(dict(raw), "AAPL", "tradier")
    native_row = int(np.flatnonzero(np.asarray(store["timestamps"]) == target_ts)[0])
    assert canonical["wt1_1h"][target] == pytest.approx(store["wt1_1h"][native_row])
    assert canonical["wt2_1h"][target] == pytest.approx(store["wt2_1h"][native_row])
    assert canonical["wt1_1h"][target] < canonical["wt2_1h"][target]
    assert any(
        int(canonical["timestamps"][index]) == target_ts
        and tf == "1h"
        and mask_value is False
        and allowed is False
        for index, tf, mask_value, allowed in calls
    )
    assert any(mask_value is True and allowed is True for _, _, mask_value, allowed in calls)
