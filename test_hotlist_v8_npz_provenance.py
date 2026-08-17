import hashlib
import json
from pathlib import Path

from tools.run_hotlist_v8_full_recipe import find_champion, resolve_frozen_npz


def test_parent_campaign_npz_hash_is_preserved_for_nested_champion(tmp_path):
    npz = tmp_path / "data/matrix_npz/cohort/ABC.npz"
    npz.parent.mkdir(parents=True)
    npz.write_bytes(b"frozen-npz")
    digest = hashlib.sha256(npz.read_bytes()).hexdigest()
    receipt = tmp_path / "receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "hotlist": [
                    {
                        "key": "ABC_LONG",
                        "npz": str(npz.relative_to(tmp_path)),
                        "npz_sha256": digest,
                        "champion": {
                            "key": "ABC_LONG",
                            "entry_family": "ENTRY_BOUNCE_5M_LOW",
                            "exit_family": "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED",
                            "exit_params": {"rebound_atr": 0.125},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    hot = {
        "key": "ABC_LONG",
        "source_receipt": str(receipt.relative_to(tmp_path)),
        "entry_family": "ENTRY_BOUNCE_5M_LOW",
        "exit_family": "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED",
        "exit_params": {"rebound_atr": 0.125},
    }
    _, champion = find_champion(tmp_path, hot)
    assert champion["_source_npz_sha256"] == digest
    assert resolve_frozen_npz(
        tmp_path, {"npz": str(npz.relative_to(tmp_path))}, champion
    ) == npz


def test_missing_npz_hash_never_falls_back_to_unbound_file(tmp_path):
    npz = tmp_path / "ABC.npz"
    npz.write_bytes(b"unbound")
    try:
        resolve_frozen_npz(tmp_path, {"npz": str(npz)}, {})
    except RuntimeError as exc:
        assert "provenance is incomplete" in str(exc)
    else:
        raise AssertionError("unbound NPZ was accepted")


def test_exact_receipts_are_bound_to_completed_snapshot_adapter_hash():
    runner = Path("tools/run_hotlist_v8_full_recipe.py").read_text()
    watchdog = Path("tools/s1_hotlist_v8_full_recipe_watchdog.sh").read_text()
    field = "v8_completed_snapshot_adapter_sha256"
    assert runner.count(field) >= 2
    assert field in watchdog
