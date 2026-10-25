"""Pair-floor keep-loaded guarantee (USER 2026-10-24): a paid host never idles
while boards exist — a host below min(cap, V15_SCHED_MIN_PAIRS=4) admits
budget-exhausted sides past the attempt/slice caps, drawing V15_SCHED_FLOOR_EXTRA.
Every safety gate (mem/cpu/disk/swap, NPZ, venue, parity, running-dedup) still applies."""

import datetime
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import v15_fleet_scheduler as _SCHED
from v15_fleet_scheduler import _below_floor_hosts, _burn_launch, _floor_left, _floor_target_n, tick


class EmptyDispatchTest(unittest.TestCase):
    def test_empty_launch_command_never_starts_dispatch_job(self):
        act = {"sym": "TEST", "side": "SHORT", "window": "30D", "key": "TEST_SHORT|30D"}
        with patch.object(_SCHED, "_drop_untradeable", return_value=[act]), \
             patch.object(_SCHED, "pair_launch_cmd", return_value=""), \
             patch.object(_SCHED.subprocess, "Popen") as popen, \
             patch.dict(os.environ, {"V15_SCHED_HARD_RECENT": "0"}):
            self.assertFalse(_SCHED._dispatch_async({"ssh": ["localhost"]}, "TEST", [act], "/tmp", 7, "test"))
        popen.assert_not_called()


class VerdictSkipsTest(unittest.TestCase):
    NOW = 10 * 86400.0

    def test_fresh_terminal_verdicts_held(self):
        ex = {"s6": {"A_LONG": [self.NOW - 60, "[SOFT-SKIP]"], "B_SHORT": [self.NOW - 60, "[IMPOSSIBLE-SKIP] verdict NO_TRADES"]}}
        got = _SCHED._verdict_skips(ex, self.NOW, 3600)
        self.assertEqual(set(got), {"A_LONG", "B_SHORT"})

    def test_stale_and_other_markers_pass(self):
        ex = {"s6": {"A_LONG": [self.NOW - 7200, "[SOFT-SKIP]"], "B_SHORT": [self.NOW - 60, "STALE_NPZ_SKIPPED"],
                      "C_LONG": [self.NOW - 60, "[DEDUP]"], "D_SHORT": [self.NOW - 60, "[DEFAULTS-GATE]"]}}
        self.assertEqual(_SCHED._verdict_skips(ex, self.NOW, 3600), {})

    def test_junk_safe(self):
        self.assertEqual(_SCHED._verdict_skips(None, self.NOW, 3600), {})
        self.assertEqual(_SCHED._verdict_skips({"s6": None, "s5": {"X_LONG": "bad"}}, self.NOW, 3600), {})
        self.assertEqual(_SCHED._verdict_skips({"s6": {"X_LONG": [self.NOW, "[SOFT-SKIP]"]}}, self.NOW, 0), {})
        self.assertEqual(_SCHED._verdict_skips({"s6": {"X_LONG": [self.NOW, "[SOFT-SKIP]"]}}, self.NOW, "bogus"), {})


class BelowFloorHostsTest(unittest.TestCase):
    def test_local_dispatchers_consume_slots_once(self):
        self.assertEqual(_SCHED._occupied_n({"AR": "running"}, {"AR", "BA", "BHP"}), 3)
        hosts = [{"name": "s5", "max_pairs": 4}, {"name": "s6", "max_pairs": 4}]
        held = {"s5": {"AR": "running"}, "s6": {"MP": "running"}}
        observed = {"s5": {"AR", "BA", "BHP"}, "s6": {"MP"}}
        self.assertEqual([h["name"] for h in _SCHED._host_order(hosts, held, observed)], ["s6", "s5"])

    def test_names_below_floor(self):
        self.assertEqual(_below_floor_hosts({"a": {"X": 1}, "b": {"w": 1, "x": 1, "y": 1, "z": 1}}, {"a": 8, "b": 8}), ["a"])
        self.assertEqual(_below_floor_hosts({}, {"a": 8}), ["a"])
        self.assertEqual(_below_floor_hosts({"a": {"1": 1, "2": 1}}, {"a": 2}), [])

    def test_disabled_floor_never_below(self):
        old = os.environ.get("V15_SCHED_MIN_PAIRS")
        os.environ["V15_SCHED_MIN_PAIRS"] = "0"
        try:
            self.assertEqual(_below_floor_hosts({}, {"a": 8}), [])
        finally:
            if old is None:
                os.environ.pop("V15_SCHED_MIN_PAIRS", None)
            else:
                os.environ["V15_SCHED_MIN_PAIRS"] = old

    def test_junk_safe(self):
        self.assertEqual(_below_floor_hosts(None, None), [])
        self.assertEqual(_below_floor_hosts({"a": 1}, {"a": 8}), [])


