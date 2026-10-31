import ast
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import v12_quick_engine as V
from vec_decisions.ppl_reaugment_gate import allows_reaugment, effective_gain


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


def _run(gain_at_trigger):
    cfg = _cfg()
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


def test_native_losing_block_and_pullback_exception_are_modeled():
    cfg = SimpleNamespace(
        MIN_GAIN=5.0,
        PULLBACK_AUGMENT_ENABLED=True,
        PULLBACK_AUGMENT_REVERSAL_MIN=1.0,
    )
    assert allows_reaugment(cfg, 3.1, 3.1, 4.0, 1.0) == (
        False,
        "LOSING_POSITION_HARD_BLOCK",
    )
    assert allows_reaugment(cfg, 3.1, 3.1, 6.2, 1.0) == (True, "OK")
    assert allows_reaugment(
        cfg, 2.8, 2.8, 4.0, 1.0, vector_exempt=True
    ) == (True, "OK")


def test_ppl_reaugment_below_native_three_percent_gate_does_not_book():
    result = _run(2.8)
    ledger = result["ledger"]
    taps = [event for event in ledger if event.get("reason", "").startswith("PPL_TP_")]
    assert taps
    assert taps[0]["bar"] < 9
    assert not any(event.get("reason") == "PPL_REAUGMENT_WT_CROSS" for event in ledger)


def test_ppl_reaugment_above_native_three_percent_gate_books_same_bar():
    result = _run(3.2)
    ledger = result["ledger"]
    reaugments = [event for event in ledger if event.get("reason") == "PPL_REAUGMENT_WT_CROSS"]
    assert len(reaugments) == 1
    assert reaugments[0]["bar"] == 9


def test_ppl_reaugment_three_percent_mask_matches_extracted_native_branch():
    native = _native_real_gain_gate()
    cfg = SimpleNamespace(
        MIN_GAIN=3.0,
        PULLBACK_AUGMENT_ENABLED=True,
        PULLBACK_AUGMENT_REVERSAL_MIN=1.0,
    )
    for raw_gain in (2.8, 3.0, 3.2):
        native_block = native(True, 20.0, raw_gain, False, False, False, "QUICK_AUGMENT", "PROBE_LONG", False)
        helper_pass, helper_reason = allows_reaugment(
            cfg, raw_gain, raw_gain * 2.0, raw_gain, 1.0
        )
        assert (native_block == "ALLOWED") is helper_pass
        if raw_gain < 3.0:
            assert native_block != "ALLOWED"
            assert helper_reason == "EXEC_AUGMENT_GATE_3PCT"

