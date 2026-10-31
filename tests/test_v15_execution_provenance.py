import dataclasses
import datetime
import hashlib
import json
import marshal
from pathlib import Path
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from tools import v15_execution_provenance as provenance


@dataclasses.dataclass
class _Config:
    threshold: float = 0.125
    labels: tuple = ("a", "b")
    flags: set = dataclasses.field(default_factory=lambda: {"x", "y"})
    path: Path = Path("data/example")
    age: datetime.timedelta = datetime.timedelta(seconds=3, microseconds=5)
    TRADIER_API_KEY: str = "never-persist-this-api-key"
    ACCOUNTS: dict = dataclasses.field(
        default_factory=lambda: {
            "trb": {
                "id": "account-number",
                "key": "never-persist-this-account-key",
                "env": "prod",
            }
        }
    )


def test_config_typed_roundtrip_redacts_credentials_and_preserves_strategy_values():
    cfg = _Config()
    document = provenance.config_document(cfg)
    restored = provenance.restore_config(document, _Config)
    encoded = json.dumps(document, sort_keys=True)
    assert "never-persist-this-api-key" not in encoded
    assert "never-persist-this-account-key" not in encoded
    assert "account-number" not in encoded
    assert restored.threshold == cfg.threshold
    assert restored.labels == cfg.labels
    assert restored.flags == cfg.flags
    assert restored.path == cfg.path
    assert restored.age == cfg.age
    assert restored.TRADIER_API_KEY == ""
    assert restored.ACCOUNTS == {"trb": {"id": "", "key": "", "env": ""}}
    assert {row["path"] for row in document["redactions"]} == {
        "TRADIER_API_KEY",
        "ACCOUNTS.trb.env",
        "ACCOUNTS.trb.id",
        "ACCOUNTS.trb.key",
    }


def test_npz_manifest_is_stable_for_objects_and_detects_input_mutation():
    npz = {
        "close": np.asarray([1.0, 2.0]),
        "labels": np.asarray(["a", "b"], dtype=object),
    }
    first = provenance.npz_manifest(npz)
    again = provenance.npz_manifest(npz)
    assert first == again
    npz["close"][1] = 2.5
    changed = provenance.npz_manifest(npz)
    assert (
        changed["arrays"]["close"]["content_sha256_b64"]
        != first["arrays"]["close"]["content_sha256_b64"]
    )


def test_capture_preserves_engine_return_and_restores_existing_wrapper(tmp_path):
    calls = []

    def existing(npz, symbol, is_long, cfg):
        calls.append((npz, symbol, is_long, cfg))
        return {"ledger": [{"type": "OPEN", "bar": 2, "qty": 1.0, "price": 10.0}]}

    module = SimpleNamespace(
        simulate_one=existing, __name__="test_engine", __file__=None
    )
    npz = {
        "timestamps": np.asarray([1, 2, 3], dtype="int64"),
        "close": np.asarray([9.0, 10.0, 11.0]),
    }
    cfg = _Config()
    expected = existing(npz, "TEST", True, cfg)
    calls.clear()
    with provenance.capture_engine_invocation(
        module, {"settings_sha256": "a" * 64}
    ) as capture:
        actual = module.simulate_one(npz, "TEST", True, cfg)
    assert actual == expected
    assert calls == [(npz, "TEST", True, cfg)]
    assert module.simulate_one is existing
    receipt = capture.result()
    assert receipt["capture_status"] == "VERIFIED"
    assert receipt["invocation"] == {
        "symbol": "TEST",
        "is_long": True,
        "force_initial_seed": False,
        "bar_ts": 3,
    }
    assert receipt["decision_context"]["settings_sha256"] == "a" * 64
    assert receipt["npz_manifest"]["arrays"]["timestamps"]["shape"] == [3]
    reference = provenance.persist_provenance(tmp_path, receipt)
    saved = provenance.load_provenance(tmp_path, reference)
    assert saved["provenance_sha256"] == reference["provenance_sha256"]
    changed_timing = {
        **receipt,
        "capture_ms": 999.0,
        "capture_components_ms": {"config": 500.0},
    }
    same_reference = provenance.persist_provenance(tmp_path, changed_timing)
    assert same_reference["provenance_path"] == reference["provenance_path"]
    assert reference["stored_bytes"] < reference["raw_bytes"]


def test_capture_records_config_after_engine_template_layer():
    calls = []

    def ensure_template_layer(cfg, symbol, is_long):
        if getattr(cfg, "template_layered", False):
            return cfg
        resolved = dataclasses.replace(cfg, threshold=0.75)
        resolved.template_layered = True
        calls.append((symbol, is_long))
        return resolved

    def simulate_one(npz, symbol, is_long, cfg):
        actual_cfg = ensure_template_layer(cfg, symbol, is_long)
        return {"ledger": [{"type": "OPEN", "price": actual_cfg.threshold}]}

    module = SimpleNamespace(
        simulate_one=simulate_one,
        ensure_template_layer=ensure_template_layer,
        __name__="test_engine",
        __file__=None,
    )
    with provenance.capture_engine_invocation(module) as capture:
        result = module.simulate_one(
            {"close": np.asarray([1.0])}, "TEST", True, _Config()
        )
    receipt = capture.result()
    cfg_doc = receipt["config"]
    restored = provenance.restore_config(cfg_doc, _Config)
    assert result["ledger"][0]["price"] == 0.75
    assert restored.threshold == 0.75
    assert calls == [("TEST", True)]


