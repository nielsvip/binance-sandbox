#!/usr/bin/env python3
"""Equivalence proof for tools/vec_live_executor.py (S1 only — needs the full NPZ corpus).

For each sym_side: ONE full-window simulate_one run from the fixed anchor (the
sheet engine's trades) vs the incremental executor replayed bar by bar — the raw
NPZ truncated at bar k (as if that bar had just landed), re-aligned, sliced from
the same anchor, compacted, simulated — for k in the last N bars. The intents +
target state the executor emits at k must equal the full run's ledger events at
bar k and its position after bar k. Also: determinism (two full runs identical),
anchored-run == canonical evaluate_v12.prepare run, and a sliding-30D sample to
document why the window must be anchored.

  .venv/bin/python tests/test_vec_live_executor.py ATOMUSDT_LONG BBUSDT_SHORT --bars 100 --workers 4
  pytest tests/test_vec_live_executor.py   (env VEC_LIVE_TEST_SS="A_LONG,B_SHORT")
"""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import vec_live_executor as X  # noqa: E402

_CTX = {}


def _sig(raw_events, tgt):
    ev = [(e["type"], e["engine_type"], e["reason"], round(float(e["price_ref"]), 10), round(float(e["qty_units"]), 10)) for e in raw_events]
    return ev, (tgt.get("side"), round(float(tgt.get("qty_units") or 0.0), 10))


def _overrides(ss):
    c = json.loads(X.CANDIDATES.read_text())
    return dict(c[ss]["overrides"]) if ss in c else {}


def _one_k(k):
    ss, ov, anchor, raw, ts = _CTX["ss"], _CTX["ov"], _CTX["anchor"], _CTX["raw"], _CTX["ts"]
    side = X.split_ss(ss)[1]
    res, npz = X.run_engine(ss, ov, anchor, end_ts=ts[k], raw=raw)
    last = X._sec(npz["timestamps"][-1])
    ev, tgt, ok, why = X.decide(ss, res, npz, ts[k], side, _CTX["base_size"])
    return k, abs(last - ts[k]) < 1e-6, _sig(ev, tgt), ok, why


