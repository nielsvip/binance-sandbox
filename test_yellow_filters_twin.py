"""Parity tests for vec_decisions/twin_yellow_filters.py (44 YELLOW switches).

Self-contained: imports the twin module + numpy + the three contract modules
that import cleanly without live daemons (tradier_reentry_wt_contract,
mtf_exit_timing, mtf_live_evaluator). Never imports v12_quick_engine /
ez_manage / tradier_manage (they need 235 absent vec modules / live daemons).

Each WIRED twin proves: (a) inert at defaults, (b) hand-computed boundary
truth tables transcribed from the live source lines, (c) vec-form ==
live-form agreement on seeded fuzz. NEEDS-OPERATOR twins prove the shipped
core + resolver behavior.
"""

import numpy as np
import pytest

import vec_decisions.twin_yellow_filters as T


def _get(d):
    return lambda k, default=None: d.get(k, default)


def _npz(n, **keys):
    return {k: np.asarray(v, dtype=np.float64) for k, v in keys.items()}


# ── registry ─────────────────────────────────────────────────────────────────

def test_registry_covers_all_44_with_verdicts():
    names = [l.split()[0] for l in open("/tmp/yellow_unwired.txt")]
    assert len(names) == 44
    assert set(T.REGISTRY) == set(names)
    wired = [k for k, v in T.REGISTRY.items() if v["verdict"] == "WIRED-BOTH-SPEC"]
    nod = [k for k, v in T.REGISTRY.items() if v["verdict"] == "NEEDS-OPERATOR-DECISION"]
    assert len(wired) == 10 and len(nod) == 29
    assert set(wired) == {"MOM3_FILTER_TF", "MOMENTUM_BREAKOUT_FILTER_TF", "DC_BREAK_FILTER_TF", "FAST_RISER_FILTER_TF", "KINDERGARTEN_FILTER_TF", "GR_FILTER_ALL_ENTRIES", "EMA_BLANKET_FILTER_FILTER_TF", "DC_BREACH_REDUCE_FILTER_TF", "MTF_DC_REJECT_FILTER_TF", "FROZEN_STOP_FILTER_TF"}
    assert {k for k, v in T.REGISTRY.items() if v["verdict"] == "WIRED-VEC-INLINE"} == {"FH_MOMENTUM_FILTER_TF"}
    assert {k for k, v in T.REGISTRY.items() if v["verdict"] == "WIRED-STOCKS-ONLY"} == {"MANDATORY_REENTRY_WT_FILTER_TF_MODE"}
    assert {k for k, v in T.REGISTRY.items() if v["verdict"] == "WIRED-VEC-MAP"} == {"DELTA_ENGINE_FILTER_TF", "DC_MOMENTUM_BOTA_SCORER_FILTER_TF", "CIRCUIT_SHARPE_GATES_FILTER_TF"}


def test_resolve_filter_tf_rules():
    assert T.resolve_filter_tf("OFF") is None
    assert T.resolve_filter_tf("off") is None
    assert T.resolve_filter_tf("") is None
    assert T.resolve_filter_tf(None) is None
    assert T.resolve_filter_tf(" 15m ") == "15m"
    assert T.resolve_filter_tf("15m", family_raw="1h", filter_default="15m") == "1h"
    assert T.resolve_filter_tf("1h", family_raw="15m", filter_default="15m") == "1h"
    assert T.resolve_filter_tf("15m", family_raw="OFF", filter_default="15m") is None
    assert T.resolve_filter_tf("WEIRD", family_raw="1h", filter_default="15m") == "WEIRD"


def test_safe_get_semantics():
    n = 4
    a = np.array([1.0, 2.0, 3.0, 4.0])
    assert np.all(T.safe_get({"k": a}, "k", n) == a)
    assert T.safe_get({"k": a}, "k", n).dtype == np.float64
    assert np.all(T.safe_get({}, "k", n) == 0.0)
    assert np.all(T.safe_get({"k": [1, 2, 3, 4]}, "k", n, 7.0) == 7.0)
    assert np.all(T.safe_get({"k": np.ones(3)}, "k", n, 5.0) == 5.0)
    assert T.safe_get_bool({"k": np.array([0, 1, 0, 2])}, "k", n).tolist() == [False, True, False, True]
    assert T.safe_get_bool({}, "k", n).tolist() == [False] * n


# ── MOM3 ─────────────────────────────────────────────────────────────────────

def test_mom3_inert_default():
    n = 6
    close = np.full(n, 100.0)
    for raw in ("OFF", "off", "", None):
        assert T.mom3_entry_gate({}, n, True, _get({"MOM3_FILTER_TF": raw}), close) is None
        assert T.mom3_entry_gate({}, n, False, _get({"MOM3_FILTER_TF": raw}), close) is None
    m = T.mom3_entry_gate({}, n, True, _get({"MOM3_FILTER_TF": "15m"}), close)
    assert m.tolist() == [True] * n


def test_mom3_boundaries():
    n = 1
    def gate(c3, px, is_long):
        npz = _npz(n, close_3bar_15m=[c3])
        return bool(T.mom3_entry_gate(npz, n, is_long, _get({"MOM3_FILTER_TF": "15m"}), np.array([px]))[0])
    assert gate(100.0, 98.5, True) is True
    assert gate(100.0, 99.5, True) is False
    assert gate(100.0, 99.0, True) is False
    assert gate(100.0, 101.5, False) is True
    assert gate(100.0, 100.5, False) is False
    assert gate(100.0, 101.0, False) is False
    assert gate(0.0, 98.0, True) is True
    assert gate(100.0, 0.0, True) is True
    assert T.mom3_blocks_live({"close_3bar_15m": 98.5}, True, 100.0, "OFF") is False
    assert T.mom3_blocks_live({"close_3bar_15m": 100.0}, True, 98.5, "15m") is False
    assert T.mom3_blocks_live({"close_3bar_15m": 100.0}, True, 99.5, "15m") is True


