"""tests for tools/v18_linknet.py (E8b): gradients, cat_side separation, chronological split, predictions labelled, proposal
enumeration (dc_low stop -> basis/bb siblings, FIXED_STOP family), template-row status, forward label rule, digest shape."""
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import v18_features as FT  # noqa: E402
from tools import v18_linknet as LN  # noqa: E402

TIDX = {"cat_side": "CRYPTO_LONG", "path": "fixture", "md5": "0" * 32,
        "tabs": {"EXIT_STRUCTURAL": {"rows": [[3, "CHANNEL_REENTRY_STOP_FIELD", "dc_low", True], [4, "CHANNEL_REENTRY_STOP_TF", "15m", True],
                                              [5, "CHANNEL_REENTRY_STOP_TF", "1h", False], [6, "STOP_LOSS_PCT", "1", False], [7, "STOP_LOSS_PCT", "2", True],
                                              [8, "STOP_LOSS_PCT", "3", False], [9, "WT_EXIT_ENABLED", "True", True], [10, "WT_EXIT_ENABLED", "False", False],
                                              [11, "ATR_STOP_TF", "4h", True]],
                                     "yellows": ["EMA_9_21_FILTER_ENABLED=True", "EMA_9_21_FILTER_ENABLED=False", "DC_BREAK_FILTER_TF=4h", "DC_BREAK_FILTER_TF=OFF"]}},
        "switches": {"CHANNEL_REENTRY_STOP_FIELD": ["dc_low"], "CHANNEL_REENTRY_STOP_TF": ["15m", "1h"], "STOP_LOSS_PCT": ["1", "2", "3"],
                     "WT_EXIT_ENABLED": ["True", "False"], "ATR_STOP_TF": ["4h"]},
        "defaults": {"CHANNEL_REENTRY_STOP_FIELD": "dc_low", "CHANNEL_REENTRY_STOP_TF": "15m", "STOP_LOSS_PCT": "2", "WT_EXIT_ENABLED": "True", "ATR_STOP_TF": "4h"}}


def fctx(cs="CRYPTO_LONG"):
    return FT.Context(cs, tidx=dict(TIDX, cat_side=cs), bible={}, deps={}, tfsem={}, fdict={})


def row_ev(sym, board_ts, ts, key, naked_d, cells=(), promoted=False, sheet="EXIT_STRUCTURAL"):
    n = [naked_d, 1, 0.07, 0.5, 1.0, -0.2, 0.0, 1.0, 1.0, 1]
    ys = [[h, md, 1, 0.07, 0.1, 0.0, -0.1, 0.0, 0.5, 1.0, (naked_d or 0) + md] for h, md in cells]
    return {"e": "row", "t": ts, "s": sym, "b": f"{sym}|{board_ts}", "sh": sheet, "k": key, "cb": 0.0, "n": n, "y": ys, "ni": 0, "sy": [], "p": promoted,
            "ps": "rowdone", "g": "UP", "ti": "M", "bh": 3.0, "rk": abs(hash((sym, ts, key))) >> 1}


def write_spill(d, events):
    d.mkdir(parents=True, exist_ok=True)
    with gzip.open(d / "spill_logs_fx_1.tsv.gz", "wt") as fh:
        for ev in events:
            fh.write(ev["t"] + "\t" + json.dumps(ev, separators=(",", ":")) + "\n")


def synth_events():
    ev = []
    hdrs = ["EMA_9_21_FILTER_ENABLED=True", "EMA_9_21_FILTER_ENABLED=False", "DC_BREAK_FILTER_TF=4h", "DC_BREAK_FILTER_TF=OFF"]
    keys = ["STOP_LOSS_PCT=1", "STOP_LOSS_PCT=2", "STOP_LOSS_PCT=3", "WT_EXIT_ENABLED=False", "CHANNEL_REENTRY_STOP_TF=1h"]
    rng = np.random.default_rng(3)
    for sym in ("AAAUSDT_LONG", "BBBUSDT_LONG", "CCCUSDT_LONG", "ZZZ_LONG"):
        for day in range(10, 20):
            bts = f"2026-10-{day:02d}T01:00:00Z"
            for j, k in enumerate(keys):
                ts = f"2026-10-{day:02d}T01:{10 + j:02d}:00Z"
                good = k.startswith("STOP_LOSS_PCT=3")
                nd = float(rng.normal(0.4 if good else -0.2, 0.3))
                cells = [(h, float(rng.normal(0.3 if "OFF" in h else -0.1, 0.2))) for h in hdrs]
                ev.append(row_ev(sym, bts, ts, k, nd, cells, promoted=(good and day == 12)))
    return ev


