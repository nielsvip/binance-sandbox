"""Research-only tests for per-account partial intent to market-lot mapping."""

import types
from pathlib import Path

import pytest

from tools.research import partial_lot_candidate as candidate


QNT_MARKET_LOT = candidate.MarketLot(step_size=0.001, min_qty=0.1, min_notional=5.0)


@pytest.mark.parametrize(
    "family_reason",
    [
        "CYCLE_TP_TIERED_T0_0.15pct_gain1.02",
        "PPL_STEP1",
        "DC_BREACH_REDUCE",
        "WT_REDUCE",
    ],
)
def test_every_engine_partial_family_maps_fraction_to_actual_account_qty(family_reason):
    event = {"type": "REDUCE", "qty": 0.1, "reason": family_reason}
    result = candidate.adapt_engine_partial(
        event,
        engine_qty_before=0.4,
        actual_live_held_qty=0.4,
        price=100.0,
        lot=QNT_MARKET_LOT,
    )
    assert result["ok"] is True
    assert result["fraction"] == pytest.approx(0.25)
    assert result["qty"] == pytest.approx(0.1)
    assert result["residual_qty"] == pytest.approx(0.3)


def test_qnt_live_dimension_rejects_actual_point_zero_two_five_without_full_close():
    # Existing real engine row: engine qty 0.025 from a 0.1 engine-held position.
    # At the live account's actual 0.1 held qty, the same 25% intent is 0.025.
    event = {
        "type": "REDUCE",
        "qty": 0.025,
        "reason": "CYCLE_TP_TIERED_T0_0.15pct_gain1.02",
    }
    result = candidate.adapt_engine_partial(
        event,
        engine_qty_before=0.1,
        actual_live_held_qty=0.1,
        price=100.0,
        lot=QNT_MARKET_LOT,
    )
    assert result["fraction"] == pytest.approx(0.25)
    assert result["ok"] is False
    assert result["qty"] == 0.0
    assert result["reason_code"] == "PARTIAL_BELOW_MIN_QTY"


def test_larger_vector_reference_does_not_make_small_live_account_tradeable():
    event = {"type": "REDUCE", "qty": 0.1, "reason": "CYCLE_TP_TIERED"}
    # Engine reference says 0.4 -> 0.1 (exchange-valid), but actual account has 0.1 -> 0.025.
    result = candidate.adapt_engine_partial(
        event,
        engine_qty_before=0.4,
        actual_live_held_qty=0.1,
        price=100.0,
        lot=QNT_MARKET_LOT,
    )
    assert result["ok"] is False
    assert result["raw_qty"] == pytest.approx(0.025)
    assert result["qty"] == 0.0


def test_below_min_qty_rejects_but_existing_residual_dust_rule_still_closes():
    below_min = candidate.quantize_partial_fraction(
        0.25, 0.1, 100.0, QNT_MARKET_LOT
    )
    dust = candidate.quantize_partial_fraction(
        0.90, 0.3, 100.0, QNT_MARKET_LOT
    )
    assert below_min["ok"] is False and below_min["qty"] == 0.0
    assert dust["ok"] is True and dust["qty"] == pytest.approx(0.3)
    assert dust["reason"] == "PARTIAL_EXECUTABLE_FULL_DUST_CLOSE"


def test_reduce_quantizer_does_not_apply_opening_min_notional_gate():
    tiny_notional = candidate.MarketLot(step_size=0.001, min_qty=0.001, min_notional=5.0)
    result = candidate.quantize_partial_fraction(0.25, 0.02, 10.0, tiny_notional)
    assert result["ok"] is True
    assert result["qty"] == pytest.approx(0.005)


def test_full_close_and_non_partial_event_are_not_routed_through_partial_quantizer():
    assert candidate.adapt_engine_partial(
        {"type": "REDUCE", "qty": 0.4, "pnl_dollars": 1.0},
        0.4,
        0.4,
        100.0,
        QNT_MARKET_LOT,
    )["reason"] == "NOT_PARTIAL_REDUCE_EVENT"
    assert candidate.adapt_engine_partial(
        {"type": "CLOSE", "qty": 0.1}, 0.4, 0.1, 100.0, QNT_MARKET_LOT
    )["reason"] == "NOT_PARTIAL_REDUCE_EVENT"


def test_binance_normalized_market_filters_are_used():
    lot = candidate.market_lot_from_exchange_info(
        {"step": 0.001, "min_qty": 0.1, "min_notional": 5.0}
    )
    assert lot == QNT_MARKET_LOT


