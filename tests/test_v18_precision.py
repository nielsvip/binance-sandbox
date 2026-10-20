"""tests for tools/v18_precision_build.py + tools/v18_precision_extract.py (fixtures only; the last two tests read the built
files when they exist and skip otherwise)."""
import gzip
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import v18_precision_build as PB  # noqa: E402
import v18_precision_extract as PX  # noqa: E402


def _est(rows):
    """rows = [(key, sym, delta, valid)] -> estimates."""
    k = np.array([r[0] for r in rows])
    s = np.array([r[1] for r in rows])
    d = np.array([r[2] for r in rows], float)
    v = np.array([r[3] for r in rows])
    return PB.key_estimates(k, s, d, v, int(k.max()) + 1, np.zeros(len(rows)))


def test_shrinkage_pulls_small_n_toward_cat_side_and_clusters_count_once():
    rows = [(0, s, -1.0, 1) for s in range(40) for _ in range(5)]          # big key, never positive -> sets p0 low
    rows += [(1, 0, 2.0, 1)]                                               # 1/1 positive on one sym_side
    rows += [(2, 0, 2.0, 1)] * 10                                          # 10/10 positive but all on ONE sym_side
    rows += [(3, s, 2.0, 1) for s in range(10)]                            # 10/10 positive on 10 sym_sides
    e = _est(rows)
    assert e["p_mean"][1] < 0.5                                            # 1/1 is shrunk hard
    assert e["p_lo"][3] > e["p_lo"][2]                                     # ten sym_sides beat ten evals on one sym_side
    assert e["n_syms"][2] == 1 and e["pos_syms"][3] == 10


def test_interval_contains_rate_and_narrows_with_n():
    small = _est([(0, s, 1.0 if s % 5 == 0 else -1.0, 1) for s in range(10)] + [(1, 0, 0.0, 1)])
    big = _est([(0, s, 1.0 if s % 5 == 0 else -1.0, 1) for s in range(200)] + [(1, 0, 0.0, 1)])
    assert small["p_lo"][0] < 0.2 < small["p_hi"][0]
    assert (big["p_hi"][0] - big["p_lo"][0]) < (small["p_hi"][0] - small["p_lo"][0])
    lo, hi = PB.beta_ci(np.array([5.0]), np.array([20.0]))
    assert 0 < lo[0] < 0.2 < hi[0] < 1


def test_jeffreys_floor_keeps_zero_positive_upper_bound_open():
    e = _est([(0, s, -1.0, 1) for s in range(3)] + [(1, s, -1.0, 1) for s in range(300)])
    assert e["p_hi"][0] > 0.05                                             # three negative evals never prove impossibility


def test_bh_fdr_counts_untested_hypotheses():
    p = np.array([1e-6, 0.01, 0.02, 0.5])
    assert PB.bh(p, 4, 0.1).tolist() == [True, True, True, False]
    assert PB.bh(p, 600000, 0.1).tolist() == [False, False, False, False]  # m = every template pair


def test_known_case_exit_velocity_min_tfs_2_is_damaging_for_board_gain():
    """EXIT_VELOCITY_WT_MIN_TFS=2 shape on CRYPTO_LONG: 28 evals / 18 sym_sides / 5 positive (small), large negatives elsewhere."""
    rows = []
    for s in range(18):
        rows.append((0, s, 1.5 if s < 5 else -9.0, 1))
    for s in range(10):
        rows.append((0, s, -6.0, 1))
    rows += [(1, s, -0.01, 1) for s in range(400)]
    e = _est(rows)
    v, sub = PB.candidate_verdict(e, 0, False)
    assert v == "DAMAGING", (v, sub, e["p_lo"][0], e["mean_hi"][0])


def test_works_needs_mean_not_significantly_negative():
    rows = [(0, s, 3.0, 1) for s in range(12)] + [(0, s, 0.5, 1) for s in range(12, 24)]
    rows += [(1, s, -0.01, 1) for s in range(400)]
    e = _est(rows)
    assert PB.candidate_verdict(e, 0, False)[0] == "WORKS"
    rows2 = [(0, s, 0.2, 1) for s in range(8)] + [(0, s, -20.0, 1) for s in range(8, 24)] + [(1, s, -0.01, 1) for s in range(400)]
    e2 = _est(rows2)
    assert PB.candidate_verdict(e2, 0, False)[0] in ("CONTESTED", "DAMAGING")


