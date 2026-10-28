import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.opt import chart_truth
from tools.opt.hires_chart import build_table_rows, worst_summary

LABELS = [f"t{i}" for i in range(30)]
CLOSE = [100.0 + i for i in range(30)]


def _trades():
    return [
        {"bar_entry": 1, "bar_exit": 4, "entry_price": 101.0, "exit_price": 104.0, "pnl_dollars": 30.0, "pnl_pct": 2.97, "bars_held": 3},
        {"bar_entry": 5, "bar_exit": 9, "entry_price": 105.0, "exit_price": 98.0, "pnl_dollars": -14.0, "pnl_pct": -6.19, "bars_held": 4},
        {"bar_entry": 10, "bar_exit": 12, "entry_price": 110.0, "exit_price": 109.0, "pnl_dollars": -28.0, "pnl_pct": -0.70, "bars_held": 2},
    ]


def _html(rows):
    t = _trades()
    return (
        "<script>\nconst CLOSE = %s;\nconst LABELS = %s;\nconst TRADES = %s;\nconst EVENTS = [];\n</script>"
        % (json.dumps(CLOSE), json.dumps(LABELS), json.dumps(t))
        + "<table><tbody>\n" + rows + "</tbody></table>"
    )


def _rows(trades):
    return build_table_rows(trades, 1000.0, True, lambda b: LABELS[int(b)] if b is not None else "")


def test_sort_by_percent_worst_first():
    r = _rows(_trades())
    pcts = [float(x) for x in re.findall(r"([-\d.]+)%</td><td style=\"color:#[0-9a-f]+\">[-\d.]+</td>", r)]
    assert pcts[0] == pytest.approx(-6.19, abs=1e-3)
    assert "worst trade -6.19% / worst $ -28.00" == worst_summary(_trades(), True)


def test_table_matching_passes():
    chart_truth.check_table(_html(_rows(_trades())), "X_LONG", _trades(), CLOSE, True)


def test_missing_row_refused():
    rows = _rows(_trades()[:2])
    with pytest.raises(AssertionError, match="rows"):
        chart_truth.check_table(_html(rows), "X_LONG", _trades(), CLOSE, True)


def test_wrong_pnl_or_price_refused():
    t = _trades()
    t[1]["pnl_dollars"] = -15.0
    with pytest.raises(AssertionError):
        chart_truth.check_table(_html(_rows(t)), "X_LONG", _trades(), CLOSE, True)
    t = _trades()
    t[0]["exit_price"] = 104.5
    with pytest.raises(AssertionError):
        chart_truth.check_table(_html(_rows(t)), "X_LONG", _trades(), CLOSE, True)


def test_no_table_refused_when_required():
    with pytest.raises(AssertionError):
        chart_truth.check_table("const TRADES = [];\n", "X_LONG", [], CLOSE, True)


def test_repair_rebuilds_table_when_moving_closes(tmp_path):
    import argparse
    from tools import v15_chart_header_repair as rep

    t = _trades()
    hidden = {"type": "CLOSE", "bar_entry": 14, "bar_exit": 16, "entry_price": 114.0, "exit_price": 116.0, "pnl_dollars": 5.0, "pnl_pct": 1.7, "bars_held": 2}
    html = (
        '<div class="metrics">gain 1.00%% trades 3</div>'
        "<script>\nconst CLOSE = %s;\nconst LABELS = %s;\nconst TRADES = %s;\nconst EVENTS = %s;\nconst c=document.getElementById('c')</script>"
        % (json.dumps(CLOSE), json.dumps(LABELS), json.dumps(t), json.dumps([hidden]))
        + "<table><thead><tr><th>#</th><th>entry_bar</th><th>exit_bar</th></tr></thead><tbody>\n" + _rows(t) + "</tbody></table>"
    )
    p = tmp_path / "X_LONG_bh1_gain1_t3_30d_matrix.html"
    p.write_text(html)
    rec = rep.repair_one(p, argparse.Namespace(force=False, apply=True), tmp_path / "bk")
    assert rec.get("changed"), rec
    out = p.read_text()
    assert len(re.findall(r"<tr><td>", out)) == 4
    chart_truth.check_table(out, "X_LONG", chart_truth.parse_js_const(out, "TRADES"), CLOSE, True)


def test_expected_line_first_and_ledger_sum_plain(tmp_path, monkeypatch):
    import hashlib

    from tools.opt import hires_chart

    ov = {"A": 1}
    md5 = hashlib.md5(json.dumps(ov, sort_keys=True).encode()).hexdigest()
    pdir = tmp_path / "data" / "reports" / "lifecycle_pilot"
    pdir.mkdir(parents=True)
    prog = {"baseline_gain": 3.5, "final_gain": 30.0, "done": {}, "cumulative_overrides": ov, "holdout_confirm": {"g_pruned": 6.25, "dropped": ["SW_A", "SW_B"]}}
    (pdir / "ZZZUSDT_LONG_v14_progress.json").write_text(json.dumps(prog))
    monkeypatch.setattr(hires_chart, "ROOT", tmp_path)
    st = {"n": 117, "gain": 70.29, "base": 308.0, "pnl": 216.48, "wins": 107, "losses": 10, "worst": -3.63, "mtm": 0.0}
    block = hires_chart._expected_block("ZZZUSDT_LONG", md5, st)
    assert block.index("EXPECTED (forward/holdout-confirmed) gain: 6.25%") < block.index("30D LEDGER: sum of all 117 trades = +70.29% of $308")
    assert "107 wins / 10 losses, worst -3.63%" in block and "fee " in block and "marked to market: $+0.00 included" in block
    assert "lift" not in block and "envelope" not in block and "SW_A, SW_B" in block
    other = hires_chart._expected_block("ZZZUSDT_LONG", "deadbeef", st)
    assert "EXPECTED (forward/holdout-confirmed) gain: n/a [UNAVAILABLE" in other and "3.50" not in other
    assert chart_truth.METRICS_OPEN not in block


def test_stock_ledger_line_and_xlsx_rows(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from tools.opt import hires_chart

    st = {"n": 29, "gain": 13.89, "base": 3996.0, "pnl": 554.88, "wins": 19, "losses": 10, "worst": -1.83, "mtm": 0.0}
    parts = hires_chart._expected_parts("TSLA_LONG", "deadbeef", st, 30)
    assert "fee 0 (Tradier)" in parts["ledger_line"] and "20 RTH sessions" in parts["ledger_line"] and parts["est_txt"] == "n/a"
    x = tmp_path / "b.xlsx"
    wb = openpyxl.Workbook()
    wb.active.title = "TSLA_LONG_BASELINE_METRICS"
    for i in range(12):
        wb.create_sheet(f"S{i}")
    wb.save(x)
    assert hires_chart.write_xlsx_honest_rows(x, parts)
    ws = openpyxl.load_workbook(x)["TSLA_LONG_BASELINE_METRICS"]
    keys = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(1, ws.max_row + 1)}
    assert keys["EXPECTED_GAIN"] == "n/a" and keys["LEDGER_SUM"].startswith("30D LEDGER")
