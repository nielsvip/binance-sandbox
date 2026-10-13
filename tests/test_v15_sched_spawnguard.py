"""Scheduler spawn-guard + fair rotation (USER 2026-10-11): every sym gets its turn, no dup stacks."""

import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from v15_fleet_scheduler import _place_order, _spawn_guard, side_cmd


class SpawnGuardShellTest(unittest.TestCase):
    def test_body_runs_when_idle_no_self_match(self):
        # REGRESSION 21:50Z: the body MUST contain the literal pilot invocation (as
        # production side_cmd emits) — the first guard matched its own bash -c wrapper
        # and self-skipped every launch fleet-wide.
        g = _spawn_guard(
            "ZZZGUARD_LONG",
            "30D",
            "echo BODY_RAN .venv/bin/python -u v15_pilot.py --sym-side ZZZGUARD_LONG",
        )
        out = subprocess.run(
            ["bash", "-c", g], capture_output=True, text=True, timeout=20
        )
        self.assertIn("BODY_RAN", out.stdout)
        self.assertNotIn("[spawnguard]", out.stdout)

    def test_body_skipped_when_running(self):
        fake = subprocess.Popen(
            [
                "bash",
                "-c",
                'exec -a ".venv/bin/python -u v15_pilot.py --sym-side ZZZGUARD_LONG" sleep 30',
            ]
        )
        try:
            time.sleep(0.5)
            g = _spawn_guard(
                "ZZZGUARD_LONG",
                "30D",
                "echo BODY_RAN .venv/bin/python -u v15_pilot.py --sym-side ZZZGUARD_LONG",
            )
            out = subprocess.run(
                ["bash", "-c", g], capture_output=True, text=True, timeout=20
            )
            self.assertIn(
                "[spawnguard] ZZZGUARD_LONG 30D already running", out.stdout
            )
            self.assertNotIn("BODY_RAN", out.stdout)
        finally:
            fake.terminate()
            fake.wait(timeout=10)

    def test_wrapper_cmdline_never_matches(self):
        # A bash wrapper whose cmdline merely CONTAINS the pilot string must not trip the guard.
        wrap = subprocess.Popen(
            [
                "bash",
                "-c",
                'echo ".venv/bin/python -u v15_pilot.py --sym-side ZZZGUARD_LONG" >/dev/null; sleep 30',
            ]
        )
        try:
            time.sleep(0.5)
            g = _spawn_guard("ZZZGUARD_LONG", "30D", "echo BODY_RAN")
            out = subprocess.run(
                ["bash", "-c", g], capture_output=True, text=True, timeout=20
            )
            self.assertIn("BODY_RAN", out.stdout)
            self.assertNotIn("[spawnguard]", out.stdout)
        finally:
            wrap.terminate()
            wrap.wait(timeout=10)

    def test_absolute_path_pilot_detected(self):
        # 2026-10-13: a 365-cycle-lane launcher uses absolute interpreter+script
        # paths (invisible to the old pattern) -> 5x ENJUSDT_SHORT stack on s2.
        # The guard must see those too, but never the ab3 A/B lane.
        fake = subprocess.Popen(
            [
                "bash",
                "-c",
                'exec -a "/home/niels/binance-sandbox/.venv/bin/python -u /home/niels/binance-sandbox/v15_pilot.py --sym-side ZZZGUARD_LONG" sleep 30',
            ]
        )
        try:
            time.sleep(0.5)
            g = _spawn_guard(
                "ZZZGUARD_LONG",
                "30D",
                "echo BODY_RAN",
            )
            out = subprocess.run(
                ["bash", "-c", g], capture_output=True, text=True, timeout=20
            )
            self.assertIn(
                "[spawnguard] ZZZGUARD_LONG 30D already running", out.stdout
            )
            self.assertNotIn("BODY_RAN", out.stdout)
        finally:
            fake.terminate()
            fake.wait(timeout=10)

    def test_ab3_lane_never_matches(self):
        # The V17 ab3 A/B lane (own sequencer, own out dirs) is not scheduler
        # territory: its pilots must never trip the 30D guard.
        g = _spawn_guard("ZZZGUARD_LONG", "30D", "echo BODY_RAN")
        self.assertNotIn("ab3", g)
        out = subprocess.run(
            ["bash", "-c", g], capture_output=True, text=True, timeout=20
        )
        self.assertIn("BODY_RAN", out.stdout)

    def test_window_patterns(self):
        self.assertIn(
            "tools/v15_365_cycle.py --sym-side",
            _spawn_guard("A_LONG", "365D", "true"),
        )
        self.assertIn(
            "tools/v15_365_cycle.py --sym-side",
            _spawn_guard("A_LONG", "REPAIR", "true"),
        )
        self.assertIn(
            "tools/v15_graph_search.py --symsides",
            _spawn_guard("A_LONG", "GS", "true"),
        )
        for w in ("30D", "365D", "REPAIR", "GS"):
            self.assertIn("^[^ ]*python", _spawn_guard("A_LONG", w, "true"))


