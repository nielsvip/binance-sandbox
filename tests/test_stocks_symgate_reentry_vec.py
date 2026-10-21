"""STOCKS_SYMGATE_REENTRY vector twin — legs, reclaim, inertness, ledger change."""

import datetime
import sys

import numpy as np

sys.path.insert(0, ".")

import v12_quick_engine as V
import vec_decisions.stocks_symgate_reentry_vec as S
from types import SimpleNamespace

N = 60


def _cfg(**kw):
    base = dict(
        WT_DC_EXIT_THRESHOLD=30.0,
        REENTRY_SYMGATE_SPEED_MIN=0.5,
        RZ_TOP_BB_THRESHOLD=0.85,
        RZ_BOT_BB_THRESHOLD=0.15,
        RZ_ZSCORE_ZONE_ENABLED=True,
        EXIT_SCORER_MIN_CONDITIONS=5,
        EXIT_SCORER_K_EXTREME=75.0,
        EXIT_SCORER_DC_EXTREME=0.80,
        EXIT_SCORER_PARTIAL_SCORE=40.0,
        EXIT_SCORER_FULL_SCORE=100.0,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _hostile():
    n = N
    return {
        "close": np.full(n, 100.0),
        "wt1_1h": np.full(n, -5.0),
        "wt2_1h": np.full(n, 5.0),
        "wt1_4h": np.full(n, -5.0),
        "wt2_4h": np.full(n, 5.0),
        "wt1_D": np.full(n, -5.0),
        "wt2_D": np.full(n, 5.0),
        "stoch_k_1h": np.full(n, 90.0),
        "stoch_k_4h": np.full(n, 90.0),
        "dc_position_1h": np.full(n, 0.95),
        "dc_position_4h": np.full(n, 0.95),
        "bb_pct_b_1h": np.full(n, 0.97),
        "bb_upper_1h": np.full(n, 90.0),
        "dc_high_1h": np.full(n, 90.0),
    }


def test_score_and_zone_bind_long():
    pre = S.precompute(_hostile(), N, True, _cfg())
    assert set(pre) == {"blocked", "score", "zone", "slow", "bull", "bear"}
    assert bool(pre["score"].all())
    assert bool(pre["zone"].all())
    assert bool(pre["blocked"].all())


def test_calm_tape_unblocked():
    n = N
    npz = {
        "close": np.full(n, 100.0),
        "wt1_1h": np.full(n, 5.0),
        "wt2_1h": np.full(n, -5.0),
        "wt1_4h": np.full(n, 5.0),
        "wt2_4h": np.full(n, -5.0),
        "wt1_D": np.full(n, 5.0),
        "wt2_D": np.full(n, -5.0),
        "stoch_k_1h": np.full(n, 50.0),
        "stoch_k_4h": np.full(n, 50.0),
        "dc_position_1h": np.full(n, 0.5),
        "dc_position_4h": np.full(n, 0.5),
        "bb_pct_b_1h": np.full(n, 0.5),
        "bb_upper_1h": np.full(n, 110.0),
        "dc_high_1h": np.full(n, 110.0),
        "wt_velocity_1h": np.linspace(0, 5, n),
        "wt_velocity_4h": np.linspace(0, 5, n),
        "wt_velocity_D": np.linspace(0, 5, n),
    }
    pre = S.precompute(npz, n, True, _cfg())
    assert not bool(pre["score"].any())
    assert not bool(pre["zone"].any())


def test_stale_failopen_waives_zero_speed():
    n = N
    npz = {"close": np.full(n, 100.0)}  # no 5m/15m WT keys at all
    bull, bear = S.speed_arrays(npz, n)
    assert bool((bull == 0.0).all()) and bool((bear == 0.0).all())
    pre = S.precompute(npz, n, True, _cfg())
    assert not bool(pre["slow"].any())


def test_reclaimed_mirrors_live_no_buffer():
    assert S.reclaimed(True, 100.0, 100.0) is True
    assert S.reclaimed(True, 99.99, 100.0) is False
    assert S.reclaimed(False, 100.0, 100.0) is True
    assert S.reclaimed(False, 100.01, 100.0) is False
    assert S.reclaimed(True, 0.0, 100.0) is False


def _synth_npz():
    rng = np.random.default_rng(7)
    days, per = 30, 26
    n = days * per
    t0 = int(
        datetime.datetime(
            2026, 10, 12, 13, 30, tzinfo=datetime.timezone.utc
        ).timestamp()
    )
    ts = np.empty(n, dtype=np.float64)
    for d in range(days):
        for b in range(per):
            ts[d * per + b] = t0 + d * 86400 + b * 900
    px = np.empty(n)
    px[0:200] = np.linspace(100, 106, 200)
    px[200:260] = np.linspace(106, 103.4, 60)
    cb = 260 + 160
    px[260:cb] = 103.65 + 0.05 * np.sin(np.arange(160) / 7.0)
    px[cb : cb + 120] = np.linspace(px[cb - 1], 107, 120)
    px[cb + 120 :] = 107 + np.cumsum(rng.normal(0, 0.008, n - cb - 120))
    against = np.zeros(n, dtype=bool)
    against[250:cb] = True
    npz = {
        "timestamps": ts,
        "close_15m": px,
        "open_15m": np.concatenate([[px[0]], px[:-1]]),
        "high_15m": px + 0.05,
        "low_15m": px - 0.05,
        "close": px,
    }
    for tf, mag in (("15m", 4.0), ("1h", 6.0), ("4h", 6.0), ("D", 5.0)):
        if tf == "15m":
            npz[f"wt1_{tf}"] = np.full(n, mag)
            npz[f"wt2_{tf}"] = np.full(n, -mag)
            npz[f"wt_velocity_{tf}"] = np.full(n, 2.0)
        else:
            s = np.where(against, -1.0, 1.0)
            npz[f"wt1_{tf}"] = s * mag
            npz[f"wt2_{tf}"] = -s * mag
            npz[f"wt_velocity_{tf}"] = np.where(against, 0.0, 2.0)
    k = np.full(n, 55.0)
    k[230:cb] = 88.0
    npz["stoch_k_1h"] = k
    npz["stoch_k_4h"] = k
    npz["stoch_k_15m"] = np.full(n, 55.0)
    dch = np.maximum.accumulate(px) + 0.4
    dcl = np.minimum.accumulate(px) - 0.4
    for tf in ("15m", "1h", "4h", "D"):
        npz[f"dc_high_{tf}"] = dch
        npz[f"dc_low_{tf}"] = dcl
        npz[f"dc_basis_{tf}"] = (dch + dcl) / 2
        npz[f"dc_position_{tf}"] = np.clip(
            (px - dcl) / np.maximum(dch - dcl, 1e-9), 0, 1
        )
        npz[f"dc_width_{tf}"] = np.full(n, 0.05)
    npz["bb_upper_1h"] = dch
    npz["bb_lower_1h"] = dcl
    npz["bb_pct_b_1h"] = np.clip((px - dcl) / np.maximum(dch - dcl, 1e-9), 0, 1)
    npz["wt_zscore_1h"] = np.where(against, -0.5, 0.5)
    npz["wt_zscore_4h"] = np.where(against, -0.5, 0.5)
    npz["ema_200_1h"] = px - 1.0
    npz["ema_200_1h_prev"] = px - 1.0
    for key in ("mfi_1h", "mfi_4h", "mfi_15m", "rsi_1h", "rsi_4h"):
        npz[key] = np.full(n, 55.0)
    return npz


def _run(npz, **kw):
    cfg = V.QuickConfig()
    cfg.apply_tradier_defaults()
    cfg.MODE = "tradier"
    for k, v in kw.items():
        setattr(cfg, k, v)
    return V.simulate_one(npz, "SYNTH", True, cfg, force_initial_seed=True)


def _oc(r):
    led = r.get("ledger") or []
    o = sorted(
        (e.get("bar"), round(float(e.get("price", 0)), 4))
        for e in led
        if e.get("type") == "OPEN"
    )
    c = sorted(
        (e.get("bar_exit"), round(float(e.get("exit_price", 0)), 4))
        for e in led
        if e.get("type") == "CLOSE"
    )
    return o, c


def test_gate_off_is_inert_and_deterministic():
    npz = _synth_npz()
    a = _run(npz)
    b = _run(npz, REENTRY_SYMGATE_ENABLED=False)
    assert a.get("gain_pct") == b.get("gain_pct")
    assert (a.get("ledger") or []) == (b.get("ledger") or [])
    c = _run(npz, REENTRY_SYMGATE_ENABLED=True)
    assert c.get("gain_pct") == _run(npz, REENTRY_SYMGATE_ENABLED=True).get("gain_pct")


def test_enabled_changes_reentry_ledger():
    npz = _synth_npz()
    no, nc = _oc(_run(npz))
    eo, ec = _oc(_run(npz, REENTRY_SYMGATE_ENABLED=True))
    assert len(no) > 2 and len(eo) >= 2
    assert (no, nc) != (eo, ec)