def test_inert_needs_zero_deltas_on_enough_sym_sides():
    e = _est([(0, s, 0.0, 1) for s in range(12)] + [(1, s, -1.0, 1) for s in range(50)])
    assert PB.candidate_verdict(e, 0, False)[0] == "INERT"
    e2 = _est([(0, 0, 0.0, 1)] * 12 + [(1, s, -1.0, 1) for s in range(50)])
    assert PB.candidate_verdict(e2, 0, False)[0] != "INERT"


def test_cat_side_routing_never_mixes():
    assert PB.cat_side_of("BTCUSDC_LONG") == "CRYPTO_LONG"
    assert PB.cat_side_of("AAPL_SHORT") == "STOCKS_SHORT"
    assert PB.cat_side_of("BTCUSDC") is None


def test_split_routes_by_cat_side_and_dedupes(tmp_path, monkeypatch):
    ex = tmp_path / "extract"
    ex.mkdir()
    lines = []
    for ss, rk in (("BTCUSDC_LONG", 1), ("BTCUSDC_LONG", 1), ("AAPL_SHORT", 2), ("ETHUSDT_SHORT", 3)):
        ev = {"e": "row", "t": "2026-10-19T00:00:00Z", "s": ss, "k": "A=1", "rk": rk}
        lines.append(f"{ev['t']}\t{json.dumps(ev)}\n")
    with gzip.open(ex / "a.tsv.gz", "wt") as fh:
        fh.writelines(lines)
    monkeypatch.setattr(PB, "PREC", tmp_path)
    PB.split(None)
    got = {cs: [json.loads(l.split("\t", 1)[1])["s"] for l in gzip.open(tmp_path / f"events_{cs}.tsv.gz", "rt")] for cs in PB.CAT_SIDES}
    assert got["CRYPTO_LONG"] == ["BTCUSDC_LONG"]                          # duplicate rk dropped
    assert got["STOCKS_SHORT"] == ["AAPL_SHORT"] and got["CRYPTO_SHORT"] == ["ETHUSDT_SHORT"] and got["STOCKS_LONG"] == []


def test_extractor_resolves_default_in_force():
    out = []
    bolds = {"CRYPTO_LONG": {"SW": "OFF", "Q": "1"}}
    asm = PX.PrecisionAssembler("BTCUSDC_LONG", "f", out.append, __import__("collections").Counter(), bolds=bolds)

    def ln(i, **kw):
        base = {"ts": f"2026-10-19T00:00:{i:02d}Z", "sym_side": "BTCUSDC_LONG", "sheet": "ENTRY_REVERSAL_BOUNCE", "fn": "x", "gain_pct": 1.0,
                "trades": 20, "valid": True, "cum_before": 10.0, "secs": 0.1, "cached": False, "tim": 30, "max_dd_pct": 1, "pool_sharpe": 0.3, "wr_pct": 60}
        base.update(kw)
        return base

    seq = [ln(0, label="BOARD_CTX", bh=5.0, tier="M", round="r1"),
           ln(1, label="naked", row=3, switch="SW", cand="1h", delta=-2.0),
           ln(2, label="ROW_DONE", row=3, switch="SW", cand="1h", promoted=False),
           ln(3, label="naked", row=4, switch="SW", cand="4h", delta=1.0),
           ln(4, label="ROW_DONE", row=4, switch="SW", cand="4h", promoted=True),
           ln(5, label="naked", row=5, switch="SW", cand="D", delta=-0.5, cum_before=11.0),
           ln(6, label="ROW_DONE", row=5, switch="SW", cand="D", promoted=False),
           ln(7, label="naked", row=6, switch="Q", cand="2", delta=0.0, cum_before=11.0),
           ln(8, label="ROW_DONE", row=6, switch="Q", cand="2", promoted=False)]
    for i, r in enumerate(seq, 1):
        asm.feed(r, i)
    asm.finish()
    rows = {e["k"]: e for e in out if e.get("e") in ("row", "id")}
    assert rows["SW=1h"]["ck"] == "BI" and rows["SW=1h"]["cv"] == "OFF"      # bold had no naked line -> chain held bold
    assert rows["SW=D"]["ck"] == "P" and rows["SW=D"]["cv"] == "4h"           # promoted earlier on the board
    assert rows["Q=2"]["ck"] == "BI" and rows["Q=2"]["cv"] == "1"