def test_mom3_vec_live_agreement():
    rng = np.random.default_rng(11)
    for is_long in (True, False):
        for tf in ("15m", "1h", "4h", "D"):
            n = 300
            px = rng.uniform(50, 150, n)
            c3 = rng.uniform(50, 150, n)
            c3[rng.random(n) < 0.05] = 0.0
            npz = _npz(n, **{"close_3bar_%s" % tf: c3})
            m = T.mom3_entry_gate(npz, n, is_long, _get({"MOM3_FILTER_TF": tf}), px)
            for i in range(n):
                live_block = T.mom3_blocks_live({"close_3bar_%s" % tf: float(c3[i])}, is_long, float(px[i]), tf)
                assert bool(m[i]) == (not live_block), (is_long, tf, i)


# ── MOMENTUM_BREAKOUT ────────────────────────────────────────────────────────

def test_momentum_inert_and_boundaries():
    n = 1
    assert T.momentum_breakout_gate({}, n, True, _get({"MOMENTUM_BREAKOUT_FILTER_TF": "OFF"}), np.ones(n)) is None
    def gate(c3, px, is_long):
        npz = _npz(n, close_3bar_1h=[c3])
        return bool(T.momentum_breakout_gate(npz, n, is_long, _get({"MOMENTUM_BREAKOUT_FILTER_TF": "1h"}), np.array([px]))[0])
    assert gate(100.0, 100.1, True) is True
    assert gate(100.0, 99.9, True) is False
    assert gate(100.0, 100.0, True) is False
    assert gate(100.0, 100.0, False) is False
    assert gate(100.0, 99.9, False) is True
    assert T.momentum_breakout_blocks_live({"close_3bar_1h": 100.0}, True, 100.1, "1h") is False
    assert T.momentum_breakout_blocks_live({"close_3bar_1h": 100.0}, True, 100.0, "1h") is True


def test_momentum_vec_live_agreement():
    rng = np.random.default_rng(12)
    for is_long in (True, False):
        n = 300
        px = rng.uniform(50, 150, n)
        c3 = rng.uniform(50, 150, n)
        npz = _npz(n, close_3bar_4h=c3)
        m = T.momentum_breakout_gate(npz, n, is_long, _get({"MOMENTUM_BREAKOUT_FILTER_TF": "4h"}), px)
        for i in range(n):
            assert bool(m[i]) == (not T.momentum_breakout_blocks_live({"close_3bar_4h": float(c3[i])}, is_long, float(px[i]), "4h"))


# ── DC_BREAK ─────────────────────────────────────────────────────────────────

def test_dc_break_inert_and_boundaries():
    n = 1
    assert T.dc_break_entry_gate({}, n, True, _get({"DC_BREAK_FILTER_TF": "OFF"}), np.ones(n)) is None
    def gate(lvl, px, is_long):
        npz = _npz(n, dc_high_15m_prev=[lvl], dc_low_15m_prev=[lvl])
        return bool(T.dc_break_entry_gate(npz, n, is_long, _get({"DC_BREAK_FILTER_TF": "15m"}), np.array([px]))[0])
    assert gate(100.0, 100.1, True) is True
    assert gate(100.0, 100.0, True) is False
    assert gate(100.0, 99.9, True) is False
    assert gate(100.0, 99.9, False) is True
    assert gate(100.0, 100.0, False) is False
    assert gate(0.0, 50.0, True) is True
    assert T.dc_break_blocks_live({"dc_high_15m_prev": 100.0}, True, 100.1, "15m") is False
    assert T.dc_break_blocks_live({"dc_high_15m_prev": 100.0}, True, 100.0, "15m") is True
    assert T.dc_break_blocks_live({}, True, 100.1, "15m") is False


def test_dc_break_vec_live_agreement():
    rng = np.random.default_rng(13)
    for is_long in (True, False):
        n = 300
        px = rng.uniform(50, 150, n)
        lv = rng.uniform(50, 150, n)
        lv[rng.random(n) < 0.05] = 0.0
        key = ("dc_high_1h_prev" if is_long else "dc_low_1h_prev")
        npz = _npz(n, **{key: lv})
        m = T.dc_break_entry_gate(npz, n, is_long, _get({"DC_BREAK_FILTER_TF": "1h"}), px)
        for i in range(n):
            assert bool(m[i]) == (not T.dc_break_blocks_live({key: float(lv[i])}, is_long, float(px[i]), "1h"))


# ── KINDERGARTEN ─────────────────────────────────────────────────────────────

def test_kg_scan_and_master():
    assert T.kg_scan_tfs("15m") == ("15m",)
    assert T.kg_scan_tfs("D") == ("D",)
    assert T.kg_scan_tfs("OFF") == ("D", "4h", "1h", "15m")
    assert T.kg_scan_tfs("") == ("D", "4h", "1h", "15m")
    assert T.kg_scan_tfs("5m") == ("D", "4h", "1h", "15m")
    n = 3
    assert T.kg_block_mask({}, n, True, _get({"KINDERGARTEN_EMA_GATE_ENABLED": False}), np.ones(n)) is None
    assert T.kg_blocks_live({}, True, 100.0, "15m", enabled=False) == (False, "KG_OFF")


