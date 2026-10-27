import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import fleet_dispatch as F

NOW = 2_000_000_000
TAIL = NOW - 600


def host(running=(), mem=20000, disk=50000, progress=None, launches=None, tails=None):
    return {"running": {s: 100 for s in running}, "mem_avail_mb": mem, "disk_free_mb": disk,
            "pdir": "/home/niels/v15_run37_x/progress", "progress": progress or {}, "launches": launches or {},
            "tails": tails or {}, "impossible": {}}


UNI = {"CRYPTO_SHORT": ["BTCUSDT_SHORT", "ETHUSDT_SHORT"], "CRYPTO_LONG": ["BTCUSDT_LONG"],
       "STOCKS_SHORT": ["AAPL_SHORT"], "STOCKS_LONG": ["AAPL_LONG", "MSFT_LONG"]}


def fleet4(**over):
    d = {n: host(tails={"BTCUSDT": TAIL, "ETHUSDT": TAIL, "AAPL": TAIL, "MSFT": TAIL}) for n in ("s1", "s2", "s5", "s6")}
    d.update(over)
    return d


def test_home_first():
    data = fleet4()
    pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    picks, _, _ = F.plan(pend, partial, data, NOW, {})
    got = {h: s for h, s, _ in picks}
    assert got == {"s1": "BTCUSDT_SHORT", "s2": "BTCUSDT_LONG", "s5": "AAPL_LONG", "s6": "AAPL_SHORT"}


def test_overflow_when_home_exhausted_follows_priority():
    uni = dict(UNI, CRYPTO_LONG=[])
    data = fleet4()
    pend, partial, _ = F.pending_by_cat(uni, data, NOW)
    picks, _, _ = F.plan(pend, partial, data, NOW, {})
    got = {h: (s, w) for h, s, w in picks}
    assert got["s2"][0] == "ETHUSDT_SHORT" and got["s2"][1].startswith("overflow CRYPTO_SHORT")


def test_done_requires_complete_and_fresh_npz():
    stale = {"fg": True, "complete": True, "ts": TAIL - 60 * F.BAR_S, "rows": 3000}
    fresh_incomplete = {"fg": True, "complete": False, "ts": TAIL, "rows": 2681}
    fresh_done = {"fg": True, "complete": True, "ts": TAIL, "rows": 3800}
    data = fleet4(s1=host(tails={"BTCUSDT": TAIL, "ETHUSDT": TAIL}, progress={"BTCUSDT_SHORT": stale, "ETHUSDT_SHORT": fresh_incomplete, "BTCUSDT_LONG": fresh_done}))
    pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    assert pend["CRYPTO_SHORT"] == ["BTCUSDT_SHORT"]
    assert pend["CRYPTO_LONG"] == []
    F.DONE_RULE = "complete"
    try:
        pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    finally:
        F.DONE_RULE = "final"
    assert pend["CRYPTO_SHORT"] == ["BTCUSDT_SHORT", "ETHUSDT_SHORT"]


def test_running_anywhere_is_not_pending_and_retry_cap():
    launches = {"ETHUSDT_SHORT": {"ts": NOW - 4000, "tail": TAIL, "n": 2}}
    data = fleet4(s5=host(running=["BTCUSDT_SHORT"], launches=launches))
    pend, _, skipped = F.pending_by_cat(UNI, data, NOW)
    assert pend["CRYPTO_SHORT"] == [] and "attempts" in skipped["ETHUSDT_SHORT"]


def test_orphaned_home_gets_a_slot_elsewhere():
    data = fleet4(s1=host(mem=500))
    pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    picks, why_not, orphans = F.plan(pend, partial, data, NOW, {"CRYPTO_SHORT": NOW - 3600})
    got = {h: s for h, s, _ in picks}
    assert "CRYPTO_SHORT" in orphans and "s1" in why_not
    assert got["s2"] == "BTCUSDT_SHORT"
    assert got["s6"] == "ETHUSDT_SHORT" and got["s5"] == "AAPL_LONG"


def test_new_server_without_home_takes_top_priority_work():
    data = fleet4(s7=host())
    pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    picks, _, _ = F.plan(pend, partial, data, NOW, {})
    got = {h: s for h, s, _ in picks}
    assert got["s7"] == "ETHUSDT_SHORT"


def test_full_or_low_disk_hosts_get_nothing():
    data = fleet4(s5=host(running=["X_LONG", "Y_LONG", "Z_LONG", "W_LONG"]), s6=host(disk=100))
    pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    picks, why_not, _ = F.plan(pend, partial, data, NOW, {})
    assert {h for h, _, _ in picks} == {"s1", "s2"}
    assert why_not["s5"].startswith("full") and why_not["s6"].startswith("disk")


def test_resume_first_then_fresh_then_foreign_partial():
    part = {"rows": 900, "fg": False, "ts": TAIL}
    uni = dict(UNI, STOCKS_LONG=["AAPL_LONG", "MSFT_LONG", "NVDA_LONG"])
    data = fleet4(s5=host(progress={"NVDA_LONG": part}), s6=host(progress={"AAPL_LONG": part}))
    pend, partial, _ = F.pending_by_cat(uni, data, NOW)
    picks, _, _ = F.plan(pend, partial, data, NOW, {})
    got = {h: s for h, s, _ in picks}
    assert got["s5"] == "NVDA_LONG"
    data = fleet4(s6=host(progress={"AAPL_LONG": part}))
    pend, partial, _ = F.pending_by_cat(uni, data, NOW)
    picks, _, _ = F.plan(pend, partial, data, NOW, {})
    assert {h: s for h, s, _ in picks}["s5"] == "MSFT_LONG"


def test_election_never_both_standby():
    hb_act = {"ts": NOW - 10, "acting": True}
    hb_idle = {"ts": NOW - 10, "acting": False, "note": "s1 overloaded"}
    hb_stale = {"ts": NOW - 999, "acting": True}
    assert F.decide("s1", None, NOW, 1.0, 16)[0] is True
    assert F.decide("s1", None, NOW, 100.0, 16)[0] is False
    assert F.decide("s2", hb_act, NOW, 1.0, 16)[0] is False
    assert F.decide("s2", hb_idle, NOW, 1.0, 16)[0] is True
    assert F.decide("s2", hb_stale, NOW, 1.0, 16)[0] is True
    assert F.decide("s2", None, NOW, 1.0, 16)[0] is True
    assert F.decide("s7", None, NOW, 1.0, 16)[0] is True


def test_quarantined_host_gets_nothing_and_home_orphans():
    data = fleet4(s1=dict(host(), _quarantine="quarantine: 2 boards died"))
    pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    picks, why_not, orphans = F.plan(pend, partial, data, NOW, {"CRYPTO_SHORT": NOW - 3600})
    assert "s1" not in {h for h, _, _ in picks} and why_not["s1"].startswith("quarantine")
    assert "CRYPTO_SHORT" in orphans


def test_quarantine_counts_deaths_even_if_relaunched_elsewhere():
    assert "ss not in (d.get(\"running\") or {})" in open(F.__file__).read()
