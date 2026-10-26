import numpy as np


def _cfg(**kwargs):
    defaults = {
        "LR_BAND_LADDER_ENABLED": True,
        "LR_BAND_LADDER_ORDINARY_PARITY_ENABLED": True,
        "LR_BAND_LADDER_STOCH_EXTREME": 30.0,
        "LR_BAND_LADDER_TF_BOTTOM": {"D": 2.0, "4h": 2.0, "1h": 2.0},
        "LR_BAND_LADDER_TF_TOP": {"D": 1.0, "4h": 1.0, "1h": 1.0},
        "LR_BAND_LADDER_TRIGGER": "union",
        "LR_BAND_LADDER_MODE": "flat",
        "LR_BAND_LADDER_BASE_UNIT_USD": 1000.0,
        "LR_BAND_LADDER_CAPACITY_USD": 8000.0,
    }
    defaults.update(kwargs)
    return type("Cfg", (), defaults)()


def _pre():
    out = {}
    for tf in ("D", "4h", "1h"):
        out[tf] = {
            "tok": np.array([900, 900], dtype=np.float64),
            "high": np.array([11, 11], dtype=np.float64),
            "high_prev": np.array([10, 10], dtype=np.float64),
            "low": np.array([9, 9], dtype=np.float64),
            "low_prev": np.array([8, 8], dtype=np.float64),
            "stoch_k": np.array([20, 20], dtype=np.float64),
            "stoch_k_prev": np.array([10, 10], dtype=np.float64),
            "pct_b": np.array([0.2, 0.2], dtype=np.float64),
            "bull": np.array([True, True]),
            "bear": np.array([False, False]),
        }
    return out


def test_flat_open_target_fires_once_on_same_available_bar():
    from vec_decisions import lr_band_ladder_aug as ladder

    cfg = _cfg()
    seen = set()
    target = ladder.fresh_open_target(cfg, True, 0, _pre(), 1000, seen)
    assert target is not None
    assert target.add_notional_usd == 1000.0
    assert target.source_tokens == (("D", 900), ("4h", 900), ("1h", 900))
    assert ladder.fresh_open_target(cfg, True, 1, _pre(), 1000, seen) is None


def test_flat_open_target_requires_both_live_switches():
    from vec_decisions import lr_band_ladder_aug as ladder

    cfg = _cfg(LR_BAND_LADDER_ORDINARY_PARITY_ENABLED=False)
    assert ladder.fresh_open_target(cfg, True, 0, _pre(), 1000, set()) is None
