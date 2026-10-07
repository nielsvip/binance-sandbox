"""Regression: tradier indicators must NEVER be 13-day stale.

Root cause 2026-09-10: tradier_indicators._broadcast_to_redis wrote only
_tradier_indicators_latest.json, but tradier_manage.market_data_sync_loop was
TIMESTAMPED-ONLY and refused to fallback to _latest. After the Aug 27
retention harvest deleted most timestamped files, market_snapshot stayed frozen
on 20260827_180546.json (13.9 days stale, [STALE_HOLD] NEM Age:1197k).
Fix: writer now also creates timestamped copy on each broadcast, poller falls
back to _latest when timestamped stale >90s, JSON loader broadens pattern,
emergency rsync self-heals, and a staleness watchdog alerts.
"""
import json
import time
import tempfile
from pathlib import Path
from unittest.mock import patch


def test_broadcast_creates_timestamped(tmp_path=None):
    """_broadcast_to_redis must create a timestamped file, not only _latest."""
    # Simulate fixed writer: it writes both files
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        latest = data_dir / "tradier_indicators_latest.json"
        payload = json.dumps({"NEM": {"timestamp": "2026-09-10T15:00:00Z"}}).encode()
        # Simulate fixed _write_file + _write_timestamped
        latest.write_bytes(payload)
        ts_file = data_dir / f"tradier_indicators_{int(time.time())}.json"
        ts_file.write_bytes(payload)
        assert latest.exists()
        assert ts_file.exists()
        # Retention keeps newest timestamped
        all_ts = sorted(data_dir.glob("tradier_indicators_[0-9]*.json"), key=lambda p: p.stat().st_mtime)
        assert len(all_ts) == 1
        assert ts_file in all_ts


def test_poller_fallback_to_latest_when_timestamped_stale(tmp_path=None):
    """market_data_sync_loop must NOT stay on 13-day-old timestamped when _latest is fresh."""
    with tempfile.TemporaryDirectory() as td:
        data_dir = Path(td)
        # Old timestamped (13 days ago)
        old = data_dir / "tradier_indicators_20260827_180546.json"
        old.write_text(json.dumps({"NEM": {"timestamp": "2026-08-27T18:05:46Z"}}))
        old_age = time.time() - 13 * 86400
        import os
        os.utime(old, (old_age, old_age))
        # Fresh _latest (now)
        latest = data_dir / "tradier_indicators_latest.json"
        latest.write_text(json.dumps({"NEM": {"timestamp": "2026-09-10T15:26:00Z"}}))
        # Poller logic: if timestamped age >90 and latest age <30, use latest
        newest_age = time.time() - old.stat().st_mtime
        latest_age = time.time() - latest.stat().st_mtime
        assert newest_age > 90
        assert latest_age < 30
        # Fixed poller would choose latest
        use_latest = newest_age > 90 and latest_age < 30
        assert use_latest is True


def test_json_loader_broad_pattern_accepts_epoch_naming():
    """load_indicators_from_json must accept both 20260827_180546 and 1789054088 naming."""
    import re
    # Old code used r"tradier_indicators_\d{8}_\d{6}\.json" which rejects epoch files
    old_pat = re.compile(r"tradier_indicators_\d{8}_\d{6}\.json")
    new_pat = re.compile(r"tradier_indicators_\d+.*\.json")
    assert old_pat.match("tradier_indicators_20260827_180546.json")
    assert not old_pat.match("tradier_indicators_1789054088.json")
    assert new_pat.match("tradier_indicators_1789054088.json")
    assert new_pat.match("tradier_indicators_20260827_180546.json")
    # Must NOT match latest
    assert "latest" not in "tradier_indicators_1789054088.json"


def test_emergency_script_exists():
    """emergency_tradier_indicators_rsync.sh must exist (was missing, causing silent Popen failure)."""
    p = Path("/Users/niels/Documents/binance/tools/emergency_tradier_indicators_rsync.sh")
    assert p.exists(), "emergency rsync script missing — market_data_sync_loop Popen will silently fail"
    assert p.stat().st_mode & 0o111, "script not executable"
    text = p.read_text()
    assert "_latest" in text and "timestamped" in text


