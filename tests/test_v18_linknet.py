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
                if (k == "STOP_LOSS_PCT=2" and day <= 12) or (k == "STOP_LOSS_PCT=3" and day >= 13):
                    nd = 0.0  # candidate == default in force: the engine returns the chain (identity)
                if sym == "AAAUSDT_LONG" and k == "STOP_LOSS_PCT=2" and day == 10:
                    nd = 0.7  # ... unless the chain differs from the inferred default: contradicted -> kept, in-force unknown
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
    a.defaults = {cs: dict(TIDX["defaults"]) for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")}
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
    # USER LAW: candidate == default in force is identity (no label). STOP_LOSS_PCT=2 is the template bold (10 days x 3 syms),
    # until its day-12 promotion makes STOP_LOSS_PCT=3 the in-force default (days 13-19); from then on =2 is a real alternative
    # -> identity = (3 + 7) days x 3 syms = 30 naked rows dropped, 40 kept per sym_side
    assert (d["kind"] == 0).sum() == 3 * 40 + 1 and (d["kind"] == 1).sum() == 600
    assert k["counters"]["R_identity_cand_eq_inforce"] == 29 and k["counters"]["inforce_contradicted_by_nonzero_delta"] == 1
    assert any(c.endswith("STOP_LOSS_PCT=2|INF=None|U") for c in k["set_combos"])
    combos = k["set_combos"]
    assert any(c.endswith("|INF=2|T") for c in combos) and any("STOP_LOSS_PCT=1|INF=3|P" in c for c in combos)  # in-force is an input
    # identity / phase events never appear; marginals are cell - naked (the brain's unit)
    assert set(np.unique(d["kind"]).tolist()) == {0, 1}


def test_inherent_vs_genuine_split(ds_dir):
    d, k = LN.load_ds("CRYPTO_LONG", ds_dir)
    rep = LN.inherent_report(d, k)
    r = rep["R"]
    assert r["inherent"]["n"] + r["genuine"]["n"] + r["thin"]["n"] == r["negatives"]
    assert sum(v["n"] for v in r["by_inforce_source"].values()) == r["negatives"]
    assert rep["identity_dropped"]["R_cand_eq_inforce"] == 29 and rep["identity_dropped"]["kept_as_inforce_unknown_R"] == 1


def test_relation_to_inforce_features():
    c = fctx()
    f = LN.Featurizer("CRYPTO_LONG", fctx=c)
    a = dict(f.set_feats("EXIT_STRUCTURAL|STOP_LOSS_PCT=3|INF=2|P"))
    assert a.get("S.inf:STOP_LOSS_PCT=2") == 1.0 and "S.infsrc:P" in a and "S.rinf:above" in a
    b = dict(f.set_feats("EXIT_STRUCTURAL|STOP_LOSS_PCT=1"))  # bare key = scored vs the template bold
    assert "S.infsrc:T" in b and "S.rinf:below" in b
    assert f.set_feats("EXIT_STRUCTURAL|STOP_LOSS_PCT=1")[2][0].startswith("S.inf:")  # slot 2 = in-force ID (dropped / masked like IDs)


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
    assert ("FIXED_STOP_REF_FIELD", "bb_low") in short and ("FIXED_STOP_REF_FIELD", "dc_low") not in short  # canonical names, engine mirrors
    assert ("FIXED_STOP_TIGHTEN_TRIGGER_PCT", "0.5") in fs
    tf = dict(TIDX)
    tf = dict(tf, switches=dict(tf["switches"], FIXED_STOP_REF_FIELD=["dc_low", "dc_basis", "bb_low", "bb_basis"]))
    ctx2 = FT.Context("CRYPTO_LONG", tidx=tf, bible={}, deps={}, tfsem={}, fdict={})
    fs2 = {(x["switch"], x["cand"]) for x in LN.enumerate_beyond(ctx2)[0] if x["rule"].startswith("fixed_stop")}
    assert not any(sw == "FIXED_STOP_REF_FIELD" for sw, _c in fs2)  # once the template has the rows they are not NEEDS_ROW


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
    assert z["row_identity"].sum() == 5 and z["cell_identity"].sum() >= 5 * 4  # template bold rows are identity, never proposals
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


def test_pinned_chain_value_from_precision_extract_wins():
    ds = LN.CatDS("CRYPTO_LONG", defaults=TIDX["defaults"])
    e1 = dict(row_ev("AAAUSDT_LONG", "2026-10-10T01:00:00Z", "2026-10-10T01:01:00Z", "STOP_LOSS_PCT=1", -0.5), cv="3", ck="Z")
    e2 = dict(row_ev("AAAUSDT_LONG", "2026-10-10T01:00:00Z", "2026-10-10T01:02:00Z", "STOP_LOSS_PCT=4", -0.5), cv=None, ck="NB")
    e3 = dict(row_ev("AAAUSDT_LONG", "2026-10-10T01:00:00Z", "2026-10-10T01:03:00Z", "STOP_LOSS_PCT=3", 0.0), cv="3", ck="Z")
    for e in (e1, e2, e3):
        ds.row(e, e["t"])
    keys = ds.scombo.keys
    assert "EXIT_STRUCTURAL|STOP_LOSS_PCT=1|INF=3|Z" in keys  # the start-set override pinned by the extract, not the bold "2"
    assert "EXIT_STRUCTURAL|STOP_LOSS_PCT=4|INF=None|N" in keys
    assert ds.c["R_identity_cand_eq_inforce"] == 1 and ds.c["R"] == 2  # cand == pinned chain value with delta 0 -> identity


# ------------------------------------------------------------------ E8d: v2 conditional network

def test_v2_gradients_match_finite_differences():
    import scipy.sparse as sp
    rng = np.random.default_rng(0)
    V, VX, k, H, B = 25, 9, 3, 5, 6
    net = LN.LinkNet2(V, VX, k=k, H=H, seed=1)
    for n in net.p:
        net.p[n] = rng.normal(0, 0.3, net.p[n].shape)
    net.p["E"][0] = 0
    idx = rng.integers(1, V, (B, 7))
    x = rng.random((B, 7))
    Xb = sp.csr_matrix((rng.random((B, VX)) < 0.5).astype(np.float64))
    Y = (rng.random((B, len(LN.HEADS))) > 0.5).astype(float)
    M = rng.random((B, len(LN.HEADS)))
    out, cache = net.forward_train2(idx, x, 2, 4, Xb)
    _l, g = LN.loss_grad(out, Y, M)
    G = net.backward2(g, cache, l2=0.0)
    for name in ("E", "EX", "wx", "W1", "W2"):
        P = net.p[name]
        i = tuple(int(v) for v in np.unravel_index(np.argmax(np.abs(G[name])), P.shape))
        old = P[i]
        P[i] = old + 1e-6
        lp, _ = LN.loss_grad(net.forward_train2(idx, x, 2, 4, Xb)[0], Y, M)
        P[i] = old - 1e-6
        lm, _ = LN.loss_grad(net.forward_train2(idx, x, 2, 4, Xb)[0], Y, M)
        P[i] = old
        assert abs((lp - lm) / 2e-6 - G[name][i]) < 1e-5 * max(1.0, abs(G[name][i])), name
    # context changes the prediction (switch x context interaction is live)
    s = net.predict_tables2((idx, x), (idx[:, :0], x[:, :0]), (idx[:, :0], x[:, :0]), net.xpool(Xb))
    s0 = net.predict_tables2((idx, x), (idx[:, :0], x[:, :0]), (idx[:, :0], x[:, :0]), net.xpool(sp.csr_matrix((B, VX))))
    assert not np.allclose(s[:, 0], s0[:, 0])


CTXJ_FEATS = [("X.f:DC_BREAK_FILTER_TF=4h",), ("X.s:WT_EXIT_ENABLED=False", "X.f:EMA_9_21_FILTER_ENABLED=False")]


def _ctxj():
    # AAA joins chain 0 (one active filter); BBB joins chain 1 from day 15 on (a NEW active-filter set), chain 0 before; CCC never joins
    t15 = LN.VB.epoch_of("2026-10-15T00:00:00Z")
    return {"ctxs": list(CTXJ_FEATS), "natmap": {},
            "cbmap": {("AAAUSDT_LONG", 0.0): [(0, 50, 0.0, 9e12)],
                      ("BBBUSDT_LONG", 0.0): [(0, 20, 0.0, t15 - 1), (1, 20, t15, 9e12)]}}


@pytest.fixture()
def ds2_dir(tmp_path):
    import pickle
    spill = tmp_path / "spill"
    write_spill(spill, synth_events())
    out = tmp_path / "ds2"
    out.mkdir()
    for cs in ("CRYPTO_LONG",):
        with gzip.open(out / f"ctxjoin_{cs}.pkl.gz", "wb") as fh:
            pickle.dump(_ctxj(), fh)

    class A:
        pass
    a = A()
    a.spill_dir, a.tmp_dir, a.out_dir, a.keep_sorted, a.ctx_dir = [str(spill)], str(tmp_path / "tmp"), str(out), False, str(out)
    a.defaults = {cs: dict(TIDX["defaults"]) for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")}
    a.encoders = {cs: FT.ContextEncoder(fctx(cs)) for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")}
    assert LN.cmd_dataset(a) == 0
    return out


def test_context_join_and_promotion_overlay(ds2_dir):
    d, k = LN.load_ds("CRYPTO_LONG", ds2_dir)
    xs = [tuple(c) for c in k["xctxs"]]
    syms = np.array([k["ctxs"][i][2] for i in d["ctx_i"]])
    assert (d["x_i"][syms == "CCCUSDT_LONG"] == -1).all()            # no chain set in the store -> no context (counted)
    assert (d["x_i"][syms == "AAAUSDT_LONG"] >= 0).all()
    assert k["counters"]["R_without_context"] > 0 and k["counters"]["R_with_context"] > 0
    # day-12 promotion STOP_LOSS_PCT=3 overlays the stored chain for the rest of that board only
    assert any("X.s:STOP_LOSS_PCT=3" in c for c in xs) and ("X.f:DC_BREAK_FILTER_TF=4h",) in xs
    # BBB's chain switches to the second set on day 15 (joined by cum_before + ts range)
    bbb = d["x_i"][(syms == "BBBUSDT_LONG") & (d["ts"] >= LN.VB.epoch_of("2026-10-15T00:00:00Z"))]
    assert all("X.s:WT_EXIT_ENABLED=False" in xs[i] for i in bbb)


def test_native_rows_join_by_row_key_and_stocks_native_excluded():
    ctxj = {"ctxs": [("X.f:DC_BREAK_FILTER_TF=4h",)], "cbmap": {}, "natmap": {12345: 0}}
    ds = LN.CatDS("CRYPTO_LONG", defaults=TIDX["defaults"], ctxj=ctxj, enc=FT.ContextEncoder(fctx()))
    ds.board_event({"e": "board", "t": "2026-10-13T02:00:00Z", "s": "AAAUSDT_LONG", "b": "NAT|99"})
    e = dict(row_ev("AAAUSDT_LONG", "x", "2026-10-13T02:01:00Z", "STOP_LOSS_PCT=1", -0.5), b="NAT|99", rk=12345, ck="NAT", cv="2")
    ds.row(e, e["t"])
    assert ds.ex.c["x_i"][-1] == 0 and ds.ex.c["prov"][-1] == 1
    assert ds.ex.c["b_t0"][-1] == LN.VB.epoch_of("2026-10-13T02:00:00Z")   # native board start = its board event, not the row ts
    ss = LN.CatDS("STOCKS_LONG", defaults=TIDX["defaults"], ctxj=ctxj, enc=FT.ContextEncoder(fctx("STOCKS_LONG")))
    ss.row(dict(e, s="ZZZ_LONG"), e["t"])
    assert ss.c["stocks_native_rows_excluded"] == 1 and len(ss.ex.c["kind"]) == 0


def test_never_tried_context_split(ds2_dir):
    tr = LN.Trainer2("CRYPTO_LONG", ds_dir=ds2_dir, k=4, H=8, batch=64, max_epochs=2, fctx=fctx(), log=lambda *a: None)
    D = tr.D
    rows = np.arange(len(D["kind"]))
    ref, ev = rows[D["ts"] < LN.VB.epoch_of("2026-10-15T00:00:00Z")], rows[D["ts"] >= LN.VB.epoch_of("2026-10-15T00:00:00Z")]
    sub = tr.subsets(ref, ev)

    def ck(i):
        return (int(D["set_i"][i]), int(D["filt_i"][i]) if D["kind"][i] == 1 else -9)
    seen_c = {ck(i) for i in ref}
    seen_cs = {(ck(i), int(tr.x_sig[i])) for i in ref}
    for kn, kv in (("R", 0), ("F", 1)):
        want = np.array([D["kind"][i] == kv and ck(i) in seen_c and tr.has_ctx[i] and (ck(i), int(tr.x_sig[i])) not in seen_cs for i in ev])
        assert (sub[(kn, "never_tried_context")] == want).all()
        assert not (sub[(kn, "never_tried_context")] & sub[(kn, "never_tried_pair")]).any()
    assert sub[("F", "never_tried_context")].sum() > 0  # BBB's new active-filter set from day 15


def test_train2_holdout_selects_and_scores_under_context(ds2_dir):
    rep = LN.train2_one("CRYPTO_LONG", ds2_dir, fctx=fctx(), log=lambda *a: None, status=False, k=4, H=8, batch=64, max_epochs=2)
    prod = json.loads((Path(ds2_dir) / "production_CRYPTO_LONG.json").read_text())
    assert prod["production_scorer"] in LN.SCORERS and prod["production_scorer"] == rep["selection"]["production_scorer"]
    for kn in ("R", "F"):
        for nm in ("held_out", "never_tried_pair", "never_tried_context"):
            assert all(f"auc_{s}" in rep["test"][kn][nm] for s in LN.SCORERS)
    sc = LN.Scorer2("CRYPTO_LONG", ds_dir=ds2_dir, fctx=fctx())
    sc.evidence()
    res = sc.grid([("CAT_SIDE_DEFAULTS", ()), ("LIVE_01", CTXJ_FEATS[1])])
    assert res["ctx"][0]["cells"]["p_pos"].shape == (len(res["cell_row"]), len(LN.REGIMES))
    assert res["ctx"][1]["cells"]["p_pos"].shape == (len(res["cell_row"]), 1)
    p = res["ctx"][0]["cells"]["p_pos"].astype(np.float32)
    assert np.isfinite(p).all() and (p >= 0).all() and (p <= 1).all()
    near = sc.closest_context(sc.ctx_by_set[sc.set_ix["EXIT_STRUCTURAL|STOP_LOSS_PCT=1"]], CTXJ_FEATS[1])
    assert near and near[0]["kind"] == "MEASURED closest context" and near[0]["jaccard"] == 1.0
    with pytest.raises((ValueError, FileNotFoundError)):
        LN.Scorer2("STOCKS_LONG", ds_dir=ds2_dir, fctx=fctx("STOCKS_LONG"))


def test_diversity_cap_per_row():
    rows = [0, 0, 0, 0, 0, 0, 0, 1, 1, 2]
    got = LN.diversity_cap(list(range(10)), lambda i: rows[i], cap=5)
    assert got == [0, 1, 2, 3, 4, 7, 8, 9]
    assert LN.diversity_cap(list(range(10)), lambda i: rows[i], cap=2, k=3) == [0, 1, 7]


def test_top_chains_most_common_then_typical():
    ch = {"A_LONG": ("X.f:a=1",), "B_LONG": ("X.f:a=1",), "C_LONG": ("X.f:a=1", "X.s:b=2"), "D_LONG": ("X.s:z=9",)}
    t = LN.top_chains(ch, k=3)
    assert t[0]["n_sym_sides"] == 2 and t[0]["sym_sides"] == ["A_LONG", "B_LONG"]
    assert t[1]["feats"] == ("X.f:a=1", "X.s:b=2")  # more typical than the isolated D chain
    assert [x["chain_id"] for x in t] == ["LIVE_01", "LIVE_02", "LIVE_03"]


def test_pair_with_context_ranks_untried_under_each_live_chain(tmp_path):
    class Stub:
        cs = "CRYPTO_LONG"
        prod = "hybrid_v2"

        def __init__(self):
            self.fz = type("F", (), {"fctx": fctx()})()
            self.calls = []

        def predict_items(self, items, feats, kind):
            self.calls.append((len(items), feats, kind))
            n = len(items)
            bonus = 0.5 if feats == ("X.f:DC_BREAK_FILTER_TF=4h",) else 0.0
            return {"p_pos": np.linspace(0.1, 0.2, n) + bonus, "e_delta": np.ones(n)}
    pos = {"_meta": {"cat_side": "CRYPTO_LONG"}, "sym_sides": ["AAAUSDT_LONG", "BBBUSDT_LONG", "CCCUSDT_LONG"],
           "rows": [{"key": "STOP_LOSS_PCT=3", "n": 9, "n_pos": 3, "tried_on": [0], "untried_on": [1, 2]}],
           "pairs": [{"key": "STOP_LOSS_PCT=3", "filter": "DC_BREAK_FILTER_TF=OFF", "n": 4, "n_pos": 2, "tried_on": [0, 1], "untried_on": [2]},
                     {"key": "WT_EXIT_ENABLED=False", "filter": "EMA_9_21_FILTER_ENABLED=True", "n": 3, "n_pos": 1, "tried_on": [0], "untried_on": [2]}]}
    st = Stub()
    doc = LN.pair_with_context(st, {"CCCUSDT_LONG": ("X.f:DC_BREAK_FILTER_TF=4h",)}, (), pos, tmp_path / "pwc.json.gz", log=lambda *a: None)
    assert set(doc["per_sym"]) == {"BBBUSDT_LONG", "CCCUSDT_LONG"}           # AAA tried everything -> nothing to order
    ccc = doc["per_sym"]["CCCUSDT_LONG"]
    assert ccc["context"]["name"] == "LIVE_CHAIN:CCCUSDT_LONG" and len(ccc["ranked"]) == 3
    assert [r[1] for r in ccc["ranked"]] == sorted([r[1] for r in ccc["ranked"]], reverse=True)
    assert doc["per_sym"]["BBBUSDT_LONG"]["context"]["name"] == "CAT_SIDE_DEFAULTS:BBBUSDT_LONG"   # no live set -> defaults, said so
    assert [r[0] for r in doc["per_sym"]["BBBUSDT_LONG"]["ranked"]] == [0]                        # only the row it has not tried
    assert doc["items"][1]["tab"] == "EXIT_STRUCTURAL" and "PREDICTION" in doc["meta"]["label"]
    assert gzip.open(tmp_path / "pwc.json.gz", "rt").read().startswith("{")
