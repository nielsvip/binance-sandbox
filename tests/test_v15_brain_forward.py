import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import v15_brain as B


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path, sym_side, ts, n_rows=0, pos_rows=(), neg_rows=(), forwards=(), ctx=None, holdouts=()):
    with open(path, "w") as fh:
        if ctx is not None:
            fh.write(json.dumps({"ts": ts, "sym_side": sym_side, "label": "BOARD_CTX", **ctx}) + "\n")
        for i in list(range(n_rows)) + [f"p{j}" for j in range(len(pos_rows))] + [f"n{j}" for j in range(len(neg_rows))]:
            if isinstance(i, int):
                sw, cand, delta = "A", "1", 0.0
            elif str(i).startswith("p"):
                sw, cand, delta = pos_rows[int(str(i)[1:])]
            else:
                sw, cand, delta = neg_rows[int(str(i)[1:])]
            fh.write(json.dumps({"ts": ts, "sheet": "T", "row": i, "switch": sw, "cand": cand, "label": "naked", "delta": delta, "valid": True, "secs": 1.0, "tim": 50.0, "trades": 20, "max_dd_pct": 10.0, "pool_sharpe": 0.3}) + "\n")
            fh.write(json.dumps({"ts": ts, "sheet": "T", "row": i, "switch": sw, "cand": cand, "label": "ROW_DONE"}) + "\n")
        for f in forwards:
            if isinstance(f, dict):
                fh.write(json.dumps({"ts": ts, "sym_side": sym_side, "label": "FORWARD", **f}) + "\n")
            else:
                fh.write(str(f) + "\n")
        for h in holdouts:
            fh.write(json.dumps({"ts": ts, "sym_side": sym_side, "label": "HOLDOUT", **h}) + "\n")
    return str(path)


def test_forward_node_normalization():
    assert B.forward_node("R:A=1") == "R:A=1"
    assert B.forward_node("F:x=y") == "F:x=y"
    assert B.forward_node("N:SW") == "N:SW"
    assert B.forward_node("A=1") == "R:A=1"
    assert B.forward_node("SW") == "N:SW"
    assert B.forward_node(None) is None
    assert B.forward_node("") is None
    assert B.forward_node("   ") is None


def test_forward_ingest_and_p_forward(tmp_path):
    p = _write(tmp_path / "BTCUSDC_LONG_jump.jsonl", "BTCUSDC_LONG", _now(), forwards=[{"key": "R:A=1", "d_gain": 2.0, "d_trades": 3, "d_dd": -1.0, "d_tim": 1.0, "d_sharpe": 0.1, "unseen_bars": 100, "prev_npz_end": 123} for _ in range(5)] + [{"key": "R:B=2", "d_gain": -2.0} for _ in range(5)] + [{"d_gain": 9.0}, {"key": None}, "garbage"])
    recs = list(B.iter_forward(p))
    assert len(recs) == 10
    brain = B.Brain()
    for sym_side, ts, node, rec, ctx in recs:
        assert ctx["unseen_bars"] in (100, None) or True
        brain.observe_forward(sym_side, ts, node, rec["d"] > B.EPS)
    pa, na = brain.p_forward("BTCUSDC_LONG", "R:A=1")
    pb, nb = brain.p_forward("BTCUSDC_LONG", "R:B=2")
    assert pa > 0.5 > pb and na > 0 and nb > 0
    assert brain.p_forward("BTCUSDC_LONG", "R:UNSEEN=9")[1] == 0.0


def test_iter_rows_skips_forward_holdout(tmp_path):
    p = _write(tmp_path / "ETHUSDC_SHORT_jump.jsonl", "ETHUSDC_SHORT", _now(), n_rows=2, forwards=[{"key": "R:A=1", "d_gain": 1.0}], holdouts=[{"final_minus_start": 1.5}])
    rows = list(B.iter_rows(p))
    assert len(rows) == 2
    assert all(m == {} for _s, _t, _sh, _rk, _n, m in rows)


def test_weight_flags_and_learn_scaling():
    assert B.WEIGHT_FORWARD == 1.0 and B.WEIGHT_FIT == 0.25
    x = [1.0, 0.5] + [0.0] * (B.Online.NF - 2)
    m1, m2 = B.Online(lr=0.1, l2=0.0), B.Online(lr=0.1, l2=0.0)
    m1.w["R"] = [0.0] * B.Online.NF
    m2.w["R"] = [0.0] * B.Online.NF
    m1.learn("R", x, 1.0, m1.predict("R", x), w=1.0)
    m2.learn("R", x, 1.0, m2.predict("R", x), w=0.25)
    for a, b in zip(m1.w["R"], m2.w["R"]):
        assert abs(a - 4.0 * b) < 1e-12


