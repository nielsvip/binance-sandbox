from pathlib import Path
import json

from tools import audit_knob_wiring as audit


def test_ast_wiring_counts_dynamic_reads_not_comments_or_definitions(tmp_path):
    (tmp_path / "config.py").write_text(
        "class Config:\n"
        "    DIRECT_KNOB: int = 1\n"
        "    STRING_KNOB: int = 2\n"
        "    DYNAMIC_TRADIER: int = 3\n"
        "    COMMENT_ONLY: int = 4\n"
        "    DEFINITION_ONLY: int = 5\n"
    )
    (tmp_path / "config_tradier.py").write_text("class TradierConfig:\n    pass\n")
    (tmp_path / "decision.py").write_text(
        "def decide(config, _cfg):\n"
        "    # config.COMMENT_ONLY must not count\n"
        "    direct = config.DIRECT_KNOB\n"
        "    string = getattr(config, 'STRING_KNOB', 0)\n"
        "    dynamic = _cfg('DYNAMIC', 0)\n"
        "    return direct + string + dynamic\n"
    )

    names, wired, disconnected = audit.scan_wiring(str(tmp_path))

    assert names == {
        "DIRECT_KNOB",
        "STRING_KNOB",
        "DYNAMIC_TRADIER",
        "COMMENT_ONLY",
        "DEFINITION_ONLY",
    }
    assert set(wired) == {"DIRECT_KNOB", "STRING_KNOB", "DYNAMIC_TRADIER"}
    assert disconnected == ["COMMENT_ONLY", "DEFINITION_ONLY"]


def test_sweep_name_list_is_not_decision_wiring(tmp_path):
    (tmp_path / "config.py").write_text(
        "class Config:\n    SEARCH_ONLY_KNOB: bool = False\n"
    )
    (tmp_path / "config_tradier.py").write_text("class TradierConfig:\n    pass\n")
    (tmp_path / "autonomous_search.py").write_text(
        "PARAMS = ['SEARCH_ONLY_KNOB']\n"
    )

    _names, wired, disconnected = audit.scan_wiring(str(tmp_path))

    assert "SEARCH_ONLY_KNOB" not in wired
    assert disconnected == ["SEARCH_ONLY_KNOB"]


def test_disconnected_classification_identifies_only_reviewed_trb_paths(
    tmp_path,
):
    data = tmp_path / "data"
    reports = data / "reports"
    reports.mkdir(parents=True)
    (data / "knob_registry.json").write_text(
        json.dumps(
            {
                "tradier": {
                    "TRB_DEAD": {},
                    "TRC_DEAD": {},
                    "AMBIGUOUS": {},
                },
                "crypto": {"CRYPTO_DEAD": {}},
            }
        )
    )
    (data / "param_sweep_manifest_tradier.json").write_text(
        json.dumps(
            {
                "params": {
                    "TRB_DEAD": {
                        "sweepable": True,
                        "runtime_control_not_alpha": False,
                    },
                    "TRC_DEAD": {
                        "sweepable": True,
                        "runtime_control_not_alpha": False,
                    },
                    "AMBIGUOUS": {
                        "sweepable": True,
                        "runtime_control_not_alpha": False,
                    },
                }
            }
        )
    )
    (reports / "SWITCH_MATRIX_INTERDEPENDENCY_20260729.json").write_text(
        json.dumps(
            {
                "paths": [
                    {
                        "param": "TRB_DEAD",
                        "differential_readiness": "BINDING_PROBE_REQUIRED",
                        "deployment_scope": "GLOBAL_ONLY",
                        "screen_backends": ["EXACT_V8"],
                    },
                    {
                        "param": "TRC_DEAD",
                        "differential_readiness": "BINDING_PROBE_REQUIRED",
                        "deployment_scope": "GLOBAL_ONLY",
                        "screen_backends": ["EXACT_V8"],
                    },
                ]
            }
        )
    )

    classified = audit.classify_disconnected(
        str(tmp_path),
        ["AMBIGUOUS", "CRYPTO_DEAD", "OPS_DEAD", "TRB_DEAD", "TRC_DEAD"],
    )

    assert classified["buckets"][
        "actionable_trb_matrix_source_orphan"
    ] == ["TRB_DEAD"]
    assert classified["buckets"]["other_account_stock"] == ["TRC_DEAD"]
    assert classified["buckets"]["crypto_only"] == ["CRYPTO_DEAD"]
    assert classified["buckets"][
        "tradier_metadata_intended_semantics_blocker"
    ] == ["AMBIGUOUS"]
    assert classified["buckets"][
        "operational_diagnostic_or_unregistered"
    ] == ["OPS_DEAD"]
