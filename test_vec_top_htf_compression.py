from types import SimpleNamespace

import numpy as np

from tools.vec_top_exit_campaign import _compress_htf


class FakeNpz(dict):
    @property
    def files(self):
        return list(self)


def test_compress_htf_ignores_late_observed_older_parent_identity():
    # The observation clock can reveal a delayed synthetic row after a newer
    # native row. Its older parent must not be replayed into a stateful book.
    source = np.arange(1, 45, dtype=np.int64) * 100
    source[12] = source[10]
    source[25] = source[22]
    z = FakeNpz({
        "timestamp_1h": source,
        "open_1h": np.arange(44, dtype=float) + 10,
        "high_1h": np.arange(44, dtype=float) + 11,
        "low_1h": np.arange(44, dtype=float) + 9,
        "close_1h": np.arange(44, dtype=float) + 10.5,
    })
    data = SimpleNamespace(
        symbol="TEST",
        z=z,
        full_indices=np.arange(44, dtype=np.int64),
    )

    compressed = _compress_htf(data, "1h")

    assert len(compressed.source_ts) >= 30
    assert np.all(np.diff(compressed.source_ts) > 0)
    assert source[12] not in compressed.source_ts[11:13]
