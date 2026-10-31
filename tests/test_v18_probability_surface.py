from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from tools import v18_brain as brain
from tools import v18_probability_surface as surface
from tools import v15_v20_selector as selector


def make_inputs(tmp_path, monkeypatch, **changes):
    monkeypatch.setattr(brain, "ROOT", tmp_path)
    model = tmp_path / "model.json"
    model.write_text(json.dumps({"_meta": {"cat_side": "CRYPTO_LONG", "p_ref": {"R": 0.002, "F": 0.002}}, "heads": {"R": [0.0] * brain.NF, "F0": [0.0] * brain.NF}, "nodes": []}))
    template = tmp_path / "SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx"
    template.parent.mkdir()
    template.write_bytes(b"exact test template identity")
    path = tmp_path / "surface.npz"
    calibration = {"R": [1.0, 0.0], "F": [1.0, 0.0]}
    metadata = {"schema": "v18_linknet_symbol_surface/1", "cat_side": "CRYPTO_LONG", "symbol_side": "BTCUSDT_LONG", "label": "PREDICTED_NOT_MEASURED", "production_scorer": "hybrid_v1", "model_trained_at": "2026-10-01T00:00:00Z", "platt": calibration, "feature_mode": "context_free", "model_context": {"symbol_side": "BTCUSDT_LONG", "regime": "UNK", "tier": "U", "phase": "", "provenance": "log", "chain_features": []}}
    arrays = {"meta": np.frombuffer(json.dumps(metadata).encode(), np.uint8), "row_keys": np.array(["TAB1|SW=1", "TAB2|SW=1"]), "yellows": np.array(["F=0", "F=1"]), "regimes": np.array(["UNK"]), "cell_row": np.array([0, 0, 1], np.int32), "cell_yellow": np.array([0, 1, 1], np.int32), "cell_n_measured": np.array([900, 0, 7], np.int32), "cell_p_pos": np.array([[0.0002], [0.0008], [0.0009]], np.float32)}
    arrays.update(changes)
    np.savez_compressed(path, **arrays)
    entry = {"surface_path": path.name, "surface_sha256": surface.sha(path), "graph_model_sha256": surface.sha(model), "template_sha256": surface.sha(template), "production_scorer": "hybrid_v1", "model_trained_at": metadata["model_trained_at"], "calibration_sha256": surface.canonical_sha(calibration), "feature_mode": "context_free", "symbol_side": "BTCUSDT_LONG"}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema": "v18_linknet_probability_surface/1", "by_category": {"CRYPTO_LONG": entry}}))
    environment = {"V18_LINKNET_SURFACE_MANIFEST": str(manifest), "V18_LINKNET_SURFACE_SHA256": surface.sha(manifest)}
    return model, template, manifest, environment


