"""V18 E5 (doc 17 §4.2 / §4.4): V18_SPEC_BATCH (speculative prefetch of the whole rest of the tab; on a chain move the
pending results computed against the old running set are cancelled and the window restarts from that row — results are
consumed in row order and keyed by the exact full set, so the chain is byte-identical by construction) and
V18_PAIR_CONCURRENT (the pair partner runs in a forked child next to the primary). Both default OFF."""
import concurrent.futures as cf
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

from tools import v15_v18_planner as V  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def test_flags_default_off(monkeypatch):
    for k in ("V18_SPEC_BATCH", "V18_PAIR_CONCURRENT"):
        monkeypatch.delenv(k, raising=False)
    assert V.spec_batch_on() is False and V.pair_concurrent_on() is False
    monkeypatch.setenv("V18_SPEC_BATCH", "1")
    monkeypatch.setenv("V18_PAIR_CONCURRENT", "1")
    assert V.spec_batch_on() and V.pair_concurrent_on()


def test_tab_span_stops_at_the_tab_boundary_and_cap():
    q = [("A", 1, "x", 1), ("A", 2, "x", 2), ("A", 3, "x", 3), ("B", 1, "y", 1)]
    assert V.tab_span(q, 0, 400) == 3
    assert V.tab_span(q, 1, 400) == 3
    assert V.tab_span(q, 0, 2) == 2
    assert V.tab_span(q, 3, 400) == 4
    assert V.tab_span(q, 4, 400) == 4


def test_cancel_stale_drops_pending_keeps_running_and_done():
    ex = cf.ThreadPoolExecutor(max_workers=1)
    gate = threading.Event()
    running = ex.submit(gate.wait, 5)
    pending = ex.submit(lambda: 1)  # queued behind the running one -> cancellable
    done = cf.Future()
    done.set_result(1)
    inflight = {"r": (running, 0), "p1": (pending, 0), "p2": (pending, 0), "d": (done, 0)}
    n = V.spec_cancel_stale(inflight)
    assert n == 2 and set(inflight) == {"r", "d"} and pending.cancelled()
    gate.set()
    ex.shutdown(wait=True)


def test_speculation_never_changes_a_result():
    # toy chain: speculative results computed for the old chain are simply never consumed (keyed by the exact set)
    cache = {}

    def ev(ov):
        return sum(ov.values())

    def run(spec):
        chain, out = {"a": 0}, []
        rows = [("r1", {"x": 1}), ("r2", {"x": -5}), ("r3", {"y": 2}), ("r4", {"z": 1})]
        cache.clear()
        for i, (name, d) in enumerate(rows):
            if spec:
                for _, d2 in rows[i:]:
                    k = tuple(sorted({**chain, **d2}.items()))
                    cache.setdefault(k, ev(dict(k)))
            k = tuple(sorted({**chain, **d}.items()))
            g = cache[k] if k in cache else ev(dict(k))
            base = ev(chain)
            out.append((name, g - base))
            if g - base > 0:
                chain = {**chain, **d}
        return out, chain
    assert run(True) == run(False)


def test_pilot_spec_wiring_pins():
    assert '_v18_spec = bool(_V18P is not None and _V18P.spec_batch_on() and _pool is not None)' in SRC
    assert '_v18_spec_stats["discarded"] += _V18P.spec_cancel_stale(inflight)' in SRC
    assert '_end = _V18P.tab_span(pending_queue, qi, int(os.environ.get("V18_SPEC_ROWS", "400")))' in SRC
    assert '_cap_busy = int(os.environ.get("V18_SPEC_TASKS", str(4 * _n_proc)))' in SRC
    assert "[V18-SPEC]" in SRC
    # OFF = the original prefetch exactly (ahead 60 / taskcap env, cap 2 x workers)
    assert '_taskcap = os.environ.get("V15_PREFETCH_TASKCAP", "0") == "1" or _v18_spec' in SRC
    assert "_cap_busy = 2 * _n_proc" in SRC


def test_pilot_pair_wiring_pins():
    assert "_v18_child = _v18_pair_fork(_sym) if (_pair_on and _sym and _win30 and _V18P is not None and _V18P.pair_concurrent_on()) else None" in SRC
    assert "pid = os.fork()" in SRC and "os._exit(int(_code or 0))" in SRC
    assert "_v18_pair_join(_sym, *_v18_child)\n    elif _pair_on and _sym and _win30:" in SRC
    # the partner rule is the sequential one, on the NPZ refreshed once before the fork
    i = SRC.index("def _v18_pair_fork(new_symside):")
    body = SRC[i:SRC.index("def _pair_child_workers():")]
    assert "_pair_partner_decision(new_symside, _progress_npz_md5(_opp0), _cur_md5" in body
    assert body.index("ensure_npz_for_symside(new_symside, 30)") < body.index("pid = os.fork()")
