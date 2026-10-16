"""NPZ preflight contract (USER 2026-10-15): verify the data BEFORE a board spends CPU on it.

Each test builds a synthetic .npz with exactly one defect, so a failure names the defect rather than a symbol. The
reference defect is the real one: `ACEUSDT_LONG_bhm14p68_gain0p00_t0_30d_matrix.xlsx` (2026-09-29, inside the
09-24..10-01 klines-tail corruption window) consumed a full board and produced gain 0.00 on 0 trades, because nothing
checked the NPZ's content before dispatch — only its tail freshness (tools/v15_npz_prep.py).
"""
import importlib.util
import pathlib

import numpy as np
import pytest

SPEC = importlib.util.spec_from_file_location("v15_npz_preflight", str(pathlib.Path(__file__).resolve().parent.parent / "tools" / "v15_npz_preflight.py"))
PF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PF)

BAR = 900.0  # 15m
NOW = 1_792_000_000.0


def write_npz(d, symbol, days=40.0, bars=None, close=None, keys_extra=180, monotonic=True, last=NOW):
    n = int(bars if bars is not None else days * 86400 / BAR)
    ts = np.arange(n, dtype="float64") * BAR + (last - (n - 1) * BAR)
    if not monotonic and n > 10:
        ts[5], ts[6] = ts[6], ts[5]
    c = np.full(n, 100.0) if close is None else np.asarray(close, dtype="float64")[:n]
    cols = {"timestamps": ts, "close": c, "open_15m": c, "high_15m": c * 1.001, "low_15m": c * 0.999, "close_15m": c, "volume_15m": np.full(n, 5.0)}
    for i in range(keys_extra):
        cols[f"ind_{i}_15m"] = np.zeros(n)
    p = pathlib.Path(d) / f"{symbol}.npz"
    np.savez(str(p), **cols)
    return p


@pytest.fixture
def ind(tmp_path):
    # a healthy neighbourhood so the measured host median is ~187 keys
    for s in ("AAAUSDT", "BBBUSDT", "CCCUSDT"):
        write_npz(tmp_path, s)
    return tmp_path


def test_healthy_npz_passes(ind):
    write_npz(ind, "GOODUSDT")
    r = PF.verify("GOODUSDT", 30, ind, now=NOW)
    assert r["ok"], r["reasons"]
    assert r["bars"] > 2800 and r["span_days"] >= 24


def test_missing_file_blocks(ind):
    r = PF.verify("NOPEUSDT", 30, ind, now=NOW)
    assert not r["ok"] and "no NPZ file" in r["reasons"]


def test_truncated_npz_blocks(ind):
    p = ind / "TRUNCUSDT.npz"
    p.write_bytes(b"PK\x03\x04 not really a zip")
    r = PF.verify("TRUNCUSDT", 30, ind, now=NOW)
    assert not r["ok"] and any("unreadable" in x or "BadZip" in x for x in r["reasons"])


def test_short_span_blocks_before_the_board_runs(ind):
    write_npz(ind, "SHORTUSDT", days=20.0)  # 20d < 0.80 * 30d
    r = PF.verify("SHORTUSDT", 30, ind, now=NOW)
    assert not r["ok"]
    assert any("span" in x and "INVALID" in x for x in r["reasons"]), r["reasons"]


def test_span_exactly_at_the_coverage_floor_passes(ind):
    write_npz(ind, "EDGEUSDT", days=24.1)  # 0.80 * 30 = 24.0
    assert PF.verify("EDGEUSDT", 30, ind, now=NOW)["ok"]


def test_all_zero_close_blocks_the_silent_zero_trade_producer(ind):
    n = int(40 * 86400 / BAR)
    write_npz(ind, "ZEROUSDT", days=40.0, close=np.zeros(n))
    r = PF.verify("ZEROUSDT", 30, ind, now=NOW)
    assert not r["ok"]
    assert any("all zero inside the window" in x for x in r["reasons"]), r["reasons"]


def test_all_nan_close_blocks(ind):
    n = int(40 * 86400 / BAR)
    write_npz(ind, "NANUSDT", days=40.0, close=np.full(n, np.nan))
    r = PF.verify("NANUSDT", 30, ind, now=NOW)
    assert not r["ok"] and any("all NaN inside the window" in x for x in r["reasons"])


def test_negative_prices_block(ind):
    n = int(40 * 86400 / BAR)
    c = np.full(n, 100.0)
    c[-50:] = -1.0
    write_npz(ind, "NEGUSDT", days=40.0, close=c)
    r = PF.verify("NEGUSDT", 30, ind, now=NOW)
    assert not r["ok"] and any("non-positive" in x for x in r["reasons"])


def test_non_monotonic_timestamps_block(ind):
    write_npz(ind, "SHUFUSDT", monotonic=False)
    r = PF.verify("SHUFUSDT", 30, ind, now=NOW)
    assert not r["ok"] and any("monotonic" in x for x in r["reasons"])