def test_market_mode_sizing_uses_live_venue_instance_and_evaluator_matches():
    from tools.opt import evaluate_v12

    for venue, engine_mode in (("crypto", "crypto"), ("tradier", "tradier")):
        profile = candidate.market_mode_sizing(venue)
        vals, label = evaluate_v12._market_mode_sizing(engine_mode)
        assert profile["market_mode"] in label
        assert profile["start_position_size"] == pytest.approx(vals["START_POSITION_SIZE"])
        assert profile["max_position_size"] == pytest.approx(vals["MAX_POSITION_SIZE"])


def test_research_trader_copy_routes_every_non_close_order_through_shared_partial_path():
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools/research/vector_direct_trader_candidate.py").read_text()
    start = source.index("    def size(self, o, held, price):")
    end = source.index("\n    def send(self, acct, o, qty):", start)
    size_method = source[start:end]
    assert 'if o["act"] == "CLOSE":\n            return held, ""' in size_method
    assert "quantize_partial_fraction(" in size_method
    assert "float(held)" in size_method
    assert "if held - q < mq:" not in size_method


def test_research_engine_copy_gates_each_common_partial_mutator_before_state_commit():
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools/research/v12_quick_engine_partial_candidate.py").read_text()
    assert source.count("_account_partial_ok(") == 14  # definition + 13 REDUCE paths
    assert "_ctp_fired.add(int(_ctp_idx))" in source
    assert source.index("_account_partial_ok(pos, _ctp_qty, px)") < source.index("_ctp_fired.add(int(_ctp_idx))")
    assert source.index("_account_partial_ok(pos, _ppl_qty, px)") < source.index("pos[\"realized\"] += _realized_ppl")
    assert "pos[\"ppl\"] = _ppl_prev" in source


def test_engine_runner_calls_real_evaluator_builder_and_real_simulate_one(monkeypatch):
    from tools.opt import evaluate_v12
    from tools.research import v12_quick_engine_partial_candidate as engine

    cfg = types.SimpleNamespace(START_POSITION_SIZE=999.0, MODE="crypto")
    npz = {"timestamps": [1]}
    calls = {}

    def fake_build(ss, overrides, window_days, sim_account):
        calls["builder"] = (ss, dict(overrides), window_days, sim_account)
        return cfg, npz, "QNTUSDT", True, "crypto", False

    def fake_simulate(actual_npz, sym, is_long, actual_cfg):
        calls["simulate"] = (actual_npz, sym, is_long, actual_cfg)
        return {"execution_ledger": [{"type": "REDUCE", "qty": 0.025}]}

    monkeypatch.setattr(evaluate_v12, "build_cfg_npz", fake_build)
    monkeypatch.setattr(engine, "simulate_one", fake_simulate)
    context = {"account": "ang", "qty_scale": 1.0, "market_lots": {"QNTUSDT": {"step": 0.001, "min_qty": 0.1, "min_notional": 5.0}}}
    out = candidate.run_real_engine("QNTUSDT_LONG", {"CYCLE_TP_TIERED_ENABLED": True}, 30, "trb", context)
    assert calls["builder"] == (
        "QNTUSDT_LONG",
        {"CYCLE_TP_TIERED_ENABLED": True},
        30,
        "trb",
    )
    assert calls["simulate"] == (npz, "QNTUSDT", True, cfg)
    assert cfg._PARTIAL_LOT_CONTEXT["account"] == context["account"]
    assert cfg._PARTIAL_LOT_CONTEXT["qty_scale"] == context["qty_scale"]
    assert cfg._PARTIAL_LOT_CONTEXT["market_lots"] == context["market_lots"]
    assert cfg._PARTIAL_LOT_CONTEXT["venue_market_mode_sizing"]["market_mode"] == "LIGHT_MODE"
    assert cfg._PARTIAL_LOT_CONTEXT["engine_start_position_size"] == cfg.START_POSITION_SIZE
    assert out["result"]["execution_ledger"][0]["qty"] == 0.025
    assert out["mode"] == "crypto"
    assert out["market_mode_sizing"]["start_position_size"] == pytest.approx(cfg.START_POSITION_SIZE)
    assert out["engine_start_position_size"] == pytest.approx(cfg.START_POSITION_SIZE)
    assert cfg.START_POSITION_SIZE != 999.0  # MARKET_MODE is reapplied after builder overrides


