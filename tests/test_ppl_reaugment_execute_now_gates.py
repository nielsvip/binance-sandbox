import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import v12_quick_engine as V
from vec_decisions.ppl_reaugment_gate import (
    allows_losing_position_increase,
    effective_gain,
    passes_augment_guard,
    passes_raw_three_percent_gate,
    passes_universal_gain_since_add,
    projected_entry_nonnegative,
)


ROOT = Path(__file__).resolve().parents[1]


def _cfg():
    cfg = V.QuickConfig()
    cfg.PARTIAL_PROFIT_LOCK_ENABLED = True
    cfg.PARTIAL_PROFIT_LOCK_GAIN_PCT = 1.0
    cfg.PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT = 2.0
    cfg.PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT = 0.02
    cfg.PARTIAL_PROFIT_LOCK_FRAC = 0.5
    cfg.PPL_REAUGMENT_ENABLED = True
    cfg.PPL_REAUGMENT_SIZE_MULT = 1.5
    cfg.PPL_REAUGMENT_TRIGGER = "WT_CROSS"
    cfg.AUGMENTED_DC_BREAK_ENABLED = False
    return cfg


def _reaugment_fixture(gain_at_trigger):
    n = 120
    close = np.empty(n, dtype=np.float64)
    close[:8] = 100.0 * (1.0035 ** np.arange(8))
    close[8] = close[7] * 1.001
    close[9] = 100.0 * (1.0 + gain_at_trigger / 100.0)
    close[10:] = close[9] * (0.999 ** np.arange(1, n - 9))
    wt1 = np.array([-5.0] * 9 + [2.0] + [3.0] * (n - 10), dtype=np.float64)
    ts = np.arange(n, dtype=np.float64) * 900.0 + 1_700_000_000.0
    return {
        "close": close,
        "close_15m": close,
        "timestamps": ts,
        "timestamp_15m": ts,
        "open_15m": close.copy(),
        "high_15m": close.copy(),
        "low_15m": close.copy(),
        "volume_15m": np.ones(n, dtype=np.float32),
        "wt1_15m": wt1,
        "wt2_15m": np.zeros(n, dtype=np.float64),
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
    }


def _run(gain_at_trigger, config_overrides=None):
    cfg = _cfg()
    for key, value in (config_overrides or {}).items():
        setattr(cfg, key, value)
    return V.simulate_one(
        _reaugment_fixture(gain_at_trigger),
        "PROBE",
        True,
        cfg,
        force_initial_seed=True,
    )


def _native_real_gain_gate():
    source = (ROOT / "ez_manage.py").read_text()
    tree = ast.parse(source)
    inner = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_execute_now_inner"
    )
    gate = next(
        node
        for node in ast.walk(inner)
        if isinstance(node, ast.If)
        and ast.unparse(node.test)
        == "is_augment and real_notional > 0 and (real_gain < 3.0)"
    )
    fn = ast.FunctionDef(
        name="native_gate",
        args=ast.arguments(
            posonlyargs=[],
            args=[
                ast.arg(arg=name)
                for name in (
                    "is_augment",
                    "real_notional",
                    "real_gain",
                    "_is_reentry_exec",
                    "_vd_exempt",
                    "_vo_armed",
                    "action",
                    "position_key",
                    "is_hedge",
                )
            ],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        ),
        body=[gate, ast.Return(value=ast.Constant(value="ALLOWED"))],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[]))

    class Logger:
        @staticmethod
        def warning(_message):
            return None

    scope = {"logger": Logger}
    exec(compile(module, "ez_manage.py#native_3pct_gate", "exec"), scope)
    return scope["native_gate"]


def test_ppl_fired_effective_gain_matches_native_double_gain_switch():
    cfg = SimpleNamespace(
        EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED=True,
        PARTIAL_PROFIT_LOCK_FRAC=0.5,
    )
    assert effective_gain(cfg, 2.8, True) == 5.6
    cfg.EZ_REENTRY_PPL_DOUBLE_GAIN_ENABLED = False
    assert effective_gain(cfg, 2.8, True) == 2.8