class SideCmdGuardTest(unittest.TestCase):
    def test_30d_keeps_start_overrides_prefix(self):
        h = {"root": "/tmp/r", "template_dir": "SPREADSHEETS"}
        body = side_cmd(h, "AAA", "LONG", "30D", "/tmp/p", 1, 4)
        self.assertIn(
            "V15_START_OVERRIDES=$(test -f ~/v15_autopsy_first/AAA_LONG_autopsy_base.json",
            body,
        )

    def test_all_windows_guarded(self):
        h = {"root": "/tmp/r", "template_dir": "SPREADSHEETS"}
        for w in ("30D", "365D", "REPAIR", "GS"):
            body = side_cmd(h, "AAA", "SHORT", w, "/tmp/p", 1, 4)
            self.assertIn("pgrep -f", body)
            self.assertIn("[spawnguard]", body)


class PlaceOrderRotationTest(unittest.TestCase):
    def test_never_launched_first_within_tier(self):
        order = _place_order(
            [],
            [],
            ["AAA", "BBB"],
            {},
            set(),
            {"AAA_LONG|30D": 3},
            {"AAA_LONG|30D": 100.0},
        )
        self.assertEqual([s for s, _ in order], ["BBB", "AAA"])

    def test_chains_before_new_despite_attempts(self):
        order = _place_order(
            ["HELD"],
            [],
            ["NEW"],
            {},
            set(),
            {"HELD_LONG|30D": 5},
            {"HELD_LONG|30D": 50.0},
        )
        self.assertEqual([s for s, _ in order], ["HELD", "NEW"])

    def test_oldest_launch_breaks_attempt_ties(self):
        att = {"AAA_LONG|30D": 1, "BBB_LONG|30D": 1}
        lat = {"AAA_LONG|30D": 200.0, "BBB_LONG|30D": 100.0}
        order = _place_order([], [], ["AAA", "BBB"], {}, set(), att, lat)
        self.assertEqual([s for s, _ in order], ["BBB", "AAA"])

    def test_redo_outranks_rotation(self):
        order = _place_order(
            [], [], ["AAA", "BBB"], {}, {"AAA_LONG"}, {"BBB_LONG|30D": 0}, {}
        )
        self.assertEqual([s for s, _ in order], ["AAA", "BBB"])

    def test_tier_outranks_rotation(self):
        tiers = {"AAA_LONG": {"tier": "W"}, "AAA_SHORT": {"tier": "W"}}
        order = _place_order(
            [], [], ["AAA", "BBB"], tiers, set(), {"AAA_LONG|30D": 3}, {}
        )
        self.assertEqual([s for s, _ in order], ["AAA", "BBB"])

    def test_director_priority_before_rotation(self):
        order = _place_order(
            [], [], ["AAA", "BBB"], {}, set(), {"AAA_LONG|30D": 2}, {}, {"AAA"}
        )
        self.assertEqual([s for s, _ in order], ["AAA", "BBB"])


if __name__ == "__main__":
    unittest.main()
