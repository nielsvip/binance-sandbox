"""Lane D parity: EXIT_VELOCITY_WT vec twin == live predicate (same synthetic inputs).

Live: ez_manage.py GREY_REWIRE chain + tradier_manage.py grey chain call
vec_decisions.twin_exits_dead.velocity_wt_exit_live_fire (no master switch, always on).
Vec: v12_quick_engine precomputes twin_exits_dead.velocity_wt_exit_mask behind
EXIT_VELOCITY_WT_ENABLED (default True = live) and closes in the walk.
"""
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _safe(npz, k, n, default=0.0):
    a = npz.get(k)
    if isinstance(a, np.ndarray) and a.shape[:1] == (n,):
        return a
    return np.full(n, default, dtype=float)


def _grid(n=4000, seed=7):
    rng = np.random.default_rng(seed)
    npz = {}
    for tf in ("15m", "1h", "4h", "D"):
        v = rng.normal(0, 3, n)
        v[rng.random(n) < 0.05] = 0.0
        npz["wt_velocity_%s" % tf] = v
    return npz, n


def test_mask_equals_live_fire_every_bar():
    import vec_decisions.twin_exits_dead as T
    npz, n = _grid()
    for tfs in ("1h,4h,D", "15m", "4h+D", "1h", "OFF", ""):
        get = lambda k, d, _t=tfs: _t if k == "EXIT_VELOCITY_WT_TFS" else d
        for is_long in (True, False):
            m = T.velocity_wt_exit_mask(npz, n, is_long, get, _safe)
            live = []
            for i in range(n):
                ind = {k: float(v[i]) for k, v in npz.items()}
                live.append(T.velocity_wt_exit_live_fire(ind, is_long, get)[0])
            if m is None:
                assert not any(live), (tfs, is_long)
            else:
                assert m.tolist() == live, (tfs, is_long)


def test_missing_key_is_no_fire_both_sides():
    import vec_decisions.twin_exits_dead as T
    get = lambda k, d: d
    m = T.velocity_wt_exit_mask({}, 10, True, get, _safe)
    assert not m.any()
    assert T.velocity_wt_exit_live_fire({}, True, get)[0] is False


def test_quickconfig_default_on_matches_live_always_on():
    import v12_quick_engine as V
    c = V.QuickConfig()
    assert c.EXIT_VELOCITY_WT_ENABLED is True
    assert c.EXIT_VELOCITY_WT_TFS == "1h,4h,D"
    t = V.QuickConfig()
    t.apply_tradier_defaults()
    assert t.EXIT_VELOCITY_WT_ENABLED is False  # lane C proof: tradier parser drops wt_velocity_* -> never fires live on stocks
    assert "EXIT_VELOCITY_WT_ENABLED" in V.AUTO_WIRED_PARAMS


def test_live_call_sites_present():
    ez = (ROOT / "ez_manage.py").read_text()
    tr = (ROOT / "tradier_manage.py").read_text()
    assert "velocity_wt_exit_live_fire" in ez
    assert "velocity_wt_exit_live_fire" in tr
    src = (ROOT / "v12_quick_engine.py").read_text()
    assert "_ted_xv.velocity_wt_exit_mask" in src
    assert "EXIT_VELOCITY_WT {" in src


if __name__ == "__main__":
    for k, f in sorted(globals().items()):
        if k.startswith("test_"):
            f()
            print("PASS", k)
