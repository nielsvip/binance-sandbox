"""Freshness-watch collateral: RTH math + breach verdicts (USER 2026-10-12)."""
import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.klines_freshness_watch import check_venue, in_rth, last_session_close  # noqa: E402

UTC = dt.timezone.utc
ET = ZoneInfo("America/New_York")


def _cache(tmp_path, sym, ts):
    d = tmp_path / "kl"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sym}_15m.json").write_text(json.dumps([{"timestamp": ts, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]))
    return d


def test_rth_boundaries():
    assert in_rth(dt.datetime(2026, 10, 12, 13, 29, tzinfo=UTC)) is False  # Mon 09:29 ET
    assert in_rth(dt.datetime(2026, 10, 12, 13, 30, tzinfo=UTC)) is True
    assert in_rth(dt.datetime(2026, 10, 12, 19, 59, tzinfo=UTC)) is True
    assert in_rth(dt.datetime(2026, 10, 12, 20, 0, tzinfo=UTC)) is False
    assert in_rth(dt.datetime(2026, 10, 10, 15, 0, tzinfo=UTC)) is False  # Sat


def test_last_session_close():
    assert last_session_close(dt.datetime(2026, 10, 11, 1, 0, tzinfo=UTC)).strftime("%m-%d %H:%M") == "10-09 16:00"  # Sun -> Fri
    assert last_session_close(dt.datetime(2026, 10, 13, 12, 0, tzinfo=UTC)).strftime("%m-%d %H:%M") == "10-12 16:00"  # Tue pre-open -> Mon
    assert last_session_close(dt.datetime(2026, 10, 12, 21, 0, tzinfo=UTC)).strftime("%m-%d %H:%M") == "10-12 16:00"  # Mon post-close -> Mon


def test_crypto_breach_and_ok(tmp_path):
    now = dt.datetime(2026, 10, 12, 2, 0, tzinfo=UTC)
    d = _cache(tmp_path, "BTCUSDC", "2026-10-12T00:00:00.000000Z")  # 120 min stale
    r = check_venue(d, ("BTCUSDC",), now.timestamp(), False, now)
    assert r["status"] == "BREACH" and r["worst_lag_min"] == 120.0
    d2 = _cache(tmp_path / "f", "BTCUSDC", "2026-10-12T01:45:00.000000Z")
    assert check_venue(d2, ("BTCUSDC",), now.timestamp(), False, now)["status"] == "OK"


def test_stocks_offhours_close_rule(tmp_path):
    sun = dt.datetime(2026, 10, 11, 1, 0, tzinfo=UTC)  # Sunday
    d = _cache(tmp_path, "SPY", "2026-10-09T19:45:00.000000Z")  # Fri last bar
    assert check_venue(d, ("SPY",), sun.timestamp(), True, sun)["status"] == "OK_CLOSED"
    d2 = _cache(tmp_path / "s", "SPY", "2026-10-07T19:45:00.000000Z")  # Wed bar
    assert check_venue(d2, ("SPY",), sun.timestamp(), True, sun)["status"] == "BREACH"
