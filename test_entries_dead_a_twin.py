"""entries-dead-A twin parity (2026-10-04 wiring mandate).

Proves vec_decisions.twin_entries_dead_a reproduces the wired semantics:
  FUNDING crowd-contrarian z-fade (long entry z<=-Z / short entry z>=+Z,
    long exit z>=+Z / short exit z<=-Z), crypto-only, venue-gated;
  OI_SURGE oi_change_1h_pct >= PCT both sides, crypto-only;
  RSI2_XTREME 10/90 extremes per TF key (missing key = honest non-binding).
Default-inert (None / (False,'')); vec rolling z == live funding_z_last.
"""
import math
from types import SimpleNamespace
import numpy as np
import vec_decisions.twin_entries_dead_a as X


def _safe(npz, key, n, default):
    if key in npz:
        a = np.asarray(npz[key], dtype=float).ravel()
        if a.size >= n:
            return a[:n]
        out = np.full(n, default, dtype=float)
        out[:a.size] = a
        return out
    return np.full(n, default, dtype=float)


def _cfg(**kw):
    return SimpleNamespace(**kw)


def _get(d):
    return lambda k, default: d.get(k, default)


def test_inert_defaults_vec():
    c = _cfg()
    npz = {"rsi_2_1h": np.full(16, 5.0), "oi_change_1h_pct": np.full(16, 9.0), "funding_rate": np.full(16, 0.01)}
    for is_long in (True, False):
        assert X.entry_mask(npz, 16, is_long, c, _safe) is None
        assert X.exit_mask(npz, 16, is_long, c, _safe) is None
    ct = _cfg(MODE="tradier")
    for is_long in (True, False):
        assert X.entry_mask(npz, 16, is_long, ct, _safe) is None
        assert X.exit_mask(npz, 16, is_long, ct, _safe) is None


def test_inert_defaults_live():
    g = _get({})
    ind = {"rsi_2_1h": 5.0, "oi_change_1h_pct": 9.0, "funding_rate": 0.01}
    for is_long in (True, False):
        for venue in (True, False):
            assert X.check_entry_proposal(g, is_long, ind, "BTCUSDC", None, None, venue) == (False, "")
            assert X.check_exit(g, is_long, ind, "BTCUSDC", None, None, venue) == (False, "")
            assert X.check_entry_proposal(g, is_long, {}, "BTCUSDC", None, None, venue) == (False, "")
            assert X.check_exit(g, is_long, {}, "BTCUSDC", None, None, venue) == (False, "")


def test_module_hygiene_no_stubs():
    src = open("vec_decisions/twin_entries_dead_a.py").read()
    for s in X.SWITCHES:
        assert f'"{s}"' in src, s
    assert len(X.SWITCHES) == 12
    for bad in ("_ =", "and False", "or True", "np.arange(n)%", "^= True"):
        assert bad not in src, bad


def test_rsi2_entry_boundaries():
    r = np.array([10.0, 10.01, 9.9, 50.0, 90.0, 89.9, 90.1, 50.0])
    npz = {"rsi_2_1h": r}
    c = _cfg(RSI2_XTREME_ENTRY_ENABLED=True, RSI2_XTREME_ENTRY_TF="1h")
    m = X.entry_mask(npz, 8, True, c, _safe)
    assert m.tolist() == [True, False, True, False, False, False, False, False]
    m = X.entry_mask(npz, 8, False, c, _safe)
    assert m.tolist() == [False, False, False, False, True, False, True, False]


def test_rsi2_exit_boundaries():
    r = np.array([10.0, 10.01, 9.9, 50.0, 90.0, 89.9, 90.1, 50.0])
    npz = {"rsi_2_1h": r}
    c = _cfg(RSI2_XTREME_EXIT_ENABLED=True, RSI2_XTREME_EXIT_TF="1h")
    m = X.exit_mask(npz, 8, True, c, _safe)
    assert m.tolist() == [False, False, False, False, True, False, True, False]
    m = X.exit_mask(npz, 8, False, c, _safe)
    assert m.tolist() == [True, False, True, False, False, False, False, False]


