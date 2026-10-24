"""History conservation pins (USER 2026-10-24 phantom-close fix).

Measured: fin FARTCOINUSDT_SHORT logged 156 identical REDUCE 32.6 rows across
21h on 2026-09-02 with zero exchange SELL fills that day; MASKUSDT_LONG
10-24 logged 830 journal close qty vs ~320 exchange. Every re-log of an
already-covered close starves the dashboard walk (burns the day's fills,
no basis) and double-counts window P&L when priced.

Law: a REDUCE/CLOSE row lands only if in-window OPEN/AUGMENT qty covers it
(file-backed, so it holds across processes and restarts where the 900s
in-memory dedup does not). Fail-open always: empty history, no in-window
opens, or any trouble -> the row lands.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from vec_decisions import execution_ledger as _el


def _row(ts, typ, qty, price=1.0):
    return {"ts": ts.isoformat(), "type": typ, "qty": qty, "price": price}


def _write(path, rows):
    with open(path, "a") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_fartcoin_storm_second_close_suppressed(tmp_path):
    p = str(tmp_path / "FARTCOINUSDT_SHORT.jsonl")
    now = datetime.now(timezone.utc)
    _write(p, [_row(now - timedelta(hours=2), "AUGMENT", 32.6, 0.15)])
    assert _el.conservation_allows(p, "REDUCE", 32.6) is True
    _write(p, [_row(now - timedelta(hours=1), "REDUCE", 32.6, 0.15)])
    assert _el.conservation_allows(p, "REDUCE", 32.6) is False


def test_scalp_alternation_all_allowed(tmp_path):
    p = str(tmp_path / "WIFUSDC_LONG.jsonl")
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(6):
        rows.append(_row(now - timedelta(minutes=30 - i * 4), "AUGMENT", 34.9, 0.20))
        rows.append(_row(now - timedelta(minutes=28 - i * 4), "REDUCE", 34.9, 0.20))
    for r in rows:
        assert _el.conservation_allows(p, r["type"], r["qty"]) is True
        _write(p, [r])


def test_partials_allowed_until_covered(tmp_path):
    p = str(tmp_path / "X_LONG.jsonl")
    now = datetime.now(timezone.utc)
    _write(p, [_row(now - timedelta(hours=3), "OPEN", 100.0)])
    assert _el.conservation_allows(p, "REDUCE", 50.0) is True
    _write(p, [_row(now - timedelta(hours=2), "REDUCE", 50.0)])
    assert _el.conservation_allows(p, "REDUCE", 50.0) is True
    _write(p, [_row(now - timedelta(hours=1), "REDUCE", 50.0)])
    assert _el.conservation_allows(p, "REDUCE", 50.0) is False


def test_fail_open_without_in_window_opens(tmp_path):
    p = str(tmp_path / "Y_LONG.jsonl")
    now = datetime.now(timezone.utc)
    _write(p, [_row(now - timedelta(hours=1), "REDUCE", 10.0)])
    assert _el.conservation_allows(p, "REDUCE", 10.0) is True
    assert (
        _el.conservation_allows(str(tmp_path / "missing.jsonl"), "REDUCE", 5.0) is True
    )
    assert _el.conservation_allows(p, "OPEN", 5.0) is True


def test_reduce_close_double_log_second_suppressed(tmp_path, monkeypatch):
    monkeypatch.delenv("V8_SIM_TS", raising=False)
    now = datetime.now(timezone.utc)
    base = str(tmp_path)
    os.makedirs(os.path.join(base, "data", "history", "fin"), exist_ok=True)
    p = os.path.join(base, "data", "history", "fin", "M_LONG.jsonl")
    _write(p, [_row(now - timedelta(hours=1), "AUGMENT", 110.0, 0.40)])
    assert _el.history_write_allowed(p, "REDUCE", 110.0, 0.405) is True
    _write(p, [_row(now - timedelta(minutes=30), "REDUCE", 110.0, 0.405)])
    assert _el.history_write_allowed(p, "CLOSE", 110.0, 0.405) is False


def test_steady_scalps_many_rounds_all_allowed(tmp_path):
    p = str(tmp_path / "W_LONG.jsonl")
    now = datetime.now(timezone.utc)
    t = now - timedelta(hours=10)
    for _ in range(20):
        assert _el.conservation_allows(p, "AUGMENT", 34.9) is True
        _write(p, [_row(t, "AUGMENT", 34.9, 0.20)])
        t += timedelta(minutes=4)
        assert _el.conservation_allows(p, "REDUCE", 34.9) is True
        _write(p, [_row(t, "REDUCE", 34.9, 0.20)])
        t += timedelta(minutes=4)


def test_rotated_history_same_qty_twice_allowed(tmp_path):
    p = str(tmp_path / "R_LONG.jsonl")
    now = datetime.now(timezone.utc)
    _write(p, [_row(now - timedelta(hours=5), "REDUCE", 10.0, 2.0)])
    assert _el.conservation_allows(p, "REDUCE", 10.0) is True


def test_storm_with_sparse_opens_mostly_suppressed(tmp_path):
    p = str(tmp_path / "S_SHORT.jsonl")
    now = datetime.now(timezone.utc)
    _write(p, [_row(now - timedelta(hours=20), "AUGMENT", 40.0, 0.15)])
    allowed = 0
    for i in range(8):
        if _el.conservation_allows(p, "REDUCE", 40.0):
            allowed += 1
            _write(p, [_row(now - timedelta(hours=19 - i), "REDUCE", 40.0, 0.15)])
    assert allowed == 1


def test_tail_cross_type_same_fill_suppressed(tmp_path):
    p = str(tmp_path / "Z_LONG.jsonl")
    now = datetime.now(timezone.utc)
    _write(p, [_row(now - timedelta(minutes=5), "REDUCE", 110.0, 0.405)])
    assert _el.history_write_allowed(p, "CLOSE", 110.0, 0.405) is False
    assert _el.history_write_allowed(p, "CLOSE", 110.0, 0.500) is True
