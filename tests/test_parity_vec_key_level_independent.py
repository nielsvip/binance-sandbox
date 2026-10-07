"""Lane D parity: KEY_LEVEL_CRASH vec leg == live_twins/key_level.decide on the same synthetic inputs,
including the case where ABLATION_DISABLE_QUICK_EXIT=True (live twin runs outside the ablated EPQ chain)."""
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


def _vec_leg(P, cfg, i, gain, is_long):
    """Exact copy of the engine's independent leg condition (v12 'KEY_LEVEL_CRASH_EXIT_ENABLED=True runs the key-level leg')."""
    if not (bool(getattr(cfg, 'KEY_LEVEL_CRASH_EXIT_ENABLED', False)) and bool(getattr(cfg, 'KEY_LEVEL_CRASH_ENABLED', True))):
        return None
    sev = int(P['keylevel_sev'][i])
    if sev >= 3 and not (gain < -0.01 and not bool(getattr(cfg, 'LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED', False))):
        return f"KEY_LEVEL_{'CRASH' if is_long else 'BREAKOUT'}_S{sev}_DC_{'LOW' if is_long else 'HIGH'}_BROKEN"
    return None


def test_vec_leg_equals_live_twin():
    import v12_quick_engine as V
    import vec_decisions.live_exit_chain as L
    from live_twins import key_level as K
    rng = np.random.default_rng(9)
    n = 3000
    close = 100 + np.cumsum(rng.normal(0, 0.6, n))
    npz = {"close": close}
    for t in ("15m", "1h", "4h", "D"):
        npz[f"dc_low_{t}_prev"] = close + rng.normal(0.3, 1.0, n)
        npz[f"dc_high_{t}_prev"] = close + rng.normal(-0.3, 1.0, n)
    gains = rng.normal(0, 1.0, n)
    for is_long in (True, False):
        cfg = V.QuickConfig()
        cfg.KEY_LEVEL_CRASH_EXIT_ENABLED = True
        cfg.ABLATION_DISABLE_QUICK_EXIT = True
        P = L.prepare(npz, n, is_long, cfg, close, _safe)
        get = lambda k, d, _c=cfg: getattr(_c, k, d)
        for i in range(n):
            ind = {k: float(v[i]) for k, v in npz.items()}
            fire, reason, frac = K.decide(get, ind, is_long, float(close[i]), float(gains[i]))
            vr = _vec_leg(P, cfg, i, float(gains[i]), is_long)
            assert (vr is not None) == fire, (i, is_long, vr, fire)
            if fire:
                assert vr == reason
                assert L.reduce_fraction(cfg, float(gains[i])) == frac


def test_default_off_and_engine_wiring():
    import v12_quick_engine as V
    assert V.QuickConfig().KEY_LEVEL_CRASH_EXIT_ENABLED is False
    src = (ROOT / "v12_quick_engine.py").read_text()
    assert "_lec_hit is None and _lec_quick_ablated and bool(getattr(cfg, 'KEY_LEVEL_CRASH_EXIT_ENABLED', False))" in src
    assert "_lec_quick_ablated = bool(getattr(cfg, 'ABLATION_DISABLE_QUICK_EXIT', False))" in src


if __name__ == "__main__":
    for k, f in sorted(globals().items()):
        if k.startswith("test_"):
            f(); print("PASS", k)