@pytest.fixture()
def ds_dir(tmp_path, monkeypatch):
    spill = tmp_path / "spill"
    write_spill(spill, synth_events())
    out = tmp_path / "ds"

    class A:
        pass
    a = A()
    a.spill_dir, a.tmp_dir, a.out_dir, a.keep_sorted = [str(spill)], str(tmp_path / "tmp"), str(out), False
    assert LN.cmd_dataset(a) == 0
    return out


def test_gradients_match_finite_differences():
    rng = np.random.default_rng(0)
    V = 20
    net = LN.LinkNet(V, k=3, H=5, seed=1)
    for n in net.p:
        net.p[n] = net.p[n].astype(np.float64)
    net.p["E"] = rng.normal(0, 0.3, (V, 3))
    net.p["E"][0] = 0
    net.p["W2"] = rng.normal(0, 0.3, net.p["W2"].shape)
    idx = rng.integers(1, V, (5, 6))
    x = rng.random((5, 6))
    Y = (rng.random((5, 6)) > 0.5).astype(float)
    M = rng.random((5, 6))
    out, cache = net.forward_train(idx, x, 2, 4)
    _l, g = LN.loss_grad(out, Y, M)
    G = net.backward(g, cache, l2=0.0)
    for name in ("W1", "W2", "E"):
        P = net.p[name]
        i = tuple(int(v) for v in np.unravel_index(np.argmax(np.abs(G[name])), P.shape))
        old = P[i]
        P[i] = old + 1e-6
        lp, _ = LN.loss_grad(net.forward_train(idx, x, 2, 4)[0], Y, M)
        P[i] = old - 1e-6
        lm, _ = LN.loss_grad(net.forward_train(idx, x, 2, 4)[0], Y, M)
        P[i] = old
        assert abs((lp - lm) / 2e-6 - G[name][i]) < 1e-5 * max(1.0, abs(G[name][i]))


def test_dataset_cat_sides_strictly_apart(ds_dir):
    d, k = LN.load_ds("CRYPTO_LONG", ds_dir)
    syms = {c[2] for c in k["ctxs"]}
    assert syms == {"AAAUSDT_LONG", "BBBUSDT_LONG", "CCCUSDT_LONG"}
    d2, k2 = LN.load_ds("STOCKS_LONG", ds_dir)
    assert {c[2] for c in k2["ctxs"]} == {"ZZZ_LONG"}
    assert len(d["kind"]) == 3 * 10 * 5 * 5 and (d["kind"] == 0).sum() == 150 and (d["kind"] == 1).sum() == 600
    # identity / phase events never appear; marginals are cell - naked (the brain's unit)
    assert set(np.unique(d["kind"]).tolist()) == {0, 1}


def test_forward_label_rule(ds_dir):
    d, _k = LN.load_ds("CRYPTO_LONG", ds_dir)
    # one promotion per crypto sym_side on day 12, resolved on day 13 by the switch's other cands / own naked
    assert len(d["fwd_y"]) == 3
    assert set(np.unique(d["fwd_y"]).tolist()) <= {0, 1}
    assert (d["fwd_t_res"] > d["fwd_ts"]).all()


def test_chronological_split_and_holdout(ds_dir):
    tr = LN.Trainer("CRYPTO_LONG", ds_dir=ds_dir, k=4, H=8, batch=64, max_epochs=3, fctx=fctx(), log=lambda *a: None)
    sp, fsp = tr.splits()
    D = tr.D
    assert (D["b_t0"][sp["test"]] >= tr.cut_test).all() and (D["ts"][sp["fit"]] < tr.cut_val).all()
    assert not (sp["fit"] & sp["test"]).any() and not (sp["pre_test"] & sp["test"]).any()
    rep, ep = tr.holdout_report()
    r = rep["results"]["F"]["held_out_boards"]
    assert r["n"] > 0 and "auc_linknet" in r and "auc_count" in r
    assert "never_seen_before_cut" in rep["results"]["F"]


def test_enumeration_dc_low_stop_gives_basis_and_bb_siblings():
    out, _ref = LN.enumerate_beyond(fctx())
    got = {(x["switch"], x["cand"]) for x in out}
    assert ("CHANNEL_REENTRY_STOP_FIELD", "dc_basis") in got and ("CHANNEL_REENTRY_STOP_FIELD", "bb_basis") in got
    assert ("CHANNEL_REENTRY_STOP_FIELD", "bb_lower") in got and ("CHANNEL_REENTRY_STOP_FIELD", "dc_low") not in got  # existing row is not a proposal
    assert ("STOP_LOSS_PCT", "4") in got and ("STOP_LOSS_PCT", "0") not in got  # one grid step above; never a sign flip below a positive span
    assert ("CHANNEL_REENTRY_STOP_TF", "4h") in got  # TF neighbour from the cat_side TF universe
    fs = {(x["switch"], x["cand"]) for x in out if x["rule"].startswith("fixed_stop")}
    assert ("FIXED_STOP_REF_FIELD", "dc_basis") in fs and ("FIXED_STOP_REF_FIELD", "bb_basis") in fs and ("FIXED_STOP_REF_FIELD", "dc_low") not in fs
    assert all(x["status"] == "NEEDS_ROW" for x in out)
    short = {(x["switch"], x["cand"]) for x in LN.enumerate_beyond(fctx("CRYPTO_SHORT"))[0] if x["rule"].startswith("fixed_stop")}
    assert ("FIXED_STOP_REF_FIELD", "bb_high") in short and ("FIXED_STOP_REF_FIELD", "dc_low") not in short


