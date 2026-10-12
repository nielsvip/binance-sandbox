"""Stale-NPZ skip gate (USER 2026-10-12): a test on an NPZ that does not reach the
latest 15m bar must SKIP with STALE_NPZ_SKIPPED, never return misleading numbers.
Hermetic: builds tiny fixture NPZs under a monkeypatched ROOT, engine never runs."""
import os
import numpy as np
import pytest

import tools.opt.v12_pilot as P


def _mk_npz(root, sym, last_ts, n=8):
    d = root / "backtest_v8" / "indicators"
    d.mkdir(parents=True, exist_ok=True)
    ts = np.arange(last_ts - (n - 1) * 900, last_ts + 1, 900, dtype=np.int64)
    np.savez_compressed(str(d / f"{sym}.npz"), timestamps=ts, close_15m=np.ones(n))


@pytest.fixture()
def fresh_root(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "ROOT", tmp_path)
    monkeypatch.delenv("V12_ALLOW_STALE_NPZ", raising=False)
    from tools.npz_bar_refresh import last_closed_bar
    return tmp_path, int(last_closed_bar())


def test_stale_crypto_skips(fresh_root):
    root, cutoff = fresh_root
    _mk_npz(root, "TSTUSDC", cutoff - 30 * 86400)
    fresh, reason = P.npz_freshness("TSTUSDC_LONG")
    assert fresh is False and "STALE_NPZ_SKIPPED" in reason
    r = P.evaluate_sanitized("TSTUSDC_LONG", {}, 30)
    assert r["valid"] is False and "STALE_NPZ_SKIPPED" in r["invalid_reason"]
    assert r["gain_pct"] == 0.0 and r["trades"] == 0


def test_fresh_crypto_passes_gate(fresh_root):
    root, cutoff = fresh_root
    _mk_npz(root, "TSTUSDC", cutoff)
    fresh, _ = P.npz_freshness("TSTUSDC_LONG")
    assert fresh is True


def test_one_bar_grace(fresh_root):
    root, cutoff = fresh_root
    _mk_npz(root, "TSTUSDC", cutoff - 900)
    fresh, _ = P.npz_freshness("TSTUSDC_LONG")
    assert fresh is True


def test_two_bars_stale(fresh_root):
    root, cutoff = fresh_root
    _mk_npz(root, "TSTUSDC", cutoff - 2 * 900)
    fresh, _ = P.npz_freshness("TSTUSDC_LONG")
    assert fresh is False


def test_stocks_rth_cutoff(fresh_root, monkeypatch):
    root, _ = fresh_root
    from tools.npz_bar_refresh import last_rth_close_bar
    cut = int(last_rth_close_bar())
    assert cut > 0
    _mk_npz(root, "TST", cut)
    fresh, _ = P.npz_freshness("TST_LONG")
    assert fresh is True
    _mk_npz(root, "TST", cut - 2 * 86400)
    fresh, reason = P.npz_freshness("TST_LONG")
    assert fresh is False and "STALE_NPZ_SKIPPED" in reason


def test_missing_file_defers_to_loader(fresh_root):
    fresh, reason = P.npz_freshness("NOSUCHSYM_LONG")
    assert fresh is True and reason == ""


def test_bypass_env(fresh_root, monkeypatch):
    root, cutoff = fresh_root
    _mk_npz(root, "TSTUSDC", cutoff - 30 * 86400)
    monkeypatch.setenv("V12_ALLOW_STALE_NPZ", "1")
    fresh, _ = P.npz_freshness("TSTUSDC_LONG")
    assert fresh is True


def test_prepare_sentinel_flows_to_invalid(fresh_root):
    root, cutoff = fresh_root
    _mk_npz(root, "TSTUSDC", cutoff - 30 * 86400)
    prep = P.prepare_batch("TSTUSDC_LONG", 30)
    assert isinstance(prep, dict) and "_stale" in prep and prep.get("npz_prepared") is None
    r = P.evaluate_prepared_sanitized(prep, {}, 30)
    assert r["valid"] is False and "STALE_NPZ_SKIPPED" in r["invalid_reason"]
    rs = P.evaluate_many_sanitized("TSTUSDC_LONG", [{}, {}], 30)
    assert len(rs) == 2 and all("STALE_NPZ_SKIPPED" in x["invalid_reason"] for x in rs)
