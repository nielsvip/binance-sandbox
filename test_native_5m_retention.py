from download_stock_klines_5m import incremental_start, merge_bars, retention_audit


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
