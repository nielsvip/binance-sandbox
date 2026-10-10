"""Universe allowlist robustness (USER 2026-10-10: non-tradeable must not burn fleet compute).

tradeable_keys.json is rewritten live and can be caught mid-write (concatenated tail).
A strict-parse failure used to silently zero the crypto allowlist (either idling crypto or
fail-opening the entire fleet). The builder must salvage the first complete array.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


def test_corrupt_tradeable_file_still_yields_crypto_allowlist(tmp_path):
    import v15_universe as U

    good = ["ang:BTCUSDC_LONG", "fin:ETHUSDC_SHORT", "men:SOLUSDC_LONG"]
    corrupt = json.dumps(good) + '\n_XX",\n  "men:PUMPUSDT_SHORT",\n  "men:ZENUSDT_SHORT"\n]'
    (tmp_path / "tradeable_keys.json").write_text(corrupt)
    allowed = U.tradeable_sym_sides(tmp_path)
    assert "BTCUSDC_LONG" in allowed, allowed
    assert "ETHUSDC_SHORT" in allowed, allowed
    assert "SOLUSDC_LONG" in allowed, allowed


def test_valid_tradeable_file_unchanged(tmp_path):
    import v15_universe as U

    good = ["ang:BTCUSDC_LONG", "fin:ETHUSDC_SHORT"]
    (tmp_path / "tradeable_keys.json").write_text(json.dumps(good))
    allowed = U.tradeable_sym_sides(tmp_path)
    assert {"BTCUSDC_LONG", "ETHUSDC_SHORT"} <= allowed, allowed


def test_scheduler_screams_on_empty_allowlist():
    src = (ROOT / "tools" / "v15_fleet_scheduler.py").read_text()
    assert "filter DISABLED, entire fleet calculating" in src


def test_quorum_collapse_serves_last_good(tmp_path):
    import v15_universe as U

    out = tmp_path / "data" / "daily_universe"
    out.mkdir(parents=True)
    lg = [f"SYM{i:03d}USDT_LONG" for i in range(300)]
    (out / "last_good.json").write_text(json.dumps({"allowed_sym_sides": lg}))
    (tmp_path / "tradeable_keys.json").write_text("GARBAGE{{not json at all")
    u = U.build(root=tmp_path)
    assert len(u["allowed_sym_sides"]) == 300, "collapsed build must serve last_good, got %d" % len(u["allowed_sym_sides"])
    assert u["degraded"] and "last_good" in u["degraded"], u.get("degraded")


def test_quorum_healthy_build_persists_last_good(tmp_path):
    import v15_universe as U

    good = [f"ang:SYM{i:03d}USDT_LONG" for i in range(10)]
    (tmp_path / "tradeable_keys.json").write_text(json.dumps(good))
    u = U.build(root=tmp_path)
    assert u["degraded"] is None, u.get("degraded")
    lg = json.loads((tmp_path / "data" / "daily_universe" / "last_good.json").read_text())
    assert len(lg["allowed_sym_sides"]) >= 10


def test_scheduler_autokill_fresh_gated_and_precise():
    src = (ROOT / "tools" / "v15_fleet_scheduler.py").read_text()
    assert 'how in ("file", "rebuilt-tradeable-changed")' in src, "autokill only on fresh universe"
    assert "V15_SCHED_AUTOKILL_DEAD" in src, "autokill must have env opt-out"
    assert '"sym_sides": sorted(' in src, "autokill must be sym_side-precise"
    assert "rebuilt-tradeable-changed" in src, "universe must rebuild when keys file is newer"
