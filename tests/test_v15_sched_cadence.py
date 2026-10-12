"""USER 2026-10-12 cadence: the sym_side ledger (gain + bh + trades + verdict) decides DAILY (W) / WEEKLY (M) /
BIWEEKLY (L); W is capped per pool for the 2-host fleet; the ledger wins over the gain-only rank."""

import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools import v15_fleet_scheduler as S
from tools import v15_symside_ledger as L


def _gains(n, venue="USDT", side="LONG"):
    return {f"S{i}{venue}_{side}": {"gain": float(n - i), "mtime": 1000.0 + i} for i in range(n)}


def test_ledger_overrides_gain_rank_and_adds_unknown_sides():
    g = _gains(10)
    base = S._gain_tiers(g, ledger={}, w_max=0)
    assert base["S0USDT_LONG"]["tier"] == "W" and base["S9USDT_LONG"]["tier"] == "L"
    led = {"S0USDT_LONG": {"tier": "L", "mtime": 5000.0}, "S9USDT_LONG": {"tier": "W", "mtime": 1.0}, "NEWUSDT_SHORT": {"tier": "M", "mtime": 777.0, "potential": 3.3}}
    t = S._gain_tiers(g, ledger=led, w_max=0)
    assert t["S0USDT_LONG"]["tier"] == "L" and t["S0USDT_LONG"]["mtime"] == 5000.0  # ledger wins, newest mtime kept
    assert t["S9USDT_LONG"]["tier"] == "W" and t["S9USDT_LONG"]["mtime"] == 1009.0
    assert t["NEWUSDT_SHORT"] == {"tier": "M", "gain": 3.3, "mtime": 777.0, "ledger": True}


def test_w_cap_per_pool_keeps_highest_gain():
    g = _gains(20)
    t = S._gain_tiers(g, ledger={}, w_max=3)
    w = [ss for ss, e in t.items() if e["tier"] == "W"]
    assert len(w) == 3 and set(w) == {"S0USDT_LONG", "S1USDT_LONG", "S2USDT_LONG"}
    assert t["S3USDT_LONG"]["tier"] == "M" and t["S3USDT_LONG"].get("w_capped")
    g2 = dict(g)
    g2.update(_gains(5, side="SHORT"))
    t2 = S._gain_tiers(g2, ledger={}, w_max=3)
    assert sum(1 for e in t2.values() if e["tier"] == "W") == 5  # 3 LONG pool + 2 SHORT pool (40% of 5)


def test_defer_windows_weekly_biweekly(monkeypatch):
    monkeypatch.delenv("V15_TIER_DEFER_M_D", raising=False)
    monkeypatch.delenv("V15_TIER_DEFER_L_D", raising=False)
    assert S._defer_windows() == {"M": 5.0, "L": 10.0}
    now = 10_000_000.0
    tiers = {"A_LONG": {"tier": "M", "mtime": now - 3 * 86400}, "B_LONG": {"tier": "L", "mtime": now - 8 * 86400}, "C_LONG": {"tier": "W", "mtime": now - 3 * 86400}}
    win = {"M": 5.0, "L": 10.0}
    assert S._side_deferred("A_LONG", tiers, now, win) and S._side_deferred("B_LONG", tiers, now, win)
    assert not S._side_deferred("C_LONG", tiers, now, win)
    assert S._refresh_due("C_LONG", tiers, now)  # W older than 24h -> daily refresh


def test_ledger_load_fresh_and_stale(tmp_path):
    p = tmp_path / "ledger.json"
    p.write_text(json.dumps({"sym_sides": {"X_LONG": {"sched_tier": "W", "mtime": 42.0, "potential": 9.9, "tier_why": "daily"}, "Y_SHORT": {"sched_tier": "ZZ", "mtime": 1.0}}}))
    now = time.time()
    led = S._ledger_load(p, now_ts=now)
    assert led == {"X_LONG": {"tier": "W", "mtime": 42.0, "potential": 9.9, "why": "daily"}}
    assert S._ledger_load(p, now_ts=now + 10 * 86400) == {}  # stale ledger fails open
    assert S._ledger_load(tmp_path / "missing.json", now_ts=now) == {}


def test_ledger_sched_tier_rules():
    assert L.sched_tier({"potential": 1.0, "bh": 0.3, "trades": 50})[0] == "L"  # lost cause
    assert L.sched_tier({"potential": -2.0, "bh": 9.0, "trades": 50})[0] == "L"  # loser
    assert L.sched_tier({"potential": 12.0, "bh": 9.0, "trades": 4})[0] == "L"  # no sample
    assert L.sched_tier({"potential": 12.0, "bh": 9.0, "trades": 40})[0] == "M"  # W decided by the daily-cap rank
    assert L.sched_tier({"potential": 1.0, "bh": 40.0, "trades": 40})[0] == "M"  # big mover never a lost cause
    assert L.sched_tier({"potential": 50.0, "bh": 9.0, "trades": 40, "verdict": "IMPOSSIBLE"})[0] == "L"
    assert L.sched_tier({"potential": None, "bh": None, "trades": 0})[0] == "M"


def test_ledger_build_ranks_daily_cap(tmp_path):
    d = tmp_path / "progress"
    d.mkdir()
    for i in range(6):
        (d / f"S{i}USDT_LONG_v14_progress.json").write_text(json.dumps({"final_gain": 30.0 - i * 5, "bh": 10.0, "final_trades": 50, "verdict": None, "tier": {"tier": "WINNER", "trades": 50}}))
    (d / "FLATUSDT_LONG_v14_progress.json").write_text(json.dumps({"final_gain": 1.0, "bh": 0.5, "final_trades": 50}))
    led = L.build([str(d / "*_v14_progress.json")], daily_cap=2, now=time.time())
    w = sorted(ss for ss, e in led.items() if e["sched_tier"] == "W")
    assert w == ["S0USDT_LONG", "S1USDT_LONG"]
    assert led["S2USDT_LONG"]["sched_tier"] == "M" and led["S2USDT_LONG"]["cadence_d"] == 5.0
    assert led["FLATUSDT_LONG"]["sched_tier"] == "L" and led["FLATUSDT_LONG"]["cadence_d"] == 10.0
    assert led["S0USDT_LONG"]["next_due"] > led["S0USDT_LONG"]["mtime"]
