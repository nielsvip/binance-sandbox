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


def _keys(root, keys):
    import json

    (root / "tradeable_keys.json").write_text(json.dumps(keys))


def test_tradeable_helpers():
    tsyms = {"FRESHUSDT", "AGUSDT"}
    assert G.is_tradeable_file("FRESHUSDT.npz", tsyms) is True
    assert G.is_tradeable_file("AG.npz", tsyms) is True
    assert G.is_tradeable_file("NOPE.npz", tsyms) is False
    assert G.is_tradeable_file("AAPL_BF.npz", {"AAPLUSDT"}) is False
    assert G.is_tradeable_file("anything.npz", None) is True
    assert G.is_derived("AAPL_BF") is True
    assert G.is_derived("AAPL_365") is True
    assert G.is_derived("VARS3_LONG") is True
    assert G.is_derived("FRESHUSDT") is False


def test_tradeable_only_exit_and_purge(tmp_path, monkeypatch):
    _fixtures(tmp_path)
    _keys(tmp_path, ["ang:FRESHUSDT_LONG", "ang:SHORTSTK_LONG"])
    monkeypatch.setattr(G, "ROOT", str(tmp_path))
    ind = tmp_path / "ind"
    ind.mkdir()
    for p in tmp_path.glob("*.npz"):
        p.rename(ind / p.name)
    assert G.main(["gate", "--dir", str(ind), "--tradeable-only"]) == 1
    assert G.main(["gate", "--dir", str(ind), "--purge-nontradeable"]) == 0
    assert (ind / "STALEUSDT.npz").exists()  # dry-run moves nothing
    assert G.main(["gate", "--dir", str(ind), "--purge-nontradeable", "--yes"]) == 0
    assert not (ind / "STALEUSDT.npz").exists()
    assert not (ind / "VARS3.npz").exists()
    assert (ind / "FRESHUSDT.npz").exists()
    assert (ind / "SHORTSTK.npz").exists()
    assert G.main(["gate", "--dir", str(ind), "--tradeable-only"]) == 1  # SHORTSTK still violates
    (ind / "SHORTSTK.npz").unlink()
    assert G.main(["gate", "--dir", str(ind), "--tradeable-only"]) == 0


def test_purge_refuses_without_keys(tmp_path, monkeypatch, capsys):
    _fixtures(tmp_path)
    monkeypatch.setattr(G, "ROOT", str(tmp_path / "emptyroot"))
    assert G.main(["gate", "--dir", str(tmp_path), "--purge-nontradeable", "--yes"]) == 1
    assert "PURGE_REFUSE" in capsys.readouterr().out


def test_protected_union_keeps_universe_and_progress(tmp_path, monkeypatch):
    import json
    (tmp_path / "data" / "daily_universe").mkdir(parents=True)
    (tmp_path / "data" / "daily_universe" / "last_good.json").write_text(json.dumps({"stocks": ["GE", "ORCL_LONG"], "crypto": ["AGLDUSDT_SHORT"]}))
    (tmp_path / "data" / "reports" / "lifecycle_pilot").mkdir(parents=True)
    (tmp_path / "data" / "reports" / "lifecycle_pilot" / "PLTR_LONG_v14_progress.json").write_text("{}")
    _keys(tmp_path, ["ang:FRESHUSDT_LONG"])
    monkeypatch.setattr(G, "ROOT", str(tmp_path))
    prot = G.protected_syms()
    assert {"FRESHUSDT", "GE", "ORCL", "AGLDUSDT", "PLTR"} <= prot
    assert G.is_tradeable_file("GE.npz", prot) is True
    assert G.is_tradeable_file("AGLDUSDT.npz", prot) is True
    assert G.is_tradeable_file("JUNKXYZ.npz", prot) is False
    assert G.protected_syms(str(tmp_path / "emptyroot")) is None
    import os, time
    stale = tmp_path / "data" / "reports" / "lifecycle_pilot" / "OLD_LONG_v14_progress.json"
    stale.write_text("{}")
    os.utime(stale, (time.time() - 8 * 86400, time.time() - 8 * 86400))
    assert "OLD" not in G.progress_syms() and "PLTR" in G.progress_syms()


def test_reverse_venue_tolerance():
    assert G.is_tradeable_file("AGLDUSDT.npz", {"AGLD"}) is True
    assert G.is_tradeable_file("AG.npz", {"AGUSDT"}) is True
    assert G.is_tradeable_file("AAPL_BF.npz", {"AAPL"}) is False


def test_shortx_exempt_maxhistory(tmp_path, monkeypatch):
    import json, time
    now = time.time()
    latest = (now // 900) * 900
    _w(tmp_path / "MAXUSDT.npz", 120, latest)
    _w(tmp_path / "MAXSTALEUSDT.npz", 120, latest - 10 * 900)
    _w(tmp_path / "TRUNCUSDT.npz", 40, latest)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "short_history_exempt.json").write_text(json.dumps({"MAXUSDT": {"maxdays": 120, "maxbars": 11520, "reason": "exchange-max", "date": "2026-10-13"}, "MAXSTALEUSDT": {"maxdays": 120, "maxbars": 11520, "reason": "exchange-max", "date": "2026-10-13"}, "TRUNCUSDT": {"maxdays": 120, "maxbars": 11520, "reason": "exchange-max", "date": "2026-10-13"}}))
    monkeypatch.setattr(G, "ROOT", str(tmp_path))
    assert G.load_shorthist_exempt() == {"MAXUSDT": 120.0, "MAXSTALEUSDT": 120.0, "TRUNCUSDT": 120.0}
    assert G.classify(str(tmp_path / "MAXUSDT.npz"), exempt_maxdays=120.0)[0] == "shortx"
    v, det = G.classify(str(tmp_path / "MAXSTALEUSDT.npz"), exempt_maxdays=120.0)
    assert v == "short" and "STALE_TAIL" in det
    assert G.classify(str(tmp_path / "TRUNCUSDT.npz"), exempt_maxdays=120.0)[0] == "short"
    assert G.classify(str(tmp_path / "MAXUSDT.npz"))[0] == "short"
    res = G.audit(str(tmp_path))
    assert any("MAXUSDT" in s for s in res["shortx"])
    assert G.load_shorthist_exempt(str(tmp_path / "emptyroot")) == {}
