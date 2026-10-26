import json
import datetime as dt
import hashlib
from pathlib import Path

import pytest

from live_twins.vec_driven import vec_live_dir
from tools import vec_live_executor as lane


def test_live_directory_defaults_to_30d_and_requires_explicit_7d(monkeypatch, tmp_path):
    monkeypatch.delenv("VEC_LIVE_SCOPE", raising=False)
    assert vec_live_dir(tmp_path) == tmp_path / "data/vec_live"
    monkeypatch.setenv("VEC_LIVE_SCOPE", "7d")
    assert vec_live_dir(tmp_path) == tmp_path / "data/vec_live_7d"
    monkeypatch.setenv("VEC_LIVE_SCOPE", "typo")
    with pytest.raises(ValueError):
        vec_live_dir(tmp_path)


def test_7d_executor_loads_only_verified_account_matched_sets(monkeypatch, tmp_path):
    registry = tmp_path / "vec_driven.json"
    sets = tmp_path / "vec_driven_sets.json"
    candidates = tmp_path / "candidates.json"
    registry.write_text(json.dumps({
        "expires_utc": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(),
        "A_LONG": {"mode": "live", "account": "fin"},
        "B_LONG": {"mode": "live", "account": "fin"},
        "C_LONG": {"mode": "live", "account": "trc"},
        "D_LONG": {"mode": "live", "account": "fin"},
    }))
    sets.write_text(json.dumps({
        "A_LONG": {"mode": "live", "account": "fin", "window_days": 7, "verified": True, "source": "7d_optimized", "overrides": {"X": 1}},
        "B_LONG": {"mode": "live", "account": "fin", "window_days": 30, "verified": True, "overrides": {"X": 2}},
        "C_LONG": {"mode": "live", "account": "fin", "window_days": 7, "verified": True, "source": "7d_optimized", "overrides": {"X": 3}},
        "D_LONG": {"mode": "live", "account": "fin", "window_days": 30, "verified": True, "source": "verified_30d_fallback", "overrides": {"X": 4}},
    }))
    candidates.write_text(json.dumps({"B_LONG": {"overrides": {"X": 9}}}))
    (tmp_path / "validation.json").write_text(json.dumps({
        "day": dt.datetime.now(dt.timezone.utc).date().isoformat(),
        "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "queue_n": 1, "selected_7d": 1, "stage_only": True,
        "sets_sha256": hashlib.sha256(sets.read_bytes()).hexdigest(),
    }))
    monkeypatch.setattr(lane, "SCOPE", "7d")
    monkeypatch.setattr(lane, "VL", tmp_path)
    monkeypatch.setattr(lane, "VEC_DRIVEN", registry)
    monkeypatch.setattr(lane, "SETS", sets)
    monkeypatch.setattr(lane, "CANDIDATES", candidates)
    got = lane.load_sets()
    assert got == {"A_LONG": ({"mode": "live", "account": "fin"}, {"X": 1}),
                   "D_LONG": ({"mode": "live", "account": "fin"}, {"X": 4})}


def test_7d_executor_namespace_is_separate(monkeypatch, tmp_path):
    # The default module import remains the production 30D path.
    assert lane.VL.name == "vec_live"
    assert Path(lane.PUBLISH_DIR).name == "vec_live"


def test_7d_vector_stage_expires_and_refuses_tampered_sets(tmp_path):
    from tools.v15_preopen_7d_runtime import assert_vector_stage_ready

    now = dt.datetime.now(dt.timezone.utc)
    sets = tmp_path / "vec_driven_sets.json"
    sets.write_text('{"A_LONG": {"overrides": {"X": 1}}}')
    (tmp_path / "validation.json").write_text(json.dumps({
        "day": now.date().isoformat(), "created_utc": now.isoformat(),
        "queue_n": 1, "selected_7d": 1, "stage_only": True,
        "sets_sha256": hashlib.sha256(sets.read_bytes()).hexdigest(),
    }))
    registry = tmp_path / "vec_driven.json"
    registry.write_text(json.dumps({"expires_utc": (now - dt.timedelta(seconds=1)).isoformat()}))
    with pytest.raises(RuntimeError, match="expired"):
        assert_vector_stage_ready(tmp_path)
    registry.write_text(json.dumps({"expires_utc": (now + dt.timedelta(hours=1)).isoformat()}))
    sets.write_text("{}")
    with pytest.raises(RuntimeError, match="differ"):
        assert_vector_stage_ready(tmp_path)
