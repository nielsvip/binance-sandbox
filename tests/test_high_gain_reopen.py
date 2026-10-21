"""TR HIGH-GAIN REOPEN predicate (vec_decisions/high_gain_reopen.py) — vector twin of
tradier_manage.periodic_direct_high_gain_reopen: re-open reduced high-gain lifecycles
(stocks/tradier path only, default off = live config_tradier.ENABLE_TR_HIGH_GAIN_REOPEN)."""
import types
from pathlib import Path

import vec_decisions.high_gain_reopen as HGR

ROOT = Path(__file__).resolve().parents[1]


def _cfg(**kw):
    d = dict(TR_HIGH_GAIN_REOPEN_ENABLED=True, TR_HIGH_GAIN_REOPEN_WINDOW_BARS=12, TR_HIGH_GAIN_REOPEN_MIN_GAIN_PCT=0.5,
             TR_HIGH_GAIN_REOPEN_MIN_RELVOL=1.2, TR_HIGH_GAIN_REOPEN_RSI_LONG_MAX=75.0, TR_HIGH_GAIN_REOPEN_RSI_SHORT_MIN=25.0)
    d.update(kw)
    return types.SimpleNamespace(**d)


GOOD_L = dict(bars_since_exit=4, entry_price=100.0, exit_price=101.0, close=101.5, rsi=60.0, k=60.0, d=55.0,
              ha_color=1.0, relvol=1.8, dc4h_low=99.0, dc4h_high=110.0)


def _fire(cfg, is_long=True, **over):
    a = dict(GOOD_L)
    a.update(over)
    return HGR.fire(cfg, is_long, a["bars_since_exit"], a["entry_price"], a["exit_price"], a["close"], a["rsi"],
                    a["k"], a["d"], a["ha_color"], a["relvol"], a["dc4h_low"], a["dc4h_high"])


def test_full_stack_fires_long():
    assert _fire(_cfg())


def test_each_condition_vetoes():
    c = _cfg()
    assert not _fire(_cfg(TR_HIGH_GAIN_REOPEN_ENABLED=False))
    assert not _fire(c, bars_since_exit=0) and not _fire(c, bars_since_exit=13)  # window 1..12
    assert not _fire(c, exit_price=100.4)  # lifecycle gain 0.4 < 0.5 floor
    assert not _fire(c, rsi=75.0)  # overbought refusal
    assert not _fire(c, k=50.0, d=55.0)  # stoch not crossed up
    assert not _fire(c, ha_color=-1.0)  # heikin not green
    assert not _fire(c, relvol=1.1)  # thin tape
    assert not _fire(c, close=98.5)  # beneath 4h structure
    assert not _fire(c, rsi=float("nan"))  # fail-closed on missing inputs


def test_short_is_mirrored():
    s = dict(bars_since_exit=2, entry_price=100.0, exit_price=99.0, close=98.5, rsi=40.0, k=40.0, d=45.0,
             ha_color=-1.0, relvol=2.0, dc4h_low=90.0, dc4h_high=99.5)
    a = dict(s)
    assert HGR.fire(_cfg(), False, a["bars_since_exit"], a["entry_price"], a["exit_price"], a["close"], a["rsi"],
                    a["k"], a["d"], a["ha_color"], a["relvol"], a["dc4h_low"], a["dc4h_high"])
    assert not _fire(_cfg(), False, **dict(s, rsi=25.0))
    assert not _fire(_cfg(), False, **dict(s, k=50.0))
    assert not _fire(_cfg(), False, **dict(s, ha_color=1.0))


def test_lifecycle_gain_signs_and_guards():
    assert HGR.lifecycle_gain_pct(True, 100.0, 101.0) == abs(HGR.lifecycle_gain_pct(True, 100.0, 101.0)) and abs(HGR.lifecycle_gain_pct(True, 100.0, 101.0) - 1.0) < 1e-9
    assert abs(HGR.lifecycle_gain_pct(False, 100.0, 99.0) - 1.0) < 1e-9
    assert HGR.lifecycle_gain_pct(True, 100.0, 99.0) < 0.0
    assert HGR.lifecycle_gain_pct(True, 0.0, 101.0) == 0.0
    assert HGR.lifecycle_gain_pct(True, 100.0, 0.0) == 0.0


def test_was_reduced_matches_lifecycle():
    close = {"type": "CLOSE", "bar_entry": 10, "bar_exit": 20}
    assert HGR.was_reduced(close, [{"type": "REDUCE", "bar_entry": 10, "bar_exit": 15}], [])
    assert HGR.was_reduced(close, [], [{"type": "REDUCE", "bar": 18, "price": 1.0}])
    assert not HGR.was_reduced(close, [{"type": "REDUCE", "bar_entry": 99, "bar_exit": 100}], [])
    assert not HGR.was_reduced(close, [], [{"type": "REDUCE", "bar": 25, "price": 1.0}])
    assert not HGR.was_reduced(close, [], [])
    assert not HGR.was_reduced({"type": "CLOSE"}, [], [])


def test_wired_once_stocks_only_default_off():
    eng = (ROOT / "v12_quick_engine.py").read_text()
    assert eng.count("_hgr.fire(") == 1
    assert "TR_HIGH_GAIN_REOPEN_ENABLED: bool = False" in eng
    assert '_hgr_on = _hgr.enabled(cfg) and str(getattr(cfg, "MODE", "crypto")) == "tradier"' in eng
    assert "if _hgr_fired[0] == i and not entry_sig[i]:" in eng
    assert "import vec_decisions.high_gain_reopen as _hgr" in eng
    live = (ROOT / "tradier_manage.py").read_text()
    assert "async def periodic_direct_high_gain_reopen" in live
    ct = (ROOT / "config_tradier.py").read_text()
    assert "ENABLE_TR_HIGH_GAIN_REOPEN: bool = False" in ct