def test_native_losing_and_augment_depth_gates_with_pullback_exemption():
    cfg = SimpleNamespace(
        MIN_GAIN=5.0,
        PULLBACK_AUGMENT_ENABLED=True,
        PULLBACK_AUGMENT_REVERSAL_MIN=1.0,
    )
    assert allows_losing_position_increase(cfg, 3.1, 4.0, 1.0, 1.0) == (
        False,
        "LOSING_POSITION_HARD_BLOCK",
    )
    assert allows_losing_position_increase(cfg, 3.1, 6.2, 1.0, 1.0) == (True, "OK")
    assert allows_losing_position_increase(cfg, 6.0, 6.0, 2.0, 1.0) == (
        False,
        "AUGMENT_LEVEL_GATE",
    )
    assert allows_losing_position_increase(cfg, 10.0, 12.0, 2.0, 1.0) == (True, "OK")
    # Vector exemption skips only the first losing-position block; native augment-depth still binds.
    assert allows_losing_position_increase(
        cfg, 3.1, 4.0, 1.0, 1.0, vector_exempt=True
    ) == (False, "AUGMENT_LEVEL_GATE")


def test_projected_entry_and_augment_guard_match_live_thresholds():
    assert not projected_entry_nonnegative(True, 100.0, 1.0, 1.0, 99.0)
    assert projected_entry_nonnegative(False, 100.0, 1.0, 1.0, 99.0)
    assert projected_entry_nonnegative(True, 100.0, 1.0, 1.0, float("nan"))
    cfg = SimpleNamespace(MIN_GAIN=3.0)
    assert not passes_augment_guard(cfg, 2.9, 51.0, 50.0)
    assert passes_augment_guard(cfg, 3.0, 51.0, 50.0)
    assert passes_augment_guard(cfg, 2.9, 50.0, 50.0)


def test_ppl_reason_uses_native_typed_uagain_tier():
    cfg = SimpleNamespace(
        UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED=True,
        AUGMENT_MIN_GAIN_PCT=3.0,
        MIN_GAIN_TO_BUY_AGGRESSIVELY=3.0,
        AUGMENT_BOUNCE_MIN_GAIN_PCT=4.0,
        AUGMENT_BREAKOUT_MIN_GAIN_PCT=2.0,
        PULLBACK_AUGMENT_ENABLED=True,
        PULLBACK_AUGMENT_REVERSAL_MIN=1.0,
    )
    assert not passes_universal_gain_since_add(
        cfg, True, 103.2, 100.0, 3.2, 3.2, "PPL_REAUGMENT_WT_CROSS"
    )
    assert passes_universal_gain_since_add(
        cfg, True, 104.2, 100.0, 4.2, 4.2, "PPL_REAUGMENT_WT_CROSS"
    )
    assert passes_universal_gain_since_add(
        cfg, True, 102.8, 100.0, 2.8, 2.8, "PPL_REAUGMENT_WT_CROSS"
    ) is False
    cfg.UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED = False
    assert passes_universal_gain_since_add(
        cfg, True, 102.8, 100.0, 2.8, 2.8, "PPL_REAUGMENT_WT_CROSS"
    )


def test_ppl_reaugment_below_native_three_percent_gate_does_not_book():
    result = _run(2.8)
    ledger = result["ledger"]
    taps = [event for event in ledger if event.get("reason", "").startswith("PPL_TP_")]
    assert taps
    assert taps[0]["bar"] < 9
    assert not any(event.get("reason") == "PPL_REAUGMENT_WT_CROSS" for event in ledger)


def test_ppl_typed_uagain_gate_blocks_trigger_bar_and_three_point_two_books():
    blocked = _run(3.2, {"AUGMENT_BOUNCE_MIN_GAIN_PCT": 4.0})
    assert not any(event.get("reason") == "PPL_REAUGMENT_WT_CROSS" for event in blocked["ledger"])
    passed = _run(3.2)
    reaugments = [event for event in passed["ledger"] if event.get("reason") == "PPL_REAUGMENT_WT_CROSS"]
    assert len(reaugments) == 1
    assert reaugments[0]["bar"] == 9


