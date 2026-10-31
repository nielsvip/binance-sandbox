import numpy as np
import pytest

from vec_decisions.asr_input_adapter import paired_npz_view
from vec_decisions.asr_input_contract import ASRInputUnavailable, bind_asr_stage_arrays


def test_stage_binding_uses_policy_selected_npz_and_explicit_state_arrays():
    n = 4
    base = {"dc_low_15m": np.arange(n, dtype=float), "dc_high_15m": np.arange(n, dtype=float) + 2}
    causal = {
        "history_present": np.ones(n, dtype=bool), "win_rate": np.full(n, 70.0),
        "total_trades": np.full(n, 3), "total_realized_pnl": np.zeros(n),
    }
    bound = bind_asr_stage_arrays(base, {"USE_1M_3M_SIGNALS_ENABLED": False}, causal, n, True, stages=("history",))
    assert bound["is_ltf_enabled"] is False
    assert bound["is_long"] is True
    assert np.array_equal(bound["stages"]["history"]["win_rate"], causal["win_rate"])
    selected = paired_npz_view(base, False)
    assert np.array_equal(selected["dc_low_3m"], base["dc_low_15m"])


def test_binding_rejects_missing_native_inputs_and_wrong_array_lengths():
    n = 3
    with pytest.raises(ASRInputUnavailable, match="dc_structure: missing NPZ"):
        bind_asr_stage_arrays({}, {"USE_1M_3M_SIGNALS_ENABLED": True}, {}, n, False, stages=("dc_structure",))
    with pytest.raises(ASRInputUnavailable, match="expected 3"):
        bind_asr_stage_arrays({}, {}, {"history_present": np.ones(n, bool), "win_rate": np.ones(1), "total_trades": np.ones(n), "total_realized_pnl": np.ones(n)}, n, True, stages=("history",))
    with pytest.raises(ASRInputUnavailable, match="scalar supplied"):
        bind_asr_stage_arrays({}, {}, {"history_present": True, "win_rate": np.ones(n), "total_trades": np.ones(n), "total_realized_pnl": np.ones(n)}, n, True, stages=("history",))


def test_ltf_on_does_not_synthesize_micro_inputs_from_15m():
    n = 2
    npz = {"dc_low_15m": np.ones(n)}
    with pytest.raises(ASRInputUnavailable, match="dc_structure: missing NPZ"):
        bind_asr_stage_arrays(npz, {"USE_1M_3M_SIGNALS_ENABLED": True}, {}, n, False, stages=("dc_structure",))
