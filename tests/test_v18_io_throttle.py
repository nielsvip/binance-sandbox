"""V18 E4 (doc 17 §4.3 / §3.4): V18_IO_THROTTLE (xlsx at tab boundaries + forced points only, diagnose checkpoint on the
PROGRESS_JSON_EVERY_S throttle, no full workbook re-load for a log line) and V18_LEDGER_WRITEBACK (START / PROMOTED / FINAL
sets: one include_ledger eval each, compact rows in <progress_dir>/v15_trade_log/<SYM_SIDE>.jsonl)."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

from tools import v15_v18_planner as V  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def test_switches_default_off(monkeypatch):
    for k in ("V18_IO_THROTTLE", "V18_LEDGER_WRITEBACK"):
        monkeypatch.delenv(k, raising=False)
    assert V.io_throttle_on() is False and V.ledger_writeback_on() is False
    monkeypatch.setenv("V18_IO_THROTTLE", "1")
    monkeypatch.setenv("V18_LEDGER_WRITEBACK", "1")
    assert V.io_throttle_on() is True and V.ledger_writeback_on() is True


def test_save_allowed_only_at_tab_boundary_when_on():
    assert V.xlsx_save_allowed(False, False) is True
    assert V.xlsx_save_allowed(False, True) is True
    assert V.xlsx_save_allowed(True, False) is False
    assert V.xlsx_save_allowed(True, True) is True


def test_pilot_io_wiring_pins():
    assert "def _maybe_save(at_tab_boundary: bool = False):" in SRC
    assert "if _v18_io and not _V18P.xlsx_save_allowed(_v18_io, at_tab_boundary):\n            return" in SRC
    assert "if _v18_io and _v18_tab[0] is not None and sname != _v18_tab[0]:\n            _maybe_save(at_tab_boundary=True)" in SRC
    assert '(_flush_interval_s("V15_PROGRESS_JSON_EVERY_S", PROGRESS_JSON_EVERY_S) if _v18_io else 10.0)' in SRC
    assert 'raise RuntimeError("V18_IO_THROTTLE: no full workbook re-load for a log line")' in SRC
    # forced saves are untouched: the slice exit and the final publish still call _atomic_save directly
    assert "try: _atomic_save(wb, wb_path)  # USER 2026-10-07: slice saves the workbook" in SRC


def test_set_sha_is_canonical():
    assert V.set_sha256({"b": 1, "a": 2}) == V.set_sha256({"a": 2, "b": 1})
    assert V.set_sha256({"a": 1}) != V.set_sha256({"a": 2})


def test_plan_writeback_one_eval_per_distinct_set_and_no_rewrites():
    s1, s2 = {"A": 1}, {"A": 2}
    pend = [("START", "best_start", s1), ("PROMOTED", "r3", s2), ("PROMOTED", "r9", s2), ("FINAL", "final", s2)]
    plan, capped = V.plan_writeback(pend, set(), 40)
    assert capped == 0 and len(plan) == 2
    assert plan[1][2] == [("PROMOTED", "r3"), ("PROMOTED", "r9"), ("FINAL", "final")]
    done = {("START", V.set_sha256(s1))}
    plan2, _ = V.plan_writeback(pend, done, 40)
    assert [k for _, _, ks in plan2 for k, _ in ks] == ["PROMOTED", "PROMOTED", "FINAL"]
    plan3, capped3 = V.plan_writeback([("PROMOTED", str(i), {"A": i}) for i in range(5)], set(), 2)
    assert len(plan3) == 2 and capped3 == 3


def test_ledger_rows_skip_seeded_and_derive_bars_held():
    res = {"ledger": [{"pnl_dollars": 1.0, "bar_entry": 3, "bar_exit": 7, "exit_reason": "X", "pnl_pct": 0.5}, {"pnl_dollars": 2.0, "seeded_prewindow": True}, {"type": "AUGMENT", "bar": 4}, {"pnl_dollars": -1.0, "bar_entry": 9, "bar_exit": 9, "reason": "Y", "unrealised": True}]}
    rows = V.ledger_rows(res)
    assert len(rows) == 2
    assert rows[0][:5] == [3, 7, 4, None, "X"] and rows[1][4] == "Y" and rows[1][-1] is True
    assert len(rows[0]) == len(V.LEDGER_COLS)


def test_trade_log_record_and_append(tmp_path):
    rec = V.trade_log_record("FINAL", "final", "X_LONG", "CRYPTO_LONG", {"A": 1}, {"gain_pct": 3.0, "trades": 12, "behavior_fingerprint": "abcdef0123456789zz", "ledger": []}, 30, "md5", "npz", "ts")
    assert rec["set_sha256"] == V.set_sha256({"A": 1}) and rec["fp"] == "abcdef0123456789" and rec["rows"] == []
    p = V.trade_log_path(tmp_path, "X_LONG")
    V.append_jsonl(p, rec)
    V.append_jsonl(p, rec)
    lines = p.read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["kind"] == "FINAL"


def test_writeback_wiring_pins():
    assert "_v18_wb = {\"on\": bool(_V18P is not None and _V18P.ledger_writeback_on())" in SRC
    assert "_res = _eps_w(prepared, _ov, args.window_days, include_ledger=True)" in SRC
    assert "write-back failed: {_we} — board unaffected" in SRC
    # the write-back runs after the board decided (BEST-EFFORT and DONE paths), never inside the chain
    assert SRC.count("_v18_writeback()") >= 3


def test_file_md5(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    assert V.file_md5(p) == "900150983cd24fb0d6963f7d28e17f72"
    assert V.file_md5(tmp_path / "missing") is None
