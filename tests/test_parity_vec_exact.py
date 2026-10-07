"""parity-loop-crypto 2026-10-06: PARITY_VEC_EXACT_MODE twin (live_twins/vec_exact.py).

Unit: order sizing from vec fractions, reason tag keeps the vec reason family, master default off.
Equivalence (needs a full crypto NPZ, S1/s5): actions_at() at every bar >= 100 == the full v12 run's actions at that bar.
"""
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_twins import vec_exact as vx  # noqa: E402


def test_master_config_consistent():
    # 2026-10-06 18:47Z switch-over: master ON live (runbook §3); when ON the Mac source is live_klines (never npz) and all 3 families are twinned
    from config import Config
    if vx.enabled(Config):
        assert str(Config.PARITY_VEC_EXACT_SOURCE) == "live_klines"
        assert vx.families(Config) == {"ENTRY", "EXIT", "AUGMENT"}
    else:
        assert vx.families(Config) <= {"ENTRY", "EXIT", "AUGMENT"}


def test_order_args_fractions():
    a = {"type": "REDUCE", "qty_frac": 0.5, "vec_qty": 3.0}
    assert vx.order_args(a, "LONG", 10.0, 1.0) == ("REDUCE", "SELL", 5.0, False)
    assert vx.order_args(dict(a, type="AUGMENT", qty_frac=0.25), "SHORT", 8.0, 1.0) == ("AUGMENT", "SELL", 2.0, False)
    assert vx.order_args(dict(a, type="CLOSE"), "SHORT", 8.0, 1.0) == ("CLOSE", "BUY", 8.0, True)
    assert vx.order_args(dict(a, type="OPEN"), "LONG", 0.0, 1.0) == ("OPEN", "BUY", 3.0, False)


def test_reason_tag_keeps_family():
    sys.path.insert(0, str(ROOT / "tools"))
    import v15_trade_parity as TP
    r = vx.tagged_reason({"reason": "EXIT_VELOCITY_WT against-short g0.19%"})
    assert vx.is_vec_exact_reason(r)
    assert TP.family(r) == "EXIT_VELOCITY_WT"
    assert TP.family(vx.tagged_reason({"reason": "B_KZONE"})) == "B_KZONE"


SS = os.environ.get("VEC_EXACT_TEST_SS", "FLNCUSDT_SHORT")
OVF = os.environ.get("VEC_EXACT_TEST_OV", "")


@pytest.mark.skipif(not OVF or not pathlib.Path(OVF).exists(), reason="set VEC_EXACT_TEST_OV=<frozen set json> on a host with the full NPZ")
def test_bar_fresh_equals_full_run(monkeypatch):
    import numpy as np
    from tools.opt import evaluate_v12 as E
    ov = json.loads(pathlib.Path(OVF).read_text())
    ov = ov.get("cumulative_overrides") or ov
    monkeypatch.setattr(vx, "overrides_for", lambda s, d: dict(ov))
    sym, side = SS.rsplit("_", 1)
    p = E.prepare(SS, 30)
    full = E.evaluate_prepared(p, dict(ov), include_ledger=True)
    exp = {}
    for e in full.get("execution_ledger") or []:
        if not str(e.get("reason", "")).startswith("FINAL_MTM"):
            exp.setdefault(float(e["ts"]), []).append((e["type"], str(e.get("reason"))))
    ts = [float(t) for t in p["npz_prepared"]["timestamps"]]
    bars = sorted(t for t in exp if ts.index(t) >= 99)[:120]
    bad = []
    for t in bars:
        got = [(a["type"], a["reason"]) for a in vx.actions_at(sym, side, t)["actions"]]
        if got != exp[t]:
            bad.append((t, exp[t], got))
    assert not bad, bad[:5]


def test_live_sizing_args_ruling():
    from types import SimpleNamespace
    from live_twins import vec_exact as vx
    cfg = SimpleNamespace(START_POSITION_SIZE=20.0, PARITY_VEC_EXACT_LIVE_SIZING=True)
    assert vx.live_sizing_args("OPEN", 7.0, 2.0, cfg) == (10.0, None)
    assert vx.live_sizing_args("AUGMENT", 3.0, 4.0, cfg) == (5.0, None)
    assert vx.live_sizing_args("REDUCE", 3.0, 4.0, cfg) == (3.0, 3.0)
    assert vx.live_sizing_args("CLOSE", 9.0, 4.0, cfg) == (9.0, 9.0)
    cfg.PARITY_VEC_EXACT_LIVE_SIZING = False
    assert vx.live_sizing_args("OPEN", 7.0, 2.0, cfg) == (7.0, 7.0)


def test_reduce_to_flat_maps_to_full_close():
    from live_twins import vec_exact as vx
    a = {"type": "REDUCE", "reason": "REDUCE_TO_FLAT", "qty_frac": 1.0, "vec_qty": 0.0, "to_flat": True}
    assert vx.order_args(a, "SHORT", 12.5, 1.0) == ("CLOSE", "BUY", 12.5, True)
    b = {"type": "REDUCE", "reason": "REGIME_REDUCE", "qty_frac": 0.5, "vec_qty": 1.0, "to_flat": False}
    assert vx.order_args(b, "LONG", 10.0, 1.0) == ("REDUCE", "SELL", 5.0, False)
