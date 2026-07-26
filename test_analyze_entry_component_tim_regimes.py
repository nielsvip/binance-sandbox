import json

from tools import analyze_entry_component_tim_regimes as regimes


def _artifact(root, family, prefinal_tims, final_tim):
    path = root / f"artifact_{family}"
    path.mkdir()
    folds = []
    for idx, tim in enumerate([*prefinal_tims, final_tim], start=1):
        folds.append(
            {
                "fold": idx,
                "train": [f"t{idx}", f"t{idx + 1}"],
                "validation": [f"v{idx}", f"v{idx + 1}"],
                "curve": {"label": f"curve_{family}_{idx}"},
                "selected_candidate": {
                    "family": family,
                    "role": "direct",
                    "params": {},
                },
                "selected_entry_signal_rows": 10,
                "selected_entry_request_count": 8,
                "training_robust_weighted_tim_pct": (
                    50.0 if idx == 3 else tim
                ),
                "inner_metrics": [
                    {"exposure_weighted_tim_pct": 50.0 if idx == 3 else tim}
                ],
                "validation_metrics": {
                    "end_ts": idx,
                    "exposure_weighted_tim_pct": tim,
                    "fill_count": 4,
                    "fill_ratio": 0.5,
                    "entry_capacity_breach": False,
                },
            }
        )
    result = {
        "manifest": {
            "family": family,
            "symbol": "PBF",
            "side": "LONG",
            "control_artifact": "control",
        },
        "outer_folds": folds,
    }
    (path / "result.json").write_text(json.dumps(result))
    return path


def test_family_selection_excludes_final_fold_and_never_blends(tmp_path):
    report_dir = tmp_path / "data" / "reports" / "vec_research"
    report_dir.mkdir(parents=True)
    a = _artifact(tmp_path, "FAMILY_A", [68.0, 76.0], 71.0)
    b = _artifact(tmp_path, "FAMILY_B", [20.0, 25.0], 75.0)
    report = {
        "rows": [
            {"artifact": str(a)},
            {"artifact": str(b)},
        ]
    }
    report_path = report_dir / "components.json"
    report_path.write_text(json.dumps(report))
    payload = regimes.analyze(report_path)
    beam = payload["beam_inputs"][0]
    assert beam["selected_family"] == "FAMILY_A"
    assert beam["selection_source_folds"] == [1, 2]
    assert beam["excluded_final_fold"] == 3
    assert beam["frozen_ladder_curve"]["label"] == "curve_FAMILY_A_2"
    assert beam["single_family_only"]
    assert not beam["blended_entry_overlay_authorized"]
    assert beam["regime_instability"]
