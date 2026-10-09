"""Tests for vec_decisions.wt_top_entry (USER 2026-10-08: WT-top + divergence entries).

Run: .venv/bin/python vec_decisions/test_wt_top_entry.py  (also collected by pytest)
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vec_decisions.wt_top_entry as w


def _mk_scalar(tf, cross=True, hi=100.0, hip=101.0, lo=99.0, lop=98.0, div=True, htf=True, bull=None):
    tf = w.TF_SUFFIX[tf]
    bear = not bull if bull is not None else True
    ind = {
        f"wt_cross_bear_{tf}": bool(cross and bear), f"wt_cross_bull_{tf}": bool(cross and not bear),
        f"high_{tf}": hi, f"high_{tf}_prev": hip, f"low_{tf}": lo, f"low_{tf}_prev": lop,
        f"div_reg_bear_wt_{tf}": bool(div), f"div_hid_bear_wt_{tf}": bool(div),
        f"div_reg_bull_wt_{tf}": bool(div), f"div_hid_bull_wt_{tf}": bool(div),
        "wt_cross_bear_4h": bool(htf), "wt_cross_bull_4h": bool(htf),
        "wt_cross_bear_D": bool(htf), "wt_cross_bull_D": bool(htf),
    }
    return ind


def _mk_vec(n, tf, cross_at, hi, hip, lo, lop, div_at, htf_at):
    tf = w.TF_SUFFIX[tf]
    z = lambda v: [v] * n
    npz = {
        f"wt_cross_bear_{tf}": z(0), f"wt_cross_bull_{tf}": z(0),
        f"high_{tf}": z(100.0), f"low_{tf}": z(99.0),
        f"div_reg_bear_wt_{tf}": z(0), f"div_hid_bear_wt_{tf}": z(0),
        f"div_reg_bull_wt_{tf}": z(0), f"div_hid_bull_wt_{tf}": z(0),
        "wt_cross_bear_4h": z(0), "wt_cross_bull_4h": z(0), "wt_cross_bear_D": z(0), "wt_cross_bull_D": z(0),
    }
    for i in cross_at:
        npz[f"wt_cross_bear_{tf}"][i] = 1
        npz[f"wt_cross_bull_{tf}"][i] = 1
    for i, v in hi.items():
        npz[f"high_{tf}"][i] = v
    for i, v in hip.items():
        npz[f"high_{tf}"][i - 1] = v
    for i, v in lo.items():
        npz[f"low_{tf}"][i] = v
    for i, v in lop.items():
        npz[f"low_{tf}"][i - 1] = v
    for i in div_at:
        for k in (f"div_reg_bear_wt_{tf}", f"div_hid_bear_wt_{tf}", f"div_reg_bull_wt_{tf}", f"div_hid_bull_wt_{tf}"):
            npz[k][i] = 1
    for i in htf_at:
        for k in ("wt_cross_bear_4h", "wt_cross_bull_4h", "wt_cross_bear_D", "wt_cross_bull_D"):
            npz[k][i] = 1
    return npz


def _spec(**kw):
    d = {"enabled": True, "tf": "15m", "struct_mode": "TOPS_ONLY", "div_mode": "OFF", "htf": []}
    d.update(kw)
    return d


def test_disabled_all_false():
    s = _spec(enabled=False)
    assert w.check_wt_top_entry(s, _mk_scalar("15m"), False) == (False, "OFF")
    m = w.build_wt_top_mask(_mk_vec(8, "15m", [4], {4: 100.0}, {4: 101.0}, {}, {}, [], []), 8, False, s, w._safe_get)
    assert m == [False] * 8


def test_short_tops_only_fires_and_guards():
    s = _spec()
    assert w.check_wt_top_entry(s, _mk_scalar("15m"), False)[0] is True
    assert w.check_wt_top_entry(s, _mk_scalar("15m", cross=False), False)[0] is False
    assert w.check_wt_top_entry(s, _mk_scalar("15m", hi=102.0, hip=101.0), False)[0] is False
    m = w.build_wt_top_mask(_mk_vec(8, "15m", [4], {4: 100.0}, {4: 101.0}, {}, {}, [], []), 8, False, s, w._safe_get)
    assert m == [False, False, False, False, True, False, False, False]


def test_long_mirror():
    s = _spec()
    assert w.check_wt_top_entry(s, _mk_scalar("15m", bull=True), True)[0] is True
    assert w.check_wt_top_entry(s, _mk_scalar("15m", bull=True, lo=97.0, lop=98.0), True)[0] is False
    assert w.check_wt_top_entry(s, _mk_scalar("15m", bull=False), True)[0] is False


def test_tops_and_lows_requires_extra_print():
    s = _spec(struct_mode="TOPS_AND_LOWS")
    assert w.check_wt_top_entry(s, _mk_scalar("15m", lo=97.0, lop=98.0), False)[0] is True
    assert w.check_wt_top_entry(s, _mk_scalar("15m", lo=98.5, lop=98.0), False)[0] is False
    assert w.check_wt_top_entry(s, _mk_scalar("15m", bull=True), True)[0] is False


def test_div_and_htf_layers():
    assert w.check_wt_top_entry(_spec(div_mode="REG"), _mk_scalar("15m", div=True), False)[0] is True
    assert w.check_wt_top_entry(_spec(div_mode="REG"), _mk_scalar("15m", div=False), False)[0] is False
    assert w.check_wt_top_entry(_spec(div_mode="HIDDEN"), _mk_scalar("15m", div=True), False)[0] is True
    assert w.check_wt_top_entry(_spec(htf=["4h"]), _mk_scalar("15m", htf=True), False)[0] is True
    assert w.check_wt_top_entry(_spec(htf=["4h"]), _mk_scalar("15m", htf=False), False)[0] is False
    assert w.check_wt_top_entry(_spec(htf=["D"]), _mk_scalar("15m", htf=True), False)[0] is True


def test_resolve_htf_vocab():
    from types import SimpleNamespace
    base = {"WT_TOP_ENTRY_ENABLED": True, "WT_TOP_ENTRY_TF": "15m", "WT_TOP_ENTRY_MODE": "TOPS_ONLY", "WT_TOP_ENTRY_DIV_MODE": "OFF"}
    assert w.resolve_wt_top_spec(SimpleNamespace(**dict(base, WT_TOP_ENTRY_HTF_CONFIRM_TF="BOTH")))["htf"] == ["4h", "D"]
    assert w.resolve_wt_top_spec(SimpleNamespace(**dict(base, WT_TOP_ENTRY_HTF_CONFIRM_TF="4h,D")))["htf"] == ["4h", "D"]
    assert w.resolve_wt_top_spec(SimpleNamespace(**dict(base, WT_TOP_ENTRY_HTF_CONFIRM_TF="OFF")))["htf"] == []
    assert w.resolve_wt_top_spec(SimpleNamespace(**dict(base, WT_TOP_ENTRY_HTF_CONFIRM_TF="W")))["htf"] == []
    assert w.resolve_wt_top_spec(SimpleNamespace(**dict(base, WT_TOP_ENTRY_TF="OFF")))["tf"] == "OFF"


def test_bad_inputs_never_fire():
    for s in (_spec(tf="OFF"), _spec(tf="3m"), _spec(struct_mode="X"), _spec(div_mode="X"), _spec(htf=["W"])):
        assert w.check_wt_top_entry(s, _mk_scalar("15m"), False)[0] is False
        assert w.check_wt_top_entry(s, _mk_scalar("15m"), True)[0] is False
    nan = _mk_scalar("15m")
    nan["high_15m"] = float("nan")
    assert w.check_wt_top_entry(_spec(), nan, False)[0] is False


def test_vec_scalar_parity_random():
    rnd = random.Random(20261008)
    n = 60
    tfs = ["15m", "1h", "4h"]
    modes = ["TOPS_ONLY", "TOPS_AND_LOWS"]
    divs = ["OFF", "REG", "HIDDEN", "REG_OR_HIDDEN"]
    htfs = [[], ["4h"], ["D"], ["4h", "D"]]
    bad = 0
    for is_long in (False, True):
        for tf in tfs:
            sfx = w.TF_SUFFIX[tf]
            cross = [rnd.random() < 0.3 for _ in range(n)]
            hi = [100.0 + rnd.uniform(-2, 2) for _ in range(n)]
            lo = [h - rnd.uniform(0.1, 1.0) for h in hi]
            dv = [rnd.random() < 0.2 for _ in range(n)]
            h4 = [rnd.random() < 0.3 for _ in range(n)]
            hd = [rnd.random() < 0.3 for _ in range(n)]
            if rnd.random() < 0.5:
                hi[rnd.randrange(n)] = float("nan")
            npz = {
                f"wt_cross_bear_{sfx}": [1 if c else 0 for c in cross],
                f"wt_cross_bull_{sfx}": [1 if c else 0 for c in cross],
                f"high_{sfx}": hi, f"low_{sfx}": lo,
                f"div_reg_bear_wt_{sfx}": [1 if x else 0 for x in dv],
                f"div_hid_bear_wt_{sfx}": [1 if x else 0 for x in dv],
                f"div_reg_bull_wt_{sfx}": [1 if x else 0 for x in dv],
                f"div_hid_bull_wt_{sfx}": [1 if x else 0 for x in dv],
                "wt_cross_bear_4h": [1 if x else 0 for x in h4], "wt_cross_bull_4h": [1 if x else 0 for x in h4],
                "wt_cross_bear_D": [1 if x else 0 for x in hd], "wt_cross_bull_D": [1 if x else 0 for x in hd],
            }
            for sm in modes:
                for dm in divs:
                    for hf in htfs:
                        s = _spec(tf=tf, struct_mode=sm, div_mode=dm, htf=hf)
                        m = w.build_wt_top_mask(npz, n, is_long, s, w._safe_get)
                        for i in range(n):
                            ind = {
                                f"wt_cross_bear_{sfx}": bool(npz[f"wt_cross_bear_{sfx}"][i]),
                                f"wt_cross_bull_{sfx}": bool(npz[f"wt_cross_bull_{sfx}"][i]),
                                f"high_{sfx}": hi[i], f"high_{sfx}_prev": hi[i - 1] if i else float("nan"),
                                f"low_{sfx}": lo[i], f"low_{sfx}_prev": lo[i - 1] if i else float("nan"),
                                f"div_reg_bear_wt_{sfx}": bool(dv[i]), f"div_hid_bear_wt_{sfx}": bool(dv[i]),
                                f"div_reg_bull_wt_{sfx}": bool(dv[i]), f"div_hid_bull_wt_{sfx}": bool(dv[i]),
                                "wt_cross_bear_4h": bool(h4[i]), "wt_cross_bull_4h": bool(h4[i]),
                                "wt_cross_bear_D": bool(hd[i]), "wt_cross_bull_D": bool(hd[i]),
                            }
                            if bool(m[i]) != w.check_wt_top_entry(s, ind, is_long)[0]:
                                bad += 1
    assert bad == 0, f"{bad} vec/scalar mismatches"


def test_config_registration():
    import config as cfg_mod
    import config_tradier as trb_mod
    import v12_quick_engine as V
    want = {"WT_TOP_ENTRY_ENABLED": False, "WT_TOP_ENTRY_TF": "OFF", "WT_TOP_ENTRY_MODE": "TOPS_ONLY", "WT_TOP_ENTRY_DIV_MODE": "OFF", "WT_TOP_ENTRY_HTF_CONFIRM_TF": "OFF"}
    for k, d in want.items():
        assert getattr(cfg_mod.Config, k, None) == d, k
        assert getattr(trb_mod.TradierConfig, k, None) == d, k
        assert getattr(V.QuickConfig, k, None) == d, k
        assert V.AUTO_WIRED_PARAMS.count(k) == 1, k
    assert V._ENTRY_FAMILY_ALIASES.get("WT_TOP") == ["B_WT_TOP"]
    assert V._ENTRY_FAMILY_MASTERS.get("WT_TOP") == ["WT_TOP_ENTRY_ENABLED"]


if __name__ == "__main__":
    test_config_registration()
    test_resolve_htf_vocab()
    test_disabled_all_false()
    test_short_tops_only_fires_and_guards()
    test_long_mirror()
    test_tops_and_lows_requires_extra_print()
    test_div_and_htf_layers()
    test_bad_inputs_never_fire()
    test_vec_scalar_parity_random()
    print("ALL WT_TOP_ENTRY TESTS PASSED")
