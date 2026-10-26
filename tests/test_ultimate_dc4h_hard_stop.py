"""ULTIMATE_DC_4H_HARD_STOP — durable regression for 2026-09-19 mandate.
No trade may be held through dc_low_4h (LONG) / dc_high_4h (SHORT) at any loss.
Gain / MIN_HOLD / NOLOSS / hedge-protect agnostic, HARD_STOP reason."""

import pathlib, openpyxl
import numpy as np
import config, config_tradier
import v12_quick_engine as vq
import pytest


def _mk(n, close, dl, dh, dl15=None, dh15=None, low3=None, high3=None, open3=None):
    ts = np.arange(1700000000, 1700000000 + n * 180, 180)
    d = {
        "close_3m": close,
        "close_15m": close,
        "close_5m": close,
        "close": close,
        "low_3m": np.asarray(low3 if low3 is not None else close, dtype=float),
        "high_3m": np.asarray(high3 if high3 is not None else close, dtype=float),
        "open_3m": np.asarray(open3 if open3 is not None else close, dtype=float),
        "dc_low_4h": dl,
        "dc_high_4h": dh,
        "timestamps": ts,
        "timestamp_3m": ts,
        "timestamp_15m": ts,
        "wt1_3m": np.zeros(n),
        "wt2_3m": np.zeros(n),
        "wt1_15m": np.zeros(n),
        "wt2_15m": np.zeros(n),
        "wt1_1h": np.zeros(n),
        "wt2_1h": np.zeros(n),
        "wt1_4h": np.zeros(n),
        "wt2_4h": np.zeros(n),
        "wt1_D": np.zeros(n),
        "wt2_D": np.zeros(n),
    }
    # FIXED_STOP reads the 15m channel (frozen at entry): without it the
    # fallback entry-based stop fires in 2-3 bars and the 4h breach is never
    # reached. Flat channels keep FIXED quiet until past the 4h cross.
    if dl15 is not None:
        d["dc_low_15m"] = dl15
    if dh15 is not None:
        d["dc_high_15m"] = dh15
    return d


def _wide_uncond(cfg):
    # Widen the switch-driven UNCOND leg (same ±buffer family) so the
    # ABSOLUTE ULTIMATE backstop is the executor under test. FIXED_STOP has
    # its own buffer knob and is unaffected.
    cfg.UNCOND_DC_HARD_STOP_BUFFER_PCT = 2.0
    return cfg


def test_config_flag_enabled():
    assert getattr(config.Config(), "ULTIMATE_DC_4H_STOP_ENABLED", False) is True
    assert (
        getattr(config_tradier.TradierConfig(), "ULTIMATE_DC_4H_STOP_ENABLED", False)
        is True
    )


def _closes(r):
    return [t for t in r["ledger"] if t.get("pnl_pct") is not None]


def _ult_exits(r):
    # Case-insensitive: live emits ULTIMATE_DC_4h_HARD_STOP_* (both venues),
    # v12 emits ULTIMATE_DC_4h_HARD_STOP at dc_4h_* 0.25pct channel.
    return [
        t
        for t in _closes(r)
        if str(t.get("exit_reason") or "")
        .upper()
        .startswith("ULTIMATE_DC_4H_HARD_STOP")
    ]


def test_live_reason_contains_hard_stop():
    # USER 2026-10-25: reason is built as ULTIMATE_DC_{TF}_HARD_STOP (TF always
    # 4h); the literal with baked-in 4H never appears — pin family + bypass.
    for fname in ["ez_manage.py", "ez_positions_quick.py", "tradier_manage.py"]:
        txt = pathlib.Path(fname).read_text()
        assert (
            "ULTIMATE_DC_" in txt and "_HARD_STOP" in txt
        ), f"{fname} missing HARD_STOP"
    txt_ez = pathlib.Path("ez_manage.py").read_text()
    assert 'or "HARD_STOP" in _hpo_reason_up' in txt_ez


def test_v12_long_breach_is_hard_stop():
    n = 150
    close = np.linspace(0.0285, 0.0239, n)
    dl = np.full(n, 0.027)
    dh = np.full(n, 0.031)
    cfg = _wide_uncond(vq.QuickConfig())
    cfg.MODE = "crypto"
    cfg.BASE_TF = "3m"
    cfg.START_POSITION_SIZE = 500
    cfg.COOLDOWN_BARS = 0
    cfg.MIN_HOLD_BARS = 0
    cfg.NOLOSS_ENABLED = True
    cfg.PROFIT_TARGET_ENABLED = False
    cfg.STOP_LOSS_ENABLED = False
    r = vq.simulate_one(
        _mk(n, close, dl, dh, np.full(n, 0.0269), np.full(n, 0.031)),
        "T_LONG",
        True,
        cfg,
        force_initial_seed=True,
    )
    ults = _ult_exits(r)
    assert ults, "expected ULTIMATE_DC_4H exit"
    # USER 2026-10-25 terms: channel -0.25%, TF 4h, no veto
    cross = int(np.where(close <= 0.027 * 0.9975)[0][0])
    assert ults[0]["bar_exit"] == cross
    assert "0.25pct channel" in ults[0]["exit_reason"]


def test_v12_short_breach_is_hard_stop():
    n = 150
    close = np.linspace(0.020, 0.025, n)
    cfg = _wide_uncond(vq.QuickConfig())
    cfg.MODE = "crypto"
    cfg.BASE_TF = "3m"
    cfg.START_POSITION_SIZE = 500
    cfg.COOLDOWN_BARS = 0
    cfg.MIN_HOLD_BARS = 0
    cfg.NOLOSS_ENABLED = True
    cfg.PROFIT_TARGET_ENABLED = False
    cfg.STOP_LOSS_ENABLED = False
    r = vq.simulate_one(
        _mk(
            n,
            close,
            np.full(n, 0.018),
            np.full(n, 0.022),
            np.full(n, 0.018),
            np.full(n, 0.0221),
        ),
        "T_SHORT",
        False,
        cfg,
        force_initial_seed=True,
    )
    assert _ult_exits(r), "expected ULTIMATE_DC_4H exit"