def test_capture_failure_is_reported_and_does_not_change_engine_result():
    class Unsupported:
        pass

    cfg = _Config()
    cfg.unsupported = Unsupported()
    expected = {"ledger": [{"type": "CLOSE", "bar": 3}]}
    module = SimpleNamespace(
        simulate_one=lambda *args: expected, __name__="test_engine", __file__=None
    )
    with provenance.capture_engine_invocation(module) as capture:
        actual = module.simulate_one({"close": np.asarray([1.0])}, "TEST", True, cfg)
    assert actual == expected
    result = capture.result()
    assert result["capture_status"] == "UNVERIFIED"
    assert "unsupported value type" in result["capture_errors"][0]
    assert module.simulate_one.__name__ == "<lambda>"


def test_capture_is_thread_scoped_and_does_not_capture_concurrent_unrelated_call():
    calls = []

    def simulate_one(npz, symbol, is_long, cfg):
        calls.append(symbol)
        return {"symbol": symbol}

    module = SimpleNamespace(
        simulate_one=simulate_one, __name__="test_engine", __file__=None
    )
    cfg = _Config()
    with provenance.capture_engine_invocation(module) as capture:
        thread = threading.Thread(
            target=module.simulate_one,
            args=({"close": np.asarray([1.0])}, "OTHER", False, cfg),
        )
        thread.start()
        thread.join()
        result = module.simulate_one(
            {"close": np.asarray([2.0])}, "CAPTURED", True, cfg
        )
    assert result == {"symbol": "CAPTURED"}
    assert sorted(calls) == ["CAPTURED", "OTHER"]
    assert capture.result()["invocation"]["symbol"] == "CAPTURED"


def test_capture_skips_filter_exemption_probe_passes():
    calls = []

    def simulate_one(npz, symbol, is_long, cfg):
        calls.append(symbol)
        if isinstance(getattr(cfg, "_FX_PROBE_SINK", None), dict):
            return {"probe": True}
        return {"ledger": [{"type": "OPEN", "bar": 1, "price": 1.0}]}

    module = SimpleNamespace(
        simulate_one=simulate_one, __name__="test_engine", __file__=None
    )
    npz = {"timestamps": np.asarray([1]), "close": np.asarray([1.0])}
    with provenance.capture_engine_invocation(module) as capture:
        for _ in range(4):
            probe = SimpleNamespace(_FX_PROBE_SINK={})
            assert module.simulate_one(npz, "PROBE", True, probe) == {"probe": True}
        result = module.simulate_one(npz, "ACTUAL", True, _Config())
    assert result["ledger"][0]["type"] == "OPEN"
    assert calls == ["PROBE"] * 4 + ["ACTUAL"]
    assert capture.result()["capture_status"] == "VERIFIED"
    assert capture.result()["invocation"]["symbol"] == "ACTUAL"


def test_real_small_frozen_npz_uses_engine_boundary_capture():
    from tools.opt import evaluate_v12

    npz_path = (
        Path(__file__).resolve().parents[1]
        / "backtest_v8"
        / "indicators"
        / "BTCUSDC.npz"
    )
    if not npz_path.is_file():
        pytest.skip("local frozen BTCUSDC NPZ is unavailable")
    prepared = evaluate_v12.prepare("BTCUSDC_LONG", 1, sim_account="trc")
    if not prepared:
        pytest.skip("local frozen NPZ could not prepare a 1-day engine input")
    with provenance.capture_engine_invocation() as capture:
        result = evaluate_v12.evaluate_prepared(prepared, {}, include_ledger=True)
    assert isinstance(result, dict)
    captured = capture.result()
    assert captured["capture_status"] == "VERIFIED"
    assert captured["invocation"]["symbol"] == "BTCUSDC"
    assert captured["npz_manifest"]["arrays"]
    assert (
        captured["engine_disk_source_sha256"]
        == hashlib.sha256(
            Path(captured["engine_disk_source_path"]).read_bytes()
        ).hexdigest()
    )
    import v12_quick_engine

    assert (
        captured["engine_loaded_entrypoint_sha256"]
        == hashlib.sha256(
            marshal.dumps(v12_quick_engine.simulate_one.__code__)
        ).hexdigest()
    )
    assert (
        captured["engine_loaded_entrypoint_scope"] == "simulate_one Python code object"
    )


def test_disk_source_hash_is_distinct_from_loaded_entrypoint_hash(tmp_path):
    import importlib.util

    source = tmp_path / "engine.py"
    source.write_text("def simulate_one(npz, symbol, is_long, cfg):\n    return 1\n")
    spec = importlib.util.spec_from_file_location("loaded_test_engine", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    loaded_hash = hashlib.sha256(
        marshal.dumps(module.simulate_one.__code__)
    ).hexdigest()
    source.write_text("def simulate_one(npz, symbol, is_long, cfg):\n    return 2\n")
    npz = {"timestamps": np.asarray([3]), "close": np.asarray([1.0])}
    with provenance.capture_engine_invocation(module) as capture:
        module.simulate_one(npz, "TEST", True, _Config())
    receipt = capture.result()
    assert (
        receipt["engine_disk_source_sha256"]
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert receipt["engine_loaded_entrypoint_sha256"] == loaded_hash
    assert (
        receipt["engine_disk_source_sha256"]
        != receipt["engine_loaded_entrypoint_sha256"]
    )
