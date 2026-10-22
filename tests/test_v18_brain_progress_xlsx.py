"""tests for tools/v18_brain.py board-level sources (2026-10-22): --source progress (*_v14_progress.json) and --source xlsx
(published board workbooks). Event counts, identity rows never evidence, settled cells never evidence, coverage skips, cat_side
routing, row-key distinctness vs log-derived keys, and the existing trainer accepting the spills."""
import gzip
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import v18_brain as B  # noqa: E402

NPZ = 1791332100


def _rec(**kw):
    d = {"delta": 0.0, "promoted": False, "reason": "", "vec_gain": 0.0, "trades": 40, "yellows": {}, "cumulative_before": 5.0, "cumulative_after": 5.0,
         "npz": f"ts{NPZ}.0_n100", "complete": True, "is_running": False, "delta_invalid": False, "naked_delta": None, "joint_delta": None,
         "yellow_reasons": {}, "noop_yellows": [], "zero_skipped_filters": []}
    d.update(kw)
    return d


def _progress(sym, npz=NPZ, **top):
    done = {
        "ENTRY_REVERSAL_BOUNCE!3:HA_WICK_QUALITY_TF=OFF": _rec(is_running=True, reason="RUNNING_IDENTITY: candidate == running set", yellows={"RSI_FILTER_TF=4h": 0.4}),
        "EXIT_VELOCITY!4:EXIT_VELOCITY_WT_MIN_TFS=2": _rec(naked_delta=1.5, delta=2.5, promoted=True, yellows={"EMA_9_21_FILTER_FILTER_TF=OFF": 2.5, "RSI_FILTER_TF=4h": 0.5,
                                                                                                           "NOOP_X=1": 1.5, "ZS_Y=OFF": 1.5},
                                                      noop_yellows=["NOOP_X=1"], zero_skipped_filters=["ZS_Y=OFF"]),
        "EXIT_VELOCITY!5:EXIT_VELOCITY_WT_MIN_TFS=3": _rec(delta=0.9, joint_delta=0.9, yellows={"RSI_FILTER_TF=4h": 0.9}, cumulative_before=7.5),
        "ENTRY_BREAKOUT_CHANNEL!6:DC_BREAK_FILTER_TF=D": _rec(delta=-1.0, yellows={"EMA_9_21_FILTER_FILTER_TF=OFF": 0.8, "X_FILTER=1": "ZERO_TRADES"}, cumulative_before=7.5),
        "EXIT_VELOCITY!7:EXIT_VELOCITY_WT_MIN_TFS=4": _rec(naked_delta=-0.2, delta=-0.2, delta_invalid=True, cumulative_before=7.5),
    }
    d = {"symside": sym, "baseline_gain": 5.0, "bh": 3.0, "tier": "WINNER", "window_days": 30, "npz_id": {"ts_last": float(npz)} if npz else {},
         "initial_overrides": {"HA_WICK_QUALITY_TF": "OFF"}, "done": done, "run_path": f"/x/SPREADSHEETS/V15_V16_CELL_BY_CELL/{sym}_bh3_gain7_t40_30d_matrix.xlsx"}
    for r in done.values():
        if npz:
            r["npz"] = f"ts{npz}.0_n100"
        else:
            r.pop("npz")
    d.update(top)
    return d


HDR = ["Switch", "default", "override", "Family", "BASELINE", "HUSTLE_DELTA", "VECTOR_DELTA", "LIVE_DELTA", "LIVE_SHARPE", "REAL_COMPLETE", "PER_ROW_FILTERS",
       "is_default", "AVG_DELTA", "POS_SYM", "EMA_9_21_FILTER_FILTER_TF=OFF", "RSI_FILTER_TF=4h", "WHAT SWITCH", "IGNORED=1"]


def _row(sw, cand, c=None, fam=None, e=None, f=None, g=None, y1=None, y2=None):
    return [sw, cand, c, fam, e, f, g, None, None, None, None, "NO", None, 0, y1, y2, None, 9.9]


def _workbook(path, sym):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "EXIT_STRUCTURAL"
    ws.append(["04 EXIT_STRUCTURAL"])
    ws.append(HDR)
    ws.append(_row("GR_X", 15, e=5.0, g=0, y1=0.3, y2=0))  # identity: G 0, F blank -> settlement; 0.3 is a cell vs naked 0, 0 is settled
    ws.append(_row("GR_X", 7.5, f=-0.5, g=-0.9, y1=-0.9, y2=-1.2))  # naked known = G; -0.9 == naked -> settled; -1.2 a cell
    ws.append(_row("GR_X", 22.5, c="GR_X=22.5 + RSI_FILTER_TF=4h", f=0.1, g=0.6, y1=0.2, y2=0.5))  # positive yellow -> naked unknown, promoted
    ws.append(_row("GR_X", 30))  # G blank -> unevaluated
    ws.append(_row("EMA_9_21_FILTER_FILTER_TF", "OFF", fam="GENERAL", g=0.7, f=0.7))  # orange rollup
    bm = wb.create_sheet(f"{sym}_BASELINE_METRICS")
    for r in (["metric", "value"], ["gain_pct", 5.0], ["bh_pct", -3.0], ["source", "vector v12_quick_engine 2026-10-11T03:00:00Z via overrides=10"]):
        bm.append(r)
    wb.save(path)


