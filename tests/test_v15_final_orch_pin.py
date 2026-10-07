"""T0 engine pin: unanimous-pin, split-refuse, drift-exclude, export-stamp (mocked hosts)."""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.v15_final_orch as FO

HOSTS = [{"name": "s1", "ssh": ["s1-int"]}, {"name": "s2", "ssh": ["s2"]}]
SNAP = {"stocks": ["CRWD"], "crypto": [], "non_shortable": ["CRWD"]}
SCAN = {"CRWD_LONG": {"round": 26, "mtime": 100.0, "path": "/p/x.json", "final_gain": 5.0, "defaults_round": 1}}
W30 = {"gain_pct": 5.0, "trades": 66, "valid": True, "tim_pct": 40.0, "max_dd_pct": 8.0}
W365 = {"gain_pct": 12.0, "trades": 790, "valid": True, "tim_pct": 60.0, "max_dd_pct": 15.0}


def vd():
    return {"both_ok": True, "final_progress": "/fp", "w30": dict(W30), "w365": dict(W365), "overrides": {"A": 1}, "span_365_days": 365.0}


def collect_res(engine, verdicts):
    return ({"verdicts": verdicts, "parity": {}, "confirmed": {}, "confirm_started": {},
             "current": {"engine_md5": engine, "updated_utc": "t", "last_deploy": "d"}}, "")


def fake_host_json(engines, verdicts_by_host):
    def f(h, args, timeout=900):
        if args.startswith("host-collect"):
            return collect_res(engines[h["name"]], verdicts_by_host.get(h["name"], {}))
        return dict(SCAN), ""

    return f


class TestT0Pin(unittest.TestCase):
    def test_assemble_pins_unanimous(self):
        st = {}
        with patch.object(FO, "host_json", side_effect=fake_host_json({"s1": "E1", "s2": "E1"}, {})):
            self.assertTrue(FO.assemble(st, SNAP, HOSTS, lambda *a: None, True))
        self.assertEqual(st["final"]["pin"]["engine_md5"], "E1")

    def test_assemble_refuses_split(self):
        st = {}
        with patch.object(FO, "host_json", side_effect=fake_host_json({"s1": "E1", "s2": "E2"}, {})):
            self.assertFalse(FO.assemble(st, SNAP, HOSTS, lambda *a: None, True))
        self.assertNotIn("pin", st["final"])

    def test_drive_excludes_drifted(self):
        st = {"final": {"pin": {"engine_md5": "E1", "per_host": {"s1": "E1", "s2": "E1"}}}}
        v = {"s1": {"CRWD_LONG": vd()}, "s2": {"SMCI_LONG": vd()}}
        with patch.object(FO, "host_json", side_effect=fake_host_json({"s1": "E1", "s2": "E2"}, v)):
            c = FO.drive(st, SNAP, HOSTS, lambda *a: None, True)
        self.assertIn("CRWD_LONG", c["verdicts"])
        self.assertNotIn("SMCI_LONG", c["verdicts"])
        self.assertIn("s2", st["final"]["drift"])

    def test_export_stamps_pin(self):
        st = {"final": {"pin": {"engine_md5": "ABCDEF123456", "at": "t"}}}
        out = FO.export(st, {"stocks": [], "crypto": []}, {"verdicts": {}, "parity": {}, "confirmed": {}}, False)
        self.assertEqual(out["engine_md5"], "ABCDEF12")
        self.assertEqual(out["pin"]["engine_md5"], "ABCDEF123456")

    def test_export_fallback_unpinned(self):
        st = {"final": {}}
        out = FO.export(st, {"stocks": [], "crypto": []}, {"verdicts": {}, "parity": {}, "confirmed": {}}, False)
        exp = (FO.jload(FO.ROOT / "data/engine_deploy/CURRENT.json", {}).get("engine_md5") or "")[:8]
        self.assertEqual(out["engine_md5"], exp)
        self.assertIsNone(out["pin"])


if __name__ == "__main__":
    unittest.main()
