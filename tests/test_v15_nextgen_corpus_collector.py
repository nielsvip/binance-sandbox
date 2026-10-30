import gzip
import json

import pytest
from tools import v15_nextgen_corpus_collector as collector


def test_preserved_row_schema_distinguishes_measured_zero_invalid_unknown_and_missing(tmp_path):
    path = tmp_path / "spill_old.tsv.gz"
    events = [{"e": "board", "b": "board", "s": "AMD_LONG", "t": "2026-10-01T00:00:00Z"}, {"e": "row", "b": "board", "s": "AMD_LONG", "rk": 7, "n": [0.0, 1], "y": [["F=1h", 0.0, 0], ["F=4h", 2.0, None], ["F=D", None, 1]]}, {"e": "id", "s": "AMD_LONG", "ni": 1000}, {"e": "settle", "s": "AMD_LONG"}]
    with gzip.open(path, "wt") as output:
        for event in events:
            output.write("t\t" + json.dumps(event) + "\n")
    report = collector.collect(tmp_path / "report", [path], max_events=0, emit=True)
    counts = report["spills"][0]["counters"]
    assert counts["naked_delta_vs_cumulative:measured_zero"] == 1
    assert counts["cell_marginal_vs_naked:invalid_flag"] == 1
    assert counts["cell_marginal_vs_naked:unknown_validity"] == 1
    assert counts["cell_marginal_vs_naked:missing_or_nonfinite_delta"] == 1
    assert report["quality_training_verified"] is False
    assert report["spills"][0]["sampled_not_complete_counts"] is False
    with gzip.open(tmp_path / "report/observations_research_only.jsonl.gz", "rt") as source:
        observed = [json.loads(line) for line in source]
    assert len(observed) == 4 and all(row["quality_training_verified"] is False for row in observed)
    assert all(row["source_sha256"] == collector.digest(path) for row in observed)


def test_historical_identity_uses_exact_original_npz_ledger_without_current_book_or_suffix_guessing(tmp_path):
    document = {"symside": "AMD_LONG", "npz_id": {"path": "/original/AMD.npz", "md5": "a" * 32}, "done": {"real": {"npz": "ts1791290700.0_n2857", "delta_invalid": False, "naked_delta": 2.0}, "ambiguous": {"delta": 0.0}, "invalid_none": {"delta_invalid": None, "naked_delta": 0.0}, "identity": {"is_running": True, "delta": 0.0}}}
    path = tmp_path / "AMD_LONG_v14_progress.json"
    path.write_text(json.dumps(document))
    index = tmp_path / "index.json"
    index.write_text(json.dumps([{"s": "AMD_LONG", "file": str(path)}]))
    report = collector.scan_progress_index(index)
    row = report["sources"][0]
    assert row["identity_tier"] == "exact_original_npz_symbol_ledger"
    assert row["counters"]["naked_delta_vs_cumulative:positive"] == 1
    assert row["counters"]["naked_delta_vs_cumulative:unknown_validity"] == 2
    assert row["counters"]["identity_not_gain_negative"] == 1
    document["symside"] = "AMD_BF_LONG"
    assert collector.progress_identity("AMD_BF_LONG", document) == "npz_symbol_mismatch_no_suffix_guessing"


def test_bounded_counts_are_marked_and_changed_source_refused(tmp_path, monkeypatch):
    path = tmp_path / "spill_old.tsv"
    path.write_text('t\t{"e":"id","s":"AMD_LONG"}\nt\t{"e":"id","s":"AMD_LONG"}\n')
    assert collector.scan_spill(path, max_events=1)["sampled_not_complete_counts"] is True
    hashes = iter(["a" * 64, "b" * 64])
    monkeypatch.setattr(collector, "digest", lambda source: next(hashes))
    with pytest.raises(ValueError, match="changed during collection"):
        collector.collect(tmp_path / "failed", [path], max_events=0)
    assert json.loads((tmp_path / "failed/inventory.json").read_text())["status"] == "failed_ineligible"
