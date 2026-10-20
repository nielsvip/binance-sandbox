"""2026-10-20 provisional-bar heal: every NPZ row must be the exchange-closed canonical bar once it exists. Hermetic: fixture NPZ + fixture canonical store."""
import os
import sys
import pathlib

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import npz_bar_refresh as R  # noqa: E402

T0 = 1_790_000_100 - (1_790_000_100 % 900)
N = 60
SYM = "TESTUSDT"


def truth(i):
    b = 100.0 + i * 0.5
    return (b, b + 1.0, b - 1.0, b + 0.25, 10.0 + i)


class FakeBuilder:
    """what the real tail builder does for this fixture: arrays for the canonical bars, indicators derived from the CANONICAL close."""

    def __init__(self, canon, drop_ts=()):
        self.canon, self.drop_ts = canon, set(drop_ts)

    def source_dfs(self, symbol, mode, fresh, cutoff):
        return {"15m": object()}

    def arrays(self, symbol, mode, dfs, w15=None):
        ts = sorted(t for t in self.canon if t not in self.drop_ts)
        c = np.array([self.canon[t] for t in ts], dtype=np.float64)
        return {"timestamps": np.array(ts, dtype=np.int64), "open_15m": c[:, 0].astype(np.float32), "high_15m": c[:, 1].astype(np.float32),
                "low_15m": c[:, 2].astype(np.float32), "close_15m": c[:, 3].astype(np.float32), "volume_15m": c[:, 4].astype(np.float32),
                "close": c[:, 3].astype(np.float32), "ema_15m": (2 * c[:, 3]).astype(np.float32), "market_sentiment_score": np.zeros(len(ts), dtype=np.float32)}


def write_npz(path, rows, wrong=()):
    ts = np.array([T0 + i * 900 for i in rows], dtype=np.int64)
    o, h, l, c, v = (np.array([truth(i)[k] for i in rows], dtype=np.float32) for k in range(5))
    for j, i in enumerate(rows):
        if i in wrong:
            c[j] = c[j] + 1.7
            h[j] = h[j] + 1.7
    np.savez_compressed(path, timestamps=ts, open_15m=o, high_15m=h, low_15m=l, close_15m=c, volume_15m=v, close=c, ema_15m=(2 * c).astype(np.float32),
                        market_sentiment_score=np.full(len(ts), 61.0, dtype=np.float32), htf_align=np.array(["causal_v3"]))


@pytest.fixture
def env(tmp_path, monkeypatch):
    ind = tmp_path / "ind"
    ind.mkdir()
    monkeypatch.setattr(R, "PROV_DIR", tmp_path / "prov")
    monkeypatch.setattr(R, "SYMLOCK_DIR", tmp_path / "locks")
    monkeypatch.setattr(R, "cache_bar_final", lambda s, c: True)
    canon = {T0 + i * 900: truth(i) for i in range(N)}
    monkeypatch.setattr(R, "canonical_window", lambda s, n: (dict(canon), max(canon)))
    return ind, canon


def run(ind, canon, builder=None, **kw):
    return R.refresh_symbol(SYM, str(ind), w15=0, cutoff=max(canon), fresh={"15m": None}, mode="crypto", builder=builder or FakeBuilder(canon), **kw)


def load(ind):
    return np.load(ind / f"{SYM}.npz", allow_pickle=True)


def test_wrong_tail_rows_are_overwritten_from_canonical_and_nothing_shrinks(env):
    ind, canon = env
    write_npz(ind / f"{SYM}.npz", range(N), wrong=(N - 1, N - 2, N - 3, N - 9))
    before = load(ind)
    n0 = len(before["timestamps"])
    r = run(ind, canon)
    assert r["status"] == "INSTALLED", r
    assert r["healed_rows"] == 9 and r["heal_close_changed"] == 4
    z = load(ind)
    assert len(z["timestamps"]) >= n0 and int(z["timestamps"][-1]) == int(before["timestamps"][-1])
    assert np.array_equal(z["timestamps"], before["timestamps"])
    for i in range(N):
        assert z["close_15m"][i] == np.float32(truth(i)[3]), i
        assert z["high_15m"][i] == np.float32(truth(i)[1]), i
    assert np.allclose(z["ema_15m"][N - 9:], 2 * z["close_15m"][N - 9:])
    assert np.all(z["market_sentiment_score"][N - 9:] == 61.0)
    assert np.all(z["ema_15m"][:N - 9] == before["ema_15m"][:N - 9])


