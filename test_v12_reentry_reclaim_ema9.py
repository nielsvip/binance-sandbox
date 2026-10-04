import numpy as np
from vec_paths.v12_reentry_augment_gap_batch2 import exit_reclaim_mask

def _arrays(n=4, **u):
    a = dict(close=np.full(n, 110.0), ema_9_15m=np.full(n, 100.0), k_3m=np.full(n, 60.0), k_3m_prev=np.full(n, 55.0), wt1_15m=np.full(n, 10.0), wt2_15m=np.full(n, 5.0), wt_velocity_15m=np.full(n, 2.0), k_15m=np.full(n, 50.0))
    a.update(u)
    return a

def _state(n=4):
    return {"candidate_mask": np.ones(n, bool), "exit_price": np.full(n, 100.0)}

def _cfg(**u):
    c = dict(REENTRY_EXIT_RECLAIM_ENABLED=True, REENTRY_EXIT_RECLAIM_BUFFER_PCT=0.2)
    c.update(u)
    return c

def test_reclaim_fires_on_ema9_15m_long():
    r = exit_reclaim_mask(_arrays(), _state(), True, _cfg())
    assert r.available, r.reason
    assert r.mask.tolist() == [True] * 4

def test_reclaim_unavailable_without_ema9_15m():
    a = _arrays()
    del a["ema_9_15m"]
    a["sma_200_1m"] = np.full(4, 100.0)
    r = exit_reclaim_mask(a, _state(), True, _cfg())
    assert not r.available
    assert "ema_9_15m" in r.reason

def test_reclaim_blocked_below_ema9_15m_long():
    r = exit_reclaim_mask(_arrays(ema_9_15m=np.full(4, 120.0)), _state(), True, _cfg())
    assert r.available, r.reason
    assert r.mask.tolist() == [False] * 4
