"""Hole-repair merge contract (USER 2026-10-15 "merge, not replace go ahead").

Context: the 2026-08-26 -> 09-30 Tradier 15m cache hole left 32 of 162 stock NPZs with a 29-35 day gap in the recent
window while still spanning years. A rebuild's row index is the FRESH klines (~10 months, clean), so it is <80% of the
holed file's rows and `npz_guard.should_allow_overwrite` blocks it as bar-loss — the hole can never be repaired and
`v15_dispatch_job` gap-refuses the symbol every tick. The repair keeps historic rows outside the candidate's coverage
and takes the candidate's rows inside it, so the merged file is strictly better and the EXISTING guards pass by
construction. These tests pin that it only ever fires when it genuinely improves the file.
"""
import importlib.util
import pathlib
import sys
import types

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
BAR = 900  # 15m, epoch seconds
SESSION_BARS = 26  # 09:30-16:00 ET


def _load_helper():
    """Import just the helper out of backtest_v8_precompute (importing the module pulls the whole builder)."""
    src = (ROOT / "backtest_v8_precompute.py").read_text()
    start = src.index("def _npz_hole_repair_merge(")
    end = src.index("def _inject_market_sentiment(")
    mod = types.ModuleType("hole_repair_helper")
    mod.__dict__.update({"np": np, "Path": pathlib.Path, "logger": _Logger()})
    sys.path.insert(0, str(ROOT))
    exec(compile(src[start:end], "hole_repair", "exec"), mod.__dict__)
    return mod


class _Logger:
    def __init__(self):
        self.lines = []

    def warning(self, m):
        self.lines.append(str(m))

    info = warning


HELPER = _load_helper()


def sessions_ts(start_day, n_sessions, skip=()):
    """Epoch-second 15m bars for n_sessions consecutive weekdays, optionally skipping some (a hole)."""
    out = []
    day = 0
    made = 0
    while made < n_sessions:
        d = start_day + day * 86400
        if made not in skip:
            out.extend(d + i * BAR for i in range(SESSION_BARS))
            made += 1
        else:
            made += 1
        day += 1
    return np.asarray(sorted(out), dtype=np.int64)


def write_npz(path, ts, keys=12, extra=None):
    cols = {"timestamps": ts.astype(np.int64), "close": np.full(len(ts), 10.0, dtype=np.float32)}
    for i in range(keys):
        cols[f"ind_{i}"] = np.arange(len(ts), dtype=np.float32)
    if extra:
        cols.update(extra)
    np.savez(str(path), **cols)
    return path


def candidate(ts, keys=12, extra=None):
    c = {"timestamps": ts.astype(np.int64), "close": np.full(len(ts), 20.0, dtype=np.float32)}
    for i in range(keys):
        c[f"ind_{i}"] = np.full(len(ts), 7.0, dtype=np.float32)
    if extra:
        c.update(extra)
    return c


NOW_DAY = 1_792_000_000 // 86400 * 86400


@pytest.fixture
def holed(tmp_path):
    """Existing: 2 years of sessions with a 25-session hole just before a clean recent tail -> gap guard FAILS."""
    old = sessions_ts(NOW_DAY - 500 * 86400, 400)
    recent = sessions_ts(NOW_DAY - 14 * 86400, 10)
    ts = np.concatenate([old, recent])
    return write_npz(tmp_path / "HOLED.npz", ts), ts


def test_repair_fills_the_hole_and_keeps_history(holed):
    path, ex_ts = holed
    cand_ts = sessions_ts(NOW_DAY - 59 * 86400, 42)  # fresh klines: ~42 clean sessions
    out = HELPER._npz_hole_repair_merge(path, candidate(cand_ts), "HOLED")
    assert out is not None, HELPER.logger.lines[-3:]
    m = out["timestamps"]
    assert m.size > ex_ts.size, "merged must add rows, or the overwrite guard would still block"
    assert m[0] == ex_ts[0], "history must be preserved from the very first bar"
    assert np.all(np.diff(m) > 0), "merged timestamps must be strictly increasing"
    from tools.stock_npz_gap_guard import check_timestamps
    assert check_timestamps(m)["ok"], "the whole point: the merged file passes the gap guard"
    assert not check_timestamps(ex_ts)["ok"], "sanity: the existing file did not"


