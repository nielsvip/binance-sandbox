"""test_switch_parity — registrar gates + multi-surface atomicity + sync editor.

Isolated via SWITCH_PARITY_ROOT / PER_SYM_STORE_DB / CAT_SIDE_DEFAULTS_PATH;
teardown rebinds the real modules so later test files are unaffected.
"""

import importlib
import json
import os
import re
from pathlib import Path

import pytest

import switch_parity as SP

CAT_FIXTURE = {
    "CRYPTO_LONG": {"SW_BOOL": False, "SW_TF": "15m", "SW_FLOAT": 2.5, "SW_INT": 10},
    "CRYPTO_SHORT": {"SW_BOOL": False, "SW_TF": "15m", "SW_FLOAT": 2.5, "SW_INT": 10},
    "STOCKS_LONG": {"SW_BOOL": False, "SW_TF": "15m", "SW_FLOAT": 2.5, "SW_INT": 10},
    "STOCKS_SHORT": {"SW_BOOL": False, "SW_TF": "15m", "SW_FLOAT": 2.5, "SW_INT": 10},
}

EVIDENCE = {
    "n_promoted": 2,
    "valid": True,
    "gain_pct": 5.25,
    "trades": 40,
    "tim_pct": 55.0,
    "max_dd_pct": 8.0,
    "pool_sharpe": 0.6,
    "bh_pct": 2.0,
    "baseline_gain": 1.0,
    "engine_md5": "testeng1",
    "template_md5": "t" * 32,
    "defaults_round": "test-round",
}


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setenv("SWITCH_PARITY_ROOT", str(tmp_path))
    monkeypatch.setenv("PER_SYM_STORE_DB", str(tmp_path / "store.db"))
    (tmp_path / "cat.json").write_text(json.dumps(CAT_FIXTURE))
    monkeypatch.setenv("CAT_SIDE_DEFAULTS_PATH", str(tmp_path / "cat.json"))
    import cat_side_defaults
    import per_sym_store

    importlib.reload(cat_side_defaults)
    importlib.reload(per_sym_store)
    yield tmp_path
    for k in ("SWITCH_PARITY_ROOT", "PER_SYM_STORE_DB", "CAT_SIDE_DEFAULTS_PATH"):
        os.environ.pop(k, None)
    importlib.reload(cat_side_defaults)
    importlib.reload(per_sym_store)


def _book(iso, name="per_sym_active_config.json"):
    p = iso / "data" / "hourly_reconfig" / name
    return json.loads(p.read_text())


def test_register_happy_path_crypto(iso):
    rep = SP.register_workbook_result(
        "BTCUSDC_LONG", {"SW_BOOL": True, "SW_TF": "1h"}, dict(EVIDENCE)
    )
    assert rep["registered"] is True
    assert rep["tag"].startswith("v15_30D_")
    entry = _book(iso)["BTCUSDC_LONG"]
    assert entry["overrides"] == {"SW_BOOL": True, "SW_TF": "1h"}
    assert entry["full_config"]["SW_BOOL"] is True
    assert entry["full_config"]["SW_FLOAT"] == 2.5
    import per_sym_store as pss

    assert pss.get_overrides("BTCUSDC_LONG") == {"SW_BOOL": True, "SW_TF": "1h"}
    assert pss.get_full_config("BTCUSDC_LONG")["SW_TF"] == "1h"
    led = (iso / "data" / "parity_promotions.jsonl").read_text().strip().splitlines()
    assert len(led) == 1 and json.loads(led[0])["sym_side"] == "BTCUSDC_LONG"
    assert rep["verify"]["ok"] is True


def test_register_disabled_env(iso, monkeypatch):
    monkeypatch.setenv("SWITCH_PARITY_REGISTER", "0")
    rep = SP.register_workbook_result("BTCUSDC_LONG", {"SW_BOOL": True}, dict(EVIDENCE))
    assert rep["registered"] is False
    assert not (iso / "data").exists()


def test_register_refuses_zero_promotions(iso):
    ev = dict(EVIDENCE, n_promoted=0)
    rep = SP.register_workbook_result("BTCUSDC_LONG", {"SW_BOOL": True}, ev)
    assert rep["registered"] is False and "n_promoted" in rep["reason"]
    assert not (iso / "data").exists()


