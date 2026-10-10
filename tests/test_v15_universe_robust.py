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
