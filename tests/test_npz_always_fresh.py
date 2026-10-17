"""NPZ freshness contracts (USER 2026-10-16 "revise and fix so they are always up to date from now on").

Pins the five defects that kept every live NPZ 2-4 bars behind and six symbols frozen for days:
  1. one global lock made every targeted top-up a silent no-op  -> per-symbol locks
  2. a cycle longer than the bar then slept to the NEXT boundary -> catchup_wait
  3. fixed job order starved the same tail of the universe       -> order_jobs (live first, worst-behind first)
  4. hard refusals were logged forever and repaired by nobody    -> repair_queue_update
  5. the monthly leg froze short crypto stores                   -> drop_keys(mode), stocks keep it (live reads wt1_M)
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
os.environ.setdefault("NPZ_REFRESH_ROOT", str(ROOT))
nbr = pytest.importorskip("tools.npz_bar_refresh")
BAR = nbr.BAR


def test_monthly_leg_droppable_for_crypto_only():
    crypto, stocks = nbr.drop_keys("crypto"), nbr.drop_keys("tradier")
    for k in ("rsi_M", "atr_M", "stoch_k_M", "wt1_M", "wt2_M"):
        assert k in crypto, f"{k} must be droppable for crypto (ZECUSDC froze 64 h on it)"
        assert k not in stocks, f"{k} must NEVER be dropped for stocks (tradier_manage + the W/M entry gate read it)"
    assert nbr.REFRESH_DROP_KEYS <= crypto and nbr.REFRESH_DROP_KEYS <= stocks


def test_per_symbol_lock_excludes_the_same_symbol_only(tmp_path, monkeypatch):
    monkeypatch.setattr(nbr, "SYMLOCK_DIR", tmp_path / "locks")
    a = nbr.sym_lock("BNBUSDC")
    assert a is not None
    assert nbr.sym_lock("BNBUSDC") is None, "a second refresh of the same symbol must be refused"
    b = nbr.sym_lock("ETHUSDC")
    assert b is not None, "a different symbol must never be blocked — that was the no-op bug"
    import fcntl
    for fd in (a, b):
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    c = nbr.sym_lock("BNBUSDC")
    assert c is not None, "lock must be released when the refresh finishes"
    fcntl.flock(c, fcntl.LOCK_UN)
    os.close(c)


def test_refresh_symbol_reports_locked_instead_of_lying(tmp_path, monkeypatch):
    monkeypatch.setattr(nbr, "SYMLOCK_DIR", tmp_path / "locks")
    (tmp_path / "ind").mkdir()
    (tmp_path / "ind" / "FOOUSDT.npz").write_bytes(b"not really an npz")
    held = nbr.sym_lock("FOOUSDT")
    r = nbr.refresh_symbol("FOOUSDT", str(tmp_path / "ind"), cutoff=nbr.last_closed_bar(), mode="crypto")
    assert r["status"] == "LOCKED_SYMBOL"
    import fcntl
    fcntl.flock(held, fcntl.LOCK_UN)
    os.close(held)


def test_catchup_does_not_wait_out_another_bar():
    now = (int(time.time()) // BAR) * BAR + 600  # 600 s into the current bar
    newest = nbr.last_closed_bar(now)
    behind = nbr.catchup_wait(newest - BAR, now=now)
    assert behind <= float(os.environ.get("NPZ_LOOP_SETTLE_S", "75") or 75), "a bar is already closed and unappended — nothing to wait for"
    current = nbr.catchup_wait(newest, now=now)
    assert current > behind and current <= BAR, "nothing new -> normal boundary wait"


def test_order_jobs_live_first_worst_behind_first_and_frozen_skipped(monkeypatch, tmp_path):
    cut = nbr.last_closed_bar()
    last = {"LIVEUSDT": cut - 1 * BAR, "OLDUSDT": cut - 40 * BAR, "NEWUSDT": cut - 2 * BAR, "FROZENUSDT": cut - 99 * BAR}
    monkeypatch.setattr(nbr, "priority_syms", lambda: {"LIVEUSDT"})
    monkeypatch.setattr(nbr, "frozen_syms", lambda: {"FROZENUSDT"})
    jobs = [(s, str(tmp_path), 2500, False, False, cut, "crypto", None) for s in ("NEWUSDT", "OLDUSDT", "LIVEUSDT", "FROZENUSDT")]
    out, skipped = nbr.order_jobs(jobs, str(tmp_path), last, cut)
    assert [j[0] for j in out] == ["LIVEUSDT", "OLDUSDT", "NEWUSDT"]
    assert [r["symbol"] for r in skipped] == ["FROZENUSDT"] and skipped[0]["status"] == "FROZEN_BOARD"


def test_repair_queue_records_hard_failures_and_prunes_healed(tmp_path):
    p = tmp_path / "q.json"
    res = [{"symbol": "ASTERUSDT", "status": "REFUSE_LEGACY_HTF_ALIGN", "mode": "crypto"},
           {"symbol": "CDW", "status": "TOO_SHORT_TO_APPEND", "mode": "tradier", "error": "1 rows"},
           {"symbol": "BNBUSDC", "status": "INSTALLED", "mode": "crypto"}]
    q = nbr.repair_queue_update(res, path=p)
    assert set(q) == {"ASTERUSDT", "CDW"}, "only the unfixable-by-topup statuses are queued"
    assert q["CDW"]["cycles"] == 1
    q = nbr.repair_queue_update(res, path=p)
    assert q["CDW"]["cycles"] == 2, "repeat offences must be counted, not overwritten"
    cur = json.loads(p.read_text())["syms"]
    cur["ASTERUSDT"]["last_seen"] = time.time() - 7 * 3600
    p.write_text(json.dumps({"syms": cur}))
    q = nbr.repair_queue_update([r for r in res if r["symbol"] == "CDW"], path=p)
    assert "ASTERUSDT" not in q and "CDW" in q, "a symbol that stops failing for 6 h is healed and leaves the queue"


def test_autoscale_targets_one_bar_and_respects_the_cap(monkeypatch):
    monkeypatch.setenv("NPZ_LOOP_AUTOSCALE", "1")
    monkeypatch.setenv("NPZ_LOOP_MAX_WORKERS", "8")

    class A:
        workers = 2
    a = A()
    state = {"last_cycle_s": 2634.1, "last_jobs": 363}  # the measured cycle that caused the 3.4-bar median
    assert nbr.autoscale(a, state) == 8, "a cycle 2.9x the bar must raise the pool to the cap"
    a2 = A()
    assert nbr.autoscale(a2, {}) == 8, "no history yet -> start at the cap, not at 2"
    monkeypatch.setenv("NPZ_LOOP_AUTOSCALE", "0")
    a3 = A()
    assert nbr.autoscale(a3, state) == 2, "kill switch pins --workers"


@pytest.mark.skipif(not (ROOT / "npz_live_generator.py").exists(), reason="writer not present")
def test_live_writer_never_shrinks_a_store(tmp_path):
    np = pytest.importorskip("numpy")
    pytest.importorskip("pandas", reason="npz_live_generator imports pandas at module level")
    import npz_live_generator as gen
    p = tmp_path / "CDW.npz"
    ts = np.arange(1_792_000_000, 1_792_000_000 + 100 * 900, 900, dtype=np.int64)
    np.savez_compressed(p, timestamps=ts, close_15m=np.ones(len(ts), dtype=np.float32))
    ok, why = gen._shrink_check(p, {"timestamps": np.array([int(ts[-1]) + 900], dtype=np.int64)})
    assert not ok and "bar-loss" in why, "a 1-row live stub must never replace a 100-row store (CDW/ADP/AEM/AXTI, 2026-10-13)"
    ok, _ = gen._shrink_check(p, {"timestamps": np.append(ts, ts[-1] + 900)})
    assert ok, "the legitimate append must still pass"
    ok, why = gen._shrink_check(p, {"timestamps": ts - 86400})
    assert not ok and "time-regression" in why


def test_monthly_leg_is_matched_as_a_family_not_a_handlist():
    # ZECUSDC refused twice over a hand-written list (45 keys, then wt_cross_*_M 28 more)
    for k in ("wt_cross_bull_M", "wt_cross_value_M", "wt_crossover_value_M", "some_future_indicator_M", "x_M_prev"):
        assert nbr.droppable(k, "crypto"), f"{k} is part of the monthly leg"
        assert not nbr.droppable(k, "tradier"), f"{k} must survive on stocks"
    assert not nbr.droppable("close_15m", "crypto") and not nbr.droppable("wt1_4h", "crypto")
    for k in nbr.REFRESH_DROP_KEYS:
        assert nbr.droppable(k, "crypto") and nbr.droppable(k, "tradier")


def test_installed_stores_are_part_of_the_universe(tmp_path):
    for n in ("42USDT.npz", "WMT.npz", "BTCUSDT_LONG.npz", "AAPL_S3.npz", "FOO_365.npz", "ETHUSDC.npz"):
        (tmp_path / n).write_bytes(b"x")
    got = nbr.installed_syms(str(tmp_path))
    assert set(got) == {"42USDT", "WMT", "ETHUSDC"}, "variant/derived files match no klines and must stay out"


def test_split_universe_adds_installed_and_honours_the_kill_switch(monkeypatch, tmp_path):
    (tmp_path / "42USDT.npz").write_bytes(b"x")
    (tmp_path / "WMT.npz").write_bytes(b"x")
    monkeypatch.setattr(nbr, "universe", lambda: ["BTCUSDC"])
    monkeypatch.setattr(nbr, "universe_stocks", lambda: ["AAPL"])

    class A:
        symbols = None
        mode = "both"
        ind_dir = str(tmp_path)
    c, s = nbr.split_universe(A())
    assert "42USDT" in c and "BTCUSDC" in c and "WMT" in s and "AAPL" in s
    monkeypatch.setenv("NPZ_COVER_INSTALLED", "0")
    c2, s2 = nbr.split_universe(A())
    assert c2 == ["BTCUSDC"] and s2 == ["AAPL"]


def test_cutoff_override_is_what_lets_a_big_hole_close(monkeypatch, tmp_path):
    # refresh_symbol refuses gap>1400, so a 3282-bar hole (AEM) needs capped steps, not repeated full-clock tries
    captured = {}
    monkeypatch.setattr(nbr, "universe", lambda: ["FOOUSDT"])
    monkeypatch.setattr(nbr, "universe_stocks", lambda: [])
    monkeypatch.setattr(nbr, "run_cycle", lambda a, ind, c, s, pf, cut, w15, lt=None, sp=None: (captured.setdefault("cutoff", cut) and None) or ([], {"status": "SKIPPED"}, 0.0))

    class A:
        symbols = "FOOUSDT"
        mode = "crypto"
        ind_dir = str(tmp_path)
        dry_run = True
        force = False
        no_stamp = True
        no_sentiment = True
        no_fng = True
        workers = 1
        cutoff = 1789147800 + 1300 * 900
    nbr.one_cycle(A(), 2500, {"last_catchup": 0.0}, False)
    assert captured["cutoff"] == A.cutoff, "the one-shot must honour --cutoff instead of the wall clock"


def test_repair_refuses_a_candidate_that_is_not_on_the_grid():
    rp = pytest.importorskip("tools.npz_repair")
    junk = {"rows": 43904, "first_ts": 1, "last_ts": 1 + 20708 * 86400, "dt_med": 0.0, "keys": 1128, "htf_align": None}
    five_min = {"rows": 58046, "first_ts": 0, "last_ts": 885 * 86400, "dt_med": 300.0, "keys": 1072, "htf_align": None}
    good = {"rows": 15895, "first_ts": 0, "last_ts": 737 * 86400, "dt_med": 900.0, "keys": 1161, "htf_align": None}
    assert not rp.grid_ok(junk), "dt_med=0 (duplicate timestamps) over 20708 days is junk — SSD2T's CDW copy"
    assert not rp.grid_ok(five_min), "a 300 s grid is a different generation, not a deeper copy — SSD2T's ADP copy"
    assert rp.grid_ok(good) and rp.appendable(good, "ADP")
    stub = {"rows": 1, "first_ts": 0, "last_ts": 0, "dt_med": 0.0, "keys": 1142, "htf_align": None}
    assert not rp.appendable(stub, "ADP")
    ok, why = rp.better(good, stub, "ADP")
    assert ok and "rows 1 -> 15895" in why
    ok2, _ = rp.better(junk, good, "ADP")
    assert not ok2, "a junk candidate must never displace a valid store even with more rows"


def test_frozen_source_is_not_queued_as_an_npz_repair(tmp_path, monkeypatch):
    # ICXUSDT/STORJUSDT/IPUSDC/VANRYUSDT: one repeated close for 4956-10618 bars, exchange has no price.
    # stoch_rsi returns None on a constant close, so the builder emits none of the 10 stoch_*_15m keys and the
    # refresh ERRORs forever. That is a delisted ticker, not a store a deeper copy can fix.
    monkeypatch.setattr(nbr, "ROOT", tmp_path)
    (tmp_path / "klines_cache").mkdir()
    frozen = [{"timestamp": f"2026-10-16T{h:02d}:{m:02d}:00.000000Z", "close": 0.01522} for h in range(24) for m in (0, 15, 30, 45)]
    (tmp_path / "klines_cache" / "ICXUSDT_15m.json").write_text(json.dumps(frozen))
    moving = [{"timestamp": r["timestamp"], "close": 0.01522 + i * 1e-5} for i, r in enumerate(frozen)]
    (tmp_path / "klines_cache" / "LIVEUSDT_15m.json").write_text(json.dumps(moving))
    assert nbr.source_frozen("ICXUSDT") is True
    assert nbr.source_frozen("LIVEUSDT") is False
    assert nbr.source_frozen("NOSUCHUSDT") is False
    q = tmp_path / "q.json"
    res = [{"symbol": "ICXUSDT", "status": "ERROR", "mode": "crypto", "error": "cannot extend 10 row keys: ['stoch_k_15m'...]"},
           {"symbol": "LIVEUSDT", "status": "ERROR", "mode": "crypto", "error": "cannot extend 10 row keys"}]
    out = nbr.repair_queue_update(res, path=q)
    assert "ICXUSDT" not in out, "a frozen/delisted source must never enter the repair queue"
    assert "LIVEUSDT" in out, "a real unextendable store still must"
    assert (tmp_path / "data" / "alerts" / "klines_frozen_source.json").exists()
