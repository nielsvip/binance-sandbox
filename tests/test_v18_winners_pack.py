"""tests for tools/v18_winners_pack.py + the evidence miner it reads (V18 L1, doc 17 §3.5 / §5 ABL-0)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import v18_evidence_mine as M  # noqa: E402
from tools import v18_learn_common as C  # noqa: E402
from tools import v18_winners_pack as W  # noqa: E402


def _tidx(extra=None):
    sw = {"A_ENABLED": ["True", "False"], "B_TF": ["15m", "1h", "4h"], "SIM_ACCOUNT": ["trb", "trc"], "ABLATION_DISABLE_FILTER_ENTRY": ["False", "True"],
          "EXIT_VELOCITY_WT_MIN_TFS": ["1", "2", "3"]}
    sw.update(extra or {})
    return {"switches": sw, "defaults": {k: v[0] for k, v in sw.items()}, "tabs": {}}


def _naked(n_syms, valid, pos, sum_pos):
    return [n_syms, valid, pos, 0, sum_pos, 0, n_syms * 3, n_syms, "2026-10-19T00:00:00Z", n_syms + 1]


def test_forward_unknown_is_half():
    assert C.forward_factor(0, 0) == 0.5
    assert C.forward_factor(4, 3) == 0.75


def test_pack_scoring_and_exclusions():
    ev = {"naked": {"A_ENABLED=False": _naked(10, 10, 4, 8.0),        # p0 from eligible keys only
                    "B_TF=1h": _naked(20, 20, 2, 2.0),
                    "B_TF=4h": _naked(4, 4, 4, 40.0),                 # n_syms < 5: never in the pack
                    "SIM_ACCOUNT=trc": _naked(30, 30, 30, 90.0),      # GLOBAL_ONLY
                    "ABLATION_DISABLE_FILTER_ENTRY=True": _naked(30, 30, 30, 90.0),
                    "EXIT_VELOCITY_WT_MIN_TFS=abc": _naked(30, 30, 30, 90.0),  # not a template candidate / type refused
                    "NOT_IN_TEMPLATE=1": _naked(30, 30, 30, 90.0)},
          "pairs": {"A_ENABLED=False|F1=OFF": [8, 8, 3, 3.0, 3, 1.5, 8, 8, 8], "A_ENABLED=False|F2=True": [8, 8, 2, 2.0, 2, 0.5, 8, 8, 8],
                    "A_ENABLED=False|F3=1h": [8, 8, 1, 1.0, 1, 0.1, 8, 8, 8], "A_ENABLED=False|F4=D": [8, 8, 1, 1.0, 1, 0.05, 8, 8, 8]}}
    winners, pairs, stats = W.build_pack("CRYPTO_LONG", ev, _tidx(), {}, top=60, min_syms=5)
    keys = [(w["switch"], w["cand"]) for w in winners]
    assert keys == [("A_ENABLED", "False"), ("B_TF", "1h")]
    p0 = (4 + 2 + 4) / (10 + 20 + 4)
    a = winners[0]
    assert abs(a["p_pos"] - (4 + C.M_SHRINK * p0) / (10 + C.M_SHRINK)) < 1e-6
    assert abs(a["e_delta_pos"] - 2.0) < 1e-9 and a["forward_factor"] == 0.5
    assert abs(a["score"] - round(a["p_pos"] * 2.0 * 0.5, 6)) < 1e-6
    assert [c["filter"] for c in a["companions"]] == ["F1", "F2", "F3"]  # top 3 by support
    assert a["n_boards"] == 11 and a["n_syms"] == 10
    assert stats["refused"]["GLOBAL_ONLY"] == 1 and stats["refused"]["ABLATION_lever"] == 1
    assert stats["refused"]["switch_not_in_template"] == 1 and stats["refused"]["cand_not_in_template"] == 1
    assert all(w["score"] >= winners[i + 1]["score"] for i, w in enumerate(winners[:-1]))


def test_forward_evidence_changes_factor():
    ev = {"naked": {"A_ENABLED=False": _naked(10, 10, 4, 8.0)}, "pairs": {}}
    w, _p, _s = W.build_pack("CRYPTO_LONG", ev, _tidx(), {"A_ENABLED=False": (4, 1)}, min_syms=5)
    assert w[0]["forward_factor"] == 0.25


def test_xpoll_other_cat_sides_only_and_template_keys():
    tid = {cs: _tidx() for cs in C.CAT_SIDES}
    tid["STOCKS_LONG"] = _tidx({"ONLY_STOCKS": ["1", "2"]})
    packs = {"CRYPTO_LONG": [{"switch": "A_ENABLED", "cand": "False", "score": 1.0}],
             "CRYPTO_SHORT": [{"switch": "B_TF", "cand": "1h", "score": 0.9}, {"switch": "A_ENABLED", "cand": "False", "score": 0.8}],
             "STOCKS_LONG": [{"switch": "ONLY_STOCKS", "cand": "2", "score": 5.0}] + [{"switch": "B_TF", "cand": c, "score": 0.1} for c in ("15m", "4h")],
             "STOCKS_SHORT": []}
    x = W.xpoll_for("CRYPTO_LONG", packs, tid, k=5)
    assert all(e["from_cat_side"] != "CRYPTO_LONG" for e in x)
    assert ("A_ENABLED", "False") not in {(e["switch"], e["cand"]) for e in x}  # already in the own pack
    assert "ONLY_STOCKS" not in {e["switch"] for e in x}  # not a template row of CRYPTO_LONG
    assert len(x) <= 5 and {e["label"] for e in x} <= {f"XPOLL:{cs}" for cs in C.CAT_SIDES}


def _line(**k):
    base = {"ts": "2026-10-19T01:00:00Z", "sym_side": "AAAUSDT_LONG", "valid": True, "trades": 20, "cached": False}
    base.update(k)
    return json.dumps(base) + "\n"


def test_miner_rows_abl_quartet_and_since(tmp_path):
    p = tmp_path / "AAAUSDT_LONG_jump.jsonl"
    L = [_line(label="BOARD_CTX", tier="M", bh=1.0, baseline_gain=10.0, round="r-eng1"),
         _line(ts="2026-10-01T00:00:00Z", label="naked", sheet="T", row=2, switch="OLD", cand="1", delta=5.0, cum_before=10.0),  # before since
         # row 3: inert switch (naked reproduces the chain) -> its yellow F=OFF is the chain+F reference Y
         _line(label="naked", sheet="T", row=3, switch="INERT", cand="1", delta=0.0, cum_before=10.0, gain_pct=10.0),
         _line(label="F=OFF", sheet="T", row=3, switch="INERT", cand="1", delta=-1.0, cum_before=10.0, cached=True),
         _line(label="ROW_DONE", sheet="T", row=3, switch="INERT", cand="1", promoted=False),
         # row 4: S naked -0.5, S+F=OFF +2.0 -> d_off = 2.0 - (-1.0) = 3.0 > d_on -> F blocks S
         _line(label="naked", sheet="T", row=4, switch="S", cand="True", delta=-0.5, cum_before=10.0),
         _line(label="F=OFF", sheet="T", row=4, switch="S", cand="True", delta=2.0, cum_before=10.0),
         _line(label="ROW_DONE", sheet="T", row=4, switch="S", cand="True", promoted=True),
         # ablation: strip 5.0, discover S2 +2.0, restore1 F -> 4.0, attr S2+F -> 4.5 (eff 0.5 < 1.0 = 0.5*2.0: killer)
         _line(label="ABL_STRIP:entry", sheet="V17_ABL", gain_pct=5.0, delta=-5.0, cum_before=10.0),
         _line(label="ABL_DISCOVER:entry:S2=1", sheet="T", row=9, switch="S2", cand="1", gain_pct=7.0, delta=2.0, cum_before=5.0),
         _line(label="ABL_RESTORE1:entry:G", sheet="V17_ABL", gain_pct=4.0, delta=-1.0, cum_before=5.0),
         _line(label="ABL_ATTR:entry:S2=1+G", sheet="V17_ABL", gain_pct=4.5, delta=-0.5, cum_before=5.0),
         _line(label="ABL_PROXY:entry:S2=1-G", sheet="T", row=9, switch="S2", cand="1", gain_pct=10.3, delta=0.3, cum_before=10.0)]
    p.write_text("".join(L))
    from collections import Counter
    cnt = Counter()
    obs, abl, disc, fwd, boards, meta = M.mine_file(str(p), "t", "2026-10-06T00:00:00Z", cnt)
    assert "N|OLD=1" not in obs and cnt["before_since"] == 1
    assert obs["N|S=True"][1] == -0.5 and obs["N|S=True"][4] == 1  # delta, promoted
    pair = obs["P|S=True|F=OFF"]
    assert pair[1] == 2.0 and abs(pair[2] - 2.5) < 1e-9  # d vs chain, marginal vs naked
    assert obs["P|INERT=1|F=OFF"][6] == 0  # cached record kept, counted as not fresh
    q = {(r[1], r[2]): r for r in meta["quart"]}
    assert abs(q[("S=True", "F")][6] - 3.0) < 1e-9 and q[("S=True", "F")][5] == -0.5
    a = {(r[1], r[2]): r for r in abl}
    assert a[("S2=1", "G")][8] == 1 and abs(a[("S2=1", "G")][7] - 0.5) < 1e-9  # killer, eff
    assert a[("S2=1", "G")][11] == 0.3 and a[("S2=1", "G")][12] == 1  # proxy delta, proxy covered this filter
    assert len(boards) == 1 and boards[0][3] == "AAAUSDT_LONG"
    assert M.cat_side_of("AAAUSDT_LONG") == "CRYPTO_LONG" and M.cat_side_of("AAA_SHORT") == "STOCKS_SHORT" and M.cat_side_of("AAA") is None
