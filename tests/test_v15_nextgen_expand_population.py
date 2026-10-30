import gzip
import hashlib
import io
import json
import tarfile

import pytest
from tools import v15_nextgen_expand_population as expanded


def inputs(tmp_path):
    symbols = {"CRYPTO_LONG": "BTCUSDC_LONG", "CRYPTO_SHORT": "BTCUSDC_SHORT", "STOCKS_LONG": "AAPL_LONG", "STOCKS_SHORT": "AAPL_SHORT"}
    sources, templates = [], {}
    archive = tmp_path / "originals.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        for category, symbol in symbols.items():
            rows = {"EXIT_STRUCTURAL!3:ROW_ENABLED=True": {"npz": "ts100_n50", "delta_invalid": False, "naked_delta": 2.0, "yellows": {"FILTER_TF=1h": 7.0}, "cumulative_before": 10.0}, "EXIT_STRUCTURAL!4:ZERO_ENABLED=True": {"npz": "ts100_n50", "delta_invalid": False, "naked_delta": 0.0, "yellows": {"FILTER_TF=1h": 0.0}, "cumulative_before": 10.0}, "EXIT_STRUCTURAL!5:BAD_ENABLED=True": {"npz": "ts100_n50", "delta_invalid": True, "naked_delta": 9.0, "yellows": {"FILTER_TF=1h": 12.0}, "cumulative_before": 10.0}, "EXIT_STRUCTURAL!6:UNKNOWN_ENABLED=True": {"npz": "ts100_n50", "naked_delta": 1.0, "cumulative_before": 10.0}, "EXIT_STRUCTURAL!7:LATE_ENABLED=True": {"npz": "ts200_n50", "delta_invalid": False, "naked_delta": 1.0, "cumulative_before": 10.0}, "EXIT_STRUCTURAL!8:NO_NPZ_ENABLED=True": {"delta_invalid": False, "naked_delta": 1.0, "cumulative_before": 10.0}}
            document = {"symside": symbol, "npz_id": {"path": "/original/" + symbol.rsplit("_", 1)[0] + ".npz", "md5": "a" * 32}, "window_days": 30, "done": rows}
            payload = json.dumps(document).encode()
            for duplicate in range(2 if category == "CRYPTO_LONG" else 1):
                name = f"source_{category}_{duplicate}.json"
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                output.addfile(info, io.BytesIO(payload))
                sources.append({"archive_member": name, "source_file": "/original/" + name, "source_sha256": hashlib.sha256(payload).hexdigest(), "symbol_side": symbol, "selection": "historical_exact_ledger"})
            templates[category] = {"tabs": {"EXIT_STRUCTURAL": {"rows": [[3, "ROW_ENABLED", "True", False]], "yellows": ["FILTER_TF=1h"]}}}
    manifest = tmp_path / "sources.json"
    manifest.write_text(json.dumps({"archive_sha256": expanded.corpus.digest(archive), "sources": sources, "cutoffs_epoch": {category: 200 for category in symbols}}))
    return manifest, archive, templates


def test_official_converter_marginal_targets_strict_validity_cutoff_dedupe_and_outside_template(tmp_path):
    manifest, archive, templates = inputs(tmp_path)
    report = expanded.convert(manifest, archive, tmp_path / "population", templates)
    assert report["status"] == "expanded_population_converted_not_training_approved"
    with gzip.open(tmp_path / "population/spill_expanded_explicit_progress.tsv.gz", "rt") as source:
        events = [json.loads(line.partition("\t")[2]) for line in source]
    rows = [event for event in events if event["e"] == "row"]
    assert len(rows) == 8
    measured = next(event for event in rows if event["k"] == "ROW_ENABLED=True")
    assert measured["n"][0] == 2.0 and measured["y"][0][1] == 5.0
    assert all(event.get("source_sha256") and event.get("progress_key") and event.get("explicit_row_npz") for event in rows)
    assert any(event["template_scope"] == "outside_frozen_template_preserved" for event in rows)
    assert all(event["k"] not in ("BAD_ENABLED=True", "UNKNOWN_ENABLED=True", "LATE_ENABLED=True", "NO_NPZ_ENABLED=True") for event in rows)
    for category, counters in report["counters_by_category"].items():
        assert counters["eligible_naked"] == counters["eligible_marginal_cells"] == 2
        assert counters["measured_zero_naked"] == counters["measured_zero_marginal"] == 1
    assert report["counters_by_category"]["CRYPTO_LONG"]["duplicate_source_key_context"] == 4
    with gzip.open(tmp_path / "population/cases_preserved_not_training.jsonl.gz", "rt") as source:
        cases = [json.loads(line) for line in source]
    invalid = next(case["event"] for case in cases if case.get("event", {}).get("k") == "BAD_ENABLED=True")
    assert invalid["n"][0] == 9.0 and invalid["n"][1] == 0


def test_source_archive_hash_mismatch_never_produces_approved_population(tmp_path):
    manifest, archive, templates = inputs(tmp_path)
    payload = json.loads(manifest.read_text())
    payload["archive_sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="archive hash mismatch"):
        expanded.convert(manifest, archive, tmp_path / "bad", templates)


def test_skipped_zero_cannot_become_negative_and_promotion_block_remains_measured():
    raw = {"delta_invalid": False, "naked_delta": 0.0, "cumulative_before": 10.0, "yellows": {"FILTER_TF=1h": 0.0}, "reason": "SKIPPED_SAMPLING(pos_sym=0)"}
    record, naked_ok, baseline_ok = expanded.strict_record(raw)
    assert not naked_ok and not baseline_ok and record["delta_invalid"] is True
    assert record["yellow_reasons"]["FILTER_TF=1h"]
    raw["reason"] = "DEAD_VEC_PATH: promotion blocked after measured evaluation"
    raw["trades"] = 143
    assert expanded.strict_record(raw)[1:] == (False, False)
    raw["naked_valid"] = True
    assert expanded.strict_record(raw)[1:] == (True, True)


def test_row_winner_is_not_a_verified_naked_delta_and_constraints_remain_diagnostic():
    raw = {"delta_invalid": False, "delta": 3.0, "joint_delta": None, "naked_delta": None, "cumulative_before": 10.0}
    assert expanded.strict_record(raw)[1:] == (False, False)
    raw.update(naked_delta=0.0, reason="ZERO_TRADES")
    assert expanded.strict_record(raw)[1:] == (False, False)
    raw["reason"] = "TIM-guard: constraint applies"
    assert expanded.strict_record(raw)[1:] == (False, False)
