"""npz_bar_refresh drop-list pins.

The tail builder cannot extend every legacy NPZ key; keys proven reader-free
are dropped on refresh instead of raising ValueError (which aborts the whole
symbol and freezes its NPZ = stock-lag disaster). Regression pins so the
dropped families are never accidentally removed.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import npz_bar_refresh as R


def test_tup_grid_droppable():
    grid = ["t_up_1h", "tco_1h", "tcu_1h", "t_up_4h", "tco_4h", "tcu_4h", "t_up_D", "tco_D", "tcu_D"]
    for k in grid:
        assert k in R.REFRESH_DROP_KEYS, f"{k} missing -> COE/DHR/EOG-class stocks ERROR every cycle"


def test_legacy_families_still_droppable():
    for k in ["macd_M", "adx_M", "stdev_edge_15m", "stdev_slope_D", "tco_15m", "linearity_1h", "slope_close_4h"]:
        assert k in R.REFRESH_DROP_KEYS


def test_pilot_syms_extend_universes(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    (tmp_path / "data" / "daily_universe").mkdir(parents=True)
    (tmp_path / "data" / "daily_universe" / "last_good.json").write_text(json.dumps({"crypto": ["ONDOUSDT_SHORT", "ADBEUSDT"], "stocks": ["FOO", "BAR_LONG"]}))
    (tmp_path / "data" / "reports" / "lifecycle_pilot").mkdir(parents=True)
    (tmp_path / "data" / "reports" / "lifecycle_pilot" / "PLTR_LONG_v14_progress.json").write_text("{}")
    (tmp_path / "symbols_tradier.json").write_text(json.dumps(["FOO", "AAPL"]))
    (tmp_path / "symbols_active.json").write_text(json.dumps([]))
    monkeypatch.setattr(R, "ROOT", Path(tmp_path))
    ps = R.pilot_syms()
    assert {"ONDOUSDT", "ADBEUSDT", "FOO", "BAR", "PLTR"} <= ps
    assert "ONDOUSDT" in R.universe() and "ADBEUSDT" in R.universe()
    assert "FOO" not in R.universe()
    st = R.universe_stocks()
    assert "FOO" in st and "BAR" in st and "PLTR" in st and "AAPL" in st
    assert "ONDOUSDT" not in st
    assert len(st) == len(set(st))
    monkeypatch.setattr(R, "ROOT", Path(tmp_path / "emptyroot"))
    assert R.pilot_syms() == set()


def _kl(rows):
    import json
    return json.dumps([{"timestamp": __import__("datetime").datetime.utcfromtimestamp(t).strftime("%Y-%m-%dT%H:%M:%S.000000Z"),
                        "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0} for t in rows])


def test_stale_canonical_copy_falls_through_to_the_long_store(tmp_path, monkeypatch):
    """USER 2026-10-15 "all npz's up to date every 15m". S1 does not WRITE klines_canonical — the Mac daemon does and
    klines_canonical_push.sh ships it. When that copy lagged (measured 70 min) while S1's own REST was refused with
    HTTP 418, cache_bar_final returned False for every canonical-complete symbol and the loop installed NOTHING for
    six cycles, although klines_cache_backtest already held the bar. The fallthrough must find it there — still only
    when a STRICTLY NEWER row proves the bar closed."""
    import backtest_v8_precompute as bvp
    import npz_bar_refresh as R
    canon, cache = tmp_path / "canon", tmp_path / "cache"
    canon.mkdir(); cache.mkdir()
    cutoff = 1_792_102_500
    (canon / "X_15m.json").write_text(_kl([cutoff - 4 * R.BAR, cutoff - 3 * R.BAR]))      # canonical copy is 45 min behind
    (cache / "X_15m.json").write_text(_kl([cutoff - R.BAR, cutoff, cutoff + R.BAR]))      # the long store has it + a successor
    monkeypatch.setattr(bvp, "CANONICAL_KLINES", canon)
    monkeypatch.setattr(bvp, "CRYPTO_KLINES", cache)
    monkeypatch.setattr(bvp, "_canonical_complete", lambda s: True)
    monkeypatch.setenv("NPZ_CANONICAL_WAIT_S", "0")
    assert R.cache_bar_final("X", cutoff) is True


def test_unfinal_bar_is_still_refused_after_the_fallthrough(tmp_path, monkeypatch):
    """The relaxation must not invent finality: with no row NEWER than the cutoff, the answer stays False."""
    import backtest_v8_precompute as bvp
    import npz_bar_refresh as R
    canon, cache = tmp_path / "canon", tmp_path / "cache"
    canon.mkdir(); cache.mkdir()
    cutoff = 1_792_102_500
    (canon / "Y_15m.json").write_text(_kl([cutoff - 3 * R.BAR]))
    (cache / "Y_15m.json").write_text(_kl([cutoff - R.BAR, cutoff]))   # cutoff is the newest row -> not provably closed
    monkeypatch.setattr(bvp, "CANONICAL_KLINES", canon)
    monkeypatch.setattr(bvp, "CRYPTO_KLINES", cache)
    monkeypatch.setattr(bvp, "_canonical_complete", lambda s: True)
    monkeypatch.setenv("NPZ_CANONICAL_WAIT_S", "0")
    assert R.cache_bar_final("Y", cutoff) is False
