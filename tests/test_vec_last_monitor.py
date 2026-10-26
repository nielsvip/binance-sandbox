from datetime import datetime, timedelta, timezone

from tools import vec_last_monitor as monitor


def test_log_timestamp_parser_supports_live_manager_formats():
    now = datetime(2026, 10, 26, 7, 30, tzinfo=timezone.utc)
    assert monitor._log_line_timestamp(
        "[2026-10-26 07:20:14,424] [VEC_LAST_REFUSE]", now
    ) == datetime(2026, 10, 26, 7, 20, 14, 424000, tzinfo=timezone.utc).timestamp()
    assert monitor._log_line_timestamp(
        "10-26 07:04:25 - [VEC_LAST_OVERRIDE]", now
    ) == datetime(2026, 10, 26, 7, 4, 25, tzinfo=timezone.utc).timestamp()
    assert monitor._log_line_timestamp(
        "26 07:04:25 - [VEC_DICTATE_ONLY_BLOCK]", now
    ) == datetime(2026, 10, 26, 7, 4, 25, tzinfo=timezone.utc).timestamp()
    assert monitor._log_line_timestamp("no timestamp [VEC_LAST_REFUSE]", now) is None


def test_scan_logs_filters_matching_lines_by_since_window(tmp_path):
    now = datetime.now().astimezone()
    recent = now.isoformat(timespec="seconds")
    old = (now - timedelta(hours=2)).isoformat(timespec="seconds")
    log = tmp_path / "ez_manage_test.log"
    log.write_text(
        f"[{old}] [VEC_LAST_OVERRIDE] old bypassed [CIRCUIT_BREAKER]\n"
        f"[{recent}] [VEC_LAST_REFUSE] recent refused [GATE_X]\n"
    )

    tags, gates, n_files = monitor.scan_logs(str(tmp_path), now.timestamp() - 3600)

    assert n_files == 1
    assert tags == {"VEC_LAST_REFUSE": 1}
    assert gates == {}
