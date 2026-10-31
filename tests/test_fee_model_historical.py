import json

import pytest

import fee_model


def _write_fills(root, rows):
    path = root / "data" / "exchange_fills" / "ang" / "test.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"rows": rows}))


def test_measure_execution_rates_converts_by_fill_minute_quote_and_liquidity(tmp_path):
    usdt_minute = 1799999940000
    usdc_minute = 1799999880000
    rows = [
        {
            "symbol": "ABCUSDT",
            "marginAsset": "USDT",
            "time": usdt_minute + 30000,
            "quoteQty": "100",
            "commission": "0.00006",
            "commissionAsset": "BNB",
            "maker": False,
            "orderId": 1,
            "type": "MARKET",
            "clientOrderId": "vd-market",
        },
        {
            "symbol": "ABCUSDT",
            "marginAsset": "USDT",
            "time": usdt_minute + 40000,
            "quoteQty": "200",
            "commission": "0.00006",
            "commissionAsset": "BNB",
            "maker": True,
            "orderId": 2,
            "type": "LIMIT",
            "clientOrderId": "od-limit",
        },
        {
            "symbol": "ABCUSDC",
            "marginAsset": "USDC",
            "time": usdc_minute + 10000,
            "quoteQty": "50",
            "commission": "0.00003",
            "commissionAsset": "BNB",
            "maker": False,
            "orderId": 3,
            "type": "MARKET",
            "clientOrderId": "vd-usdc",
        },
        {
            "symbol": "ABCUSDC",
            "marginAsset": "USDC",
            "time": usdc_minute + 20000,
            "quoteQty": "200",
            "commission": "0",
            "commissionAsset": "USDC",
            "maker": True,
            "orderId": 4,
            "type": "LIMIT",
            "clientOrderId": "od-free-maker",
        },
        {
            "symbol": "ABCUSDT",
            "marginAsset": "USDT",
            "time": 1790000000000,
            "quoteQty": "500",
            "commission": "1",
            "commissionAsset": "USDT",
            "maker": False,
        },
    ]
    _write_fills(tmp_path, rows)
    candles = {
        "BNBUSDT": {usdt_minute: (600.0, 610.0)},
        "BNBUSDC": {usdc_minute: (600.0, 620.0)},
    }
    result = fee_model.measure_execution_rates(
        window_days=7,
        base_path=tmp_path,
        now_ms=1800000000000,
        bnb_minute_candles=candles,
    )
    assert result["conversion_complete"] is True
    assert result["fills"] == 4
    assert result["fee_bearing_fills"] == 3
    assert result["by_quote_liquidity"]["USDT"]["TAKER"]["round_trip_pct_high"] == pytest.approx(0.0732)
    assert result["by_quote_liquidity"]["USDT"]["MAKER"]["round_trip_pct_high"] == pytest.approx(0.0366)
    assert result["by_quote"]["USDC"]["fills"] == 2
    assert result["by_quote"]["USDC"]["quote_notional"] == pytest.approx(250.0)
    assert result["by_quote_execution_type"]["USDT"]["MARKET"]["TAKER"]["fills"] == 1
    assert result["by_quote_client_prefix"]["USDT"]["vd"]["TAKER"]["fills"] == 1
    assert fee_model.round_trip_pct_for_execution(result, "USDT", "MARKET") == pytest.approx(0.0732)
    assert fee_model.round_trip_pct_for_execution(result, "USDT", "LIMIT_MAKER") == pytest.approx(0.0366)
    with pytest.raises(ValueError, match="actual maker/taker"):
        fee_model.round_trip_pct_for_execution(result, "USDT", "LIMIT")


def test_missing_historical_bnb_minute_fails_closed(tmp_path):
    minute = 1799999940000
    _write_fills(
        tmp_path,
        [
            {
                "symbol": "ABCUSDT",
                "marginAsset": "USDT",
                "time": minute + 10000,
                "quoteQty": "100",
                "commission": "0.00006",
                "commissionAsset": "BNB",
                "maker": False,
                "type": "MARKET",
            }
        ],
    )
    result = fee_model.measure_execution_rates(
        window_days=7,
        base_path=tmp_path,
        now_ms=1800000000000,
        bnb_minute_candles={"BNBUSDT": {}, "BNBUSDC": {}},
    )
    assert result["conversion_complete"] is False
    assert result["unconverted_bnb_fee_fills"] == 1
    assert result["by_quote"]["USDT"]["conversion_complete"] is False
    assert "round_trip_pct_high" not in result["by_quote_liquidity"]["USDT"]["TAKER"]
    with pytest.raises(ValueError, match="unverified"):
        fee_model.round_trip_pct_for_execution(result, "USDT", "MARKET")
