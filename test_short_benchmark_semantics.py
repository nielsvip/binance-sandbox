import importlib.util
from pathlib import Path


def _load_reopt():
    path = Path(__file__).with_name("tools") / "reopt_loop.py"
    spec = importlib.util.spec_from_file_location("reopt_loop_short_benchmark_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_short_benchmark_positive_decline_has_ratio():
    mod = _load_reopt()
    ratio, beats = mod.eval_vs_bh("MSTR_SHORT", 30.0, -10.0)
    assert ratio == 3.0
    assert beats is True


def test_short_benchmark_bull_market_uses_cash_floor_not_negative_ratio():
    mod = _load_reopt()
    ratio, beats = mod.eval_vs_bh("NVDA_SHORT", -10.0, 100.0)
    assert ratio is None
    assert beats is False
    ratio, beats = mod.eval_vs_bh("NVDA_SHORT", 10.0, 100.0)
    assert ratio is None
    assert beats is True


def test_long_benchmark_semantics_unchanged():
    mod = _load_reopt()
    ratio, beats = mod.eval_vs_bh("NVDA_LONG", 150.0, 100.0)
    assert ratio == 1.5
    assert beats is True