def test_kg_live_boundaries():
    ind = {"close": 100.0, "ema_200_15m": 90.0, "sma_200_15m": 90.0, "ema_9_above_21_15m": 1}
    assert T.kg_blocks_live(ind, True, 100.0, "15m") == (False, "KG_LONG_OK_tf=15m")
    assert T.kg_blocks_live(dict(ind, ema_200_15m=110.0), True, 100.0, "15m")[0] is True
    assert T.kg_blocks_live(dict(ind, ema_9_above_21_15m=0), True, 100.0, "15m")[0] is True
    d = {k: v for k, v in ind.items() if k != "ema_9_above_21_15m"}
    assert T.kg_blocks_live(d, True, 100.0, "15m")[0] is False
    assert T.kg_blocks_live(dict(ind, ema_9_above_21_15m=0), False, 80.0, "15m") == (False, "KG_SHORT_OK_tf=15m")
    assert T.kg_blocks_live(ind, False, 80.0, "15m")[0] is True
    assert T.kg_blocks_live(ind, False, 95.0, "15m")[0] is True
    assert T.kg_blocks_live({"close": 100.0, "ema_21_15m": 50.0, "sma_50_15m": 60.0}, True, 100.0, "15m")[0] is True
    assert T.kg_blocks_live({"close": 100.0, "ema_21_15m": 70.0, "sma_50_15m": 60.0}, True, 100.0, "15m") == (False, "KG_NO_HTF_DATA_ALLOW")
    assert T.kg_blocks_live(dict(ind, close=0.0), True, 0.0, "15m") == (False, "KG_NO_PRICE_tf=15m")
    assert T.kg_blocks_live({"close": 100.0}, True, 100.0, "OFF") == (False, "KG_NO_HTF_DATA_ALLOW")


def test_kg_vec_live_agreement():
    rng = np.random.default_rng(14)
    for is_long in (True, False):
        for tf_raw in ("15m", "OFF"):
            n = 250
            px = rng.uniform(50, 150, n)
            ema = rng.uniform(50, 150, n)
            sma = rng.uniform(50, 150, n)
            ab = (rng.random(n) < 0.5).astype(np.float64)
            keys = {}
            for tf in (T.kg_scan_tfs(tf_raw)):
                keys["ema_200_%s" % tf] = ema
                keys["sma_200_%s" % tf] = sma
                keys["ema_9_above_21_%s" % tf] = ab
            npz = _npz(n, **keys)
            cfg = _get({"KINDERGARTEN_EMA_GATE_ENABLED": True, "KINDERGARTEN_FILTER_TF": tf_raw})
            m = T.kg_block_mask(npz, n, is_long, cfg, px)
            for i in range(n):
                ind = {"close": float(px[i])}
                for k, v in keys.items():
                    ind[k] = float(v[i])
                blocked, _ = T.kg_blocks_live(ind, is_long, float(px[i]), tf_raw, enabled=True)
                assert bool(m[i]) == blocked, (is_long, tf_raw, i)
    n = 60
    px = np.full(n, 100.0)
    npz = _npz(n, ema_21_15m=np.full(n, 50.0), sma_50_15m=np.full(n, 60.0))
    cfg = _get({"KINDERGARTEN_EMA_GATE_ENABLED": True, "KINDERGARTEN_FILTER_TF": "15m"})
    assert T.kg_block_mask(npz, n, True, cfg, px).tolist() == [True] * n
    assert T.kg_block_mask(npz, n, False, cfg, px).tolist() == [False] * n


# ── FAST_RISER ───────────────────────────────────────────────────────────────

def test_fast_riser_master_chain_and_keys():
    n = 2
    px = np.array([100.0, 100.0])
    base = {"ENABLE_FAST_RISER_REDUCE": True, "FAST_RISER_DOUBLE_ENABLED": True, "FAST_RISER_FILTER_TF": "OFF"}
    npz = _npz(n, close_3m_prev=[99.0, 99.0], low_3m=[98.0, 98.0], low_3m_prev=[99.0, 99.0], ha_3m=[1.0, 1.0])
    assert T.fast_riser_signal(npz, n, True, _get(base), px) is not None
    assert T.fast_riser_signal(npz, n, True, _get(dict(base, ENABLE_FAST_RISER_REDUCE=False)), px) is None
    assert T.fast_riser_signal(npz, n, True, _get(dict(base, FAST_RISER_DOUBLE_ENABLED=False)), px) is None
    assert T.fast_riser_signal(npz, n, True, _get(dict(base, ABLATION_DISABLE_FAST_RISER=True)), px) is None
    npz2 = _npz(n, close_1h_prev=[99.0, 99.0], low_1h=[98.0, 98.0], low_1h_prev=[99.0, 99.0], ha_1h=[1.0, 1.0])
    assert T.fast_riser_signal(npz2, n, True, _get(dict(base, FAST_RISER_FILTER_TF="1h")), px).tolist() == [True, True]
    assert T.fast_riser_signal({}, n, True, _get(base), px).tolist() == [False, False]


def test_fast_riser_boundaries():
    n = 1
    def fire(cp, lo, lp, ha, px, is_long, tf="OFF", ck="close_3m_prev", lk="low_3m", lpk="low_3m_prev", hk="ha_3m"):
        npz = _npz(n, **{ck: [cp], lk: [lo], lpk: [lp], hk: [ha]})
        cfg = _get({"ENABLE_FAST_RISER_REDUCE": True, "FAST_RISER_DOUBLE_ENABLED": True, "FAST_RISER_FILTER_TF": tf})
        return bool(T.fast_riser_signal(npz, n, is_long, cfg, np.array([px]))[0])
    assert fire(100.0, 99.0, 100.0, 1.0, 100.2, True) is True
    assert fire(100.0, 99.0, 100.0, 1.0, 100.19, True) is False
    assert fire(100.0, 100.0, 100.0, 1.0, 100.2, True) is False
    assert fire(100.0, 99.0, 100.0, 0.0, 100.2, True) is False
    assert fire(100.0, 101.0, 100.0, -1.0, 99.8, False) is True
    g = lambda k, d=None: {"close_3m_prev": 100.0, "low_3m": 99.0, "low_3m_prev": 100.0, "ha_3m": "green"}.get(k, d)
    assert T.fast_riser_jump_live(g, True, 100.2, "OFF") is True
    g2 = lambda k, d=None: {"close_3m_prev": 100.0, "low_3m": 99.0, "low_3m_prev": 100.0, "ha_3m": "red"}.get(k, d)
    assert T.fast_riser_jump_live(g2, True, 100.2, "OFF") is False


