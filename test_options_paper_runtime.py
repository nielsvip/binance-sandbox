"""Focused contract tests for the paper-options runtime and safety lock."""
import asyncio
import logging
import logging.handlers
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

# Import the order gate without initializing the production API logger. The
# real module writes under ~/logs on macOS; this test intentionally has no
# network or external-file side effects.
api_stub = types.ModuleType("tradier_api")
api_stub.TradierAPIClient = object
sys.modules["tradier_api"] = api_stub

class _NoFileHandler(logging.Handler):
    def __init__(self, *args, **kwargs):
        super().__init__()

    def emit(self, record):
        pass

logging.handlers.RotatingFileHandler = _NoFileHandler

from config_tradier import TradierConfig
from tradier_options_analyzer import place_option_order


class _NoNetworkClient:
    _current_id = "must-not-be-used"

    async def _request(self, *args, **kwargs):  # pragma: no cover - failure is the test
        raise AssertionError("paper-only order path attempted a network request")


def test_live_options_are_locked_by_default():
    assert TradierConfig().OPTIONS_LIVE_TRADING_ENABLED is False


def test_option_order_is_blocked_without_network():
    result = asyncio.run(place_option_order(
        _NoNetworkClient(), "AAPL", "AAPL260918C00200000", "buy_to_open", 1, price=1.0
    ))
    assert result["status"] == "paper_only_blocked"


if __name__ == "__main__":
    test_live_options_are_locked_by_default()
    test_option_order_is_blocked_without_network()
    print("options paper runtime: PASS")
