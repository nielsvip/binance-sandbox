"""MU_LONG 19-vs-61 gap: B_KZONE venue-flag parity (2026-10-06).

Live stocks reads ONLY K_ZONE_ENTRY_ENABLED_TRADIER (tradier_manage
process_position branch B); live crypto reads K_ZONE_ENTRY_ENABLED
(ez_positions_quick rate). The vector engine fired the crypto B_KZONE block
for MODE=tradier from the crypto flag, minting 8 vec-only MU_LONG opens
(S1 parity audit 2026-10-05: vec 61 vs live 19).

Venue rule enforced here: MODE=tradier builds B_KZONE_TRADIER (from the
_TRADIER flag) and never B_KZONE; crypto keeps B_KZONE. Synthetic NPZ so
the test runs on Mac staging without S1.
"""
import numpy as np
import v12_quick_engine as V


def _npz(n=300):
    close = 100.0 + np.arange(n) * 0.01
    return {
        "close": close, "open": close, "high": close * 1.001, "low": close * 0.999,
        "volume": np.full(n, 1000.0), "timestamps": 1787558400 + np.arange(n) * 900,
        "close_15m": close,
        "stoch_k": np.full(n, 20.0), "stoch_d": np.full(n, 25.0),
        "stoch_k_15m": np.full(n, 20.0),
        "stoch_k_1h": np.full(n, 20.0), "stoch_d_1h": np.full(n, 15.0),
        "wt1_15m": np.full(n, 1.0), "wt2_15m": np.full(n, 0.0),
        "dc_high_1h": close * 1.02, "dc_low_1h": close * 0.98,
    }


def _cfg_tradier():
    cfg = V.QuickConfig()
    cfg.apply_tradier_defaults()
    cfg.MODE = "tradier"
    return cfg


def test_tradier_crypto_flag_builds_no_bkzone():
    cfg = _cfg_tradier()
    cfg.K_ZONE_ENTRY_ENABLED = True  # crypto flag True (STOCKS_LONG cat_side)
    cfg.K_ZONE_ENTRY_ENABLED_TRADIER = False  # live stocks default
    blocks = V.compute_reentry_blocks(_npz(), 300, True, cfg)
    assert "B_KZONE" not in blocks
    assert "B_KZONE_TRADIER" not in blocks


def test_tradier_tradier_flag_builds_tradier_block_only():
    cfg = _cfg_tradier()
    cfg.K_ZONE_ENTRY_ENABLED = False
    cfg.K_ZONE_ENTRY_ENABLED_TRADIER = True
    blocks = V.compute_reentry_blocks(_npz(), 300, True, cfg)
    assert "B_KZONE" not in blocks
    assert "B_KZONE_TRADIER" in blocks
    assert int(np.asarray(blocks["B_KZONE_TRADIER"]).sum()) == 300


def test_crypto_unchanged():
    cfg = V.QuickConfig()
    cfg.MODE = "crypto"
    cfg.K_ZONE_ENTRY_ENABLED = True
    blocks = V.compute_reentry_blocks(_npz(), 300, True, cfg)
    assert "B_KZONE" in blocks
    assert int(np.asarray(blocks["B_KZONE"]).sum()) == 300
