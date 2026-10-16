"""per_sym_store.get_overrides serves the FULL measured set (USER 2026-10-15), not the sheet-key diff.

"we do not store overrides anymore, we store and apply the full set of switches and filters for every symbol and side
 ... defaults change every morning so overrides are absolutely useless"

The row keeps overrides_json (sheet keys), defaults_snapshot_json (the default layer frozen when the board was
written) and full_config_json (snapshot + overrides = what the board was measured with). Live must resolve the full
set so a morning per_sym_settings rebuild cannot move a qualified board, and the go-live integrity strip must apply
to the snapshot half too (a frozen MAX_ORDER_VALUE of 0 would block every order for the sym_side).
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _store(tmp_path, monkeypatch):
    db = tmp_path / "per_sym_store.db"
    monkeypatch.setenv("PER_SYM_STORE_DB", str(db))
    monkeypatch.setenv("PER_SYM_OVR_MEMO_SEC", "0")
    for m in [m for m in list(sys.modules) if m == "per_sym_store"]:
        del sys.modules[m]
    import per_sym_store as pss
    pss.DB_PATH = db
    pss._OVR_MEMO.clear()
    return pss


def _row(pss, sym_side, overrides, snapshot):
    full = dict(snapshot)
    full.update(overrides)
    pss.upsert(sym_side, dict(overrides), dict(snapshot), dict(full), {"winning_tag": "test"},
               json_path=Path(os.environ["PER_SYM_STORE_DB"]).parent / "book.json")


def test_full_set_is_served_not_the_diff(tmp_path, monkeypatch):
    pss = _store(tmp_path, monkeypatch)
    _row(pss, "BTCUSDC_LONG", {"SHEET_KEY": True}, {"SNAPSHOT_KEY": 7, "OTHER": "15m"})
    ov = pss.get_overrides("BTCUSDC_LONG")
    assert ov["SHEET_KEY"] is True
    # the snapshot half is served too: a morning default move can no longer reach this sym_side
    assert ov["SNAPSHOT_KEY"] == 7
    assert ov["OTHER"] == "15m"


def test_kill_switch_restores_the_diff(tmp_path, monkeypatch):
    pss = _store(tmp_path, monkeypatch)
    _row(pss, "BTCUSDC_LONG", {"SHEET_KEY": True}, {"SNAPSHOT_KEY": 7})
    monkeypatch.setenv("PER_SYM_APPLY_FULL_SET", "0")
    pss._OVR_MEMO.clear()
    ov = pss.get_overrides("BTCUSDC_LONG")
    assert ov == {"SHEET_KEY": True}


def test_mandate_strip_applies_to_the_snapshot_half(tmp_path, monkeypatch):
    pss = _store(tmp_path, monkeypatch)
    _row(pss, "BTCUSDC_LONG", {"SHEET_KEY": True},
         {"MAX_ORDER_VALUE": 0, "MODE": "crypto",
          "SIMPLE_PRICE_GT0_ENABLED": True, "ABLATION_DISABLE_REENTRY": True, "KEEP_ME": 1})
    ov = pss.get_overrides("BTCUSDC_LONG")
    # a zero order cap and the engine artefacts must never reach live, whatever the frozen snapshot says
    assert "MAX_ORDER_VALUE" not in ov
    for k in ("MODE", "SIMPLE_PRICE_GT0_ENABLED", "ABLATION_DISABLE_REENTRY"):
        assert k not in ov
    assert ov["KEEP_ME"] == 1 and ov["SHEET_KEY"] is True


def test_positive_max_order_value_survives(tmp_path, monkeypatch):
    pss = _store(tmp_path, monkeypatch)
    _row(pss, "BTCUSDC_LONG", {}, {"MAX_ORDER_VALUE": 300.0})
    assert pss.get_overrides("BTCUSDC_LONG")["MAX_ORDER_VALUE"] == 300.0


def test_upsert_invalidates_the_read_memo(tmp_path, monkeypatch):
    """switch_parity.verify_symside read the pre-write cached row and failed every registration."""
    pss = _store(tmp_path, monkeypatch)
    monkeypatch.setenv("PER_SYM_OVR_MEMO_SEC", "30.0")
    _row(pss, "BTCUSDC_LONG", {"A": 1}, {})
    assert pss.get_overrides("BTCUSDC_LONG")["A"] == 1  # warms the memo
    _row(pss, "BTCUSDC_LONG", {"A": 2}, {})
    assert pss.get_overrides("BTCUSDC_LONG")["A"] == 2


def test_missing_row_still_returns_none(tmp_path, monkeypatch):
    pss = _store(tmp_path, monkeypatch)
    assert pss.get_overrides("NOSUCHUSDT_LONG") is None
