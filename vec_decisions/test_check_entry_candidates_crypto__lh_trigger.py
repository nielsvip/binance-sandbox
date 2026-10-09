"""test_check_entry_candidates_crypto__lh_trigger — scalar<->vec parity + AGLD-shape regression (INV-0001 v4)."""

import numpy as np

from vec_decisions import check_entry_candidates_crypto__lh_trigger as LH


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _cfg(**kw):
    d = {"ENTRY_LH_TRIGGER_ENABLED": True, "ENTRY_LH_TRIGGER_REGRESS_PCT": 5.0}
    d.update(kw)
    return Cfg(**d)


def _ind(h15, l15, dch, dcl, h1, h1p, l1, l1p):
    return {
        "high_15m": h15,
        "low_15m": l15,
        "dc_high_1h": dch,
        "dc_low_1h": dcl,
        "high_1h": h1,
        "high_1h_prev": h1p,
        "low_1h": l1,
        "low_1h_prev": l1p,
    }


def test_scalar_vec_parity_both_sides():
    rng = np.random.default_rng(11)
    n = 1500
    h15 = rng.uniform(0.5, 3.0, n)
    l15 = h15 - rng.uniform(0.01, 0.3, n)
    dch = np.maximum.accumulate(h15) * 1.001
    dcl = np.minimum.accumulate(l15) * 0.999
    h1 = rng.uniform(0.5, 3.0, n)
    h1p = rng.uniform(0.5, 3.0, n)
    l1 = h1 - rng.uniform(0.01, 0.3, n)
    l1p = h1p - rng.uniform(0.01, 0.3, n)
    for is_long in (True, False):
        for pct in (2.0, 5.0, 10.0):
            cfg = _cfg(ENTRY_LH_TRIGGER_REGRESS_PCT=pct)
            vec = LH.check_lh_trigger_entry_vec(
                cfg, h15, l15, dch, dcl, h1, h1p, l1, l1p, is_long
            )
            assert vec.dtype == bool and vec.shape == (n,)
            for i in range(0, n, 53):
                ind = _ind(h15[i], l15[i], dch[i], dcl[i], h1[i], h1p[i], l1[i], l1p[i])
                fire, reason = LH.check_lh_trigger_entry(cfg, ind, is_long)
                assert fire == bool(vec[i]), (is_long, pct, i)
                assert (reason == "") != fire


def test_default_off_inert_strict_edges_and_missing():
    ind = _ind(2.0, 1.0, 3.5, 0.5, 2.2, 2.4, 1.1, 1.0)
    assert LH.check_lh_trigger_entry(Cfg(), ind, False) == (False, "")
    assert LH.check_lh_trigger_entry(Cfg(), ind, True) == (False, "")
    cfg = _cfg()
    assert (
        LH.check_lh_trigger_entry(
            cfg, _ind(2.0, 1.0, 3.5, 0.5, 2.2, 2.4, 1.1, 1.0), False
        )[0]
        is True
    )
    assert (
        LH.check_lh_trigger_entry(
            cfg, _ind(2.0, 1.0, 3.5, 0.5, 2.4, 2.2, 1.1, 1.0), False
        )[0]
        is False
    )
    assert (
        LH.check_lh_trigger_entry(
            cfg, _ind(2.0, 1.0, 3.5, 0.5, 2.2, 2.4, 1.1, 1.0), True
        )[0]
        is True
    )
    assert (
        LH.check_lh_trigger_entry(
            cfg, _ind(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0), False
        )[0]
        is False
    )
    assert LH.check_lh_trigger_entry(cfg, {}, False)[0] is False
    assert LH.check_lh_trigger_entry(cfg, {}, True)[0] is False


def test_strength_weight_standalone():
    import v12_quick_engine as V12

    src = open(V12.__file__).read()
    assert '"LH_TRIGGER_ENTRY": 6' in src


def test_agld_shape_fires_short_after_top_not_at_top():
    cfg = _cfg()
    top = 0.2292
    assert (
        LH.check_lh_trigger_entry(
            cfg, _ind(top, 0.22, top, 0.19, top, 0.228, 0.22, 0.219), False
        )[0]
        is False
    )
    assert (
        LH.check_lh_trigger_entry(
            cfg, _ind(0.226, 0.22, top, 0.19, 0.227, 0.228, 0.22, 0.219), False
        )[0]
        is False
    )
    assert (
        LH.check_lh_trigger_entry(
            cfg, _ind(0.215, 0.21, top, 0.19, 0.216, 0.218, 0.21, 0.209), False
        )[0]
        is True
    )
    assert (
        LH.check_lh_trigger_entry(
            cfg, _ind(0.215, 0.21, top, 0.19, 0.216, 0.218, 0.21, 0.211), True
        )[0]
        is False
    )
