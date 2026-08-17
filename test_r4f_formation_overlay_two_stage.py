import hashlib
import json
from pathlib import Path

from tools import r4f_formation_overlay_two_stage as r4f
from tools.r4f_formation_overlay_two_stage import select_train_only, train_only_r4f_candidates


def _fold():
    return {"real_close_trades": 11, "strategy_return_pct": 1.0, "alpha_vs_bh_pp": 1.0,
            "alpha_vs_ordinary_control_pp": 1.0, "tim_pct": 75.0,
            "formation_entry_action_count": 1, "formation_exit_action_count": 1,
            "real_entries": 1, "deployed_capital_gate": True,
            "capacity_gate": True, "reclaim_gate": True, "countertrend_gate": True,
            "causal_data_gate": True, "causal_action_evidence": True, "emergency_gate": True,
            "invalid_event_count": 0, "unsettled_event_ledger": False}


def test_r4f_inventory_is_single_arm_or_overlay_not_entry_exit_and():
    rows = train_only_r4f_candidates(
        [{"key": "ABC_LONG", "variant": "wedge_entry_1h"}],
        {"ABC_LONG": [{"family": "wedge", "timeframe": "1h"}]},
    )
    assert {row["formation_arm"] for row in rows} == {"wedge_entry_1h", "wedge_exit_1h"}
    assert all(row["topology"] == "ORDINARY_CONTROLS_PLUS_ONE_FORMATION_OR_OVERLAY" for row in rows)


def test_r4f_train_screen_has_no_final_argument_or_access():
    class NoFinal(dict):
        def __getitem__(self, key):
            if key in {"final", "holdout", "untouched_final"}:
                raise AssertionError("final accessed during R4F TRAIN selection")
            return super().__getitem__(key)
    candidates = train_only_r4f_candidates(
        [{"key": "ABC_LONG", "variant": "wedge_entry_1h"}], {}
    )
    calls = []
    def run_train(candidate):
        calls.append(candidate["formation_arm"])
        return [NoFinal(_fold()), NoFinal(_fold())]
    _, winner = select_train_only(candidates, run_train)
    assert calls == ["wedge_entry_1h"]
    assert winner["train_all_folds_strict"] is True


def test_r4f_negative_strategy_cannot_beat_negative_bh_into_selection():
    candidates = train_only_r4f_candidates(
        [{"key": "ABC_LONG", "variant": "wedge_entry_1h"}], {}
    )
    negative = {**_fold(), "strategy_return_pct": -1.0, "alpha_vs_bh_pp": 2.0}
    _, winner = select_train_only(candidates, lambda _candidate: [negative])
    assert winner["train_all_folds_strict"] is False


def test_r4f_reads_one_final_overlay_only_after_train_freeze(tmp_path, monkeypatch):
    npz = tmp_path / "ABC.npz"; npz.write_bytes(b"sealed")
    candidate = train_only_r4f_candidates(
        [{"key": "ABC_LONG", "variant": "wedge_entry_1h"}], {}
    )[0] | {"symbol": "ABC", "side": "LONG", "npz": str(npz), "npz_sha256": hashlib.sha256(b"sealed").hexdigest()}
    receipt = tmp_path / "train.json"; receipt.write_text("{}")
    qualified = tmp_path / "qualified.json"; qualified.write_text("{}")
    inventory = tmp_path / "inventory.json"; inventory.write_text("{}")
    plan = {"train_receipt": str(receipt), "qualified_inventory": str(qualified), "productive_v2_inventory": str(inventory), "source_hashes": {
        "qualified_single_arm_inventory_sha256": r4f.sha256_file(qualified),
        "productive_v2_inventory_sha256": r4f.sha256_file(inventory),
        "train_receipt_sha256": r4f.sha256_file(receipt),
        "v8_vec_sweep_sha256": r4f.sha256_file(r4f.ROOT / "v8_vec_sweep.py"),
        "classic_formations_sha256": r4f.sha256_file(r4f.ROOT / "classic_formations.py"),
        "runner_sha256": r4f.sha256_file(r4f.ROOT / "tools/r4f_formation_overlay_two_stage.py"),
    }, "folds": {"train": [["2025-01-01", "2025-06-01"], ["2025-06-01", "2025-12-01"]], "untouched_final": ["2025-12-01", None]}, "plans": [{"key": "ABC_LONG", "status": "READY_FOR_R4F_TWO_STAGE_EXECUTOR", "ordinary_controls": "control", "active_overrides": {}, "candidates": [candidate]}]}
    monkeypatch.setattr(r4f.combo, "base_config_from_train", lambda _path: (r4f.v8.SweepConfig(), {}))
    monkeypatch.setattr(r4f.v8, "load_npz", lambda *_args, **_kwargs: ({"close": r4f.np.array([100.0, 101.0])}, r4f.np.array([1, 2])))
    monkeypatch.setattr(r4f.combo, "slice_cached_npz", lambda arrays, ts, start, _end: (arrays, r4f.np.array([start, start + 1])))
    monkeypatch.setattr(r4f.combo, "tim_pct", lambda *_args: 75.0)
    monkeypatch.setattr(r4f.v8, "_vec_round_trip_cost_for_sym", lambda *_args: 0.0)
    calls = []
    def simulate(_symbol, _side, _mode, config, _npz_cache):
        final = int(_npz_cache[1][0]) == r4f.timestamp("2025-12-01")
        overlay = bool(getattr(config, "FORMATION_WEDGE_ENTRY_ENABLED"))
        calls.append((final, overlay))
        reason = "CLASSIC_FORMATION_ENTRY_WEDGE" if overlay else "CONTROL"
        events = [type("E", (), {"ts": i + 1, "type": "CLOSE", "reason": reason})() for i in range(11)]
        return events, ([2.0] * 11 if overlay else [1.0] * 11), 2
    monkeypatch.setattr(r4f.v8, "simulate_one_symbol", simulate)
    def evidence(events, _returns, *_args):
        overlay = "CLASSIC_FORMATION_ENTRY_" in str(getattr(events[0], "reason", ""))
        return {**_fold(), "strategy_return_pct": 2.0 if overlay else 1.0,
                "formation_entry_action_count": 1 if overlay else 0,
                "causal_action_evidence": overlay}
    monkeypatch.setattr(r4f, "_evidence", evidence)
    result = r4f.execute_key(plan, "ABC_LONG", r4f.ROOT, tmp_path / "out")
    assert result["final"]["status"] == "STRICT_SURVIVOR"
    assert sum(final and overlay for final, overlay in calls) == 1


