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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import v15_fleet_scheduler as _SCHED
from v15_fleet_scheduler import _below_floor_hosts, _burn_launch, _floor_left, _floor_target_n, tick


class BelowFloorHostsTest(unittest.TestCase):
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

    def _run_tick(self, floor_on, early_age_s=None):
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
                keys[f"{s}_{side}|30D"] = 3
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


if __name__ == "__main__":
    unittest.main()
