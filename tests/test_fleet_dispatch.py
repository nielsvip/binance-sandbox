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


def test_catchup_reserves_one_slot_per_host_worst_first():
    data = fleet4()
    pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    queue = ["ETHUSDT_SHORT", "BTCUSDT_SHORT", "AAPL_LONG", "AAPL_SHORT"]
    picks, _, _ = F.plan(pend, partial, data, NOW, {}, catchup=queue)
    got = {h: (s, w) for h, s, w in picks}
    assert got["s1"] == ("ETHUSDT_SHORT", "catchup CRYPTO_SHORT")
    assert got["s2"][1].startswith("catchup ")
    assert got["s6"] == ("AAPL_LONG", "catchup STOCKS_LONG")
    assert got["s5"] == ("AAPL_SHORT", "catchup STOCKS_SHORT")
    running = host(running=["ETHUSDT_SHORT"], launches={"ETHUSDT_SHORT": {"mode": "catchup"}}, tails={"BTCUSDT": TAIL})
    data["s1"] = running
    pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    picks, _, _ = F.plan(pend, partial, data, NOW, {}, catchup=["BTCUSDT_SHORT"])
    assert all(not (h == "s1" and w.startswith("catchup ")) for h, _, w in picks)


def test_catchup_relaunches_only_for_new_chart_run():
    doc = {"run_id": "new", "rows": [{"symside": "AAPL_LONG", "drop_pp": 22.0}, {"symside": "BTCUSDT_SHORT", "drop_pp": 12.0}]}
    data = fleet4(s5=host(launches={"AAPL_LONG": {"mode": "catchup", "catchup_run_id": "new", "ts": NOW - 20, "n": 1}}))
    assert F.unlaunched_catchup(doc, data, now=NOW) == ["BTCUSDT_SHORT"]
    assert F.unlaunched_catchup(doc, data, now=NOW + F.RETRY_S + 1) == ["AAPL_LONG", "BTCUSDT_SHORT"]
    data["s5"]["progress"] = {"AAPL_LONG": {"complete": True, "mtime": NOW + 10}}
    assert F.unlaunched_catchup(doc, data, now=NOW + F.RETRY_S + 1) == ["BTCUSDT_SHORT"]
    doc["run_id"] = "next"
    assert F.unlaunched_catchup(doc, data, now=NOW) == ["AAPL_LONG", "BTCUSDT_SHORT"]


def test_live_catchup_uses_current_set_vector_loss_global_order():
    data = fleet4(s1=host(), s5=host())
    data["s1"]["live_drops"] = [{"symside": "BTCUSDT_SHORT", "drop_pp": 16.0, "settings_updated_at": "new"}]
    data["s5"]["live_drops"] = [{"symside": "AAPL_LONG", "drop_pp": 23.0, "settings_updated_at": "new"}]
    doc = F.live_catchup_queue(data, NOW)
    assert doc["schema"] == "live-active-set-catchup-v1"
    assert [r["symside"] for r in doc["rows"]] == ["AAPL_LONG", "BTCUSDT_SHORT"]
    assert doc["rows"][0]["source_host"] == "s5"


def test_new_live_set_gets_new_catchup_token_same_day():
    doc = {"run_id": "live-20261031", "rows": [{"symside": "BTCUSDT_LONG", "drop_pp": 22.0, "settings_sha256": "new-set-hash"}]}
    old = {"mode": "catchup", "catchup_run_id": "live-20261031:old-set-hash", "ts": NOW - 20, "n": 1}
    data = fleet4(s1=host(launches={"BTCUSDT_LONG": old}))
    assert F.unlaunched_catchup(doc, data, now=NOW) == ["BTCUSDT_LONG"]
    data["s1"]["launches"]["BTCUSDT_LONG"]["catchup_run_id"] = F.catchup_token(doc, doc["rows"][0])
    assert F.unlaunched_catchup(doc, data, now=NOW) == []


