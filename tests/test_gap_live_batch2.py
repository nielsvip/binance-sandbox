"""Gap batch2 2026-10-21 — DELTA_ENTRY + REENTRY B02/B09/B10 stocks live wiring.

Covers 6 switches (venue C, stocks vec-no-live):
  DELTA_ENTRY_Z_THRESHOLD / DELTA_ENTRY_ACCEL_THRESHOLD / DELTA_ENTRY_MIN_TF
  REENTRY_B02_BC156_BOTTOM_ENABLED / REENTRY_B09_SNAPBACK_ENABLED /
  REENTRY_B10_STOCH_REV_ENABLED
Live home: tradier_manage._b2_delta_entry_proposals +
_b2_reentry_b029010_proposals + flat-proposal branches (stocks only).
Vec reference: v12_quick_engine.compute_reentry_blocks B_DELTAENTRY + B02/B09/B10.
Executes the shipped helper AST (not a copy). No Sharpe/gain numbers.
Ledger proof on real NPZ (flip changes opens/closes) is a follow-up for the
S1 herd; these tests prove default-inertness, firing, and fail-open behavior.
"""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
TM = ROOT / "tradier_manage.py"


def _load(name):
    tree = ast.parse(TM.read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    ns = {}
    exec(compile(ast.fix_missing_locations(ast.Module([node], [])), str(TM), "exec"), ns)
    return ns[name]


DELTA = _load("_b2_delta_entry_proposals")
BBLOCKS = _load("_b2_reentry_b029010_proposals")


def _don(getter_on=()):
    def get(k, d):
        return True if k in getter_on else d
    return get


DELTA_ON = ("DELTA_ENGINE_ENABLED", "DELTA_ENTRY_ENABLED")


class DeltaDefaultInert(unittest.TestCase):
    def test_bare_defaults_inert(self):
        self.assertEqual(DELTA(lambda k, d: d, {"wt_velocity_1h": 9.0, "wt_velocity_4h": 5.0}, True), [])

    def test_engine_on_entry_off_inert(self):
        get = _don(("DELTA_ENGINE_ENABLED",))
        self.assertEqual(DELTA(get, {"wt_velocity_1h": 9.0, "wt_velocity_4h": 5.0}, True), [])

    def test_raising_getter_inert(self):
        def bad(k, d):
            raise RuntimeError("no cfg")
        self.assertEqual(DELTA(bad, {"wt_velocity_1h": 9.0}, True), [])

    def test_missing_velocity_no_fire(self):
        self.assertEqual(DELTA(_don(DELTA_ON), {}, True), [])

    def test_nan_velocity_no_fire(self):
        get = _don(DELTA_ON)
        self.assertEqual(DELTA(get, {"wt_velocity_1h": float("nan"), "wt_velocity_4h": 1.0}, True), [])


class DeltaFiring(unittest.TestCase):
    def test_long_fires(self):
        get = _don(DELTA_ON)
        ind = {"wt_velocity_1h": 3.0, "wt_velocity_4h": 1.0}
        self.assertEqual(DELTA(get, ind, True), ["DELTAENTRY_LONG"])

    def test_short_fires(self):
        get = _don(DELTA_ON)
        ind = {"wt_velocity_1h": -3.0, "wt_velocity_4h": -1.0}
        self.assertEqual(DELTA(get, ind, False), ["DELTAENTRY_SHORT"])

    def test_wrong_side_no_fire(self):
        get = _don(DELTA_ON)
        ind = {"wt_velocity_1h": -3.0, "wt_velocity_4h": -1.0}
        self.assertEqual(DELTA(get, ind, True), [])

    def test_accel_gate_blocks(self):
        def get(k, d):
            if k in DELTA_ON:
                return True
            if k == "DELTA_ENTRY_ACCEL_THRESHOLD":
                return 0.3
            return d
        ind = {"wt_velocity_1h": 3.0, "wt_velocity_4h": 1.0, "wt_acceleration_1h": 0.1}
        self.assertEqual(DELTA(get, ind, True), [])
        ind["wt_acceleration_1h"] = 0.5
        self.assertEqual(DELTA(get, ind, True), ["DELTAENTRY_LONG"])

    def test_min_tf4_d_leg(self):
        def get(k, d):
            if k in DELTA_ON:
                return True
            if k == "DELTA_ENTRY_MIN_TF":
                return 4
            return d
        ind = {"wt_velocity_1h": 3.0, "wt_velocity_4h": 1.0, "wt_velocity_D": -2.0}
        self.assertEqual(DELTA(get, ind, True), [])
        ind["wt_velocity_D"] = 2.0
        self.assertEqual(DELTA(get, ind, True), ["DELTAENTRY_LONG"])

    def test_atr_subgate(self):
        def get(k, d):
            if k in DELTA_ON or k == "DELTA_ATR_ENTRY_FILTER":
                return True
            return d
        ind = {"wt_velocity_1h": 3.0, "wt_velocity_4h": 1.0, "atr_1h": 1.0, "close": 100.0, "close_5m_prev": 100.1}
        self.assertEqual(DELTA(get, ind, True, 100.0), [])
        ind["close_5m_prev"] = 99.0
        self.assertEqual(DELTA(get, ind, True, 100.0), ["DELTAENTRY_LONG"])


def _bull(ind, side_long=True):
    for tf in ("3m", "15m", "1h", "4h"):
        ind["wt1_" + tf] = 10.0 if side_long else -10.0
        ind["wt2_" + tf] = -10.0 if side_long else 10.0


class BBlocksDefaultInert(unittest.TestCase):
    def test_bare_defaults_inert(self):
        ind = {"wt1_15m": -30.0, "wt_velocity_15m": 1.0, "k_3m": 20.0, "k_3m_prev": 10.0}
        _bull(ind)
        self.assertEqual(BBLOCKS(lambda k, d: d, ind, True), [])

    def test_explicit_false_inert(self):
        get = lambda k, d: False if k.endswith("_ENABLED") else d
        ind = {"wt1_15m": -30.0, "wt_velocity_15m": 1.0, "k_3m": 20.0, "k_3m_prev": 10.0}
        _bull(ind)
        self.assertEqual(BBLOCKS(get, ind, True), [])

    def test_raising_getter_inert(self):
        def bad(k, d):
            raise RuntimeError("no cfg")
        self.assertEqual(BBLOCKS(bad, {}, True), [])

    def test_missing_keys_no_fire(self):
        get = _don(("REENTRY_B02_BC156_BOTTOM_ENABLED",))
        self.assertEqual(BBLOCKS(get, {}, True), [])


class B02Firing(unittest.TestCase):
    def test_long_fires(self):
        get = _don(("REENTRY_B02_BC156_BOTTOM_ENABLED",))
        ind = {"wt1_15m": -30.0, "wt_velocity_15m": 1.5, "wt1_1h": 5.0, "wt2_1h": -5.0}
        _bull(ind)
        ind["wt1_15m"] = -30.0
        self.assertEqual(BBLOCKS(get, ind, True), ["B02_BC156_BOTTOM_LONG"])

    def test_short_fires(self):
        get = _don(("REENTRY_B02_BC156_BOTTOM_ENABLED",))
        ind = {"wt_velocity_15m": -1.5}
        _bull(ind, False)
        ind["wt1_15m"] = 30.0
        self.assertEqual(BBLOCKS(get, ind, False), ["B02_BC156_BOTTOM_SHORT"])

    def test_bull_count_blocks(self):
        get = _don(("REENTRY_B02_BC156_BOTTOM_ENABLED",))
        ind = {"wt1_15m": -30.0, "wt_velocity_15m": 1.5, "wt1_1h": 5.0, "wt2_1h": 6.0}
        for tf in ("3m", "15m", "1h", "4h"):
            ind["wt1_" + tf] = -5.0
            ind["wt2_" + tf] = 5.0
        self.assertEqual(BBLOCKS(get, ind, True), [])


class B09Firing(unittest.TestCase):
    def test_long_fires(self):
        get = _don(("REENTRY_B09_SNAPBACK_ENABLED",))
        ind = {"k_3m": 20.0, "k_3m_prev": 10.0, "wt1_5m": 3.0, "wt2_5m": -3.0}
        self.assertEqual(BBLOCKS(get, ind, True), ["B09_SNAPBACK_LONG"])

    def test_short_fires(self):
        get = _don(("REENTRY_B09_SNAPBACK_ENABLED",))
        ind = {"k_3m": 80.0, "k_3m_prev": 90.0, "wt1_5m": -3.0, "wt2_5m": 3.0}
        self.assertEqual(BBLOCKS(get, ind, False), ["B09_SNAPBACK_SHORT"])

    def test_no_cross_no_fire(self):
        get = _don(("REENTRY_B09_SNAPBACK_ENABLED",))
        ind = {"k_3m": 20.0, "k_3m_prev": 20.0, "wt1_5m": 3.0, "wt2_5m": -3.0}
        self.assertEqual(BBLOCKS(get, ind, True), [])


class B10Firing(unittest.TestCase):
    def test_long_fires(self):
        get = _don(("REENTRY_B10_STOCH_REV_ENABLED",))
        ind = {"k_3m": 20.0, "k_3m_prev": 10.0, "d_3m": 15.0, "k_15m": 30.0}
        self.assertEqual(BBLOCKS(get, ind, True), ["B10_STOCH_REV_LONG"])

    def test_short_fires(self):
        get = _don(("REENTRY_B10_STOCH_REV_ENABLED",))
        ind = {"k_3m": 80.0, "k_3m_prev": 90.0, "d_3m": 85.0, "k_15m": 70.0}
        self.assertEqual(BBLOCKS(get, ind, False), ["B10_STOCH_REV_SHORT"])

    def test_k15_confirm_blocks(self):
        get = _don(("REENTRY_B10_STOCH_REV_ENABLED",))
        ind = {"k_3m": 20.0, "k_3m_prev": 10.0, "d_3m": 15.0, "k_15m": 50.0}
        self.assertEqual(BBLOCKS(get, ind, True), [])


class FlatCallSite(unittest.TestCase):
    def test_gates_present_in_process_position(self):
        src = TM.read_text()
        for needle in ("_b2_delta_entry_proposals(", "_b2_reentry_b029010_proposals(", "[B2_DELTAENTRY]", "[B2_REENTRY_B]"):
            self.assertIn(needle, src)


if __name__ == "__main__":
    unittest.main()