@pytest.fixture()
def boards(tmp_path):
    prog = tmp_path / "progress"
    prog.mkdir()
    (prog / "ETHUSDT_LONG_v14_progress.json").write_text(json.dumps(_progress("ETHUSDT_LONG")))
    (prog / "AAPL_SHORT_v14_progress.json").write_text(json.dumps(_progress("AAPL_SHORT", npz=NPZ + 900)))
    (prog / "SOLUSDT_SHORT_v14_progress.json").write_text(json.dumps(_progress("SOLUSDT_SHORT", npz=None, result_start_utc="2026-10-10T00:30:00+00:00")))
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "AAPL_SHORT_jump.jsonl").write_text(json.dumps({"ts": "2026-10-07T00:20:00Z", "sym_side": "AAPL_SHORT", "label": "BOARD_CTX", "bh": 1.0,
                                                           "npz_last_bar_ts": NPZ + 900}) + "\n")
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps([["SOLUSDT_SHORT", "2026-10-10T01:15:00Z"], ["NOPE", 1]]))
    xl = tmp_path / "xlsx"
    xl.mkdir()
    _workbook(xl / "LINKUSDT_SHORT_bhm3_gain5_t40_30d_matrix.xlsx", "LINKUSDT_SHORT")
    shutil.copy(xl / "LINKUSDT_SHORT_bhm3_gain5_t40_30d_matrix.xlsx", xl / "LINKUSDT_SHORT_30d_matrix.xlsx")  # a second copy of the same board
    _workbook(xl / "ETHUSDT_LONG_bh3_gain7_t40_30d_matrix.xlsx", "ETHUSDT_LONG")  # the progress board's own workbook -> covered
    spill = tmp_path / "spill"
    assert B.main(["extract", "--source", "progress", "--from", str(prog / "*_v14_progress.json"), "--host", "t", "--spill-dir", str(spill),
                   "--covered-by", str(logs / "*.jsonl"), "--covered-by-ctx", str(ctx)]) == 0
    assert B.main(["extract", "--source", "xlsx", "--from", str(xl / "*.xlsx"), "--host", "t", "--spill-dir", str(spill),
                   "--progress-index", str(spill / "progress_index_t.json")]) == 0
    reps = {json.loads(p.read_text())["source"]: json.loads(p.read_text()) for p in spill.glob("extract_*.json")}
    evs = {}
    for p in spill.glob("spill_*.tsv.gz"):
        src = p.name.split("_")[1]
        with gzip.open(p, "rt") as fh:
            evs[src] = [json.loads(line.split("\t", 1)[1]) for line in fh]
    return {"tmp": tmp_path, "spill": spill, "reps": reps, "evs": evs, "logs": logs}


def test_progress_event_counts_and_settlements(boards):
    c = boards["reps"]["progress"]["counters"]
    assert c["CRYPTO_LONG:boards_used"] == 1
    assert c["CRYPTO_LONG:naked_events"] == 3  # rows 4, 6, 7 (7 is an invalid eval: evidence with valid 0)
    assert c["CRYPTO_LONG:cell_events"] == 4  # row 3 (identity, vs naked 0): 1; row 4: EMA + RSI; row 6: EMA
    assert c["CRYPTO_LONG:cells_settled"] == 2  # noop + zero-skipped
    assert c["CRYPTO_LONG:cells_reason_not_numeric"] == 1
    assert c["CRYPTO_LONG:identity_rows"] == 1 and c["CRYPTO_LONG:naked_settled_identity_with_cells"] == 1
    assert c["CRYPTO_LONG:naked_unknown_rows"] == 1 and c["CRYPTO_LONG:naked_unknown_cells_raw"] == 1
    assert c["CRYPTO_LONG:promotions"] == 1
    rows = [e for e in boards["evs"]["progress"] if e["e"] == "row" and e["s"] == "ETHUSDT_LONG"]
    r4 = next(e for e in rows if e["k"] == "EXIT_VELOCITY_WT_MIN_TFS=2")
    assert r4["n"][0] == 1.5 and r4["n"][-1] == 1 and r4["p"] is True
    assert sorted((y[0], y[1]) for y in r4["y"]) == [("EMA_9_21_FILTER_FILTER_TF=OFF", 1.0), ("RSI_FILTER_TF=4h", -1.0)]  # marginal = cell - naked
    r3 = next(e for e in rows if e["k"] == "HA_WICK_QUALITY_TF=OFF")
    assert r3["n"][-1] == 0 and r3["y"][0][1] == 0.4  # identity naked is a settlement, never evidence
    r7 = next(e for e in rows if e["k"] == "EXIT_VELOCITY_WT_MIN_TFS=4")
    assert r7["n"][1] == 0
    board = next(e for e in boards["evs"]["progress"] if e["e"] == "board" and e["s"] == "ETHUSDT_LONG")
    assert board["ts_source"] == "progress_npz_last_bar" and board["npz"] == NPZ and board["t"] == B.iso_of(NPZ) and board["ti"] == "WINNER"
    assert board["set0"] == {"HA_WICK_QUALITY_TF": "OFF"} and board["cs"] == "CRYPTO_LONG"


