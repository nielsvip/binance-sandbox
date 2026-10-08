"""per_sym_settings parity: SQL kv_json is PRIMARY, data/per_sym_settings.json the identical backup.

Covers the 2026-10-08 rename (cat_side_defaults_4.* -> per_sym_settings.*):
- new JSON exists, old JSON gone (no stale reads)
- kv row "per_sym_settings" == JSON bytes (buttons: tools/v15_state_kv_sync.py --apply)
- old kv row gone, old module import still works via shim
"""
import json
from pathlib import Path

import per_sym_store as pss

ROOT = Path(__file__).resolve().parents[1]
NEW_JSON = ROOT / "data" / "per_sym_settings.json"
OLD_JSON = ROOT / "data" / "cat_side_defaults_4.json"


def test_new_json_exists_old_gone():
    assert NEW_JSON.exists(), "data/per_sym_settings.json missing"
    assert not OLD_JSON.exists(), "stale data/cat_side_defaults_4.json still present"


def test_kv_identical_to_json():
    js = json.loads(NEW_JSON.read_text())
    assert pss.kv_get(pss.KV_CAT_SIDE_DEFAULTS_4) == js, "SQL kv != JSON backup (run tools/v15_state_kv_sync.py --apply)"


def test_old_kv_row_gone():
    assert pss.kv_get("cat_side_defaults_4") is None, "old kv row still parked"


def test_shim_import_matches():
    import cat_side_defaults as old
    import per_sym_settings as new
    assert old.PATH == new.PATH == NEW_JSON
    assert old.get_for("REENTRY_TIER2_MAX_MINUTES", "BTCUSDC", "LONG", None) == new.get_for("REENTRY_TIER2_MAX_MINUTES", "BTCUSDC", "LONG", None)