def test_candidate_rows_win_inside_their_coverage(holed):
    path, _ = holed
    cand_ts = sessions_ts(NOW_DAY - 59 * 86400, 42)
    out = HELPER._npz_hole_repair_merge(path, candidate(cand_ts), "HOLED")
    assert out is not None
    # close is 10.0 in the existing file and 20.0 in the candidate. Assert by TIMESTAMP membership, not by position:
    # the union puts any existing tail newer than the candidate's range AFTER it, so "the last N rows" is a mix.
    m_ts, m_close = out["timestamps"], out["close"]
    from_cand = np.isin(m_ts, cand_ts)
    assert np.all(m_close[from_cand] == 20.0), "where the candidate has a bar, the FRESH value must win — that is what fills the hole"
    assert np.all(m_close[~from_cand] == 10.0), "everywhere else the historic bars stay untouched"
    assert from_cand.sum() == len(cand_ts), "every candidate bar must be present exactly once"


def test_no_repair_when_existing_is_already_clean(tmp_path):
    ts = sessions_ts(NOW_DAY - 59 * 86400, 42)
    p = write_npz(tmp_path / "CLEAN.npz", ts)
    assert HELPER._npz_hole_repair_merge(p, candidate(sessions_ts(NOW_DAY - 59 * 86400, 42)), "CLEAN") is None


def test_refuses_when_the_candidate_is_holed_too(holed):
    """CALM/ETH: klines themselves lack the sessions — a merge must not paper over that."""
    path, _ = holed
    bad = sessions_ts(NOW_DAY - 59 * 86400, 12)  # only 12 sessions in 60d
    assert HELPER._npz_hole_repair_merge(path, candidate(bad), "HOLED") is None
    assert any("candidate is holed too" in l for l in HELPER.logger.lines), HELPER.logger.lines[-3:]


def test_refuses_when_merge_would_not_add_rows(tmp_path):
    ts = sessions_ts(NOW_DAY - 500 * 86400, 400)
    p = write_npz(tmp_path / "LONGHOLE.npz", ts)  # gap guard fails (nothing recent)
    cand_ts = ts[-SESSION_BARS * 5:]  # entirely inside the existing range, adds nothing
    assert HELPER._npz_hole_repair_merge(p, candidate(cand_ts), "LONGHOLE") is None


def test_one_sided_keys_are_padded_not_dropped(holed):
    """Dropping a column would make keys(merged) < keys(existing) and the guard would block the repair."""
    path, ex_ts = holed
    with np.load(str(path), allow_pickle=True) as z:
        ex_keys = set(z.files)
    cand_ts = sessions_ts(NOW_DAY - 59 * 86400, 42)
    c = candidate(cand_ts)
    c["brand_new_col"] = np.ones(len(cand_ts), dtype=np.float32)
    out = HELPER._npz_hole_repair_merge(path, c, "HOLED")
    assert out is not None
    assert ex_keys <= set(out), "every existing column must survive the merge"
    assert "brand_new_col" in out
    n = len(out["timestamps"])
    assert all(len(out[k]) == n for k in ("close", "ind_0", "brand_new_col")), "row arrays must stay aligned to timestamps"
    assert np.isnan(out["brand_new_col"][0]), "a column the historic side never had is padded, never invented"


def test_non_row_markers_prefer_the_candidate(holed):
    path, _ = holed
    cand_ts = sessions_ts(NOW_DAY - 59 * 86400, 42)
    c = candidate(cand_ts, extra={"kline_source": np.array(["canonical_v1"])})
    out = HELPER._npz_hole_repair_merge(path, c, "HOLED")
    assert out is not None and out["kline_source"][0] == "canonical_v1"


def test_missing_existing_file_is_not_a_repair(tmp_path):
    assert HELPER._npz_hole_repair_merge(tmp_path / "nope.npz", candidate(sessions_ts(NOW_DAY, 5)), "NOPE") is None


def test_never_raises_on_garbage(tmp_path):
    p = tmp_path / "JUNK.npz"
    p.write_bytes(b"not an npz")
    assert HELPER._npz_hole_repair_merge(p, candidate(sessions_ts(NOW_DAY, 5)), "JUNK") is None
