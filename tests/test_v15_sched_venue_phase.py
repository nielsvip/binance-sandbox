"""USER 2026-10-12: stocks only until every tradeable stock has passed, then crypto (+ W stocks daily); load divided
across hosts by free capacity."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools import v15_fleet_scheduler as S


def _cs(stock_states, crypto_states):
    cs = {}
    for i, st in enumerate(stock_states):
        cs[f"ST{i}"] = {"LONG": (st, None), "SHORT": ("terminal_ok", None)}
    for i, st in enumerate(crypto_states):
        cs[f"C{i}USDT"] = {"LONG": (st, None), "SHORT": (st, None)}
    return cs


import datetime

RTH = datetime.datetime(2026, 10, 15, 14, 0, tzinfo=datetime.timezone.utc)  # Wed 10:00 ET, market open
OFF = datetime.datetime(2026, 10, 15, 3, 58, tzinfo=datetime.timezone.utc)  # Tue 23:58 ET, market closed


def test_auto_phase_stocks_while_launchable_at_every_hour():
    """The 2026-10-12 rule holds verbatim for the stock half, at every hour: USER 2026-10-15 (corrected) keeps the
    guaranteed daily stock pass, including after the 20:00 UTC close, because a post-close NPZ rebuilt to the
    session's final bar is current."""
    for now in (RTH, OFF):
        phase, pend = S._venue_phase(_cs(["need30", "terminal_ok"], ["need30"]), mode="auto", now=now)
        assert phase == "stocks" and pend == ["ST0_LONG"], now


def test_auto_phase_hands_the_rest_of_the_night_to_crypto_once_stocks_are_done():
    """USER 2026-10-15: "dedicate the rest of the night until market open to all cryptos that can fit". Before
    this, a drained stock queue fell through to "any" and crypto never had a window of its own."""
    phase, pend = S._venue_phase(_cs(["terminal_ok", "running", "waiting_base", "terminal_deferred"], ["need30"]), mode="auto", now=OFF)
    assert phase == "crypto" and pend == []  # running / waiting_base / deferred stocks never hold the phase
    assert S._venue_phase(_cs(["terminal_ok"], ["terminal_ok"]), mode="auto", now=OFF)[0] == "any"


def test_auto_phase_kill_switch_removes_only_the_crypto_phase(monkeypatch):
    monkeypatch.setenv("V15_SCHED_VENUE_CLOCK", "0")
    assert S._venue_phase(_cs(["need30"], ["need30"]), mode="auto", now=OFF)[0] == "stocks"
    assert S._venue_phase(_cs(["terminal_ok"], ["need30"]), mode="auto", now=OFF)[0] == "any"


def test_forced_crypto_phase_exists():
    assert S._venue_phase(_cs(["need30"], ["need30"]), mode="crypto", now=RTH)[0] == "crypto"


def test_forced_and_none_modes():
    cs = _cs(["terminal_ok"], ["need30"])
    assert S._venue_phase(cs, mode="stocks")[0] == "stocks"
    assert S._venue_phase(cs, mode="")[0] == "any"
    assert S._venue_phase({}, mode="auto")[0] == "any"


def test_venue_allowed_follows_phase():
    assert S._venue_allowed("AAPL", phase="stocks")
    assert not S._venue_allowed("ADAUSDC", phase="stocks")
    assert S._venue_allowed("ADAUSDC", phase="any")
    assert not S._venue_allowed("AAPLUSDT", phase="stocks")  # USDT stock twin is crypto


def test_host_order_most_free_first():
    hosts = [{"name": "s1", "max_pairs": 2}, {"name": "s2", "max_pairs": 3}, {"name": "s5", "max_pairs": 5}, {"name": "s6", "max_pairs": 5}]
    held = {"s1": {"A": "running", "B": "running"}, "s2": {"C": "running"}, "s5": {}, "s6": {"D": "running"}}
    assert [h["name"] for h in S._host_order(hosts, held)] == ["s5", "s6", "s2", "s1"]
    assert [h["name"] for h in S._host_order(hosts, {})] == ["s5", "s6", "s2", "s1"]  # ties: more free slots, then name