def test_matching_npz_is_left_alone(env):
    ind, canon = env
    write_npz(ind / f"{SYM}.npz", range(N))
    b = (ind / f"{SYM}.npz").read_bytes()
    r = run(ind, canon)
    assert r["status"] == "FRESH" and r.get("healed_rows") is None
    assert (ind / f"{SYM}.npz").read_bytes() == b


def test_provisional_mark_forces_rebuild_even_when_ohlcv_matches(env):
    ind, canon = env
    write_npz(ind / f"{SYM}.npz", range(N))
    R.prov_mark(SYM, [T0 + (N - 1) * 900, T0 + (N - 2) * 900], "ingest")
    r = run(ind, canon)
    assert r["status"] == "INSTALLED" and r["healed_rows"] == 2
    assert R.prov_read(SYM) == {}


def test_provisional_mark_waits_for_the_canonical_bar(env, monkeypatch):
    ind, canon = env
    write_npz(ind / f"{SYM}.npz", range(N))
    older = {t: v for t, v in canon.items() if t < T0 + (N - 1) * 900}
    monkeypatch.setattr(R, "canonical_window", lambda s, n: (dict(older), max(older)))
    R.prov_mark(SYM, [T0 + (N - 1) * 900], "ingest")
    r = run(ind, older)
    assert r["status"] == "FRESH" or r["status"] == "HEAL_DEFERRED_NO_SHRINK" or r["status"] == "NO_NEW_ROWS"
    assert R.prov_read(SYM) == {T0 + (N - 1) * 900: "ingest"}


def test_refuses_to_shrink_when_builder_cannot_recover_a_rewound_bar(env):
    ind, canon = env
    write_npz(ind / f"{SYM}.npz", range(N), wrong=(N - 4,))
    b = (ind / f"{SYM}.npz").read_bytes()
    r = run(ind, canon, builder=FakeBuilder(canon, drop_ts=[T0 + (N - 2) * 900]))
    assert r["status"] == "HEAL_DEFERRED_NO_SHRINK", r
    assert (ind / f"{SYM}.npz").read_bytes() == b


def test_scan_finds_missing_canonical_bar_and_rewinds_to_it(env, tmp_path):
    ind, canon = env
    write_npz(ind / f"{SYM}.npz", [i for i in range(N) if i != 50])
    z = load(ind)
    n, info = R.heal_scan(SYM, z, np.asarray(z["timestamps"]).astype(np.int64), 24)
    assert n == N - 1 - 50 and info["heal_gap_bars"] == 1


def test_scan_never_rewinds_past_a_phantom_bar(env, monkeypatch):
    ind, canon = env
    phantom_ts = T0 + 55 * 900
    without = {t: v for t, v in canon.items() if t != phantom_ts}
    monkeypatch.setattr(R, "canonical_window", lambda s, n: (dict(without), max(without)))
    write_npz(ind / f"{SYM}.npz", range(N), wrong=(52,))
    z = load(ind)
    n, info = R.heal_scan(SYM, z, np.asarray(z["timestamps"]).astype(np.int64), 24)
    assert n == N - 56 or n == 0
    assert info.get("heal_phantom_rows") == 1


def test_not_canonical_symbol_is_never_healed(env, monkeypatch):
    ind, canon = env
    monkeypatch.setattr(R, "canonical_window", lambda s, n: (None, None))
    write_npz(ind / f"{SYM}.npz", range(N), wrong=(N - 1,))
    r = run(ind, canon)
    assert r["status"] == "FRESH"


def test_ingest_marks_applied_crypto_rows_provisional():
    src = (pathlib.Path(__file__).resolve().parent.parent / "tools" / "npz_bar_ingest.py").read_text()
    assert 'prov_mark(sym, [ts], "ingest")' in src and 'if mode == "crypto"' in src
