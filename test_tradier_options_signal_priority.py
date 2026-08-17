import logging
import logging.handlers
import sys
import types
from dataclasses import dataclass
from pathlib import Path


BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

api_stub = types.ModuleType("tradier_api")
api_stub.TradierAPIClient = object
sys.modules["tradier_api"] = api_stub


class _NoFileHandler(logging.Handler):
    def __init__(self, *args, **kwargs):
        super().__init__()

    def emit(self, record):
        pass


logging.handlers.RotatingFileHandler = _NoFileHandler

import tradier_options_agent as agent


@dataclass
class _Signal:
    symbol: str
    conviction: float


def test_prioritize_signals_prefers_scanner_backed_liquid_symbol():
    signals = [_Signal("CIEN", 100), _Signal("GOOGL", 56)]
    scan_data = {
        "outliers": [
            {
                "symbol": "GOOGL",
                "type": "call",
                "recommendation": "BUY_CHEAP_CALL",
                "dte": 72,
                "open_interest": 55385,
                "score": 76,
            }
        ]
    }
    ranked = agent._prioritize_signals_for_chain_fetch(signals, scan_data, "call")
    assert [sig.symbol for sig in ranked[:2]] == ["GOOGL", "CIEN"]


def test_scanner_liquidity_priority_filters_non_qualifying_outliers():
    scan_data = {
        "outliers": [
            {
                "symbol": "CAT",
                "type": "call",
                "recommendation": "BUY_CHEAP_CALL",
                "dte": 72,
                "open_interest": 16,
                "score": 78,
            },
            {
                "symbol": "TSLA",
                "type": "call",
                "recommendation": "BUY_CHEAP_CALL",
                "dte": 23,
                "open_interest": 1525,
                "score": 76,
            },
            {
                "symbol": "GOOGL",
                "type": "call",
                "recommendation": "BUY_CHEAP_CALL",
                "dte": 72,
                "open_interest": 55385,
                "score": 76,
            },
        ]
    }
    priority = agent._scanner_liquidity_priority(scan_data, "call")
    assert priority == {"GOOGL": 76}