def test_rsi2_tf_key_resolution():
    assert X.rsi2_key_for({"rsi_2_1h": 1}, "1h") == "rsi_2_1h"
    assert X.rsi2_key_for({"rsi2_15m": 1}, "15m") == "rsi2_15m"
    assert X.rsi2_key_for({"rsi_2_4h": 1}, "4h") == "rsi_2_4h"
    assert X.rsi2_key_for({"rsi_2_1h": 1}, "D") is None
    assert X.rsi2_key_for({"rsi_2_1h": 1}, "4h") is None
    assert X.rsi2_key_for({}, "1h") is None
    assert X.rsi2_key_for({"rsi_2_npz_15m": 1}, "15m") == "rsi_2_npz_15m"
    c = _cfg(RSI2_XTREME_ENTRY_ENABLED=True, RSI2_XTREME_ENTRY_TF="15m")
    m = X.entry_mask({"rsi2_15m": np.full(6, 5.0)}, 6, True, c, _safe)
    assert m.tolist() == [True] * 6
    c4 = _cfg(RSI2_XTREME_ENTRY_ENABLED=True, RSI2_XTREME_ENTRY_TF="4h")
    m = X.entry_mask({"rsi_2_1h": np.full(6, 5.0)}, 6, True, c4, _safe)
    assert m is not None and m.tolist() == [False] * 6
    mt = _cfg(RSI2_XTREME_ENTRY_ENABLED=True, RSI2_XTREME_ENTRY_TF="4h", MODE="tradier")
    m = X.entry_mask({"rsi_2_4h": np.full(6, 5.0)}, 6, True, mt, _safe)
    assert m.tolist() == [True] * 6
    cD = _cfg(RSI2_XTREME_ENTRY_ENABLED=True, RSI2_XTREME_ENTRY_TF="D")
    m = X.entry_mask({"rsi_2_1h": np.full(6, 5.0)}, 6, True, cD, _safe)
    assert m is not None and m.tolist() == [False] * 6


def test_oi_surge_entry_exit():
    oi = np.array([3.0, 2.99, 3.01, 0.0, -5.0, 9.0])
    npz = {"oi_change_1h_pct": oi}
    ce = _cfg(OI_SURGE_ENTRY_ENABLED=True, OI_SURGE_ENTRY_PCT=3.0)
    for is_long in (True, False):
        m = X.entry_mask(npz, 6, is_long, ce, _safe)
        assert m.tolist() == [True, False, True, False, False, True]
    cx = _cfg(OI_SURGE_EXIT_ENABLED=True, OI_SURGE_EXIT_PCT=3.0)
    for is_long in (True, False):
        m = X.exit_mask(npz, 6, is_long, cx, _safe)
        assert m.tolist() == [True, False, True, False, False, True]
    cz = _cfg(OI_SURGE_ENTRY_ENABLED=True, OI_SURGE_ENTRY_PCT=0.0)
    assert X.entry_mask(npz, 6, True, cz, _safe) is None
    ct = _cfg(OI_SURGE_ENTRY_ENABLED=True, OI_SURGE_ENTRY_PCT=3.0, MODE="tradier")
    assert X.entry_mask(npz, 6, True, ct, _safe) is None
    ctx = _cfg(OI_SURGE_EXIT_ENABLED=True, OI_SURGE_EXIT_PCT=3.0, MODE="tradier")
    assert X.exit_mask(npz, 6, True, ctx, _safe) is None


def test_funding_z_last_pure():
    z = X.funding_z_last([1.0, 2.0, 3.0, 4.0, 5.0])
    assert abs(z - math.sqrt(2.0)) < 1e-12
    assert X.funding_z_last([0.0001] * 21) is None
    assert X.funding_z_last([1.0, 2.0, 3.0]) is None
    assert X.funding_z_last([]) is None
    assert X.funding_z_last(None) is None
    assert X.funding_z_last([float("nan")] * 21) is None
    z = X.funding_z_last([0.0001] * 20 + [0.01])
    assert z is not None and z > 4.0
    z = X.funding_z_last([0.0001] * 20 + [-0.005])
    assert z is not None and z < -4.0


def _spike_funding(nseg=30, seg=32, base=0.0001, spike=0.01, sign=1.0):
    vals = [base] * (nseg - 1) + [base + sign * (spike - base)]
    return np.repeat(np.array(vals, dtype=float), seg)


