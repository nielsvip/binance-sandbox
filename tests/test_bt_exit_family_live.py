"""VEC parity for the switch-backed bottom/top exit wrappers."""
import numpy as np


def test_bottom_top_rsi2_predicate_matches_live_and_vector_for_enabled_per_sym_switch(monkeypatch):
    from vec_decisions import bottom_top_signals as bt
    from vec_decisions import vec_parity_gates as gates
    from live_twins import vector_enforce as ve

    values = {"RSI2_XTREME_EXIT_ENABLED": True, "RSI2_XTREME_EXIT_TF": "1h"}
    get = lambda key, default=False: values.get(key, default)
    monkeypatch.setattr(gates, "_per_sym_knob_true", lambda _ss, key: values.get(key))
    ind = {"rsi_2_1h": 90.5}
    npz = {"rsi_2_1h": np.array([90.5])}
    fire, live_reason = bt.exit_fire_live(ind, True, get)
    vec_masks = bt.exit_fire_masks(npz, 1, True, get)
    assert fire is True and live_reason == "RSI2_XTREME_EXIT"
    assert vec_masks["RSI2_XTREME"].tolist() == [True]
    assert not gates.non_vector_exit_refused(
        "BT_RSI2_XTREME_EXIT_g=0.26%", True, True,
        sym_side="UNIUSDC_LONG", action="QUICK_CLOSE",
    )
    assert ve.family_of("BT_RSI2_XTREME_EXIT_g=0.26%") == "RSI2_XTREME_EXIT"


def test_bt_switch_exit_families_bypass_stale_bt_kill_narrowly(tmp_path, monkeypatch):
    import json
    import sys
    import types
    from live_twins import vector_enforce as ve

    kills = tmp_path / "kills.json"
    kills.write_text(json.dumps({"BT": {"disabled_at": 1.0, "why": "historical broad family kill"}}))
    monkeypatch.setattr(ve, "KILLS", kills)
    monkeypatch.setattr(ve, "VIOLATIONS", tmp_path / "violations.jsonl")
    monkeypatch.setattr(ve, "RETRACE_QUEUE", tmp_path / "retrace.jsonl")
    monkeypatch.setattr(ve, "MONITOR_LOG", tmp_path / "monitor.jsonl")
    ve._KILLS.update(loaded=0.0, killed={})
    config = types.ModuleType("config")
    config.Config = type("Config", (), {"VECTOR_ENFORCE_ENABLED": True, "VECTOR_BAR_WINDOW_S": 180.0})
    monkeypatch.setitem(sys.modules, "config", config)
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: True)

    for reason, family in (
        ("BT_RSI2_XTREME_EXIT_g=0.26%", "RSI2_XTREME_EXIT"),
        ("BT_STOCH_XTREME_EXIT_g=0.26%", "STOCH_XTREME_EXIT"),
        ("BT_WICK_REJECT_EXIT_g=0.26%", "WICK_REJECT_EXIT"),
    ):
        assert ve.family_of(reason) == family
        assert ve.check("QUICK_CLOSE", "men:UNIUSDC_LONG", reason) == (True, "VEC_SWITCH_EXIT")
    assert ve.check("QUICK_CLOSE", "men:UNIUSDC_LONG", "BT_UNMODELED_EXIT_g=0.26%") == (
        False, "KILLED_FAMILY_BT"
    )


def _simulate_bt_exit(close_at_signal, switch="RSI2_XTREME_EXIT", enabled=True):
    import v12_quick_engine as vq

    n = 110
    close = np.full(n, 100.0)
    close[1:3] = close_at_signal
    close[3:] = close_at_signal
    rsi = np.full(n, 50.0)
    rsi[2:] = 95.0
    ts = np.arange(1700000000, 1700000000 + n * 180, 180)
    npz = {
        "close": close,
        "close_3m": close,
        "close_15m": close,
        "close_5m": close,
        "timestamps": ts,
        "timestamp_3m": ts,
        "timestamp_15m": ts,
        "low_3m": close,
        "high_3m": close,
        "open_3m": close,
        "dc_low_4h": np.full(n, 80.0),
        "dc_high_4h": np.full(n, 120.0),
        "dc_low_15m": np.full(n, 80.0),
        "dc_high_15m": np.full(n, 120.0),
        "rsi_2_1h": rsi,
    }
    if switch == "STOCH_XTREME_EXIT":
        npz["stoch_k_1h"] = np.r_[np.full(2, 50.0), np.full(n - 2, 90.0)]
    elif switch == "WICK_REJECT_EXIT":
        npz["bar_upper_wick_1h"] = np.r_[np.full(2, 0.1), np.full(n - 2, 0.7)]
        npz["bar_body_ratio_1h"] = np.r_[np.full(2, 0.5), np.full(n - 2, 0.2)]
    for tf in ("15m", "1h", "4h", "D"):
        npz[f"wt1_{tf}"] = np.zeros(n)
        npz[f"wt2_{tf}"] = np.zeros(n)
    cfg = vq.QuickConfig()
    cfg.MODE = "crypto"
    cfg.BASE_TF = "3m"
    cfg.START_POSITION_SIZE = 500.0
    cfg.COOLDOWN_BARS = 1000
    cfg.COMMISSION_BUFFER_PCT = 0.08
    cfg.CRYPTO_ROUND_TRIP_COMMISSION_PCT = 0.04
    for key in vars(cfg):
        if key.endswith("_ENABLED") and any(
            token in key for token in ("EXIT", "TP", "STOP", "PROFIT", "SATOSHIT")
        ):
            setattr(cfg, key, False)
    setattr(cfg, f"{switch}_ENABLED", enabled)
    setattr(cfg, f"{switch}_TF", "1h")
    cfg.VEC_EXIT_SIG_IS_TRIGGER = False
    cfg.ABLATION_DISABLE_REENTRY = True
    cfg.BB_RECOVERY_EXIT_ENABLED_TRADIER = False
    return vq.simulate_one(npz, "RSI2_TEST_LONG", True, cfg, force_initial_seed=True)


def test_simulate_one_books_switch_reason_only_when_enabled_and_fee_positive():
    for switch in ("RSI2_XTREME_EXIT", "STOCH_XTREME_EXIT", "WICK_REJECT_EXIT"):
        reason = f"BT_{switch}"
        enabled = _simulate_bt_exit(100.30, switch=switch, enabled=True)
        closes = [
            row for row in enabled["ledger"]
            if row.get("type") == "CLOSE" and row.get("reason") == reason
        ]
        assert closes and closes[0]["pnl_pct"] > 0.0, (switch, enabled["ledger"])

        disabled = _simulate_bt_exit(100.30, switch=switch, enabled=False)
        assert not [row for row in disabled["ledger"] if row.get("reason") == reason]

        below_fee_floor = _simulate_bt_exit(100.01, switch=switch, enabled=True)
        assert not [row for row in below_fee_floor["ledger"] if row.get("reason") == reason]
