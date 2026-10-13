"""USER 2026-10-13: fleet-wide endgame knowledge merge (tools/v15_endgame_knowledge_merge.py, BIBLE §69)."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import v15_endgame_knowledge_merge as M  # noqa: E402


def _rec(symside, key, ts, lift, naked, seq, dropped=(), adopted=True, rejected="", cand_lift=None):
    return {"symside": symside, "key": key, "ts": ts, "lift": lift, "adopted": adopted, "target_met": False, "rejected": rejected,
            "cand_lift": cand_lift, "naked": [{"switch": s, "value": v, "delta": d} for s, v, d in naked],
            "reapply_seq": [{"filter": f, "step_delta": d} for f, d in seq], "dropped": list(dropped)}


def test_merge_dedupe_cat_side_and_load_ev(tmp_path):
    h1 = tmp_path / "s2" / "v15_run32" / "progress"
    h2 = tmp_path / "s5" / "v15_run31" / "progress"
    h1.mkdir(parents=True)
    h2.mkdir(parents=True)
    a = _rec("BTCUSDT_LONG", "BTCUSDT_LONG|x", "t1", 1.0, [("SW", "True", 2.0)], [("F1", 0.5)], ["F9"])
    b = _rec("ETHUSDC_LONG", "ETHUSDC_LONG|y", "t2", -1.0, [("SW", "True", -1.0)], [], adopted=False, rejected="worse lift -1.0", cand_lift=-1.0)
    c = _rec("AAPL_SHORT", "AAPL_SHORT|z", "t3", 0.0, [("SW", "False", 4.0)], [("F1", 1.5)])
    (h1 / "endgame_ledger.jsonl").write_text(json.dumps(a) + "\n" + json.dumps(b) + "\nnot json\n")
    (h2 / "endgame_ledger.jsonl").write_text(json.dumps(a) + "\n" + json.dumps(c) + "\n")  # a duplicated across copies
    out = tmp_path / "fleet.json"
    rc = M.main(["--no-default-globs", "--ledger-glob", str(tmp_path / "**" / "*.jsonl"), "--out", str(out)])
    assert rc == 0
    k = json.loads(out.read_text())
    assert k["n_records"] == 3 and k["workbooks"]["n"] == 3 and k["bad_lines"] == 1
    assert k["workbooks"]["rejected"] == 1 and k["workbooks"]["adopted"] == 2
    assert k["keys"]["SW"] == {"n": 3, "sum": 5.0, "pos": 2}
    assert k["keys"]["F9"]["dropped"] == 1
    assert set(k["by_cat_side"]) == {"CRYPTO_LONG", "STOCKS_SHORT"}
    assert k["by_cat_side"]["CRYPTO_LONG"]["workbooks"]["n"] == 2
    ev = M.load_ev("CRYPTO_LONG", path=out)
    assert ev["SW"] == (2, 0.5, 0.5) and ev["F1"] == (1, 0.5, 1.0)
    evs = M.load_ev("STOCKS_SHORT", path=out)
    assert evs["SW=False"] == (1, 4.0, 1.0)
    assert M.load_ev(None, path=out)["SW"][0] == 3
    assert M.load_ev("CRYPTO_LONG", path=tmp_path / "missing.json") == {}


def test_cat_side_matches_pilot():
    assert M.cat_side_of("1000PEPEUSDC_SHORT") == "CRYPTO_SHORT"
    assert M.cat_side_of("NKE_LONG") == "STOCKS_LONG"
