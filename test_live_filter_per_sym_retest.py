"""test_live_filter_per_sym_retest.py — EACH live filter skip not in vector → per-sym/side A/B retest, leave ON if pos delta else OFF."""
import json, tempfile, unittest
from pathlib import Path

class TestLiveFilterPerSymRetest(unittest.TestCase):
    def test_registry_maps_blocked_to_config(self):
        from live_filter_per_sym_retest import LIVE_FILTER_REGISTRY, _registry_lookup
        self.assertIn("BLOCKED_COUNTER_TREND", LIVE_FILTER_REGISTRY)
        self.assertIn("BLOCKED_MTF_NO_ARMED_STATE", LIVE_FILTER_REGISTRY)
        cfg, vec = _registry_lookup("BLOCKED_COUNTER_TREND_1H_AGAINST_LONG")
        self.assertEqual(cfg, "COUNTER_TREND_ADD_BLOCK_ENABLED")
        self.assertFalse(vec)
        cfg2, _ = _registry_lookup("BLOCKED_TOP_OF_RANGE_LONG_thr0.95")
        self.assertEqual(cfg2, "TOP_OF_RANGE_BLOCK_ENABLED")

    def test_maybe_queue_only_if_not_in_vector(self):
        import live_filter_per_sym_retest as m
        # fresh temp queue
        tmpq = Path(tempfile.gettempdir()) / f"test_queue_{m.QUEUE_PATH.stem}.jsonl"
        orig_q = m.QUEUE_PATH
        orig_manifest = m.MANIFEST_PATH
        try:
            m.QUEUE_PATH = tmpq
            if tmpq.exists(): tmpq.unlink()
            # manifest says filter NOT in vector → should queue
            m.MANIFEST_PATH = Path("/tmp/nonexistent_manifest.json")
            # ensure registry entry is not covered
            queued = m.maybe_queue_filter_retest("BTCUSDC", "LONG", "BLOCKED_COUNTER_TREND_1H_AGAINST_LONG", "ang")
            self.assertTrue(queued)
            self.assertTrue(tmpq.exists())
            content = tmpq.read_text()
            self.assertIn("BTCUSDC", content)
            self.assertIn("COUNTER_TREND_ADD_BLOCK_ENABLED", content)
            # second call with same filter but manifest says already covered → should NOT queue
            # write manifest that marks it covered
            man_path = Path(tempfile.gettempdir()) / "test_manifest.json"
            man_path.write_text(json.dumps({
                "filters": {"COUNTER_TREND_ADD_BLOCK_ENABLED": {"vector_covered": True}},
                "per_sym": {}
            }))
            m.MANIFEST_PATH = man_path
            tmpq.write_text("")  # clear
            queued2 = m.maybe_queue_filter_retest("BTCUSDC", "LONG", "BLOCKED_COUNTER_TREND_1H_AGAINST_LONG", "ang")
            self.assertFalse(queued2, "should not queue if already in vector at test time")
            self.assertEqual(tmpq.read_text().strip(), "")
        finally:
            m.QUEUE_PATH = orig_q
            m.MANIFEST_PATH = orig_manifest
            try: tmpq.unlink()
            except: pass
            try: Path(tempfile.gettempdir() + "/test_manifest.json").unlink()
            except: pass

    def test_atomic_update_per_sym_override_on_off(self):
        import live_filter_per_sym_retest as m
        tmpcfg = Path(tempfile.gettempdir()) / "test_per_sym_active.json"
        orig_cfg = m.ACTIVE_CFG
        try:
            m.ACTIVE_CFG = tmpcfg
            if tmpcfg.exists(): tmpcfg.unlink()
            tmpcfg.write_text(json.dumps({"_meta": {}, "BTCUSDC_LONG": {"side": "LONG", "overrides": {"COUNTER_TREND_ADD_BLOCK_ENABLED": True}}}))
            m._atomic_update_active_config("BTCUSDC_LONG", "TOP_OF_RANGE_BLOCK_ENABLED", enable=False)
            cfg = json.loads(tmpcfg.read_text())
            self.assertEqual(cfg["BTCUSDC_LONG"]["_filter_decision"]["TOP_OF_RANGE_BLOCK_ENABLED"]["enabled"], False)
            m._atomic_update_active_config("BTCUSDC_LONG", "TOP_OF_RANGE_BLOCK_ENABLED", enable=True)
            cfg2 = json.loads(tmpcfg.read_text())
            self.assertEqual(cfg2["BTCUSDC_LONG"]["_filter_decision"]["TOP_OF_RANGE_BLOCK_ENABLED"]["enabled"], True)
            m._atomic_update_active_config("ETHUSDC_SHORT", "MTF_ARMED_ENTRY_ENABLED", enable=False)
            cfg3 = json.loads(tmpcfg.read_text())
            self.assertEqual(cfg3["ETHUSDC_SHORT"]["_filter_decision"]["MTF_ARMED_ENTRY_ENABLED"]["enabled"], False)
        finally:
            m.ACTIVE_CFG = orig_cfg
            try: tmpcfg.unlink()
            except: pass

    def test_process_queue_dedup_and_delta_decision(self):
        import live_filter_per_sym_retest as m
        tmpq = Path(tempfile.gettempdir()) / "test_q_dedup.jsonl"
        tmpcfg = Path(tempfile.gettempdir()) / "test_q_cfg.json"
        orig_q, orig_cfg = m.QUEUE_PATH, m.ACTIVE_CFG
        orig_compute = m._compute_delta_sharpe
        try:
            m.QUEUE_PATH = tmpq
            m.ACTIVE_CFG = tmpcfg
            tmpcfg.write_text(json.dumps({"_meta": {}}))
            tmpq.write_text(
                json.dumps({"sym": "BTCUSDC","side":"LONG","sym_side":"BTCUSDC_LONG","filter_key":"COUNTER_TREND_ADD_BLOCK_ENABLED","blocked_reason":"BLOCKED_COUNTER_TREND_1H","account":"ang"})+"\n"+
                json.dumps({"sym": "BTCUSDC","side":"LONG","sym_side":"BTCUSDC_LONG","filter_key":"COUNTER_TREND_ADD_BLOCK_ENABLED","blocked_reason":"BLOCKED_COUNTER_TREND_1H","account":"inf"})+"\n"+
                json.dumps({"sym": "ETHUSDC","side":"SHORT","sym_side":"ETHUSDC_SHORT","filter_key":"MTF_ARMED_ENTRY_ENABLED","blocked_reason":"BLOCKED_MTF_NO_ARMED_STATE","account":"ang"})+"\n"
            )
            def fake_delta(sym, side, fk, years_back=0.5):
                if sym=="BTCUSDC": return 0.02
                return -0.03
            m._compute_delta_sharpe = fake_delta
            dec = m.process_queue(years_back=0.5, dry_run=False)
            self.assertEqual(len(dec), 2)
            btc = [d for d in dec if d["sym_side"]=="BTCUSDC_LONG"][0]
            eth = [d for d in dec if d["sym_side"]=="ETHUSDC_SHORT"][0]
            self.assertTrue(btc["enable"], "pos delta → leave ON")
            self.assertFalse(eth["enable"], "neg delta → switch OFF")
            cfg = json.loads(tmpcfg.read_text())
            self.assertEqual(cfg["BTCUSDC_LONG"]["_filter_decision"]["COUNTER_TREND_ADD_BLOCK_ENABLED"]["enabled"], True)
            self.assertEqual(cfg["ETHUSDC_SHORT"]["_filter_decision"]["MTF_ARMED_ENTRY_ENABLED"]["enabled"], False)
            self.assertEqual(tmpq.read_text().strip(), "")
        finally:
            m.QUEUE_PATH = orig_q
            m.ACTIVE_CFG = orig_cfg
            m._compute_delta_sharpe = orig_compute
            try: tmpq.unlink()
            except: pass
            try: tmpcfg.unlink()
            except: pass

    def test_retest_all_sets_global_to_max_avg_and_per_sym(self):
        import live_filter_per_sym_retest as m
        tmpcfg = Path(tempfile.gettempdir()) / "test_retest_all_cfg.json"
        orig_cfg = m.ACTIVE_CFG
        orig_compute = m._compute_delta_sharpe
        # need at least 4 sym_sides
        try:
            m.ACTIVE_CFG = tmpcfg
            tmpcfg.write_text(json.dumps({"_meta": {}, "AAAUSDC_LONG": {"side":"LONG","overrides":{}}, "BBBUSDC_LONG": {"side":"LONG","overrides":{}}, "CCCUSDC_LONG": {"side":"LONG","overrides":{}}, "DDDUSDC_LONG": {"side":"LONG","overrides":{}}}))
            # mock: 1 pos, 3 neg -> avg negative -> global OFF
            def fake_delta(sym, side, fk, years_back=0.5):
                return 0.02 if sym=="AAAUSDC" else -0.02
            m._compute_delta_sharpe = fake_delta
            # ensure registry has tight filters
            res = m.retest_all_filters(years_back=0.5, dry_run=True, max_sym_sides=4)
            self.assertTrue(len(res)>0)
            for r in res:
                # tight filters should be OFF when avg negative
                if r["filter_key"]=="COUNTER_TREND_ADD_BLOCK_ENABLED":
                    self.assertFalse(r["global_enable"])
                    self.assertEqual(r["pos_cnt"], 1)
                    self.assertEqual(r["neg_cnt"], 3)
            # now dry_run False should set per_sym True for the one positive
            m._compute_delta_sharpe = fake_delta
            res2 = m.retest_all_filters(years_back=0.5, dry_run=False, max_sym_sides=4)
            cfg = json.loads(tmpcfg.read_text())
            # AAA should have True override (global OFF, needs True to stay ON)
            self.assertTrue(cfg["AAAUSDC_LONG"]["overrides"].get("COUNTER_TREND_ADD_BLOCK_ENABLED") is True)
            self.assertTrue(cfg["AAAUSDC_LONG"]["_filter_decision"]["COUNTER_TREND_ADD_BLOCK_ENABLED"]["enabled"] is True)
        finally:
            m.ACTIVE_CFG = orig_cfg
            m._compute_delta_sharpe = orig_compute
            try: tmpcfg.unlink()
            except: pass

    def test_ez_manage_hooks_present(self):
        src = open("ez_manage.py").read()
        # each major filter BLOCKED must queue retest
        self.assertIn("live_filter_per_sym_retest", src)
        self.assertIn("BLOCKED_COUNTER_TREND", src)
        self.assertIn("BLOCKED_TOP_OF_RANGE", src)
        self.assertIn("BLOCKED_GR_FILTER", src)
        self.assertIn("BLOCKED_MTF_NO_ARMED_STATE", src)
        self.assertIn("BLOCKED_EXIT_LH_LL", src)

if __name__ == "__main__":
    unittest.main()
