"""DELTA zone BASELINE-BOUNCE twin (USER 2026-10-24: every path a switch + vectorized
version). Pins live fidelity of the scalar, scalar/mask agreement, presence
semantics (absent key = skip, never fabricated), and the RSRUSDT indifference
case (3m-missing still fires)."""
import numpy as np

import vec_decisions.delta_zone_baseline as dzb


def _cfg(**kw):
    base = {"rz_entry_enabled": True, "rz_baseline_tol": 0.03, "rz_k_exit": 80.0,
            "rz_div_block_min": 2, "rz_top_bb": 0.85, "rz_bot_bb": 0.15,
            "rz_zscore_zone_enabled": True, "rz_ltf_micro": "3m",
            "rz_baseline_bounce_short_enabled": True}
    base.update(kw)
    return base


def _g():
    c = _cfg()
    return lambda kk, dd=None: c.get(kk, dd)


def _ind(**kw):
    ind = {"current_price": 100.0, "dc_basis_15m": 100.1, "wt_velocity_15m": 2.0,
           "wt_velocity_1h": 1.0, "wt_momentum_state_4h": 0, "wt_momentum_state_D": 0,
           "stoch_k_1h": 40.0, "wt_divergence_1h": 0, "wt_divergence_4h": 0,
           "wt_divergence_D": 0, "wt_zscore_1h": 0.0, "wt_zscore_4h": 0.0,
           "bb_pct_b_1h": 0.5, "dc_position_1h": 0.5, "bb_upper_1h": 110.0,
           "bb_lower_1h": 90.0, "dc_high_1h": 112.0, "dc_low_1h": 88.0,
           "wt1_15m": 10.0, "wt1_prev_15m": 5.0}
    ind.update(kw)
    return ind


def test_long_bounce_fires_on_basis_touch():
    ok, tf = dzb.baseline_bounce_fires_scalar(_ind(), True, _g(), {})
    assert ok is True and tf == "15m"


def test_no_touch_no_fire():
    ok, _ = dzb.baseline_bounce_fires_scalar(_ind(dc_basis_15m=120.0), True, _g(), {})
    assert ok is False


def test_bear_dominance_blocks_long():
    ok, _ = dzb.baseline_bounce_fires_scalar(
        _ind(wt1_15m=1.0, wt1_prev_15m=9.0), True, _g(), {})
    assert ok is False


def test_gate_off_silent():
    c = _cfg(rz_entry_enabled=False)
    ok, _ = dzb.baseline_bounce_fires_scalar(_ind(), True, lambda k, d=None: c.get(k, d), {})
    assert ok is False


def test_short_mirror():
    ind = _ind(current_price=100.0, dc_basis_15m=99.9, wt_velocity_15m=-2.0,
               wt_velocity_1h=-1.0, stoch_k_1h=60.0, wt1_15m=1.0, wt1_prev_15m=9.0)
    ok, tf = dzb.baseline_bounce_fires_scalar(ind, False, _g(), {})
    assert ok is True and tf == "15m"


def test_encoded_htf_vetoes_match_npz_numeric_states():
    for value in (-1, "IMPULSE_DOWN"):
        ind = _ind(wt_momentum_state_4h=value, wt_momentum_state_D=value)
        assert dzb.baseline_bounce_fires_scalar(ind, True, _g(), {})[0] is False
    for value in (-1, "BEAR"):
        ind = _ind(wt_divergence_1h=value, wt_divergence_4h=value)
        assert dzb.baseline_bounce_fires_scalar(ind, True, _g(), {})[0] is False


def test_delta_weights_follow_native_venue_defaults():
    crypto = lambda key, default=None: {"MODE": "crypto"}.get(key, default)
    stocks = lambda key, default=None: {"MODE": "tradier"}.get(key, default)
    assert dzb._tf_weights(crypto) == {"3m": 3.0, "15m": 2.0, "1h": 1.0, "4h": 1.0, "D": 0.5}
    assert dzb._tf_weights(stocks) == {"1h": 2.0, "4h": 3.0, "D": 2.0}


def test_mask_matches_scalar_on_synthetic():
    n = 60
    close = np.linspace(90, 110, n)
    basis = np.full(n, 100.0)
    vel = np.full(n, 2.0)
    npz = {"close": close, "dc_basis_15m": basis, "wt_velocity_15m": vel,
           "wt_velocity_1h": vel, "wt_momentum_state_4h": np.zeros(n),
           "wt_momentum_state_D": np.zeros(n), "stoch_k_1h": np.full(n, 40.0),
           "wt_divergence_1h": np.zeros(n), "wt_divergence_4h": np.zeros(n),
           "wt_divergence_D": np.zeros(n), "wt_zscore_1h": np.zeros(n),
           "wt_zscore_4h": np.zeros(n), "bb_pct_b_1h": np.full(n, 0.5),
           "dc_position_1h": np.full(n, 0.5), "bb_upper_1h": np.full(n, 200.0),
           "bb_lower_1h": np.full(n, 10.0), "dc_high_1h": np.full(n, 200.0),
           "dc_low_1h": np.full(n, 10.0), "wt1_15m": np.linspace(0, 20, n)}

    class _S:
        def __init__(self, d):
            self._d = d

        def __contains__(self, k):
            return k in self._d

    def _safe(d, k, nn, default=0.0):
        a = np.asarray(npz[k], dtype=float)
        assert a.shape[0] == nn
        return a

    class _C:
        rz_entry_enabled = True
        rz_baseline_tol = 0.03
        rz_k_exit = 80.0
        rz_div_block_min = 2
        rz_top_bb = 0.85
        rz_bot_bb = 0.15
        rz_zscore_zone_enabled = True
        rz_ltf_micro = "3m"
        rz_baseline_bounce_short_enabled = True
        tf_weights = {}

    mask = dzb.baseline_bounce_mask(_S(npz), n, True, _C(), _safe)
    assert mask.dtype == bool and mask.any(), "touch band must fire somewhere"
    # scalar replay of firing bars agrees (state primed with prior bar)
    cfg = _C()
    get = lambda k, d=None: getattr(cfg, k, d)
    idx = sorted(int(i) for i in np.where(mask)[0])[:4]
    assert idx, "need firing bars"
    for i in idx:
        assert i > 1
        prev = {}
        for k in ("wt1_15m",):
            prev[k] = float(npz[k][i - 1])
        ind = {"current_price": float(close[i]), "dc_basis_15m": float(basis[i]),
               "wt_velocity_15m": float(vel[i]), "wt_velocity_1h": float(vel[i]),
               "wt_momentum_state_4h": 0, "wt_momentum_state_D": 0,
               "stoch_k_1h": 40.0, "wt1_15m": float(npz["wt1_15m"][i])}
        ok, _ = dzb.baseline_bounce_fires_scalar(ind, True, get, prev)
        assert ok is True, f"scalar disagrees at bar {i}"
