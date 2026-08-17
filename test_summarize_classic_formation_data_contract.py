import hashlib
import json

from tools import summarize_classic_formation_data_contract as summary


def test_compact_diagnostic_preserves_exact_failed_symbol_evidence(tmp_path):
    source = tmp_path / "audit.json"
    source.write_text(
        json.dumps(
            {
                "status": "FAILED",
                "npz_dir": "frozen",
                "key_count": 115,
                "underlying_symbols": 108,
                "audits": {
                    "GOOD": {"valid": True, "errors": []},
                    "BAD": {
                        "valid": False,
                        "errors": ["jump"],
                        "warnings": ["inspect"],
                        "returncode": 2,
                        "stats": {
                            "max_bar_jump_classification": "PERSISTENT_OR_UNVERIFIED",
                            "max_bar_jump_from_timestamp": 10,
                            "max_bar_jump_to_timestamp": 20,
                            "max_bar_jump_from_close": 1.0,
                            "max_bar_jump_to_close": 4.0,
                            "max_bar_jump_pct": 300.0,
                            "timestamp_gaps_gt_7d": 0,
                        },
                    },
                },
            }
        )
    )

    payload = summary.summarize(source)

    assert payload["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert payload["failed_symbol_count"] == 1
    assert payload["failed_symbols"] == ["BAD"]
    assert payload["failures"]["BAD"]["max_bar_jump_pct"] == 300.0
    assert payload["matrix_written"] is False
