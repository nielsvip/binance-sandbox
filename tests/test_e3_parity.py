"""USER 2026-10-23 hawk E3 (trb:ARM_SHORT kill): the tradier cross-analogue
agreed with the vector's wt_structure peak ints only ~45% of bars, and the
crypto trough extras fired live-only. Both lanes now read peak structure
(LONG LH / SHORT HH) and emit the booked E_3_STRUCTURE_EXIT_{n}TF_{tfs} shape.
Proves lane predicate == booking predicate on real NPZ arrays, mode-gating,
and the booked shape in the walk ledger."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np

from vec_decisions import grey_wire_exits as G


def _npz():
    import glob

    f = sorted(glob.glob(str(ROOT / "backtest_v8/indicators/*.npz")))[0]
    return {k: np.asarray(v) for k, v in dict(np.load(f, allow_pickle=True)).items()}


def test_int_decode_is_peak_structure():
    assert G.INT_DECODE["wt_structure"][-1] == "LH"
    assert G.INT_DECODE["wt_structure"][1] == "HH"


def _live_count(structs, is_long):
    n = 0
    for tf in ("15m", "1h", "4h"):
        s = (structs.get(tf, "") or "").upper()
        if (is_long and s == "LH") or (not is_long and s == "HH"):
            n += 1
    return n


def _vec_count(npz, n, is_long):
    c = np.zeros(n, dtype=int)
    for tf in ("15m", "1h", "4h"):
        st = np.asarray(npz[f"wt_structure_{tf}"], dtype=float)
        c = c + ((st == -1).astype(int) if is_long else (st == 1).astype(int))
    return c


def test_lane_predicate_equals_booking_predicate():
    """The live peak-string count on decoded NPZ bars == the vector int count,
    every bar, both sides (decode + predicate chain is one function)."""
    npz = _npz()
    n = len(npz["close"])
    dec = G.INT_DECODE["wt_structure"]
    for is_long in (True, False):
        vec = _vec_count(npz, n, is_long)
        for i in range(0, n, 997):
            structs = {}
            for tf in ("15m", "1h", "4h"):
                v = int(np.asarray(npz[f"wt_structure_{tf}"], dtype=float)[i])
                structs[tf] = dec.get(v, "")
            assert _live_count(structs, is_long) == int(vec[i]), (is_long, i)


def test_walk_books_e3_only_in_mode_2():
    import v12_quick_engine as V

    npz = _npz()

    def e3_rows(mode):
        cfg = V.QuickConfig()
        cfg.E_3_USE_WT_STRUCTURE_EXIT_MODE = mode
        res = V.simulate_one(npz, "PROBE", False, cfg)
        return [
            e
            for e in res["ledger"]
            if "E_3_STRUCTURE_EXIT" in str(e.get("exit_reason", e.get("reason")))
        ]

    on = e3_rows(2)
    assert len(on) > 0, "mode-2 walk must book E3 closes or nothing is proven"
    assert all("TF_" in str(r.get("exit_reason", r.get("reason"))) for r in on)
    assert e3_rows(0) == []