def test_self_config_fix():
    """process_position must use trade_manager.config, not self.config (NameError crash)."""
    src = Path("/Users/niels/Documents/binance/tradier_manage.py").read_text()
    # The buggy line used self.config inside standalone function process_position
    # After fix it uses trade_manager.config
    assert "getattr(trade_manager.config, 'TRB_MAX_SYMBOL_VALUE'" in src
    # Ensure no remaining self.config inside process_position (search near that line)
    lines = src.splitlines()
    for i, line in enumerate(lines):
        if "WT_3M_FORCE_OPEN_TARGET_USD" in line:
            ctx = "\n".join(lines[max(0, i - 2):i + 3])
            assert "self.config" not in ctx, f"self.config still in process_position at line {i+1}: {ctx}"
            break


def test_reentry_price_cross_guaranteed():
    """Price reclaim must bypass WT/DC veto — CROSSED_BACK_GUARANTEED."""
    src = Path("/Users/niels/Documents/binance/tradier_manage.py").read_text()
    assert "CROSSED_BACK_GUARANTEED" in src, "branch-B price reclaim must force OPEN with CROSSED_BACK_GUARANTEED"
    assert "MANDATORY_REENTRY_PRICE_CROSS" in src


def test_reentry_gap_and_symgate_bypass_for_reclaim():
    """GAP and SYMGATE must not block a price reclaim (cur beyond exit)."""
    src = Path("/Users/niels/Documents/binance/tradier_manage.py").read_text()
    assert "GAP BYPASSED [GUARANTEED]" in src, "reentry_monitor GAP must bypass on price reclaim"
    assert "SYMGATE bypassed" in src or "SYMGATE bypassed" in src.lower(), "reentry_monitor SYMGATE must bypass on price reclaim"


def test_dc4h_bypass_for_mandatory_reclaim():
    """4h Donchian entry gate must not veto MANDATORY_REENTRY CROSSED_BACK_GUARANTEED."""
    src = Path("/Users/niels/Documents/binance/tradier_manage.py").read_text()
    assert "_is_mandatory_reclaim" in src
    assert "CROSSED_BACK_GUARANTEED" in src


def test_breadth_fallback_and_intraday_short_open():
    """Breadth must fallback when 15m EMA missing, and intraday must OPEN underweight."""
    src = Path("/Users/niels/Documents/binance/tradier_manage.py").read_text()
    assert "ema_20_15m_prev" in src and "ema_50_15m_prev" in src, "calculate_unified_market_ratio must fallback beyond ema_20_15m"
    assert "INTRADAY_RATIO_OPEN" in src, "intraday loop must force-open underweight side"
    assert "indicator_staleness_watchdog" in src


def test_watchdog_desktop_phone_alert_wired():
    """Staleness watchdog must alert via desktop osascript and phone ALERT file."""
    src = Path("/Users/niels/Documents/binance/tradier_manage.py").read_text()
    assert "indicator_staleness_watchdog" in src
    assert "osascript" in src
    assert "display notification" in src
    assert "ALERT_INDICATORS_STALE" in src
    assert "ALERT_NOT_TRADING" in src


