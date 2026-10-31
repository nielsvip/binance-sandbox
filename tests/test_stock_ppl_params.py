from types import SimpleNamespace

from vec_decisions.reduce_profit_lock import ppl_step as vector_ppl_step
from vec_decisions.stock_ppl_params import stock_ppl_params
from vec_decisions.twin_sizing_reduce import ppl_step as native_ppl_step


def _cfg(enabled):
    return SimpleNamespace(
        PARTIAL_PROFIT_LOCK_ENABLED=enabled,
        PARTIAL_PROFIT_LOCK_GAIN_PCT=1.5,
        PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT=1.75,
        PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT=0.10,
        PARTIAL_PROFIT_LOCK_FRAC=0.5,
        PARTIAL_PROFIT_LOCK_GAIN_PCT_TRADIER=0.5,
        PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT_TRADIER=0.75,
        PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT_TRADIER=0.02,
        PARTIAL_PROFIT_LOCK_FRAC_TRADIER=0.625,
    )


def test_stock_ppl_params_use_tradier_keys_and_preserve_master_off():
    cfg = _cfg(False)
    assert stock_ppl_params(lambda key, default: getattr(cfg, key, default)) == (
        False,
        0.5,
        0.75,
        0.02,
        0.625,
    )


def test_native_and_vector_stock_ppl_both_fire_at_stock_gain_boundary():
    cfg = _cfg(True)
    getter = lambda key, default=None: getattr(cfg, key, default)
    native = native_ppl_step(getter, 0.6, 100.6, 100.0, 8.0, 1.0, {}, True)
    vector_action, vector_frac, _, _ = vector_ppl_step(
        cfg, True, True, 100.6, 100.0, 0.6, {}
    )
    assert native["action"] == "reduce"
    assert vector_action == "REDUCE"
    assert vector_frac == 0.625


def test_generic_crypto_values_do_not_override_stock_ppl_values():
    cfg = _cfg(True)
    getter = lambda key, default=None: getattr(cfg, key, default)
    native = native_ppl_step(getter, 0.6, 100.6, 100.0, 8.0, 1.0, {}, True)
    vector_action, _, _, _ = vector_ppl_step(
        cfg, True, True, 100.6, 100.0, 0.6, {}
    )
    assert native["action"] == "reduce"
    assert vector_action == "REDUCE"
