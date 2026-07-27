import json

import numpy as np

from tools.audit_tradier_npz_lineage import build_receipt, compare_npzs


def _write_source(path, timestamps):
    rows = []
    for index, timestamp in enumerate(timestamps):
        close = 100.0 + index
        rows.append(
            {
                "timestamp": np.datetime_as_string(
                    np.datetime64(int(timestamp), "s"), unit="s", timezone="UTC"
                ),
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 1000 + index,
            }
        )
    path.write_text(json.dumps(rows))


def _write_npz(path, timestamps, with_parent=True):
    timestamps = np.asarray(timestamps, dtype=np.int64)
    n = len(timestamps)
    close = 100.0 + np.arange(n)
    synthetic = np.zeros(n, dtype=np.int8)
    synthetic[-2:] = 1
    arrays = {
        "timestamps": timestamps,
        "close": close.astype(np.float32),
        "synthetic_5m": synthetic,
        "open_5m": (close - 0.1).astype(np.float32),
        "high_5m": (close + 0.2).astype(np.float32),
        "low_5m": (close - 0.2).astype(np.float32),
        "close_5m": close.astype(np.float32),
        "volume_5m": (1000 + np.arange(n)).astype(np.float32),
    }
    for tf in ("15m", "1h", "4h", "D"):
        arrays[f"timestamp_{tf}"] = timestamps.copy()
    if with_parent:
        parent = timestamps.copy()
        parent[synthetic.astype(bool)] = (
            (timestamps[synthetic.astype(bool)] + 899) // 900
        ) * 900
        arrays["synthetic_5m_parent_close_ts"] = parent
    np.savez_compressed(path, **arrays)


def test_added_parent_field_preserves_every_original_array(tmp_path):
    timestamps = np.arange(1_700_000_100, 1_700_000_100 + 12 * 300, 300)
    legacy = tmp_path / "legacy.npz"
    causal = tmp_path / "causal.npz"
    _write_npz(legacy, timestamps, with_parent=False)
    _write_npz(causal, timestamps, with_parent=True)

    comparison = compare_npzs(legacy, causal, 1e-4)

    assert comparison["equivalent_on_overlap"]
    assert comparison["right_only_fields"] == ["synthetic_5m_parent_close_ts"]


def test_material_raw_gap_fails_closed_even_when_npz_parent_clock_is_valid(tmp_path):
    start = 1_700_000_100
    early = [start + i * 300 for i in range(10)]
    late = [start + 10 * 86400 + i * 300 for i in range(10)]
    timestamps = early + late
    raw_5m = tmp_path / "VT_5m.json"
    raw_15m = tmp_path / "VT_15m.json"
    candidate = tmp_path / "VT.npz"
    _write_source(raw_5m, timestamps)
    _write_source(raw_15m, late)
    _write_npz(candidate, timestamps, with_parent=True)

    receipt = build_receipt(
        "VT",
        raw_5m,
        raw_15m,
        [("candidate", candidate)],
    )

    assert receipt["decision"] == "FAIL_CLOSED_SOURCE_INCOMPLETE"
    assert not receipt["source_complete"]
    assert receipt["sources"]["5m"]["material_gaps_gt_7d"]
    assert receipt["npzs"]["candidate"]["parent_clock"]["valid"]
