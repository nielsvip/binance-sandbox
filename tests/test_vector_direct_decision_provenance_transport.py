import datetime as dt
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tools.vector_direct_fill_export import export, export_decision_states
from tools.vector_direct_history_import import build_row, sync_decision_state_snapshots


class VectorDirectDecisionProvenanceTransportTests(unittest.TestCase):
    def test_export_and_history_row_preserve_exact_decision_provenance(self):
        provenance = {"state_sha256": "state-hash", "state_file": "decision_states/ab/state-hash.json",
                      "settings_sha256": "settings-hash", "engine_ast_sha256": "engine-hash",
                      "npz_tail": 1793408400, "state_ts": "2026-10-31T01:00:00Z"}
        actual_profit_check = {"evaluated": True, "entry_price": 10.25, "broker_mark": 9.5,
                               "profit_only_stop": True, "loss_close_authorized": False}
        stop_trigger_authorization = {"kind": "LIFECYCLE_BOUND_FIXED_STOP_CROSSED", "opened_at": "2026-10-31T01:00:00Z",
                                      "fixed_stop_level": 9.75, "broker_mark": 9.5,
                                      "source_file": "positions.json"}
        now = dt.datetime(2026, 10, 31, 12, 0, tzinfo=dt.timezone.utc).timestamp()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "data" / "vector_direct"
            out.mkdir(parents=True)
            order = {"event": "ORDER", "acct": "ang", "ss": "BTCUSDC_LONG", "act": "OPEN", "key": "k",
                     "why": "board holds", "ts": now - 1, "status": "SENT", "resp": {"orderId": "10001"},
                     "decision_provenance": provenance, "actual_profit_check": actual_profit_check,
                     "stop_trigger_authorization": stop_trigger_authorization}
            (out / "ledger_crypto_2026-10-31.jsonl").write_text(json.dumps(order) + "\n")
            fill = {"found": True, "order_id": "10001", "client_order_id": "vd123", "status": "FILLED",
                    "exec_qty": 2, "avg_price": 10, "quote": 20, "fill_ms": int((now - 1) * 1000), "src": "cached"}
            (out / "fills_crypto.json").write_text(json.dumps({"oid:ang:10001": fill}))
            output = []
            export("crypto", root, now - 2, 0, now=now, emit=output.append)
            emitted = next(json.loads(line) for line in output if "_stats" not in json.loads(line))
            self.assertEqual(emitted["decision_provenance"], provenance)
            self.assertEqual(emitted["actual_profit_check"], actual_profit_check)
            self.assertEqual(emitted["stop_trigger_authorization"], stop_trigger_authorization)
            history_row = build_row(emitted, "S1_VECTOR_DIRECT")
            self.assertEqual(history_row["decision_provenance"], provenance)
            self.assertEqual(history_row["actual_profit_check"], actual_profit_check)
            self.assertEqual(history_row["stop_trigger_authorization"], stop_trigger_authorization)

    def test_missing_decision_provenance_stays_absent(self):
        rec = {"acct": "ang", "ss": "BTCUSDC_LONG", "act": "OPEN", "fill": {"found": True, "order_id": "1",
               "exec_qty": 1, "avg_price": 1, "quote": 1, "fill_ms": 1793408400000}}
        row = build_row(rec, "S1_VECTOR_DIRECT")
        self.assertNotIn("decision_provenance", row)
        self.assertNotIn("actual_profit_check", row)
        self.assertNotIn("stop_trigger_authorization", row)

    def test_only_requested_content_addressed_decision_states_are_exported(self):
        state = {"bar_ts": 1793408400, "events_this_bar": [{"type": "OPEN", "reason": "REENTRY_LOWER_HIGH"}],
                 "settings_sha256": "settings", "engine_ast_sha256": "engine"}
        digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        rel = f"decision_states/{digest[:2]}/{digest}.json"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "vector_direct" / rel
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(state, indent=1))
            emitted = []
            stats = export_decision_states(root, [digest, "../untrusted"], emitted.append)
            self.assertEqual(stats["exported"], 1)
            self.assertEqual(stats["skipped"], 1)
            row = json.loads(emitted[0])
            self.assertEqual(row["state_sha256"], digest)
            self.assertEqual(row["state_file"], rel)
            self.assertEqual(row["state"], state)

    def test_importer_fetches_only_missing_state_hashes_and_verifies_before_cache(self):
        state = {"bar_ts": 1793408400, "events_this_bar": [{"type": "OPEN", "reason": "REENTRY_LOWER_HIGH"}]}
        digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        rel = f"decision_states/{digest[:2]}/{digest}.json"
        rec = {"venue": "crypto", "decision_provenance": {"state_sha256": digest, "state_file": rel}}
        calls = []
        def fetcher(venue, hashes):
            calls.append((venue, hashes))
            return [{"_decision_state": True, "state_sha256": digest, "state_file": rel, "state": state}]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = sync_decision_state_snapshots([rec], root, fetcher)
            self.assertEqual(result, {"requested": 1, "verified": 1, "rejected": 0, "invalid_references": 0})
            self.assertEqual(calls, [("crypto", [digest])])
            saved = root / rel
            self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(), digest)
            second = sync_decision_state_snapshots([rec], root, fetcher)
            self.assertEqual(second["requested"], 0)
            self.assertEqual(len(calls), 1)

            bad_root = root / "bad"
            bad = {"_decision_state": True, "state_sha256": digest, "state_file": rel, "state": {"tampered": True}}
            rejected = sync_decision_state_snapshots([rec], bad_root, lambda venue, hashes: [bad])
            self.assertEqual(rejected["rejected"], 1)
            self.assertFalse((bad_root / rel).exists())


if __name__ == "__main__":
    unittest.main()
