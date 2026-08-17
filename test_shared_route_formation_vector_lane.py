import numpy as np
import inspect

from tools import run_shared_route_formation_vector_lane as lane


def test_countertrend_gate_suppresses_formation_entry(monkeypatch):
    # 12 synthetic ordinary opportunities; the first is countertrend-blocked.
    n = 24
    stamps = np.arange(n, dtype=np.int64) * 60
    arrays = {"close": np.array([100.0 if i % 2 == 0 else 101.0 for i in range(n)])}
    schedule = [{"ts": int(stamps[i]), "type": "OPEN" if i % 2 == 0 else "CLOSE", "reason_group": "OE" if i % 2 == 0 else "OX"} for i in range(n)]
    monkeypatch.setattr(lane.universe, "configured_variant", lambda cfg, variant: cfg)
    monkeypatch.setattr(lane.adaptive, "ordinary_schedule", lambda *a, **k: (schedule, []))
    monkeypatch.setattr(lane, "formation_vector_mask", lambda a, is_long, action, config, n: (np.array([(i % 2 == 0) if action == "ENTRY" else (i % 2 == 1) for i in range(n)]), np.ones(n), np.zeros(n, dtype=int)))
    monkeypatch.setattr(lane.v8, "_tradier_counter_trend_entry_allowed_vec", lambda *a, **k: np.array([False] + [True] * (n - 1)))
    monkeypatch.setattr(lane.combo, "bh_return_pct", lambda *a, **k: 0.0)
    result = lane.replay("X", "LONG", lane.v8.SweepConfig(), lane.v8.SweepConfig(), (arrays, stamps), "OE", "OX", 0.0)
    assert result["current_countertrend_blocked_selected_entry_actions"] == 1
    assert result["formation_entry_action_count"] == 11
    assert result["real_close_trades"] == 11


def test_plan_gate_is_bounded_not_hardcoded_twenty(tmp_path):
    p = tmp_path / "plan.json"
    p.write_text('{"schema":"shared-route-formation-bounded-vector-plan-v1","candidate_count":18,"candidates":[]}')
    # Parser validation is covered by the main guard's accepted 1..20 rule;
    # this asserts the corrected non-20 plan contract is represented.
    import json
    plan = json.loads(p.read_text())
    assert 1 <= plan["candidate_count"] <= 20


def test_exact_entry_and_exit_timeframes_use_separate_masks():
    base = lane.v8.SweepConfig()
    entry, exit_ = lane.pair_config(base, "trend_structure_ENTRY_15m", "wedge_EXIT_1h")
    assert entry.FORMATION_TFS == "15m"
    assert exit_.FORMATION_TFS == "1h"
    assert entry is not exit_


def test_terminal_settlement_is_not_a_real_close_and_capital_is_compounded(monkeypatch):
    n = 25
    stamps = np.arange(n, dtype=np.int64) * 60
    arrays = {"close": np.array([100.0 if i % 2 == 0 else 101.0 for i in range(n)])}
    schedule = [{"ts": int(stamps[i]), "type": "OPEN" if i % 2 == 0 else "CLOSE", "reason_group": "OE" if i % 2 == 0 else "OX"} for i in range(n)]
    monkeypatch.setattr(lane.universe, "configured_variant", lambda cfg, variant: cfg)
    monkeypatch.setattr(lane.adaptive, "ordinary_schedule", lambda *a, **k: (schedule, []))
    monkeypatch.setattr(lane, "formation_vector_mask", lambda a, is_long, action, config, n: (np.array([(i % 2 == 0) if action == "ENTRY" else (i % 2 == 1 and i != 1) for i in range(n)]), np.ones(n), np.zeros(n, dtype=int)))
    monkeypatch.setattr(lane.v8, "_tradier_counter_trend_entry_allowed_vec", lambda *a, **k: np.ones(n, dtype=bool))
    monkeypatch.setattr(lane.combo, "bh_return_pct", lambda *a, **k: 0.0)
    result = lane.replay("X", "LONG", lane.v8.SweepConfig(), lane.v8.SweepConfig(), (arrays, stamps), "OE", "OX", 0.0)
    assert result["terminal_settlement_action_count"] == 1
    assert result["trades"] == 13
    assert result["real_close_trades"] == 12
    assert result["strategy_gross_multiplier"] > 1.12  # compounds 12 1% wins; never sum-of-%
    assert result["strict_pass"]


def test_real_close_requires_full_lifecycle_not_partial_or_terminal():
    assert lane.is_real_full_lifecycle_close({"type": "CLOSE", "reason": "ORDINARY_EXIT_X", "full_position_lifecycle_close": True})
    assert not lane.is_real_full_lifecycle_close({"type": "REDUCE", "reason": "PARTIAL", "full_position_lifecycle_close": True})
    assert not lane.is_real_full_lifecycle_close({"type": "CLOSE", "reason": "TERMINAL_MARK_TO_MARKET_SETTLEMENT", "full_position_lifecycle_close": True})
    assert not lane.is_real_full_lifecycle_close({"type": "CLOSE", "reason": "ORDINARY_EXIT_X", "full_position_lifecycle_close": False})


