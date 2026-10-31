import json

import pytest

from tools.paper_forward_worker import load_frames


def test_load_frames_keeps_distinct_ordered_native_bars(tmp_path):
    source = tmp_path / "frames.ndjson"
    source.write_text(
        "\n".join(
            json.dumps({"ts": ts, "indicators": {"current_price": price, "timestamp_1m": f"bar-{ts}"}})
            for ts, price in ((1000, 10.0), (1060, 10.1))
        )
        + "\n",
        encoding="utf-8",
    )

    assert load_frames(source, "BTCUSDT") == [
        {"ts": 1000.0, "indicators": {"current_price": 10.0, "timestamp_1m": "bar-1000"}},
        {"ts": 1060.0, "indicators": {"current_price": 10.1, "timestamp_1m": "bar-1060"}},
    ]


def test_load_frames_rejects_one_frame_probe(tmp_path):
    source = tmp_path / "frame.ndjson"
    source.write_text(json.dumps({"ts": 1000, "indicators": {"current_price": 10.0}}), encoding="utf-8")

    with pytest.raises(ValueError, match="at least two distinct"):
        load_frames(source, "BTCUSDT")


def test_load_frames_rejects_duplicate_or_out_of_order_timestamp(tmp_path):
    source = tmp_path / "frames.ndjson"
    source.write_text(
        "\n".join(json.dumps({"ts": ts, "indicators": {"current_price": 10.0}}) for ts in (1000, 1000)) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not increasing"):
        load_frames(source, "BTCUSDT")