def test_card_size_limit():
    s = {"default": "x", "state": "CONTESTED", "state_sub": "y" * 500, "n_evals": 1, "n_syms": 1, "best_by_regime": {g: {"cand": "c" * 40, "p_pos_ci": [0.1, 0.2], "n": 3, "thin": True} for g in PB.REGIMES},
         "needed_filters": [{"filter_opt": "F" * 60, "lift": 1.0, "interval": [0.1, 2.0], "fdr": False}] * 10, "filters_evidence": [],
         "suffocating_filters": [{"filter_opt": "G" * 60, "remedy": "reset " + "G" * 60, "remedy_delta": 1.0}] * 10,
         "forward": {"promoted": 0, "survived_start": 0, "beaten": 0}, "live": {"n": 0, "pos": 0, "pnl_pp": 0.0}, "untried_cands": [],
         "coverage": {"pairs_untried_n0": 1, "pairs_total": 2}, "damaging_cands": ["d" * 40] * 20, "eval_secs": 1.0, "exit_shift": None, "exits_cooccurrence": None}
    rows = [{"is_default": False, "n_evals": 5, "interval": [0.1, 0.2], "sum_pos_delta": 1.0, "cand": f"c{i}" * 10, "cand_verdict": "CONTESTED", "p_pos": 0.1,
             "mean_delta": 0.0, "interval_mean_delta": [0, 0], "e_delta_pos": 0.1, "n_syms": 3, "pos_syms": 2} for i in range(30)]
    card = PB.make_card("SW" * 20, s, rows)
    assert len(json.dumps(card, separators=(",", ":"))) <= PB.T["card_bytes"]


@pytest.mark.parametrize("cs", PB.CAT_SIDES)
def test_built_outputs_respect_limits_and_cat_side(cs):
    p = ROOT / "data" / "learn" / f"switch_cards_{cs}.json"
    if not p.exists():
        pytest.skip("not built")
    assert p.stat().st_size <= 10 * 1024 * 1024
    doc = json.loads(p.read_text())
    assert doc["_meta"]["cat_side"] == cs
    assert max(len(json.dumps(c, separators=(",", ":"))) for c in doc["cards"].values()) <= 2048
    md = (ROOT / "data" / "learn" / f"switch_cards_{cs}.md").read_text().splitlines()
    assert len(md) <= 300 and "ALMOST EVERY SWITCH" in md[2]
    prof = json.loads((ROOT / "data" / "learn" / f"switch_profile_{cs}.json").read_text())
    assert prof["_meta"]["cat_side"] == cs
    nf = json.loads((ROOT / "data" / "learn" / f"needed_filters_{cs}.json").read_text())
    for k, v in nf.items():
        assert len(set(v["needed"]) | set(v["suffocating"]) | set(v["evidence_pos"])) <= 24
        assert {"needed", "suffocating", "evidence_pos", "untried_count"} <= set(v)


def test_built_known_case_crypto_long():
    p = ROOT / "data" / "learn" / "switch_profile_CRYPTO_LONG.json"
    if not p.exists():
        pytest.skip("not built")
    r = json.loads(p.read_text())["rows"].get("EXIT_VELOCITY_WT_MIN_TFS=2")
    if r is None or r.get("cand_verdict") is None:
        pytest.skip("profile is not a precision build")
    assert r["cand_verdict"] == "DAMAGING" and r["interval_mean_delta"][1] < 0
