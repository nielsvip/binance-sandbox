"""Regression contracts for the GUI-lab Binance catalog and combiner lanes."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parent / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import gui_lab_binance_futures as futures  # noqa: E402
import gui_lab_manual_v8_runner as runner  # noqa: E402
import gui_lab_supervisor as supervisor  # noqa: E402


class _Response:
    def __init__(self, payload: dict):
        self._stream = io.BytesIO(json.dumps(payload).encode())

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self._stream.read()


def test_configured_tradifi_contract_is_active_and_base_alias_resolves(monkeypatch, tmp_path):
    """MUUSDT must not be discarded merely because Binance calls it TradFi."""
    configured = tmp_path / "symbols.json"
    configured.write_text(json.dumps(["MUUSDT", "BTCUSDT"]))
    monkeypatch.setattr(futures, "CONFIGURED_SYMBOLS_PATH", configured)
    monkeypatch.setattr(futures, "CACHE", tmp_path / "catalog.json")
    monkeypatch.setattr(
        futures.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response({
            "symbols": [
                {"symbol": "MUUSDT", "status": "TRADING", "contractType": "TRADIFI_PERPETUAL"},
                {"symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL"},
                {"symbol": "MUSDT", "status": "TRADING", "contractType": "PERPETUAL"},
            ],
        }),
    )

    catalog = futures.futures_catalog(refresh=True)

    assert catalog["configured_unavailable_symbols"] == []
    assert catalog["configured_active_symbols"] == ["BTCUSDT", "MUUSDT"]
    assert futures.resolve_binance_symbol("MU", catalog) == "MUUSDT"
    assert futures.resolve_binance_symbol("MUUSDT", catalog) == "MUUSDT"
    assert futures.resolve_binance_symbol("MUSDT", catalog) == "MUSDT"


def test_combiner_starts_each_symbol_with_a_distinct_seed(monkeypatch):
    """A free slot must not repeatedly default to MU/NVDA's first recipe."""
    declared = {}
    for _suite_id, _label, overrides in (*runner.COMBINER_EXIT_SUITES, *runner.COMBINER_REENTRY_SUITES):
        for param, value in overrides.items():
            declared[param] = {"test_values": [value]}
    monkeypatch.setattr(runner, "catalog_by_param", lambda: declared)

    first_ids = [
        runner.symbol_combiner_templates([], symbol)[0]["id"]
        for symbol, _side in runner.AUTONOMOUS_KEYS
    ]

    assert len(first_ids) == len(set(first_ids))
    assert all(recipe.startswith("repair:") for recipe in first_ids)


def test_trade_starved_beam_adds_reentry_before_another_exit(monkeypatch):
    declared = {}
    for _suite_id, _label, overrides in (*runner.COMBINER_EXIT_SUITES, *runner.COMBINER_REENTRY_SUITES):
        for param, value in overrides.items():
            declared[param] = {"test_values": [value]}
    monkeypatch.setattr(runner, "catalog_by_param", lambda: declared)
    seed = {
        "run_id": "mu-first",
        "source": "autonomous_direct_v8_combination",
        "state": "AUDITED",
        "accounting_verified": True,
        "capital_normalized": True,
        "combiner_template_id": "repair:bb_frozen_stop+breakout_reentry",
        "spec": {
            "symbol": "MU", "months": 12,
            "capital_deployment_policy": runner.CAPITAL_DEPLOYMENT_POLICY,
            "manual_override_params": ["BB_FROZEN_STOP_ENABLED", "REENTRY_BREAKOUT_ENABLED"],
            "overrides": {"BB_FROZEN_STOP_ENABLED": True, "REENTRY_BREAKOUT_ENABLED": True},
        },
        "metrics": {
            "gain_pct": -4.4, "bh_gain_pct": 599.1, "delta_vs_bh_pct": -603.5,
            "max_dd_pct": 5.2, "tim_pct": 0.2, "closes_per_month": 0.16,
        },
    }

    first = runner.symbol_combiner_templates([seed], "MU")[0]

    assert first["id"].endswith("+reentry_2")
    assert first["overrides"]["REENTRY_2_ENABLED"] is True


def test_combiner_fairness_does_not_give_mu_a_second_live_slot():
    rows = [
        {
            "source": "autonomous_direct_v8_combination",
            "state": "RUNNING",
            "spec": {
                "symbol": "MU",
                "capital_deployment_policy": runner.CAPITAL_DEPLOYMENT_POLICY,
            },
        },
        {
            "source": "autonomous_direct_v8_combination",
            "state": "RUNNING",
            "spec": {
                "symbol": "NVDA",
                "capital_deployment_policy": runner.CAPITAL_DEPLOYMENT_POLICY,
            },
        },
    ]

    order = [symbol for symbol, _side in runner.combiner_symbol_order(rows)]

    assert order[:4] == ["VT", "TTD", "ACN", "AAPL"]
    assert order[-2:] == ["MU", "NVDA"]


def test_negative_combination_is_forensic_not_published():
    assert supervisor.positive_combination_result({
        "gain_per_mo_pct": -1.0, "delta_vs_bh_per_mo_pct": 2.0,
    }) is False
    assert supervisor.positive_combination_result({
        "gain_per_mo_pct": 1.0, "delta_vs_bh_per_mo_pct": 0.1,
    }) is True
