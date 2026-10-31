import sys

import numpy as np

import v12_quick_engine as v12

sys.path.insert(0, "tests")


def test_simulate_one_passes_weighted_position_basis_to_ppl_after_augment(monkeypatch):
    n = 5
    prices = np.array([100.0, 105.0, 105.0, 105.0, 105.0])
    timestamps = np.arange(n, dtype=float) * 900.0 + 1700000000.0
    data = {
        "timestamps": timestamps,
        "close": prices.copy(),
        "close_15m": prices.copy(),
        "open_15m": prices.copy(),
        "high_15m": prices.copy(),
        "low_15m": prices.copy(),
        "wt1_3m": np.array([0.0, 2.0, 2.0, 2.0, 2.0]),
        "wt2_3m": np.ones(n),
        "wt1_15m": np.array([0.0, 2.0, 2.0, 2.0, 2.0]),
        "wt2_15m": np.ones(n),
        "wt1_1h": np.ones(n),
        "wt2_1h": np.zeros(n),
        "wt1_4h": np.ones(n),
        "wt2_4h": np.zeros(n),
        "wt1_D": np.ones(n),
        "wt2_D": np.zeros(n),
        "wt_trough_structure_15m": np.zeros(n),
        "wt_peak_structure_15m": np.zeros(n),
        "dc_low_15m": np.full(n, 80.0),
        "dc_high_15m": np.full(n, 120.0),
        "dc_low_4h": np.full(n, 70.0),
        "dc_high_4h": np.full(n, 130.0),
    }
    cfg = v12.QuickConfig()
    cfg.apply_cat_side_defaults("CRYPTO_LONG")
    for key, value in {
        "MODE": "crypto",
        "BASE_TF": "15m",
        "START_POSITION_SIZE": 500.0,
        "COOLDOWN_BARS": 0,
        "MIN_HOLD_BARS": 0,
        "MIN_GAIN": 3.0,
        "USE_1M_3M_SIGNALS_ENABLED": False,
        "WT_WINNER_PULLBACK_AUGMENT_ENABLED": True,
        "HTF_DIRECTION_GATE_ENABLED": False,
        "HTF_GATE_APPLY_TO_AUGMENT": False,
        "STRUCTURAL_RANGE_SHIFT_EXIT": False,
        "LIVE_EXIT_CHAIN_ENABLED": False,
        "STOP_LOSS_ENABLED": False,
        "NOLOSS_ENABLED": False,
        "PROFIT_TARGET_ENABLED": False,
        "PEAK_GIVEBACK_PROTECTION_ENABLED": False,
        "AUGMENTED_DC_BREAK_ENABLED": False,
        "BOUNCE_AUGMENT_ENABLED": False,
        "PYRAMID_ENABLED": False,
        "WT_CROSS_EXIT_ENABLED": False,
        "ULTIMATE_DC_HARD_STOP_ENABLED": False,
        "PARTIAL_PROFIT_LOCK_ENABLED": False,
        "CYCLE_TP_TIERED_ENABLED": False,
        "QUICK_CYCLE_TP_ENABLED": False,
        "HAIKU_WINNER_AUGMENT_ENABLED": False,
    }.items():
        setattr(cfg, key, value)
    cfg._PARITY_PREFIX_EVAL = True

    seen = []

    def record_ppl(config, is_tradier, is_long, px, entry_px, gain_pct, state):
        seen.append((float(px), float(entry_px)))
        return "", 0.0, "", state

    monkeypatch.setattr(v12.vec_decisions.reduce_profit_lock, "ppl_step", record_ppl)
    result = v12.simulate_one(data, "PPL_WEIGHTED_BASIS_TEST", True, cfg, force_initial_seed=True)
    augment = next(row for row in result["ledger"] if row.get("type") == "AUGMENT")
    assert augment["bar"] == 1
    bar_one_basis = next(entry for px, entry in seen if px == 105.0)
    assert 100.0 < bar_one_basis < 105.0


def test_tradier_simulate_one_passes_weighted_stock_basis_to_ppl_after_native_augment(monkeypatch):
    from test_tradier_reviewed_exit_ledgers import _base_npz, _cfg

    npz = _base_npz()
    npz["low"] = np.full(len(npz["close"]), 100.0)
    augment_bar = 201
    npz["close"][augment_bar] = 99.8
    npz["low"][augment_bar] = 99.8
    npz["close_15m"][augment_bar] = 99.8
    npz["low_15m_prev"][augment_bar - 1] = 98.0
    npz["low_15m_prev"][augment_bar] = 99.0
    npz["dc_low_15m_prev"][augment_bar - 1] = 94.0
    npz["dc_low_15m_prev"][augment_bar] = 95.0
    cfg = _cfg()
    cfg.AUGMENT_AT_LOSS_ENABLED = True
    seen = []

    def record_ppl(config, is_tradier, is_long, px, entry_px, gain_pct, state):
        if is_tradier:
            seen.append((int(np.where(npz["close"] == px)[0][0]), float(entry_px)))
        return "", 0.0, "", state

    monkeypatch.setattr(v12.vec_decisions.reduce_profit_lock, "ppl_step", record_ppl)
    result = v12.simulate_one(npz, "SYNTH", True, cfg, force_initial_seed=True)
    augment = next(row for row in result["ledger"] if row.get("type") == "AUGMENT")
    assert augment["bar"] == augment_bar
    weighted_basis = next(entry for bar, entry in seen if bar == augment_bar)
    assert 99.8 < weighted_basis < 100.0


def test_tradier_simulate_one_blocks_red_mark_close_with_positive_lifecycle_pnl(monkeypatch):
    from test_tradier_reviewed_exit_ledgers import _base_npz, _cfg
    from vec_decisions import net_exit_gate

    npz = _base_npz()
    npz["close"][201] = 110.0
    npz["close_15m"][201] = 110.0
    npz["close"][202:] = 99.0
    npz["close_15m"][202:] = 99.0
    npz["dc_low_15m"] = np.full(len(npz["close"]), 80.0)
    npz["dc_low_4h"] = np.full(len(npz["close"]), 70.0)
    npz["dc_high_4h"] = np.full(len(npz["close"]), 130.0)
    npz["low"] = np.full(len(npz["close"]), 100.0)
    npz["low"][202:] = 99.0
    cfg = _cfg()
    decisions = []

    def action(config, is_tradier, is_long, px, entry_px, gain_pct, state):
        if px == 110.0:
            return "REDUCE", 0.5, "TEST_PROFIT_REDUCE", state
        if px == 99.0:
            return "CLOSE_REMAINDER", 1.0, "TEST_RED_MARK_CLOSE", state
        return "", 0.0, "", state

    original = net_exit_gate.vector_exit_event_allowed

    def observe(event, position, before_position, *args, **kwargs):
        accepted = original(event, position, before_position, *args, **kwargs)
        if event.get("reason") == "TEST_RED_MARK_CLOSE":
            decisions.append((dict(event), accepted))
        return accepted

    monkeypatch.setattr(v12.vec_decisions.reduce_profit_lock, "ppl_step", action)
    monkeypatch.setattr(net_exit_gate, "vector_exit_event_allowed", observe)
    result = v12.simulate_one(npz, "SYNTH", True, cfg, force_initial_seed=True)
    assert decisions, result.get("ledger")
    close_decision, accepted = next(row for row in decisions if row[0].get("bar_exit") == 202)
    assert close_decision["pnl_dollars"] > 0.0
    assert close_decision["exit_price"] < close_decision["entry_price"]
    assert accepted is False
    assert not any(row.get("type") == "CLOSE" and row.get("bar_exit") == 202 for row in result["ledger"])
