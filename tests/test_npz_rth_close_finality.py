"""2026-10-22 RTH-close finality for stock NPZ tails. Hermetic: fake builder module + fake klines store."""
import datetime as dt
import pathlib
import sys
import types
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import npz_bar_refresh as R  # noqa: E402

ET = ZoneInfo("America/New_York")


def et(day, h, m):
    return int(dt.datetime(2026, 10, day, h, m, tzinfo=ET).timestamp())


LAST = et(21, 15, 45)
CLOSE = et(21, 16, 0)


def store(rows):
    idx = pd.DatetimeIndex([dt.datetime.fromtimestamp(t, dt.timezone.utc) for t in rows])
    return pd.DataFrame({"close": [1.0] * len(rows)}, index=idx)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    state = {"rows": [LAST - 2 * 900, LAST - 900, LAST]}
    mod = types.ModuleType("backtest_v8_precompute")
    mod.load_klines = lambda p: store(state["rows"]) if "tradier" in str(p) else None
    mod.CRYPTO_KLINES = tmp_path / "crypto"
    monkeypatch.setitem(sys.modules, "backtest_v8_precompute", mod)
    monkeypatch.setattr(R, "PROV_DIR", tmp_path / "prov")
    monkeypatch.setattr(R, "_epoch_s", lambda idx: [int(x.timestamp()) for x in idx] and __import__("numpy").array([int(x.timestamp()) for x in idx]))
    return state


def test_after_close_last_bar_is_final(fake, monkeypatch):
    monkeypatch.setattr(R.time, "time", lambda: CLOSE + 6 * 60)
    assert R.cache_bar_final_stocks("AAPL", LAST) is True
    assert R.cache_last_final("AAPL", "tradier") == LAST


def test_before_close_without_newer_not_final(fake, monkeypatch):
    monkeypatch.setattr(R.time, "time", lambda: CLOSE - 60)
    assert R.cache_bar_final_stocks("AAPL", LAST) is False
    assert R.cache_last_final("AAPL", "tradier") == LAST - 900


def test_inside_grace_not_final(fake, monkeypatch):
    monkeypatch.setattr(R.time, "time", lambda: CLOSE + 2 * 60)
    assert R.cache_bar_final_stocks("AAPL", LAST) is False


def test_provisional_row_never_final(fake, monkeypatch):
    monkeypatch.setattr(R.time, "time", lambda: CLOSE + 10 * 60)
    monkeypatch.setattr(R, "prov_read", lambda s: {LAST: "snapshot"})
    assert R.cache_bar_final_stocks("AAPL", LAST) is False
    assert R.cache_last_final("AAPL", "tradier") == LAST - 900


def test_newer_bar_rule_unchanged(fake, monkeypatch):
    monkeypatch.setattr(R.time, "time", lambda: CLOSE - 3600)
    fake["rows"].append(LAST + 900)
    assert R.cache_bar_final_stocks("AAPL", LAST) is True


def test_bar_after_close_needs_successor(fake, monkeypatch):
    monkeypatch.setattr(R.time, "time", lambda: CLOSE + 3600)
    assert R.rth_session_over("AAPL", CLOSE) is False


def test_earlier_session_last_bar_final(fake, monkeypatch):
    monkeypatch.setattr(R.time, "time", lambda: CLOSE + 3 * 86400)
    assert R.rth_session_over("AAPL", et(20, 15, 45)) is True


def test_crypto_unchanged(fake, monkeypatch):
    monkeypatch.setattr(R.time, "time", lambda: CLOSE + 3600)
    assert R.cache_last_final("BTCUSDT", "crypto") is None