def test_register_refuses_unqualified(iso):
    for ev in (
        dict(EVIDENCE, valid=False),
        dict(EVIDENCE, gain_pct=-1.0),
        dict(EVIDENCE, trades=3),
        dict(EVIDENCE, tim_pct=95.0),
    ):
        rep = SP.register_workbook_result("BTCUSDC_LONG", {"SW_BOOL": True}, ev)
        assert rep["registered"] is False, ev
    assert not (
        iso / "data" / "hourly_reconfig" / "per_sym_active_config.json"
    ).exists()


def test_register_refuses_secrets_and_unknown_and_type(iso):
    r1 = SP.register_workbook_result(
        "BTCUSDC_LONG", {"SW_BOOL": True, "TRADIER_API_KEY": "x"}, dict(EVIDENCE)
    )
    assert r1["registered"] is False and "secret" in r1["reason"].lower()
    r2 = SP.register_workbook_result(
        "BTCUSDC_LONG", {"NO_SUCH_SWITCH": 1}, dict(EVIDENCE)
    )
    assert r2["registered"] is False and "not a cat_side" in r2["reason"]
    r3 = SP.register_workbook_result(
        "BTCUSDC_LONG", {"SW_BOOL": True, "SW_TF": 15}, dict(EVIDENCE)
    )
    assert r3["registered"] is False and "type-gate" in r3["reason"]
    assert not (iso / "data").exists()


def test_neg_block_preserved_then_lifted(iso):
    book_p = iso / "data" / "hourly_reconfig" / "per_sym_active_config.json"
    book_p.parent.mkdir(parents=True, exist_ok=True)
    book_p.write_text(
        json.dumps(
            {
                "BTCUSDC_LONG": {
                    "winning_tag": "old_NEG_BLOCK",
                    "overrides": {"SW_BOOL": False},
                }
            }
        )
    )
    r1 = SP.register_workbook_result("BTCUSDC_LONG", {"SW_BOOL": True}, dict(EVIDENCE))
    assert r1["registered"] is True and r1["neg_block_preserved"] is True
    assert _book(iso)["BTCUSDC_LONG"]["winning_tag"].endswith("_NEG_BLOCK")
    r2 = SP.register_workbook_result(
        "BTCUSDC_LONG", {"SW_BOOL": True}, dict(EVIDENCE), lift_block=True
    )
    assert r2["registered"] is True and "_NEG_BLOCK" not in r2["tag"]
    assert (iso / "backups").exists()


def test_verify_symside_catches_tamper(iso):
    SP.register_workbook_result("BTCUSDC_LONG", {"SW_BOOL": True}, dict(EVIDENCE))
    assert SP.verify_symside("BTCUSDC_LONG", root=iso)["ok"] is True
    book_p = iso / "data" / "hourly_reconfig" / "per_sym_active_config.json"
    raw = json.loads(book_p.read_text())
    raw["BTCUSDC_LONG"]["overrides"]["SW_BOOL"] = False
    book_p.write_text(json.dumps(raw))
    rep = SP.verify_symside("BTCUSDC_LONG", root=iso)
    assert rep["ok"] is False and any("SW_BOOL" in m for m in rep["mismatches"])


def test_register_stocks_updates_trb_overlay(iso):
    trb = iso / "data" / "hourly_reconfig" / "trb" / "active_config.json"
    trb.parent.mkdir(parents=True, exist_ok=True)
    trb.write_text(json.dumps({"AAPL_LONG": {"overrides": {"SW_BOOL": False}}}))
    rep = SP.register_workbook_result("AAPL_LONG", {"SW_BOOL": True}, dict(EVIDENCE))
    assert rep["registered"] is True and rep.get("trb_overlay") == "updated"
    assert json.loads(trb.read_text())["AAPL_LONG"]["overrides"] == {"SW_BOOL": True}
    assert _book(iso, "per_sym_active_config_stocks.json")["AAPL_LONG"][
        "overrides"
    ] == {"SW_BOOL": True}


def test_coerce_matrix():
    assert SP.coerce_like("False", True) == (True, False)
    assert SP.coerce_like("38", 15) == (True, 38)
    assert SP.coerce_like(15, "OFF")[0] is False
    assert SP.coerce_like("15m", 0.5)[0] is False
    assert SP.coerce_like(True, 15)[0] is False
    assert SP.coerce_like("x", {"a": 1})[0] is False