def test_progress_coverage_rules(boards):
    c = boards["reps"]["progress"]["counters"]
    assert c["STOCKS_SHORT:boards_covered_npz"] == 1  # the delta log's BOARD_CTX carries the same npz
    assert c["CRYPTO_SHORT:boards_covered_time"] == 1  # no npz: ctx point within 2 h of result_start_utc
    assert "STOCKS_SHORT:boards_used" not in c and "CRYPTO_SHORT:boards_used" not in c
    assert not any(e["s"] in ("AAPL_SHORT", "SOLUSDT_SHORT") for e in boards["evs"]["progress"])
    idx = json.loads((boards["spill"] / "progress_index_t.json").read_text())
    assert sorted(e["status"] for e in idx) == ["covered_npz", "covered_time", "used"]


def test_progress_without_coverage_uses_every_board(tmp_path, boards):
    sp = tmp_path / "s2"
    assert B.main(["extract", "--source", "progress", "--from", str(boards["tmp"] / "progress" / "*.json"), "--host", "u", "--spill-dir", str(sp)]) == 0
    c = json.loads(next(sp.glob("extract_*.json")).read_text())["counters"]
    assert c["CRYPTO_LONG:boards_used"] == 1 and c["STOCKS_SHORT:boards_used"] == 1 and c["CRYPTO_SHORT:boards_used"] == 1
    board = json.loads(next(sp.glob("progress_index_u.json")).read_text())
    sol = next(e for e in board if e["s"] == "SOLUSDT_SHORT")
    assert sol["t0"] == "2026-10-10T00:30:00Z"  # no npz -> result_start_utc


def test_xlsx_counts_coverage_and_dup_copies(boards):
    c = boards["reps"]["xlsx"]["counters"]
    assert c["CRYPTO_SHORT:boards_used"] == 1
    assert c["CRYPTO_SHORT:boards_all_rows_dup_or_empty"] == 1  # the second copy of the same board
    assert c["CRYPTO_LONG:boards_covered_progress_name"] == 1  # the progress JSON's run_path
    assert c["CRYPTO_SHORT:naked_events"] == 1  # row 4 only (row 3 identity, row 5 naked unknown)
    assert c["CRYPTO_SHORT:cell_events"] == 2  # 0.3 on the identity row, -1.2 on row 4
    assert c["CRYPTO_SHORT:cells_settled"] == 2  # 0 on the identity row, -0.9 == naked (the dropped copy's reads are not counted)
    assert c["CRYPTO_SHORT:naked_unknown_rows"] == 1 and c["CRYPTO_SHORT:promotions"] == 1
    assert c["CRYPTO_SHORT:xlsx_rows_unevaluated"] == 1 and c["CRYPTO_SHORT:xlsx_rows_general"] == 1
    assert c["CRYPTO_SHORT:rows_in_dup_or_empty_boards"] == 3
    rows = [e for e in boards["evs"]["xlsx"] if e["e"] == "row"]
    r4 = next(e for e in rows if e["k"] == "GR_X=7.5")
    assert r4["n"][0] == -0.9 and [y[:2] for y in r4["y"]] == [["RSI_FILTER_TF=4h", -0.3]]
    r5 = next(e for e in rows if e["k"] == "GR_X=22.5")
    assert r5["nu"] == 1 and r5["n"] is None and r5["y"] == [] and r5["p"] is True and len(r5["ya"]) == 2
    board = next(e for e in boards["evs"]["xlsx"] if e["e"] == "board")
    assert board["ts_source"] == "xlsx_baseline_source_ts" and board["t"] == "2026-10-11T03:00:00Z" and board["bh"] == -3.0


