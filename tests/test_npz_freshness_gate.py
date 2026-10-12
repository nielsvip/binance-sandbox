"""NPZ freshness gate — verdicts, modes, variant split, exit codes."""

import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import npz_freshness_gate as G


def _w(path, days_ago, end_ts):
    n = max(int((end_ts - (time.time() - days_ago * 86400)) / 900), 2)
    ts = np.linspace(time.time() - days_ago * 86400, end_ts, n)
    np.savez(path, timestamps=ts, wt1_W=np.zeros(3))


def _fixtures(d):
    now = time.time()
    latest = (now // 900) * 900
    _w(d / "FRESHUSDT.npz", 400, latest)
    _w(d / "STALEUSDT.npz", 400, latest - 10 * 900)
    _w(d / "SHORTUSDT.npz", 100, latest)
    _w(d / "FRESHSTK.npz", 400, now - 86400)
    _w(d / "STALESTK.npz", 400, now - 10 * 86400)
    _w(d / "SHORTSTK.npz", 100, now - 86400)
    _w(d / "VARS3.npz", 400, latest - 10 * 900)
    _w(d / "VARS3_LONG.npz", 400, latest - 10 * 900)


def test_verdicts(tmp_path):
    _fixtures(tmp_path)
    got = {p.name: G.classify(str(p))[0] for p in sorted(tmp_path.glob("*.npz"))}
    assert got["FRESHUSDT.npz"] == "ok"
    assert got["STALEUSDT.npz"] == "stale"
    assert got["SHORTUSDT.npz"] == "short"
    assert got["FRESHSTK.npz"] == "ok"
    assert got["STALESTK.npz"] == "stale"
    assert got["SHORTSTK.npz"] == "short"


def test_modes_are_precompute_modes():
    assert G.mode_of("AVAXUSDC") == "crypto"
    assert G.mode_of("AMAT") == "tradier"
    assert G.mode_of("XUSDC") == "crypto"


def test_plan_splits_variants():
    refresh, rebuild, unref = G.plan(
        ["SHORTUSDT.npz 100d", "VARS3.npz 100d"],
        ["STALEUSDT.npz lag10bars", "VARS3_LONG.npz lag10bars"],
    )
    assert refresh == ["STALEUSDT"]
    assert rebuild == ["SHORTUSDT"]
    assert sorted(unref) == ["VARS3", "VARS3_LONG"]


def test_purge_targets_only_variants(tmp_path):
    _fixtures(tmp_path)
    got = sorted(p.name for p in (Path(f) for f in G.variant_files(str(tmp_path))))
    assert got == ["VARS3.npz", "VARS3_LONG.npz"]


def test_purge_moves_with_yes(tmp_path):
    _fixtures(tmp_path)
    assert G.main(["gate", "--dir", str(tmp_path), "--purge-variants"]) == 0
    assert (tmp_path / "VARS3.npz").exists()  # dry-run moves nothing
    assert G.main(["gate", "--dir", str(tmp_path), "--purge-variants", "--yes"]) == 0
    assert not (tmp_path / "VARS3.npz").exists()
    assert not (tmp_path / "VARS3_LONG.npz").exists()
    assert (tmp_path / "FRESHUSDT.npz").exists()  # canonical untouched
    moved = list((tmp_path / ".purged_variants").rglob("VARS3.npz"))
    assert len(moved) == 1
    res = G.audit(str(tmp_path))
    assert all("VARS3" not in s for s in res["short"] + res["stale"] + res["bad"])


def test_exit_codes(tmp_path, capsys):
    _fixtures(tmp_path)
    assert G.main(["gate", "--dir", str(tmp_path)]) == 1
    (tmp_path / "STALEUSDT.npz").unlink()
    (tmp_path / "SHORTUSDT.npz").unlink()
    (tmp_path / "STALESTK.npz").unlink()
    (tmp_path / "SHORTSTK.npz").unlink()
    (tmp_path / "VARS3.npz").unlink()
    (tmp_path / "VARS3_LONG.npz").unlink()
    assert G.main(["gate", "--dir", str(tmp_path)]) == 0
    assert G.main(["gate", "--dir", str(tmp_path / "nope")]) == 1