def test_fast_riser_vec_live_agreement():
    rng = np.random.default_rng(15)
    for is_long in (True, False):
        n = 300
        px = rng.uniform(50, 150, n)
        cp = rng.uniform(50, 150, n)
        lo = rng.uniform(40, 140, n)
        lp = rng.uniform(40, 140, n)
        ha = rng.choice([-1.0, 0.0, 1.0], n)
        npz = _npz(n, close_15m_prev=cp, low_15m=lo, low_15m_prev=lp, ha_15m=ha)
        cfg = _get({"ENABLE_FAST_RISER_REDUCE": True, "FAST_RISER_DOUBLE_ENABLED": True, "FAST_RISER_FILTER_TF": "15m"})
        m = T.fast_riser_signal(npz, n, is_long, cfg, px)
        for i in range(n):
            d = {"close_15m_prev": float(cp[i]), "low_15m": float(lo[i]), "low_15m_prev": float(lp[i]), "ha_15m": float(ha[i])}
            assert bool(m[i]) == T.fast_riser_jump_live(lambda k, dd=None, _d=d: _d.get(k, dd), is_long, float(px[i]), "15m"), (is_long, i)


# ── EMA_BLANKET ──────────────────────────────────────────────────────────────

def test_blanket_inert_and_boundaries():
    n = 1
    cfg_off = _get({"EMA_BLANKET_FILTER_ENABLED": False, "EMA_BLANKET_FILTER_FILTER_TF": "1h"})
    assert T.ema_blanket_pass_vec({}, n, True, cfg_off) is None
    cfg_legacy = _get({"EMA_BLANKET_FILTER_ENABLED": True, "EMA_BLANKET_FILTER_FILTER_TF": "OFF"})
    assert T.ema_blanket_pass_vec({}, n, True, cfg_legacy) is None
    assert T.ema_blanket_blocks_live(lambda k, d=None: 1, True, "OFF", 3) is False
    cfg = _get({"EMA_BLANKET_FILTER_ENABLED": True, "EMA_BLANKET_FILTER_FILTER_TF": "1h", "EMA_BLANKET_FILTER_MIN_TFS": 1})
    assert bool(T.ema_blanket_pass_vec(_npz(n, ema_9_above_21_1h=[1.0]), n, True, cfg)[0]) is True
    assert bool(T.ema_blanket_pass_vec(_npz(n, ema_9_above_21_1h=[0.0]), n, True, cfg)[0]) is False
    assert bool(T.ema_blanket_pass_vec(_npz(n, ema_9_above_21_1h=[0.0]), n, False, cfg)[0]) is True
    cfg3 = _get({"EMA_BLANKET_FILTER_ENABLED": True, "EMA_BLANKET_FILTER_FILTER_TF": "1h", "EMA_BLANKET_FILTER_MIN_TFS": 3})
    assert bool(T.ema_blanket_pass_vec(_npz(n, ema_9_above_21_1h=[1.0]), n, True, cfg3)[0]) is False
    assert bool(T.ema_blanket_pass_vec({}, n, True, cfg3)[0]) is True
    assert T.ema_blanket_blocks_live(lambda k, d=None: 1, True, "1h", 1) is False
    assert T.ema_blanket_blocks_live(lambda k, d=None: 0, True, "1h", 1) is True
    assert T.ema_blanket_blocks_live(lambda k, d=None: None, True, "1h", 1) is False


def test_blanket_vec_live_agreement():
    rng = np.random.default_rng(16)
    for is_long in (True, False):
        n = 200
        ab = rng.choice([0.0, 1.0], n)
        cfg = _get({"EMA_BLANKET_FILTER_ENABLED": True, "EMA_BLANKET_FILTER_FILTER_TF": "4h", "EMA_BLANKET_FILTER_MIN_TFS": 1})
        m = T.ema_blanket_pass_vec(_npz(n, ema_9_above_21_4h=ab), n, is_long, cfg)
        for i in range(n):
            live_block = T.ema_blanket_blocks_live(lambda k, d=None, _v=float(ab[i]): _v, is_long, "4h", 1)
            assert bool(m[i]) == (not live_block)


# ── GR_FILTER_ALL_ENTRIES ────────────────────────────────────────────────────

def test_gr_relax_boundaries():
    g = lambda k, d=None: {"MTF_GR_MIN_IND": 7}.get(k, d)
    ind = lambda k, d=None: {"sma_200_15m": 100.0}.get(k, d)
    assert T.gr_min_ind_effective(ind, "LONG", g, 105.0) == 7
    assert T.gr_min_ind_effective(ind, "LONG", g, 105.01) == 4
    assert T.gr_min_ind_effective(ind, "SHORT", g, 95.0) == 7
    assert T.gr_min_ind_effective(ind, "SHORT", g, 94.99) == 4
    assert T.gr_min_ind_effective(lambda k, d=None: 0, "LONG", g, 200.0) == 7
    g2 = lambda k, d=None: {"MTF_GR_MIN_IND": 2}.get(k, d)
    assert T.gr_min_ind_effective(ind, "LONG", g2, 200.0) == 2


