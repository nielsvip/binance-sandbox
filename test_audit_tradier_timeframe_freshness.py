import json
from datetime import datetime, timezone

from tools.audit_tradier_timeframe_freshness import audit_symbol


def _write(path, rows):
    path.write_text(json.dumps(rows))


def test_stale_higher_timeframe_blocks_calculation(tmp_path):
    recent = "2026-07-31T19:45:00Z"
    stale = "2026-04-07T17:30:00Z"
    for tf in ("5m", "15m", "D"):
        _write(tmp_path / f"MSTR_{tf}.json", [{"timestamp": recent}])
    for tf in ("1h", "4h"):
        _write(tmp_path / f"MSTR_{tf}.json", [{"timestamp": stale}])
    report = audit_symbol("MSTR", tmp_path)
    assert report["calculation_allowed"] is False
    assert set(report["failures"]) == {"1h_STALE", "4h_STALE"}


def test_fresh_timeframes_pass(tmp_path):
    for tf in ("5m", "15m", "1h", "4h", "D"):
        _write(tmp_path / f"MU_{tf}.json", [{"timestamp": "2026-07-31T19:45:00Z"}])
    report = audit_symbol("MU", tmp_path)
    assert report["calculation_allowed"] is True
    assert report["data_contract_status"] == "PASS"
