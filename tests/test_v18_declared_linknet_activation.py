import hashlib
import json
import ast
import copy
import os
from pathlib import Path

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
    with pytest.raises(planner.LinkNetBundleError, match="hash mismatch"):
        planner.load_linknet_first("CRYPTO_LONG", tmp_path)
    path.unlink()
    with pytest.raises(planner.LinkNetBundleError, match="artifact absent"):
        planner.load_linknet_first("CRYPTO_LONG", tmp_path)


def test_required_declaration_missing_is_error_but_legacy_remains_open(tmp_path, monkeypatch):
    monkeypatch.delenv("V18_DIGEST_DIR", raising=False)
    monkeypatch.setenv("V18_LINKNET_BUNDLE_REQUIRED", "1")
    with pytest.raises(planner.LinkNetBundleError, match="declaration absent"):
        planner.load_linknet_first("CRYPTO_LONG", tmp_path)
    monkeypatch.delenv("V18_LINKNET_BUNDLE_REQUIRED")
    assert planner.load_linknet_first("CRYPTO_LONG", tmp_path)[0] == {}


def test_declared_selected_model_source_and_category_are_required(tmp_path, monkeypatch):
    monkeypatch.delenv("V18_DIGEST_DIR", raising=False)
    path, declaration = fixture(tmp_path)
    entry = declaration["linknet_activation"]["by_category"]["CRYPTO_LONG"]
    entry["source_artifacts_sha256"] = {"unrelated.npz": "a" * 64}
    declaration_path = tmp_path / "tools/v18_model_bundle.json"
    declaration_path.write_text(json.dumps(declaration))
    with pytest.raises(planner.LinkNetBundleError, match="selected scorer source hash"):
        planner.load_linknet_first("CRYPTO_LONG", tmp_path)

    entry["source_artifacts_sha256"] = {"model_v1_CRYPTO_LONG.npz": "a" * 64}
    document = json.loads(path.read_text())
    document["meta"]["cat_side"] = "STOCKS_LONG"
    path.write_text(json.dumps(document))
    declaration["expected_sha256_by_filename"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    declaration_path.write_text(json.dumps(declaration))
    with pytest.raises(planner.LinkNetBundleError, match="category/scorer mismatch"):
        planner.load_linknet_first("CRYPTO_LONG", tmp_path)


def test_strict_guard_escapes_actual_pilot_spec_fallback(capsys):
    tree = ast.parse((Path(planner.__file__).parents[1] / "v15_pilot.py").read_text())
    caller = next(node for node in ast.walk(tree) if isinstance(node, ast.Try) and any(handler.name == "_spec_e" for handler in node.handlers))
    probe = ast.Module(body=[ast.Try(body=[ast.Expr(value=ast.Call(func=ast.Name(id="fail", ctx=ast.Load()), args=[], keywords=[]))], handlers=copy.deepcopy(caller.handlers), orelse=[], finalbody=[]), ast.Assign(targets=[ast.Name(id="fell_back", ctx=ast.Store())], value=ast.Constant(True))], type_ignores=[])
    executable = compile(ast.fix_missing_locations(probe), "actual_pilot_spec_fallback", "exec")
    def strict_failure():
        raise planner.LinkNetBundleError("missing declared artifact")
    namespace = {"fail": strict_failure}
    with pytest.raises(planner.LinkNetBundleError):
        exec(executable, namespace)
    assert "fell_back" not in namespace
    def ordinary_failure():
        raise ValueError("ordinary fallback")
    namespace = {"fail": ordinary_failure}
    exec(executable, namespace)
    assert namespace["fell_back"] is True


def test_actual_pilot_flags_activate_required_consumer(tmp_path, monkeypatch):
    tree = ast.parse((Path(planner.__file__).parents[1] / "v15_pilot.py").read_text())
    loader = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_load_gate_flags")
    flags = tmp_path / "flags.json"
    flags.write_text(json.dumps({"flags": {"V18_LINKNET_FIRST": "1", "V18_LINKNET_BUNDLE_REQUIRED": "1"}}))
    monkeypatch.setenv("V15_GATE_FLAGS", str(flags))
    monkeypatch.setenv("V18_LINKNET_FIRST", "tracked before actual loader mutation")
    monkeypatch.setenv("V18_LINKNET_BUNDLE_REQUIRED", "tracked before actual loader mutation")
    monkeypatch.delenv("V18_LINKNET_FIRST", raising=False)
    monkeypatch.delenv("V18_LINKNET_BUNDLE_REQUIRED", raising=False)
    namespace = {"Path": Path, "os": os, "json": json, "ROOT": tmp_path}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(loader)], type_ignores=[])), "actual_pilot_flags", "exec"), namespace)
    namespace["_load_gate_flags"]()
    assert planner.linknet_first_on() is True
    monkeypatch.setenv("V18_LINKNET_FIRST", "0")
    with pytest.raises(planner.LinkNetBundleError, match="consumer is disabled"):
        planner.linknet_first_on()
