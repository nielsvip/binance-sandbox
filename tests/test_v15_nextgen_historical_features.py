import hashlib
import json
import numpy as np
import pytest

from tools import v18_features as features
from tools import v18_linknet as network
from tools import v15_nextgen_historical_features as historical


def current_context(default, candidates):
    return features.Context("CRYPTO_LONG", tidx={"cat_side": "CRYPTO_LONG", "tabs": {}, "switches": {"STOP_PCT": candidates}, "defaults": {"STOP_PCT": default}}, bible={"STOP_PCT": {"defaults": {"cat_side_defaults_4": {"CRYPTO_LONG": default}}}})


def test_intrinsic_features_and_fixed_network_scores_ignore_future_defaults_and_spans():
    first = network.Featurizer("CRYPTO_LONG", current_context(2, [0, 2, 4]), feature_mode="context_free")
    second = network.Featurizer("CRYPTO_LONG", current_context(900, [100, 900, 10000]), feature_mode="context_free")
    key = "EXIT_STRUCTURAL|STOP_PCT=3|INF=2|T"
    assert first.set_feats(key) == second.set_feats(key)
    assert not any("vrel:" in name or "rinf:" in name or "rinfx" in name for name, _ in first.set_feats(key))
    one, two = first.table([first.set_feats(key)], network.LS), second.table([second.set_feats(key)], network.LS)
    model = network.LinkNet(len(first.names), k=4, H=8, seed=7)
    empty_filter = (np.zeros((1, network.LF), int), np.zeros((1, network.LF), float))
    empty_context = (np.zeros((1, network.LC), int), np.zeros((1, network.LC), float))
    np.testing.assert_array_equal(model.predict_tables(one, empty_filter, empty_context), model.predict_tables(two, empty_filter, empty_context))


def test_missing_asof_snapshot_disables_features_and_refuses_quality_training():
    context = historical.adapt(current_context(900, [1, 9, 900]), "historical_quality")
    assert context.default("STOP_PCT") is None
    assert context.span("STOP_PCT") == []
    assert historical.inforce(context, 900, "T") == (None, "U")
    with pytest.raises(ValueError, match="immutable pre-cut"):
        historical.require_quality_training(context, {"feature_mode": "historical_quality"}, {})


def test_precut_snapshot_is_hashed_and_relative_values_must_be_verified(tmp_path):
    index = {"cat_side": "CRYPTO_LONG", "tabs": {}, "switches": {"STOP_PCT": [0, 2, 4]}, "defaults": {"STOP_PCT": 2}}
    path = tmp_path / "index.json"
    path.write_text(json.dumps(index))
    manifest = {"schema": "v18_asof_templates_v1", "holdout_start": "2026-10-20T00:00:00Z", "templates": {"CRYPTO_LONG": {"index_path": str(path), "index_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "defaults_sha256": hashlib.sha256(json.dumps(index["defaults"], sort_keys=True, separators=(",", ":")).encode()).hexdigest(), "recorded_at": "2026-10-10T00:00:00Z"}}}
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    first = historical.adapt(current_context(900, [100, 900]), "historical_quality", manifest_path)
    second = historical.adapt(current_context(-100, [-100, 10000]), "historical_quality", manifest_path)
    assert first.span("STOP_PCT") == second.span("STOP_PCT") == [0, 2, 4]
    assert historical.inforce(first, 900, "T") == (None, "U")
    assert historical.inforce(first, 2, "V") == (2, "V")
    assert historical.context_encoder(first).diff({"STOP_PCT": 2, "GATE": False}) == {"STOP_PCT": "2", "GATE": "False"}
    with pytest.raises(ValueError, match="market-window lineage"):
        historical.require_quality_training(first, {"feature_mode": "historical_quality"}, {})
    path.write_text(json.dumps(dict(index, defaults={"STOP_PCT": 900})))
    with pytest.raises(ValueError, match="hash mismatch"):
        historical.adapt(current_context(2, [0, 2]), "historical_quality", manifest_path)


def test_context_free_dataset_never_infers_current_template_values(monkeypatch):
    monkeypatch.setenv("V18_FEATURE_MODE", "context_free")
    dataset = network.CatDS("CRYPTO_LONG", defaults={"STOP_PCT": 900})
    dataset.inforce["BTCUSDT_LONG"]["STOP_PCT"] = 2
    assert dataset.in_force("BTCUSDT_LONG", "STOP_PCT") == (None, "U")
    assert network._template_defaults("CRYPTO_LONG") == {}


def test_intrinsic_scoring_keeps_candidate_universe_without_default_features():
    first = current_context(2, [0, 2, 4])
    second = current_context(900, [100, 900, 10000])
    for context, default in ((first, 2), (second, 900)):
        context.tidx["tabs"] = {"EXIT_STRUCTURAL": {"rows": [[3, "STOP_PCT", 3, default]], "yellows": ["FILTER_TF=1h"]}}
    left = network.Featurizer("CRYPTO_LONG", first, feature_mode="context_free")
    right = network.Featurizer("CRYPTO_LONG", second, feature_mode="context_free")
    assert left.fctx.scoring_index == right.fctx.scoring_index == {"tabs": {"EXIT_STRUCTURAL": {"rows": [[3, "STOP_PCT", 3, None]], "yellows": ["FILTER_TF=1h"]}}}
    assert left.fctx.tidx["tabs"] == right.fctx.tidx["tabs"] == {}
    assert left.fctx.tabs_of == right.fctx.tabs_of == {}
    assert left.fctx.fopts == right.fctx.fopts == {}
    assert left.fctx.span("STOP_PCT") == right.fctx.span("STOP_PCT") == []
    assert left.fctx.default("STOP_PCT") is None
    assert left.set_feats("EXIT_STRUCTURAL|STOP_PCT=3") == right.set_feats("EXIT_STRUCTURAL|STOP_PCT=3")
    training = network.Featurizer("CRYPTO_LONG", current_context(2, [0, 2, 4]), feature_mode="context_free")
    assert left.set_feats("EXIT_STRUCTURAL|STOP_PCT=3") == training.set_feats("EXIT_STRUCTURAL|STOP_PCT=3")
    assert left.filt_feats("FILTER_TF=1h") == training.filt_feats("FILTER_TF=1h")