def test_ppl_reaugment_three_percent_mask_matches_extracted_native_branch():
    native = _native_real_gain_gate()
    for raw_gain in (2.8, 3.0, 3.2):
        native_block = native(True, 20.0, raw_gain, False, False, False, "QUICK_AUGMENT", "PROBE_LONG", False)
        helper_pass = passes_raw_three_percent_gate(raw_gain, 20.0)
        assert (native_block == "ALLOWED") is helper_pass
    assert native(True, 20.0, 2.8, False, True, False, "QUICK_AUGMENT", "PROBE_LONG", False) == "ALLOWED"
    assert native(True, 0.0, 2.8, False, False, False, "QUICK_AUGMENT", "PROBE_LONG", False) == "ALLOWED"


def test_short_ppl_reaugment_passes_native_gates_on_trigger_bar():
    n = 120
    close = np.empty(n, dtype=np.float64)
    close[:8] = 100.0 * (0.9965 ** np.arange(8))
    close[8] = close[7] * 0.999
    close[9] = 95.4
    close[10:] = close[9] * (1.001 ** np.arange(1, n - 9))
    wt1 = np.array([5.0] * 9 + [-2.0] + [-3.0] * (n - 10), dtype=np.float64)
    ts = np.arange(n, dtype=np.float64) * 900.0 + 1_700_000_000.0
    npz = {
        "close": close,
        "close_15m": close,
        "timestamps": ts,
        "timestamp_15m": ts,
        "open_15m": close.copy(),
        "high_15m": close.copy(),
        "low_15m": close.copy(),
        "volume_15m": np.ones(n, dtype=np.float32),
        "wt1_15m": wt1,
        "wt2_15m": np.zeros(n, dtype=np.float64),
        "dc_low_15m": np.full(n, 10.0),
        "dc_high_15m": np.full(n, 200.0),
        "stoch_k_15m": np.full(n, 50.0),
        "stoch_k_1h": np.full(n, 50.0),
        "stoch_k": np.full(n, 50.0),
        "wt1_1h": np.full(n, -5.0),
        "wt2_1h": np.zeros(n),
        "wt1_4h": np.full(n, -5.0),
        "wt2_4h": np.zeros(n),
        "wt1_D": np.full(n, -5.0),
        "wt2_D": np.zeros(n),
        "wt_velocity_1h": np.zeros(n),
    }
    cfg = _cfg()
    result = V.simulate_one(npz, "PROBE", False, cfg, force_initial_seed=True)
    reaugments = [event for event in result["ledger"] if event.get("reason") == "PPL_REAUGMENT_WT_CROSS"]
    assert len(reaugments) == 1
    assert reaugments[0]["bar"] == 9


def test_tradier_ppl_ledger_does_not_apply_crypto_three_percent_gate():
    import datetime as dt

    npz = _reaugment_fixture(2.8)
    npz["timestamps"] = np.arange(len(npz["close"]), dtype=float) * 300.0 + int(
        dt.datetime(2025, 10, 31, 13, 30, tzinfo=dt.timezone.utc).timestamp()
    )
    npz["timestamp_15m"] = npz["timestamps"]
    npz["open_15m"] = npz["close"].copy()
    npz["high_15m"] = npz["close"].copy()
    npz["low_15m"] = npz["close"].copy()
    npz["close"][10:] = npz["close"][9]
    cfg = V.QuickConfig()
    cfg.apply_tradier_defaults()
    cfg.apply_cat_side_defaults("STOCKS_LONG")
    cfg.PARTIAL_PROFIT_LOCK_ENABLED = True
    cfg.PARTIAL_PROFIT_LOCK_GAIN_PCT = 1.0
    cfg.PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT = 2.0
    cfg.PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT = 0.02
    cfg.PARTIAL_PROFIT_LOCK_FRAC = 0.5
    cfg.PPL_REAUGMENT_ENABLED = True
    cfg.PPL_REAUGMENT_SIZE_MULT = 1.5
    cfg.PPL_REAUGMENT_TRIGGER = "WT_CROSS"
    cfg.AUGMENTED_DC_BREAK_ENABLED = False
    result = V.simulate_one(npz, "PROBE", True, cfg, force_initial_seed=True)
    reaugments = [
        event for event in result["ledger"]
        if event.get("reason") == "PPL_REAUGMENT_WT_CROSS"
    ]
    assert len(reaugments) == 1
    assert reaugments[0]["bar"] == 9