def test_r4f_no_all_train_strict_survivor_reads_no_final(tmp_path, monkeypatch):
    npz = tmp_path / "ABC.npz"; npz.write_bytes(b"sealed")
    candidate = train_only_r4f_candidates([{"key": "ABC_LONG", "variant": "wedge_entry_1h"}], {})[0] | {"symbol": "ABC", "side": "LONG", "npz": str(npz), "npz_sha256": hashlib.sha256(b"sealed").hexdigest()}
    receipt = tmp_path / "train.json"; receipt.write_text("{}"); qualified = tmp_path / "q.json"; qualified.write_text("{}"); inventory = tmp_path / "i.json"; inventory.write_text("{}")
    hashes={"qualified_single_arm_inventory_sha256":r4f.sha256_file(qualified),"productive_v2_inventory_sha256":r4f.sha256_file(inventory),"train_receipt_sha256":r4f.sha256_file(receipt),"v8_vec_sweep_sha256":r4f.sha256_file(r4f.ROOT/"v8_vec_sweep.py"),"classic_formations_sha256":r4f.sha256_file(r4f.ROOT/"classic_formations.py"),"runner_sha256":r4f.sha256_file(r4f.ROOT/"tools/r4f_formation_overlay_two_stage.py")}
    plan={"train_receipt":str(receipt),"qualified_inventory":str(qualified),"productive_v2_inventory":str(inventory),"source_hashes":hashes,"folds":{"train":[["2025-01-01","2025-06-01"],["2025-06-01","2025-12-01"]],"untouched_final":["2025-12-01",None]},"plans":[{"key":"ABC_LONG","status":"READY_FOR_R4F_TWO_STAGE_EXECUTOR","ordinary_controls":"control","active_overrides":{},"candidates":[candidate]}]}
    monkeypatch.setattr(r4f.combo,"base_config_from_train",lambda _:(r4f.v8.SweepConfig(),{})); monkeypatch.setattr(r4f.v8,"load_npz",lambda *_args,**_kwargs:({"close":r4f.np.array([100.,101.])},r4f.np.array([1,2]))); monkeypatch.setattr(r4f.combo,"slice_cached_npz",lambda arrays,ts,start,_end:(arrays,r4f.np.array([start,start+1]))); monkeypatch.setattr(r4f.v8,"_vec_round_trip_cost_for_sym",lambda *_:0.0)
    calls=[]
    monkeypatch.setattr(r4f.v8,"simulate_one_symbol",lambda *_args,**_kwargs:(calls.append(1) or ([],[],2)))
    monkeypatch.setattr(r4f,"_evidence",lambda *_args:{**_fold(),"strategy_return_pct":-1.0,"alpha_vs_bh_pp":1.0})
    result=r4f.execute_key(plan,"ABC_LONG",r4f.ROOT,tmp_path/"out")
    assert result["untouched_final_read"] is False
    assert len(calls) == 4  # two controls + two candidate TRAIN folds, no final calls


def test_r4f_evidence_uses_capital_event_ledger_not_unweighted_trade_returns(monkeypatch):
    ts = r4f.np.arange(1, 50, dtype=r4f.np.int64)
    arrays = {"close": r4f.np.linspace(100.0, 101.0, len(ts)), "high": r4f.np.full(len(ts), 101.0), "low": r4f.np.full(len(ts), 99.0)}
    events = []
    for index in range(11):
        events.extend([
            {"ts": int(2 + index * 4), "type": "OPEN", "qty": 20.0, "price": 100.0, "value": 2000.0, "reason": "CLASSIC_FORMATION_ENTRY_WEDGE"},
            {"ts": int(3 + index * 4), "type": "CLOSE", "qty": 20.0, "price": 101.0, "value": 2020.0, "reason": "CLASSIC_FORMATION_EXIT_WEDGE"},
        ])
    monkeypatch.setattr(r4f.v8, "_tradier_counter_trend_entry_allowed_vec", lambda *_args, **_kwargs: r4f.np.ones(len(ts), dtype=bool))
    evidence = r4f._evidence(events, [-99.0] * 11, arrays, ts, "ABC", "LONG", 0.0, "ENTRY", r4f.v8.SweepConfig())
    assert evidence["legacy_unweighted_trade_return_count"] == 11
    assert evidence["strategy_return_pct"] > 0.0
    assert evidence["capital"]["capital_accounting_version"]
