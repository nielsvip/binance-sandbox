"""Shared harness for PARITY LANE C stocks live-twin tests (2026-10-06).

Live side = tradier_filter_tf_twins (called from tradier_manage.process_position FILTER_TF veto stack).
Vec side  = the exact vec_decisions functions v12_quick_engine.simulate_one ANDs into entry_sig.
Every bar of a seeded synthetic NPZ is replayed through the live scalar on a dict built from that
bar; live "allowed" (veto is None) must equal vec mask[i] on every bar, both sides, all TFs.
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tradier_filter_tf_twins as L  # noqa: E402

TFS = ("15m", "1h", "4h", "D")
TM_PATH = os.path.join(ROOT, "tradier_manage.py")
CODE_TO_NAME = {v: k for k, v in L.BAR_PATTERN_CODES.items()}


def vsafe(npz, key, n, default=0.0):
    """Byte-copy of v12_quick_engine._safe (v12 cannot be imported in a minimal checkout)."""
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(np.float64)
    return np.full(n, default, dtype=np.float64)


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def bar_dict(npz, i, drop=()):
    d = {}
    for k, v in npz.items():
        if k in drop:
            continue
        x = v[i]
        if k.startswith("bar_pattern_"):
            d[k] = CODE_TO_NAME.get(int(x), "none")
        else:
            d[k] = float(x)
    return d


def getter(d):
    return lambda k: d.get(k)


def rng_npz(n, seed, tfs=TFS, zero_frac=0.08):
    r = np.random.default_rng(seed)
    close = 100 + np.cumsum(r.normal(0, 1, n))
    npz = {"close": close}
    for tf in tfs:
        c3 = close * (1 + r.normal(0, 0.02, n))
        c3[r.random(n) < zero_frac] = 0.0
        npz[f"close_3bar_{tf}"] = c3
        b = r.uniform(-0.3, 1.3, n)
        npz[f"bb_pct_b_{tf}"] = b
        lvl_h = close * (1 + r.normal(0, 0.01, n))
        lvl_l = close * (1 + r.normal(0, 0.01, n))
        lvl_h[r.random(n) < zero_frac] = 0.0
        lvl_l[r.random(n) < zero_frac] = 0.0
        npz[f"dc_high_{tf}_prev"] = lvl_h
        npz[f"dc_low_{tf}_prev"] = lvl_l
        w1 = r.normal(0, 40, n)
        w2 = w1 + r.normal(0, 8, n)
        z = r.random(n) < zero_frac
        w1[z] = 0.0
        w2[z] = 0.0
        npz[f"wt1_{tf}"] = w1
        npz[f"wt2_{tf}"] = w2
        lo = close * (1 - np.abs(r.normal(0, 0.01, n)))
        hi = close * (1 + np.abs(r.normal(0, 0.01, n)))
        lo[r.random(n) < zero_frac] = 0.0
        hi[r.random(n) < zero_frac] = 0.0
        npz[f"low_{tf}"] = lo
        npz[f"high_{tf}"] = hi
        bl = close * (1 + r.normal(-0.004, 0.006, n))
        bu = close * (1 + r.normal(0.004, 0.006, n))
        bl[r.random(n) < zero_frac] = 0.0
        bu[r.random(n) < zero_frac] = 0.0
        npz[f"bb_lower_{tf}"] = bl
        npz[f"bb_upper_{tf}"] = bu
        npz[f"bar_pattern_{tf}"] = r.integers(0, 21, n).astype(np.float64)
    return npz


def assert_agree(npz, vec_mask, live_fn, label):
    n = len(npz["close"])
    vec_allow = np.ones(n, dtype=bool) if vec_mask is None else np.asarray(vec_mask, dtype=bool)
    mism = []
    n_block = 0
    for i in range(n):
        d = bar_dict(npz, i)
        veto = live_fn(getter(d), float(npz["close"][i]))
        live_allow = veto is None
        n_block += int(not live_allow)
        if live_allow != bool(vec_allow[i]):
            mism.append((i, live_allow, bool(vec_allow[i]), veto))
    assert not mism, f"{label}: {len(mism)} live/vec mismatches, first {mism[:3]}"
    return n_block


def tm_source():
    with open(TM_PATH, encoding="utf-8") as fh:
        return fh.read()


def tradier_default(name):
    import config_tradier
    return getattr(config_tradier.TradierConfig(), name)


V12_PATH = os.path.join(ROOT, "v12_quick_engine.py")


def v12_block(start_marker, end_marker):
    """Return the dedented live text of a v12_quick_engine block (start line .. end line inclusive) — executed, never copied."""
    import textwrap
    lines = open(V12_PATH, encoding="utf-8").read().split("\n")
    s = next(k for k, l in enumerate(lines) if start_marker in l)
    e = next(k for k in range(s, len(lines)) if end_marker in lines[k])
    return textwrap.dedent("\n".join(lines[s:e + 1]))


def v12_safeb(npz, key, n):
    v = npz.get(key)
    if v is not None and isinstance(v, np.ndarray) and len(v) == n:
        return v.astype(bool)
    return np.zeros(n, dtype=bool)


def rng_npz_p3(n, seed):
    """rng_npz + keys for phase-3 predicates (row-prev semantics on the 15m grid)."""
    r = np.random.default_rng(seed)
    npz = rng_npz(n, seed)
    close = npz["close"]
    npz["ema_50_15m"] = close * (1 + r.normal(0, 0.01, n))
    npz["ema_50_15m"][r.random(n) < 0.05] = 0.0
    npz["wt_cross_rising_1h"] = (r.random(n) < 0.5).astype(np.float64)
    npz["wt_cross_rising_4h"] = (r.random(n) < 0.5).astype(np.float64)
    npz["relative_volume_15m"] = r.uniform(0.3, 2.5, n)
    for tf in TFS:
        npz[f"close_{tf}_prev"] = close * (1 + r.normal(0, 0.004, n))
        npz[f"low_{tf}_prev"] = close * (1 - np.abs(r.normal(0, 0.01, n)))
        npz[f"ha_{tf}"] = np.where(r.random(n) < 0.5, 1.0, -1.0)
        npz[f"wt_velocity_{tf}"] = r.normal(0, 3, n)
    npz["wt_velocity_3m"] = r.normal(0, 3, n)
    return npz


def prev_row(a):
    p = np.roll(a, 1)
    p[0] = a[0]
    return p
