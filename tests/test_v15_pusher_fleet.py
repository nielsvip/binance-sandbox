"""Fleet pusher coordinator logic — ingest, registry merge, round counting, host pick."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import v15_pusher_coordinator as C
import v15_gain_pusher as P


def _rep(ss, rnd, gain, moves=()):
    return {"symside": ss, "round": rnd, "final": {"gain": gain, "trades": 20, "valid": True},
            "moves": [{"flip": dict(m), "delta": 1.0} for m in moves],
            "engine_md5": "abc", "host": "s2"}


def test_ingest_advances_and_adopts_best(tmp_path):
    base = tmp_path / "p"
    (base / "anchors").mkdir(parents=True)
    st = {"syms": {}, "assigned": {"0001_A_LONG": {"host": "s2", "ts": 0}}, "units_done": 0}
    ok, adopted = C.ingest_report(st, base, _rep("A_LONG", 1, 5.0), {"K": 1})
    assert ok and adopted
    assert st["syms"]["A_LONG"]["done_round"] == 1
    assert st["syms"]["A_LONG"]["best"] == {"gain": 5.0, "round": 1}
    assert json.loads((base / "anchors" / "A_LONG.json").read_text()) == {"K": 1}
    assert "0001_A_LONG" not in st["assigned"]
    ok2, _ = C.ingest_report(st, base, _rep("A_LONG", 1, 9.0), {"K": 2})
    assert not ok2
    ok3, adopted3 = C.ingest_report(st, base, _rep("A_LONG", 2, 4.0), {"K": 3})
    assert ok3 and not adopted3
    assert json.loads((base / "anchors" / "A_LONG.json").read_text()) == {"K": 1}


def test_ingest_rejects_error_and_below_round(tmp_path):
    base = tmp_path / "p"
    (base / "anchors").mkdir(parents=True)
    st = {"syms": {"A_LONG": {"done_round": 2}}, "assigned": {}, "units_done": 0, "units_err": 0}
    ok, _ = C.ingest_report(st, base, {"symside": "A_LONG", "round": 2, "error": "x"}, None)
    assert not ok and st["units_err"] == 1
    ok, _ = C.ingest_report(st, base, _rep("A_LONG", 1, 99.0), {})
    assert not ok


def test_merge_registry_adds_frequent_winners(tmp_path):
    base = tmp_path / "p"
    (base / "registry").mkdir(parents=True)
    (base / "registry" / "PRIORITY_SWITCHES.json").write_text(json.dumps({"P1_MUST_TEST": {}, "_version": 1}))
    st = {"rounds_complete": 3, "registry_version": 1}
    reps = [_rep(f"S{i}_LONG", 4, 1.0, moves=[{"STOP_LOSS_PCT": 1}]) for i in range(6)]
    reps.append(_rep("Z_LONG", 4, 1.0, moves=[{"RARE_K": 2}]))
    n = C.merge_registry(base, st, reps)
    assert n == 1
    reg = json.loads((base / "registry" / "PRIORITY_SWITCHES.json").read_text())
    assert reg["_version"] == 2
    assert reg["P1_MUST_TEST"]["STOP_LOSS_PCT"]["test_values"] == [1]
    assert "RARE_K" not in reg["P1_MUST_TEST"]


def test_rounds_complete_counts_full_rounds():
    st = {"syms": {"A": {"done_round": 3}, "B": {"done_round": 2}, "C": {"done_round": 9}}}
    assert C.rounds_complete(st, ["A", "B", "C"]) == 2
    assert C.rounds_complete({"syms": {}}, ["A"]) == 0
    assert C.rounds_complete(st, []) == 0


def test_terminal_sym_counts_until_ttl():
    import time
    now = time.time()
    st = {"syms": {"A": {"done_round": 1}, "B": {"done_round": 0, "terminal": {"reason": "npz missing/corrupt", "ts": now, "round": 1}}}}
    assert C.rounds_complete(st, ["A", "B"], now) == 1
    assert C.rounds_complete(st, ["A", "B"], now + C.TERMINAL_TTL + 1) == 0


def test_covered_frontier_rejoins_without_blocking():
    import time
    now = time.time()
    st = {"syms": {"A": {"done_round": 50}, "B": {"done_round": 0, "covered": 49, "terminal": {"reason": "npz missing/corrupt", "ts": now - C.TERMINAL_TTL - 1, "round": 1}}}}
    assert C.eff_done(st, "B") == 49
    assert C.rounds_complete(st, ["A", "B"], now) == 49


def test_ingest_terminal_skip(tmp_path):
    base = tmp_path / "p"
    (base / "anchors").mkdir(parents=True)
    st = {"syms": {}, "assigned": {"0001_Q_LONG": {"host": "s2", "ts": 0}}, "units_done": 0, "units_err": 0}
    ok, _ = C.ingest_report(st, base, {"symside": "Q_LONG", "round": 1, "skip": "npz missing/corrupt"}, None)
    assert not ok and st["units_err"] == 1
    assert st["syms"]["Q_LONG"]["terminal"]["reason"] == "npz missing/corrupt"
    assert "0001_Q_LONG" not in st["assigned"]


def test_pick_host_least_loaded_by_weight():
    hosts = {"s1": "127.0.0.1", "s2": "10.0.0.4", "s5": "10.0.0.5"}
    st = {"assigned": {f"u{i}": {"host": "s2", "ts": 0} for i in range(14)},
          "hostinfo": {"s2": {"w": 14, "ts": 9e12}, "s5": {"w": 7, "ts": 9e12}}}
    assert C.pick_host(st, hosts, set()) in ("s5", "s1")
    st2 = {"assigned": {}}
    assert C.pick_host(st2, hosts, set()) == "s1"
    assert C.pick_host(st2, hosts, {"s1", "s2", "s5"}) is None
    st3 = {"assigned": {f"u{i}": {"host": "s2", "ts": 0} for i in range(100)},
           "hostinfo": {"s2": {"w": 14, "ts": 9e12}, "s5": {"w": 7, "ts": 9e12}}}
    assert C.pick_host(st3, hosts, set()) != "s2"


def test_p2_topups_rank_and_parse():
    possym = {"cat_sides": {"CRYPTO_LONG": {
        "S!A=True": {"kind": "switch", "pos_sym": 10, "avg_delta": 1.0},
        "S!B=0.95": {"kind": "switch", "pos_sym": 10, "avg_delta": 2.0},
        "S!C=1h": {"kind": "filter", "pos_sym": 99, "avg_delta": 9.0},
        "S!D=7": {"kind": "switch", "pos_sym": 2, "avg_delta": 5.0}}}}
    top = P.p2_topups(possym, "CRYPTO_LONG")
    assert [(t["switch"], t["value"]) for t in top] == [("B", 0.95), ("A", True)]
    assert top[0]["pos_sym"] == 10
    assert P.p2_topups(possym, "NOPE") == []
    assert P.parse_candidate_value("False") is False
    assert P.parse_candidate_value("1h") == "1h"
    assert P.parse_candidate_value("7") == 7


def test_ablation_keys_filters_in_set():
    assert P.ablation_keys({"A": 1, "B": 2}, ["B", "C"]) == ["B"]
    assert P.ablation_keys({"A": 1}, []) == []


def test_host_down_requeues(tmp_path):
    base = tmp_path / "p"
    (base / "outbox" / "s6").mkdir(parents=True)
    (base / "outbox" / "s6" / "0002_A.json").write_text("{}")
    st = {"assigned": {"0002_A": {"host": "s6", "ts": 0}, "0002_B": {"host": "s2", "ts": 0}}}
    C.host_down(st, base, "s6")
    assert st["assigned"] == {"0002_B": {"host": "s2", "ts": 0}}
    assert list((base / "outbox" / "s6").glob("*.json")) == []
