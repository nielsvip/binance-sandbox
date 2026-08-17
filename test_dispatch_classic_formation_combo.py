from tools import dispatch_classic_formation_combo as dispatch
from pathlib import Path


def test_compact_combo_receipt_keeps_only_best_holdout_qualified_pair():
    evidence = {
        "return_pct": 20.0,
        "bh_return_pct": 5.0,
        "alpha_vs_bh_pp": 15.0,
        "trades": 12,
        "formation_entry_action_count": 2,
        "formation_exit_action_count": 1,
    }
    receipt = {
        "selection_contract": "TRAIN_SELECT_THEN_FROZEN_SAME_COMBOS_ON_UNTOUCHED_HOLDOUT",
        "plan_fingerprint": "a" * 64,
        "key_count": 115,
        "combo_count": 1,
        "results": [
            {
                "key": "ABC_SHORT",
                "symbol": "ABC",
                "side": "SHORT",
                "status": "HOLDOUT_QUALIFIED",
                "best_qualified_combo": "ENTRY:wedge_entry_1h|EXIT:triangle_exit_1h",
                "npz_sha256": "b" * 64,
                "config_fingerprint": "c" * 64,
                "combos": [
                    {
                        "label": "ENTRY:wedge_entry_1h|EXIT:triangle_exit_1h",
                        "entry": "wedge_entry_1h",
                        "exit": "triangle_exit_1h",
                        "timeframe": "1h",
                        "train": evidence,
                        "holdout": evidence,
                    }
                ],
            }
        ],
    }
    compact = dispatch.compact_qualified(receipt)
    assert compact["key_count"] == 115
    assert compact["holdout_qualified_key_count"] == 1
    assert compact["failed_key_count"] == 0
    assert compact["qualified"][0]["side"] == "SHORT"
    assert compact["exact_v8_queued"] is False


def test_failed_combo_key_blocks_strict_completion():
    compact = dispatch.compact_qualified(
        {
            "key_count": 115,
            "results": [
                {"key": "BAD_LONG", "status": "FAILED", "failure_reasons": ["boom"]}
            ],
        }
    )
    assert compact["failed_key_count"] == 1
    assert compact["failed_keys"][0]["key"] == "BAD_LONG"


def test_repaired_dispatch_waits_for_previous_run_instead_of_losing_rerun():
    source = Path("tools/dispatch_classic_formation_combo.py").read_text()
    assert "request_id_for" in source
    assert "TRAIN_CLASSIC_FORMATIONS_SOURCE_MISMATCH" in source
    assert "DEFERRED_STALE_TRAIN_SOURCE" in source
    assert '"WAITING_FOR_PREVIOUS_COMBO_RUN"' in source
    assert "time.sleep(30)" in source
    assert 'return 0\n    campaign = root / CAMPAIGN_REL' not in source