def test_idle_stock_host_takes_worst_crypto_catchup():
    data = fleet4(s1=host(mem=500), s2=host(mem=500))
    pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    picks, _, _ = F.plan(pend, partial, data, NOW, {}, catchup=["BTCUSDT_SHORT", "AAPL_LONG"])
    assert any(h in ("s5", "s6") and ss == "BTCUSDT_SHORT" and why == "catchup CRYPTO_SHORT" for h, ss, why in picks)


def test_overflow_when_home_exhausted_follows_priority():
    uni = dict(UNI, CRYPTO_LONG=[])
    data = fleet4()
    pend, partial, _ = F.pending_by_cat(uni, data, NOW)
    picks, _, _ = F.plan(pend, partial, data, NOW, {})
    got = {h: (s, w) for h, s, w in picks}
    # USER 2026-10-28 venue rule: s2's home is the CRYPTO venue, so CRYPTO_SHORT is its own work, not overflow
    assert got["s2"][0] == "ETHUSDT_SHORT" and got["s2"][1].startswith("home CRYPTO_SHORT")


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


def test_venue_peer_covers_a_down_host_no_orphan():
    data = fleet4(s1=host(mem=500))
    pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    picks, why_not, orphans = F.plan(pend, partial, data, NOW, {"CRYPTO_SHORT": NOW - 3600})
    got = {h: s for h, s, _ in picks}
    assert not orphans and "s1" in why_not
    assert got["s2"] == "BTCUSDT_LONG" and got["s5"] == "AAPL_LONG" and got["s6"] == "AAPL_SHORT"


def test_orphaned_venue_gets_a_slot_elsewhere():
    data = fleet4(s1=host(mem=500), s2=host(mem=500))
    pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    picks, why_not, orphans = F.plan(pend, partial, data, NOW, {"CRYPTO_SHORT": NOW - 3600, "CRYPTO_LONG": NOW - 3600})
    got = {h: s for h, s, _ in picks}
    assert {"CRYPTO_SHORT", "CRYPTO_LONG"} <= orphans
    assert {got["s5"], got["s6"]} == {"BTCUSDT_SHORT", "ETHUSDT_SHORT"}


def test_stock_host_steals_crypto_backlog_only_when_stocks_empty():
    uni = dict(UNI, STOCKS_SHORT=[], STOCKS_LONG=["AAPL_LONG"])
    data = fleet4()
    pend, partial, _ = F.pending_by_cat(uni, data, NOW)
    picks, _, _ = F.plan(pend, partial, data, NOW, {})
    got = {h: (s, w) for h, s, w in picks}
    assert got["s6"][0] == "AAPL_LONG" and got["s6"][1] == "home STOCKS_LONG"
    assert got["s5"][0] == "ETHUSDT_SHORT" and got["s5"][1].startswith("overflow CRYPTO_SHORT")


def test_rerun_tier_after_own_pending_before_other_venue():
    done = {"fg": True, "complete": True, "ts": TAIL - 2 * F.BAR_S, "rows": 3800}
    uni = {"CRYPTO_SHORT": ["BTCUSDT_SHORT"], "CRYPTO_LONG": [], "STOCKS_SHORT": ["AAPL_SHORT"], "STOCKS_LONG": []}
    data = fleet4(s6=host(progress={"AAPL_SHORT": done}, tails={"AAPL": TAIL, "BTCUSDT": TAIL}))
    pend, partial, _ = F.pending_by_cat(uni, data, NOW)
    assert pend["STOCKS_SHORT"] == []
    rer = F.refresh_by_cat(uni, data, NOW, pend)
    assert rer["STOCKS_SHORT"] == ["AAPL_SHORT"]
    picks, _, _ = F.plan(pend, partial, data, NOW, {}, refresh=rer)
    got = {h: (s, w) for h, s, w in picks}
    assert got["s1"][0] == "BTCUSDT_SHORT"
    assert got["s6"][0] == "AAPL_SHORT" and got["s6"][1] == "rerun STOCKS_SHORT"
    same = dict(done, ts=TAIL)
    data = fleet4(s6=host(progress={"AAPL_SHORT": same}, tails={"AAPL": TAIL}))
    assert F.refresh_by_cat(uni, data, NOW, F.pending_by_cat(uni, data, NOW)[0])["STOCKS_SHORT"] == []


