"""tests for the DAILY LEARN LOOP (BIBLE §99): tools/v17_daily_learn_prep.py (live leg on fixtures, packet cap, insights
validation) + tools/v17_combo_engine.py daily_insight consumer (load, schedule first, outcome recorded in the library).
Run: python -m pytest -q tests/test_v17_daily_learn.py"""
import datetime
import json
import random
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from tools import v17_daily_learn_prep as L  # noqa: E402
from tools import v17_combo_engine as CE  # noqa: E402

T0 = 1791900000.0  # 2026-10-13T~13:20Z, a 15m boundary is not required


def _kl(n=400, start=None):
    """synthetic 15m klines: down to a low at bar 200, rally to a top at bar 260, fade to bar 300, rally again."""
    start = start or (T0 - 220 * 900)
    rows = []
    for i in range(n):
        if i <= 200:
            c = 100.0 - 0.05 * i
        elif i <= 260:
            c = 90.0 + 0.25 * (i - 200)
        elif i <= 300:
            c = 105.0 - 0.1 * (i - 260)
        else:
            c = 101.0 + 0.1 * (i - 300)
        ts = datetime.datetime.fromtimestamp(start + i * 900, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        rows.append({"timestamp": ts, "open": c, "high": c * 1.002, "low": c * 0.998, "close": c, "volume": 1.0})
    return rows, start


def _iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat()


@pytest.fixture()
def fixture_root(tmp_path):
    rows, start = _kl()
    (tmp_path / "klines_cache").mkdir()
    (tmp_path / "klines_cache" / "XYZUSDT_15m.json").write_text(json.dumps(rows))
    h = tmp_path / "data" / "history" / "flz"
    h.mkdir(parents=True)
    b = lambda i: start + i * 900 + 30  # noqa: E731
    ev = [
        {"ts": _iso(b(204)), "type": "OPEN", "qty": 1.0, "price": 91.0, "reason": "B12 |VEC_EXACT"},
        {"ts": _iso(b(204) + 20), "type": "OPEN", "qty": 1.0, "price": 91.0, "reason": "BROKER_RECONCILED order=1"},  # echo -> dropped
        {"ts": _iso(b(205)), "type": "OPEN", "qty": 50.0, "price": 91.2, "reason": "BROKER_SYNC_REJECTED_TOTAL_VALUE_CAP_349"},  # never filled
        {"ts": _iso(b(230)), "type": "AUGMENT", "qty": 1.0, "price": 97.5, "reason": "B_DELTAENTRY |VEC_EXACT"},  # higher low add
        {"ts": _iso(b(290)), "type": "CLOSE", "qty": 2.0, "price": 102.0, "reason": "EXIT_VELOCITY_WT against-long g+2.1% |VEC_EXACT"},
    ]
    (h / "XYZUSDT_LONG.jsonl").write_text("\n".join(json.dumps(e) for e in ev) + "\n")
    return tmp_path, start


def test_live_leg_metrics_on_fixture(fixture_root):
    root, start = fixture_root
    live = L.collect_live(root=root, now=start + 330 * 900, days=7)
    assert live["n_trades"] == 1
    t = live["trades"][0]
    assert t["closed"] and t["cat_side"] == "CRYPTO_LONG" and t["entry_path"] == "B12" and t["exit_path"] == "EXIT_VELOCITY_WT"
    assert t["n_adds"] == 1 and t["n_augments"] == 1  # echo + rejected intent ignored
    assert 0.0 <= t["entry_vs_extreme_pct"] < 1.5 and t["entry_bars_from_extreme"] == 4  # 4 bars after the bar-200 low
    assert t["mfe_pct"] > 15.0 and t["giveback_pct"] > 3.0  # top at bar 260 (~105), exit 102
    assert t["exit_bars_from_extreme"] > 0  # exited after the top
    assert t["adds"][0]["hl"] is True and t["adds"][0]["fwd_pct"] > 0
    assert t["pnl_pct"] > 0 and t["hold_bars"] == 86
    agg = L.aggregate_live(live)
    assert agg["168h"]["cat_side"]["CRYPTO_LONG"]["closed"] == 1
    assert "CRYPTO_LONG|HL|AUGMENT" in agg["168h"]["adds"]


def test_path_label_normalisation():
    assert L.path_label("B12 |VEC_EXACT") == "B12"
    assert L.path_label("UNCOND_DC_LOW_15M_UNCOND_px107.5350_lvl107.8200_g0.08_qty_999999") == "UNCOND_DC_LOW_15M_UNCOND"
    assert L.path_label("CLOSE MULTI_TF_EXIT s60/55 g-1.36%") == "MULTI_TF_EXIT"
    assert L.path_label("MARGIN_FREE_FOR_men x") == "MARGIN_FREE_FOR"
    assert L.path_label("DC_BREAK_HIGH_15M") == "DC_BREAK_HIGH_15M"


def _fake_bt(n_boards=600):
    boards = []
    for i in range(n_boards):
        ss = f"SYM{i}USDT_{'LONG' if i % 2 else 'SHORT'}" if i % 3 else f"STK{i}_LONG"
        boards.append({"sym_side": ss, "host": "h", "run": "r", "done_utc": f"2026-10-13T{i % 24:02d}:00:00Z", "v17": True, "start": 1.0, "final": 5.0 + i % 7, "bh": 2.0, "trades": 50, "tier": "WINNER", "best_start": "start", "phases": {"P2_WORST_FIRST": {"lift": 3.0, "evals": 100, "wall_s": 60.0, "promotions": 2}}, "promotions": [{"phase": "V17_PROBE", "parts": [f"KEY_{i % 50}=True", f"OTHER_{i}=1h"], "delta": 1.0, "trades": 40, "tim": 30.0, "dd": 3.0}], "surgeon": [{"symptoms": {"losers": {"n": 3, "size": 2.0}}, "exits": [["FIXED_STOP", 4]], "by_symptom": {"losers": {"tried": 10, "pos": 1}}}], "regime_now": "RANGE"})
    lib = {"origins_24h": [{"source": "v17_combo_engine", "origin": f"o{i}", "w": 30, "n": 100, "pos": 5, "mean_pos_delta": 1.0} for i in range(30)], "top_combos_24h": [{"cat_side": "CRYPTO_LONG", "combo": {f"K{i}": True, f"J{i}": "4h"}, "w": 30, "n_boards": 3, "mean_delta": 1.0, "max_delta": 2.0, "tim": 20, "dd": 2, "trades": 100, "origins": "x"} for i in range(200)], "insight_outcomes": [{"insight_id": f"20261012-{i:02d}", "date": "20261012", "cat_side": "CRYPTO_LONG", "status": "evaluated", "n": 3, "boards": 3, "d30_mean": 0.1, "d30_max": 0.4, "pos_gated": 1, "d365_mean": None, "adopted": 0} for i in range(40)]}
    return {"generated_utc": "x", "hosts": {"h1": {"n_files": n_boards, "boards": boards, "library": lib}, "h2": {"n_files": 1, "boards": [], "library": dict(lib, top_combos_24h=lib["top_combos_24h"][::-1])}}, "ab": [{"report": "r", "sym_side": f"S{i}_LONG", "arm": "new", "gain": "1", "bh": "1", "trades": "1", "tim": "1", "dd": "1", "g365": "", "evals": "1"} for i in range(60)]}


def test_packet_hard_cap_30kb(fixture_root):
    root, start = fixture_root
    live = L.collect_live(root=root, now=start + 330 * 900, days=7)
    big = dict(live, trades=[dict(live["trades"][0], sym_side=f"S{i}USDT_LONG", entry_path=f"P{i % 40}", exit_path=f"X{i % 37}", regime=f"R{i % 9}", t_exit=live["now"] - 3600) for i in range(3000)])
    md, data = L.build_packet(big, _fake_bt(), "20261013")
    assert len(md.encode()) <= L.PACKET_CAP == 30 * 1024
    assert data["packet_bytes"] == len(md.encode())
    assert "## A. LIVE TRADES" in md and "## B. BACKTESTS" in md
    assert "Sharpe" not in md.replace("No Sharpe here", "")


def test_board_row_from_progress_shape():
    d = {"symside": "ZCSH_SHORT", "initial_baseline_gain": 19.5, "final_gain_fresh_vec": 39.9, "bh": 3.1, "final_trades": 20, "defaults_challenger": {"chosen": "cat_side_defaults", "start_gain": 1.2}, "tier": {"tier": "WINNER"}, "v17": {"on": True, "phases": {"P1_PROBE": {"lift": 1.7, "evals": 57, "wall_s": 4.9, "promotions": 1}, "P3_FOCUS_1": {"lift": 0.9, "evals": 10, "wall_s": 2.5}, "P3_FOCUS_2": {"lift": 0.1, "evals": 3, "wall_s": 1.0}}, "promotions": [{"phase": "V17_PROBE", "what": "V17_PROBE:MOM3_FILTER_TF=D", "delta": 1.7024, "trades": 19, "tim": 24.4, "dd": 13.6, "parts": ["MOM3_FILTER_TF=D"]}], "regime": {"regime_now": "RANGE"}}}
    r = L.board_row(d, "/home/x/v15_run32/progress/ZCSH_SHORT_v14_progress.json", "h")
    assert r["phases"]["P3_FOCUS"]["lift"] == pytest.approx(1.0) and r["phases"]["P3_FOCUS"]["evals"] == 13
    assert r["promotions"][0]["parts"] == ["MOM3_FILTER_TF=D"] and r["best_start"] == "cat_side_defaults" and r["run"] == "v15_run32"


VK = {"CRYPTO_LONG": {"keys": {"WT_DIV_ENTRY_GATE_ENABLED": {"tabs": ["ENTRY_CONFIRMATION_GATES"], "values": [True, False], "filter": True}, "MAX_AUGMENTS_PER_POSITION": {"tabs": ["AUGMENT_TREND"], "values": [0, 1, 2, 5], "filter": False}, "EXIT_VELOCITY_WT_MIN_TFS": {"tabs": ["EXIT_VELOCITY"], "values": [1, 2, 3], "filter": True}}}, "CRYPTO_SHORT": {"keys": {"WT_DIV_ENTRY_GATE_ENABLED": {"tabs": [], "values": [True, False], "filter": True}}}}


def test_validate_insights_drops_invalid_keys():
    raw = {"entry_timing_rules": ["r1", {"x": 1}], "exit_timing_rules": "single", "candidate_paths": [
        {"cat_side": "CRYPTO_LONG", "sym_side": None, "regime": None, "overrides_delta": {"WT_DIV_ENTRY_GATE_ENABLED": False, "NOT_A_REAL_KEY_XYZ": True, "MAX_AUGMENTS_PER_POSITION": 0, "EXIT_VELOCITY_WT_MIN_TFS": 7}, "why": "w", "evidence_refs": ["A.24h: x"]},
        {"cat_side": "CRYPTO_LONG", "overrides_delta": {"WT_DIV_ENTRY_GATE_ENABLED": "maybe"}},
        {"cat_side": "BOGUS", "overrides_delta": {"WT_DIV_ENTRY_GATE_ENABLED": False}},
        {"cat_side": "crypto_short", "sym_side": "ETHUSDC_LONG", "overrides_delta": {"WT_DIV_ENTRY_GATE_ENABLED": "false"}},
        {"cat_side": "CRYPTO_LONG", "overrides_delta": {"EXIT_VELOCITY_WT_MIN_TFS": 2.0, "MAX_AUGMENTS_PER_POSITION": "2"}, "regime": "range"},
    ]}
    clean, rep = L.validate_insights(raw, VK, "20261013")
    cps = clean["candidate_paths"]
    assert rep["paths_in"] == 5 and len(cps) == 3
    a = cps[0]
    assert a["overrides_delta"] == {"WT_DIV_ENTRY_GATE_ENABLED": False}
    assert set(a["dropped_keys"]) == {"NOT_A_REAL_KEY_XYZ", "MAX_AUGMENTS_PER_POSITION", "EXIT_VELOCITY_WT_MIN_TFS"}
    b = cps[1]
    assert b["cat_side"] == "CRYPTO_SHORT" and b["sym_side"] is None and b["overrides_delta"] == {"WT_DIV_ENTRY_GATE_ENABLED": False}
    c = cps[2]
    assert c["overrides_delta"]["EXIT_VELOCITY_WT_MIN_TFS"] == 2 and c["overrides_delta"]["MAX_AUGMENTS_PER_POSITION"] == 2 and c["regime"] == "RANGE"
    assert [x["insight_id"] for x in cps] == ["20261013-00", "20261013-01", "20261013-02"]
    assert clean["exit_timing_rules"] == ["single"] and clean["entry_timing_rules"][1] == '{"x": 1}'


def test_parse_llm_result():
    raw, md = L.parse_llm_result('```json\n{"candidate_paths": [], "retire": []}\n```\n---MD---\n# brief')
    assert raw == {"candidate_paths": [], "retire": []} and md == "# brief"
    assert L.parse_llm_result("no json here")[0] is None


# ------------------------------------------------------------------------------------------------- combo engine consumer
@pytest.fixture(scope="module")
def space():
    return CE.ComboSpace("CRYPTO_LONG")


def _write_insights(path, paths, date=None):
    date = date or datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    path.write_text(json.dumps({"date": date, "candidate_paths": [dict(p, insight_id=f"{date}-{i:02d}", date=date) for i, p in enumerate(paths)]}))
    return date


def test_load_daily_insights_filters(tmp_path):
    p = tmp_path / "ins.json"
    _write_insights(p, [{"cat_side": "CRYPTO_LONG", "sym_side": None, "regime": None, "overrides_delta": {"A": 1}}, {"cat_side": "CRYPTO_LONG", "sym_side": "ETHUSDC_LONG", "regime": None, "overrides_delta": {"B": 1}}, {"cat_side": "CRYPTO_LONG", "sym_side": None, "regime": "TREND_UP", "overrides_delta": {"C": 1}}, {"cat_side": "STOCKS_LONG", "overrides_delta": {"D": 1}}])
    got = CE.load_daily_insights("CRYPTO_LONG", "BTCUSDC_LONG", "RANGE", path=p)
    assert [list(x["delta"]) for x in got] == [["A"]]
    got = CE.load_daily_insights("CRYPTO_LONG", "ETHUSDC_LONG", "TREND_UP+HIGH_VOL", path=p)
    assert sorted(k for x in got for k in x["delta"]) == ["A", "B", "C"]
    _write_insights(p, [{"cat_side": "CRYPTO_LONG", "overrides_delta": {"A": 1}}], date="20200101")
    assert CE.load_daily_insights("CRYPTO_LONG", "BTCUSDC_LONG", path=p) == []


def test_generate_offers_insights_first_and_search_records_outcome(space, tmp_path, monkeypatch):
    from test_v17_combo_engine import FakeEval
    cands, _st = CE.generate(space, "AVAXUSDC_LONG", {}, {}, {}, None, random.Random(5), 3000)
    singles = [c["delta"] for c in cands if len(c["delta"]) == 1 and c["origin"] in ("symptom_path", "filter_path")]
    w_ins, w2, w3 = singles[10], singles[0], singles[1]
    p = tmp_path / "insights_latest.json"
    date = _write_insights(p, [{"cat_side": "CRYPTO_LONG", "sym_side": None, "regime": None, "overrides_delta": w_ins, "why": "test insight"}, {"cat_side": "CRYPTO_LONG", "overrides_delta": {"NOT_A_KEY_AT_ALL": True}, "why": "invalid -> refused"}])
    monkeypatch.setenv("V17_INSIGHTS_PATH", str(p))
    c2, st2 = CE.generate(space, "AVAXUSDC_LONG", {}, {}, {}, None, random.Random(5), 3000)
    order = CE.schedule(c2)
    assert order[0]["origin"] == "daily_insight" and order[0]["why"].startswith(f"daily_insight[{date}-00]")
    stat = {s["insight_id"]: s["status"] for s in st2["daily_insight"]}
    assert stat[f"{date}-00"] == "offered" and stat[f"{date}-01"] == "refused_type"
    lib = CE.ComboLibrary(tmp_path / "lib.sqlite")
    st = {}
    ranked = CE.search({"npz_prepared": {}}, "AVAXUSDC_LONG", {}, symptoms={}, budget_s=120, workers=1, stats_out=st, evaluator=FakeEval(w_ins, w2, w3), library=lib, seed=7, log=lambda *_: None, max_gen=3000)
    assert ranked and ranked[0]["kind"] == "joint"
    lib.close()
    db = sqlite3.connect(str(tmp_path / "lib.sqlite"))
    rows = db.execute("SELECT insight_id, status, d30, adopted FROM insight_outcomes ORDER BY insight_id").fetchall()
    assert (f"{date}-00", "evaluated", pytest.approx(1.0), 1) == (rows[0][0], rows[0][1], rows[0][2], rows[0][3])
    assert rows[1][:2] == (f"{date}-01", "refused_type")
    s = L.library_summary(tmp_path / "lib.sqlite", "2000-01-01T00")
    io = {x["insight_id"]: x for x in s["insight_outcomes"]}
    assert io[f"{date}-00"]["adopted"] == 1 and io[f"{date}-00"]["pos_gated"] == 1