def test_gr_blocks_passthrough_vs_real_gr_filter():
    import mtf_live_evaluator as mle
    from types import SimpleNamespace
    cfg = {"GR_FILTER_ALL_ENTRIES": True, "MTF_GR_MIN_IND": 7}
    get = _get(cfg)
    assert T.gr_all_entries_blocks({"sma_200_15m": 1.0}, "LONG", _get({"GR_FILTER_ALL_ENTRIES": False}), 100.0, mle.gr_filter_pass, SimpleNamespace()) is False
    assert T.gr_all_entries_blocks({}, "LONG", get, 100.0, mle.gr_filter_pass, SimpleNamespace()) is False
    ind = {}
    for tf in ("5m", "15m", "1h", "4h", "D", "W"):
        ind.update({"wt1_%s" % tf: 50.0, "wt2_%s" % tf: 0.0, "rsi_%s" % tf: 60.0, "mfi_%s" % tf: 60.0, "dc_pct_%s" % tf: 0.5, "bb_pct_b_%s" % tf: 0.5, "relative_volume_%s" % tf: 1.5, "k_%s" % tf: 50.0, "d_%s" % tf: 40.0, "adx_%s" % tf: 25.0, "macd_hist_%s" % tf: 1.0, "ha_color_%s" % tf: 1.0})
    ind["current_price"] = 100.0
    ns = SimpleNamespace(MTF_GR_FILTER_ENABLED=True, MTF_GR_MIN_TFS=3, MTF_GR_MIN_IND=7, MTF_GR_INVERT_DC_BB=False)
    assert mle.gr_filter_pass(ind, "LONG", "tradier", ns, min_ind=7) is True
    assert T.gr_all_entries_blocks(dict(ind), "LONG", get, 100.0, mle.gr_filter_pass, ns) is False
    bad = dict(ind)
    for tf in ("5m", "15m", "1h", "4h", "D", "W"):
        bad["wt1_%s" % tf] = -50.0
        bad["rsi_%s" % tf] = 40.0
        bad["mfi_%s" % tf] = 40.0
        bad["macd_hist_%s" % tf] = -1.0
        bad["ha_color_%s" % tf] = -1.0
    assert mle.gr_filter_pass(bad, "LONG", "tradier", ns, min_ind=7) is False
    assert T.gr_all_entries_blocks(bad, "LONG", get, 100.0, mle.gr_filter_pass, ns) is True


# ── DC_BREACH ────────────────────────────────────────────────────────────────

def test_dc_breach_inert_and_boundaries():
    n = 1
    assert T.dc_breach_tf("OFF") == "15m"
    assert T.dc_breach_tf("1h") == "1h"
    assert T.dc_breach_reduce_mask({}, n, True, _get({"EXIT_DC_BREACH_REDUCE_ENABLED": False}), np.ones(n)) is None
    def mask(lvl, px, is_long):
        npz = _npz(n, dc_low_15m=[lvl], dc_high_15m=[lvl])
        return bool(T.dc_breach_reduce_mask(npz, n, is_long, _get({"EXIT_DC_BREACH_REDUCE_ENABLED": True, "DC_BREACH_REDUCE_FILTER_TF": "OFF"}), np.array([px]))[0])
    assert mask(100.0, 99.9, True) is True
    assert mask(100.0, 100.0, True) is False
    assert mask(0.0, 50.0, True) is False
    assert mask(100.0, 100.1, False) is True
    assert mask(100.0, 100.0, False) is False
    g = lambda k, d=None: {"dc_low_1h": 100.0}.get(k, d)
    assert T.dc_breach_fires_live(g, True, 99.9, "1h") is True
    assert T.dc_breach_fires_live(g, True, 100.0, "1h") is False


def test_dc_breach_vec_live_agreement():
    rng = np.random.default_rng(17)
    for is_long in (True, False):
        n = 300
        px = rng.uniform(50, 150, n)
        lv = rng.uniform(50, 150, n)
        lv[rng.random(n) < 0.05] = 0.0
        key = "dc_low_1h" if is_long else "dc_high_1h"
        npz = _npz(n, **{key: lv})
        cfg = _get({"EXIT_DC_BREACH_REDUCE_ENABLED": True, "DC_BREACH_REDUCE_FILTER_TF": "1h"})
        m = T.dc_breach_reduce_mask(npz, n, is_long, cfg, px)
        for i in range(n):
            assert bool(m[i]) == T.dc_breach_fires_live(lambda k, dd=None, _v=float(lv[i]): _v, is_long, float(px[i]), "1h")


# ── MTF_DC_REJECT ────────────────────────────────────────────────────────────

def test_mtf_dc_resolver_and_keys():
    assert T.mtf_dc_reject_tf("15m", "1h") == "1h"
    assert T.mtf_dc_reject_tf("4h", "1h") == "4h"
    assert T.mtf_dc_reject_tf("OFF", "1h") is None
    assert T.mtf_dc_band_key("1h", True) == "dc_high_1h"
    assert T.mtf_dc_band_key("1h", False) == "dc_low_1h"
    assert T.mtf_dc_band_key("1h", True, True) == "dc_high4_1h"
    assert T.mtf_dc_band_key("1h", False, True) == "dc_low4_1h"


def test_dc_reject_crypto_latch_trace():
    band = [100.0] * 6
    assert T.dc_reject_crypto_walk(band, [99.0, 101.0, 101.0, 99.5, 99.0, 101.0], True).tolist() == [False, False, False, True, True, False]
    assert T.dc_reject_crypto_walk(band, [101.0, 99.0, 99.0, 100.5, 101.0, 99.0], False).tolist() == [False, False, False, True, True, False]
    assert T.dc_reject_crypto_walk([0.0] * 3, [101.0] * 3, True).tolist() == [False] * 3
    assert T.dc_reject_crypto_walk(band, [100.0] * 3, True).tolist() == [False] * 3
    o, f = T.dc_reject_crypto_step(False, 101.0, 100.0, True)
    assert (o, f) == (True, False)
    o, f = T.dc_reject_crypto_step(True, 99.0, 100.0, True)
    assert (o, f) == (True, True)