class BoardedMergeTest(unittest.TestCase):
    """Only a manifest dates a board: result_done_utc without one is a run
    that published nothing (INTC_LONG wore 18:24 with 6-day-old cells)."""

    def test_manifest_dates_board(self):
        self.assertEqual(_SCHED._boarded_merge({}, {"X_LONG": 100.0}, {"X_LONG": 200.0}), {"X_LONG": 100.0})

    def test_done_utc_without_manifest_contributes_nothing(self):
        self.assertEqual(_SCHED._boarded_merge({"Y_LONG": 50.0}, {}, {"X_LONG": 200.0}), {"Y_LONG": 50.0})

    def test_max_with_existing(self):
        self.assertEqual(_SCHED._boarded_merge({"X_LONG": 150.0}, {"X_LONG": 100.0}, {"X_LONG": 200.0}), {"X_LONG": 150.0})
        self.assertEqual(_SCHED._boarded_merge({}, {"X_LONG": 300.0}, {"X_LONG": 200.0}), {"X_LONG": 300.0})

    def test_junk_safe(self):
        self.assertEqual(_SCHED._boarded_merge(None, None, None), {})
        self.assertEqual(_SCHED._boarded_merge({}, {"X_LONG": "bogus"}, {"X_LONG": 1.0}), {})


class RefreshZeroWindowTest(unittest.TestCase):
    """A zeroed defer window means 'never defer', not 'never refresh' (S1
    zeroes M/L deferral for daily recalcs — M/L boards must still requeue
    daily or the fleet starves once the round drains)."""

    NOW = 10 * 86400.0

    def test_zero_window_falls_back_to_daily(self):
        tiers = {"X_LONG": {"tier": "M", "mtime": self.NOW - 1.1 * 86400.0}}
        self.assertTrue(_SCHED._refresh_due("X_LONG", tiers, self.NOW, {"M": 0.0, "L": 0.0}))
        fresh = {"X_LONG": {"tier": "M", "mtime": self.NOW - 0.5 * 86400.0}}
        self.assertFalse(_SCHED._refresh_due("X_LONG", fresh, self.NOW, {"M": 0.0, "L": 0.0}))

    def test_positive_window_unchanged(self):
        tiers = {"X_LONG": {"tier": "M", "mtime": self.NOW - 1.1 * 86400.0}}
        self.assertTrue(_SCHED._refresh_due("X_LONG", tiers, self.NOW, {"M": 1.0, "L": 2.0}))
        fresh = {"X_LONG": {"tier": "M", "mtime": self.NOW - 0.5 * 86400.0}}
        self.assertFalse(_SCHED._refresh_due("X_LONG", fresh, self.NOW, {"M": 1.0, "L": 2.0}))

    def test_refresh_all_off_still_gates(self):
        old = os.environ.get("V15_SCHED_REFRESH_ALL")
        os.environ["V15_SCHED_REFRESH_ALL"] = "0"
        try:
            tiers = {"X_LONG": {"tier": "M", "mtime": self.NOW - 5 * 86400.0}}
            self.assertFalse(_SCHED._refresh_due("X_LONG", tiers, self.NOW, {"M": 0.0, "L": 0.0}))
        finally:
            if old is None:
                os.environ.pop("V15_SCHED_REFRESH_ALL", None)
            else:
                os.environ["V15_SCHED_REFRESH_ALL"] = old


class RefundTerminalTest(unittest.TestCase):
    def test_terminal_verdict_skips_never_refund(self):
        st = {"attempts": {"FLOKI_SHORT|30D": 3, "ADA_SHORT|30D": 3}}
        ex = {"s6": {"FLOKI_SHORT": [1000.0, "[SOFT-SKIP]"], "ADA_SHORT": [1000.0, "[IMPOSSIBLE-SKIP]"]}}
        self.assertEqual(_SCHED._refund_structural_exits(st, ex), [])
        self.assertEqual(st["attempts"]["FLOKI_SHORT|30D"], 3)
        self.assertEqual(st["attempts"]["ADA_SHORT|30D"], 3)

    def test_stale_and_gates_still_refund(self):
        st = {"attempts": {"X_LONG|30D": 3}}
        ex = {"s1": {"X_LONG": [1000.0, "STALE_NPZ_SKIPPED"]}}
        got = _SCHED._refund_structural_exits(st, ex)
        self.assertEqual(got, [("s1", "X_LONG", "STALE_NPZ_SKIPPED", 2)])


class FloorTargetTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("V15_SCHED_MIN_PAIRS")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("V15_SCHED_MIN_PAIRS", None)
        else:
            os.environ["V15_SCHED_MIN_PAIRS"] = self._old

    def test_default_four_capped_by_cap(self):
        os.environ.pop("V15_SCHED_MIN_PAIRS", None)
        self.assertEqual(_floor_target_n(8), 4)
        self.assertEqual(_floor_target_n(2), 2)
        self.assertEqual(_floor_target_n(12), 4)

    def test_env_override(self):
        os.environ["V15_SCHED_MIN_PAIRS"] = "6"
        self.assertEqual(_floor_target_n(8), 6)
        self.assertEqual(_floor_target_n(4), 4)

    def test_zero_disables(self):
        os.environ["V15_SCHED_MIN_PAIRS"] = "0"
        self.assertEqual(_floor_target_n(8), 0)

    def test_junk_safe(self):
        os.environ["V15_SCHED_MIN_PAIRS"] = "bogus"
        self.assertEqual(_floor_target_n(8), 4)
        os.environ.pop("V15_SCHED_MIN_PAIRS", None)
        self.assertEqual(_floor_target_n("bogus"), 2)
        self.assertEqual(_floor_target_n(None), 2)


class FloorLeftTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("V15_SCHED_FLOOR_EXTRA")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("V15_SCHED_FLOOR_EXTRA", None)
        else:
            os.environ["V15_SCHED_FLOOR_EXTRA"] = self._old

    def test_fresh_key_has_full_extra(self):
        os.environ.pop("V15_SCHED_FLOOR_EXTRA", None)
        self.assertEqual(_floor_left({}, "X_LONG|30D"), 6)

    def test_burned_counts_down(self):
        self.assertEqual(_floor_left({"floor": {"X_LONG|30D": 2}}, "X_LONG|30D"), 4)
        self.assertEqual(_floor_left({"floor": {"X_LONG|30D": 6}}, "X_LONG|30D"), 0)
        self.assertEqual(_floor_left({"floor": {"X_LONG|30D": 99}}, "X_LONG|30D"), 0)

    def test_zero_disables(self):
        os.environ["V15_SCHED_FLOOR_EXTRA"] = "0"
        self.assertEqual(_floor_left({}, "X_LONG|30D"), 0)

    def test_junk_safe(self):
        self.assertEqual(_floor_left(None, "X_LONG|30D"), 6)
        self.assertEqual(_floor_left({"floor": "bogus"}, "X_LONG|30D"), 6)
        os.environ["V15_SCHED_FLOOR_EXTRA"] = "bogus"
        self.assertEqual(_floor_left({}, "X_LONG|30D"), 6)


class BurnFloorTest(unittest.TestCase):
    def test_floor_burn_leaves_attempts_and_slices_alone(self):
        st = {"attempts": {"K": 3}, "slices": {"K": 20}}
        _burn_launch("K", "30D", "SS", st, {"SS"}, floor=True)
        self.assertEqual(st["floor"], {"K": 1})
        self.assertEqual(st["attempts"], {"K": 3})
        self.assertEqual(st["slices"], {"K": 20})

    def test_normal_burn_unchanged(self):
        st = {}
        _burn_launch("K", "30D", "SS", st, {"SS"})
        self.assertEqual(st.get("slices"), {"K": 1})
        self.assertNotIn("floor", st)
        st2 = {}
        _burn_launch("K", "365D", "SS", st2, set())
        self.assertEqual(st2.get("attempts"), {"K": 1})
        self.assertNotIn("floor", st2)


