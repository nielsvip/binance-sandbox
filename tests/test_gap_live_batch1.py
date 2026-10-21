"""Gap batch1 2026-10-21 — REENTRY_15M HTF trio live wiring (ENTRY/REENTRY scope).

Covers 6 template switches (C+D shared):
  REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED / _MIN_TFS
  REENTRY_15M_LRL_PULLBACK_HTF_ENABLED / _MIN_TFS
  REENTRY_15M_BB1H_LOW_BOUNCE_HTF_ENABLED / _MIN_TFS
Live homes: ez_manage._r15_15m_htf_proposals + flat-proposal branch (crypto),
tradier_manage._r15_15m_htf_proposals + flat-proposal branch (stocks).
Vec reference: v12_quick_engine.compute_reentry_blocks 15m-HTF blocks.
Executes the shipped helper AST (not a copy). No Sharpe/gain numbers.
"""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
EZ = ROOT / "ez_manage.py"
TM = ROOT / "tradier_manage.py"
V12 = ROOT / "v12_quick_engine.py"


def _load_r15(path):
    tree = ast.parse(Path(path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_r15_15m_htf_proposals")
    ns = {}
    exec(compile(ast.fix_missing_locations(ast.Module([node], [])), str(path), "exec"), ns)
    return ns["_r15_15m_htf_proposals"]


EZ_R15 = _load_r15(EZ)
TM_R15 = _load_r15(TM)


def _wt(ind, tf, a, b):
    ind["wt1_" + tf] = a
    ind["wt2_" + tf] = b


def _bull3(ind):
    for tf in ("15m", "1h", "4h"):
        _wt(ind, tf, 10.0, -10.0)
    _wt(ind, "D", -10.0, 10.0)


def _bear3(ind):
    for tf in ("15m", "1h", "4h"):
        _wt(ind, tf, -10.0, 10.0)
    _wt(ind, "D", 10.0, -10.0)


class R15DefaultInert(unittest.TestCase):
    def test_ez_default_inert(self):
        fires, _ = EZ_R15(lambda k, d: d, {"close_15m": 100.0}, None, True, 100.0)
        self.assertEqual(fires, [])

    def test_tm_default_inert(self):
        fires, _ = TM_R15(lambda k, d: d, {"close_15m": 100.0}, None, False, 100.0)
        self.assertEqual(fires, [])

    def test_explicit_false_board(self):
        get = lambda k, d: False if k.endswith("_ENABLED") else d
        ind = {"close_15m": 101.0, "bb_middle_15m": 100.0, "bb_pct_b_1h": 0.5}
        for fn in (EZ_R15, TM_R15):
            fires, _ = fn(get, ind, {"close_15m": 99.0, "mid_15m": 100.0, "bb_pct_1h": 0.1, "lrl_15m": 0.1}, True, 101.0)
            self.assertEqual(fires, [])


class R15DcBasisCross(unittest.TestCase):
    def _get(self, d=None):
        return lambda k, dd: True if k == "REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED" else (dd if d is None else d)

    def test_long_fires_both_helpers(self):
        ind = {"close_15m": 101.0, "bb_middle_15m": 100.0, "bb_pct_b_1h": 0.5}
        _bull3(ind)
        prev = {"close_15m": 99.0, "mid_15m": 100.0, "bb_pct_1h": 0.5, "lrl_15m": 0.5}
        for fn in (EZ_R15, TM_R15):
            fires, _ = fn(self._get(), ind, prev, True, 101.0)
            self.assertEqual(fires, ["REENTRY_15M_DC_BASIS_CROSS_HTF_LONG"])

    def test_short_fires(self):
        ind = {"close_15m": 99.0, "bb_middle_15m": 100.0, "bb_pct_b_1h": 0.5}
        _bear3(ind)
        prev = {"close_15m": 101.0, "mid_15m": 100.0, "bb_pct_1h": 0.5, "lrl_15m": 0.5}
        fires, _ = EZ_R15(self._get(), ind, prev, False, 99.0)
        self.assertEqual(fires, ["REENTRY_15M_DC_BASIS_CROSS_HTF_SHORT"])

    def test_bb_extreme_blocks(self):
        ind = {"close_15m": 101.0, "bb_middle_15m": 100.0, "bb_pct_b_1h": 0.9}
        _bull3(ind)
        prev = {"close_15m": 99.0, "mid_15m": 100.0, "bb_pct_1h": 0.9, "lrl_15m": 0.5}
        fires, _ = TM_R15(self._get(), ind, prev, True, 101.0)
        self.assertEqual(fires, [])

    def test_min_tfs_binds(self):
        ind = {"close_15m": 101.0, "bb_middle_15m": 100.0, "bb_pct_b_1h": 0.5}
        _bull3(ind)
        prev = {"close_15m": 99.0, "mid_15m": 100.0, "bb_pct_1h": 0.5, "lrl_15m": 0.5}
        get4 = lambda k, d: 4 if k.endswith("_MIN_TFS") else (True if k.endswith("_ENABLED") else d)
        fires, _ = EZ_R15(get4, ind, prev, True, 101.0)
        self.assertEqual(fires, [])
        get3 = lambda k, d: 3 if k.endswith("_MIN_TFS") else (True if k.endswith("_ENABLED") else d)
        fires, _ = EZ_R15(get3, ind, prev, True, 101.0)
        self.assertEqual(len(fires), 1)

    def test_mid_fallback_chain(self):
        ind = {"close_15m": 101.0, "dc_basis_15m": 100.0, "bb_pct_b_1h": 0.5}
        _bull3(ind)
        prev = {"close_15m": 99.0, "mid_15m": 100.0, "bb_pct_1h": 0.5, "lrl_15m": 0.5}
        fires, _ = TM_R15(self._get(), ind, prev, True, 101.0)
        self.assertEqual(fires, ["REENTRY_15M_DC_BASIS_CROSS_HTF_LONG"])


class R15LrlBb1h(unittest.TestCase):
    def test_lrl_long(self):
        get = lambda k, d: True if k == "REENTRY_15M_LRL_PULLBACK_HTF_ENABLED" else d
        ind = {"lrL_pct_b_15m": 0.35}
        _bull3(ind)
        prev = {"close_15m": 100.0, "mid_15m": 100.0, "bb_pct_1h": 0.5, "lrl_15m": 0.10}
        fires, _ = EZ_R15(get, ind, prev, True, 100.0)
        self.assertEqual(fires, ["REENTRY_15M_LRL_PULLBACK_HTF_LONG"])

    def test_lrl_short(self):
        get = lambda k, d: True if k == "REENTRY_15M_LRL_PULLBACK_HTF_ENABLED" else d
        ind = {"lrL_pct_b_15m": 0.65}
        _bear3(ind)
        prev = {"close_15m": 100.0, "mid_15m": 100.0, "bb_pct_1h": 0.5, "lrl_15m": 0.90}
        fires, _ = TM_R15(get, ind, prev, False, 100.0)
        self.assertEqual(fires, ["REENTRY_15M_LRL_PULLBACK_HTF_SHORT"])

    def test_bb1h_long(self):
        get = lambda k, d: True if k == "REENTRY_15M_BB1H_LOW_BOUNCE_HTF_ENABLED" else d
        ind = {"bb_pct_b_1h": 0.30, "close_15m": 101.0, "bb_lower_1h": 100.0}
        _bull3(ind)
        prev = {"close_15m": 99.0, "mid_15m": 100.0, "bb_pct_1h": 0.10, "lrl_15m": 0.5}
        fires, _ = EZ_R15(get, ind, prev, True, 101.0)
        self.assertEqual(fires, ["REENTRY_15M_BB1H_LOW_BOUNCE_HTF_LONG"])

    def test_bb1h_short_band_and_pctonly(self):
        get = lambda k, d: True if k == "REENTRY_15M_BB1H_LOW_BOUNCE_HTF_ENABLED" else d
        prev = {"close_15m": 101.0, "mid_15m": 100.0, "bb_pct_1h": 0.90, "lrl_15m": 0.5}
        ind = {"bb_pct_b_1h": 0.70, "close_15m": 99.0, "bb_lower_1h": 100.0}
        _bear3(ind)
        fires, _ = TM_R15(get, ind, prev, False, 99.0)
        self.assertEqual(fires, ["REENTRY_15M_BB1H_LOW_BOUNCE_HTF_SHORT"])
        ind2 = dict(ind)
        ind2["bb_lower_1h"] = 0
        fires, _ = TM_R15(get, ind2, prev, False, 99.0)
        self.assertEqual(fires, ["REENTRY_15M_BB1H_LOW_BOUNCE_HTF_SHORT"])


class R15Robustness(unittest.TestCase):
    def test_first_seen_cannot_cross(self):
        get = lambda k, d: True if k.endswith("_ENABLED") else d
        ind = {"close_15m": 101.0, "bb_middle_15m": 100.0, "bb_pct_b_1h": 0.5, "lrL_pct_b_15m": 0.35, "bb_lower_1h": 100.0}
        _bull3(ind)
        for fn in (EZ_R15, TM_R15):
            fires, new_prev = fn(get, ind, None, True, 101.0)
            self.assertEqual(fires, [])
            self.assertEqual(new_prev["close_15m"], 101.0)

    def test_empty_and_nan_inert(self):
        get = lambda k, d: True if k.endswith("_ENABLED") else d
        for fn in (EZ_R15, TM_R15):
            fires, _ = fn(get, {}, None, True)
            self.assertEqual(fires, [])
            fires, _ = fn(get, {"close_15m": float("nan"), "bb_pct_b_1h": float("nan")}, None, True)
            self.assertEqual(fires, [])

    def test_prev_threading_detects_cross(self):
        get = lambda k, d: True if k == "REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED" else d
        ind1 = {"close_15m": 99.0, "bb_middle_15m": 100.0, "bb_pct_b_1h": 0.5}
        _bull3(ind1)
        _, prev = EZ_R15(get, ind1, None, True, 99.0)
        ind2 = {"close_15m": 101.0, "bb_middle_15m": 100.0, "bb_pct_b_1h": 0.5}
        _bull3(ind2)
        fires, _ = EZ_R15(get, ind2, prev, True, 101.0)
        self.assertEqual(fires, ["REENTRY_15M_DC_BASIS_CROSS_HTF_LONG"])


class R15WiringPresence(unittest.TestCase):
    def test_ez_branch_calls_helper_flat_and_open(self):
        src = EZ.read_text()
        self.assertIn("_r15_15m_htf_proposals(lambda _k, _d: _psym_get(_sym_z, _side_z, _k, _d)", src)
        self.assertIn('await queue_trade_action(order_queue, trade_manager, position_key, "OPEN", _r15_reason_z, 80.0)', src)
        self.assertIn('REENTRY_15M_BB1H_LOW_BOUNCE_HTF_ENABLED', src)

    def test_tm_branch_calls_helper_flat_and_open(self):
        src = TM.read_text()
        self.assertIn("_r15_15m_htf_proposals(_r15_ct, i or {}", src)
        self.assertIn('await queue_trade_action(order_queue, trade_manager, position_key, "OPEN", _r15_reason_t, 80.0)', src)
        self.assertIn("(not has_position)", src)

    def test_vec_reference_present(self):
        src = V12.read_text()
        self.assertIn("def compute_reentry_blocks", src)
        for sw in ("REENTRY_15M_DC_BASIS_CROSS_HTF_ENABLED", "REENTRY_15M_LRL_PULLBACK_HTF_ENABLED", "REENTRY_15M_BB1H_LOW_BOUNCE_HTF_ENABLED"):
            self.assertIn('getattr(cfg, "%s", False)' % sw, src)


if __name__ == "__main__":
    unittest.main()
