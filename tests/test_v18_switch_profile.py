import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import v18_switch_profile as SP  # noqa: E402

CS_COLS = ["node", "wpos", "wn", "n", "pos", "neg", "inv", "sum_pos", "d_tim", "d_trades", "d_dd", "d_sharpe", "d_wr", "n_sym", "fwd_n", "fwd_pos",
           "live_n", "live_pos", "n_ident", "mean_secs", "live_pnl_pp"]
PAIR_COLS = ["filter", "row", "wpos", "wn", "n", "pos", "n_rescue", "n_kill", "n_syn", "syn_sum", "n_sym", "abl_n", "abl_kill"]


def make_blob(cat_side, rows, pairs, regimes=(), syms=None):
    """rows: {key: (n, pos, neg, inv, n_sym)}; pairs: {(filter_opt, row_key): (n, pos, rescue, kill, n_sym, abl_kill)}."""
    nodes, idx = [], {}

    def ni(s):
        if s not in idx:
            idx[s] = len(nodes)
            nodes.append(s)
        return idx[s]

    cs = []
    for k, (n, pos, neg, inv, n_sym) in rows.items():
        cs.append([ni("R:" + k), float(pos), float(n), n, pos, neg, inv, float(pos) * 2.0, -1.0, -2.0, 0.1, 0.0, 0.0, n_sym, 0, 0, 0, 0, 0, 0.5, 0.0])
    for fo in sorted({f for f, _ in pairs}):
        cs.append([ni("F:" + fo), 10.0, 100.0, 100, 10, 50, 0, 20.0, 0, 0, 0, 0, 0, 20, 0, 0, 0, 0, 0, 0.3, 0.0])
    pr = []
    for (fo, rk), (n, pos, resc, kill, n_sym, abl) in pairs.items():
        pr.append([ni("F:" + fo), ni("R:" + rk), float(pos), float(n), n, pos, resc, kill, 0, 0.0, n_sym, 0, abl])
    rg = [[ni("R:" + k), g, float(wp), float(wn)] for k, g, wp, wn in regimes]
    sym = {}
    for ss, lst in (syms or {}).items():
        sym[ss] = [[ni("R:" + k), float(wp), float(wn)] for k, wp, wn in lst]
    return {"_meta": {"schema": "v18_brain/1", "cat_side": cat_side, "at": "x", "last_ts": "y", "counters": {}, "cs_cols": CS_COLS, "pair_cols": PAIR_COLS},
            "nodes": nodes, "cs": cs, "pair": pr, "regime": rg, "sym": sym}


def make_tidx(yellows):
    return {"path": "t.xlsx", "md5": "m", "defaults": {"SW_A": "False", "SW_B": "off"},
            "tabs": {"ENTRY": {"rows": [[3, "SW_A", "False", True], [4, "SW_A", "True", False], [5, "SW_B", "off", True], [6, "SW_B", "1h", False], [7, "SW_C", "x", False]],
                                "yellows": yellows}}}


YEL = [f"FLT{i}=on" for i in range(40)]


def test_classify_thresholds():
    c = SP.classify
    assert c(2, 2, 0, 0, 1.0)[0] == "UNTRIED"
    assert c(20, 3, 0, 0, 0.5)[0] == "WORKS"
    assert c(20, 3, 0, 0, -0.5)[0] == "SOMETIMES"          # mean delta must be > 0 for WORKS
    assert c(20, 3, 0, 0, None)[0] == "WORKS"              # unknown mean delta: test skipped
    assert c(20, 2, 0, 0, 1.0)[0] == "SOMETIMES"
    assert c(40, 3, 0, 0, 1.0)[0] == "INERT"               # 7.5% < 15% and < 10%
    assert c(20, 0, 5, 0, None) == ("DAMAGING", "HARMFUL")
    assert c(20, 0, 0, 4, None)[0] == "DAMAGING"
    assert c(20, 0, 0, 0, None) == ("INERT", "DEAD")
    assert c(30, 1, 5, 0, None) == ("INERT", "USELESS")
    assert c(9, 0, 5, 0, None) == ("UNTRIED", "THIN")


def test_switch_verdict_order_and_damaging_share():
    assert SP.switch_verdict([("INERT", 10), ("WORKS", 5)]) == "WORKS"
    assert SP.switch_verdict([("DAMAGING", 10), ("INERT", 100)]) == "INERT"
    assert SP.switch_verdict([("DAMAGING", 60), ("INERT", 40)]) == "DAMAGING"
    assert SP.switch_verdict([("UNTRIED", 0)]) == "UNTRIED"
    assert SP.switch_verdict([]) == "UNTRIED"


def build(cat_side, rows, pairs, needed_max=24, digest=None, yel=YEL, npos=5):
    return SP.build_profile(cat_side, make_blob(cat_side, rows, pairs, syms={f"S{i}_X": [("SW_A=True", 1 if i < npos else 0, 1)] for i in range(7)}),
                            make_tidx(yel), digest, None, None, None, needed_max)


def test_row_verdicts_defaults_and_untried():
    rows = {"SW_A=True": (30, 6, 20, 0, 20), "SW_B=1h": (30, 0, 20, 0, 20)}
    prof, pilot = build("CRYPTO_LONG", rows, {})
    r = prof["rows"]
    assert r["SW_A=False"]["is_default"] and r["SW_A=False"]["verdict"] == "UNTRIED" and r["SW_A=False"]["verdict_sub"] == "DEFAULT_REFERENCE"
    assert r["SW_A=True"]["verdict"] == "WORKS" and r["SW_A=True"]["pos_syms"] == 5    # from sym rows wpos > 0 (state absent), capped at n_sym
    assert r["SW_B=1h"]["verdict"] == "DAMAGING"
    assert r["SW_C=x"]["verdict"] == "UNTRIED" and r["SW_C=x"]["n_evals"] == 0
    assert prof["switches"]["SW_A"]["verdict"] == "WORKS" and prof["switches"]["SW_B"]["verdict"] == "DAMAGING" and prof["switches"]["SW_C"]["verdict"] == "UNTRIED"
    assert set(pilot) == set(r)
    assert prof["_meta"]["totals"]["pairs_total"] == 5 * len(YEL)


