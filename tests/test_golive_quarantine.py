"""Quarantine gate in tools/golive_final.py: blocklisted symsides never promote."""
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _w30():
    return {"valid": True, "gain_pct": 5.0, "trades": 50, "tim_pct": 30.0, "max_dd_pct": 5.0}


def _w365():
    return {"valid": True, "gain_pct": 20.0, "trades": 100, "tim_pct": 30.0, "max_dd_pct": 5.0}


def _rec():
    return {"status": "QUALIFIED", "overrides": {"A": 1}, "w30": _w30(), "w365": _w365(),
            "span_365_days": 340, "confirm": {"valid": True, "gain_365d": 20.0, "trades": 100}}


def _stage(tmp, qual_syms, blocked_stems):
    (tmp / "data" / "autopilot_final").mkdir(parents=True)
    (tmp / "data" / "hourly_reconfig").mkdir(parents=True)
    (tmp / "data" / "engine_deploy").mkdir(parents=True)
    (tmp / "data" / "reports" / "lifecycle_pilot").mkdir(parents=True)
    (tmp / "data" / "wiring").mkdir(parents=True)
    (tmp / "backups").mkdir(parents=True)
    now = dt.datetime.now(dt.timezone.utc)
    (tmp / "data" / "engine_deploy" / "CURRENT.json").write_text(json.dumps({"engine_md5": "abc12345"}))
    q = {"finalized": True, "generated_at": now.isoformat(), "engine_md5": "abc12345",
         "expected": 2, "counts": {}, "evaluated": {s: _rec() for s in qual_syms}}
    (tmp / "data" / "autopilot_final" / "qualifiers_latest.json").write_text(json.dumps(q))
    bl = {"quarantined_charts": [{"stem": s, "reason": "t"} for s in blocked_stems]}
    (tmp / "data" / "reports" / "lifecycle_pilot" / "promotion_blocklist_e3.json").write_text(json.dumps(bl))
    (tmp / "data" / "hourly_reconfig" / "per_sym_active_config.json").write_text(json.dumps({}))
    (tmp / "data" / "hourly_reconfig" / "per_sym_active_config_stocks.json").write_text(json.dumps({}))
    (tmp / "DAILY_AVG_DELTA_CHECKLIST.md").write_text("")
    (tmp / "data" / "confirmed_365d.json").write_text(json.dumps({}))
    return tmp


def _run(tmp):
    env = dict(os.environ, GOLIVE_ROOT=str(tmp))
    return subprocess.run([sys.executable, str(ROOT / "tools" / "golive_final.py"), "--apply", "--force-time"],
                          capture_output=True, text=True, timeout=120, env=env, cwd=str(ROOT))


def test_quarantined_symside_demoted(tmp_path):
    _stage(tmp_path, ["AAUSDT_LONG", "BBUSDT_LONG"], ["AAUSDT_LONG_bh1p00_gain2p00_t10_30d_matrix"])
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    book = json.loads((tmp_path / "data" / "hourly_reconfig" / "per_sym_active_config.json").read_text())
    assert book["BBUSDT_LONG"]["winning_tag"].startswith("v15_FINAL")
    assert "_NEG_BLOCK" in book["AAUSDT_LONG"]["winning_tag"]
    assert "promotion-blocklisted" in book["AAUSDT_LONG"]["neg_block_reason"]
    rep = json.loads(sorted((tmp_path / "data" / "autopilot_final").glob("golive_report_*.json"))[-1].read_text())
    assert rep["gates"]["quarantine_demoted"] == ["AAUSDT_LONG"]


def test_no_blocklist_file_still_promotes(tmp_path):
    _stage(tmp_path, ["CCUSDT_LONG"], [])
    (tmp_path / "data" / "reports" / "lifecycle_pilot" / "promotion_blocklist_e3.json").unlink()
    r = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    book = json.loads((tmp_path / "data" / "hourly_reconfig" / "per_sym_active_config.json").read_text())
    assert book["CCUSDT_LONG"]["winning_tag"].startswith("v15_FINAL")
