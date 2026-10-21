"""NPZ inventory watch: missing/stale builds bounded, fresh untouched."""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import v15_npz_inventory_watch as W  # noqa: E402

NOW = 1792617600.0


def _root(tmp_path, keys, trb_long, trb_short, files):
    (tmp_path / "backtest_v8" / "indicators").mkdir(parents=True)
    (tmp_path / "tradeable_keys.json").write_text(json.dumps(keys))
    (tmp_path / "symbols_trb_long.json").write_text(json.dumps(trb_long))
    (tmp_path / "symbols_trb_short.json").write_text(json.dumps(trb_short))
    for name, age_s in files:
        p = tmp_path / "backtest_v8" / "indicators" / name
        p.write_text("x")
        ts = NOW - age_s
        os.utime(p, (ts, ts))
    return tmp_path


def test_classify_missing_stale_fresh(tmp_path):
    root = _root(
        tmp_path,
        ["ang:XRPUSDC_LONG", "fin:AAPL_LONG"],
        ["AAPL"],
        ["MSFT"],
        [("XRPUSDC_15m.npz", 100), ("AAPL_15m.npz", 100000), ("MSFT_15m.npz", 100)],
    )
    missing, stale, fresh = W.classify(root=root, max_age_s=3600, now=NOW)
    assert missing == []
    assert "XRPUSDC" in fresh
    assert "AAPL" in stale
    assert "MSFT" in fresh


def test_mode_split():
    assert W.mode_of("XRPUSDC") == "crypto"
    assert W.mode_of("AAPL") == "tradier"


def test_dry_run_builds_nothing(tmp_path):
    root = _root(tmp_path, ["ang:NEWCOINUSDT_LONG"], [], [], {})
    res = W.watch(max_age_s=3600, max_builds=4, apply=False, root=root)
    assert res["missing"] == ["NEWCOINUSDT"]
    assert res["would_build"] == ["NEWCOINUSDT"]
    assert res["built"] == []


def test_apply_uses_builder_stub(tmp_path):
    root = _root(tmp_path, ["ang:NEWCOINUSDT_LONG"], [], [], {})
    stub = ["touch", str(tmp_path / "built_marker")]
    res = W.watch(max_age_s=3600, max_builds=4, apply=True, root=root, builder=stub)
    assert len(res["built"]) == 1 and not res["failed"]
    assert (tmp_path / "built_marker").exists()


def test_cap_bounds_builds(tmp_path):
    root = _root(tmp_path, [f"ang:S{i}USDT_LONG" for i in range(6)], [], [], {})
    res = W.watch(max_age_s=3600, max_builds=2, apply=False, root=root)
    assert len(res["would_build"]) == 2 and res["would_build_truncated"] is True
