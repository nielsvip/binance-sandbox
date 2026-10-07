"""Parity lane B 2026-10-06: ez_manage._psym_cs_get resolves per-sym PROMOTION (overrides) > cat_side default > global
and never returns the per_sym_store full_config defaults snapshot (director rule: cat_side TEMPLATE baseline reaches live)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ez_manage
import per_sym_store


@pytest.fixture
def iso(monkeypatch, tmp_path):
    js = tmp_path / "per_sym_active_config.json"
    js.write_text(json.dumps({"_meta": {}, "AAAUSDT_SHORT": {"overrides": {"KNOB_J": "1h"}}}))
    monkeypatch.setattr(ez_manage, "_ezm_per_sym_cfgs_path", js)
    monkeypatch.setattr(ez_manage, "_ezm_per_sym_cfgs_mtime", None)
    monkeypatch.setattr(per_sym_store, "get_overrides", lambda ss: {"KNOB_S": True} if ss == "AAAUSDT_SHORT" else {})
    monkeypatch.setattr(per_sym_store, "get_full_config", lambda ss: {"KNOB_S": False, "KNOB_J": "4h", "KNOB_C": "SNAPSHOT", "KNOB_G": "SNAPSHOT"})
    cs = {"KNOB_C": "cat_side_val", "KNOB_J": "cat_j", "KNOB_S": "cat_s"}
    monkeypatch.setattr(ez_manage, "_ezm_cat_side_default", lambda s, sd, k: cs.get(k, ez_manage._EZM_CSD_MISSING))
    monkeypatch.setattr(ez_manage._ezm_base_config, "KNOB_G", "global_val", raising=False)
    yield


def test_precedence(iso):
    g = ez_manage._psym_cs_get
    assert g("AAAUSDT", "SHORT", "KNOB_S", None) is True          # sqlite overrides (promotion)
    assert g("AAAUSDT", "SHORT", "KNOB_J", None) == "1h"          # json overrides (promotion)
    assert g("AAAUSDT", "SHORT", "KNOB_C", None) == "cat_side_val"  # cat_side beats full_config snapshot
    assert g("AAAUSDT", "SHORT", "KNOB_G", None) == "global_val"  # global beats full_config snapshot
    assert g("AAAUSDT", "SHORT", "KNOB_MISSING", "dflt") == "dflt"


def test_old_psym_get_returns_snapshot_for_contrast(iso):
    assert ez_manage._psym_get("AAAUSDT", "SHORT", "KNOB_C", None) == "SNAPSHOT"


def test_lane_b_call_sites_use_cs_getter():
    src = (Path(__file__).resolve().parents[1] / "ez_manage.py").read_text()
    for needle in (
        "_lpog_get = lambda _k, _d=None: _psym_cs_get(symbol, position_side, _k, _d)",
        '_psym_cs_get(symbol, position_side, "DC_PRIOR_BAR_CHANNEL", True)',
        "_lbsz_get = lambda _k, _d: _psym_cs_get(symbol, position.position_side, _k, _d)",
        '_psym_cs_get(symbol, position_side, "MULTI_TF_EXIT_ENABLED", False)',
        "_get_p = lambda _k, _d: _psym_cs_get(_sym_p, _side_p, _k, _d)",
    ):
        assert needle in src, needle
    assert src.count("_lbxc.exit_confirm(") == 2
