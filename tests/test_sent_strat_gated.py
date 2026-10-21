"""USER 2026-10-21 EXACT-PARITY: StockSentimentStrategy has no vector model and no
per-sym board keys, so it must not trade. Pins the kill switch default and the
run_loop gate order (gate before the trading loop). Source-level pin, same
spirit as test_vec_approval_is_the_only_door.py: no live stack import."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_sent_strat_default_off():
    import config_tradier as C

    assert C.TradierConfig.STOCK_SENTIMENT_STRATEGY_ENABLED is False


def test_sent_strat_gate_before_loop():
    src = (ROOT / "tradier_manage.py").read_text()
    i_cls = src.index("class StockSentimentStrategy")
    i_run = src.index("async def run_loop", i_cls)
    i_while = src.index("while self.trade_manager.running", i_run)
    i_gate = src.index("STOCK_SENTIMENT_STRATEGY_ENABLED", i_run)
    assert i_run < i_gate < i_while, "gate must sit between loop start and trading loop"
    tail = src[i_gate:i_while]
    assert "return" in tail, "disabled loop must return without trading"
