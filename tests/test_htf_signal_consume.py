"""2026-10-13 V17 TF law — vec_decisions/htf_signal_consume (vector array API == live scalar API)."""
import numpy as np

import v12_quick_engine as V
import vec_decisions.htf_signal_consume as H


class _C:
    HTF_SIGNAL_CONSUME_ENABLED = True
    HTF_SIGNAL_MAX_ATTEMPTS = 12
    HTF_SIGNAL_VALID_BARS_D = 96
    HTF_SIGNAL_VALID_BARS_4H = 16
    HTF_SIGNAL_VALID_BARS_1H = 4
    HTF_RETRY_LTF_WINDOW_BARS = 4
    HTF_RETRY_TIGHTEN_WINDOW = 0.75
    HTF_RETRY_TIGHTEN_CONFIRMS = 0.25
    HTF_RETRY_TIGHTEN_SIZE = 1.0
    MODE = "crypto"


def test_defaults_equal_on_all_config_surfaces():
    import config as C
    import config_tradier as CT

    q = V.QuickConfig()
    qt = V.QuickConfig()
    qt.apply_tradier_defaults()
    c = C.Config()
    t = CT.TradierConfig()
    for k in H.KEYS + ("BB_BOUNCE_ENTRY_FILTER_ENABLED",):
        assert getattr(q, k) == getattr(c, k), k
        assert getattr(qt, k) == getattr(t, k), k
    assert (q.HTF_SIGNAL_VALID_BARS_D, q.HTF_SIGNAL_VALID_BARS_4H, q.HTF_SIGNAL_VALID_BARS_1H) == (96, 16, 4)
    assert (qt.HTF_SIGNAL_VALID_BARS_D, qt.HTF_SIGNAL_VALID_BARS_4H, qt.HTF_SIGNAL_VALID_BARS_1H) == (26, 14, 4)
    assert q.HTF_SIGNAL_CONSUME_ENABLED is True and q.BB_BOUNCE_ENTRY_FILTER_ENABLED is True


def test_attempt_schedule_monotone_tightening():
    prev = None
    for k in range(1, 13):
        w, need, size = H.attempt_requirements(_C, k)
        if prev:
            assert w <= prev[0] and need >= prev[1] and size <= prev[2]
        prev = (w, need, size)
    assert H.attempt_requirements(_C, 1) == (4, 1, 1.0)
    assert H.attempt_requirements(_C, 5)[1] == 2 and H.attempt_requirements(_C, 9)[1] == 3
    assert H.attempt_requirements(_C, 12)[0] == 1


def test_retry_book_cap_and_instance_reset():
    b = H.RetryBook()
    always = lambda w, le: 3
    for k in range(12):
        ok, d = b.decide(_C, [("P", 7, 0, 10 ** 9)], 1, -1, always)
        assert ok and d["paths"][0]["attempt"] == k + 1
        b.commit([("P", 7)])
    ok, d = b.decide(_C, [("P", 7, 0, 10 ** 9)], 1, -1, always)
    assert not ok and d["paths"][0]["attempt"] == 13
    ok, d = b.decide(_C, [("P", 8, 0, 10 ** 9)], 1, -1, always)  # new HTF bar = new instance
    assert ok and d["paths"][0]["attempt"] == 1


def test_no_confirmation_never_opens_and_window_expires():
    b = H.RetryBook()
    ok, _ = b.decide(_C, [("P", 1, 0, 10 ** 9)], 5, -1, lambda w, le: 0)
    assert not ok  # mere persistence of the HTF state is not enough
    ok, _ = b.decide(_C, [("P", 1, 0, 16)], 16, -1, lambda w, le: 3)
    assert not ok  # outside the validity window


def test_event_armed_window_vec_equals_live():
    n = 200
    ts = 1_790_000_000 - (1_790_000_000 % 14400) + np.arange(n) * 900.0
    ev = np.zeros(n, bool)
    ev[[3, 20, 21, 70, 150]] = True
    ids = H.instance_ids({}, ts, "4h", n)
    vec = H.extend_event(ev, ids, 16)
    H._LIVE_EVENTS.clear()
    live = [H.live_event_armed("pk", "BB_BOUNCE_ENTRY_4h", bool(ev[i]), ts[i] + 14400, "4h", "crypto", _C) for i in range(n)]
    assert list(vec) == live


def test_law_off_bb_bounce_block_is_raw_event():
    n = 300
    rng = np.random.default_rng(3)
    npz = {"timestamps": np.arange(n) * 900.0 + 1_790_000_000, "bb_pct_b_4h": np.repeat(rng.uniform(0, 1, n // 16 + 1), 16)[:n],
           "bb_lower_4h": np.ones(n), "bb_upper_4h": np.full(n, 2.0), "close": np.ones(n) * 1.5}
    on = V.QuickConfig()
    on.BB_BOUNCE_ENTRY_TF = "4h"
    off = V.QuickConfig()
    off.BB_BOUNCE_ENTRY_TF = "4h"
    off.HTF_SIGNAL_CONSUME_ENABLED = False
    b_on = V.compute_reentry_blocks(npz, n, True, on)["BB_BOUNCE_ENTRY_4h"]
    b_off = V.compute_reentry_blocks(npz, n, True, off)["BB_BOUNCE_ENTRY_4h"]
    pct = npz["bb_pct_b_4h"]
    raw = (np.r_[pct[0], pct[:-1]] < 0.2) & (pct > 0.25)
    assert np.array_equal(b_off, raw)
    assert b_on.sum() > b_off.sum() and np.all(b_on[b_off])


def test_ltf_confirmation_vec_equals_live_dict():
    n = 60
    w1 = np.sin(np.arange(n) / 3.0)
    w2 = np.zeros(n) + 0.05
    npz = {"wt1_15m": w1, "wt2_15m": w2, "wt1_1h": np.zeros(n), "wt2_1h": np.zeros(n), "stoch_k_15m": np.full(n, 50.0), "stoch_d_15m": np.full(n, 50.0)}
    ctx = H.ltf_context(npz, n, True, V._safe)
    for i in range(2, n):
        last, side = int(ctx["l15"][i]), ("BULL" if ctx["ok15"][i] else "BEAR")
        ind = {"wt_cross_15m": side if last >= 0 else "", "wt_cross_bars_ago_15m": (i - last) if last >= 0 else 999}
        for w in (1, 2, 4):
            assert H.ltf_confirmations(ctx, i, w, -1) == H.live_confirmations(ind, True, w, None), (i, w)


def test_bb_bounce_filter_switch():
    import vec_decisions.generic_filter_tf as G

    n = 10
    npz = {"bb_lower_1h": np.ones(n), "low_1h": np.full(n, 2.0)}
    c = V.QuickConfig()
    c.BB_BOUNCE_ENTRY_TF = "1h"
    m_on = G.build_masks(npz, n, True, c, np.full(n, 3.0), V._safe)["entry"]
    c.BB_BOUNCE_ENTRY_FILTER_ENABLED = False
    m_off = G.build_masks(npz, n, True, c, np.full(n, 3.0), V._safe)["entry"]
    assert m_on is not None and not m_on.any() and (m_off is None or bool(np.all(m_off)))
