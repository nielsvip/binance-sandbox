import gzip
import hashlib
import io
import json

from tools.v20_candidate_refresh import gain_population


def test_full_scope_none_cap_and_direct_stream_preserve_explicit_lineage(tmp_path):
    entries = []
    symbols = {"CRYPTO_LONG": "BTCUSDC_LONG", "CRYPTO_SHORT": "BTCUSDC_SHORT", "STOCKS_LONG": "AAPL_LONG", "STOCKS_SHORT": "AAPL_SHORT"}
    for category, symbol in symbols.items():
        row = {"npz": "ts1700000000_n50", "naked_delta": 1.0, "delta": 1.0, "cumulative_before": 5.0, "promoted": False, "yellows": {"FILTER_TF=1h": 1.2}, "trades": 40}
        unknown = dict(row)
        unknown.pop("npz")
        future = dict(row, npz="ts1900000000_n50")
        document = {"symside": symbol, "window_days": 30, "done": {"EXIT_STRUCTURAL!3:SW=True": row, "EXIT_STRUCTURAL!4:UNKNOWN=True": unknown, "EXIT_STRUCTURAL!5:FUTURE=True": future}}
        path = tmp_path / (symbol + ".json")
        path.write_text(json.dumps(document))
        entries.append({"s": symbol, "file": str(path)})
    index = tmp_path / "index.json"
    index.write_text(json.dumps(entries))
    wire = io.BytesIO()
    report = gain_population(index, tmp_path / "population", {category: 1800000000 for category in symbols}, max_boards_per_cat=None, output_stream=wire)
    assert report["source_rows_by_category"] == {category: 1 for category in symbols}
    assert report["counters"]["unknown_explicit_row_npz"] == 4
    assert report["counters"]["market_end_not_before_holdout"] == 4
    assert report["spill_sha256"] == hashlib.sha256(wire.getvalue()).hexdigest()
    events = [json.loads(line.partition("\t")[2]) for line in gzip.decompress(wire.getvalue()).decode().splitlines()]
    rows = [event for event in events if event["e"] == "row"]
    assert len(rows) == 4
    assert all(event["npz"] == 1700000000 and event["explicit_row_npz"] == "ts1700000000_n50" for event in rows)
    assert all(event["progress_key"] == "EXIT_STRUCTURAL!3:SW=True" and event["source_sha256"] for event in rows)
