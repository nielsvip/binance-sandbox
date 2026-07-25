from __future__ import annotations

import sys
import types
from datetime import datetime, timezone

import pytest

# This suite exercises report identity/direction contracts, not indicator math.
# Avoid importing the live indicator stack, which initializes production log
# handlers at module import time.
_ez_indicators = types.ModuleType("ez_indicators")
for _name in (
    "adx_value",
    "atr_series",
    "bb_features",
    "choppiness_index",
    "donchian",
    "ema_pair",
    "ha_streak_count",
    "heikin_ashi",
    "macd_values",
    "mfi_value",
    "relative_volume",
    "rsi_series",
    "rsi_value",
    "sma_pair",
    "stoch_rsi",
):
    setattr(_ez_indicators, _name, lambda *args, **kwargs: None)
sys.modules.setdefault("ez_indicators", _ez_indicators)

import bitget_trader_scraper as scraper
import trader_research_agent as agent
from trader_deep_analyzer import (
    TradeRecord,
    TrackerLogIngester,
    extract_patterns,
)


def _trade(
    side: str,
    index: int,
    *,
    symbol: str = "TESTUSDT",
    winner: bool = True,
) -> TradeRecord:
    entry = 100.0
    move = 2.0 if winner else -1.0
    exit_ = entry + move if side == "LONG" else entry - move
    pnl_pct = (move / entry) * 100.0
    trade = TradeRecord(
        f"account-{index % 3}",
        symbol,
        side,
        entry,
        exit_,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 2, tzinfo=timezone.utc),
        pnl_pct,
        pnl_pct,
        1.0,
        100.0,
    )
    trade.indicators = {
        "adx_14": 30.0 if index % 2 else 10.0,
        "above_sma200": 1 if side == "LONG" else 0,
        "feature_a": float(index),
        "feature_b": float(index % 7),
        "feature_c": float(index % 11),
        "feature_d": float(index % 13),
    }
    return trade


def test_unknown_position_side_is_rejected_fail_closed():
    with pytest.raises(ValueError, match="invalid position_side"):
        _trade("SIDEWAYS", 0)
    assert TrackerLogIngester()._parse_entry(
        {"symbol": "BTCUSDT", "side": "SIDEWAYS"}
    ) is None
    assert scraper._parse_order(
        {
            "symbol": "BTCUSDT",
            "posSide": "SIDEWAYS",
            "openTime": "2026-01-01T00:00:00Z",
        },
        "account",
    ) is None


def test_patterns_are_trained_and_labeled_side_pure():
    trades = []
    for side in ("LONG", "SHORT"):
        for index in range(80):
            trades.append(_trade(side, index, winner=index >= 20))
    patterns = extract_patterns(trades, min_samples_leaf=5)
    assert patterns
    assert {pattern["position_side"] for pattern in patterns} <= {"LONG", "SHORT"}
    assert all(pattern["side_pure"] for pattern in patterns)
    assert all(
        set(pattern["side_counts"]) == {pattern["position_side"]}
        for pattern in patterns
    )


def test_scope_and_regime_summaries_never_pool_sides():
    trades = [_trade("LONG", i, winner=True) for i in range(40)]
    trades += [_trade("SHORT", i, winner=False) for i in range(40)]
    scope = agent._scope_summary(trades)
    assert scope["side_isolation"] is True
    assert scope["by_position_side"]["LONG"]["equal_weight_mean_return_pct"] == 2.0
    assert scope["by_position_side"]["SHORT"]["equal_weight_mean_return_pct"] == -1.0
    regimes = agent._regime_side_summary(trades)
    assert regimes
    assert all(
        key.startswith(("ALL_SYMBOLS:LONG:", "ALL_SYMBOLS:SHORT:"))
        for key in regimes
    )
    assert all(value["position_side"] in ("LONG", "SHORT") for value in regimes.values())


def test_findings_lead_with_explicit_scope_and_side_summaries():
    trades = [_trade("LONG", i, winner=True) for i in range(40)]
    trades += [_trade("SHORT", i, winner=False) for i in range(40)]
    findings = agent.compare_to_our_system(
        {
            "trades": trades,
            "patterns": [],
            "indicator_analysis": {},
            "health": {},
            "regime_data": {},
        }
    )
    assert findings[0].startswith("SCOPE:")
    assert "ALL_SYMBOLS LONG" in findings[1]
    assert "ALL_SYMBOLS SHORT" in findings[2]
    assert any(item.startswith("REGIME_SCOPE: ALL_SYMBOLS LONG") for item in findings)
    assert any(item.startswith("REGIME_SCOPE: ALL_SYMBOLS SHORT") for item in findings)


def test_email_does_not_truncate_side_provenance(monkeypatch, tmp_path):
    sent = {}
    module = types.ModuleType("morning_email")

    def send_email(body, subject):
        sent["body"] = body
        sent["subject"] = subject

    module.send_email = send_email
    monkeypatch.setitem(sys.modules, "morning_email", module)
    findings = ["SCOPE: explicit", "SIDE_SCOPE: LONG", "SIDE_SCOPE: SHORT"]
    findings.extend(f"item-{index}" for index in range(20))
    agent.email_digest(tmp_path / "report.md", findings)
    assert "SCOPE: explicit" in sent["body"]
    assert "SIDE_SCOPE: LONG" in sent["body"]
    assert "SIDE_SCOPE: SHORT" in sent["body"]
    assert "item-19" in sent["body"]


def test_invalid_identity_and_malformed_numeric_rows_fail_closed(tmp_path):
    class Invalid:
        trader_id = "account"
        symbol = ""
        side = "LONG"
        position_side = "LONG"

    with pytest.raises(ValueError, match="identity contract failed"):
        agent._scope_summary([Invalid()])

    csv_path = tmp_path / "bad.csv"
    csv_path.write_text(
        "trader_id,symbol,side,entry_price,exit_price,pnl,pnl_pct\n"
        "a,BTCUSDT,LONG,not-a-number,101,1,1\n"
    )
    from trader_deep_analyzer import CSVIngester

    assert CSVIngester().ingest(str(csv_path)) == []
