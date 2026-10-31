import numpy as np

from vec_decisions import asr_ordered_composer as composer
from vec_decisions import asr_entry_prefix


def test_raw_candidate_binds_actual_delta_tracker_output(monkeypatch):
    n = 5
    ts = np.arange(n, dtype=np.int64) * 900 + 1_700_000_000
    npz = {
        "timestamps": ts,
        "wt1_15m": np.array([0, 1, 3, 2, 0], dtype=float),
        "wt2_15m": np.zeros(n),
        "wt_score_15m": np.array([0, 1, 3, 2, 0], dtype=float),
        "wt_velocity_15m": np.array([0, 1, 2, 1, -1], dtype=float),
        "wt_bullish_15m": np.array([0, 1, 1, 1, 0], dtype=np.int8),
        "dc_position_15m": np.array([.5, .4, .3, .5, .7]),
        "dc_width_15m": np.ones(n),
    }
    cfg = {"DELTA_ENGINE_ENABLED": True, "USE_1M_3M_SIGNALS_ENABLED": True}
    price = np.full(n, 10.0)
    captured = {}
    monkeypatch.setattr(asr_entry_prefix, "score_asr_entry_prefix_array", lambda *a, **k: {"score": np.ones(n), "entry_allowed": np.ones(n, dtype=bool), "masks": {}})
    monkeypatch.setattr(composer, "assemble_asr_composer_inputs", lambda *a, **k: {"n": n, "cfg": cfg})
    def candidate(prefix, late, side, upstream, **kwargs):
        captured.update(kwargs)
        return {"masks": {}, "entry_allowed": np.ones(n, dtype=bool)}
    monkeypatch.setattr(composer, "compose_asr_entry_candidate_array", candidate)
    late_state = {
        "current_price": price,
        "delta_tracker_available": True,
        "delta_tracker_replay": {
            "symbol": "TESTUSDC",
            "tracker_cfg": {"tf_weights": {"15m": 1.0}, "rz_entry_enabled": False, "rz_exit_enabled": False},
            "timestamps": ts,
            "position_states": [None] * n,
        },
    }
    result = composer.evaluate_asr_entry_candidate_array(
        npz, n, True, cfg, {"current_price": price}, late_state, np.ones(n, dtype=bool)
    )
    post = captured["post_late_entry_state"]
    assert post["delta_tracker_available"].all()
    assert post["delta_signal_present"].all()
    assert post["delta_entry_ok"].shape == (n,)
    assert post["delta_active_tfs"].shape == (n,)
    assert post["delta_tracker_state_scope"] == "fresh_tracker_replayed_from_first_supplied_row"
    assert "delta_tracker_missing_inputs" in post
    assert result["complete_rate"] is False


def test_delta_active_candidate_requires_explicit_replay_context(monkeypatch):
    n = 2
    price = np.full(n, 10.0)
    cfg = {"DELTA_ENGINE_ENABLED": True}
    monkeypatch.setattr(asr_entry_prefix, "score_asr_entry_prefix_array", lambda *a, **k: {"score": np.ones(n), "entry_allowed": np.ones(n, dtype=bool), "masks": {}})
    monkeypatch.setattr(composer, "assemble_asr_composer_inputs", lambda *a, **k: {"n": n, "cfg": cfg})
    try:
        composer.evaluate_asr_entry_candidate_array(
            {"timestamps": np.array([1, 2])}, n, True, cfg, {"current_price": price},
            {"current_price": price, "delta_tracker_available": True}, np.ones(n, dtype=bool)
        )
    except ValueError as error:
        assert "delta_tracker_replay" in str(error)
    else:
        raise AssertionError("active DeltaTracker without exact replay context must fail closed")