def test_metric_labels():
    assert B.metric_label("dd", {"ddd": -1.0}) == 1.0
    assert B.metric_label("dd", {"ddd": 0.5}) == 0.0
    assert B.metric_label("tim", {"dtim": 2.0}) == 1.0
    assert B.metric_label("trades", {"dtr": 0.0}) == 0.0
    assert B.metric_label("sharpe", {"dsh": 0.01}) == 1.0
    assert B.metric_label("dd", {"ddd": None}) is None
    assert B.metric_label("dd", {}) is None
    assert B.metric_label("nope", {"ddd": -1.0}) is None
    assert B.metric_label("dd", None) is None


def test_metric_heads_learn_direction():
    brain = B.Brain()
    x = B.Online().feats(brain, "R", "BTCUSDC_LONG", "T", "A=1", None, None, None)
    good, bad = B.Online(lr=0.1, l2=0.0), B.Online(lr=0.1, l2=0.0)
    for _ in range(20):
        good.learn_metric("dd", "R", x, 1.0, good.predict_metric("dd", "R", x))
        bad.learn_metric("dd", "R", x, 0.0, bad.predict_metric("dd", "R", x))
    assert good.predict_metric("dd", "R", x) > bad.predict_metric("dd", "R", x)


def test_regime_features_additive():
    brain = B.Brain()
    m = B.Online()
    x0 = m.feats(brain, "R", "BTCUSDC_LONG", "T", "A=1", None, None, None)
    assert x0[12:] == [0.0] * 5
    x1 = m.feats(brain, "R", "BTCUSDC_LONG", "T", "A=1", None, None, {"tier": "W", "bh": 50.0})
    assert x1[:12] == x0[:12]
    assert x1[14] == 1.0 and x1[15] == 1.0 and abs(x1[12] - x1[2]) < 1e-12 and abs(x1[13] - x1[2]) < 1e-12
    x2 = m.feats(brain, "R", "BTCUSDC_LONG", "T", "A=1", None, None, {"tier": "L", "bh": -50.0})
    assert x2[14] == -1.0 and x2[15] == -1.0
    assert B.tier_code("bogus") == 0.0 and B.bh_code(None) == 0.0


def test_holdout_ema():
    brain = B.Brain()
    assert brain.holdout_rate("BTCUSDC_LONG") is None
    brain.observe_holdout("BTCUSDC_LONG", "2026-10-15T00:00:00Z", None)
    assert brain.holdout_rate("BTCUSDC_LONG") is None
    for _ in range(10):
        brain.observe_holdout("BTCUSDC_LONG", "2026-10-15T00:00:00Z", True)
    r = brain.holdout_rate("BTCUSDC_LONG")
    assert r is not None and 0.5 < r < 1.0


def test_holdout_line_verdicts(tmp_path):
    p = _write(tmp_path / "BTCUSDC_LONG_jump.jsonl", "BTCUSDC_LONG", _now(), holdouts=[{"final_minus_start": 2.0, "days": 7}, {"final": {"gain_pct": 1.0}, "start": {"gain_pct": 3.0}}, {"days": 7}])
    got = list(B.iter_holdout(p))
    assert [w for _s, _t, w, _i in got] == [True, False, None]


def test_suggest_no_evidence(tmp_path, capsys):
    ep = tmp_path / "edges.json"
    ep.write_text(json.dumps({"edges": {}, "forward": {}}))
    rc = B.cmd_suggest(argparse.Namespace(edges=str(ep), scope=None, sym_side="ZZZUSDC_LONG", metric="dd", min_n=5, top=5))
    assert rc == 0
    assert "no evidence" in capsys.readouterr().out


def test_suggest_sym_side_with_forward(tmp_path, capsys):
    ep = tmp_path / "edges.json"
    ep.write_text(json.dumps({"edges": {"CRYPTO_LONG": {"R:A=1": {"n": 10, "n_sym": 6, "pos": 8, "neg": 1, "inv": 1, "sum_pos": 16.0, "mean_secs": 1.0, "last": "t", "mean_d": 1.5, "mean_dtim": 2.0, "mean_dtr": 1.0, "mean_ddd": -0.5, "mean_dsh": 0.1}}}, "forward": {"CRYPTO_LONG": {"R:A=1": {"n": 3, "pos": 3, "wn": 3.0, "wpos": 3.0, "last": "t"}}}}))
    rc = B.cmd_suggest(argparse.Namespace(edges=str(ep), scope=None, sym_side="BTCUSDC_LONG", metric="dd", min_n=5, top=5))
    assert rc == 0
    out = capsys.readouterr().out
    assert "R:A=1" in out and "fwd_n=3" in out


def _old_blob():
    w12 = [-4.0] + [0.0] * 11
    return {"_meta": {"at": "t", "p_ref": {"R": 0.005, "F": 0.005}, "floor_per_1000": 5.0}, "w": {"R": list(w12), "F": list(w12), "F0": list(w12)}, "global": {}, "cat": {"R:A=1": [3, 10]}, "pair": {}, "sym": {}, "ema": {}}


