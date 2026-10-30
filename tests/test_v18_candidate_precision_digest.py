import json
import numpy as np
from tools import v18_precision_build as precision
from tools import v18_digest as digest


def test_precision_invalid_and_unknown_visits_do_not_change_gain_posterior():
    def estimate(deltas, valid):
        n = len(deltas)
        return precision.key_estimates(np.zeros(n, dtype=int), np.zeros(n, dtype=int), np.array(deltas, float), np.array(valid), 1, np.zeros(n))
    clean = estimate([1.0, 0.0], [1, 1])
    mixed = estimate([1.0, 0.0, 99.0, np.nan], [1, 1, 0, 1])
    for field in ("p_mean", "p_lo", "p_hi", "mean_c"):
        np.testing.assert_allclose(clean[field], mixed[field], equal_nan=True)
    assert mixed["n"].tolist() == [4]
    assert mixed["inv"].tolist() == [2]
    assert mixed["zero"].tolist() == [1]


def test_digest_uses_matched_validation_choice_independent_of_test(tmp_path):
    report = {"selection": {"production_scorer": "v2", "v1_gate": {"test_used_for_selection": False}}, "validation": {"F": {"never_tried_pair": {"auc_v2": 0.8, "n": 20, "pos": 2}}}}
    path = tmp_path / "report_v2_CRYPTO_LONG.json"
    path.write_text(json.dumps(report))
    first, _ = digest.linknet_scorer("CRYPTO_LONG", tmp_path, tmp_path)
    report["test"] = {"F": {"never_tried_pair": {"auc_v2": 0.0, "auc_counts": 1.0}}}
    path.write_text(json.dumps(report))
    second, _ = digest.linknet_scorer("CRYPTO_LONG", tmp_path, tmp_path)
    assert first == second
    assert first["CRYPTO_LONG"]["test_used_for_selection"] is False