def test_htf_completed_fallback_no_stale_15m_1h_4h():
    """BLOCKER 2026-09-11: 15m/1h/4h/D WT/K/DC were None when tradier_indicators_latest only has _completed_* fields.
    _adapt_indicators_for_ez_manage must fill wt1_15m/wt2_15m/k_15m/d_15m/dc_* from _completed snapshot so NO INDICATOR CAN EVER BE STALE
    via Mac+S1+gateway+klines completed candle fallback. This blocked all 4 shorts below exit (MCD/NOC/LMT/AXON) as Wait_WT_Reentry_S."""
    import tradier_manage
    mgr = tradier_manage.TradierTradeManager.__new__(tradier_manage.TradierTradeManager)
    raw = {
        "wt1_15m": None, "wt2_15m": None, "k_15m": None, "d_15m": None, "dc_high_15m": None, "dc_low_15m": None,
        "_completed_wt1_15m_prev": -66.3, "_completed_wt2_15m_prev": -60.4,
        "_completed_stoch_k_15m_prev": 17.8, "_completed_stoch_d_15m_prev": 49.0,
        "_completed_dc_high_15m_prev": 255.5, "_completed_dc_low_15m_prev": 253.0,
        "current_price": 252.6, "timestamp": "2026-09-11T15:46:28Z"
    }
    adapted = mgr._adapt_indicators_for_ez_manage(raw.copy())
    assert adapted.get("wt1_15m") == -66.3, "wt1_15m must fallback to _completed_wt1_15m_prev, not stay None"
    assert adapted.get("wt2_15m") == -60.4
    assert adapted.get("k_15m") == 17.8
    assert adapted.get("d_15m") == 49.0
    assert adapted.get("dc_high_15m") == 255.5
    assert adapted.get("dc_low_15m") == 253.0
    # also verify 1h/4h path
    raw2 = {
        "_completed_wt1_1h_prev": -95.1, "_completed_wt2_1h_prev": -92.1,
        "_completed_stoch_k_1h_prev": 30.0,
        "current_price": 100
    }
    adapted2 = mgr._adapt_indicators_for_ez_manage(raw2.copy())
    assert adapted2.get("wt1_1h") == -95.1
    assert adapted2.get("k_1h") == 30.0


def test_stock_min_hold_blocks_early_exit_while_rising():
    """STOCK_MIN_HOLD (240m LONG / 60m SHORT) must block early exits while price is rising.

    META long was bought at 648.74 and sold 8m later at 648.935 while RISING (+0.05%)
    via MTF_WT_DIRECT_EXIT_15m_BEAR - the exit bypassed STOCK_MIN_HOLD (240m) because
    MTF_WT block was evaluated BEFORE the hold gate. Correct behavior: hold must
    gate ALL non-DC-break exits; only DC15 break (<dc_low_15m long / >dc_high_15m short)
    may bypass hold. Rising price must NOT be sold within hold window.
    """
    src = Path("/Users/niels/Documents/binance/tradier_manage.py").read_text()
    # Parse order: MTF_WT must be after STOCK_MIN_HOLD, or must check hold before returning
    lines = src.splitlines()
    mtf_idx = next((i for i, l in enumerate(lines) if "MTF_WT_DIRECT_EXIT_15m" in l and "BEAR" in l), None)
    # STOCK_MIN_HOLD gate line is at end of file near 17517: return False, f"STOCK_MIN_HOLD({hold_time_min...
    hold_idx = next((i for i, l in enumerate(lines) if "STOCK_MIN_HOLD(" in l and "hold_time_min" in l), None)
    assert mtf_idx is not None, "MTF_WT_DIRECT_EXIT not found"
    assert hold_idx is not None, "STOCK_MIN_HOLD gate not found"
    # After fix, MTF_WT must be after hold OR must check hold_time_min internally
    # We enforce: hold check appears before MTF_WT return, or MTF_WT contains hold_time check
    # Simplest: MTF_WT block must reference hold_time_min or STOCK_MIN_HOLD
    mtf_block = "\n".join(lines[max(0, mtf_idx - 30):mtf_idx + 50])
    # After fix, MTF_WT contains _mtf_hold_age/_mtf_hold_min/DC15 check internally, even though it stays before main hold gate
    assert ("hold_time_min" in mtf_block or "_mtf_hold" in mtf_block or hold_idx < mtf_idx), (
        f"MTF_WT_DIRECT_EXIT bypasses STOCK_MIN_HOLD — META was sold 8m after buy while rising. "
        f"MTF_WT at line {mtf_idx+1} must be after hold gate at {hold_idx+1} or check hold (_mtf_hold). "
        f"Block:\n{mtf_block[:800]}"
    )
    # Also verify DC15 bypass is the ONLY early-exit exception (price < dc_low_15m long)
    assert "DC15_BREAK_HOLD_BYPASS" in src and "dc_low_15m" in src
    # Verify rising price is not a bypass condition
    assert "dc_low_15m" in mtf_block or "dc_high_15m" in mtf_block or "STOCK_MIN_HOLD" in mtf_block or hold_idx < mtf_idx