class FloorTickTest(unittest.TestCase):
    """An idle host whose every side exhausted its attempt/slice budget must
    launch floor fills (and name them) instead of idling — with the floor off
    the same tick launches nothing."""

    HOST = "floortest"
    NOW = datetime.datetime(2026, 10, 22, 15, 30, tzinfo=datetime.timezone.utc)

    def _run_tick(self, floor_on, early_age_s=None, att=3):
        import v15_universe as _U
        uni, _how = _U.load_or_build(ROOT, self.NOW, write=False)
        syms = {str(s) for s in list(uni.get("stocks", [])) + list(uni.get("crypto", []))}
        try:  # the tick rebuilds the universe when a tradeable source moved — seed that set too
            _rb = _U.build(ROOT, self.NOW)
            syms |= {str(s) for s in list(_rb.get("stocks", [])) + list(_rb.get("crypto", []))}
        except Exception:
            pass
        syms = sorted(syms)
        self.assertTrue(syms)
        keys = {}
        slices = {}
        early = {}
        for s in syms:
            for side in ("LONG", "SHORT"):
                keys[f"{s}_{side}|30D"] = att
                slices[f"{s}_{side}|30D"] = 40
                if early_age_s is not None:
                    early[f"{s}_{side}"] = [self.NOW.timestamp() - early_age_s, "[SOFT-SKIP]"]
        stats = {"nproc": 16, "load1": 2.0, "busy_pct": 10.0, "busy_nonnice_pct": 8.0, "mem_avail_mb": 60000,
                 "pdir": "/tmp/fake_pdir", "swap_total_mb": 0, "swap_used_mb": 0, "disk_avail_mb": 200000,
                 "running": {}, "started": [], "done": [], "quarantined": [], "redo": [], "v365": {}, "repair": {}, "gs": {}, "gains": {},
                 "early_exits": early}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sim_p = str(Path(tmp.name) / "sim.json")
        json.dump({"hosts": {self.HOST: stats}, "ready": {self.HOST: {}}}, open(sim_p, "w"))
        state_p = Path(tmp.name) / "fleet_state.json"
        state_p.write_text(json.dumps({"date": "20261022", "held": {}, "attempts": keys, "slices": slices, "ready": {},
                                       "launched_at": {}, "gain_seen": {}, "attempts_pdir": "/tmp/fake_pdir"}))
        old_state, old_floor = _SCHED.STATE, os.environ.get("V15_SCHED_MIN_PAIRS")
        _SCHED.STATE = state_p
        os.environ["V15_SCHED_MIN_PAIRS"] = "4" if floor_on else "0"
        self.addCleanup(setattr, _SCHED, "STATE", old_state)
        if old_floor is None:
            self.addCleanup(os.environ.pop, "V15_SCHED_MIN_PAIRS", None)
        else:
            self.addCleanup(os.environ.__setitem__, "V15_SCHED_MIN_PAIRS", old_floor)
        args = types.SimpleNamespace(simulate=sim_p, dry_run=False, no_reap=True, max_launch=10, max_attempts=2,
                                     repair_cutoff_min=20, extend_universe=False)
        cfg = {"cpu_target_pct": 200, "pair_floor_mb": 6000,
               "hosts": [{"name": self.HOST, "ssh": ["127.0.0.1"], "root": "~/binance-sandbox", "venues": ["stocks", "crypto"],
                          "mem_reserve_mb": 4000, "max_pairs": 8, "workers_per_side": 4, "oom_mb": 1500}]}
        return tick(args, cfg, self.NOW)

    def test_floor_off_idles_on_exhausted_budgets(self):
        log = self._run_tick(floor_on=False)
        h = log["hosts"][self.HOST]
        self.assertEqual(h.get("launched_pairs"), 0)
        self.assertEqual(h.get("idle_reason"), "no eligible ready symbol for this host")
        self.assertEqual(log.get("launched"), [])

    def test_floor_on_fills_idle_host_past_budgets(self):
        log = self._run_tick(floor_on=True)
        h = log["hosts"][self.HOST]
        self.assertGreaterEqual(h.get("launched_pairs", 0), 1)
        self.assertNotIn("idle_reason", h)
        fills = log.get("floor_fills", {}).get(self.HOST, 0)
        self.assertGreaterEqual(fills, 1)
        fl = log.get("floor", {}).get(self.HOST)
        self.assertIsNotNone(fl)
        self.assertEqual(fl["target"], 4)


class FloorSkipCooldownTest(unittest.TestCase):
    """A side that just structural-exited must not be floor-refilled into the
    same exit (s6 FLOKI/ADA SHORT); after the cooldown it retries."""

    def test_fresh_exits_block_floor_refill(self):
        t = FloorTickTest("_run_tick")
        log = FloorTickTest._run_tick(t, floor_on=True, early_age_s=60)
        h = log["hosts"][FloorTickTest.HOST]
        self.assertEqual(h.get("launched_pairs"), 0)
        self.assertEqual(log.get("launched"), [])
        self.assertTrue(log.get("floor_skipped_exits"))

    def test_stale_exits_retry(self):
        t = FloorTickTest("_run_tick")
        log = FloorTickTest._run_tick(t, floor_on=True, early_age_s=7200)
        h = log["hosts"][FloorTickTest.HOST]
        self.assertGreaterEqual(h.get("launched_pairs", 0), 1)
        self.assertGreaterEqual(log.get("floor_fills", {}).get(FloorTickTest.HOST, 0), 1)


class VerdictHoldTickTest(unittest.TestCase):
    """Terminal-verdict skips hold on the NORMAL path too: fresh budgets,
    floor on, every side freshly soft-skipped -> nothing launches and the
    hold is named (s6 HOOD/FLOKI/ADA cycled 28-39x on PINNED normal budgets)."""

    def test_fresh_verdicts_hold_despite_budgets(self):
        t = FloorTickTest("_run_tick")
        log = FloorTickTest._run_tick(t, floor_on=True, early_age_s=60, att=0)
        h = log["hosts"][FloorTickTest.HOST]
        self.assertEqual(h.get("launched_pairs"), 0)
        self.assertEqual(log.get("launched"), [])
        self.assertTrue(log.get("verdict_skip_hold"))


if __name__ == "__main__":
    unittest.main()