def test_sync_editor_on_real_copies(tmp_path):
    real = Path(__file__).resolve().parents[1]
    src = (real / "config.py").read_text().splitlines()
    pick = next(
        l
        for l in src
        if re.match(r"    [A-Z][A-Z0-9_]+: bool = (True|False)\s*(#|$)", l)
    )
    key = pick.split(":")[0].strip()
    bold = False if "= True" in pick.split("#")[0] else True
    (tmp_path / "backups").mkdir()
    shutil_copy = real / "config.py"
    (tmp_path / "config.py").write_text(shutil_copy.read_text())
    audit = {
        "mismatches": [
            {
                "cat_side": "CRYPTO_LONG",
                "key": key,
                "bold": bold,
                "class": "bold-vs-global",
                "global_differs": True,
                "quick_differs": False,
            }
        ],
        "bolds": {c: {} for c in SP.CAT_SIDES},
    }
    import unittest.mock as mock

    with mock.patch.object(SP, "verify_default_surfaces", return_value=audit):
        plan = SP.sync_default_surfaces("CRYPTO_LONG", root=tmp_path)
        assert len(plan["planned"]) == 1 and plan["planned"][0]["key"] == key
        refused = SP.sync_default_surfaces("CRYPTO_LONG", apply=True, root=tmp_path)
        assert "LOCKED" in refused["error"]
        done = SP.sync_default_surfaces(
            "CRYPTO_LONG", apply=True, confirm_unlocked=True, root=tmp_path
        )
        assert done["applied"] and done["applied"][0]["file"] == "config.py"
    got = [
        l
        for l in (tmp_path / "config.py").read_text().splitlines()
        if l.strip().startswith(key + ":")
    ][0]
    assert f"= {bold}" in got.split("#")[0]
    assert (real / "config.py").read_text().splitlines()[src.index(pick)] == pick
    import py_compile

    py_compile.compile(str(tmp_path / "config.py"), doraise=True)


def test_verify_defaults_smoke_real_repo():
    rep = SP.verify_default_surfaces("CRYPTO_LONG")
    assert rep.get("compared", 0) > 100
    assert "mismatches" in rep and "per_side" in rep


def test_fallback_split_documented_not_hard():
    rep = SP.verify_default_surfaces("CRYPTO_LONG")
    rows = [
        m
        for m in rep["mismatches"]
        if m.get("key") == "REGIME_TRENDING_WT_REDUCE_FRAC_LOW"
    ]
    # 2026-10-06 daily-chain promotion: bolds are now 0.2/0.2/0.15/0.2 (STOCKS_LONG side split) and the crypto QuickConfig raw is synced to 0.2,
    # so CRYPTO_LONG has no mismatch row at all; any row left must be informational, never hard
    assert all(m["class"] in ("fallback-split", "side-split-cat-truth", "bold-vs-cat") for m in rows)
    hard = [
        m
        for m in rep["mismatches"]
        if m.get("class") in ("bold-vs-global", "bold-vs-quick")
    ]
    assert not [m for m in hard if m["key"] == "REGIME_TRENDING_WT_REDUCE_FRAC_LOW"]


def test_max_augments_excluded_by_mandate():
    assert SP._excluded("MAX_AUGMENTS_PER_POSITION") != ""
    rep = SP.verify_default_surfaces("CRYPTO_SHORT")
    assert not [
        m
        for m in rep["mismatches"]
        if m.get("key") == "MAX_AUGMENTS_PER_POSITION"
        and m.get("class") in ("bold-vs-global", "bold-vs-quick")
    ]


def test_sync_keys_filter_on_copies(tmp_path):
    real = Path(__file__).resolve().parents[1]
    (tmp_path / "backups").mkdir()
    (tmp_path / "config.py").write_text((real / "config.py").read_text())
    src = (real / "config.py").read_text().splitlines()
    picks = [
        l
        for l in src
        if re.match(r"    [A-Z][A-Z0-9_]+: bool = (True|False)\s*(#|$)", l)
    ][:2]
    audit = {"mismatches": [], "bolds": {c: {} for c in SP.CAT_SIDES}}
    for ln in picks:
        key = ln.split(":")[0].strip()
        audit["mismatches"].append(
            {
                "cat_side": "CRYPTO_LONG",
                "key": key,
                "bold": ("= True" not in ln.split("#")[0]),
                "class": "bold-vs-global",
                "global_differs": True,
                "quick_differs": False,
            }
        )
    want = audit["mismatches"][0]["key"]
    import unittest.mock as mock

    with mock.patch.object(SP, "verify_default_surfaces", return_value=audit):
        done = SP.sync_default_surfaces(
            "CRYPTO_LONG", apply=True, confirm_unlocked=True, root=tmp_path, keys=[want]
        )
        assert [p["key"] for p in done["planned"]] == [want]
        assert done["applied"] and done["applied"][0]["file"] == "config.py"


