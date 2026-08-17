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


def test_override_contract_receipt_is_whitelisted_in_sweep_output():
    source = (Path(__file__).parent / "backtest_v8_engine.py").read_text()
    allow_block = source.split("_ALLOW_PREFIXES = (", 1)[1].split(")", 1)[0]
    assert '"V8_OVERRIDE_CONTRACT"' in allow_block


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
    candidates = sorted(root.glob("*.py")) + sorted((root / "tools").glob("*.py"))
    for path in candidates:
        if path.name.startswith("test_") or path.name in exempt:
            continue
        text = path.read_text(errors="replace")
        if "V8_OVERRIDE_FILE" in text and "backtest_v8_engine.py" in text:
            launchers.append(path)
    assert launchers
    # S1 can carry additional research launchers that are not present in a
    # developer checkout.  An exact-list assertion made a safe superset fail;
    # require the canonical launchers while checking every discovered launcher
    # through the central engine contract below.
    canonical = {
        "OPUS_VOMIT.py",
        "auto_promote.py",
        "backtest_v8_parallel.py",
        "backtest_v8_sweep.py",
        "expand_tier2_sweepset.py",
        "ez_manage.py",
        "ppl_orchestrator.py",
        "sweep_coordinator.py",
        "sweep_quality_sniper.py",
        "sweep_reentry_blocks.py",
        "tools/band_ladder_sweep.py",
        "tools/compare_engines.py",
        "tools/patch_ofat_tier_aware.py",
        "tools/persym_baseline_campaign.py",
        "tools/quick_reduce_trap_ab.py",
        "tools/run_v8_research_ladder_replay.py",
        "tools/run_v8_research_short_guard_replay.py",
        "tools/run_v8_research_top_exit_replay.py",
        "tools/run_wt_dc_exact_confirmation.py",
    }
    discovered = {path.relative_to(root).as_posix() for path in launchers}
    assert canonical <= discovered
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


def test_process_signature_ignores_metadata_only_sync(monkeypatch, tmp_path):
    from tools import persym_baseline_campaign as campaign

    helper = tmp_path / "engine.py"
    helper.write_text("same executable bytes\n")
    monkeypatch.setattr(campaign, "SBX", tmp_path)
    monkeypatch.setattr(campaign, "MATRIX_CONTRACT_FILES", ["engine.py"])
    monkeypatch.setattr(campaign, "C5_ADDITIONAL_CONTRACT_FILES", [])
    monkeypatch.setattr(campaign, "MATRIX_ORCHESTRATION_FILES", [])
    before = campaign._matrix_process_source_signature()
    stat = helper.stat()
    __import__("os").utime(helper, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert campaign._matrix_process_source_signature() == before