def test_funding_vec_contrarian():
    fr = _spike_funding(sign=1.0)
    n = fr.size
    npz = {"funding_rate": fr}
    ce = _cfg(FUNDING_CROWD_ENTRY_ENABLED=True, FUNDING_CROWD_ENTRY_Z=2.0)
    ml = X.entry_mask(npz, n, True, ce, _safe)
    ms = X.entry_mask(npz, n, False, ce, _safe)
    assert ml is not None and ms is not None
    assert not ml.any()
    assert ms.sum() == 32 and ms[-1]
    cx = _cfg(FUNDING_CROWD_EXIT_ENABLED=True, FUNDING_CROWD_EXIT_Z=2.0)
    xl = X.exit_mask(npz, n, True, cx, _safe)
    xs = X.exit_mask(npz, n, False, cx, _safe)
    assert xl.sum() == 32 and xl[-1]
    assert not xs.any()
    frn = _spike_funding(sign=-1.0)
    npzn = {"funding_rate": frn}
    mln = X.entry_mask(npzn, n, True, ce, _safe)
    assert mln.sum() == 32 and mln[-1]
    xln = X.exit_mask(npzn, n, True, cx, _safe)
    assert not xln.any()


def test_funding_vec_honest_nonbinding():
    n = 288
    cz = _cfg(FUNDING_CROWD_ENTRY_ENABLED=True, FUNDING_CROWD_ENTRY_Z=2.0)
    m = X.entry_mask({"funding_rate": np.zeros(n)}, n, True, cz, _safe)
    assert m is not None and not m.any()
    m = X.entry_mask({"funding_rate": np.full(n, 0.0001)}, n, True, cz, _safe)
    assert m is not None and not m.any()
    m = X.entry_mask({}, n, True, cz, _safe)
    assert m is not None and not m.any()
    ct = _cfg(FUNDING_CROWD_ENTRY_ENABLED=True, FUNDING_CROWD_ENTRY_Z=2.0, MODE="tradier")
    assert X.entry_mask({"funding_rate": _spike_funding()}, 960, True, ct, _safe) is None
    ctx = _cfg(FUNDING_CROWD_EXIT_ENABLED=True, FUNDING_CROWD_EXIT_Z=2.0, MODE="tradier")
    assert X.exit_mask({"funding_rate": _spike_funding()}, 960, True, ctx, _safe) is None
    cz0 = _cfg(FUNDING_CROWD_ENTRY_ENABLED=True, FUNDING_CROWD_ENTRY_Z=0.0)
    assert X.entry_mask({"funding_rate": _spike_funding()}, 960, True, cz0, _safe) is None


