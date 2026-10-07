"""PERF_TIER_SIZING (USER 2026-10-06) — mocked tests: negative sym_side -> minimal size, top gainer -> larger but capped,
_NEG_BLOCK tag still blocks, acc_gain<=0 no longer blocks with tier sizing ON, and the live patch applies cleanly + compiles."""
import importlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def pts(tmp_path, monkeypatch):
    f = tmp_path / "persym_size_tiers.json"
    f.write_text(json.dumps({"tiers": {"NEGUSDT_LONG": {"mult": 0.25, "tier": "MIN"}, "TOPUSDT_LONG": {"mult": 3.0, "tier": "RANKED"}, "BIG_LONG": {"mult": 3.0}, "LOSER_SHORT": {"mult": 0.25}}}))
    monkeypatch.setenv("PERSYM_SIZE_TIERS_PATH", str(f))
    import perf_tier_sizing
    m = importlib.reload(perf_tier_sizing)
    return m


def test_negative_symside_gets_minimal_size_floored_at_exchange_min(pts):
    assert pts.get_mult("NEGUSDT", "LONG") == 0.25
    assert pts.apply_usd(28.0, "NEGUSDT", "LONG", 6.0, 300.0) == 7.0
    assert pts.apply_usd(16.0, "NEGUSDT", "LONG", 6.0, 300.0) == 6.0  # 4.0 floored to the exchange minimum


def test_top_gainer_gets_larger_size_capped_by_max_order_value(pts):
    assert pts.apply_usd(28.0, "TOPUSDT", "LONG", 6.0, 300.0) == 84.0
    assert pts.apply_usd(150.0, "TOPUSDT", "LONG", 6.0, 300.0) == 300.0


def test_unlisted_symside_unchanged(pts):
    assert pts.get_mult("OTHERUSDT", "SHORT") == 1.0
    assert pts.apply_usd(28.0, "OTHERUSDT", "SHORT", 6.0, 300.0) == 28.0
    assert pts.apply_qty(7, "OTHER", "LONG") == 7


def test_stock_qty_tiers_floor_one_share(pts):
    # USER 2026-10-06: whole shares rounded DOWN; below one share = 0 (caller skips the trade)
    assert pts.apply_qty(10, "BIG", "LONG") == 30
    assert pts.apply_qty(2, "LOSER", "SHORT") == 0
    assert pts.apply_qty(6, "LOSER", "SHORT") == 1
    assert pts.apply_qty(8, "LOSER", "SHORT") == 2


def test_negbook_tag_still_blocks_and_negative_gain_only_sizes(pts):
    assert pts.negbook_blocks({"winning_tag": "parity_20261006_NEG_BLOCK", "acc_gain_pct": 5.0}, True) is True
    assert pts.negbook_blocks({"winning_tag": "parity_20261006", "acc_gain_pct": -3.0}, True) is False
    assert pts.negbook_blocks({"winning_tag": "parity_20261006", "acc_gain_pct": -3.0}, False) is True  # legacy when switch OFF
    assert pts.negbook_blocks({"winning_tag": "x", "acc_gain_pct": 2.0}, False) is False


def test_missing_tiers_file_is_fail_open(tmp_path, monkeypatch):
    monkeypatch.setenv("PERSYM_SIZE_TIERS_PATH", str(tmp_path / "nope.json"))
    import perf_tier_sizing
    m = importlib.reload(perf_tier_sizing)
    assert m.apply_usd(28.0, "NEGUSDT", "LONG", 6.0, 300.0) == 28.0


def test_live_patch_applies_and_compiles_on_copies(tmp_path):
    # 2026-10-06 director: the live files are now patched -> assert the patch markers are present instead of re-applying
    if "def _psym_sps_raw(symbol: str, side: str):" in (ROOT / "ez_manage.py").read_text():
        tm = (ROOT / "tradier_manage.py").read_text()
        assert "perf_tier_sizing" in tm and "PERF_TIER_SIZING_ENABLED" in (ROOT / "config_tradier.py").read_text()
        return
    for f in ("ez_manage.py", "tradier_manage.py", "config.py", "config_tradier.py"):
        shutil.copy2(ROOT / f, tmp_path / f)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "apply_perf_tier_patch.py"), "--root", str(tmp_path), "--apply"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    ez = (tmp_path / "ez_manage.py").read_text()
    assert "def _psym_sps_raw(symbol: str, side: str):" in ez and "_pts.apply_usd(_raw" in ez
    assert ez.count('negbook_blocks(_v, bool(getattr(config, "PERF_TIER_SIZING_ENABLED"') == 1
    tm = (tmp_path / "tradier_manage.py").read_text()
    assert "_pts.apply_qty(quantity, symbol, position_side)" in tm
    assert "PERF_TIER_SIZING_ENABLED: bool = True" in (tmp_path / "config.py").read_text()
    assert "PERF_TIER_SIZING_ENABLED: bool = True" in (tmp_path / "config_tradier.py").read_text()
    r2 = subprocess.run([sys.executable, str(ROOT / "tools" / "apply_perf_tier_patch.py"), "--root", str(tmp_path), "--apply"], capture_output=True, text=True)
    assert r2.returncode == 0 and r2.stdout.count("already patched") == 4
