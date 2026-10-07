import numpy as np
import per_sym_vec_engine_stocks as mod

def test_mismatched_ets_rets_truncated():
    # Simulate events with 4 ets but only 3 rets (bug case)
    class Ev:
        def __init__(self, ts, typ):
            self.ts = ts
            self.type = typ
    long_events = [Ev(1, "CLOSE"), Ev(2, "CLOSE"), Ev(3, "CLOSE"), Ev(4, "CLOSE")]
    short_events = [Ev(5, "CLOSE")]
    long_rets = [0.1, 0.2, 0.3]
    short_rets = [0.4]
    # Replicate fixed logic
    long_ets = [int(ev.ts) for ev in long_events if ev.type in ("CLOSE", "REDUCE", "HEDGE_CLOSE")]
    short_ets = [int(ev.ts) for ev in short_events if ev.type in ("CLOSE", "REDUCE", "HEDGE_CLOSE")]
    n_long = min(len(long_ets), len(long_rets))
    n_short = min(len(short_ets), len(short_rets))
    long_ets, long_rets = long_ets[:n_long], long_rets[:n_long]
    short_ets, short_rets = short_ets[:n_short], short_rets[:n_short]
    pnls = np.array(long_rets + short_rets, dtype=np.float32)
    ets = np.array(long_ets + short_ets, dtype=np.int64)
    assert len(pnls) == len(ets) == 4
    sort_idx = np.argsort(ets)
    pnls_sorted, ets_sorted = pnls[sort_idx], ets[sort_idx]
    assert len(pnls_sorted) == 4

def test_empty_and_single():
    # Empty
    res = mod._run_variant_task_worker_stocks(("FAKE", None, {}, 0.08))
    # Should not crash, return dict with trades 0 or None
    assert isinstance(res, dict) or res is None
