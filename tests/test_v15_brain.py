import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import v15_brain as B


def _log(tmp_path, sym_side, rows):
    p = tmp_path / f"{sym_side}_jump.jsonl"
    with open(p, "w") as fh:
        for i, (sw, cand, naked, yellows) in enumerate(rows):
            ts = f"2026-10-1{i % 10}T00:00:00Z"
            fh.write(json.dumps({"ts": ts, "sheet": "T", "row": i, "switch": sw, "cand": cand, "label": "naked", "delta": naked, "valid": True, "secs": 1.0, "tim": 50.0, "trades": 20}) + "\n")
            for h, d in yellows.items():
                fh.write(json.dumps({"ts": ts, "sheet": "T", "row": i, "switch": sw, "cand": cand, "label": h, "delta": d, "valid": True, "secs": 0.5, "tim": 52.0, "trades": 18, "max_dd_pct": 9.0}) + "\n")
            fh.write(json.dumps({"ts": ts, "sheet": "T", "row": i, "switch": sw, "cand": cand, "label": "ROW_DONE"}) + "\n")
    return str(p)


def test_marginal_and_pair_edges(tmp_path):
    f = _log(tmp_path, "BTCUSDC_LONG", [("A", "1", 0.0, {"F=x": 2.0, "F=y": -1.0}), ("C", "2", 1.0, {"F=x": 1.0})])
    brain = B.Brain()
    for sym_side, ts, _s, rk, naked, marg in B.iter_rows(f):
        brain.observe_row(sym_side, ts, rk, naked, marg)
    e = brain.edge("CRYPTO_LONG", "F:F=x", "R:A=1")
    assert e.pos == 1 and abs(e.sum_pos - 2.0) < 1e-9
    e2 = brain.edge("CRYPTO_LONG", "F:F=x", "R:C=2")
    assert e2.pos == 0 and e2.neg == 0  # marginal 1.0 - 1.0 = 0 -> neither
    assert brain.edge("GLOBAL", "R:C=2").pos == 1
    assert brain.edge("GLOBAL", "R:A=1").pos == 0


def test_p_eval_floor_and_failopen():
    assert B.p_eval(0.0, 0.1, 5, n_ev=100) == 0.005
    assert B.p_eval(0.5, 0.1, 5, n_ev=100) == 1.0
    assert B.p_eval(0.0, 0.1, 5, n_ev=1) == 1.0
    assert abs(B.p_eval(0.05, 0.1, 5, n_ev=100) - 0.5) < 1e-9


def test_shrinkage_orders_evidence(tmp_path):
    rows = [("DEAD", "1", 0.0, {}) for _ in range(30)] + [("GOOD", "1", 1.0, {}) for _ in range(5)] + [("GOOD", "1", 0.0, {}) for _ in range(5)]
    f = _log(tmp_path, "ETHUSDC_SHORT", rows)
    brain = B.Brain()
    for sym_side, ts, _s, rk, naked, marg in B.iter_rows(f):
        brain.observe_row(sym_side, ts, rk, naked, marg)
    p_dead, _ = brain.p_pos("ETHUSDC_SHORT", "R:DEAD=1")
    p_good, _ = brain.p_pos("ETHUSDC_SHORT", "R:GOOD=1")
    assert p_dead < 0.05 < p_good
    c, pe, _ = B.decide(brain, "ETHUSDC_SHORT", "T", "GOOD=1", None, "r1", 5, 0.1)
    assert pe == 1.0 and c