def test_vec_live_z_agreement():
    import random
    random.seed(11)
    for trial in range(25):
        nseg = random.randint(6, 40)
        vals = [random.uniform(-0.002, 0.002) for _ in range(nseg)]
        vals = [v + (0.005 if trial % 2 else -0.005) for v in vals[:-1]] + [vals[-1] + (0.01 if trial % 3 else -0.01)]
        fr = np.repeat(np.array(vals, dtype=float), X.FUNDING_STRIDE_BARS)
        n = fr.size
        zser = X.funding_z_series(fr)
        for i in (n - 1, n // 2, 5 * X.FUNDING_STRIDE_BARS):
            obs = [fr[i - 32 * k] for k in range(21) if i - 32 * k >= 0][::-1]
            exp = X.funding_z_last(obs)
            got = zser[i]
            if exp is None:
                assert not np.isfinite(got), (trial, i)
            else:
                assert abs(got - exp) < 1e-9, (trial, i, got, exp)


def test_live_scalar_fires_and_reasons():
    g = _get({"RSI2_XTREME_ENTRY_ENABLED": True, "RSI2_XTREME_ENTRY_TF": "1h"})
    fire, reason = X.check_entry_proposal(g, True, {"rsi_2_1h": 8.0}, "BTCUSDC")
    assert fire and reason.startswith("RSI2_XTREME_ENTRY_LONG_1h_")
    fire, _ = X.check_entry_proposal(g, True, {"rsi_2_1h": 50.0}, "BTCUSDC")
    assert not fire
    fire, _ = X.check_entry_proposal(g, True, {}, "BTCUSDC")
    assert not fire
    gx = _get({"RSI2_XTREME_EXIT_ENABLED": True, "RSI2_XTREME_EXIT_TF": "1h"})
    fire, reason = X.check_exit(gx, True, {"rsi_2_1h": 92.5}, "AAPL", None, None, False)
    assert fire and "RSI2_XTREME_EXIT_LONG_1h_92.5" in reason
    go = _get({"OI_SURGE_ENTRY_ENABLED": True, "OI_SURGE_ENTRY_PCT": 3.0})
    for is_long in (True, False):
        fire, reason = X.check_entry_proposal(go, is_long, {"oi_change_1h_pct": 4.0}, "BTCUSDC")
        assert fire and reason.startswith("OI_SURGE_ENTRY_")
        fire, _ = X.check_entry_proposal(go, is_long, {"oi_change_1h_pct": 4.0}, "BTCUSDC", None, None, False)
        assert not fire
    hist = [0.0001] * 20
    gf = _get({"FUNDING_CROWD_ENTRY_ENABLED": True, "FUNDING_CROWD_ENTRY_Z": 2.0})
    fire, reason = X.check_entry_proposal(gf, False, {"funding_rate": 0.01}, "BTCUSDC", None, hist, True)
    assert fire and reason.startswith("FUNDING_CROWD_ENTRY_SHORT_")
    fire, _ = X.check_entry_proposal(gf, True, {"funding_rate": 0.01}, "BTCUSDC", None, hist, True)
    assert not fire
    fire, _ = X.check_entry_proposal(gf, False, {"funding_rate": 0.01}, "BTCUSDC", None, hist, False)
    assert not fire
    fire, _ = X.check_entry_proposal(gf, False, {}, "BTCUSDC", None, hist, True)
    assert not fire
    fire, reason = X.check_entry_proposal(gf, False, {"funding_rate": 0.01}, "BTCUSDC:TRB_LONG", None, hist, True)
    assert fire
    gfe = _get({"FUNDING_CROWD_EXIT_ENABLED": True, "FUNDING_CROWD_EXIT_Z": 2.0})
    fire, reason = X.check_exit(gfe, True, {"funding_rate": 0.01}, "BTCUSDC", None, hist, True)
    assert fire and reason.startswith("FUNDING_CROWD_EXIT_LONG_")


def test_vec_formula_agreement_grid():
    import random
    random.seed(7)
    for is_long in (True, False):
        for tf, key in (("1h", "rsi_2_1h"), ("15m", "rsi2_15m"), ("4h", "rsi_2_4h")):
            d = {"RSI2_XTREME_ENTRY_ENABLED": True, "RSI2_XTREME_ENTRY_TF": tf, "RSI2_XTREME_EXIT_ENABLED": True, "RSI2_XTREME_EXIT_TF": tf,
                 "OI_SURGE_ENTRY_ENABLED": True, "OI_SURGE_ENTRY_PCT": 3.0, "OI_SURGE_EXIT_ENABLED": True, "OI_SURGE_EXIT_PCT": 3.0}
            c = _cfg(**d)
            for _ in range(30):
                n = 24
                r = np.array([random.uniform(0, 100) for _ in range(n)])
                oi = np.array([random.uniform(-6, 6) for _ in range(n)])
                npz = {key: r, "oi_change_1h_pct": oi}
                me = X.entry_mask(npz, n, is_long, c, _safe)
                mx = X.exit_mask(npz, n, is_long, c, _safe)
                for i in range(n):
                    rsi_e = (r[i] <= 10.0) if is_long else (r[i] >= 90.0)
                    rsi_x = (r[i] >= 90.0) if is_long else (r[i] <= 10.0)
                    oi_f = oi[i] >= 3.0
                    assert bool(me[i]) == bool(rsi_e or oi_f), (is_long, tf, i)
                    assert bool(mx[i]) == bool(rsi_x or oi_f), (is_long, tf, i)


def test_live_market_data_fallback():
    g = _get({"OI_SURGE_EXIT_ENABLED": True, "OI_SURGE_EXIT_PCT": 3.0})
    fire, reason = X.check_exit(g, True, {}, "ETHUSDC", {"ETHUSDC": {"oi_change_1h_pct": 5.5}}, None, True)
    assert fire and "OI_SURGE_EXIT_LONG" in reason
    fire, _ = X.check_exit(g, True, {}, "ETHUSDC", {"OTHER": {"oi_change_1h_pct": 5.5}}, None, True)
    assert not fire