def _one_slide(k):
    ss, ov, raw_full, ts, mode = _CTX["ss"], _CTX["ov"], _CTX["raw"], _CTX["ts"], _CTX["anchor"]["mode"]
    side = X.split_ss(ss)[1]
    a_ts = ts[k] - 30 * 86400.0
    ls = dt.datetime.fromtimestamp(a_ts, tz=dt.timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    anchor = {"anchor_ts": a_ts, "load_start_ts": ls, "mode": mode}
    raw = X.load_raw(X.split_ss(ss)[0], ls)
    res, npz = X.run_engine(ss, ov, anchor, end_ts=ts[k], raw=raw)
    ev, tgt, ok, why = X.decide(ss, res, npz, ts[k], side, _CTX["base_size"])
    return k, _sig(ev, tgt)


def run_ss(ss: str, bars: int, workers: int, slide: int) -> dict:
    import numpy as np
    from tools.opt import evaluate_v12 as E
    from tools.opt import v12_pilot as P
    t0 = time.time()
    ov = _overrides(ss)
    sym, side = X.split_ss(ss)
    anchor = X.canonical_anchor(ss)
    raw = X.load_raw(sym, anchor["load_start_ts"])
    base_size = float(X.base_prepared(ss)["base_cfg_dict"].get("START_POSITION_SIZE") or 0.0)
    full, npz = X.run_engine(ss, ov, anchor, raw=raw)
    full2, _ = X.run_engine(ss, ov, anchor, raw=raw)
    led = full.get("execution_ledger") or []
    det = json.dumps(led, sort_keys=True, default=str) == json.dumps(full2.get("execution_ledger") or [], sort_keys=True, default=str)
    canon = P.evaluate_prepared_sanitized(P.prepare_batch(ss, 30), dict(ov), 30, include_ledger=True)
    canon_eq = {"anchored_trades": full.get("trades"), "canonical_trades": canon.get("trades"), "anchored_gain": full.get("gain_pct"), "canonical_gain": canon.get("gain_pct"), "fingerprint_equal": full.get("behavior_fingerprint") == canon.get("behavior_fingerprint")}
    ts = [X._sec(t) for t in np.asarray(npz["timestamps"], dtype="float64")]
    n = len(ts)
    ks = list(range(max(X.MIN_BARS, n - bars), n))
    expected = {}
    for k in ks:
        ev, tgt, _, _ = X.decide(ss, full, npz, ts[k], side, base_size)
        expected[k] = _sig(ev, tgt)
    _CTX.update({"ss": ss, "ov": ov, "anchor": anchor, "raw": raw, "ts": ts, "base_size": base_size})
    import multiprocessing as mp
    with mp.get_context("fork").Pool(workers) as pool:
        got = pool.map(_one_k, ks)
        slid = pool.map(_one_slide, ks[-slide:]) if slide else []
    mism, end_bad, incons = [], 0, []
    for k, end_ok, sig, ok, why in got:
        end_bad += 0 if end_ok else 1
        if not ok:
            incons.append((k, why))
        if sig != expected[k]:
            mism.append({"k": k, "bar": X._iso(ts[k]), "expected": expected[k], "got": sig})
    slide_mism = [{"k": k, "bar": X._iso(ts[k]), "expected": expected[k], "got": sig} for k, sig in slid if sig != expected[k]]
    n_event_bars = sum(1 for k in ks if expected[k][0])
    return {"ss": ss, "n_bars_window": n, "bars_tested": len(ks), "bars_with_events": n_event_bars, "events_expected": sum(len(expected[k][0]) for k in ks), "mismatches": len(mism), "mismatch_detail": mism[:10], "truncation_end_misaligned": end_bad, "inconsistent_state_flags": incons[:10], "deterministic": det, "anchored_vs_canonical": canon_eq, "sliding_sample": len(slid), "sliding_mismatches": len(slide_mism), "sliding_detail": slide_mism[:5], "anchor_iso": X._iso(anchor["anchor_ts"]), "secs": round(time.time() - t0, 1)}


_STALE_REASON = "STALE test: npz older than gate (unit probe)"


def _stale_base(ss):
    return {"_stale": _STALE_REASON, "symside": ss, "window_days": 30}


def test_run_engine_stale_prepare_no_build():
    """Stale prepare must short-circuit before any raw/npz build (USER 2026-10-13: 200k/cycle KeyError burn)."""
    _orig_base, _orig_load = X.base_prepared, X.load_raw
    X.base_prepared = lambda ss: _stale_base(ss)
    X.load_raw = lambda *a, **k: (_ for _ in ()).throw(AssertionError("load_raw must not run on stale prepare"))
    try:
        res, npz = X.run_engine("PROBEUSDT_LONG", {}, {"mode": "crypto", "anchor_ts": 0.0, "load_start_ts": 0.0})
    finally:
        X.base_prepared, X.load_raw = _orig_base, _orig_load
    assert npz is None
    assert res.get("execution_ledger") is None and _STALE_REASON in str(res.get("invalid_reason"))


def test_process_stale_prepare_engine_error(tmp_path, monkeypatch):
    """process() with stale prepare records ENGINE_ERROR instead of raising KeyError."""
    import numpy as np
    monkeypatch.setattr(X, "base_prepared", lambda ss: _stale_base(ss))
    monkeypatch.setattr(X, "npz_last_parent", lambda sym: (1700000000.0, 1700000000.0))
    monkeypatch.setattr(X, "get_anchor", lambda ss: {"mode": "crypto", "anchor_ts": 1699900000.0, "load_start_ts": 1699900000.0})
    monkeypatch.setattr(X, "run_engine", lambda ss, ov, anchor, end_ts=None, raw=None: ({"valid": False, "invalid_reason": _STALE_REASON}, None))
    monkeypatch.setattr(X, "decide", lambda *a, **k: (_ for _ in ()).throw(AssertionError("decide must not run without bars")))
    monkeypatch.setattr(X, "STATE", tmp_path / "state")
    rec = X.process("PROBEUSDT_LONG", {"mode": "shadow"}, {}, "emd5-test", False)
    assert rec["status"] == "ENGINE_ERROR" and _STALE_REASON in rec["why"]
    st = json.loads((tmp_path / "state" / "PROBEUSDT_LONG.json").read_text())
    assert st["status"] == "ENGINE_ERROR" and st["actionable"] is False


def test_equivalence():
    sss = [s for s in os.environ.get("VEC_LIVE_TEST_SS", "").split(",") if s]
    if not sss:
        import pytest
        pytest.skip("set VEC_LIVE_TEST_SS")
    for ss in sss:
        r = run_ss(ss, int(os.environ.get("VEC_LIVE_TEST_BARS", "100")), 4, 0)
        assert r["deterministic"] and r["mismatches"] == 0 and r["truncation_end_misaligned"] == 0, r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ss", nargs="+")
    ap.add_argument("--bars", type=int, default=100)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--slide", type=int, default=20, help="sliding-window sample bars (documentation only)")
    ap.add_argument("--out", default=str(X.VL / "equivalence_test.json"))
    a = ap.parse_args()
    import v12_quick_engine  # noqa: F401
    em = X.engine_md5()
    out = {"run_at": X._iso(time.time()), "engine_md5": em["combined"], "v12_md5": em["v12_quick_engine.py"], "results": []}
    for ss in a.ss:
        r = run_ss(ss, a.bars, a.workers, a.slide)
        out["results"].append(r)
        print(json.dumps(r, default=str), flush=True)
    out["PASS"] = all(r["deterministic"] and r["mismatches"] == 0 and r["truncation_end_misaligned"] == 0 for r in out["results"])
    X._atomic_json(Path(a.out), out)
    print("PASS" if out["PASS"] else "FAIL", a.out)
    return 0 if out["PASS"] else 1


if __name__ == "__main__":
    sys.exit(main())
