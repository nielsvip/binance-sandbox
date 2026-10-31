import numpy as np

from vec_decisions.b12_inputs import resolve_b12_arrays, resolve_b12_scalar
from vec_decisions.check_entry_b12_wt_mom import b12_fires_scalar, b12_mask_vec


def _indicator_row():
    return {
        "wt1_3m": 10.0,
        "wt2_3m": 0.0,
        "wt_velocity_3m": 2.0,
        "wt1_15m": -10.0,
        "wt2_15m": 0.0,
        "wt_velocity_15m": -2.0,
        "wt1_1h": 10.0,
        "wt2_1h": 0.0,
    }


def test_b12_off_uses_15m_even_when_contradictory_3m_fields_exist():
    row = _indicator_row()
    values = resolve_b12_scalar(row, False)
    assert values == (-10.0, 0.0, -10.0, 0.0, 10.0, 0.0, -2.0)
    assert not b12_fires_scalar(*values[:2], *values[2:6], values[6], True)


def test_b12_on_uses_real_3m_and_does_not_fallback_when_missing():
    row = _indicator_row()
    assert resolve_b12_scalar(row, True) == (10.0, 0.0, -10.0, 0.0, 10.0, 0.0, 2.0)
    del row["wt1_3m"]
    assert resolve_b12_scalar(row, True) is None


def test_b12_vector_adapter_matches_scalar_for_both_switch_states():
    row = _indicator_row()
    npz = {key: np.array([value, value]) for key, value in row.items()}
    safe = lambda source, key, n: source[key]
    for use_ltf in (False, True):
        arrays = resolve_b12_arrays(npz, 2, use_ltf, safe)
        scalar = resolve_b12_scalar(row, use_ltf)
        assert arrays is not None
        for index in range(2):
            values = tuple(float(array[index]) for array in arrays)
            assert b12_fires_scalar(*values[:2], *values[2:6], values[6], True) == b12_mask_vec(
                *(np.array([value]) for value in values), True
            )[0]
            assert b12_fires_scalar(*scalar[:2], *scalar[2:6], scalar[6], True) == b12_fires_scalar(
                *values[:2], *values[2:6], values[6], True
            )


def test_b12_on_array_adapter_refuses_missing_ltf_instead_of_using_15m():
    row = _indicator_row()
    del row["wt_velocity_3m"]
    npz = {key: np.array([value]) for key, value in row.items()}
    assert resolve_b12_arrays(npz, 1, True, lambda source, key, n: source[key]) is None
