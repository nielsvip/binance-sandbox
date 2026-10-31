import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import paper_scenario_catalog as catalog


def _fixture(tmp_path):
    (tmp_path / "config.py").write_text('class Config:\n    EXAMPLE_ENABLED: bool = True\n    TEXT_TF: str = "15m"\n    LOW_3M_ENABLED: bool = True\n    PRIMARY_15M_ENABLED: bool = False\n    NUMBER_ENABLED: float = 1.5\n    USE_1M_3M_SIGNALS_ENABLED: bool = True\nLOW_5M_MODULE_SWITCH = True\nLIVE_5m_trading_ENABLED = True\ndef unrelated():\n    EXAMPLE_ENABLED = False\n')
    (tmp_path / "config_tradier.py").write_text('class TradierConfig:\n    PARITY_NON_VECTORIZABLE_SWITCHES = ("STOCK_GATE_ENABLED",)\n    STOCK_GATE_ENABLED: bool = True\n    LOW_3M_ENABLED: bool = True\n')
    (tmp_path / "ez_manage.py").write_text('_NON_VEC_KNOBS_EZ = frozenset(["EXAMPLE_ENABLED", "NUMBER_ENABLED", "USE_1M_3M_SIGNALS_ENABLED"])\nSUB15M_BLOCKED_SWITCHES = {"LOW_3M_ENABLED": False}\n')
    (tmp_path / "tradier_manage.py").write_text('SUB15M_BLOCKED_SWITCHES = {"STOCK_GATE_ENABLED": False, "LOW_3M_ENABLED": "OFF"}\n')


def test_ast_masks_defaults_and_venue_isolation(tmp_path):
    _fixture(tmp_path)
    result = catalog.inventory(tmp_path)
    scenarios = result["scenarios"]
    crypto_keys = {row["switch"] for row in scenarios if row["venue"] == "crypto"}
    stock_keys = {row["switch"] for row in scenarios if row["venue"] == "stocks"}
    assert "EXAMPLE_ENABLED" in crypto_keys
    assert "LOW_3M_ENABLED" in crypto_keys
    assert "STOCK_GATE_ENABLED" in stock_keys
    assert "STOCK_GATE_ENABLED" not in crypto_keys
    assert "EXAMPLE_ENABLED" not in stock_keys
    assert "PRIMARY_15M_ENABLED" not in crypto_keys
    assert "LOW_5M_MODULE_SWITCH" in crypto_keys
    assert "LIVE_5m_trading_ENABLED" in crypto_keys
    assert "USE_1M_3M_SIGNALS_ENABLED" in crypto_keys
    assert result["sources"]["ez_manage.py:_NON_VEC_KNOBS_EZ"]["count"] == 3
    crypto_low = next(row for row in scenarios if row["venue"] == "crypto" and row["switch"] == "LOW_3M_ENABLED")
    stock_low = next(row for row in scenarios if row["venue"] == "stocks" and row["switch"] == "LOW_3M_ENABLED")
    assert crypto_low["blocked_value"] is False
    example = next(row for row in scenarios if row["venue"] == "crypto" and row["switch"] == "EXAMPLE_ENABLED")
    assert example["default_value"] is True
    assert stock_low["blocked_value"] == "OFF"
    assert crypto_low["coverage_kind"] == stock_low["coverage_kind"] == "verified_mask"
    assert result["sources"]["ez_manage.py:SUB15M_BLOCKED_SWITCHES"]["line"] is not None
    assert len(result["file_sha256"]["config.py"]) == 64
    assert len([row for row in scenarios if row["kind"] == "baseline"]) == 4


def test_nonbool_defaults_are_captured_without_coercion(tmp_path):
    _fixture(tmp_path)
    rows = [row for row in catalog.inventory(tmp_path)["scenarios"] if row["switch"] == "NUMBER_ENABLED"]
    assert rows
    crypto_row = next(row for row in rows if row["venue"] == "crypto")
    assert crypto_row["default_known"] and crypto_row["default_value"] == 1.5
    assert crypto_row["default_kind"] == "non_boolean"


def test_unknown_default_is_explicit_and_ids_are_deterministic(tmp_path):
    _fixture(tmp_path)
    config = tmp_path / "config.py"
    config.write_text(config.read_text().replace("LOW_5M_MODULE_SWITCH = True", "    DYNAMIC_ENABLED: bool = make_value()\nLOW_5M_MODULE_SWITCH = True"))
    ez = tmp_path / "ez_manage.py"
    ez.write_text(ez.read_text().replace('"EXAMPLE_ENABLED",', '"EXAMPLE_ENABLED", "DYNAMIC_ENABLED",'))
    first = catalog.inventory(tmp_path)
    second = catalog.inventory(tmp_path)
    row = next(row for row in first["scenarios"] if row["switch"] == "EXAMPLE_ENABLED")
    again = next(row for row in second["scenarios"] if row["switch"] == "EXAMPLE_ENABLED")
    assert row["scenario_id"] == again["scenario_id"]
    assert all(not scenario["executable"] for scenario in first["scenarios"])
    dynamic = next(scenario for scenario in first["scenarios"] if scenario["switch"] == "DYNAMIC_ENABLED")
    assert dynamic["default_kind"] == "unresolved"
    assert first == second
