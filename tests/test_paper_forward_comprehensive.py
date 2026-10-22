"""Ledger collateral for tools/paper_forward_comprehensive.py (USER 2026-10-22).

The comprehensive forward test must score every disabled/nonvec/low-TF path per
sym_side honestly: canonical families (no param-singleton explosion), correct
entry/exit signs, pending-never-zero, venue alias tradier->stocks, and
promotion candidates gated on sample (never automatic).
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.paper_forward_comprehensive as pfc  # noqa: E402


def _ts(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def test_canon_family_collapses_param_singletons():
    assert pfc.canon_family("QUICK_REDUCE_REDUCE_k_1m:92/d_1m:90/k_3m:63") == pfc.canon_family("QUICK_REDUCE_REDUCE_k_1m")
    assert pfc.canon_family("WT_4H_VEL_EXIT_vel=16.1_g=0.36%_k3m=100_k15m=4") == pfc.canon_family("WT_4H_VEL_EXIT_vel=16.1_g=0.53%_k3m=8_k15m=4")
    assert pfc.canon_family("VEC_EXACT_OPEN_B12_ENTRY") == "B12_ENTRY"
    assert pfc.canon_family("") == "UNKNOWN"


def test_canon_venue_alias_tradier_is_stocks():
    assert pfc.canon_venue("tradier") == "stocks"
    assert pfc.canon_venue("stocks") == "stocks"
    assert pfc.canon_venue("fin") == "crypto"
    assert pfc.canon_venue(None) == "crypto"


def test_split_sym_side_venue():
    assert pfc.split_sym_side("BTCUSDC_LONG") == ("crypto", "BTCUSDC", "LONG")
    assert pfc.split_sym_side("AAPL_SHORT") == ("stocks", "AAPL", "SHORT")


def _seed_klines(monkeypatch, venue, symbol, t0, px0=100.0, step=0.01, n=64):
    ts = [t0 + i * 900 for i in range(n)]
    cl = [px0 * (1 + step) ** i for i in range(n)]
    monkeypatch.setitem(pfc._KL, (canon_venue_alias(venue), symbol), (ts, cl))
    return ts, cl


def canon_venue_alias(v):
    return pfc.canon_venue(v)


def test_entry_scores_positive_on_rising_long_net_of_cost(monkeypatch):
    t0 = 1_700_000_000
    _seed_klines(monkeypatch, "crypto", "TST", t0)
    e = {"venue": "crypto", "kind": "entry", "fn": "GOLDEN_RULE", "_t": t0 + 900,
         "position_key": "fin:TST_LONG", "_venue": "crypto", "_fam": "GOLDEN_RULE"}
    per, _ = pfc.score([e], horizon_bars=4)
    rows = per[("GOLDEN_RULE", "crypto", "entry", "TST_LONG")]
    assert len(rows) == 1 and not rows[0][1] and rows[0][0] > 0


def test_entry_scores_negative_on_falling_long(monkeypatch):
    t0 = 1_700_000_000
    _seed_klines(monkeypatch, "crypto", "TST", t0, step=-0.01)
    e = {"venue": "crypto", "kind": "entry", "fn": "GOLDEN_RULE", "_t": t0 + 900,
         "position_key": "fin:TST_LONG", "_venue": "crypto", "_fam": "GOLDEN_RULE"}
    per, _ = pfc.score([e], horizon_bars=4)
    assert per[("GOLDEN_RULE", "crypto", "entry", "TST_LONG")][0][0] < 0


def test_exit_long_paper_above_real_is_positive(monkeypatch, tmp_path):
    t0 = 1_700_000_000
    _seed_klines(monkeypatch, "crypto", "TST", t0, px0=100.0, step=0.0)
    # paper exits at bar close 100; real lifecycle close later at 90 -> +11.1%
    hist = tmp_path / "fin" / "TST_LONG.jsonl"
    hist.parent.mkdir(parents=True)
    hist.write_text(json.dumps({"type": "CLOSE", "ts": _ts(t0 + 50 * 900), "price": 90.0}) + "\n")
    monkeypatch.setattr(pfc, "ROOT", tmp_path, raising=False)
    # ROOT swap: closes()/real_closes use module ROOT; restore via direct cache instead
    monkeypatch.setattr(pfc, "ROOT", ROOT, raising=False)
    real = [(t0 + 50 * 900, 90.0)]
    monkeypatch.setattr(pfc, "real_closes", lambda a, ss: real)
    e = {"venue": "crypto", "kind": "exit", "fn": "FIXED_STOP", "_t": t0 + 900,
         "position_key": "fin:TST_LONG", "_venue": "crypto", "_fam": "FIXED_STOP"}
    pfc._KL[("crypto", "TST")] = ([t0 + i * 900 for i in range(64)], [100.0] * 64)
    per, _ = pfc.score([e], horizon_bars=4)
    d, pend = per[("FIXED_STOP", "crypto", "exit", "TST_LONG")][0]
    assert not pend and abs(d - (100.0 - 90.0) / 90.0) < 1e-9


def test_missing_horizon_is_pending_never_zero(monkeypatch):
    t0 = 1_700_000_000
    pfc._KL[("crypto", "TST2")] = ([t0], [100.0])
    e = {"venue": "crypto", "kind": "entry", "fn": "GOLDEN_RULE", "_t": t0,
         "position_key": "fin:TST2_LONG", "_venue": "crypto", "_fam": "GOLDEN_RULE"}
    # symbol mismatch (TST2 vs TST_LONG sym) still resolves TST2 klines with 1 bar -> horizon missing
    e["position_key"] = "fin:TST2_LONG"
    per, _ = pfc.score([e], horizon_bars=16)
    assert per[("GOLDEN_RULE", "crypto", "entry", "TST2_LONG")][0] == (None, True)


def test_aggregate_promotion_needs_sample_and_majority():
    now = time.time()
    per = {("P", "crypto", "entry", f"S{i}_LONG"): [(0.01, False)] * 3 for i in range(6)}
    _, paths = pfc.aggregate(per, {("crypto", "entry", "P"): now - 8 * 86400}, now, 7.0, 5, 3)
    assert paths[0]["verdict"] == "PROMOTE_CANDIDATE"
    per_neg = {("Q", "crypto", "entry", f"S{i}_LONG"): [(-0.01, False)] * 3 for i in range(6)}
    _, paths2 = pfc.aggregate(per_neg, {("crypto", "entry", "Q"): now - 8 * 86400}, now, 7.0, 5, 3)
    assert paths2[0]["verdict"] == "REJECT"
    _, paths3 = pfc.aggregate(per, {("crypto", "entry", "P"): now - 1 * 86400}, now, 7.0, 5, 3)
    assert paths3[0]["verdict"] == "WATCH"


def test_inventory_covers_known_knobs():
    inv = pfc.build_inventory()
    assert "WT_3M_FORCE_OPEN" in inv
    assert "BREAKOUT_LEASH" in inv
    assert "QUICK_REDUCE_STRONG" in inv or "QUICK_REDUCE" in inv
    kinds = {d.get("kind") for pid, d in inv.items() if not pid.startswith("_")}
    assert {"nonvec", "low_tf", "disabled"} <= kinds
