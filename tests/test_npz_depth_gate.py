"""v15_npz_prep depth gate — a tail-current NPZ that is far shorter than its own klines must rebuild once.

USER 2026-10-16: 53 of 112 live-lane symbols held a ~93-day NPZ against 730+ days of klines (ADP/CDW: ONE bar)
because every gate was age/tail-staleness. U came back `action: none, npz_bars: 821` from the deepening driver.
"""
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import v15_npz_prep as P

DAY = 86400
NOW = 1792200000


def _env(tmp_path, monkeypatch, npz_span_d, kline_span_d, tail_ok=True, real=False):
    ind = tmp_path / "backtest_v8" / "indicators"
    ind.mkdir(parents=True, exist_ok=True)
    (ind / "U.npz").write_bytes(b"x")
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(P, "ROOT", tmp_path)
    monkeypatch.setattr(P, "IND", ind)
    t1 = NOW - 3600
    monkeypatch.setattr(P, "last_bar", lambda p: (int(t1 - npz_span_d * DAY), int(t1), 821, 40))
    monkeypatch.setattr(P, "kline_span", lambda s, v: (int(t1 - kline_span_d * DAY), int(t1)))
    monkeypatch.setattr(P, "kline_last", lambda s, v: int(t1))
    monkeypatch.setattr(P.time, "time", lambda: NOW)
    cut = types.ModuleType("tools.npz_cutoffs")
    cut.tail_currency = lambda t, venue, now: (tail_ok, "+0min vs RTH close")
    pkg = sys.modules.get("tools") or types.ModuleType("tools")
    monkeypatch.setitem(sys.modules, "tools", pkg)
    monkeypatch.setitem(sys.modules, "tools.npz_cutoffs", cut)
    monkeypatch.setitem(sys.modules, "tools.v15_npz_freeze", types.ModuleType("tools.v15_npz_freeze"))
    bld = types.ModuleType("backtest_v8_precompute")
    bld.compute_symbol = lambda s, m: False
    monkeypatch.setitem(sys.modules, "backtest_v8_precompute", bld)
    monkeypatch.setattr(sys, "argv", ["v15_npz_prep.py", "--symbol", "U"] + ([] if real else ["--dry-run"]))


def _run(capsys):
    with pytest.raises(SystemExit):
        P.main()
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_short_npz_with_a_current_tail_rebuilds(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch, npz_span_d=93, kline_span_d=745)
    r = _run(capsys)
    assert r["action"] == "would_regen" and r["depth_regen"] == pytest.approx(652, abs=1), r
    assert "depth: npz span 93d vs klines 745d" in r["reason"]


def test_a_deep_npz_is_left_alone(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch, npz_span_d=740, kline_span_d=745)
    r = _run(capsys)
    assert r["action"] == "none" and r["reason"] == "tail current to latest closed bar", r
    assert "depth_regen" not in r


def test_one_bar_stub_rebuilds(tmp_path, monkeypatch, capsys):
    """ADP/CDW held 1-bar NPZs against 14-15k klines and could never dispatch."""
    _env(tmp_path, monkeypatch, npz_span_d=0, kline_span_d=745)
    assert _run(capsys)["action"] == "would_regen"


def test_a_dry_run_stamps_nothing(tmp_path, monkeypatch, capsys):
    """Found 2026-10-16 by running it: --dry-run stamped the attempt, so the REAL pass that followed skipped the
    symbol it had just diagnosed. A dry run must leave no trace."""
    _env(tmp_path, monkeypatch, npz_span_d=93, kline_span_d=745)
    assert _run(capsys)["action"] == "would_regen"
    assert not (tmp_path / "data" / "npz_depth_attempts.json").exists()


def test_a_real_attempt_is_stamped_so_it_cannot_loop(tmp_path, monkeypatch, capsys):
    """A builder that caps its own output would otherwise re-run a full rebuild on every single dispatch."""
    _env(tmp_path, monkeypatch, npz_span_d=93, kline_span_d=745, real=True)
    assert _run(capsys)["action"] == "regen_failed"
    st = json.loads((tmp_path / "data" / "npz_depth_attempts.json").read_text())
    assert st["U"]["npz_bars_before"] == 821
    _env(tmp_path, monkeypatch, npz_span_d=93, kline_span_d=745)
    r = _run(capsys)
    assert r["action"] == "none" and "depth_regen" not in r, "second pass on the same kline head must be free"


def test_deeper_klines_arriving_re_arm_the_gate(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch, npz_span_d=93, kline_span_d=745, real=True)
    assert _run(capsys)["action"] == "regen_failed"
    _env(tmp_path, monkeypatch, npz_span_d=93, kline_span_d=900)
    assert _run(capsys)["action"] == "would_regen", "a new kline head is a new attempt"


def test_threshold_is_tunable(tmp_path, monkeypatch, capsys):
    _env(tmp_path, monkeypatch, npz_span_d=93, kline_span_d=745)
    monkeypatch.setattr(sys, "argv", ["v15_npz_prep.py", "--symbol", "U", "--dry-run", "--max-span-short-d", "9999"])
    assert _run(capsys)["action"] == "none"


def test_rebuild_backups_are_capped_per_symbol(tmp_path):
    """Each regen parks a ~15 MB copy of the previous NPZ in ~/npzprep/backup and nothing pruned them."""
    bk = tmp_path / "backup"
    bk.mkdir()
    for ts in (1792100000, 1792200000, 1792300000, 1792400000):
        (bk / f"U.{ts}.npz").write_bytes(b"x")
    (bk / "ADP.1792100000.npz").write_bytes(b"x")
    P.prune_rebuild_backups(bk, "U")
    assert sorted(q.name for q in bk.glob("U.*.npz")) == ["U.1792300000.npz", "U.1792400000.npz"]
    assert (bk / "ADP.1792100000.npz").exists(), "another symbol's copies are untouched"
    P.prune_rebuild_backups(bk, "MISSING")
    P.prune_rebuild_backups(bk, "U", keep=0)
    assert not list(bk.glob("U.*.npz"))
