"""tools/v15_census_evidence: baseline-mode, class rules, aggregation on synthetic rows (no repo data)."""

import json

from tools import v15_census_evidence as CE


def _row(ss, lever, trades, delta, cum, tim=50.0, dd=5.0, tab="ENTRY_X"):
    return {
        "symside": ss,
        "cat": CE.cat_side_of(ss),
        "tab": tab,
        "lever": lever,
        "trades": trades,
        "tim": tim,
        "dd": dd,
        "delta": delta,
        "cum": cum,
    }


def test_baseline_mode_from_zero_delta_rows():
    rows = [
        _row("AAUSDT_LONG", "A=1", 100, 0.0, 1.0),
        _row("AAUSDT_LONG", "B=2", 100, 0.0, 1.0),
        _row("AAUSDT_LONG", "C=3", 120, 1.5, 1.0),
        _row("AAUSDT_LONG", "D=4", 90, 0.5, 2.0),
    ]
    hit, miss = CE.attach_baselines(rows)
    assert (hit, miss) == (3, 1)
    assert rows[2]["b_trades"] == 100


def test_classify_branches():
    assert CE.classify([5, 6, 7, 8]) == "ADDS_TRADES"
    assert CE.classify([-5, -6, -7, -8]) == "REMOVES_TRADES"
    assert CE.classify([5, 6, -7, -8]) == "MIXED"
    assert CE.classify([0, 0, 0, 0]) == "NEUTRAL"
    assert CE.classify([5]) == "NEUTRAL"


def test_aggregate_counts_and_ranking():
    rows = []
    for i in range(4):
        ss = f"S{i}USDT_LONG"
        rows += [
            _row(ss, "ADD=1", 100, 0.0, 1.0),
            _row(ss, "ADD=1", 100, 0.0, 1.0),
            _row(ss, "ADD=1", 130, 0.5, 1.0),
        ]
    CE.attach_baselines(rows)
    agg = CE.aggregate(rows)
    assert len(agg) == 1
    a = agg[0]
    assert (a["n_evals"], a["n_symsides"], a["class_trades"]) == (12, 4, "ADDS_TRADES")
    assert a["symsides_trades_up"] == 4 and a["median_dtrades_nonzero"] == 30.0
    top = CE.top_tables(agg, "ADDS_TRADES")
    assert len(top) == 1 and CE.top_tables(agg, "REMOVES_TRADES") == []


def test_parse_and_cat():
    assert CE.parse_done_key("TAB!3:SW=V") == ("TAB", "SW=V")
    assert CE.cat_side_of("AGLDUSDT_SHORT") == "CRYPTO_SHORT"
    assert CE.cat_side_of("AAPL_LONG") == "STOCKS_LONG"


def test_collect_naked_reads_both_schemas(tmp_path):
    d = {
        "symside": "AAUSDT_LONG",
        "done": {
            "T!3:FLAT=1": {"naked_delta": 0.0, "trades": 50, "cumulative_before": 1.0},
            "T!4:SKIP=1": {"delta": None, "trades": None, "reason": "NOT_WIRED"},
            "T!5:OLD=1": {
                "delta": 0.5,
                "cumulative_before": 1.0,
                "vec": {"trades": 60, "tim_pct": 40.0, "max_dd_pct": 2.0},
            },
        },
    }
    (tmp_path / "AAUSDT_LONG_v14_progress.json").write_text(json.dumps(d))
    rows, n = CE.collect_naked(str(tmp_path))
    assert n == 1 and len(rows) == 2
    flat = [r for r in rows if r["lever"] == "FLAT=1"][0]
    assert (flat["trades"], flat["delta"], flat["tim"]) == (50, 0.0, None)
    old = [r for r in rows if r["lever"] == "OLD=1"][0]
    assert (old["trades"], old["tim"], old["dd"]) == (60, 40.0, 2.0)


def test_fleet_table_from_tmp(tmp_path):
    cell = tmp_path / "cell"
    cell.mkdir()
    (cell / "XUSDT_LONG_bhm1_gain2p_30d_matrix_manifest.json").write_text(
        json.dumps(
            {
                "symside": "XUSDT_LONG",
                "publish_class": "QUALIFIED",
                "host": "s1",
                "published_utc": "t",
                "template": "T",
                "npz_name": "N",
                "final_name": "F.xlsx",
                "metrics": {
                    "gain_pct": 5.0,
                    "trades": 40,
                    "tim_pct": 50.0,
                    "max_dd_pct": 3.0,
                    "valid": True,
                    "bh": 1.0,
                },
                "counts": {"done_n": 10, "F": 1, "C": 2, "E": 3},
            }
        )
    )
    rows = CE.fleet_table(str(cell), str(tmp_path))
    assert (
        len(rows) == 1
        and rows[0]["gain_minus_bh"] == 4.0
        and rows[0]["cat_side"] == "CRYPTO_LONG"
    )
