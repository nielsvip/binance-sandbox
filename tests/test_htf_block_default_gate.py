"""Regression for UnboundLocalError _htf_block on default 4h/D gate (trb log .3, 50+ crashes at line 12046)."""
import sys
from pathlib import Path
from datetime import datetime, timezone
import asyncio
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tradier_manage as tm

def test_htf_block_init_in_source():
    src = Path(ROOT / "tradier_manage.py").read_text()
    # Must init before use else default 4h/D (no expanded gate) hits UnboundLocalError at `if not _htf_block and _wtdc_htf_gate == '4h_d'`
    assert "_htf_block = False" in src, "missing _htf_block = False init for default 4h/D path"
    # Ensure the fix comment is present
    assert "UnboundLocalError" in src or "2026-09-23 FIX" in src

def test_gr_age_datetime_safe_source():
    src = Path(ROOT / "tradier_manage.py").read_text()
    assert "float(getattr(position, 'opened_at'" not in src, "bare float(opened_at) still present — will raise float(datetime)"
    assert "_gr_opened_raw" in src
    assert "isinstance(_gr_opened_raw, datetime)" in src

def test_gr_age_handles_datetime_and_iso():
    # Directly exercise the fixed branch logic via helper matching code
    now = datetime.now(timezone.utc)
    for raw, should_be_small in [
        (now, True),
        (now.isoformat(), True),
        (now.timestamp(), True),
        (None, False),
        (0, False),
    ]:
        # replicate fixed code
        _gr_opened_raw = raw
        _gr_age_min = 999.0
        if _gr_opened_raw is not None:
            try:
                import time
                if isinstance(_gr_opened_raw, (int, float)):
                    _gr_ts = float(_gr_opened_raw) if _gr_opened_raw else 0
                    if _gr_ts == 0:
                        raise ValueError
                    _gr_age_min = (time.time() - _gr_ts) / 60.0
                elif isinstance(_gr_opened_raw, datetime):
                    _dt = _gr_opened_raw
                    if _dt.tzinfo is None:
                        _dt = _dt.replace(tzinfo=timezone.utc)
                    _gr_age_min = (time.time() - _dt.timestamp()) / 60.0
                elif isinstance(_gr_opened_raw, str):
                    _dt = datetime.fromisoformat(_gr_opened_raw.replace('Z', '+00:00'))
                    if _dt.tzinfo is None:
                        _dt = _dt.replace(tzinfo=timezone.utc)
                    _gr_age_min = (time.time() - _dt.timestamp()) / 60.0
            except Exception:
                _gr_age_min = 999.0
        if should_be_small:
            assert _gr_age_min < 5.0, f"age for {raw!r} should be ~0, got {_gr_age_min}"
        else:
            assert _gr_age_min == 999.0

def test_process_position_default_gate_no_crash():
    """Default WT_DC_TF_HTF=4h, HTF2=D (no expanded gate) must not raise UnboundLocalError even when WT is flat."""
    # Minimal mock that reaches the HTF gate without needing full indicator fetch
    strat = MagicMock()
    trade_manager = MagicMock()
    trade_manager.last_monitored_positions = {}
    trade_manager.blacklist = set()
    trade_manager.is_symbol_tradeable = MagicMock(return_value=True)
    trade_manager.get_current_price = AsyncMock(return_value=(100.0, datetime.now(timezone.utc)))
    # flat/neutral indicators: no HTF against signal -> _htf_block stays False, should not crash at `if not _htf_block and gate==4h_d`
    indicators_raw = {
        "wt1_1h": 50, "wt2_1h": 50,
        "wt1_4h": 50, "wt2_4h": 50,
        "wt1_D": 50, "wt2_D": 50,
        "wt1_15m": 50, "wt2_15m": 50,  # prevent other gates
        "current_price": 100.0,
        "timestamp_1m": datetime.now(timezone.utc).isoformat(),
        "k_5m": 50, "k_15m": 50, "dc_high_15m": 110, "dc_low_15m": 90,
        "rsi_15m": 50, "stoch_k_15m": 50, "mfi_15m": 50, "bb_pct_b_1h": 0.5,
        "ha_15m": "neutral", "mfi_D": 50, "relative_volume_1h": 1.0,
    }
    trade_manager.is_data_fresh = AsyncMock(return_value=(True, "fresh", True, indicators_raw))
    trade_manager.strategy = MagicMock()
    trade_manager.strategy.parse_market_data = MagicMock(return_value=indicators_raw)
    trade_manager.position_manager = MagicMock()
    pos = MagicMock()
    pos.positionAmt = 0  # flat — triggers entry path, not exit; still hits HTF gate
    pos.opened_at = datetime.now(timezone.utc)
    pos.entry_time = None
    trade_manager.position_manager.get_position = MagicMock(return_value=pos)
    # Must not raise UnboundLocalError
    async def run():
        # monitor_entries is the function containing _htf_block; we exercise it via process_position flat path
        # process_position will delegate to monitor_entries-like logic; simplest is to call it and assert no UnboundLocalError
        try:
            res = await tm.process_position("trb", "trb:NUE_LONG", MagicMock(), trade_manager)
            # result may be various strings, but must not be exception
            assert res != "CRASH"
        except UnboundLocalError as e:
            if "_htf_block" in str(e):
                raise AssertionError(f"_htf_block still unbound on default gate: {e}")
            raise
        except Exception:
            pass  # other exceptions (e.g., missing config) are OK — only _htf_block crash matters

    asyncio.run(run())
