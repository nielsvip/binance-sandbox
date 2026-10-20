"""tests for tools/v18_digest.py — the digest contract v18_digest_v1 the pilot codes against (doc 17 §3.1 / §3.6)."""
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from tools import v18_digest as D  # noqa: E402
from tools import v18_learn_common as C  # noqa: E402

CS = "STOCKS_SHORT"


def _tidx():
    return {"path": "fixture.xlsx", "md5": "0" * 32, "switches": {"S": ["1", "2"], "F": ["OFF", "1h"]}, "defaults": {"S": "1", "F": "1h"},
            "tabs": {"EXIT_VELOCITY": {"rows": [[3, "S", "1", True], [4, "S", "2", False]], "yellows": ["F=OFF", "F=1h", "G=True"]},
                     "EXIT_STRUCTURAL": {"rows": [[3, "S", "2", False]], "yellows": ["F=OFF"]}}}


@pytest.fixture()
def learn_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "template_index", lambda cs, cache=True: _tidx())
    (tmp_path / "evidence").mkdir()
    ev = {"meta": {"built_at": "2026-10-20T00:00:00Z", "since": "2026-10-06T00:00:00Z", "host": "s2", "files": 3, "n_board_ctx": 7,
                   "n_sym_sides_evidence": 5},
          "naked": {}, "pairs": {"S=1|F=OFF": [4, 4, 1, 1.0, 1, 0.5, 4, 4, 4],    # n=4, pos -> evidenced, not at floor
                                 "S=1|F=1h": [3, 3, 0, 0.0, 0, 0.0, 3, 3, 3],     # n=3, never positive -> at floor
                                 "S=2|F=OFF": [2, 2, 0, 0.0, 0, 0.0, 2, 2, 2],    # n<3
                                 "X=9|F=OFF": [9, 9, 0, 0.0, 0, 0.0, 9, 9, 9]}}    # not a template cell
    with gzip.open(tmp_path / "evidence" / f"evidence_{CS}.json.gz", "wt") as fh:
        json.dump(ev, fh)
    rank = {"meta": {"built_at": "x", "board_ledgers": 2},
            "columns": {"board": [{"family": "MULTI_TF_EXIT", "n": 5, "losers": 3, "loss_sum_pct": -2.0, "one_bar_share": 0.2, "mean_bars_held": 3.0,
                                   "giveback_sum": 0.1, "premature_sum": 0.3, "drift_into_loss_sum": -0.5, "drift_n": 1, "extra": 1}],
                        "live_hermetic": [], "stop_execution": [{"family": "FIXED_STOP", "n": 2, "losers": 2, "loss_sum_pct": -1.0, "one_bar_share": 1.0,
                                                                 "mean_bars_held": 0.0, "giveback_sum": 0.0, "premature_sum": 0.0, "drift_into_loss_sum": 0.0}]}}
    (tmp_path / f"exit_loss_rank_{CS}.json").write_text(json.dumps(rank))
    menu = {"meta": {"built_at": "x"}, "menu": {"MULTI_TF_EXIT": [{"switch": "S", "cand": 2, "companions": [{"filter": "F", "opt": "OFF", "support_syms": 1}],
                                                                   "source": "curated", "support_n": 4, "support_syms": 1, "forward_factor": 0.5,
                                                                   "learned_status": "pending_evidence", "symptom": "x"}]}}
    (tmp_path / f"exit_repair_menu_{CS}.json").write_text(json.dumps(menu))
    win = {"meta": {"built_at": "x"},
           "winners": [{"switch": "S", "cand": "2", "companions": [], "p_pos": 0.1, "e_delta_pos": 1.0, "n_boards": 6, "n_syms": 5, "forward_factor": 0.5,
                        "score": 0.05, "pos_syms": 1, "n_forward": 0},
                       {"switch": "S", "cand": "1", "companions": [{"filter": "F", "opt": "OFF", "support_syms": 1, "sum_marginal": 0.5}], "p_pos": 0.2,
                        "e_delta_pos": 2.0, "n_boards": 6, "n_syms": 5, "forward_factor": 0.5, "score": 0.2}],
           "xpoll": [{"from_cat_side": "CRYPTO_LONG", "switch": "S", "cand": "2", "score": 0.4, "label": "XPOLL:CRYPTO_LONG"}]}
    (tmp_path / f"winners_{CS}.json").write_text(json.dumps(win))
    veto = {"meta": {"built_at": "x"}, "pairs": [{"switch": "S", "cand": "2", "filter": "F", "opt": "OFF", "vetoed_in_missed_moves": None,
                                                  "vetoed_losers": None, "support_syms": 2, "remedy": {"kind": "reset", "override": {"F": "15m"}, "source": "t"},
                                                  "remedies": []}]}
    (tmp_path / f"filter_veto_{CS}.json").write_text(json.dumps(veto))
    return tmp_path


