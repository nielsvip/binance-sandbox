import hashlib
import json

import pytest
from tools import v15_v18_planner as planner


def fixture(tmp_path):
    learn = tmp_path / "data/learn"
    tools = tmp_path / "tools"
    learn.mkdir(parents=True)
    tools.mkdir()
    rows = [{"switch": "DC_DAYTRADE_ENABLED", "cand": "True", "filter": "MOM3_FILTER_TF", "opt": option, "p_pos_pred": probability, "status": "PREDICTED_NOT_MEASURED"} for option, probability in (("4h", 0.2), ("1h", 0.8))]
    document = {"meta": {"cat_side": "CRYPTO_LONG", "production_scorer": "v1"}, "template_untried": rows}
    path = learn / "linknet_proposals_CRYPTO_LONG.json"
    path.write_text(json.dumps(document))
    declaration = {"bundle_id": "test", "expected_sha256_by_filename": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()}, "linknet_activation": {"schema": "compiled_linknet_first/1", "by_category": {"CRYPTO_LONG": {"filename": path.name, "production_scorer": "v1", "source_artifacts_sha256": {"model_v1_CRYPTO_LONG.npz": "a" * 64}}}}}
    (tools / "v18_model_bundle.json").write_text(json.dumps(declaration))
    return path, declaration


def test_declared_rank_drives_existing_pilot_floor_consumer(tmp_path, monkeypatch):
    monkeypatch.delenv("V18_DIGEST_DIR", raising=False)
    fixture(tmp_path)
    ranked, why = planner.load_linknet_first("CRYPTO_LONG", tmp_path, top=1)
    assert list(ranked) == [("DC_DAYTRADE_ENABLED=True", "MOM3_FILTER_TF=1h")]
    info = {"kind": "eval", "hdrs": [], "prior_skipped": ["MOM3_FILTER_TF=4h", "MOM3_FILTER_TF=1h"]}
    planner.floor_apply(info, lambda kind, header: kind == "cell" and ("DC_DAYTRADE_ENABLED=True", header) in ranked, 1.0)
    assert info["hdrs"] == ["MOM3_FILTER_TF=1h"]
    assert info["prior_skipped"] == ["MOM3_FILTER_TF=4h"]
    assert "compiled_sha256=" in why and "model_v1_CRYPTO_LONG.npz" in why


def test_declared_missing_or_tampered_artifact_refuses_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("V18_DIGEST_DIR", raising=False)
    path, _ = fixture(tmp_path)
    path.write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        planner.load_linknet_first("CRYPTO_LONG", tmp_path)
    path.unlink()
    with pytest.raises(ValueError, match="artifact absent"):
        planner.load_linknet_first("CRYPTO_LONG", tmp_path)


def test_required_declaration_missing_is_error_but_legacy_remains_open(tmp_path, monkeypatch):
    monkeypatch.delenv("V18_DIGEST_DIR", raising=False)
    monkeypatch.setenv("V18_LINKNET_BUNDLE_REQUIRED", "1")
    with pytest.raises(ValueError, match="declaration absent"):
        planner.load_linknet_first("CRYPTO_LONG", tmp_path)
    monkeypatch.delenv("V18_LINKNET_BUNDLE_REQUIRED")
    assert planner.load_linknet_first("CRYPTO_LONG", tmp_path)[0] == {}
