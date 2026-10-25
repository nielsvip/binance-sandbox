#!/usr/bin/env python3
"""Tests for tools/kline_lag_watchdog.py — no lane ever >60s behind the kline."""

import time

from tools.kline_lag_watchdog import (
    bar_close_lag_s,
    bar_label_lag_s,
    check_hop,
    file_lag_s,
    last_closed_15m_epoch,
    stocks_in_hours,
)


def test_last_closed_bar():
    # 03:47 -> last close 03:45; 03:45:20 (inside settle) -> last close 03:30
    assert last_closed_15m_epoch(1792900020) == 1792899900
    assert last_closed_15m_epoch(1792899920) == 1792899000


def test_bar_close_lag():
    lc = 1792899000
    assert bar_close_lag_s(lc, lc + 30) == 0.0
    assert bar_close_lag_s(lc, lc + 95) == 65.0


def test_check_hop_ok_and_breach():
    assert check_hop("kline", 40.0) == ("kline", 40.0, True)
    assert check_hop("kline", 61.0) == ("kline", 61.0, False)


def test_file_lag():
    lc = 1792899000
    assert file_lag_s(lc, lc + 30) == 0.0
    assert file_lag_s(lc, lc - 60) == 90.0
    assert file_lag_s(lc, None) == 10**9


def test_bar_label_lag():
    lc, now = 1792899000, 1792899000 + 300
    assert bar_label_lag_s(lc, lc, now) == 0.0
    assert bar_label_lag_s(lc, lc + 900, now) == 0.0
    assert bar_label_lag_s(lc, lc - 900, now) == now - (lc - 900 + 930)
    assert bar_label_lag_s(lc, None, now) == 10**9


def test_stocks_hours_weekday():
    # Monday 2026-10-26 15:00 UTC -> in hours; Sunday -> out
    assert stocks_in_hours(1793026800) is True
    assert stocks_in_hours(1792900020) is False
