"""ang_validity_gate — only backtest-proven sym_sides stay in the ANG universe."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import ang_validity_gate as G
def _entry(trades=100, gain=5.0, sharpe=0.1, dd=5.0):
    return {"trades": trades, "acc_gain_pct": gain, "pool_sharpe": sharpe, "max_dd_pct": dd}
def test_valid_passes():
    ok, why = G.validity("A_LONG", {"A_LONG": _entry()})
    assert ok and why in ("ok",) or why.startswith("WEAK")
def test_weak_sharpe_passes_with_flag():
    ok, why = G.validity("A_LONG", {"A_LONG": _entry(sharpe=0.05)})
    assert ok and why.startswith("WEAK_SHARPE")
def test_missing_fails():
    ok, why = G.validity("A_LONG", {})
    assert not ok and why == "MISSING_FROM_PERSYM"
def test_below_trade_floor_fails():
    ok, why = G.validity("A_LONG", {"A_LONG": _entry(trades=29)})
    assert not ok and "trades=29<30" in why
def test_zero_gain_fails():
    ok, why = G.validity("A_LONG", {"A_LONG": _entry(gain=0.0)})
    assert not ok and "gain=" in why
def test_negative_gain_fails():
    ok, why = G.validity("A_LONG", {"A_LONG": _entry(gain=-7.33)})
    assert not ok and "gain=-7.33" in why
def test_zero_sharpe_fails():
    ok, why = G.validity("A_LONG", {"A_LONG": _entry(sharpe=0.0)})
    assert not ok and "pool_sharpe=" in why
def test_negative_sharpe_fails():
    ok, why = G.validity("A_LONG", {"A_LONG": _entry(sharpe=-0.029)})
    assert not ok
def test_high_dd_fails():
    ok, why = G.validity("A_LONG", {"A_LONG": _entry(dd=31.0)})
    assert not ok and "dd=" in why
def test_gain_30d_fallback():
    e = {"trades": 50, "gain_30d": 3.0, "wsharpe": 0.2, "max_dd_pct": 2.0}
    ok, _ = G.validity("A_LONG", {"A_LONG": e})
    assert ok
def test_backfill_entry_fails():
    e = {"template_md5": "x", "winning_tag": "backfill", "overrides": {}}
    ok, why = G.validity("A_LONG", {"A_LONG": e})
    assert not ok and "trades=0<30" in why
def test_real_polluters_fail():
    ps = {"DOTUSDT_LONG": {"trades": 2, "acc_gain_pct": -1.12, "pool_sharpe": -0.743, "max_dd_pct": 1.3}, "FARTCOINUSDT_SHORT": {"trades": 168, "acc_gain_pct": -1.35, "pool_sharpe": -0.056, "max_dd_pct": 6.6}, "NEARUSDC_LONG": {"trades": 2, "acc_gain_pct": 0.23, "pool_sharpe": 1.217, "max_dd_pct": 0.0}}
    for k in ps:
        ok, _ = G.validity(k, ps)
        assert not ok, k
def test_real_keepers_pass():
    ps = {"MELANIAUSDT_SHORT": {"trades": 207, "acc_gain_pct": 19.26, "pool_sharpe": 0.068, "max_dd_pct": 6.8}, "RVNUSDT_SHORT": {"trades": 63, "acc_gain_pct": 5.3, "pool_sharpe": 0.03, "max_dd_pct": 0.7}}
    for k in ps:
        ok, _ = G.validity(k, ps)
        assert ok, k