def test_needed_suffocating_evidence_lists_capped_deduped_disjoint():
    rows = {"SW_A=True": (30, 6, 20, 0, 20)}
    pairs = {}
    for i in range(40):
        pairs[(f"FLT{i}=on", "SW_A=True")] = (10, 4 if i < 30 else 0, 0, 0, 5, 0)       # 30 needed-qualifying (pos 4 on 4-5 sym)
    pairs[("FLT35=on", "SW_A=True")] = (10, 0, 3, 0, 5, 0)                                 # rescue x3 -> suffocating
    pairs[("FLT36=on", "SW_A=True")] = (10, 1, 0, 0, 5, 2)                                 # abl_kill x2 + one positive eval -> suffocating and evidence
    for cap in (24, 7):
        prof, pilot = build("CRYPTO_LONG", rows, pairs, needed_max=cap)
        p = pilot["SW_A=True"]
        allf = p["needed"] + p["suffocating"] + p["evidence_pos"]
        assert len(allf) == len(set(allf)) <= cap
        assert allf == p["needed"] + p["suffocating"] + p["evidence_pos"]
        assert p["untried_count"] == 0
    prof, pilot = build("CRYPTO_LONG", rows, pairs, needed_max=40)
    p = pilot["SW_A=True"]
    assert "FLT35=on" in p["suffocating"] and "FLT36=on" in p["suffocating"]
    assert "FLT36=on" not in p["needed"] and "FLT36=on" not in p["evidence_pos"]            # disjoint: highest-priority list keeps it
    assert len(p["needed"]) == 30 and set(p["needed"]).isdisjoint(p["suffocating"])
    row = prof["rows"]["SW_A=True"]
    assert row["coverage"]["pairs_n_ge3"] == 40 and row["coverage"]["pairs_total"] == len(YEL)
    assert row["needed"][0]["pos_syms_basis"] == "proxy_min(pos,n_sym)"


def test_digest_veto_remedy_and_companions():
    rows = {"SW_A=True": (30, 6, 20, 0, 20)}
    digest = {"filter_veto": [{"switch": "SW_A", "cand": "True", "filter": "FLT3", "opt": "on", "support_syms": 6, "remedy": {"kind": "exemption", "override": {"X": 1}}}],
              "winners": [{"switch": "SW_A", "cand": "True", "companions": [{"filter": "FLT7", "opt": "on"}]}]}
    prof, pilot = build("CRYPTO_LONG", rows, {}, digest=digest)
    p = pilot["SW_A=True"]
    assert p["suffocating"] == ["FLT3=on"] and p["evidence_pos"] == ["FLT7=on"]
    assert prof["rows"]["SW_A=True"]["suffocating"][0]["remedy"]["remedy"] == "exemption"


def test_cat_side_separation():
    rows_l = {"SW_A=True": (30, 6, 20, 0, 20)}
    rows_s = {"SW_A=True": (30, 0, 25, 0, 20)}
    pl, nl = build("CRYPTO_LONG", rows_l, {("FLT1=on", "SW_A=True"): (10, 5, 0, 0, 5, 0)})
    ps, ns = build("STOCKS_SHORT", rows_s, {}, npos=0)
    assert pl["_meta"]["cat_side"] == "CRYPTO_LONG" and ps["_meta"]["cat_side"] == "STOCKS_SHORT"
    assert pl["rows"]["SW_A=True"]["verdict"] == "WORKS" and ps["rows"]["SW_A=True"]["verdict"] == "DAMAGING"
    assert nl["SW_A=True"]["needed"] == ["FLT1=on"] and ns["SW_A=True"]["needed"] == []


def test_run_refuses_foreign_brain(tmp_path):
    (tmp_path / "v18_brain_CRYPTO_LONG.json").write_text(json.dumps(make_blob("STOCKS_LONG", {}, {})))

    class A:
        brain_dir = str(tmp_path)
        learn_dir = str(tmp_path)
        out_dir = str(tmp_path)
        state = None
        needed_max = 24

    with pytest.raises(SystemExit):
        SP.run("CRYPTO_LONG", A)


def test_trim_to_size_keeps_pilot_list_untouched():
    rows = {"SW_A=True": (30, 6, 20, 0, 20)}
    pairs = {(f"FLT{i}=on", "SW_A=True"): (10, 4, 0, 0, 5, 0) for i in range(30)}
    prof, pilot = build("CRYPTO_LONG", rows, pairs)
    before = json.dumps(pilot, sort_keys=True)
    txt = SP.trim_to_size(prof, max_bytes=1000)
    assert len(prof["rows"]["SW_A=True"]["needed"]) == 0 and prof["_meta"]["trimmed_row_detail_to"] == 0
    assert json.dumps(pilot, sort_keys=True) == before
    assert json.loads(txt)["_meta"]["cat_side"] == "CRYPTO_LONG"


def test_md_renders_thresholds_first():
    prof, pilot = build("CRYPTO_LONG", {"SW_A=True": (30, 6, 20, 0, 20)}, {})
    md = SP.render_md(prof, pilot)
    assert md.index("Verdict thresholds") < md.index("## Switches") and "Not derivable" in md
