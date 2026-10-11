"""Settle-aware expected bar (USER 2026-10-11 fix-as-discovered): precompute drops bars newer than TF+75s settle, but live demanded the just-closed bar instantly at each 15m boundary → fleet-wide warn+hold storms (div/noref waves) every 15m. During the 75s window live now demands the prior settled bar — exactly what the chart builder can serve.
"""
import logging
import numpy as np
from live_twins import vec_exact as V


BSTART = 1791685800.0


def test_mid_bucket_demands_just_closed():
    now = BSTART + 300.0
    assert V._expected_last_bar(now) == float((int(now // 900) - 1) * 900)


def test_settle_window_demands_prior_settled():
    b = int(BSTART // 900)
    assert V._expected_last_bar(BSTART + 10.0) == float((b - 2) * 900)
    assert V._expected_last_bar(BSTART + 74.9) == float((b - 2) * 900)


def test_settle_close_flips_to_just_closed():
    b = int(BSTART // 900)
    assert V._expected_last_bar(BSTART + 75.0) == float((b - 1) * 900)


def test_raw_live_klines_no_warn_in_settle_window(monkeypatch, caplog):
    b = int(BSTART // 900)
    prior = float((b - 2) * 900)
    monkeypatch.setattr(V, "_arrays_live_klines", lambda s: {"timestamps": np.asarray([prior - 900.0, prior])})
    monkeypatch.setattr(V, "_shared_raw_get", lambda *a: None)
    monkeypatch.setattr(V, "_shared_raw_put", lambda *a: None)
    monkeypatch.setattr(V._time, "time", lambda: BSTART + 10.0)
    V._RAW.pop("BTCUSDC", None)
    with caplog.at_level(logging.WARNING, logger=V.logger.name):
        r = V._raw_live_klines("BTCUSDC")
    assert r["last_ts"] == prior
    assert "bar not written yet" not in caplog.text
    V._RAW.pop("BTCUSDC", None)
