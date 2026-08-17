from download_stock_klines_5m import (
    incremental_start,
    merge_bars,
    repair_range_completed,
    retention_audit,
)
import download_stock_klines_5m as downloader


def bar(timestamp, close):
    return {
        "timestamp": timestamp,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1,
    }


def test_incremental_merge_never_drops_retained_native_bars():
    existing = [
        bar("2026-07-20T13:30:00.000000Z", 10),
        bar("2026-07-20T13:35:00.000000Z", 11),
    ]
    incoming = [
        bar("2026-07-20T13:35:00.000000Z", 12),
        bar("2026-07-20T13:40:00.000000Z", 13),
    ]
    merged = merge_bars(existing, incoming)
    audit = retention_audit(existing, merged)
    assert audit["valid"] is True
    assert audit["before_count"] == 2
    assert audit["after_count"] == 3
    assert audit["added_count"] == 1
    assert next(x for x in merged if x["timestamp"] == incoming[0]["timestamp"])["close"] == 12


def test_retention_audit_rejects_a_shorter_refresh():
    existing = [
        bar("2026-07-20T13:30:00.000000Z", 10),
        bar("2026-07-20T13:35:00.000000Z", 11),
    ]
    audit = retention_audit(existing, existing[1:])
    assert audit["valid"] is False
    assert audit["missing_count"] == 1


def test_incremental_fetch_overlaps_latest_native_tail():
    existing = [bar("2026-07-20T13:35:00.000000Z", 11)]
    assert incremental_start(existing, "2024-01-01", overlap_days=2) == "2026-07-18"
    assert incremental_start([], "2024-01-01", overlap_days=2) == "2024-01-01"


def test_exact_repair_range_checkpoint_skips_completed_symbol():
    progress = {
        "completed_ranges": {
            "AAPL": {
                "fetch_from": "2024-08-01",
                "fetch_to": "2026-08-03",
                "valid": True,
            }
        }
    }
    assert repair_range_completed(
        progress, {}, "AAPL", "2024-08-01", "2026-08-03"
    )
    assert not repair_range_completed(
        progress, {}, "AAPL", "2024-08-01", "2026-08-04"
    )


def test_retention_receipt_recovers_checkpoint_after_interrupted_run():
    rows = {
        "A": {
            "fetch_from": "2024-08-01",
            "fetch_to": "2026-08-03",
            "valid": True,
        }
    }
    assert repair_range_completed(
        {"completed_ranges": {}}, rows, "A", "2024-08-01", "2026-08-03"
    )
    rows["A"]["valid"] = False
    assert not repair_range_completed(
        {"completed_ranges": {}}, rows, "A", "2024-08-01", "2026-08-03"
    )


def test_corrupt_interrupted_progress_recovers_fail_closed(tmp_path, monkeypatch):
    progress_path = tmp_path / "progress.json"
    progress_path.write_text('{"completed": [')
    monkeypatch.setattr(downloader, "PROGRESS_FILE", progress_path)
    assert downloader.load_progress() == {
        "completed": [],
        "failed": {},
        "completed_ranges": {},
    }
