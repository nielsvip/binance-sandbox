"""tests for the V20 v3 additions (2026-10-22): binding features (tools/v18_features.py), heads M / B and the running-set
decide() (tools/v18_brain.py), the scorer selection key of the digest (tools/v18_digest.py). Fixture binding map only."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import v18_brain as B  # noqa: E402
import v18_features as FX  # noqa: E402
from tools import v18_digest as D  # noqa: E402

CS = "CRYPTO_LONG"


def _map(cs=CS, contract="binding_map_v1"):
    cell = lambda v, r, k=None, mode=None: dict({"verdict": v, "reason": r, "condition_keys": list(k or [])}, **({"condition_mode": mode} if mode else {}))  # noqa: E731
    return {
        "meta": {"cat_side": cs, "contract": contract, "built_at": "2026-10-22T00:00:00Z",
                 "masters_default": {"WT_BOUNCE_ENABLED": {"value": False, "source": "template_bold"}, "COUNTER_TREND_ADD_BLOCK_ENABLED": {"value": False, "source": "template_bold"}},
                 "augment_gate_keys": []},
        "switches": {"EXIT_VELOCITY_WT_MIN_TFS": {"family": "EXIT_VEL", "dead": False}, "DC_BREAK_FILTER_TF": {"family": "ENTRY", "dead": False},
                     "DEAD_SW": {"family": "ENTRY", "dead": True}},
        "filters": {"EMA_9_21_FILTER_FILTER_TF": {"class": "ENTRY_TF_MASK", "stages": ["ENTRY"], "master": None},
                    "RSI_FILTER_TF": {"class": "EXIT_CONFIRM", "stages": ["EXIT_SIG"], "master": None},
                    "WT_BOUNCE_K": {"class": "ENTRY_PARAM", "stages": ["ENTRY"], "master": "WT_BOUNCE_ENABLED"},
                    "MASK_WITH_MASTER": {"class": "ENTRY_TF_MASK", "stages": ["ENTRY"], "master": "MASK_MASTER_ENABLED"},
                    "ADX_REGIME_FILTER_ENABLED": {"class": "ENTRY_BOOL_GATE", "stages": ["ENTRY"], "master": None},
                    "TR_ADX4H_GATE_ENABLED": {"class": "ENTRY_BOOL_GATE", "stages": ["ENTRY"], "master": None}},
        "rules": [],
        "rows": {
            "ENTRY_BREAKOUT_CHANNEL!DC_BREAK_FILTER_TF=D": {"family": "ENTRY", "dead_row": False, "cells": {
                "EMA_9_21_FILTER_FILTER_TF=OFF": cell("BINDS", "ENTRY_MASK"),
                "WT_BOUNCE_K=2": cell("CONDITIONAL", "MASTER_OFF", ["WT_BOUNCE_ENABLED=True"]),
                "MASK_WITH_MASTER=4h": cell("BINDS", "ENTRY_MASK"),
                "RSI_FILTER_TF=4h": cell("INDIRECT", "DIFFERENT_STAGE")}},
            "EXIT_VELOCITY!EXIT_VELOCITY_WT_MIN_TFS=2": {"family": "EXIT_VEL", "dead_row": False, "cells": {
                "RSI_FILTER_TF=4h": cell("BINDS", "EXIT_SIG_CONFIRM"), "EMA_9_21_FILTER_FILTER_TF=OFF": cell("INDIRECT", "DIFFERENT_STAGE")}},
            "ENTRY_BREAKOUT_CHANNEL!DEAD_SW=1": {"family": "ENTRY", "dead_row": True, "cells": {"EMA_9_21_FILTER_FILTER_TF=OFF": cell("NEVER", "DEAD_ROW")}}},
        "summary": {}}


@pytest.fixture()
def learn(tmp_path):
    d = tmp_path / "learn"
    d.mkdir()
    (d / f"binding_map_{CS}.json").write_text(json.dumps(_map()))
    (d / "binding_map_CRYPTO_SHORT.json").write_text(json.dumps(_map(cs="STOCKS_SHORT")))
    (d / "binding_map_STOCKS_LONG.json").write_text(json.dumps(_map(cs="STOCKS_LONG", contract="other")))
    return d


def test_loader_own_cat_side_only_and_fail_open(learn, tmp_path):
    bi, why = FX.load_binding_map(CS, learn_dir=learn)
    assert bi is not None and why.startswith("ok")
    bi, why = FX.load_binding_map("CRYPTO_SHORT", learn_dir=learn)
    assert bi is None and "CAT_SIDE_MISMATCH" in why
    bi, why = FX.load_binding_map("STOCKS_LONG", learn_dir=learn)
    assert bi is None and "CONTRACT_MISMATCH" in why
    bi, why = FX.load_binding_map("STOCKS_SHORT", learn_dir=tmp_path / "nope")
    assert bi is None and why.startswith("absent")
    bf = FX.binding_features(None, "ENTRY_BREAKOUT_CHANNEL", "DC_BREAK_FILTER_TF", "D", "EMA_9_21_FILTER_FILTER_TF=OFF", {"X": 1})
    assert bf["bind_verdict"] == "UNKNOWN" and bf["stage_switch"] == "UNKNOWN" and bf["masters_armed"] is None and bf["upstream_gate_active"] == 0
    assert len(FX.bind_vector(bf)) == FX.BIND_DIM == len(FX.BIND_FEATURE_NAMES)


def test_verdicts_stages_and_masters(learn):
    bi, _ = FX.load_binding_map(CS, learn_dir=learn)
    tab, sw = "ENTRY_BREAKOUT_CHANNEL", "DC_BREAK_FILTER_TF"
    b = FX.binding_features(bi, tab, sw, "D", "EMA_9_21_FILTER_FILTER_TF=OFF", None)
    assert (b["bind_verdict"], b["stage_switch"], b["stage_filter"], b["same_stage"]) == ("BINDS", "ENTRY", "ENTRY", True)
    assert b["masters_armed"] is None  # running set unknown
    c = FX.binding_features(bi, tab, sw, "D", "WT_BOUNCE_K=2", None)
    assert c["bind_verdict"] == "UNKNOWN"  # conditional cell, running set unknown -> never guessed
    c = FX.binding_features(bi, tab, sw, "D", "WT_BOUNCE_K=2", {})
    assert c["bind_verdict"] == "CONDITIONAL_OFF" and c["masters_armed"] == 0.0  # map default WT_BOUNCE_ENABLED=False
    c = FX.binding_features(bi, tab, sw, "D", "WT_BOUNCE_K=2", {"WT_BOUNCE_ENABLED": True})
    assert c["bind_verdict"] == "CONDITIONAL_ARMED" and c["masters_armed"] == 1.0
    m = FX.binding_features(bi, tab, sw, "D", "MASK_WITH_MASTER=4h", {"MASK_MASTER_ENABLED": False})
    assert m["bind_verdict"] == "CONDITIONAL_OFF"  # a BINDS cell whose master the running set turned off
    r = FX.binding_features(bi, tab, sw, "D", "RSI_FILTER_TF=4h", {})
    assert r["bind_verdict"] == "INDIRECT" and r["stage_filter"] == "EXIT_VEL" and not r["same_stage"]
    x = FX.binding_features(bi, "EXIT_VELOCITY", "EXIT_VELOCITY_WT_MIN_TFS", "2.0", "RSI_FILTER_TF=4h", {})
    assert x["bind_verdict"] == "BINDS" and x["same_stage"]  # cand spelling 2.0 == 2 (canon)
    d = FX.binding_features(bi, tab, "DEAD_SW", "1", "EMA_9_21_FILTER_FILTER_TF=OFF", {})
    assert d["bind_verdict"] == "NEVER" and d["dead_row"]


def test_upstream_gates_and_tab_digest_are_attributes_not_ids(learn):
    bi, _ = FX.load_binding_map(CS, learn_dir=learn)
    assert "TR_ADX4H_GATE_ENABLED" in bi.gates and "ADX_REGIME_FILTER_ENABLED" in bi.gates
    rs = {"ADX_REGIME_FILTER_ENABLED": True, "TR_ADX4H_GATE_ENABLED": "True", "COUNTER_TREND_ADD_BLOCK_ENABLED": False, "EMA_9_21_FILTER_FILTER_TF": "4h",
          "MASK_WITH_MASTER": "OFF", "UNRELATED": 5}
    b = FX.binding_features(bi, "ENTRY_BREAKOUT_CHANNEL", "DC_BREAK_FILTER_TF", "D", "EMA_9_21_FILTER_FILTER_TF=OFF", rs)
    assert b["upstream_gate_active"] == 2
    assert dict(b["tab_digest"]) == {"ENTRY_TF_MASK|htf|on": 1, "ENTRY_TF_MASK|off|off": 1}
    strs = FX.binding_feature_strings(b)
    assert all("=" not in f for f, _x in strs) and ("B.bv:BINDS", 1.0) in strs and ("B.gates", 1.0) in strs
    v = FX.bind_vector(b)
    assert len(v) == FX.BIND_DIM and v[FX.BIND_FEATURE_NAMES.index("upstream_gate_active")] == 1.0


def _ev(ts, sym, sheet, row, sw, cand, label, delta, cum, fp, valid=True):
    return {"ts": ts, "sym_side": sym, "nav": "sequential", "sheet": sheet, "row": row, "switch": sw, "cand": cand, "label": label, "gain_pct": cum + delta,
            "trades": 30, "tim": 50.0, "max_dd_pct": 2.0, "pool_sharpe": 0.3, "wr_pct": 60.0, "valid": valid, "invalid_reason": "", "cum_before": cum,
            "delta": delta, "secs": 0.5, "cached": False, "err": "", "fp": fp}


def _board(sym, day):
    t = lambda m: f"2026-10-{day:02d}T01:{m:02d}:00Z"  # noqa: E731
    L = [{"ts": t(0), "sym_side": sym, "label": "BOARD_CTX", "bh": 3.0, "tier": "MEDIOCRE", "round": f"r{day}"}]
    L.append(_ev(t(1), sym, "ENTRY_BREAKOUT_CHANNEL", 6, "DC_BREAK_FILTER_TF", "D", "naked", 0.5, 10.0, "nk"))
    L.append(_ev(t(1), sym, "ENTRY_BREAKOUT_CHANNEL", 6, "DC_BREAK_FILTER_TF", "D", "EMA_9_21_FILTER_FILTER_TF=OFF", 1.2, 10.0, "c1"))
    L.append(_ev(t(1), sym, "ENTRY_BREAKOUT_CHANNEL", 6, "DC_BREAK_FILTER_TF", "D", "WT_BOUNCE_K=2", 0.5, 10.0, "nk"))  # noop by fingerprint
    L.append(_ev(t(1), sym, "ENTRY_BREAKOUT_CHANNEL", 6, "DC_BREAK_FILTER_TF", "D", "RSI_FILTER_TF=4h", 0.1, 10.0, "c3"))
    L.append({"ts": t(1), "sym_side": sym, "sheet": "ENTRY_BREAKOUT_CHANNEL", "row": 6, "switch": "DC_BREAK_FILTER_TF", "cand": "D", "label": "ROW_DONE", "promoted": True})
    return L


@pytest.fixture()
def trained(tmp_path, learn):
    logs = tmp_path / "logs"
    logs.mkdir()
    lines = [x for day in (14, 15, 16, 17) for x in _board("BTCUSDT_LONG", day)]
    (logs / "BTCUSDT_LONG_jump.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    spill = tmp_path / "spill"
    assert B.main(["extract", "--source", "logs", "--from", str(logs / "*.jsonl"), "--host", "t", "--spill-dir", str(spill)]) == 0
    out, state, rep = tmp_path / "brain", tmp_path / "state.pkl", tmp_path / "rep.json"
    assert B.main(["train", "--spill-dir", str(spill), "--cut", "2026-10-16T00:00:00Z", "--state", str(state), "--out-dir", str(out), "--report", str(rep),
                   "--template-cache", str(tmp_path / "none"), "--binding-map-dir", str(learn)]) == 0
    return {"spill": spill, "state": B.load_state(state), "out": out, "report": json.loads(rep.read_text()), "learn": learn, "state_path": state}


def test_logs_extract_marks_fingerprint_noops(trained):
    rows = []
    for p in trained["spill"].glob("spill_*.tsv*"):
        import gzip
        with gzip.open(p, "rt") as fh:
            rows += [json.loads(line.split("\t", 1)[1]) for line in fh if '"e":"row"' in line]
    assert rows and all(r["nz"] == [["WT_BOUNCE_K=2", 0.5, 1]] for r in rows)
    assert all(len(r["y"]) == 3 for r in rows)  # every other field unchanged: the noop cell stays a y cell


def test_heads_m_b_labels_and_report(trained):
    sb = trained["state"]["brains"][CS]
    c = sb["c"]
    assert c["M_labels"] == 12 and c["M_pos"] == 4  # 3 cells x 4 boards; only EMA (1.2 > 0.5) beats its naked
    assert c["B_labels"] == 12 and c["B_binds"] == 8  # the fingerprint noop is the only noop
    assert c["bind_verdict_BINDS"] == 4 and c["bind_verdict_INDIRECT"] == 4
    assert c["bind_verdict_CONDITIONAL_OFF"] == 4  # master default False, never armed by the chain
    mb = sb["mb"]
    assert mb["M"]["n"] == 12 and mb["M"]["n_test"] == 6 and mb["B"]["pos"] == 8
    rp = trained["report"]["per_cat_side"][CS]
    assert rp["heads_mb"]["M"]["n"] == 12 and rp["binding_map"].startswith("ok")
    blob = json.loads((trained["out"] / f"v18_brain_{CS}.json").read_text())
    assert len(blob["heads"]["M"]) == B.NF + B.NBF and len(blob["heads"]["B"]) == B.NF + B.NBF
    assert blob["prequential_mb"]["B"]["n"] == 12 and blob["_meta"]["bind"]["names"] == list(FX.BIND_FEATURE_NAMES)


def test_decide_returns_marginal_and_binds_and_keeps_old_call(trained, monkeypatch):
    monkeypatch.setenv("V18_BINDING_MAP_DIR", str(trained["learn"]))
    pb = B.PilotBrainV18(trained["out"] / f"v18_brain_{CS}.json", "BTCUSDT_LONG")
    assert pb.bi is not None
    d = pb.decide("ENTRY_BREAKOUT_CHANNEL", "DC_BREAK_FILTER_TF=D", "WT_BOUNCE_K=2", "r1")
    assert 0.0 < d["p_marginal"] < 1.0 and 0.0 < d["p_binds"] < 1.0 and d["bind_verdict"] == "UNKNOWN"
    d2 = pb.decide("ENTRY_BREAKOUT_CHANNEL", "DC_BREAK_FILTER_TF=D", "WT_BOUNCE_K=2", "r1", running_set={"WT_BOUNCE_ENABLED": True})
    assert d2["bind_verdict"] == "CONDITIONAL_ARMED" and d2["p"] == d["p"] and d2["keep"] == d["keep"]  # M/B never change keep/p
    r = pb.decide("ENTRY_BREAKOUT_CHANNEL", "DC_BREAK_FILTER_TF=D", None, "r1")
    assert r["p_marginal"] is None and r["p_binds"] is None
    old = B.PilotBrainV18.__new__(B.PilotBrainV18)
    old.__dict__.update(pb.__dict__)
    old.w = {k: v for k, v in pb.w.items() if k not in B.BIND_HEADS}
    o = old.decide("ENTRY_BREAKOUT_CHANNEL", "DC_BREAK_FILTER_TF=D", "WT_BOUNCE_K=2", "r1")
    assert o["p_marginal"] is None and o["p"] == d["p"]


def test_explain_shows_mb(trained, capsys):
    assert B.main(["explain", "--cat-side", CS, "--switch", "DC_BREAK_FILTER_TF", "--state", str(trained["state_path"]), "--binding-map-dir", str(trained["learn"])]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "heads_mb" in out and out["heads_mb"]["prequential"]["M"]["n"] == 12
    assert all("p_marginal" in r and "p_binds" in r for r in out["heads_mb"]["pairs"])


def _report(cs, counts, linknet, hybrid):
    nev = {"n": 100, "pos": 7, "auc_count": counts, "auc_linknet": linknet, "auc_hybrid_linknet_prior_counts": hybrid}
    return {"cat_side": cs, "cut_test": "2026-10-20T00:00:00Z", "results": {"F": {"held_out_boards": {"n": 900, "pos": 30, "auc_count": 0.8, "auc_linknet": 0.79,
                                                                                                   "auc_hybrid_linknet_prior_counts": 0.81}, "never_seen_before_cut": nev}}}


def test_scorer_rule():
    assert D.choose_scorer(_report(CS, 0.741, 0.639, 0.695))["arm"] == "counts"
    assert D.choose_scorer(_report(CS, 0.787, 0.789, 0.805))["arm"] == "hybrid"
    assert D.choose_scorer(_report(CS, 0.80, 0.90, 0.895))["arm"] == "hybrid"  # within 0.01 -> hybrid for stability
    assert D.choose_scorer(_report(CS, 0.80, 0.90, 0.88))["arm"] == "linknet"
    s = D.choose_scorer(_report(CS, 0.80, 0.90, 0.88))
    assert (s["auc_never"], s["n_never"], s["n_pos_never"], s["auc_seen"]) == (0.90, 100, 7, 0.79)
    assert D.choose_scorer({"results": {"F": {"never_seen_before_cut": {"n": 3, "pos": 0, "auc_count": None}}}}) is None


def test_digest_scorer_key_and_validator(tmp_path):
    d = tmp_path / "learn"
    (d / "linknet").mkdir(parents=True)
    (d / "linknet" / f"linknet_report_{CS}.json").write_text(json.dumps(_report(CS, 0.741, 0.639, 0.695)))
    (d / "linknet" / "linknet_report_CRYPTO_SHORT.json").write_text(json.dumps(_report("STOCKS_LONG", 0.7, 0.8, 0.8)))
    sc, _p = D.linknet_scorer(CS, d)
    assert sc == {CS: D.choose_scorer(_report(CS, 0.741, 0.639, 0.695))}
    assert D.linknet_scorer("CRYPTO_SHORT", d)[0] is None  # a report of another cat_side is refused
    base = {k: None for k in D.TOP_KEYS}
    base.update({"meta": {"cat_side": CS}, "exit_loss_rank": [], "repair_menu": {}, "winners": [], "filter_veto": [], "coverage": {k: 0 for k in D.COV_KEYS},
                 "xpoll": [], "proposals": [], "proposed_rows": []})
    good = dict(base, scorer=sc, binding_audit={})
    assert not [b for b in D.validate(good) if not b.startswith("meta keys")]
    assert any("top keys" in b for b in D.validate(dict(base, other=1)))
    assert any("scorer must hold" in b for b in D.validate(dict(base, scorer={"STOCKS_LONG": sc[CS]})))


def test_progress_noop_yellows_become_b_labels(tmp_path, learn):
    import gzip
    rec = {"delta": 2.5, "promoted": True, "reason": "", "trades": 40, "cumulative_before": 5.0, "npz": "ts1791332100.0_n100", "is_running": False,
           "delta_invalid": False, "naked_delta": 0.5, "joint_delta": None, "yellow_reasons": {}, "zero_skipped_filters": [],
           "yellows": {"EMA_9_21_FILTER_FILTER_TF=OFF": 1.2, "WT_BOUNCE_K=2": 0.5, "RSI_FILTER_TF=4h": 0.1}, "noop_yellows": ["WT_BOUNCE_K=2"]}
    old = dict(rec)
    old.pop("noop_yellows")
    prog = tmp_path / "progress"
    prog.mkdir()
    (prog / "ETHUSDT_LONG_v14_progress.json").write_text(json.dumps({"symside": "ETHUSDT_LONG", "bh": 3.0, "initial_overrides": {"WT_BOUNCE_ENABLED": True},
                                                                      "done": {"ENTRY_BREAKOUT_CHANNEL!6:DC_BREAK_FILTER_TF=D": rec}}))
    (prog / "SOLUSDT_LONG_v14_progress.json").write_text(json.dumps({"symside": "SOLUSDT_LONG", "bh": 3.0, "done": {"ENTRY_BREAKOUT_CHANNEL!6:DC_BREAK_FILTER_TF=D": old}}))
    spill = tmp_path / "spill"
    assert B.main(["extract", "--source", "progress", "--from", str(prog / "*_v14_progress.json"), "--host", "t", "--spill-dir", str(spill)]) == 0
    evs = []
    for p in spill.glob("spill_*.tsv.gz"):
        with gzip.open(p, "rt") as fh:
            evs += [json.loads(line.split("\t", 1)[1]) for line in fh]
    rows = {e["s"]: e for e in evs if e["e"] == "row"}
    assert rows["ETHUSDT_LONG"]["nzk"] == 1 and rows["ETHUSDT_LONG"]["nz"] == [["WT_BOUNCE_K=2", 0.5, 1]]
    assert sorted(y[0] for y in rows["ETHUSDT_LONG"]["y"]) == ["EMA_9_21_FILTER_FILTER_TF=OFF", "RSI_FILTER_TF=4h"]  # the noop stays settled for the old heads
    assert "nzk" not in rows["SOLUSDT_LONG"] and "nz" not in rows["SOLUSDT_LONG"]  # an older progress file without the list: B never guesses
    state = tmp_path / "s.pkl"
    assert B.main(["train", "--spill-dir", str(spill), "--cut", "2026-01-01T00:00:00Z", "--state", str(state), "--template-cache", str(tmp_path / "none"),
                   "--binding-map-dir", str(learn)]) == 0
    c = B.load_state(state)["brains"][CS]["c"]
    assert c["B_labels"] == 3 and c["B_binds"] == 2 and c["M_labels"] == 3 + 3  # ETH: 2 y + 1 noop; SOL: its unlisted noop stays a y cell (3 y, no B)
    assert c["bind_verdict_CONDITIONAL_ARMED"] == 1  # the start set (initial_overrides) arms WT_BOUNCE_ENABLED on the ETH board