def test_dc_reject_stocks_step_vs_contract():
    import mtf_exit_timing as met
    assert T._tf_seconds("15m") == met.timeframe_seconds("15m") == 900
    assert T._tf_seconds("1h") == met.timeframe_seconds("1h") == 3600
    assert T._tf_seconds("4h") == met.timeframe_seconds("4h") == 14400
    assert T._tf_seconds("D") == met.timeframe_seconds("D") == 86400
    assert T._tf_seconds("5m") == met.timeframe_seconds("5m") == 300
    assert T._tf_seconds("bogus") == met.timeframe_seconds("bogus") == 300
    rng = np.random.default_rng(18)
    for is_long in (True, False):
        for tf in ("15m", "1h", "4h"):
            outside = 0.0
            my_out = 0.0
            for i in range(400):
                now = 1700000000.0 + i * 900
                edge = 100.0
                px = float(rng.uniform(98, 102))
                outside, exp_fire = met.dc_reject_step(outside, now_ts=now, price=px, band=edge, lookback_bars=5, timeframe=tf, is_long=is_long)
                my_out, my_fire = T.dc_reject_stocks_step(my_out, now, px, edge, 5, tf, is_long)
                assert (my_out, my_fire) == (outside, exp_fire), (is_long, tf, i)
    ts = [1700000000.0 + i * 900 for i in range(6)]
    assert T.dc_reject_stocks_walk(ts, [99.0, 101.0, 99.0, 101.0, 99.0, 99.0], [100.0] * 6, 5, "15m", True).tolist() == [False, False, True, False, True, False]


# ── FROZEN_STOP ──────────────────────────────────────────────────────────────

def test_frozen_resolver_keys_and_core():
    assert T.frozen_stop_tf("15m", "1h") == "1h"
    assert T.frozen_stop_tf("4h", "1h") == "4h"
    assert T.frozen_stop_tf("OFF", "1h") is None
    assert T.frozen_bb_key("1h", "lower", True) == "bb_lower_1h"
    assert T.frozen_bb_key("1h", "lower", False) == "bb_upper_1h"
    assert T.frozen_bb_key("1h", "upper", True) == "bb_lower_1h"
    assert T.frozen_bb_key("1h", "middle", True) == "bb_middle_1h"
    assert T.frozen_stop_fires(100.0, 99.0, -1.0, True) is True
    assert T.frozen_stop_fires(100.0, 100.0, -1.0, True) is False
    assert T.frozen_stop_fires(100.0, 99.0, 0.0, True) is False
    assert T.frozen_stop_fires(0.0, 50.0, -5.0, True) is False
    assert T.frozen_stop_fires(None, 50.0, -5.0, True) is False
    assert T.frozen_stop_fires(100.0, 101.0, -1.0, False) is True


def test_frozen_mask_from_entry():
    n = 5
    npz = _npz(n, bb_lower_1h=[100.0] * n)
    px = np.array([101.0, 99.0, 101.0, 98.0, 102.0])
    assert T.frozen_stop_breach_mask(npz, n, True, 1, "1h", "lower", px).tolist() == [False, True, False, True, False]
    assert T.frozen_stop_breach_mask(npz, n, True, 9, "1h", "lower", px).tolist() == [False] * n
    assert T.frozen_stop_breach_mask({}, n, True, 0, "1h", "lower", px).tolist() == [False] * n
    npz2 = _npz(n, bb_upper_1h=[100.0] * n)
    assert T.frozen_stop_breach_mask(npz2, n, False, 0, "1h", "lower", px).tolist() == [True, False, True, False, True]


# ── MANDATORY vec port vs the venue-neutral contract ─────────────────────────

def test_mandatory_mode_and_master():
    assert T.mandatory_mode_tfs("15m_only") == ("15m",)
    assert T.mandatory_mode_tfs("5m_only") == ("5m",)
    assert T.mandatory_mode_tfs("5m_or_15m") == ("5m", "15m")
    assert T.mandatory_mode_tfs(" 15M_ONLY ") == ("15m",)
    assert T.mandatory_mode_tfs(None) == ("5m", "15m")
    assert T.mandatory_wt_vec({}, 3, True, _get({"MANDATORY_REENTRY_WT_FILTER_ENABLED": False})) is None


def test_mandatory_vec_vs_contract_agreement():
    from tradier_reentry_wt_contract import mandatory_reentry_wt_gate
    rng = np.random.default_rng(19)
    for is_long in (True, False):
        for mode in ("15m_only", "5m_or_15m"):
            tfs = T.mandatory_mode_tfs(mode)
            n = 120
            arr = {}
            for tf in ("5m", "15m"):
                arr["wt1_%s" % tf] = rng.uniform(-60, 60, n)
                arr["wt2_%s" % tf] = rng.uniform(-60, 60, n)
                arr["wt_velocity_%s" % tf] = rng.uniform(-8, 8, n)
                arr["wt_cross_bull_%s" % tf] = (rng.random(n) < 0.1).astype(np.float64)
                arr["wt_cross_bear_%s" % tf] = (rng.random(n) < 0.1).astype(np.float64)
            npz = _npz(n, **arr)
            cfg = _get({"MANDATORY_REENTRY_WT_FILTER_ENABLED": True, "MANDATORY_REENTRY_WT_FILTER_TF_MODE": mode, "MANDATORY_REENTRY_WT_FILTER_MIN_TFS": 1, "MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP": True, "MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO": 0.90, "MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY": 0.0})
            m = T.mandatory_wt_vec(npz, n, is_long, cfg)
            for i in range(1, n):
                ind = {}
                for tf in ("5m", "15m"):
                    ind["wt1_%s" % tf] = float(arr["wt1_%s" % tf][i])
                    ind["wt2_%s" % tf] = float(arr["wt2_%s" % tf][i])
                    ind["wt1_%s_prev" % tf] = float(arr["wt1_%s" % tf][i - 1])
                    ind["wt2_%s_prev" % tf] = float(arr["wt2_%s" % tf][i - 1])
                    ind["wt_velocity_%s" % tf] = float(arr["wt_velocity_%s" % tf][i])
                    ind["wt_velocity_%s_prev" % tf] = float(arr["wt_velocity_%s" % tf][i - 1])
                    ind["wt_cross_bull_%s" % tf] = bool(arr["wt_cross_bull_%s" % tf][i])
                    ind["wt_cross_bear_%s" % tf] = bool(arr["wt_cross_bear_%s" % tf][i])
                ok, _ = mandatory_reentry_wt_gate(ind, is_long, enabled=True, tf_mode=mode, min_tfs=1, require_flip=True, velocity_ratio=0.90, min_velocity=0.0)
                assert bool(m[i]) == ok, (is_long, mode, i)