def test_template_row_status():
    c = fctx()
    assert LN.template_status(c, "STOP_LOSS_PCT", "2") == "HAS_TEMPLATE_ROW"
    assert LN.template_status(c, "STOP_LOSS_PCT", "4") == "NEEDS_ROW"
    assert LN.template_status(c, "FIXED_STOP_REF_FIELD", "dc_basis") == "NEEDS_ROW"


def test_scoring_and_proposals_labelled(ds_dir, tmp_path, monkeypatch):
    tr = LN.Trainer("CRYPTO_LONG", ds_dir=ds_dir, k=4, H=8, batch=64, max_epochs=2, fctx=fctx(), log=lambda *a: None)
    net, _h, _s = tr.final(2)
    net.save(Path(ds_dir) / "linknet_model_CRYPTO_LONG.npz", {"schema": LN.SCHEMA, "cat_side": "CRYPTO_LONG", "trained_at": "x", "vocab": tr.fz.names,
                                                               "corpus_last_ts": "x", "label": LN.PRED_LABEL})
    (Path(ds_dir) / "linknet_model_STOCKS_LONG.npz").write_bytes((Path(ds_dir) / "linknet_model_CRYPTO_LONG.npz").read_bytes())
    with pytest.raises(ValueError):  # a CRYPTO_LONG model can never score a STOCKS_LONG board (principle 13)
        LN.Scorer("STOCKS_LONG", ds_dir=ds_dir, fctx=fctx("STOCKS_LONG"))
    sc = LN.Scorer("CRYPTO_LONG", ds_dir=ds_dir, fctx=fctx())
    sc.evidence()
    res = sc.score_template(tmp_path / "s.npz")
    z = np.load(tmp_path / "s.npz")
    assert bytes(z["label"]).decode() == LN.PRED_LABEL
    assert z["cell_p_pos"].shape == (len(z["cell_row"]), len(LN.REGIMES))
    assert len(z["cell_row"]) == 9 * 4  # every template cell scored
    assert int(res["n_meas"].max()) > 0  # measured pairs carry their counts
    sc.prep_neighbours(min_n=1)
    near = sc.nearest("EXIT_STRUCTURAL|FIXED_STOP_REF_FIELD=dc_basis", k=3)
    assert near and all(x["kind"] == "MEASURED" for x in near)
    # digest adapter: only own cat_side, every entry labelled PREDICTION, status in the contract set
    doc = {"meta": {"cat_side": "CRYPTO_LONG"}, "template_untried": [{"switch": "WT_EXIT_ENABLED", "cand": "False", "filter": "DC_BREAK_FILTER_TF", "opt": "4h", "tab": "EXIT_STRUCTURAL",
                                                                      "regime": "ALL", "p_pos_pred": 0.2, "e_delta_pred": 1.0, "forward_pred": 0.5, "nearest_evidence": [],
                                                                      "status": "PREDICTED_NOT_MEASURED"}],
           "needs_row": [{"switch": "FIXED_STOP_REF_FIELD", "cand": "dc_basis", "filter": None, "opt": None, "tab": "EXIT_STRUCTURAL", "regime": "ALL", "p_pos_pred": 0.1,
                          "e_delta_pred": 0.5, "forward_pred": 0.5, "nearest_evidence": near, "status": "NEEDS_ROW", "rule": "fixed_stop_family_doc17_3_7", "config_field": False}]}
    (tmp_path / "linknet_proposals_CRYPTO_LONG.json").write_text(json.dumps(doc))
    props, rows = LN.digest_proposals("CRYPTO_LONG", tmp_path)
    assert [p["status"] for p in props] == ["PREDICTED_NOT_MEASURED", "NEEDS_ROW"] and all(p["label"] == "PREDICTION" for p in props)
    assert rows[0]["switch"] == "FIXED_STOP_REF_FIELD" and rows[0]["default"] == "dc_low" and rows[0]["candidates"][0] == "dc_low" and rows[0]["needs_wiring"]
    assert LN.digest_proposals("CRYPTO_SHORT", tmp_path) == ([], [])
    (tmp_path / "linknet_proposals_CRYPTO_SHORT.json").write_text(json.dumps(doc))  # a CRYPTO_LONG file under the SHORT name is refused
    assert LN.digest_proposals("CRYPTO_SHORT", tmp_path) == ([], [])