def test_v12_no_breach_and_missing_dc_hold():
    n = 150
    close = np.linspace(0.0285, 0.0239, n)
    cfg = vq.QuickConfig()
    cfg.MODE = "crypto"
    cfg.BASE_TF = "3m"
    cfg.START_POSITION_SIZE = 500
    cfg.COOLDOWN_BARS = 0
    cfg.MIN_HOLD_BARS = 0
    cfg.NOLOSS_ENABLED = True
    cfg.PROFIT_TARGET_ENABLED = False
    cfg.STOP_LOSS_ENABLED = False
    # 15m channel present so FIXED stays quiet: the position must be HELD
    # the whole run (FINAL_MTM at the last bar), never ultimate-stopped.
    r = vq.simulate_one(
        _mk(
            n,
            close,
            np.full(n, 0.010),
            np.full(n, 0.05),
            np.full(n, 0.009),
            np.full(n, 0.05),
        ),
        "T_NOB",
        True,
        cfg,
        force_initial_seed=True,
    )
    assert not _ult_exits(r)
    r2 = vq.simulate_one(
        _mk(n, close, np.zeros(n), np.zeros(n)),
        "T_MISS",
        True,
        cfg,
        force_initial_seed=True,
    )
    assert not _ult_exits(r2)


def test_v12_min_hold_and_noloss_bypass():
    n = 150
    close = np.linspace(0.0285, 0.0239, n)
    dl = np.full(n, 0.027)
    dh = np.full(n, 0.031)
    cfg = _wide_uncond(vq.QuickConfig())
    cfg.MODE = "crypto"
    cfg.BASE_TF = "3m"
    cfg.START_POSITION_SIZE = 500
    cfg.COOLDOWN_BARS = 0
    cfg.MIN_HOLD_BARS = 100
    cfg.NOLOSS_ENABLED = True
    cfg.PROFIT_TARGET_ENABLED = False
    cfg.STOP_LOSS_ENABLED = False
    r = vq.simulate_one(
        _mk(n, close, dl, dh, np.full(n, 0.0269), np.full(n, 0.031)),
        "T_MH",
        True,
        cfg,
        force_initial_seed=True,
    )
    cross = int(np.where(close <= 0.027 * 0.9975)[0][0])
    ults = _ult_exits(r)
    assert ults and ults[0]["bar_exit"] == cross  # breach < MIN_HOLD still closes


def test_v12_dc4h_intrabar_wick_touch_closes_after_recovery():
    n = 150
    close = np.full(n, 0.0285)
    low = close.copy()
    low[50] = 0.0268  # touches dc_low_4h * 0.9975 then recovers before this bar closes
    cfg = _wide_uncond(vq.QuickConfig())
    cfg.MODE = "crypto"
    cfg.BASE_TF = "3m"
    cfg.START_POSITION_SIZE = 500
    cfg.COOLDOWN_BARS = 0
    cfg.MIN_HOLD_BARS = 0
    cfg.PROFIT_TARGET_ENABLED = False
    r = vq.simulate_one(
        _mk(n, close, np.full(n, 0.027), np.full(n, 0.031), np.full(n, 0.025), np.full(n, 0.031), low3=low, open3=close),
        "T_WICK_LONG",
        True,
        cfg,
        force_initial_seed=True,
    )
    ults = _ult_exits(r)
    assert ults and ults[0]["bar_exit"] == 50
    assert ults[0]["price"] == pytest.approx(0.027 * 0.9975)


def test_v12_rolling_1h_15m_levels_do_not_create_loss_stops():
    n = 150
    close = np.full(n, 0.0285)
    cfg = _wide_uncond(vq.QuickConfig())
    cfg.MODE = "crypto"
    cfg.BASE_TF = "3m"
    cfg.START_POSITION_SIZE = 500
    cfg.COOLDOWN_BARS = 0
    cfg.MIN_HOLD_BARS = 0
    cfg.PROFIT_TARGET_ENABLED = False
    npz = _mk(n, close, np.full(n, 0.020), np.full(n, 0.040), np.full(n, 0.030), np.full(n, 0.027))
    npz["dc_low_1h"] = np.full(n, 0.030)
    npz["dc_low_15m"] = np.full(n, 0.030)
    r = vq.simulate_one(npz, "T_NO_ROLLING", True, cfg, force_initial_seed=True)
    assert not _ult_exits(r)


def test_template_flags_present():
    # NOTE: generic SPREADSHEETS/TEMPLATE.xlsx was deleted 2026-09-28; the
    # four per-venue workbooks are the live set (same pre-existing class as
    # test_dc_hard_stop_tf::test_template_has_switch).
    for name in [
        "TEMPLATE_CRYPTO_LONG.xlsx",
        "TEMPLATE_CRYPTO_SHORT.xlsx",
        "TEMPLATE_STOCKS_LONG.xlsx",
        "TEMPLATE_STOCKS_SHORT.xlsx",
    ]:
        p = pathlib.Path(f"SPREADSHEETS/{name}")
        # read_only=False avoids BadZipFile on concurrently-written large files
        wb = openpyxl.load_workbook(str(p), read_only=False, data_only=True)
        found = False
        for ws in wb.worksheets:
            for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
                for c in row:
                    if c.value and "ULTIMATE_DC_4H_STOP_ENABLED" in str(c.value):
                        found = True
                        break
                if found:
                    break
            if found:
                break
        assert found, f"{name} missing ULTIMATE flag"