def test_digest_contract_exact(learn_dir):
    doc = D.assemble(CS, learn_dir)
    assert D.validate(doc) == []
    assert tuple(doc) == D.TOP_KEYS
    assert set(doc["meta"]) == set(D.META_KEYS) and doc["meta"]["cat_side"] == CS
    assert doc["meta"]["era_cut"] == "2026-10-17T13:00:00Z"  # stocks hermetic cut
    assert doc["meta"]["n_boards"] == 7 and doc["meta"]["n_sym_sides"] == 5
    assert [r["column"] for r in doc["exit_loss_rank"]] == ["board", "stop_execution"]
    assert set(doc["exit_loss_rank"][0]) == set(D.RANK_KEYS)  # extra ranker fields stripped
    p = doc["repair_menu"]["MULTI_TF_EXIT"][0]
    assert set(p) == set(D.MENU_KEYS) and p["source"] == "curated:pending_evidence" and p["cand"] == "2"
    assert p["companions"] == [{"filter": "F", "opt": "OFF"}]
    assert [w["cand"] for w in doc["winners"]] == ["1", "2"]  # sorted by score desc
    assert doc["filter_veto"][0]["vetoed_in_missed_moves"] is None and doc["filter_veto"][0]["remedy"] == {"kind": "reset", "override": {"F": "15m"}}
    assert doc["xpoll"] == [{"from_cat_side": "CRYPTO_LONG", "switch": "S", "cand": "2", "score": 0.4}]


def test_coverage_counts(learn_dir):
    cov = D.assemble(CS, learn_dir)["coverage"]
    # distinct template cells: S=1 x {F=OFF, F=1h, G=True}, S=2 x {F=OFF, F=1h, G=True} (S=2|F=OFF repeats on 2 tabs) = 6
    assert cov == {"pairs_total": 6, "pairs_n_lt_3": 4, "pairs_at_floor": 1, "floor_per_1000": C.FLOOR_PER_1000}


def test_validate_rejects_contract_breaks(learn_dir):
    doc = D.assemble(CS, learn_dir)
    bad = dict(doc)
    bad["extra"] = 1
    assert D.validate(bad)
    doc2 = D.assemble(CS, learn_dir)
    doc2["winners"] = list(reversed(doc2["winners"]))
    assert any("sorted" in b for b in D.validate(doc2))
    doc3 = D.assemble(CS, learn_dir)
    doc3["xpoll"] = [{"from_cat_side": CS, "switch": "S", "cand": "1", "score": 1.0}]
    assert any("own cat_side" in b for b in D.validate(doc3))
    doc4 = D.assemble(CS, learn_dir)
    doc4["winners"][0]["forward_factor"] = 1.5
    assert D.validate(doc4)


def test_cat_sides_never_share_inputs(learn_dir):
    other = D.assemble("CRYPTO_LONG", learn_dir)  # no CRYPTO_LONG files in the fixture: nothing leaks from STOCKS_SHORT
    assert other["winners"] == [] and other["repair_menu"] == {} and other["exit_loss_rank"] == []
    assert other["meta"]["era_cut"] == "2026-10-14T13:00:00Z"