def test_xlsx_skipped_when_board_has_progress_npz(tmp_path, boards):
    xl = tmp_path / "x2"
    xl.mkdir()
    _workbook(xl / "AAPL_SHORT_bh1_gain2_t9_30d_matrix.xlsx", "AAPL_SHORT")
    (xl / "AAPL_SHORT_bh1_gain2_t9_30d_matrix_manifest.json").write_text(json.dumps({"npz_last_bar_ts": NPZ + 900, "cells_vintage": "2026-10-07T00:40:00Z"}))
    sp = tmp_path / "s3"
    assert B.main(["extract", "--source", "xlsx", "--from", str(xl / "*.xlsx"), "--host", "v", "--spill-dir", str(sp),
                   "--progress-index", str(boards["spill"] / "progress_index_t.json")]) == 0
    c = json.loads(next(sp.glob("extract_*.json")).read_text())["counters"]
    assert c["STOCKS_SHORT:boards_covered_progress_npz"] == 1
    sp2 = tmp_path / "s4"
    assert B.main(["extract", "--source", "xlsx", "--from", str(xl / "*.xlsx"), "--host", "v", "--spill-dir", str(sp2), "--covered-by", str(boards["logs"] / "*.jsonl")]) == 0
    c2 = json.loads(next(sp2.glob("extract_*.json")).read_text())["counters"]
    assert c2["STOCKS_SHORT:boards_covered_log_npz"] == 1


def test_cat_side_routing_and_rk_distinct_from_logs(tmp_path, boards):
    for src, evs in boards["evs"].items():
        for e in evs:
            assert B.cat_side_of(e["s"]) is not None
            if e["e"] == "board":
                assert e["cs"] == B.cat_side_of(e["s"]) and e["src"] == src
    logs = tmp_path / "l"
    logs.mkdir()
    t = B.iso_of(NPZ)
    lines = [{"ts": t, "sym_side": "ETHUSDT_LONG", "label": "BOARD_CTX", "bh": 3.0, "tier": "WINNER"},
             {"ts": t, "sym_side": "ETHUSDT_LONG", "sheet": "EXIT_VELOCITY", "row": 4, "switch": "EXIT_VELOCITY_WT_MIN_TFS", "cand": "2", "label": "naked",
              "delta": 1.5, "gain_pct": 6.5, "cum_before": 5.0, "valid": True, "cached": False},
             {"ts": t, "sym_side": "ETHUSDT_LONG", "sheet": "EXIT_VELOCITY", "row": 4, "switch": "EXIT_VELOCITY_WT_MIN_TFS", "cand": "2", "label": "ROW_DONE", "promoted": True}]
    (logs / "ETHUSDT_LONG_jump.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    sp = tmp_path / "sl"
    assert B.main(["extract", "--source", "logs", "--from", str(logs / "*.jsonl"), "--host", "t", "--spill-dir", str(sp)]) == 0
    with gzip.open(next(sp.glob("spill_*.tsv.gz")), "rt") as fh:
        log_rk = {json.loads(line.split("\t", 1)[1])["rk"] for line in fh}
    board_rk = [e["rk"] for evs in boards["evs"].values() for e in evs]
    assert len(board_rk) == len(set(board_rk))  # no collision inside the board sources
    assert not (log_rk & set(board_rk))  # same board, same row, same ts: still never the log key


def test_trainer_accepts_board_spills(boards):
    tmp = boards["tmp"]
    state = tmp / "state.pkl"
    out = tmp / "brain"
    assert B.main(["train", "--spill-dir", str(boards["spill"]), "--cut", "2026-10-01T00:00:00Z", "--state", str(state), "--out-dir", str(out),
                   "--report", str(tmp / "rep.json"), "--template-cache", str(tmp / "none")]) == 0
    st = B.load_state(state)
    cl = st["brains"]["CRYPTO_LONG"]
    V = B.get_view(cl)
    _i, E = V.row(V.sid.get("CS"), "R:EXIT_VELOCITY_WT_MIN_TFS=2")
    assert E[B.C["n"]] == 1 and E[B.C["pos"]] == 1
    _i, Ei = V.row(V.sid.get("CS"), "R:HA_WICK_QUALITY_TF=OFF")
    assert Ei[B.C["n"]] == 0 and Ei[B.C["n_ident"]] == 1  # identity row = one settlement, no evidence
    assert set(cl["scopes"][1:]) == {"ETHUSDT_LONG"}
    assert set(st["brains"]["CRYPTO_SHORT"]["scopes"][1:]) == {"LINKUSDT_SHORT"}
    assert st["brains"]["STOCKS_SHORT"]["c"].get("rows", 0) == 0  # the covered AAPL_SHORT board never reached the trainer
    assert st["meta"]["dedupe"].get("dup_row", 0) == 0
    assert cl["c"]["evidence_cells"] == 4 and st["brains"]["CRYPTO_SHORT"]["c"]["evidence_cells"] == 2
