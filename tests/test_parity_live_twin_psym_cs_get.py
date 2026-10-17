"""ez_manage per-sym resolution contract (rewritten 2026-10-17 — the 2026-10-06 version asserted the pre-mandate layers).

USER 2026-10-06 SINGLE SOURCE + 2026-10-15 full-set mandate: `_psym_cs_get` IS `_psym_get`; one precedence,
  per_sym_store.get_overrides (the board's FULL frozen set) > cat_side TEMPLATE default > global config > caller default.
The per_sym_active_config.json overrides half (removed 2026-10-06) and a separate get_full_config snapshot read
(collapsed into get_overrides 2026-10-15) are NOT layers any more — a value present only there must never reach live.
USER 2026-10-17: a crypto sizing knob above the ACTIVE market mode's own value is clamped to it (mode ceiling)."""
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
    monkeypatch.setattr(ez_manage, "_ezm_per_sym_cfgs_path", js, raising=False)
    monkeypatch.setattr(ez_manage, "_ezm_per_sym_cfgs_mtime", None, raising=False)
    board = {"KNOB_S": True, "MAX_POSITION_SIZE": 10_000.0, "START_POSITION_SIZE": 1.0}
    monkeypatch.setattr(per_sym_store, "get_overrides", lambda ss: dict(board) if ss == "AAAUSDT_SHORT" else {})
    monkeypatch.setattr(per_sym_store, "get_full_config", lambda ss: {"KNOB_C": "SNAPSHOT", "KNOB_G": "SNAPSHOT"}, raising=False)
    cs = {"KNOB_C": "cat_side_val", "KNOB_J": "cat_j", "KNOB_S": "cat_s"}
    monkeypatch.setattr(ez_manage, "_ezm_cat_side_default", lambda s, sd, k: cs.get(k, ez_manage._EZM_CSD_MISSING))
    monkeypatch.setattr(ez_manage._ezm_base_config, "KNOB_G", "global_val", raising=False)
    yield


def test_precedence(iso):
    for g in (ez_manage._psym_cs_get, ez_manage._psym_get):
        assert g("AAAUSDT", "SHORT", "KNOB_S", None) is True            # board full set first
        assert g("AAAUSDT", "SHORT", "KNOB_J", None) == "cat_j"         # JSON overrides half is NOT a layer
        assert g("AAAUSDT", "SHORT", "KNOB_C", None) == "cat_side_val"  # no separate full_config snapshot read
        assert g("AAAUSDT", "SHORT", "KNOB_G", None) == "global_val"    # global after cat_side
        assert g("AAAUSDT", "SHORT", "KNOB_MISSING", "dflt") == "dflt"


def test_cs_getter_is_the_single_source(iso):
    for k in ("KNOB_S", "KNOB_J", "KNOB_C", "KNOB_G", "KNOB_MISSING"):
        assert ez_manage._psym_cs_get("AAAUSDT", "SHORT", k, "d") == ez_manage._psym_get("AAAUSDT", "SHORT", k, "d")


def test_mode_ceiling_on_board_sizes(iso, monkeypatch):
    """Above the active mode's own value -> clamped; below -> untouched (minimum mode off, ceiling on)."""
    monkeypatch.setattr(ez_manage._ezm_base_config, "CRYPTO_MINIMUM_TRADING_MODE", False, raising=False)
    monkeypatch.setattr(ez_manage._ezm_base_config, "CRYPTO_MODE_SIZE_CEILING_ENABLED", True, raising=False)
    cap = float(ez_manage._ezm_base_config.MAX_POSITION_SIZE)
    assert ez_manage._psym_get("AAAUSDT", "SHORT", "MAX_POSITION_SIZE", None) == cap
    assert ez_manage._psym_get("AAAUSDT", "SHORT", "START_POSITION_SIZE", None) == 1.0
    monkeypatch.setattr(ez_manage._ezm_base_config, "CRYPTO_MODE_SIZE_CEILING_ENABLED", False, raising=False)
    assert ez_manage._psym_get("AAAUSDT", "SHORT", "MAX_POSITION_SIZE", None) == 10_000.0


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
