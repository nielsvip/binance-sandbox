import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_exact_audit_is_not_hidden_by_legacy_display_champion(tmp_path):
    reports = tmp_path / "data/reports"
    reports.mkdir(parents=True)
    achievement = {
        "live_enabled_count": 60,
        "gate_aware_vector_pass_count": 1,
        "classic_formation_vector_pass_count": 0,
        "rows": [{
            "key": "XLE_LONG",
            "beats_bh": True,
            "real_close_trades": 2,
            "exact_v8_status": "V8_FULL_RECIPE_FAIL",
            "exact_v8_full_recipe_run_summary": {
                "run_id": "exact-xle",
                "selected_entry_family": "ENTRY_4H_DEEP_VALUE",
                "selected_exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
                "strategy_return_pct": 0.0,
                "side_aware_bh_return_pct": 33.5,
                "real_closes": 0,
                "direct_route_claims": 12,
                "direct_route_actions": 0,
                "entry_route_fills": 0,
                "selected_exit_route_fills": 0,
                "checks": {"SHARED_ENTRY_ROUTE_FIRED": False},
            },
        }],
    }
    (reports / "MATRIX_SYMBOL_SIDE_ACHIEVEMENT_CURRENT.json").write_text(
        json.dumps(achievement)
    )
    subprocess.run(
        [sys.executable, str(ROOT / "tools/build_trb_60_readiness_digest.py"),
         "--root", str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    out = json.loads((reports / "TRB_60_RESEARCH_READINESS_CURRENT.json").read_text())
    assert out["qualifying_count"] == 0
    assert out["full_v8_recipe_confirmed_count"] == 0
    assert out["strict_gate_aware_vector_winner_count"] == 1
    assert len(out["full_v8_recipe_audits"]) == 1
    audit = out["full_v8_recipe_audits"][0]
    assert audit["key"] == "XLE_LONG"
    assert audit["direct_route_claims"] == 12
    assert audit["direct_route_actions"] == 0
    assert audit["failed_checks"] == ["SHARED_ENTRY_ROUTE_FIRED"]