def test_startup_gate_real_repo():
    g = SP.startup_gate("CRYPTO_LONG")
    assert g["ok"] is True and g["hard"] == []
    g2 = SP.startup_gate("CRYPTO_LONG")
    assert g2["cached"] is True and g2["ok"] is True


def test_startup_gate_off_switch(monkeypatch):
    monkeypatch.setenv("SWITCH_PARITY_GATE", "off")
    g = SP.startup_gate("CRYPTO_LONG")
    assert g["ok"] is True and any("off" in w for w in g["warnings"])


def test_sync_cat_keys_fill_and_fossil_hold(iso):
    cat_p = iso / "data" / "cat_side_defaults_4.json"
    cat_p.parent.mkdir(parents=True, exist_ok=True)
    cat_p.write_text(
        json.dumps(
            {
                "CRYPTO_LONG": {"SW_BOOL": False},
                "CRYPTO_SHORT": {"MOM3_FILTER_TF": "OFF"},
            }
        )
    )
    audit = {
        "mismatches": [
            {
                "cat_side": "CRYPTO_LONG",
                "key": "SW_TF",
                "bold": "1h",
                "class": "bold-vs-cat",
                "cat": "15m",
            },
            {
                "cat_side": "CRYPTO_SHORT",
                "key": "MOM3_FILTER_TF",
                "bold": "15m",
                "class": "bold-vs-cat",
                "cat": "OFF",
            },
            {
                "cat_side": "CRYPTO_LONG",
                "key": "SW_BAD",
                "bold": {"x": 1},
                "class": "bold-vs-cat",
                "cat": 0,
            },
        ],
        "missing_from_cat": ["CRYPTO_LONG:SW_FLOAT"],
        "bolds": {"CRYPTO_LONG": {"SW_FLOAT": 2.5}, "CRYPTO_SHORT": {}},
    }
    import unittest.mock as mock

    with mock.patch.object(SP, "verify_default_surfaces", return_value=audit):
        plan = SP.sync_cat_keys(root=iso)
        assert {p["key"] for p in plan["planned"]} == {"SW_TF", "SW_FLOAT"}
        assert any("MOM3" in f for f in plan["fossils_held"])
        assert any("SW_BAD" in s for s in plan["skipped"])
        assert plan["applied"] is False
        done = SP.sync_cat_keys(root=iso, apply=True)
        assert done["applied"] is True
    back = json.loads(cat_p.read_text())
    assert back["CRYPTO_LONG"]["SW_TF"] == "1h"
    assert back["CRYPTO_LONG"]["SW_FLOAT"] == 2.5
    assert back["CRYPTO_SHORT"]["MOM3_FILTER_TF"] == "OFF"
    assert list((iso / "backups").glob("before_parity_catfill_*.json"))
    import per_sym_store as pss

    assert pss.kv_get(pss.KV_CAT_SIDE_DEFAULTS_4)["CRYPTO_LONG"]["SW_TF"] == "1h"


def test_partner_cat_fallback_makes_side_split_informational():
    for cs in ("STOCKS_LONG", "STOCKS_SHORT"):
        rep = SP.verify_default_surfaces(cs)
        hard = [m["key"] for m in rep["mismatches"] if m.get("class") in ("bold-vs-global", "bold-vs-quick")]
        assert "DC_HARD_STOP_TF" not in hard and "MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY" not in hard and "REGIME_TRENDING_WT_REDUCE_FRAC_LOW" not in hard


def test_sync_promoted_bold_vs_cat_and_overlay_add(tmp_path, monkeypatch):
    rep = SP.sync_default_surfaces(None, apply=False, keys=["FAST_RISER_DOUBLE_ENABLED"])
    assert not rep.get("error")
    assert not [p for p in rep["needs_manual"] if p.get("key") == "FAST_RISER_DOUBLE_ENABLED" and "overlay" in str(p.get("why"))]