def test_thin_build_blocks_against_the_measured_host_median(ind):
    write_npz(ind, "THINUSDT", keys_extra=2)
    r = PF.verify("THINUSDT", 30, ind, now=NOW)
    assert not r["ok"] and any("thin NPZ" in x for x in r["reasons"]), r["reasons"]


def test_missing_core_column_blocks(ind):
    n = 3000
    ts = np.arange(n, dtype="float64") * BAR + NOW - (n - 1) * BAR
    np.savez(str(ind / "NOCLOSEUSDT.npz"), timestamps=ts, open_15m=np.full(n, 1.0))
    r = PF.verify("NOCLOSEUSDT", 30, ind, now=NOW)
    assert not r["ok"] and any("core columns missing" in x for x in r["reasons"])


def test_span_ok_is_pure_and_uses_the_engine_floor():
    assert PF.MIN_WINDOW_COVERAGE == 0.80
    assert PF.span_ok(24.0, 30) and PF.span_ok(30, 30) and not PF.span_ok(23.9, 30)
    assert not PF.span_ok(None, 30) and not PF.span_ok("x", 30)


def test_venue_split():
    assert PF.venue_of("ACEUSDT") == "crypto" and PF.venue_of("ADAUSDC") == "crypto"
    assert PF.venue_of("AAPL") == "stocks" and PF.venue_of("BMNR") == "stocks"


def test_deferral_ledger_records_then_clears(ind, tmp_path):
    led = tmp_path / "npz_deferred.json"
    write_npz(ind, "FLIPUSDT", days=10.0)
    bad = PF.verify("FLIPUSDT", 30, ind, now=NOW)
    rec = PF.record_deferral(bad, led)
    assert rec and rec["deferrals"] == 1 and "backtest_v8_precompute.py --symbol FLIPUSDT" in rec["rebuild_cmd"]
    rec2 = PF.record_deferral(PF.verify("FLIPUSDT", 30, ind, now=NOW), led)
    assert rec2["deferrals"] == 2, "a repeat deferral must count, so a symbol cannot spin forever unnoticed"
    write_npz(ind, "FLIPUSDT", days=40.0)  # rebuilt
    good = PF.verify("FLIPUSDT", 30, ind, now=NOW)
    assert good["ok"]
    PF.record_deferral(good, led)
    import json
    assert "FLIPUSDT" not in json.loads(led.read_text()), "a passing symbol must leave the deferral ledger so it is requeued"


def test_stale_tail_blocks_a_new_board(ind):
    """USER 2026-10-15: no new board may start on old NPZ bars — update them first."""
    write_npz(ind, "STALEUSDT", days=40.0, last=NOW - 8 * 3600)
    r = PF.verify("STALEUSDT", 30, ind, now=NOW)
    assert not r["ok"]
    assert any("stale tail" in x for x in r["reasons"]), r["reasons"]
    assert r["tail_age_h"] >= 8.0


def test_fresh_tail_passes(ind):
    write_npz(ind, "FRESHUSDT", days=40.0, last=NOW - 900)
    assert PF.verify("FRESHUSDT", 30, ind, now=NOW)["ok"]


def test_frozen_snapshot_carveout_for_script_version_validation(ind):
    """The S2 version gate benches champion vs candidate on a FROZEN snapshot; enforcing freshness there would
    reject the verifier's own input. check_freshness=False is that single sanctioned exception."""
    write_npz(ind, "SNAPUSDT", days=40.0, last=NOW - 48 * 3600)
    assert not PF.verify("SNAPUSDT", 30, ind, now=NOW)["ok"]
    r = PF.verify("SNAPUSDT", 30, ind, now=NOW, check_freshness=False)
    assert r["ok"], r["reasons"]
    assert not any("stale tail" in x for x in r["reasons"])


def test_stocks_get_a_wider_tail_window_than_crypto(ind):
    """A stock has no bars overnight or at the weekend; crypto always does."""
    assert PF.MAX_TAIL_AGE_H["stocks"] > PF.MAX_TAIL_AGE_H["crypto"]
    write_npz(ind, "XYZ", days=40.0, last=NOW - 6 * 3600)      # stock, 6h old
    write_npz(ind, "SIXHUSDT", days=40.0, last=NOW - 6 * 3600)  # crypto, 6h old
    assert PF.verify("XYZ", 30, ind, now=NOW)["ok"], "a 6h-old stock tail is normal"
    assert not PF.verify("SIXHUSDT", 30, ind, now=NOW)["ok"], "a 6h-old crypto tail means 24 missed 15m bars"


def test_staleness_never_masks_a_structural_defect(ind):
    n = int(40 * 86400 / BAR)
    write_npz(ind, "BOTHUSDT", days=40.0, close=np.zeros(n), last=NOW - 9 * 3600)
    r = PF.verify("BOTHUSDT", 30, ind, now=NOW)
    assert not r["ok"]
    assert any("stale tail" in x for x in r["reasons"]) and any("all zero" in x for x in r["reasons"]), r["reasons"]