# ── WT_BOUNCE claim ──────────────────────────────────────────────────────────

def _wt_base_cfg(**ov):
    d = {"WT_15M_BOUNCE_OPEN_ENABLED": True, "WT_15M_BOUNCE_BB_MIN": 0.05, "WT_15M_BOUNCE_BB_MAX": 0.95, "WT_15M_BOUNCE_REQUIRE_BOTH_HTF": False, "WT_15M_BOUNCE_FILTER_HL_ENABLED": False, "WT_15M_BOUNCE_FILTER_HH_ENABLED": False, "WT_15M_BOUNCE_LOW_1H_GT_PREV": False, "WT_15M_BOUNCE_HIGH_1H_GT_PREV": False, "WT_15M_BOUNCE_FILTER_MODE": "AND", "WT_15M_BOUNCE_VOLUME_FILTER_ENABLED": False, "WT_15M_BOUNCE_REL_VOL_GT_1": False, "WT_15M_BOUNCE_VOLUME_MODE": "relvol", "WT_15M_BOUNCE_VOLUME_THRESHOLD": 1.0}
    d.update(ov)
    return _get(d)


def _wt_base_ind(**ov):
    d = {"wt1_15m": 10.0, "wt2_15m": 0.0, "wt1_15m_prev": -5.0, "wt2_15m_prev": 0.0, "bb_pct_b_15m": 0.5, "wt_cross_rising_1h": True, "wt_cross_rising_4h": False, "dc_low_1h": 100.0, "dc_low_1h_prev": 99.0, "dc_high_1h": 110.0, "dc_high_1h_prev": 109.0, "relative_volume_1h": 1.5, "relative_volume_15m": 0.5}
    d.update(ov)
    return d


def test_wt_claim_master_and_triggers():
    assert T.wt_bounce_claim(lambda k, d=None: None, True, _get({"WT_15M_BOUNCE_OPEN_ENABLED": False})) == (False, "WT15_OFF")
    ind = _wt_base_ind()
    g = lambda k, dd=None: ind.get(k, dd)
    assert T.wt_bounce_claim(g, True, _wt_base_cfg())[0] is True
    assert T.wt_bounce_claim(g, False, _wt_base_cfg())[0] is False
    ind2 = _wt_base_ind(wt1_15m_prev=15.0)
    assert T.wt_bounce_claim(lambda k, dd=None: ind2.get(k, dd), True, _wt_base_cfg())[0] is False
    ind3 = _wt_base_ind(bb_pct_b_15m=0.05)
    assert T.wt_bounce_claim(lambda k, dd=None: ind3.get(k, dd), True, _wt_base_cfg())[0] is True
    ind4 = _wt_base_ind(bb_pct_b_15m=0.049)
    assert T.wt_bounce_claim(lambda k, dd=None: ind4.get(k, dd), True, _wt_base_cfg())[0] is False
    assert T.wt_bounce_claim(g, True, _wt_base_cfg(WT_15M_BOUNCE_REQUIRE_BOTH_HTF=True))[0] is False
    assert T.wt_bounce_claim(g, True, _wt_base_cfg(WT_15M_BOUNCE_LOW_1H_GT_PREV=True))[0] is True
    ind5 = _wt_base_ind(dc_low_1h=99.0, dc_low_1h_prev=99.0)
    assert T.wt_bounce_claim(lambda k, dd=None: ind5.get(k, dd), True, _wt_base_cfg(WT_15M_BOUNCE_LOW_1H_GT_PREV=True))[0] is False
    ind6 = _wt_base_ind(wt1_15m_prev=15.0)
    assert T.wt_bounce_claim(lambda k, dd=None: ind6.get(k, dd), True, _wt_base_cfg(WT_15M_BOUNCE_LOW_1H_GT_PREV=True))[0] is True
    assert T.wt_bounce_claim(g, True, _wt_base_cfg(WT_15M_BOUNCE_REL_VOL_GT_1=True))[0] is True
    ind7 = _wt_base_ind(relative_volume_1h=1.0)
    assert T.wt_bounce_claim(lambda k, dd=None: ind7.get(k, dd), True, _wt_base_cfg(WT_15M_BOUNCE_REL_VOL_GT_1=True))[0] is False
    ind8 = dict(_wt_base_ind())
    del ind8["relative_volume_1h"]
    assert T.wt_bounce_claim(lambda k, dd=None: ind8.get(k, dd), True, _wt_base_cfg(WT_15M_BOUNCE_REL_VOL_GT_1=True))[0] is False
    ind9 = _wt_base_ind(volume_1h=200.0, volume_sma_1h=100.0)
    assert T.wt_bounce_claim(lambda k, dd=None: ind9.get(k, dd), True, _wt_base_cfg(WT_15M_BOUNCE_REL_VOL_GT_1=True, WT_15M_BOUNCE_VOLUME_MODE="sma"))[0] is True
    ind10 = _wt_base_ind(volume_1h=50.0, volume_sma_1h=0.0)
    assert T.wt_bounce_claim(lambda k, dd=None: ind10.get(k, dd), True, _wt_base_cfg(WT_15M_BOUNCE_REL_VOL_GT_1=True, WT_15M_BOUNCE_VOLUME_MODE="sma"))[0] is True


