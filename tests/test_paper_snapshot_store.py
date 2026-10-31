import numpy as np
import pytest

from tools.paper_snapshot_store import PaperSnapshotStore


def test_raw_snapshot_fields_are_not_synthesized_or_aliased():
    store = PaperSnapshotStore()
    store.append(100, {"current_price": 10.0, "timestamp_3m": "source-3m", "stoch_k_3m": 42.0})
    raw = store.build_indicator_dict(0)
    assert "stoch_k_1m" not in raw
    assert "wt1_1m" not in raw
    assert "stoch_k_5m" not in raw
    assert raw["timestamp_3m"] == "source-3m"
    assert store.has_3m is True
    assert store.has_5m is False


def test_missing_fields_are_nan_or_none_without_forward_fill():
    store = PaperSnapshotStore()
    store.append(100, {"current_price": 10.0, "metric": 3.5, "label": "a"})
    store.append(200, {"current_price": 11.0})
    assert np.isnan(store.arrays["metric"][1])
    assert store.arrays["label"][1] is None
    assert store.get("metric", 1, "missing") == "missing"
    assert store.get("label", 1, "missing") == "missing"
    assert "metric" not in store.build_indicator_dict(1)
    assert store.build_indicator_dict(0)["metric"] == 3.5


def test_snapshots_are_copied_on_append_and_return():
    source = {"current_price": 10.0, "nested": {"items": [1, 2]}}
    store = PaperSnapshotStore()
    store.append(100, source)
    source["nested"]["items"].append(3)
    output = store.build_indicator_dict(0)
    assert output["nested"]["items"] == [1, 2]
    output["nested"]["items"].append(4)
    assert store.build_indicator_dict(0)["nested"]["items"] == [1, 2]


def test_duplicate_identical_snapshot_is_idempotent_changed_snapshot_refused():
    store = PaperSnapshotStore()
    frame = {"current_price": 10.0, "timestamp": "original"}
    assert store.append(100, frame) == 0
    assert store.append(100, frame) == 0
    assert store.n_bars == 1
    with pytest.raises(ValueError, match="conflicting changed snapshot"):
        store.append(100, {"current_price": 10.1, "timestamp": "original"})


@pytest.mark.parametrize("bad_ts", [True, 0, -1, float("nan"), float("inf")])
def test_invalid_observed_timestamps_refused(bad_ts):
    store = PaperSnapshotStore()
    with pytest.raises(ValueError, match="observed_ts"):
        store.append(bad_ts, {"current_price": 1.0})


def test_timestamps_must_increase_and_price_must_be_explicit_valid():
    store = PaperSnapshotStore()
    store.append(100, {"current_price": 1.0})
    with pytest.raises(ValueError, match="strictly increasing"):
        store.append(99, {"current_price": 2.0})
    with pytest.raises(ValueError, match="current_price"):
        store.append(101, {"mark_price": 3.0})
    with pytest.raises(ValueError, match="current_price"):
        store.append(101, {"current_price": float("nan")})


def test_price_and_exact_indicator_snapshot():
    store = PaperSnapshotStore([
        {"ts": 100, "indicators": {"current_price": 10.0, "wt1_3m": 4.0}},
        {"ts": 200, "indicators": {"current_price": 12.0, "wt1_5m": 5.0}},
    ])
    assert store.n_bars == 2
    assert store.ts_to_idx == {100.0: 0, 200.0: 1}
    assert store.price(0) == 10.0
    assert store.price(1) == 12.0
    assert store.build_indicator_dict(1) == {"current_price": 12.0, "wt1_5m": 5.0}
    assert store.has_3m is True and store.has_5m is True
