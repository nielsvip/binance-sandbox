"""A full slot count cannot stand in for completed boards or useful CPU."""
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from fleet_throughput_monitor import summarize


def test_full_slots_still_alert_on_low_cpu_and_board_rate():
    when = dt.datetime(2026, 10, 25, 23, 50, tzinfo=dt.timezone.utc)
    source = {
        "now": when.isoformat(),
        "round_completion": {"round": "run36", "n_launchable_pending": 27},
        "coverage": {"throughput": {"sym_sides": 3, "published_per_host": {"s5": 3}}},
        "hosts": {"s5": {"slots": "4/4", "cpu_real": 50, "observed_running": ["A", "B"], "mem_avail_mb": 20000}},
    }
    got = summarize(source, when.timestamp() + 20)
    assert got["hosts"]["s5"]["published_last_hour"] == 3
    assert "LOW_THROUGHPUT:s5:3/h" in got["alerts"]
    assert "LOW_CPU:s5:50%" in got["alerts"]