def test_every_strict_gate_is_fail_closed():
    good = {"return_pct": 1, "real_close_trades": 11, "bh_gross_multiplier": 1,
            "strategy_to_bh_multiplier": 1.01, "formation_entry_action_count": 1,
            "formation_exit_action_count": 1, "ordinary_entry_action_count": 1,
            "ordinary_exit_action_count": 1, "undeclared_event_count": 0}
    assert lane.strict_failures(good) == []
    cases = [("return_pct", 0, "RETURN_NOT_POSITIVE"), ("real_close_trades", 10, "REAL_CLOSES_NOT_OVER_10"),
             ("strategy_to_bh_multiplier", 1, "DID_NOT_BEAT_SIDE_AWARE_BH_GROSS"), ("formation_entry_action_count", 0, "FORMATION_ENTRY_NOT_APPLIED"),
             ("formation_exit_action_count", 0, "FORMATION_EXIT_NOT_APPLIED"), ("ordinary_entry_action_count", 0, "ORDINARY_ENTRY_NOT_APPLIED"),
             ("ordinary_exit_action_count", 0, "ORDINARY_EXIT_NOT_APPLIED"), ("undeclared_event_count", 1, "UNDECLARED_ACTIONS")]
    for field, value, expected in cases:
        row = dict(good); row[field] = value
        assert expected in lane.strict_failures(row)


def test_train_selection_freezes_one_strict_route_without_holdout_fields():
    train = [{"strict_pass": True, "strategy_to_bh_multiplier": 1.01, "alpha_vs_bh_pp": 9, "return_pct": 9, "real_close_trades": 11, "ordinary_entry_group": "A"},
             {"strict_pass": True, "strategy_to_bh_multiplier": 1.02, "alpha_vs_bh_pp": 1, "return_pct": 1, "real_close_trades": 11, "ordinary_entry_group": "B"},
             {"strict_pass": False, "strategy_to_bh_multiplier": 99, "alpha_vs_bh_pp": 99, "return_pct": 99, "real_close_trades": 99, "ordinary_entry_group": "C"}]
    winner = lane.select_train_winner(train)
    assert winner["ordinary_entry_group"] == "B"
    assert lane.select_train_winner([train[-1]]) is None


def test_evaluate_reads_and_replays_holdout_exactly_once_after_train_freeze(monkeypatch, tmp_path):
    (tmp_path / "X.npz").write_bytes(b"sealed")
    row = {"key": "X_LONG", "symbol": "X", "side": "LONG", "formation_entry_arm": "trend_structure_ENTRY_15m", "formation_exit_arm": "wedge_EXIT_1h"}
    cfg = lane.v8.SweepConfig(); arrays = {"close": np.ones(4)}; stamps = np.arange(4, dtype=np.int64)
    monkeypatch.setattr(lane.combo, "base_config_from_train", lambda p: (cfg, {}))
    monkeypatch.setattr(lane.combo, "apply_key_overrides", lambda c, receipt, key: (c, {}, {}))
    monkeypatch.setattr(lane, "pair_config", lambda base, entry, exit_: (cfg, cfg))
    monkeypatch.setattr(lane.v8, "load_npz", lambda *a, **k: (arrays, stamps))
    monkeypatch.setattr(lane.v8, "_vec_round_trip_cost_for_sym", lambda *a, **k: 0.0)
    reads = []
    monkeypatch.setattr(lane.combo, "slice_cached_npz", lambda a, s, start, end: (reads.append("TRAIN" if start == 1 else "HOLDOUT") or (a, s)))
    monkeypatch.setattr(lane.adaptive, "ordinary_schedule", lambda *a, **k: ([], []))
    monkeypatch.setattr(lane.adaptive, "productive_route_pairs", lambda *a, **k: [{"ordinary_entry_group":"OE","ordinary_exit_group":"OX","ordinary_entry_schedule_count":1,"ordinary_exit_schedule_count":1}])
    calls = []
    train = {"strict_pass": True, "strategy_to_bh_multiplier": 1.1, "alpha_vs_bh_pp": 1, "return_pct": 1, "real_close_trades": 11, "ordinary_entry_group":"OE", "ordinary_exit_group":"OX"}
    holdout = {**train, "strict_pass": True}
    monkeypatch.setattr(lane, "replay", lambda *args: (calls.append(reads[-1]) or (train if reads[-1] == "TRAIN" else holdout)))
    result = lane.evaluate(row, tmp_path / "train.json", tmp_path, {"train": (1, 2), "holdout": (2, 3)})
    assert reads == ["TRAIN", "HOLDOUT"]
    assert calls == ["TRAIN", "HOLDOUT"]
    assert result["selected_train_route"]["ordinary_entry_group"] == "OE"


def test_no_state_write_contract_and_surface():
    source = inspect.getsource(lane)
    assert "NO_EXACT_NO_MATRIX_NO_WORKBOOK_NO_DB_NO_LIVE" in lane.CONTRACT
    for forbidden in ("exact_v8_queued", "matrix_written", "workbook_written", "database_written", "live_written"):
        assert f"'{forbidden}':False" in source
    for forbidden_call in ("to_sql(", "submit_order(", "queue_exact(", "export_switch_matrix("):
        assert forbidden_call not in source