def test_wt_claim_vs_independent_core_oracle():
    """Second transcription of tr:1305-1319 (cross+bb+htf core) must agree."""
    def oracle(ind, is_long, bb_min, bb_max, req_both):
        w1, w2 = float(ind["wt1_15m"]), float(ind["wt2_15m"])
        w1p, w2p = float(ind.get("wt1_15m_prev", w1)), float(ind.get("wt2_15m_prev", w2))
        cross = ((w1p <= w2p) and (w1 > w2)) if is_long else ((w1p >= w2p) and (w1 < w2))
        bb = float(ind.get("bb_pct_b_15m", 0.5) or 0.5)
        r1, r4 = bool(ind.get("wt_cross_rising_1h", False)), bool(ind.get("wt_cross_rising_4h", False))
        h1, h4 = (r1, r4) if is_long else ((not r1), (not r4))
        htf = (h1 and h4) if req_both else (h1 or h4)
        return bool(cross and (bb_min <= bb <= bb_max) and htf)
    rng = np.random.default_rng(20)
    for is_long in (True, False):
        for req_both in (False, True):
            for _ in range(300):
                ind = {"wt1_15m": float(rng.uniform(-60, 60)), "wt2_15m": float(rng.uniform(-60, 60)), "wt1_15m_prev": float(rng.uniform(-60, 60)), "wt2_15m_prev": float(rng.uniform(-60, 60)), "bb_pct_b_15m": float(rng.uniform(-0.2, 1.2)), "wt_cross_rising_1h": bool(rng.random() < 0.5), "wt_cross_rising_4h": bool(rng.random() < 0.5)}
                g = lambda k, dd=None, _d=ind: _d.get(k, dd)
                mine, _ = T.wt_bounce_claim(g, is_long, _wt_base_cfg(WT_15M_BOUNCE_REQUIRE_BOTH_HTF=req_both))
                assert mine == oracle(ind, is_long, 0.05, 0.95, req_both)


# ── PENDING cores ────────────────────────────────────────────────────────────

def test_newborn_pending_core():
    assert T.newborn_vel_against(-0.5, True) is True
    assert T.newborn_vel_against(0.0, True) is False
    assert T.newborn_vel_against(0.5, False) is True
    assert T.newborn_vel_against(0.0, False) is False
    assert T.newborn_vel_against(None, True) is False
    npz = _npz(3, wt_velocity_15m=[-1.0, 0.0, 2.0])
    assert T.newborn_vel_vec(npz, 3, True, "15m").tolist() == [True, False, False]
    assert T.newborn_vel_vec(npz, 3, False, "15m").tolist() == [False, False, True]


def test_r2_pending_resolver():
    assert T.r2_eff_tfs("15m", ("15m",)) == ("15m",)
    assert T.r2_eff_tfs("OFF", ("1h",)) == ("1h",)
    assert T.r2_eff_tfs("4h", ("15m",)) == ("4h",)
    assert T.r2_eff_tfs("", ()) == ("15m",)


def test_mtf_atr_resolver_and_kg_stocks_restrict():
    assert T.mtf_atr_trail_tf("15m", "1h") == "1h"
    assert T.mtf_atr_trail_tf("4h", "1h") == "4h"
    assert T.mtf_atr_trail_tf("OFF", "1h") is None
    assert T.kg_stocks_tfs_restrict(["1h"], "15m") == ["1h"]
    assert T.kg_stocks_tfs_restrict(["1h"], "OFF") == ["1h"]
    assert T.kg_stocks_tfs_restrict(["1h", "D"], "4h") == ["4h"]
    assert T.kg_stocks_tfs_restrict(["1h", "D"], "D") == ["D"]
    assert T.kg_stocks_tfs_restrict(["1h"], "ALL") == ["1h"]
    assert T.kg_stocks_tfs_restrict(["1h"], "") == ["1h"]


# ── batch appliers ───────────────────────────────────────────────────────────

def test_batch_appliers_default_inert():
    n = 8
    entry = np.ones(n, dtype=bool)
    close = np.full(n, 100.0)
    out = T.apply_yellow_entry_masks({}, n, True, _get({}), close, entry.copy(), [])
    assert out.tolist() == [True] * n
    blocked, why = T.yellow_live_veto({"close": 100.0}, True, 100.0, _get({}))
    assert (blocked, why) == (False, "YELLOW_OK")
    cfg = _get({"MOM3_FILTER_TF": "15m"})
    npz = _npz(n, close_3bar_15m=np.full(n, 100.0))
    out2 = T.apply_yellow_entry_masks(npz, n, True, cfg, np.full(n, 98.0), entry.copy(), [])
    assert out2.tolist() == [True] * n
    out3 = T.apply_yellow_entry_masks(npz, n, True, cfg, np.full(n, 99.0), entry.copy(), [])
    assert out3.tolist() == [False] * n
    b, w = T.yellow_live_veto({"close_3bar_15m": 100.0}, True, 99.0, cfg)
    assert (b, w) == (True, "YELLOW_MOM3_BLOCK")
    kgcfg = _get({"KINDERGARTEN_EMA_GATE_ENABLED": True, "KINDERGARTEN_FILTER_TF": "15m"})
    kgnpz = _npz(n, ema_200_15m=np.full(n, 110.0), sma_200_15m=np.full(n, 110.0))
    out4 = T.apply_yellow_entry_masks(kgnpz, n, True, kgcfg, np.full(n, 100.0), entry.copy(), [])
    assert out4.tolist() == [False] * n
    out5 = T.apply_yellow_entry_masks(kgnpz, n, True, kgcfg, np.full(n, 100.0), entry.copy(), [], include_kg=False)
    assert out5.tolist() == [True] * n
