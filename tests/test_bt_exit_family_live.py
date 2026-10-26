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
