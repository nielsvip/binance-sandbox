"""Ranking top-25 priority (USER 2026-10-14): winners/losers from the ranking
loops jump the sweep queue (after their gain tier) and recalculate daily.

Covers tools/v15_ranking_priority.py + its two consumers:
tools/v15_fleet_scheduler._place_order/_side_deferred and
v15_pilot._apply_ranking_floor. Fixture ranking files live in tmp_path
(V15_RANKING_ROOT); real data/ files are never touched.
"""

import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from tools import v15_fleet_scheduler as S
from tools import v15_ranking_priority as R
import v15_pilot as P


def _write_rankings(root, name="rankings.json", n=30, prefix="C"):
    d = root / "data"
    d.mkdir(parents=True, exist_ok=True)
    payload = {"%s%dUSDT" % (prefix, i): {"lt_rank": i + 1, "final_score_norm": float(100 - i)} for i in range(n)}
    (d / name).write_text(json.dumps(payload))
    return d / name


def _use_root(tmp_path, monkeypatch):
    _write_rankings(tmp_path, "rankings.json", prefix="C")
    _write_rankings(tmp_path, "tradier_rankings.json", prefix="S")
    monkeypatch.setenv("V15_RANKING_ROOT", str(tmp_path))
    monkeypatch.setenv("V15_RANKING_PRIORITY", "1")
    monkeypatch.delenv("RANKING_TOP_N", raising=False)
    monkeypatch.delenv("RANKING_MAX_AGE_H", raising=False)
    R.reset_cache()


def test_winners_long_losers_short_top25(tmp_path, monkeypatch):
    _use_root(tmp_path, monkeypatch)
    sets = R.load_ranking_sets(str(tmp_path))
    assert "C0USDT_LONG" in sets["long"] and "C24USDT_LONG" in sets["long"]
    assert "C25USDT_LONG" not in sets["long"]
    assert "C29USDT_SHORT" in sets["short"] and "C5USDT_SHORT" in sets["short"]
    assert "C4USDT_SHORT" not in sets["short"]
    assert "S0USDT_LONG" in sets["long"] and "S29USDT_SHORT" in sets["short"]


def test_missing_and_killed_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("V15_RANKING_ROOT", str(tmp_path))
    monkeypatch.setenv("V15_RANKING_PRIORITY", "1")
    R.reset_cache()
    assert R.ranking_sets()["pairs"] == frozenset()
    _use_root(tmp_path, monkeypatch)
    assert len(R.ranking_sets()["pairs"]) > 0
    monkeypatch.setenv("V15_RANKING_PRIORITY", "0")
    assert R.ranking_sets()["pairs"] == frozenset()
    assert R.ranking_boost("C0USDT_LONG") == 1.0


def test_stale_file_ignored(tmp_path, monkeypatch):
    p = _write_rankings(tmp_path, "rankings.json", prefix="C")
    old = time.time() - 49 * 3600.0
    os.utime(p, (old, old))
    monkeypatch.setenv("V15_RANKING_ROOT", str(tmp_path))
    monkeypatch.setenv("V15_RANKING_PRIORITY", "1")
    R.reset_cache()
    assert R.ranking_sets()["long"] == frozenset()


def test_place_order_ranking_breaks_tier_ties_only(tmp_path, monkeypatch):
    _use_root(tmp_path, monkeypatch)
    tiers = {
        "W0USDT_LONG": {"tier": "W", "gain": 9, "mtime": 1},
        "C29USDT_LONG": {"tier": "M", "gain": 1, "mtime": 1},
        "C29USDT_SHORT": {"tier": "M", "gain": 1, "mtime": 1},
        "M9USDT_LONG": {"tier": "M", "gain": 1, "mtime": 1},
        "M9USDT_SHORT": {"tier": "M", "gain": 1, "mtime": 1},
    }
    got = S._place_order([], [], ["M9USDT", "C29USDT", "W0USDT"], tiers)
    order = [s for s, _ in got]
    assert order[0] == "W0USDT", order  # BEST_WORST tier stays primary
    assert order.index("C29USDT") < order.index("M9USDT"), order  # ranking wins the M tie


def test_place_order_ignores_ranking_when_killed(tmp_path, monkeypatch):
    _use_root(tmp_path, monkeypatch)
    monkeypatch.setenv("V15_RANKING_PRIORITY", "0")
    tiers = {
        "C29USDT_LONG": {"tier": "M", "gain": 1, "mtime": 1},
        "C29USDT_SHORT": {"tier": "M", "gain": 1, "mtime": 1},
        "M9USDT_LONG": {"tier": "M", "gain": 1, "mtime": 1},
        "M9USDT_SHORT": {"tier": "M", "gain": 1, "mtime": 1},
    }
    got = S._place_order([], [], ["C29USDT", "M9USDT"], tiers)
    assert [s for s, _ in got] == ["C29USDT", "M9USDT"]  # input order preserved, old behavior


def test_side_deferred_skips_ranking_members(tmp_path, monkeypatch):
    _use_root(tmp_path, monkeypatch)
    now = time.time()
    tiers = {
        "C29USDT_SHORT": {"tier": "M", "gain": 1, "mtime": now - 3600.0},
        "M9USDT_SHORT": {"tier": "M", "gain": 1, "mtime": now - 3600.0},
    }
    assert S._side_deferred("C29USDT_SHORT", tiers, now) is False  # hot-now recalculates daily
    assert S._side_deferred("M9USDT_SHORT", tiers, now) is True


def test_pilot_floor_lifts_lightning_only(tmp_path, monkeypatch):
    _use_root(tmp_path, monkeypatch)
    monkeypatch.delenv("V15_RANKING_TIER_FLOOR", raising=False)
    name, meta = P._apply_ranking_floor("LOSER", {"source": "legacy", "focus_s": 0}, "C0USDT_LONG")
    assert name == "MEDIOCRE" and meta.get("ranking_floor") is True
    assert meta["focus_s"] == P._TIER_FOCUS_S["MEDIOCRE"]
    # LOST_CAUSE (near-zero compute waster) gets only a cheap STRICT re-measure, never a full board.
    name_lc, meta_lc = P._apply_ranking_floor("LOST_CAUSE", {"source": "legacy", "focus_s": 0}, "C0USDT_LONG")
    assert name_lc == "LOSER" and meta_lc.get("ranking_floor") == "loser"
    assert meta_lc["focus_s"] == P._TIER_FOCUS_S["LOSER"]
    name2, _ = P._apply_ranking_floor("LOST_CAUSE", {"source": "legacy"}, "M9USDT_LONG")
    assert name2 == "LOST_CAUSE"  # unranked keeps its measured tier
    name3, _ = P._apply_ranking_floor("WINNER", {"source": "ledger"}, "C0USDT_LONG")
    assert name3 == "WINNER"  # earned winner untouched
    monkeypatch.setenv("V15_RANKING_PRIORITY", "0")
    name4, _ = P._apply_ranking_floor("LOSER", {"source": "legacy"}, "C0USDT_LONG")
    assert name4 == "LOSER"  # kill switch restores old behavior
