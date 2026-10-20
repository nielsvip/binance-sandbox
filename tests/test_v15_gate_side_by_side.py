import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


os.environ.setdefault("V15_BENCH_DIR", "/nonexistent_bench_for_tests")
gate = _load("v15_version_gate")
bench = _load("v15_npz_bench")
SIDES = ["A_LONG", "B_SHORT", "C_LONG", "D_SHORT"]


def _res(fit, oos, cells, naked):
    return {"final_gain": fit, "oos_final": oos, "coverage": {"cells_eval": cells, "naked_rows": naked, "rows": naked},
            "truth": {"fit": {"gain_pct": fit}, "oos": {"gain_pct": oos}}}


def _both(fit_c, oos_c, cells_c, fit_n, oos_n, cells_n, naked_c=100, naked_n=100):
    ch = {s: _res(fit_c, oos_c, cells_c, naked_c) for s in SIDES}
    ca = {s: _res(fit_n, oos_n, cells_n, naked_n) for s in SIDES}
    return gate.decide(ch, ca, SIDES)


def test_equal_coverage_within_tolerance_passes():
    d = _both(10, 1, 1000, 9.8, 0.9, 1000)
    assert d["PASS"] and not d["corner_cut"]


def test_corner_cut_with_same_results_fails():
    d = _both(10, 1, 1000, 10, 1, 500)
    assert d["corner_cut"] and not d["PASS"]
    assert any("CORNER_CUT" in r for r in d["reasons"])


def test_corner_cut_with_tiny_fit_loss_fails_even_inside_tolerance():
    d = _both(10, 1, 1000, 9.9, 3, 500)
    assert not d["PASS"]


def test_corner_cut_that_wins_oos_beyond_noise_and_keeps_fit_passes():
    d = _both(10, 1, 1000, 10.2, 2, 500)
    assert d["corner_cut"] and d["PASS"]


def test_naked_row_cut_is_a_corner_cut():
    d = _both(10, 1, 1000, 10, 1, 1000, naked_c=100, naked_n=50)
    assert d["corner_cut"] and not d["PASS"]


def test_unreadable_candidate_coverage_is_a_cut():
    ch = {s: _res(10, 1, 1000, 100) for s in SIDES}
    ca = {s: {"final_gain": 10, "oos_final": 1, "coverage": {"err": "x"}} for s in SIDES}
    assert gate.decide(ch, ca, SIDES)["corner_cut"]


def test_missing_champion_coverage_gives_no_cut_verdict():
    ch = {s: {"final_gain": 10, "oos_final": 1} for s in SIDES}
    ca = {s: _res(10, 1, 10, 1) for s in SIDES}
    d = gate.decide(ch, ca, SIDES)
    assert d["coverage"]["cell_ratio"] is None and not d["corner_cut"]


def test_lost_board_fails():
    ch = {s: _res(10, 1, 1000, 100) for s in SIDES}
    ca = dict(ch)
    ca["A_LONG"] = {}
    assert not gate.decide(ch, ca, SIDES)["PASS"]


def test_env_diff_ignores_routing_keys():
    want = gate.result_env({"V15_INERT_SAMPLE_EVERY": "25", "V15_PROGRESS_DIR": "/a"})
    got = gate.result_env({"V15_INERT_SAMPLE_EVERY": "100", "V15_PROGRESS_DIR": "/b", "PATH": "x"})
    assert gate.env_diff(want, got) == {"V15_INERT_SAMPLE_EVERY": {"champion": "25", "running": "100"}}


def test_board_coverage_counts_evaluated_and_skipped():
    p = {"done": {
        "EXIT_VELOCITY!3:X=1": {"naked_delta": 0.1, "yellows": {"F1": 0.2}, "coarse_deferred_filters": ["F1", "F2", "F3"], "coarse_revived": ["F3"]},
        "EXIT_VELOCITY!4:Y=1": {"reason": "CARRIED_DEAD:x", "inert_filters": ["G1", "G2"]},
        "V17_PROBE!1:Z=1": {"naked_delta": 1.0, "yellows": {"H": 1}},
    }}
    c = bench.board_coverage(p)
    assert c["rows"] == 2 and c["naked_rows"] == 1 and c["rows_no_naked"] == 1
    assert c["cells_eval"] == 1 and c["cells_skipped"] == 3 and c["zero_yellow_rows"] == 1
    assert c["skipped_by_mechanism"] == {"coarse_deferred_filters": 1, "inert_unsampled": 2}
    assert c["no_naked_by_reason"] == {"CARRIED_DEAD": 1}


def test_bench_env_keeps_snapshot_routing_and_forces_stale_ok(monkeypatch):
    monkeypatch.setattr(gate, "snap_manifest", lambda: {"env": {"V12_ALLOW_STALE_NPZ": "1", "V15_DEFAULTS_ROUND": "r32", "V15_INERT_SAMPLE_EVERY": "100", "V15_V17_ROUNDS": "1"}})
    e = gate.bench_env({"V15_INERT_SAMPLE_EVERY": "25"})
    assert e["V12_ALLOW_STALE_NPZ"] == "1" and e["V15_DEFAULTS_ROUND"] == "r32"
    assert e["V15_INERT_SAMPLE_EVERY"] == "25" and "V15_V17_ROUNDS" not in e