def test_real_engine_cycle_tp_output_is_quantized_against_live_inventory():
    import numpy as np
    import v12_quick_engine as engine

    n = 100
    close = np.linspace(100.0, 100.5, n)
    timestamps = np.arange(1700000000, 1700000000 + n * 180, 180)
    npz = {
        "close": close,
        "close_3m": close,
        "close_15m": close,
        "close_5m": close,
        "timestamps": timestamps,
        "timestamp_3m": timestamps,
        "timestamp_15m": timestamps,
        "low_3m": close,
        "high_3m": close,
        "open_3m": close,
        "dc_low_4h": np.full(n, 80.0),
        "dc_high_4h": np.full(n, 120.0),
        "dc_low_15m": np.full(n, 90.0),
        "dc_high_15m": np.full(n, 130.0),
    }
    for timeframe in ("15m", "1h", "4h", "D"):
        npz[f"wt1_{timeframe}"] = np.zeros(n)
        npz[f"wt2_{timeframe}"] = np.zeros(n)
    cfg = engine.QuickConfig()
    cfg.MODE = "crypto"
    cfg.BASE_TF = "3m"
    cfg.START_POSITION_SIZE = 10.0
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
    result = engine.simulate_one(npz, "QNTUSDT", True, cfg, force_initial_seed=True)
    event = next(
        row
        for row in result["ledger"]
        if row.get("type") == "REDUCE"
        and str(row.get("reason") or "").startswith("CYCLE_TP_TIERED_T0_")
        and "pnl_dollars" not in row
    )
    assert event["qty"] == pytest.approx(0.025)
    small_live = candidate.adapt_engine_partial(
        event, 0.1, 0.1, float(event["price"]), QNT_MARKET_LOT
    )
    larger_live = candidate.adapt_engine_partial(
        event, 0.1, 0.4, float(event["price"]), QNT_MARKET_LOT
    )
    assert small_live["ok"] is False and small_live["qty"] == 0.0
    assert larger_live["ok"] is True and larger_live["qty"] == pytest.approx(0.1)


def _seeded_npz_for_account_gate():
    import numpy as np

    n = 120
    close = np.empty(n)
    close[:8] = 100.0 * (1.005 ** np.arange(8))
    close[8:] = close[7] * (0.999 ** np.arange(1, n - 7))
    timestamps = np.arange(n, dtype=float) * 900 + 1_700_000_000.0
    wt = np.array([5.0] * 7 + [20.0] * (n - 7))
    return {
        "close": close,
        "close_15m": close,
        "timestamps": timestamps,
        "timestamp_15m": timestamps,
        "open_15m": close,
        "high_15m": close,
        "low_15m": close * 0.999,
        "volume_15m": np.ones(n),
        "wt1_15m": wt,
        "wt2_15m": np.zeros(n),
        "dc_low_15m": np.full(n, 10.0),
        "dc_high_15m": np.full(n, 200.0),
        "stoch_k_15m": np.full(n, 50.0),
        "stoch_k_1h": np.full(n, 50.0),
        "stoch_k": np.full(n, 50.0),
        "wt1_1h": np.full(n, 5.0),
        "wt2_1h": np.zeros(n),
        "wt1_4h": np.full(n, 5.0),
        "wt2_4h": np.zeros(n),
        "wt1_D": np.full(n, 5.0),
        "wt2_D": np.zeros(n),
        "wt_velocity_1h": np.zeros(n),
        "close_3m": close,
        "close_5m": close,
        "low_3m": close * 0.999,
        "high_3m": close,
        "open_3m": close,
        "timestamp_3m": timestamps,
        "dc_low_4h": np.full(n, 10.0),
        "dc_high_4h": np.full(n, 200.0),
    }


def _account_context(symbol):
    return {
        "account": "ang",
        "qty_scale": 1.0,
        "market_lots": {
            symbol: {"step": 0.001, "min_qty": 0.1, "min_notional": 5.0}
        },
    }


def test_research_engine_refuses_unexecutable_cycle_partial_before_tier_or_pnl_mutation():
    from tools.research import v12_quick_engine_partial_candidate as engine

    cfg = engine.QuickConfig()
    cfg.MODE = "crypto"
    cfg.BASE_TF = "3m"
    cfg.START_POSITION_SIZE = 10.0
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
    cfg._PARTIAL_LOT_CONTEXT = _account_context("QNTUSDT")
    result = engine.simulate_one(
        _seeded_npz_for_account_gate(), "QNTUSDT", True, cfg, force_initial_seed=True
    )
    reductions = [
        row
        for row in result["ledger"]
        if str(row.get("reason") or "").startswith("CYCLE_TP_TIERED_")
    ]
    final = next(row for row in result["ledger"] if row.get("reason") == "FINAL_MTM")
    assert reductions == []
    assert cfg._PARTIAL_LOT_CONTEXT["rejected_partial_count"] > 1
    assert final["qty"] == pytest.approx(0.1)
    assert cfg._PARTIAL_LOT_CONTEXT["last_rejected_partial"]["reason"] == "PARTIAL_BELOW_MIN_QTY"