def runtime(model, monkeypatch, environment=None, n=10):
    for key in ("V18_LINKNET_SURFACE_MANIFEST", "V18_LINKNET_SURFACE_SHA256"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (environment or {}).items():
        monkeypatch.setenv(key, value)
    value = brain.PilotBrainV18(model, "BTCUSDT_LONG")
    value.ctx["regime"] = "ALL"
    value._x = lambda *args: ([1.0] + [0.0] * (brain.NF - 1), n)
    value.w["F0"][0] = -8.0
    return value


def test_exact_tab_row_header_regime_and_immutable_cache(tmp_path, monkeypatch):
    model, _, _, environment = make_inputs(tmp_path, monkeypatch)
    provider, why = surface.load_surface(environment, model, "CRYPTO_LONG", tmp_path, "BTCUSDT_LONG")
    again, _ = surface.load_surface(environment, model, "CRYPTO_LONG", tmp_path, "BTCUSDT_LONG")
    assert provider is again and why.startswith("loaded")
    assert provider.lookup("TAB1", "SW=1", "F=0", "UP") == pytest.approx((0.0002, 900))
    assert provider.lookup("TAB1", "SW=1", "F=0", "UNK") == provider.lookup("TAB1", "SW=1", "F=0", "ALL")
    assert provider.lookup("TAB1", "SW=1", "F=0", None) is None
    assert provider.lookup("TAB2", "SW=1", "F=0", "ALL") is None
    assert provider.lookup("TAB1", "SW=1", "F=0", "FUTURE") is None
    assert provider.lookup("TAB1", "SW=10", "F=0", "ALL") is None
    monkeypatch.setattr(Path, "open", lambda *args, **kwargs: pytest.fail("per-cell file I/O"))
    assert provider.lookup("TAB1", "SW=1", "F=1", "ALL")[1] == 0
    with pytest.raises(ValueError):
        provider.probability[0, 0] = 0


def test_substitution_uses_existing_policy_and_graph_n_not_linknet_n(tmp_path, monkeypatch):
    model, _, _, environment = make_inputs(tmp_path, monkeypatch, cell_p_net=np.full((3, 1), 0.9))
    value = runtime(model, monkeypatch, environment, n=10)
    decision = value.decide("TAB1", "SW=1", "F=0", "r")
    assert decision["p_pos"] == pytest.approx(0.0002)
    assert decision["p"] == brain.p_eval(decision["p_pos"], 0.002, value.floor, 10)
    assert decision["n"] == 10 and decision["linknet_n_measured"] == 900
    assert decision["u"] == brain.draw(value.sym_side, "TAB1", "SW=1|F=0", "r")
    selected = selector.decide_cells(["F=0"], None, "TAB1", "SW", "1", {}, cat_side="CRYPTO_LONG", symside=value.sym_side, rid="r", model_p={"F=0": decision["p"]})
    assert selected["settled"]["F=0"]["p"] == decision["p"]
    assert selected["settled"]["F=0"]["reason_code"] != selector.RC_KEEP_EVIDENCE
    untried = runtime(model, monkeypatch, environment, n=2).decide("TAB1", "SW=1", "F=0", "r")
    assert untried["n"] == 2 and untried["p"] == 1.0


def test_existing_cost_aware_cap_and_positive_revival_remain_in_force(tmp_path, monkeypatch):
    model, _, _, environment = make_inputs(tmp_path, monkeypatch, cell_p_pos=np.full((3, 1), 0.9))
    value = runtime(model, monkeypatch, environment)
    value._ca = {}
    monkeypatch.setattr(brain, "cost_aware_hit", lambda *args: True)
    decision = value.decide("TAB1", "SW=1", "F=0", "r")
    assert decision["p"] == brain.COST_AWARE_P
    assert value.booked["cost_aware"] == value.booked["linknet_surface"] == 1
    assert "COST_AWARE" in decision["why"] and "LINKNET_SURFACE" in decision["why"]
    assert value.decide("TAB1", "SW=1", "F=0", "r", naked_d=0.1)["p"] == 1.0


def test_foreign_symbol_and_unsupported_context_fall_back(tmp_path, monkeypatch):
    model, _, _, environment = make_inputs(tmp_path, monkeypatch)
    provider, why = surface.load_surface(environment, model, "CRYPTO_LONG", tmp_path, "ETHUSDT_LONG")
    assert provider is None and "symbol identity" in why
    baseline = runtime(model, monkeypatch)
    enabled = runtime(model, monkeypatch, environment)
    baseline.ctx["regime"] = enabled.ctx["regime"] = "UNSUPPORTED"
    assert baseline.decide("TAB1", "SW=1", "F=0", "r") == enabled.decide("TAB1", "SW=1", "F=0", "r")


def test_absent_missing_and_positive_naked_paths_preserve_graph_decisions(tmp_path, monkeypatch):
    model, _, _, environment = make_inputs(tmp_path, monkeypatch)
    baseline = runtime(model, monkeypatch)
    enabled = runtime(model, monkeypatch, environment)
    for args in (("TAB1", "SW=2", "F=0", "r"), ("TAB1", "SW=1", None, "r"), ("TAB2", "SW=1", "F=0", "r")):
        assert baseline.decide(*args) == enabled.decide(*args)
    assert baseline.decide("TAB1", "SW=1", "F=0", "r", naked_d=0.1) == enabled.decide("TAB1", "SW=1", "F=0", "r", naked_d=0.1)
    assert baseline.booked == enabled.booked


@pytest.mark.parametrize("field", ["graph_model_sha256", "template_sha256", "calibration_sha256", "model_trained_at", "production_scorer"])
def test_foreign_model_template_or_calibration_identity_falls_back(tmp_path, monkeypatch, field):
    model, _, manifest, environment = make_inputs(tmp_path, monkeypatch)
    declaration = json.loads(manifest.read_text())
    declaration["by_category"]["CRYPTO_LONG"][field] = "wrong"
    manifest.write_text(json.dumps(declaration))
    environment["V18_LINKNET_SURFACE_SHA256"] = surface.sha(manifest)
    provider, why = surface.load_surface(environment, model, "CRYPTO_LONG", tmp_path, "BTCUSDT_LONG")
    assert provider is None and why.startswith("graph fallback")
    assert runtime(model, monkeypatch).decide("TAB1", "SW=1", "F=0") == runtime(model, monkeypatch, environment).decide("TAB1", "SW=1", "F=0")


@pytest.mark.parametrize("changes", [{"cell_p_pos": np.full((3, 1), np.nan)}, {"cell_p_pos": np.full((3, 1), 1.1)}, {"cell_n_measured": np.array([1.0, 2.0, 3.0])}, {"cell_yellow": np.array([0, 0, 1], np.int32)}, {"cell_row": np.array([1, 0, 0], np.int32)}])
def test_invalid_surface_is_not_consumed(tmp_path, monkeypatch, changes):
    model, _, _, environment = make_inputs(tmp_path, monkeypatch, **changes)
    provider, why = surface.load_surface(environment, model, "CRYPTO_LONG", tmp_path, "BTCUSDT_LONG")
    assert provider is None and why.startswith("graph fallback")
