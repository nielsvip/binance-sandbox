from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_live_direct_claim_reaches_normal_open_before_full_recipe_hold():
    source = (ROOT / "tradier_manage.py").read_text()
    evaluate = source.index("_shared_direct_claim = _shared_direct_entry_claim(")
    action = source.index(
        "if action_type != \"OPEN\" and _shared_direct_claim is not None:"
    )
    hold = source.index('return "FULL_RECIPE_ONLY_HOLD_ENTRY"', action)
    execute = source.index(
        "await queue_trade_action(order_queue, trade_manager, position_key, "
        "action_type, reason, conf, override_qty=qty)",
        hold,
    )
    assert evaluate < action < hold
    assert hold < execute
    call = source[evaluate:action]
    assert "not bool(has_position) and is_regular_trading_hours()" in call
    assert "_shared_direct_entry_pending_claim" in source


def test_v8_injects_before_mark_rewrite_admits_every_bar_and_calls_real_process():
    source = (ROOT / "backtest_v8_engine.py").read_text()
    assert '"V8_SHARED_DIRECT_ROUTE_TELEMETRY",' in source
    inject = source.index("_completed_direct_omissions_t = inject_completed_snapshot_v8(")
    mark = source.index("ind['mark_price'] = p", inject)
    per_bar = source.index("_shared_direct_flat_route_enabled_t(", mark)
    formation_per_bar = source.index(
        "_classic_formation_flat_route_enabled_t(", per_bar
    )
    process = source.index("tm_mod.process_position(", per_bar)
    assert inject < mark < per_bar < formation_per_bar < process
    assert "ind.setdefault(\n                        f'_completed_source_ts_{_tf}'" in source


def test_v8_distinguishes_queue_acceptance_from_consumer_fill():
    source = (ROOT / "backtest_v8_engine.py").read_text()
    assert '"V8_SHARED_DIRECT_CONSUMER_TELEMETRY",' in source
    queue_create = source.index("manager.order_queue = tm_mod.OrderQueue(manager)")
    observer = source.index("async def _v8_queue_eta_observed_t", queue_create)
    install = source.index(
        "manager.execute_trade_action = _v8_queue_eta_observed_t", observer
    )
    emit = source.index("V8_SHARED_DIRECT_CONSUMER_TELEMETRY:", install)
    assert queue_create < observer < install < emit
    observed = source[observer:install]
    assert "len(executed_trades)" in observed
    assert '"DIRECT_" in _reason_upper_t' in observed
    assert '"CLASSIC_FORMATION_" in _reason_upper_t' in observed


def test_selected_direct_route_is_not_blocked_by_disabled_wt_fallback_sentinel():
    source = (ROOT / "backtest_v8_engine.py").read_text()
    marker = source.index("_selected_completed_direct_r = (")
    wt_gate = source.index("if _wt_dc_thr > 0", marker)
    score_gate = source.index(
        'if account_key.startswith(("trb", "trc"))', wt_gate
    )
    assert (
        '"CLASSIC_FORMATION_ENTRY_" in _entry_reason_upper_r'
        in source[marker:wt_gate]
    )
    assert "not _selected_completed_direct_r" in source[wt_gate:score_gate]
    assert "not _selected_completed_direct_r" in source[score_gate:score_gate + 300]


def test_bottom_b_real_evaluate_stop_site_precedes_full_recipe_hold():
    source = (ROOT / "tradier_manage.py").read_text()
    method = source.index("async def evaluate_stop(")
    bottom_b = source.index("_bottom_b_eval_signal = _ordinary_bottom_b_exit(", method)
    hold = source.index('return False, "FULL_RECIPE_ONLY_HOLD_EXIT", 0', bottom_b)
    assert bottom_b < hold
    assert "MANDATORY_REENTRY" in source[bottom_b:hold]
