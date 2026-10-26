"""CYCLE_TP_TIERED must share its live/vector tier rule and fee-positive execution law."""
import numpy as np

from vec_decisions import twin_sizing_reduce as sr
from vec_decisions.net_exit_gate import vector_exit_event_allowed


def _get(key, default):
    values = {
        "CYCLE_TP_TIERED_ENABLED": True,
        "CYCLE_TP_TIERED_LEVELS": [0.0015, 0.003],
        "CYCLE_TP_TIERED_FRAC": 0.25,
        "NOLOSS_MIN_PROFIT_PCT": 0.05,
    }
    return values.get(key, default)


def test_cycle_tp_tier_threshold_and_fired_tier_one_shot():
    fired = set()
    assert sr.cycle_tp_tier(_get, 0.05, fired) is None
    assert sr.cycle_tp_tier(_get, 0.15, fired) == (0, 0.0015, 0.25)
    fired.add(0)
    assert sr.cycle_tp_tier(_get, 0.20, fired) is None
    assert sr.cycle_tp_tier(_get, 0.30, fired) == (1, 0.003, 0.25)
    fired.add(1)
    assert sr.cycle_tp_tier(_get, 0.40, fired) is None


def test_cycle_tp_reduce_requires_positive_net_after_modeled_and_estimated_fees():
    pos = {"avg_price": 100.0}
    before = dict(pos)
    fee_negative = {"type": "REDUCE", "reason": "CYCLE_TP_TIERED_T0_0.15pct_gain0.05", "qty": 1.0, "price": 100.05}
    profitable = {**fee_negative, "price": 100.20}
    assert not vector_exit_event_allowed(fee_negative, pos, before, is_long=True, half_fee=0.0004, additional_fee_pct=0.04)
    assert vector_exit_event_allowed(profitable, pos, before, is_long=True, half_fee=0.0004, additional_fee_pct=0.04)
    short_profit = {**fee_negative, "price": 99.80}
    assert vector_exit_event_allowed(short_profit, pos, before, is_long=False, half_fee=0.0004, additional_fee_pct=0.04)


def test_v12_emits_tiered_reduce_once_with_live_reason_and_quantity():
    import v12_quick_engine as vq

    n = 100
    close = np.linspace(100.0, 100.5, n)
    ts = np.arange(1700000000, 1700000000 + n * 180, 180)
    npz = {"close": close, "close_3m": close, "close_15m": close, "close_5m": close, "timestamps": ts, "timestamp_3m": ts, "timestamp_15m": ts, "low_3m": close, "high_3m": close, "open_3m": close, "dc_low_4h": np.full(n, 80.0), "dc_high_4h": np.full(n, 120.0), "dc_low_15m": np.full(n, 90.0), "dc_high_15m": np.full(n, 130.0)}
    for tf in ("15m", "1h", "4h", "D"):
        npz[f"wt1_{tf}"] = np.zeros(n)
        npz[f"wt2_{tf}"] = np.zeros(n)
    cfg = vq.QuickConfig()
    cfg.MODE = "crypto"
    cfg.BASE_TF = "3m"
    cfg.START_POSITION_SIZE = 500.0
    cfg.COOLDOWN_BARS = 0
    cfg.CYCLE_TP_TIERED_ENABLED = True
    cfg.CYCLE_TP_TIERED_LEVELS = [0.0015, 0.003]
    cfg.CYCLE_TP_TIERED_FRAC = 0.25
    cfg.NOLOSS_MIN_PROFIT_PCT = 0.05
    cfg.COMMISSION_BUFFER_PCT = 0.08
    cfg.CRYPTO_ROUND_TRIP_COMMISSION_PCT = 0.04
    cfg.PROFIT_TARGET_ENABLED = False
    cfg.STOP_LOSS_ENABLED = False
    cfg.ALL_TF_AGAINST_FORCE_CLOSE_ENABLED = False
    result = vq.simulate_one(npz, "CYCLE_LONG", True, cfg, force_initial_seed=True)
    rows = [e for e in result["ledger"] if str(e.get("reason") or "").startswith("CYCLE_TP_TIERED_")]
    assert rows
    assert rows[0]["reason"].startswith("CYCLE_TP_TIERED_T0_0.15pct_gain")
    assert np.isclose(rows[0]["qty"], 1.25)
    tiers = [int(e["reason"].split("_")[3][1:]) for e in rows]
    assert len(tiers) == len(set(tiers))
    close_fee_cfg = vq.QuickConfig()
    close_fee_cfg.MODE = "crypto"
    close_fee_cfg.BASE_TF = "3m"
    close_fee_cfg.START_POSITION_SIZE = 500.0
    close_fee_cfg.COOLDOWN_BARS = 0
    close_fee_cfg.CYCLE_TP_TIERED_ENABLED = True
    close_fee_cfg.CYCLE_TP_TIERED_LEVELS = [0.0015, 0.003]
    close_fee_cfg.CYCLE_TP_TIERED_FRAC = 0.25
    close_fee_cfg.NOLOSS_MIN_PROFIT_PCT = 0.05
    close_fee_cfg.COMMISSION_BUFFER_PCT = 0.20
    close_fee_cfg.CRYPTO_ROUND_TRIP_COMMISSION_PCT = 0.04
    close_fee_cfg.PROFIT_TARGET_ENABLED = False
    close_fee_cfg.STOP_LOSS_ENABLED = False
    close_fee_cfg.ALL_TF_AGAINST_FORCE_CLOSE_ENABLED = False
    small_gain = np.linspace(100.0, 100.19, n)
    for key in ("close", "close_3m", "close_15m", "close_5m", "low_3m", "high_3m", "open_3m"):
        npz[key] = small_gain
    fee_blocked = vq.simulate_one(npz, "CYCLE_FEE_LONG", True, close_fee_cfg, force_initial_seed=True)
    assert not [e for e in fee_blocked["ledger"] if str(e.get("reason") or "").startswith("CYCLE_TP_TIERED_")]


def test_cycle_tp_is_registered_as_a_vectorized_reason_family(monkeypatch):
    from live_twins import vector_enforce as ve
    from vec_paths import vec_parity_gate as pg
    from vec_decisions import vec_parity_gates as pgates
    import per_sym_store as pss

    reason = "CYCLE_TP_TIERED_T0_0.15pct_gain0.27"
    monkeypatch.setattr(pss, "get_overrides", lambda key: {"CYCLE_TP_TIERED_ENABLED": True})
    assert ve.family_of(reason) == "CYCLE_TP_TIERED"
    assert "CYCLE_TP_TIERED" in ve.VEC_SWITCH_EXIT_FAMILIES
    assert pg.is_vec_achievable(reason)
    assert not pgates.non_vector_exit_refused(reason, True, True, sym_side="MEN_LONG", action="REDUCE")
