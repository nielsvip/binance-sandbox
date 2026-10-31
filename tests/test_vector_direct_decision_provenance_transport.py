import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from tools.vector_direct_fill_export import export
from tools.vector_direct_history_import import build_row


class VectorDirectDecisionProvenanceTransportTests(unittest.TestCase):
    def test_export_and_history_row_preserve_exact_decision_provenance(self):
        provenance = {"state_sha256": "state-hash", "state_file": "decision_states/ab/state-hash.json",
                      "settings_sha256": "settings-hash", "engine_ast_sha256": "engine-hash",
                      "npz_tail": 1793408400, "state_ts": "2026-10-31T01:00:00Z"}
        now = dt.datetime(2026, 10, 31, 12, 0, tzinfo=dt.timezone.utc).timestamp()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "data" / "vector_direct"
            out.mkdir(parents=True)
            order = {"event": "ORDER", "acct": "ang", "ss": "BTCUSDC_LONG", "act": "OPEN", "key": "k",
                     "why": "board holds", "ts": now - 1, "status": "SENT", "resp": {"orderId": "10001"},
                     "decision_provenance": provenance}
            (out / "ledger_crypto_2026-10-31.jsonl").write_text(json.dumps(order) + "\n")
            fill = {"found": True, "order_id": "10001", "client_order_id": "vd123", "status": "FILLED",
                    "exec_qty": 2, "avg_price": 10, "quote": 20, "fill_ms": int((now - 1) * 1000), "src": "cached"}
            (out / "fills_crypto.json").write_text(json.dumps({"oid:ang:10001": fill}))
            output = []
            export("crypto", root, now - 2, 0, now=now, emit=output.append)
            emitted = next(json.loads(line) for line in output if "_stats" not in json.loads(line))
            self.assertEqual(emitted["decision_provenance"], provenance)
            history_row = build_row(emitted, "S1_VECTOR_DIRECT")
            self.assertEqual(history_row["decision_provenance"], provenance)

    def test_missing_decision_provenance_stays_absent(self):
        rec = {"acct": "ang", "ss": "BTCUSDC_LONG", "act": "OPEN", "fill": {"found": True, "order_id": "1",
               "exec_qty": 1, "avg_price": 1, "quote": 1, "fill_ms": 1793408400000}}
        row = build_row(rec, "S1_VECTOR_DIRECT")
        self.assertNotIn("decision_provenance", row)


if __name__ == "__main__":
    unittest.main()
