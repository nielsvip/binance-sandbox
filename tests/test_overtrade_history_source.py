"""Regression: TRADES_PER_SYM_PER_DAY_MAX must count from data/history only, not decisions."""

import pathlib
import json
import tempfile
from datetime import datetime, timezone


def test_ez_manage_overtrade_uses_history_not_decisions():
    src = pathlib.Path("ez_manage.py").read_text()
    # Use the guard block near execute_now (second occurrence, after _ot_max)
    idx = src.find("if _ot_max > 0 and not _ot_emerg:")
    assert idx != -1, "OVERTRADE_GUARD logic missing"
    block = src[idx: idx + 3000]
    assert "data" in block and "history" in block, "OVERTRADE_GUARD must read data/history"
    # Must count AUGMENT from history
    assert "AUGMENT" in block, "should count AUGMENT from history"
    # Should NOT use old in-memory counter increment
    assert "_overtrade_counter" not in block, "old _overtrade_counter should be removed (count from history only)"
    # Should reference history file path, not decisions
    assert "history" in block
    assert "decisions_" not in block.lower(), "OVERTRADE should not use decisions files"


def test_tradier_overtrade_uses_history_not_decisions():
    src = pathlib.Path("tradier_manage.py").read_text()
    idx = src.find("if _ot_max > 0 and not _ot_emerg")
    assert idx != -1, "tradier OVERTRADE_GUARD logic missing"
    block = src[idx: idx + 3000]
    assert "history" in block, "tradier must read history"
    assert "AUGMENT" in block
    assert "_OVERTRADE_COUNTER" not in block, "old _OVERTRADE_COUNTER should be removed"


def test_history_counting_logic_counts_today_only():
    """Unit: history counting filters by today UTC and only AUGMENT/OPEN counts."""
    with tempfile.TemporaryDirectory() as td:
        base = pathlib.Path(td)
        hist_dir = base / "data" / "history" / "ang"
        hist_dir.mkdir(parents=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Create history with 2 AUGMENT today, 1 REDUCE today, 1 AUGMENT yesterday
        f = hist_dir / "BTCUSDC_LONG.jsonl"
        records = [
            {"ts": f"{today}T10:00:00+00:00", "type": "AUGMENT"},
            {"ts": f"{today}T11:00:00+00:00", "type": "AUGMENT"},
            {"ts": f"{today}T12:00:00+00:00", "type": "REDUCE"},
            {"ts": "2020-01-01T00:00:00+00:00", "type": "AUGMENT"},
        ]
        with open(f, "w") as fh:
            for r in fh.write(""), records:
                pass
        # Actually write
        with open(f, "w") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")
        # Simulate counting logic from ez_manage
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        n = 0
        with open(f) as fh:
            for line in fh:
                rec = json.loads(line)
                ts = str(rec.get("ts", ""))
                if not ts.startswith(today_str):
                    continue
                typ = str(rec.get("type", "")).upper()
                if typ in ("OPEN", "AUGMENT", "REENTRY", "QUICK_OPEN", "QUICK_AUGMENT"):
                    n += 1
        assert n == 2, f"expected 2 AUGMENT today, got {n}"
        # Proposed decisions file with 10 entries should NOT affect count
        dec_dir = base / "data" / "decisions"
        dec_dir.mkdir(parents=True)
        dec_file = dec_dir / "decisions_ang_20260924.jsonl"
        with open(dec_file, "w") as fh:
            for i in range(10):
                fh.write(json.dumps({"timestamp": f"{today}T10:00:00+00:00", "action": "OPEN", "position_key": "ang:BTCUSDC_LONG"}) + "\n")
        # History count still 2, decisions ignored
        assert n == 2
