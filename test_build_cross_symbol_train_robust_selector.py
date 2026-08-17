import json

from tools import build_cross_symbol_train_robust_selector as selector


def test_selector_uses_train_only_and_prefers_worst_tim_margin(tmp_path):
    fold = {"alpha_vs_bh_pp": 2, "alpha_vs_same_entry_e02_pp": 1,
            "real_close_trades": 12, "weighted_tim_pct": 75}
    payload = {"manifest": {"symbol": "A", "side": "LONG"}, "candidates": [
        {"family": "BOTTOM_A", "params": {"x": 1},
         "fold_evidence": [{**fold, "weighted_tim_pct": 72}, {**fold, "weighted_tim_pct": 1}]},
        {"family": "BOTTOM_A", "params": {"x": 2},
         "fold_evidence": [{**fold, "weighted_tim_pct": 79}, fold]},
    ]}
    path = tmp_path / "one" / "result.json"; path.parent.mkdir(); path.write_text(json.dumps(payload))
    result = selector.build(tmp_path)
    assert result["selection_contract"]["untouched_final_used"] is False
    assert result["ranked_supported_grid_params"][0]["params"] == {"x": 1}
