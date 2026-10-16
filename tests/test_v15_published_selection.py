"""USER 2026-10-13: go-live source = latest PUBLISHED board per sym_side (tools/v15_published_selection.py) + golive published mode."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import v15_published_selection as PS  # noqa: E402
import v15_persym_golive as GL  # noqa: E402


def _db(tmp_path, rows):
    p = tmp_path / "store.db"
    c = sqlite3.connect(p)
    c.execute("create table per_sym_history (id integer primary key, sym_side text, updated_at text, cat_side text, winning_tag text, gain_pct real, baseline_gain real, bh_pct real, trades integer, pool_sharpe real, max_dd_pct real, tim_pct real, valid integer, defaults_round text, overrides_json text, meta_json text)")
    for r in rows:
        c.execute("insert into per_sym_history (sym_side, updated_at, cat_side, winning_tag, gain_pct, baseline_gain, bh_pct, trades, pool_sharpe, max_dd_pct, tim_pct, valid, defaults_round, overrides_json, meta_json) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", r)
    c.commit()
    c.close()
    return p


def _row(ss, at, gain, ov, source="fleet_orchestrator_publish"):
    return (ss, at, "CRYPTO_LONG", "pilot30d", gain, 1.0, 0.5, 40, 0.3, 3.0, 30.0, 1, "r", json.dumps(ov), json.dumps({"source": source, "host": "127.0.0.1", "progress": f"/p/{ss}.json"}))


def _man(d, ss, name, at, gain, ov):
    (d / f"{ss}_{name}_30d_matrix_manifest.json").write_text(json.dumps({"symside": ss, "published_utc": at, "metrics": {"gain_pct": gain}, "overrides_md5": PS.overrides_md5(ov)}))


def test_selection_rules(tmp_path):
    sheets = tmp_path / "sheets"
    sheets.mkdir()
    a, b = {"K": 1}, {"K": 2}
    db = _db(tmp_path, [
        _row("OKUSDT_LONG", "2026-10-12T00:00:00Z", 5.0, a),
        _row("OKUSDT_LONG", "2026-10-13T00:00:00Z", 9.0, b),  # latest row wins
        _row("DRIFTUSDT_LONG", "2026-10-13T00:00:00Z", 7.0, b),
        _row("NEWERUSDT_LONG", "2026-10-12T00:00:00Z", 7.0, b),
        _row("HISTUSDT_LONG", "2026-10-13T00:00:00Z", 3.0, b),
        _row("OLDUSDT_LONG", "2026-10-13T00:00:00Z", 3.0, b),
        _row("IGNUSDT_LONG", "2026-10-13T00:00:00Z", 3.0, b, source="switch_parity"),
    ])
    _man(sheets, "OKUSDT_LONG", "g9", "2026-10-13T00:00:30Z", 9.0, b)
    _man(sheets, "DRIFTUSDT_LONG", "g7", "2026-10-10T00:00:00Z", 7.0, a)
    _man(sheets, "NEWERUSDT_LONG", "g8", "2026-10-13T00:00:00Z", 8.0, a)
    _man(sheets, "OLDUSDT_LONG", "g1", "2026-10-05T00:00:00Z", 1.0, a)
    sel = PS.build(db, sheets)
    assert set(sel) == {"OKUSDT_LONG", "DRIFTUSDT_LONG", "NEWERUSDT_LONG", "HISTUSDT_LONG", "OLDUSDT_LONG"}
    assert sel["OKUSDT_LONG"]["verify"] == "MANIFEST_MD5_OK" and sel["OKUSDT_LONG"]["evidence"]["gain_pct"] == 9.0
    assert sel["DRIFTUSDT_LONG"]["refuse"].startswith("SET_DRIFTED_SINCE_PUBLISH")
    assert sel["NEWERUSDT_LONG"]["refuse"].startswith("MANIFEST_MD5_MISMATCH")
    assert sel["HISTUSDT_LONG"]["verify"].startswith("HISTORY_PUBLISH_ROW") and sel["HISTUSDT_LONG"]["manifest_ok"]
    assert sel["OLDUSDT_LONG"]["verify"].startswith("HISTORY_PUBLISH_ROW")
    strict = PS.build(db, sheets, strict=True)
    assert strict["HISTUSDT_LONG"]["refuse"].startswith("NO_SHEET_MANIFEST") and strict["OLDUSDT_LONG"]["refuse"].startswith("NO_MANIFEST_FOR_THIS_PUBLISH")
    assert strict["OKUSDT_LONG"]["manifest_ok"] is True


def test_golive_published_collected_strips_artefacts_and_skips_refused(tmp_path):
    doc = {"_meta": {"n": 2}, "sym_sides": {
        "AUSDT_LONG": {"manifest_ok": True, "overrides": {"K": 1, "MODE": "crypto", "ABLATION_X": True, "MAX_AUGMENTS_PER_POSITION": 0, "MAX_ORDER_VALUE": 0}, "evidence": {"gain_pct": -2.0, "trades": 12, "valid": True}},
        "BUSDT_LONG": {"manifest_ok": False, "refuse": "SET_DRIFTED_SINCE_PUBLISH: x", "overrides": {"K": 1}},
    }}
    p = tmp_path / "sel.json"
    p.write_text(json.dumps(doc))
    meta, col = GL.published_collected(p)
    assert col["AUSDT_LONG"]["overrides"] == {"K": 1}
    assert col["AUSDT_LONG"]["evidence"]["gain_pct"] == -2.0  # negative sets are NOT skipped (minimal tier size live)
    # MAX_AUGMENTS_PER_POSITION joined ARTEFACT_KEYS 2026-10-15 (switch deleted; boards published before the
    # deletion still carry it, so go-live drops it outright instead of applying a dead key).
    assert sorted(col["AUSDT_LONG"]["artefact_stripped"]) == ["ABLATION_X", "MAX_AUGMENTS_PER_POSITION", "MODE"]
    # one, not two: MAX_AUGMENTS_PER_POSITION moved to ARTEFACT_KEYS when the switch was deleted 2026-10-15,
    # so MAX_ORDER_VALUE=0 is the only remaining mandate strip.
    assert col["AUSDT_LONG"]["mandate_stripped"] == ["MAX_ORDER_VALUE=0"]
    assert col["BUSDT_LONG"]["skip"].startswith("published: SET_DRIFTED")