def test_research_engine_without_account_context_matches_canonical_cycle_ledger():
    import v12_quick_engine as canonical
    from tools.research import v12_quick_engine_partial_candidate as candidate_engine

    def make_cfg(engine):
        cfg = engine.QuickConfig()
        cfg.MODE = "crypto"
        cfg.BASE_TF = "3m"
        cfg.START_POSITION_SIZE = 10.0
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
        return cfg

    fixture = _seeded_npz_for_account_gate()
    base = canonical.simulate_one(fixture, "QNTUSDT", True, make_cfg(canonical), force_initial_seed=True)
    research = candidate_engine.simulate_one(fixture, "QNTUSDT", True, make_cfg(candidate_engine), force_initial_seed=True)
    assert base["ledger"] == research["ledger"]


def test_research_engine_refuses_unexecutable_ppl_partial_without_state_or_pnl_mutation():
    from tools.research import v12_quick_engine_partial_candidate as engine

    cfg = engine.QuickConfig()
    cfg.MODE = "crypto"
    cfg.START_POSITION_SIZE = 10.0
    cfg.PARTIAL_PROFIT_LOCK_ENABLED = True
    cfg.PARTIAL_PROFIT_LOCK_GAIN_PCT = 1.0
    cfg.PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT = 2.0
    cfg.PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT = 0.02
    cfg.PARTIAL_PROFIT_LOCK_FRAC = 0.5
    cfg.PPL_REAUGMENT_ENABLED = False
    cfg._PARTIAL_LOT_CONTEXT = _account_context("PROBE")
    result = engine.simulate_one(
        _seeded_npz_for_account_gate(), "PROBE", True, cfg, force_initial_seed=True
    )
    taps = [
        row
        for row in result["ledger"]
        if row.get("type") == "REDUCE" and "PPL_TP" in str(row.get("reason"))
    ]
    final = next(row for row in result["ledger"] if row.get("reason") == "FINAL_MTM")
    assert taps == []
    assert cfg._PARTIAL_LOT_CONTEXT["rejected_partial_count"] > 1
    assert final["qty"] == pytest.approx(0.1)
    assert cfg._PARTIAL_LOT_CONTEXT["last_rejected_partial"]["reason"] == "PARTIAL_BELOW_MIN_QTY"


def test_rejected_partial_paths_do_not_bar_continue_or_leave_ppl_reaugment_unscaled():
    root = Path(__file__).resolve().parents[1]
    source = (root / "tools/research/v12_quick_engine_partial_candidate.py").read_text()
    cs_start = source.index("_cs_partial_rejected = not _account_partial_ok")
    cs_end = source.index("_cs_snap = _cash_settle.snapshot", cs_start)
    assert "if not _cs_partial_rejected:\n                        continue" in source[cs_start:cs_end]
    assert "_cs_book.owed = _cs_ow" in source[cs_start:cs_end]
    assert '"owed_executed"' in source[cs_start:cs_end]
    lec_start = source.index("if _lec_hit and not _lec_hit[1]:\n                _lec_preview_frac")
    lec_end = source.index("if _lec_hit:\n                _lec_reason", lec_start)
    assert "_lec_hit = None" in source[lec_start:lec_end]
    assert "continue" not in source[lec_start:lec_end]
    sfk_start = source.index("_sfk_rejected = False")
    sfk_end = source.index("# 2026-09-30 STATEFUL PORTED EXITS", sfk_start)
    assert "if not _sfk_rejected:\n                        continue" in source[sfk_start:sfk_end]
    dd_start = source.index("_dd_exec_ok = _account_partial_ok")
    dd_end = source.index("_ty_fire, _ty_reason = False", dd_start)
    assert "if _dd_exec_ok:" in source[dd_start:dd_end]
    assert "if not _dd_exec_ok" not in source[dd_start:dd_end]
    sat_start = source.index("if _account_partial_ok(pos, reduce_qty, px):", source.index("reduce_qty = pos[\"qty\"] * satoshit_partial"))
    sat_end = source.index("pnl_preview = pos[\"realized\"] + (", sat_start)
    sat_flow = source[sat_start:sat_end]
    assert "if _account_partial_ok(pos, reduce_qty, px):" in sat_flow
    assert "if not _account_partial_ok" not in sat_flow
    assert all(line.startswith("                    continue") for line in sat_flow.splitlines() if line.lstrip() == "continue")
    bb_start = source.index("if _account_partial_ok(pos, _bbr_reduce_qty, px):")
    bb_end = source.index("_exit_fee = abs(pos[\"qty\"] * px) * half_fee", bb_start)
    assert "if not _account_partial_ok" not in source[bb_start:bb_end]
    assert "_ctp_fired.add(int(_ctp_idx))" in source
    assert source.index("events.append(_ctp_event)") < source.index("_ctp_fired.add(int(_ctp_idx))")
    assert '_actual_reaug = (_partial_ctx.get("augment_qty_by_bar") or {}).get(int(i))' in source
    assert 'pos["_account_exec_qty"] = None' in source[source.index("_actual_reaug"):source.index("_actual_reaug") + 400]
