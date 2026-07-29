import json
from pathlib import Path

import pytest

from stock_v8_override_contract import establish_stock_v8_override


def test_explicit_stock_override_sets_both_guards_and_hash(tmp_path):
    override = tmp_path / "override.json"
    override.write_text(json.dumps({"WT_3M_FORCE_OPEN_ENABLED": True}))
    env = {}
    receipt = establish_stock_v8_override(env, override)
    assert env["V8_OVERRIDE_FILE"] == str(override)
    assert env["V8_SWEEP_MODE"] == "1"
    assert env["V8_BACKTEST_OVERRIDE_PRECEDENCE"] == "1"
    assert receipt["kind"] == "EXPLICIT_STOCK_SWEEP_OVERRIDE"
    assert receipt["explicit_keys"] == 1
    assert len(receipt["override_sha256"]) == 64


def test_empty_override_preserves_accepted_overlays():
    env = {}
    receipt = establish_stock_v8_override(env, "")
    assert env == {}
    assert receipt["kind"] == "BASELINE_OVERLAY_PRESERVED"


@pytest.mark.parametrize("payload", ["[]", "null", "\"string\""])
def test_non_object_override_fails_closed(tmp_path, payload):
    override = tmp_path / "bad.json"
    override.write_text(payload)
    with pytest.raises(RuntimeError):
        establish_stock_v8_override({}, override)


def test_every_exact_stock_override_launcher_has_central_or_launcher_belt():
    root = Path(__file__).parent
    exempt = {
        "backtest_v8_engine.py",  # central enforcement site
        "tradier_manage.py",  # override consumer, not launcher
        "config.py",
        "config_tradier.py",
    }
    launchers = []
    for path in root.rglob("*.py"):
        if ".git" in path.parts or path.name.startswith("test_") or path.name in exempt:
            continue
        text = path.read_text(errors="replace")
        if "V8_OVERRIDE_FILE" in text and "backtest_v8_engine.py" in text:
            launchers.append(path)
    assert launchers
    # All such launchers are protected centrally by the engine. Canonical
    # launchers should additionally call the shared helper where practical.
    central = (root / "backtest_v8_engine.py").read_text()
    assert "establish_stock_v8_override(" in central
    assert "V8_OVERRIDE_CONTRACT:" in central
    assert any(
        "establish_stock_v8_override(" in path.read_text(errors="replace")
        for path in launchers
    )


def test_override_helper_is_fingerprinted_and_mutation_changes_signature(
    monkeypatch, tmp_path
):
    from tools import persym_baseline_campaign as campaign

    assert "stock_v8_override_contract.py" in campaign.MATRIX_CONTRACT_FILES
    helper = tmp_path / "stock_v8_override_contract.py"
    helper.write_text("version = 1\n")
    monkeypatch.setattr(campaign, "SBX", tmp_path)
    monkeypatch.setattr(
        campaign, "MATRIX_CONTRACT_FILES", ["stock_v8_override_contract.py"]
    )
    before = campaign._matrix_process_source_signature()
    helper.write_text("version = 2\n")
    after = campaign._matrix_process_source_signature()
    assert before != after
