"""Sim-time market sessions (USER 2026-10-24 backtest fidelity).

The stocks live leg must evaluate RTH in SIM bar time, not wall-clock: a parity
run launched on a weekend otherwise measures a permanently closed market (live 0
trips on every sym_side) while the vector — which models no session gate —
trades. Live never sets V8_SIM_TS, so live behavior is unchanged.
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WED_RTH = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc).timestamp()  # Wed 10:00 ET
SAT = datetime(2026, 10, 24, 14, 0, tzinfo=timezone.utc).timestamp()  # Sat 10:00 ET
WED_NIGHT = datetime(2026, 10, 21, 22, 0, tzinfo=timezone.utc).timestamp()  # Wed 18:00 ET


def test_sim_time_drives_session():
    import tradier_manage as TM

    old = os.environ.pop("V8_SIM_TS", None)
    try:
        assert TM.is_regular_trading_hours(WED_RTH) is True
        assert TM.is_regular_trading_hours(SAT) is False
        assert TM.is_regular_trading_hours(WED_NIGHT) is False
        os.environ["V8_SIM_TS"] = str(WED_RTH)
        assert TM.is_regular_trading_hours() is True
        os.environ["V8_SIM_TS"] = str(SAT)
        assert TM.is_regular_trading_hours() is False
    finally:
        if old is None:
            os.environ.pop("V8_SIM_TS", None)
        else:
            os.environ["V8_SIM_TS"] = old


def test_backtest_sets_sim_ts_per_bar():
    src = Path("backtest_v12_engine.py").read_text()
    assert 'os.environ["V8_SIM_TS"]' in src
