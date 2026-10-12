"""USER 2026-10-12: V15_REPAIR_ALL_TIERS=1 opens GS/repair to every tier (every
sym_side over BH push). Default (unset) preserves the 2026-10-09 90%-on-winners
gates: GS for W/M/discovery, repair for W/discovery."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import v15_fleet_scheduler as S


def test_default_gates_preserved(monkeypatch):
    monkeypatch.delenv("V15_REPAIR_ALL_TIERS", raising=False)
    assert S._gs_allowed_for_tier("W", True) is True
    assert S._gs_allowed_for_tier("M", True) is True
    assert S._gs_allowed_for_tier("L", True) is False
    assert S._gs_allowed_for_tier("L", False) is True
    assert S._repair_allowed_for_tier("W", True) is True
    assert S._repair_allowed_for_tier("M", True) is False
    assert S._repair_allowed_for_tier("L", True) is False
    assert S._repair_allowed_for_tier("L", False) is True


def test_all_tiers_open_with_env(monkeypatch):
    monkeypatch.setenv("V15_REPAIR_ALL_TIERS", "1")
    for t in ("W", "M", "L"):
        assert S._gs_allowed_for_tier(t, True) is True
        assert S._repair_allowed_for_tier(t, True) is True


def test_quota_blocks_fresh_only_chain_drains():
    tiers = {"MCD_LONG": {"tier": "L"}, "MCD_SHORT": {"tier": "L"}}
    assert S._quota_blocks("MCD", tiers, {"MCD": {"LONG": ("need30", 1), "SHORT": ("terminal_ok", None)}}) is True
    assert S._quota_blocks("MCD", tiers, {"MCD": {"LONG": ("terminal_ok", None), "SHORT": ("needgs", 1)}}) is False
    assert S._quota_blocks("MCD", tiers, {"MCD": {"LONG": ("terminal_ok", None), "SHORT": ("needrepair", 1)}}) is False
    assert S._quota_blocks("MCD", tiers, {"MCD": {"LONG": ("terminal_ok", None), "SHORT": ("need365", 1)}}) is False
    tiers_w = {"A_LONG": {"tier": "W"}, "A_SHORT": {"tier": "M"}}
    assert S._quota_blocks("A", tiers_w, {"A": {"LONG": ("need30", 1), "SHORT": ("need30", 1)}}) is False
    assert S._quota_blocks("NEW", {}, {"NEW": {"LONG": ("need30", 1)}}) is False
