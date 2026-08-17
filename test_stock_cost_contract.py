import ast
import importlib
import json
from pathlib import Path
import re

import pytest

from backtest_v8_harness import accumulate_partial_close
from config_tradier import TradierConfig
from tools import results_dashboard_lib as dashboard
from tools import switch_matrix_digest as matrix_digest


def _engine_function(name, namespace=None):
    """Load one pure engine helper without importing the CLI-style engine."""
    tree = ast.parse(Path("backtest_v8_engine.py").read_text())
    node = next(
        item
        for item in tree.body
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
        and item.name == name
    )
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    scope = dict(namespace or {})
    exec(compile(module, "backtest_v8_engine.py", "exec"), scope)
    return scope[name]


def test_ordinary_asset_cost_contract_is_five_bps_stocks_eight_bps_crypto():
    cost_for_sym = _engine_function(
        "_round_trip_cost_for_sym",
        {"config": importlib.import_module("config")},
    )
    assert TradierConfig.ROUND_TRIP_COST_PCT == pytest.approx(0.05)
    assert cost_for_sym("MU") == pytest.approx(0.05)
    assert cost_for_sym("BTCUSDC") == pytest.approx(0.08)


def test_stock_research_cli_defaults_do_not_restore_fourteen_bps_contract():
    stale = []
    for path in Path("tools").glob("*.py"):
        source = path.read_text()
        if re.search(r"commission-bps.+default=5\.0", source):
            stale.append(f"{path}:commission")
        if re.search(r"(?<!commission-)cost-bps.+default=5\.0", source):
            stale.append(f"{path}:cost")
        if re.search(r"slippage-bps.+default=2\.0", source):
            stale.append(f"{path}:slippage")
    assert stale == []


def test_partial_closes_charge_five_bps_once_over_total_closed_entry_basis():
    state = {"qty": 10.0, "entry_price": 100.0}
    assert accumulate_partial_close(state, 4.0, 110.0, 0.05, True) is None
    summary = accumulate_partial_close(state, 6.0, 120.0, 0.05, True)

    assert summary["pnl_dollars_gross"] == pytest.approx(160.0)
    assert summary["pnl_dollars"] == pytest.approx(159.5)
    assert summary["pnl_pct_gross"] - summary["pnl_pct"] == pytest.approx(0.05)


def test_reentry_is_a_new_round_trip_but_no_closed_share_is_double_charged():
    cost_for_sym = _engine_function(
        "_round_trip_cost_for_sym",
        {"config": importlib.import_module("config")},
    )
    compute_trade_pnl = _engine_function(
        "_compute_trade_pnl",
        {"_round_trip_cost_for_sym": cost_for_sym},
    )
    events = [
        {
            "position_key": "trb:MU_LONG",
            "action": "OPEN",
            "position_side": "LONG",
            "symbol": "MU",
            "price": 100.0,
            "quantity": 10.0,
        },
        {
            "position_key": "trb:MU_LONG",
            "action": "CLOSE",
            "position_side": "LONG",
            "symbol": "MU",
            "price": 110.0,
            "quantity": 10.0,
        },
        {
            "position_key": "trb:MU_LONG",
            "action": "REENTRY",
            "position_side": "LONG",
            "symbol": "MU",
            "price": 90.0,
            "quantity": 10.0,
        },
        {
            "position_key": "trb:MU_LONG",
            "action": "CLOSE",
            "position_side": "LONG",
            "symbol": "MU",
            "price": 100.0,
            "quantity": 10.0,
        },
    ]
    compute_trade_pnl(events)
    closes = [row for row in events if row["action"] == "CLOSE"]

    assert [row["round_trip_cost_pct"] for row in closes] == pytest.approx(
        [0.05, 0.05]
    )
    assert sum(
        row["pnl_dollars_gross"] - row["pnl_dollars"] for row in closes
    ) == pytest.approx(0.95)


def test_stock_monitor_rejects_unscoped_and_historical_campaign_records(
    tmp_path, monkeypatch
):
    gating = tmp_path / "gating.jsonl"
    gating.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {
                    "mode": "stock",
                    "key": "OLD_LONG",
                    "campaign": "stocks_baseline_v2_s4h",
                    "real_baseline_sharpe": 4.0,
                    "action": "RESCUED",
                    "ts": 1,
                },
                {
                    "mode": "stock",
                    "key": "UNSCOPED_LONG",
                    "real_baseline_sharpe": 5.0,
                    "action": "RESCUED",
                    "ts": 2,
                },
                {
                    "mode": "stock",
                    "key": "MU_LONG",
                    "campaign": "stocks_repaired_20260725_c2",
                    "matrix_contract_version": (
                        "tradier-matrix-exec-c4-20260729"
                    ),
                    "real_baseline_sharpe": 1.0,
                    "real_trades": 40,
                    "action": "CURRENT",
                    "ts": 3,
                },
            )
        )
        + "\n"
    )
    rescues = tmp_path / "rescues.jsonl"
    rescues.write_text("")
    vec = tmp_path / "vec.json"
    vec.write_text(
        json.dumps(
            {
                "OLD_LONG": {"score": 1},
                "MU_LONG": {
                    "campaign": "stocks_repaired_20260725_c2",
                    "matrix_contract_version": (
                        "tradier-matrix-exec-c4-20260729"
                    ),
                    "score": 1,
                },
            }
        )
    )
    active = tmp_path / "active.json"
    active.write_text("{}")
    monkeypatch.setattr(dashboard, "GATING_CORRECTIONS", gating)
    monkeypatch.setattr(dashboard, "RESCUES", rescues)
    monkeypatch.setitem(dashboard.VEC_BASELINES, "stock", vec)
    monkeypatch.setitem(dashboard.ACTIVE_CONFIG, "stock", active)

    report = dashboard.build_report("stock")

    assert [row["key"] for row in report["all_confirmed"]] == ["MU_LONG"]
    assert report["vec_screened_keys"] == 1
    assert report["excluded_legacy_records"] == 2
    assert report["campaign_scope"] == {
        "campaigns": ["stocks_repaired_20260725_c2"],
        "exact_contracts": ["tradier-matrix-exec-c4-20260729"],
    }


def test_active_digest_does_not_render_legacy_stock_rankings(monkeypatch):
    monkeypatch.setattr(
        dashboard,
        "build_report",
        lambda mode: {"winners": []},
    )
    monkeypatch.setattr(
        "tools.results_digest_email._parity_sections",
        lambda: "<p>parity</p>",
    )
    section = importlib.import_module(
        "tools.results_digest_email"
    )._gainmo_sections()

    assert "Historical stock diagnostics" in section
    assert "QUARANTINED / NOT RANKED" in section
    assert "STOCKS B&amp;H WINNERS" not in section
    assert "TOP-20 KEYS" not in section
    assert "GAINMO test queue" not in section


def test_matrix_verdict_keeps_sub_2x_result_gray():
    base = {
        "validation_status": "PASS",
        "reentry_violations": 0,
        "inert": 0,
        "trades": 10,
        "gain_per_mo": 1.5,
        "delta_gain_mo_vs_bh": 0.5,
        "time_in_mkt_pct": 70.0,
        "param": "MTF_DC_REJECT_EXIT_ENABLED",
    }
    assert (
        matrix_digest.verdict(base)
        == "GRAY: ABOVE B&H BUT BELOW 2X TARGET"
    )
    base["gain_per_mo"] = 2.1
    base["delta_gain_mo_vs_bh"] = 1.1
    assert matrix_digest.verdict(base) == "MEETS 2X B&H RESEARCH BAR"
