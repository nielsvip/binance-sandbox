from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import tradier_manage as T


class _PriceManager:
    async def get_current_price(self, symbol):
        return 100.0, None


def _original_calculator():
    path = Path(__file__).resolve().parents[1] / "backups" / "before_tradier_dd_bounce_sizing_factor_20261031T025400Z.py"
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "StockStrategy":
            method = next(
                item
                for item in node.body
                if isinstance(item, ast.AsyncFunctionDef)
                and item.name == "calculate_quantity_complex_extended"
            )
            break
    namespace = dict(T.__dict__)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["calculate_quantity_complex_extended"]


def _strategy():
    strategy = object.__new__(T.StockStrategy)
    strategy.trade_manager = _PriceManager()
    strategy.config = T.config
    strategy.exceptions = set()
    strategy.limit_exception_order = 10000.0
    strategy.limit_normal_order = 5000.0
    strategy.limit_total_pos = 10000.0
    return strategy


def test_refactored_full_calculator_matches_saved_original_for_augments(monkeypatch):
    original = _original_calculator()
    positions = SimpleNamespace(positionAmt=2.0)
    indicators = {
        "current_price": 100.0,
        "ha_5m": "neutral",
        "k_15m": 65.0,
        "d_15m": 55.0,
        "k_1h": 82.0,
        "k_5m": 90.0,
        "dc_low_1m": 95.0,
        "dc_high_1m": 105.0,
        "dc_low_5m": 90.0,
        "dc_high_5m": 110.0,
        "dc_low_15m": 90.0,
        "dc_high_15m": 110.0,
        "dc_low_1h": 80.0,
        "dc_high_1h": 120.0,
        "dc_low_4h": 70.0,
        "dc_high_4h": 130.0,
        "dc_low_D": 60.0,
        "dc_high_D": 140.0,
        "wt1_5m": 5.0,
        "wt2_5m": 4.0,
        "wt1_1h": 7.0,
        "wt2_1h": 5.0,
        "mfi_1h": 45.0,
        "mfi_15m": 40.0,
        "rsi_5m": 25.0,
        "rsi_1h": 30.0,
        "rel_vol_5m": 1.4,
        "rel_vol_1h": 1.6,
    }
    original_dt = T.datetime
    for local in (
        datetime(2026, 10, 21, 10, 59, tzinfo=ZoneInfo("America/New_York")),
        datetime(2026, 10, 21, 11, 0, tzinfo=ZoneInfo("America/New_York")),
        datetime(2026, 10, 21, 14, 0, tzinfo=ZoneInfo("America/New_York")),
        datetime(2026, 10, 21, 15, 31, tzinfo=ZoneInfo("America/New_York")),
    ):
        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = local.astimezone(timezone.utc)
                return value.astimezone(tz) if tz is not None else value.replace(tzinfo=None)

        monkeypatch.setattr(T, "datetime", _FixedDatetime)
        original.__globals__["datetime"] = _FixedDatetime
        for is_long, side in ((True, "LONG"), (False, "SHORT")):
            current = asyncio.run(
                _strategy().calculate_quantity_complex_extended(
                    "AAPL", "AUGMENT", side, 3.0, indicators, positions, account_key="trb"
                )
            )
            legacy = asyncio.run(
                original(
                    _strategy(), "AAPL", "AUGMENT", side, 3.0, indicators, positions,
                    account_key="trb"
                )
            )
            assert current == legacy
    monkeypatch.setattr(T, "datetime", original_dt)
