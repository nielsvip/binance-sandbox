"""2026-10-14 (BIBLE §106 C1): the vector FIXED_STOP fires on the 15m bar wick like live (software leg on the live price every ~6 s +
exchange STOP_MARKET), fills at the stop level or the bar open when gapped through — always inside the bar's [low, high]."""
import pathlib
import pytest
import v12_quick_engine as V

NPZ_DIR = pathlib.Path(__file__).resolve().parents[1] / "backtest_v8" / "indicators"


@pytest.mark.skipif(not (NPZ_DIR / "ALGOUSDT.npz").exists(), reason="needs ALGOUSDT.npz")
@pytest.mark.parametrize("is_long", [True, False])
def test_fixed_stop_fills_inside_bar_and_fires_on_wicks(is_long):
    d = V.load_npz("crypto", ["ALGOUSDT"], "2026-09-10", npz_dir=str(NPZ_DIR))
    z = d["ALGOUSDT"]
    cfg = V.QuickConfig()
    cfg.MODE = "crypto"
    r = V.simulate_one(z, "ALGOUSDT", is_long, cfg)
    lo, hi, cl = z["low_15m"], z["high_15m"], z["close_15m"]
    stops = [t for t in r["ledger"] if isinstance(t, dict) and t.get("type") == "CLOSE" and t.get("reason") == "FIXED_STOP"]
    assert stops, "no FIXED_STOP exits at all"
    wick_only = 0
    for t in stops:
        i, px = int(t["bar_exit"]), float(t["exit_price"])
        assert float(lo[i]) - 1e-9 <= px <= float(hi[i]) + 1e-9
        wick_only += abs(px - float(cl[i])) > 1e-12
    assert wick_only >= 1, "intrabar (wick) stops never fired"
