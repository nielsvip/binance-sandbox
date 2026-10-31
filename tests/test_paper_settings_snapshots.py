import hashlib
import json

import pytest

from tools.paper_scenario_db import ScenarioDB


def canonical_hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def test_canonical_settings_and_code_snapshots_are_immutable_and_linked(tmp_path):
    db = ScenarioDB(tmp_path / "inventory.sqlite")
    settings = {"switch": False, "size": 12}
    settings_hash = canonical_hash(settings)
    assert db.add_settings_snapshot(settings_hash=settings_hash, settings=settings) == settings_hash
    assert db.add_settings_snapshot(settings_hash=settings_hash, settings_json='{"size":12,"switch":false}') == settings_hash
    with pytest.raises(ValueError, match="settings_hash"):
        db.add_settings_snapshot(settings_hash="wrong", settings=settings)

    files = {"ez_manage.py": "a" * 64, "config.py": "b" * 64}
    code_hash = canonical_hash(files)
    assert db.add_source_code_snapshot(code_hash=code_hash, file_hashes=files) == code_hash
    assert db.add_source_code_snapshot(code_hash=code_hash, file_hashes_json=json.dumps(files)) == code_hash
    with pytest.raises(ValueError, match="code_hash"):
        db.add_source_code_snapshot(code_hash="wrong", file_hashes=files)

    db.add_path(path_id="p", venue="crypto", path_name="paper", switch_name="X", classification="catalog", provenance={})
    db.add_scenario(scenario_id="s", path_id="p", side="LONG", parameters={}, created_at="t")
    db.add_run(run_id="r", scenario_id="s", sym_side="BTCUSDC_LONG", host="s1", settings_hash=settings_hash, code_hash=code_hash, evidence_kind="catalog_only", status="PENDING_PARAMETERS", started_at="t", ended_at=None)
    inventory = db.query("SELECT settings_json,file_hashes_json FROM scenario_result_inventory WHERE run_id='r'")[0]
    assert json.loads(inventory["settings_json"]) == settings
    assert json.loads(inventory["file_hashes_json"]) == files
    db.close()