def test_no_scored_side_is_harness_fail_not_fail():
    d = gate.decide({}, {}, SIDES)
    assert not d["PASS"] and not d["scored"]


def test_truth_numbers_replace_self_reported_and_missing_truth_blocks():
    def arm(engine_fit, engine_oos, t_fit, t_oos):
        return {s: {"final_gain": engine_fit, "oos_final": engine_oos, "coverage": {"cells_eval": 10, "naked_rows": 10},
                    "truth": {"fit": {"gain_pct": t_fit}, "oos": {"gain_pct": t_oos}}} for s in SIDES}
    # candidate's self-reported numbers look better, its truth is worse -> FAIL on truth
    d = gate.decide(arm(10, 1, 10, 1), arm(20, 5, 5, -3), SIDES)
    assert d["truth_sides"] == 4 and not d["PASS"]
    ch, ca = arm(10, 1, 10, 1), arm(10, 1, 10, 1)
    for s in SIDES:
        ca[s].pop("truth")
    d = gate.decide(ch, ca, SIDES)
    assert not d["PASS"] and any("TRUTH_MISSING" in r for r in d["reasons"])


def test_champion_arm_missing_sides_is_harness_fail_and_names_champion(tmp_path):
    ch = {s: _res(10, 1, 1000, 100) for s in SIDES}
    ca = {s: _res(10, 1, 1000, 100) for s in SIDES}
    ch["A_LONG"] = {"err": "no progress json", "wall_s": 5.0}
    ch["C_LONG"] = {}
    d = gate.decide(ch, ca, SIDES)
    assert not d["PASS"] and d["harness_fail"]
    assert d["missing_champ"] == ["A_LONG", "C_LONG"] and d["missing_cand"] == []
    assert any("CHAMPION arm lost boards" in r and "HARNESS_FAIL" in r for r in d["reasons"])
    assert not any("candidate lost" in r.lower() for r in d["reasons"])
    cd = tmp_path / "cand"
    cd.mkdir()
    gate.DECISIONS = tmp_path / "decisions.jsonl"
    gate.CHAMP = tmp_path / "champion"
    gate.CHAMP.mkdir()
    (gate.CHAMP / "meta.json").write_text("{}")
    gate.write_status = lambda champ: None
    meta = {"id": "X1", "note": "n", "files": {}, "flags": {}}
    gate.finalize(cd, meta, d)
    assert meta["state"] == "HARNESS_FAIL"
    assert '"HARNESS_FAIL"' in gate.DECISIONS.read_text()


def test_candidate_arm_missing_sides_still_fails_and_names_candidate():
    ch = {s: _res(10, 1, 1000, 100) for s in SIDES}
    ca = dict(ch)
    ca["B_SHORT"] = {}
    d = gate.decide(ch, ca, SIDES)
    assert not d["PASS"] and not d["harness_fail"]
    assert d["missing_cand"] == ["B_SHORT"] and d["missing_champ"] == []
    assert any("CANDIDATE arm lost boards" in r for r in d["reasons"])


def test_candidate_fails_only_on_sides_both_arms_finished():
    ch = {s: _res(10, 1, 1000, 100) for s in SIDES}
    ca = {s: _res(5, 1, 1000, 100) for s in SIDES}
    ch["D_SHORT"] = {}
    d = gate.decide(ch, ca, SIDES)
    assert d["harness_fail"] and d["scored"] == ["A_LONG", "B_SHORT", "C_LONG"]


def test_ephemeral_deploy_never_blocks_promotion():
    # 2026-10-20 WP1: s5/s6 are ephemeral; a missed optional record must not make a PASS incomplete, a missed mandatory one must.
    ok_m = {"host": "10.0.0.3", "ok": True}
    assert gate.deploy_complete([ok_m, {"host": "10.0.0.5", "ok": False, "optional": True}])
    assert not gate.deploy_complete([{"host": "10.0.0.3", "ok": False}, {"host": "10.0.0.5", "ok": True, "optional": True}])
    assert not gate.deploy_complete([])


def test_ephemeral_targets_are_optional_and_mandatory_kept(monkeypatch):
    monkeypatch.setattr(gate, "_reachable", lambda h: h == "10.0.0.5")
    monkeypatch.setattr(gate, "EPHEMERAL_DEPLOY", [("10.0.0.5", "/home/niels/binance-sandbox"), ("10.0.0.6", "/home/niels/binance-sandbox")])
    t = gate.deploy_targets()
    assert [x for x in t if not x[2]] == [(h, r, False) for h, r in gate.DEPLOY]
    assert [x[0] for x in t if x[2]] == ["10.0.0.5"]
