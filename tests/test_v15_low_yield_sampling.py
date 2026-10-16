import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("V15_PROGRESS_DIR", "/tmp/v15_low_yield_test")
import v15_pilot as P  # noqa: E402


def _rate(pos, n, rounds=1, keys=4000):
    kept = 0
    for i in range(keys):
        for r in range(rounds):
            kept += P.low_yield_keep(pos, n, 15, f"TAB!SW=c{i}", f"r{r}")[0]
    return kept / (keys * rounds)


def test_zero_evidence_sampled_one_in_100():
    assert 0.005 <= _rate(0, 200) <= 0.016


def test_under_three_percent_sampled_one_in_20():
    assert 0.035 <= _rate(5, 291) <= 0.067


def test_proven_and_unevidenced_always_kept():
    assert P.low_yield_keep(10, 291, 15, "k", "r") == (True, 1.0, "proven")
    assert P.low_yield_keep(0, 5, 15, "k", "r") == (True, 1.0, "unevidenced")
    assert P.low_yield_keep(None, None, 15, "k", "r")[0]


def test_deterministic_and_rotates_across_rounds():
    a = [P.low_yield_keep(0, 100, 15, f"k{i}", "round1")[0] for i in range(2000)]
    assert a == [P.low_yield_keep(0, 100, 15, f"k{i}", "round1")[0] for i in range(2000)]
    b = [P.low_yield_keep(0, 100, 15, f"k{i}", "round2")[0] for i in range(2000)]
    assert a != b


def test_partition_off_keeps_legacy_zero_rule(monkeypatch):
    monkeypatch.delenv("V15_LOW_YIELD_SAMPLING", raising=False)
    ev = {"r@H1": {"pos_sym": 0, "n_sym": 50}, "r@H2": {"pos_sym": 3, "n_sym": 50}}
    kept, skipped = P._zero_partition_hdrs(["H1", "H2"], {"H1": "r@H1", "H2": "r@H2"}, ev, 10)
    assert kept == ["H2"] and skipped == ["H1"]


def test_partition_on_samples_instead_of_condemning(monkeypatch):
    monkeypatch.setenv("V15_LOW_YIELD_SAMPLING", "1")
    hdrs = [f"H{i}" for i in range(3000)]
    keys = {h: f"r@{h}" for h in hdrs}
    ev = {k: {"pos_sym": 0, "n_sym": 50} for k in keys.values()}
    kept, skipped = P._zero_partition_hdrs(hdrs, keys, ev, 10, "rX")
    assert 10 <= len(kept) <= 60 and len(kept) + len(skipped) == 3000
