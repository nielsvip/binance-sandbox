from types import SimpleNamespace

import numpy as np
import pytest

import vec_decisions.asr_entry_stream as stream_module


class _FakeDeltaStream:
    def __init__(self, symbol, cfg, is_long, npz, use_ltf):
        self.calls = []

    def update_index(self, index, position_state, current_price=None):
        self.calls.append((index, position_state, current_price))
        return {"signal": SimpleNamespace(entry_long=True), "delta_entry_ok": True,
                "delta_active_tfs": 3, "timestamp": int(index), "update_count": len(self.calls)}


def _stream(monkeypatch, cfg=None):
    monkeypatch.setattr("vec_decisions.asr_delta_provider.DeltaTrackerNpZStream", _FakeDeltaStream)
    npz = {"timestamps": np.array([100, 200]), "close": np.array([10.0, 11.0]),
           "k_15m": np.array([20.0, 21.0]), "htf_align": np.array(["causal_v3"])}
    cfg = cfg or SimpleNamespace(DELTA_ENGINE_ENABLED=True, USE_1M_3M_SIGNALS_ENABLED=False)
    stream = stream_module.AsrEntryStream(npz, cfg, "TESTUSDC", True, delta_tracker_cfg={})
    assert stream.arrays["htf_align"].tolist() == ["causal_v3"]
    return stream


def test_asr_entry_stream_advances_delta_and_composes_candidate_once_per_bar(monkeypatch):
    stream = _stream(monkeypatch)
    seen = {}

    def prefix(npz, n, is_long, cfg, context, score=None):
        seen["prefix_ts"] = int(npz["timestamps"][0])
        seen["prefix_price"] = float(context["current_price"][0])
        return {"score": np.array([18.0]), "entry_allowed": np.array([True]), "masks": {}}

    def assemble(npz, cfg, state, n, is_long):
        seen["bound_score"] = float(state["score_before_dc"][0])
        return {"n": n, "cfg": cfg, "score_before_dc": state["score_before_dc"], "stages": {}}

    def compose(prefix_result, late_inputs, is_long, upstream, **kwargs):
        seen["post_late"] = kwargs["post_late_entry_state"]
        seen["lifecycle"] = kwargs["lifecycle_state"]
        return {"final_score": np.array([27.0]), "recommendation": np.array(["GOOD_BUY"], dtype=object),
                "entry_allowed": np.array([True]), "masks": {}, "complete_rate": False}

    monkeypatch.setattr(stream_module, "score_asr_entry_prefix_array", prefix)
    monkeypatch.setattr(stream_module, "assemble_asr_composer_inputs", assemble)
    monkeypatch.setattr(stream_module, "compose_asr_entry_candidate_array", compose)
    result = stream.update(
        0,
        {"current_price": 10.0, "is_hedge": False},
        {"current_price": 10.0},
        {"is_allowed": True},
        {"position_exists": False},
        position_state=None,
    )
    assert result["index"] == 0 and result["timestamp"] == 100
    assert seen["prefix_ts"] == 100 and seen["prefix_price"] == 10.0
    assert seen["bound_score"] == 18.0
    assert seen["post_late"]["delta_tracker_available"].tolist() == [True]
    assert seen["post_late"]["delta_entry_ok"].tolist() == [True]
    assert seen["post_late"]["delta_active_tfs"].tolist() == [3.0]
    assert stream.delta_stream.calls == [(0, None, 10.0)]
    assert stream.last_index == 0


def test_asr_entry_stream_requires_full_explicit_context_and_sequential_bars(monkeypatch):
    stream = _stream(monkeypatch)
    with pytest.raises(ValueError, match="prefix, late, upstream, and lifecycle"):
        stream.update(0, {}, None, {}, {})
    assert stream.last_index == -1
    with pytest.raises(ValueError, match="requires sequential index 0"):
        stream.update(1, {}, {}, {}, {})


def test_asr_entry_stream_delta_mode_requires_explicit_tracker_config(monkeypatch):
    with pytest.raises(ValueError, match="explicit delta_tracker_cfg"):
        stream_module.AsrEntryStream(
            {"timestamps": np.array([100]), "close": np.array([10.0])},
            SimpleNamespace(DELTA_ENGINE_ENABLED=True, USE_1M_3M_SIGNALS_ENABLED=False),
            "TESTUSDC", True,
        )