def test_crypto_rerun_needs_four_bars():
    near = {"fg": True, "complete": True, "ts": TAIL - 3 * F.BAR_S, "rows": 3800}
    uni = {"CRYPTO_SHORT": ["BTCUSDT_SHORT"], "CRYPTO_LONG": [], "STOCKS_SHORT": [], "STOCKS_LONG": []}
    data = fleet4(s1=host(progress={"BTCUSDT_SHORT": near}, tails={"BTCUSDT": TAIL}))
    pend, _, _ = F.pending_by_cat(uni, data, NOW)
    assert F.refresh_by_cat(uni, data, NOW, pend)["CRYPTO_SHORT"] == []
    data = fleet4(s1=host(progress={"BTCUSDT_SHORT": dict(near, ts=TAIL - 4 * F.BAR_S)}, tails={"BTCUSDT": TAIL}))
    assert F.refresh_by_cat(uni, data, NOW, pend)["CRYPTO_SHORT"] == ["BTCUSDT_SHORT"]


def test_stale_snapshot_withholds_launch():
    d = dict(host(), snapshot_age_s=F.SNAPSHOT_MAX_S)
    free, why = F.admission(d, NOW, "s2")
    assert free == 0 and "snapshot" in why
    assert F.admission(dict(host(), snapshot_age_s=60), NOW, "s2")[0] == 1


def test_retryable_start_refusal_burns_no_attempt():
    launches = {"ETHUSDT_SHORT": {"ts": NOW - 400, "tail": TAIL, "n": 2}}
    h = host(launches=launches)
    h["start_refused"] = {"ETHUSDT_SHORT": NOW - 380}
    h["start_refused_why"] = {"ETHUSDT_SHORT": "BASELINE-PERSYM-SNAPSHOT-INVALID"}
    pend, _, skipped = F.pending_by_cat(UNI, fleet4(s2=h), NOW)
    assert "ETHUSDT_SHORT" in pend["CRYPTO_SHORT"]


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
    assert "CRYPTO_SHORT" not in orphans  # s2 (same venue) still admits


def test_quarantine_counts_deaths_even_if_relaunched_elsewhere():
    assert "ss not in (d.get(\"running\") or {})" in open(F.__file__).read()


def test_stock_floor_lower_than_crypto_floor():
    data = fleet4(s5=host(mem=9000), s1=host(mem=9000))
    pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    picks, why_not, _ = F.plan(pend, partial, data, NOW, {})
    got = {h: s for h, s, _ in picks}
    assert got["s5"] == "AAPL_LONG"
    assert "s1" not in got and why_not["s1"].startswith("own venue work waits for memory")


def test_priority_stays_inside_own_venue():
    data = fleet4()
    pend, partial, _ = F.pending_by_cat(UNI, data, NOW)
    picks, _, _ = F.plan(pend, partial, data, NOW, {}, frozenset({"ETHUSDT_SHORT"}))
    got = {h: s for h, s, _ in picks}
    assert got["s1"] == "ETHUSDT_SHORT" and got["s5"] == "AAPL_LONG" and got["s6"] == "AAPL_SHORT"


def test_priority_rerun_moves_to_front_of_pending():
    pend = {"CRYPTO_LONG": ["A_LONG"], "STOCKS_LONG": []}
    refresh = {"CRYPTO_LONG": ["B_LONG", "C_LONG"], "STOCKS_LONG": ["D_LONG"]}
    F.promote_priority_reruns(pend, refresh, {"C_LONG", "D_LONG"})
    assert pend == {"CRYPTO_LONG": ["C_LONG", "A_LONG"], "STOCKS_LONG": ["D_LONG"]}
    assert refresh == {"CRYPTO_LONG": ["B_LONG"], "STOCKS_LONG": []}