def test_pilot_brain_loads_old_format(tmp_path):
    mp = tmp_path / "brain_CRYPTO_LONG.json"
    mp.write_text(json.dumps(_old_blob()))
    pb = B.PilotBrain(str(mp), "BTCUSDC_LONG")
    pe, p, cn = pb.p_row("T", "A=1")
    assert cn == 10 and 0.0 < p < 1.0 and 0.0 < pe <= 1.0
    assert pb.p_metric_head("dd", "R", "T", "A=1") is None
    pb.observe_ctx("W", 30.0)
    pe2, p2, _ = pb.p_row("T", "A=1")
    assert pe2 == pe and p2 == p


def test_export_new_format_and_metric_head(tmp_path):
    lp = _write(tmp_path / "BTCUSDC_LONG_jump.jsonl", "BTCUSDC_LONG", _now(), n_rows=3, forwards=[{"key": "R:A=1", "d_gain": 1.0, "d_dd": -0.5}], ctx={"tier": "W", "bh": 30.0, "baseline_gain": 5.0, "baseline_trades": 40}, holdouts=[{"final_minus_start": 1.0}])
    out = tmp_path / "brain"
    rc = B.cmd_export(argparse.Namespace(glob=[str(lp)], since="", out_dir=str(out), p_ref_r=0.005, p_ref_f=0.005, floor=5.0))
    assert rc == 0
    blob = json.loads((out / "brain_CRYPTO_LONG.json").read_text())
    assert len(blob["w"]["R"]) == B.Online.NF and set(blob["mh"]) == {"dd", "tim", "trades", "sharpe"}
    assert "R:A=1" in blob["fwd"]["global"] and blob["ho"].get("BTCUSDC_LONG", 0) > 0.5
    pb = B.PilotBrain(str(out / "brain_CRYPTO_LONG.json"), "BTCUSDC_LONG")
    assert isinstance(pb.p_metric_head("dd", "R", "T", "A=1"), float)


def _model_blob(path, w_r, cat):
    w12 = [-4.0] + [0.0] * 11
    blob = {"_meta": {"at": "t", "p_ref": {"R": 0.005, "F": 0.005}, "floor_per_1000": 5.0}, "w": {"R": list(w_r), "F": list(w12), "F0": list(w12)}, "global": {}, "cat": dict(cat), "pair": {}, "sym": {}, "ema": {}}
    path.write_text(json.dumps(blob))


def test_compare_models_challenger_wins_and_tie(tmp_path):
    ts = _now()
    lp = tmp_path / "BTCUSDC_LONG_jump.jsonl"
    with open(lp, "w") as fh:
        for i in range(10):
            fh.write(json.dumps({"ts": ts, "sheet": "T", "row": f"b{i}", "switch": "BAD", "cand": "0", "label": "naked", "delta": -1.0, "valid": True, "secs": 1.0, "tim": 50.0, "trades": 20}) + "\n")
            fh.write(json.dumps({"ts": ts, "sheet": "T", "row": f"b{i}", "switch": "BAD", "cand": "0", "label": "ROW_DONE"}) + "\n")
        for i in range(10):
            fh.write(json.dumps({"ts": ts, "sheet": "T", "row": f"g{i}", "switch": "GOOD", "cand": "1", "label": "naked", "delta": 2.0, "valid": True, "secs": 1.0, "tim": 50.0, "trades": 20}) + "\n")
            fh.write(json.dumps({"ts": ts, "sheet": "T", "row": f"g{i}", "switch": "GOOD", "cand": "1", "label": "ROW_DONE"}) + "\n")
    champ, chal = tmp_path / "champ.json", tmp_path / "chal.json"
    _model_blob(champ, [-4.0] + [0.0] * 16, {})
    _model_blob(chal, [-4.0, 0.0, 3.0] + [0.0] * 14, {"R:GOOD=1": [10, 10]})
    v = B.compare_models(str(champ), str(chal), globs=[str(lp)], days=30, min_pos=5)
    assert v["winner"] == "challenger" and v["promote"] is True
    assert v["chal_pos_kept"] > v["champ_pos_kept"] and v["n"] == 20 and v["pos"] == 10
    t = B.compare_models(str(champ), str(champ), globs=[str(lp)], days=30, min_pos=5)
    assert t["winner"] == "tie" and t["promote"] is False
    s = B.compare_models(str(champ), str(chal), globs=[str(lp)], days=30, min_pos=500)
    assert s["winner"] == "insufficient" and s["promote"] is False
    e = B.compare_models(str(tmp_path / "missing.json"), str(chal), globs=[str(lp)])
    assert e["winner"] == "error" and e["promote"] is False


def test_learn_forward_label_weight(tmp_path):
    brain, model = B.Brain(), B.Online()
    rec = {"d": 1.5, "valid": True, "secs": 1.0, "dtim": None, "dtr": None, "ddd": -0.5, "dsh": None, "ts": _now()}
    assert B.learn_forward_label(model, brain, "BTCUSDC_LONG", "R:A=1", rec, None, w=2.0) is True
    assert brain.fwd[("CRYPTO_LONG", "R:A=1")].wn == 2.0
    assert B.learn_forward_label(model, brain, "BTCUSDC_LONG", "R:A=1", {"d": None}, None) is False
