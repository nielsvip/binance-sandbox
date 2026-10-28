"""2026-10-28 WT_CROSS_EXIT twin: the LIVE block (ez_positions_quick.check_exit_candidates, executed verbatim from source) and the
shared vector twin (vec_decisions.check_exit_candidates_crypto__wt_cross_exit.wt_cross_exit_twin) take the same decision."""
import pathlib
import random
import textwrap
import types

import vec_decisions.check_exit_candidates_crypto__wt_cross_exit as W
import vec_decisions.live_native_exit_twins as LNX

ROOT = pathlib.Path(__file__).resolve().parent.parent
START = "# ═══ WT CROSS EXIT (2026-04-16)"
STOP = "# ═══ STDEV BREAKOUT FAILURE EXIT"


def _live_block():
    src = (ROOT / "ez_positions_quick.py").read_text()
    a = src.rindex("\n", 0, src.index(START)) + 1
    b = src.rindex("\n", 0, src.index(STOP, a)) + 1
    lines = src[a:b].splitlines()
    body = textwrap.dedent("\n".join(lines))
    return compile("def _live(config, position, current_gain, _pos_age_min, indicators, is_long, _in_grace_period, _cfgd):\n" + textwrap.indent("hard_exit_reason = None\nis_hedge = False\nsymbol = 'X'\nposition_side = 'LONG'\nposition_key = 'X_LONG'\n" + body + "\nreturn hard_exit_reason\n", "    "), "ez_positions_quick_wt_block", "exec")


class _Log:
    def __getattr__(self, _n):
        return lambda *a, **k: None


def _sff(v, d=0.0):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return float(d)
    return float(d) if x != x else x


def _mk_live():
    ns = {"safe_fetch_float": _sff, "logger": _Log()}
    exec(_live_block(), ns)
    def run(cfgd, gain, age_min, ind, is_long, in_grace):
        ns["_sw_get"] = lambda s, ps, k, d: cfgd.get(k, d)
        cfg = types.SimpleNamespace(**cfgd)
        pos = types.SimpleNamespace(gain=gain)
        return ns["_live"](cfg, pos, gain, age_min, ind, is_long, in_grace, cfgd)
    return run


def _fixtures(n=4000, seed=7):
    r = random.Random(seed)
    for _ in range(n):
        cfgd = {"WT_CROSS_EXIT_VEC_ENABLED": True, "WT_CROSS_EXIT_ENABLED": r.random() < 0.85, "WT_CROSS_EXIT_REQUIRE_15M_CONFIRM": r.random() < 0.6,
                "WT_CROSS_EXIT_MIN_AGE_MINUTES": r.choice([0.0, 0.5, 1.0, 2.0, 10.0]), "WT_CROSS_EXIT_APPLIES_TO_LOSERS": r.random() < 0.7,
                "WT_CROSS_EXIT_APPLIES_TO_WINNERS": r.random() < 0.7, "WT_CROSS_EXIT_3M_VETO_MAX_AGE": r.choice([0.0, 30.0, 60.0]), "REENTRY_GRACE_MINUTES": 30.0}
        ind = {k: (0.0 if r.random() < 0.08 else r.uniform(-80, 80)) for k in ("wt1_1h", "wt2_1h", "wt1_15m", "wt2_15m", "wt1_3m", "wt2_3m")}
        gain = r.choice([-3.0, -0.5, 0.0, 0.01, 0.015, 0.02, 0.5, 4.0])
        age_min = r.choice([0.5, 1.0, 2.5, 4.0, 20.0, 45.0, 200.0])
        reentered, augmented = r.random() < 0.3, r.random() < 0.3
        yield cfgd, ind, gain, age_min, r.random() < 0.5, reentered, augmented


def test_live_block_equals_twin():
    live = _mk_live()
    n_fire = 0
    for cfgd, ind, gain, age_min, is_long, reentered, augmented in _fixtures():
        grace = cfgd["REENTRY_GRACE_MINUTES"] if reentered else 3.0
        in_grace = (age_min < grace) and not augmented
        lv = live(cfgd, gain, age_min, ind, is_long, in_grace)
        c = lambda k, d=None: cfgd.get(k, d)
        fire, reason = W.wt_cross_exit_twin(c, lambda k, d=None: ind.get(k, d), {"gain": gain, "age_s": age_min * 60.0, "reentered": reentered, "augmented": augmented}, is_long)
        assert bool(lv) == fire, (cfgd, ind, gain, age_min, is_long, reentered, augmented, lv, reason)
        if fire:
            assert lv.split("_15m")[0] == reason.split("_15m")[0]
            n_fire += 1
    assert n_fire > 100


def test_default_off_not_listed_and_never_fires():
    cfgd = {"WT_CROSS_EXIT_ENABLED": True}
    c = lambda k, d=None: cfgd.get(k, d)
    assert W._wt_cross_exit_fires is not None
    assert W.wt_cross_exit_twin not in LNX.active_exits(c)
    assert W.wt_cross_exit_twin(c, lambda k, d=None: {"wt1_1h": -50, "wt2_1h": 10}.get(k, d), {"gain": 2.0, "age_s": 3600}, True) == (False, "")
    cfgd["WT_CROSS_EXIT_VEC_ENABLED"] = True
    assert W.wt_cross_exit_twin in LNX.active_exits(c)
    cfgd["WT_CROSS_EXIT_ENABLED"] = False
    assert W.wt_cross_exit_twin not in LNX.active_exits(c)


def test_config_defaults_inert():
    import config
    import config_tradier
    import v12_quick_engine as V
    assert config.Config.WT_CROSS_EXIT_VEC_ENABLED is False
    assert config_tradier.TradierConfig.WT_CROSS_EXIT_VEC_ENABLED is False
    assert V.QuickConfig().WT_CROSS_EXIT_VEC_ENABLED is False
