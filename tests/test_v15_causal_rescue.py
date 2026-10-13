"""v15_causal_rescue: rescue parsing, ablation value, verdicts, fix_outcomes write, DB cap/prune."""

import json
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import v15_causal_learner as cl
import v15_causal_rescue as cr


def test_load_rescue_order_dedup_and_deny(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"rescue": [{"switch": "LR_BAND_LADDER_TF_TOP"}, {"switch": "ABLATION_X"}, {"switch": "MODE"}, {"switch": "PYRAMID_ENABLED"}, {"switch": "LR_BAND_LADDER_TF_TOP"}],
                             "fixes_proposed": [{"target": "DC_BREAK_FILTER_TF"}, {"target": "PYRAMID_ENABLED"}]}))
    assert cr.load_rescue(str(p)) == ["LR_BAND_LADDER_TF_TOP", "PYRAMID_ENABLED"]
    assert cr.load_rescue(str(p), include_proposed=2) == ["LR_BAND_LADDER_TF_TOP", "PYRAMID_ENABLED", "DC_BREAK_FILTER_TF"]
    assert cr.load_rescue(str(p), max_switches=1) == ["LR_BAND_LADDER_TF_TOP"]


def test_ablation_value_semantics():
    assert cr.ablation_value("MIN_HOLD_BARS", 6, 0) == 0
    assert cr.ablation_value("MIN_HOLD_BARS", 0, 0) is None
    assert cr.ablation_value("PYRAMID_ENABLED", True, True) is False
    assert cr.ablation_value("PYRAMID_ENABLED", False, False) is None
    assert cr.ablation_value("LR_BAND_LADDER_TF_TOP", "4h", "4h") == "OFF"
    assert cr.ablation_value("LR_BAND_LADDER_TF_TOP", "OFF", "OFF") is None
    assert cr.ablation_value("LR_BAND_LADDER_TF_TOP", "1h", "4h") == "4h"


def test_verdicts():
    h = {"d_gain": 1.0, "valid": True}
    n = {"d_gain": -1.0, "valid": True}
    assert cr.verdict_of([h, h, h])[0] == "DISABLE_CANDIDATE"
    assert cr.verdict_of([h, h])[0] == "INSUFFICIENT"
    assert cr.verdict_of([n])[0] == "KEEP"
    assert cr.verdict_of([h, n])[0] == "MIXED"
    assert cr.verdict_of([])[0] == "NOT_ACTIVE"
    assert cr.verdict_of([{"d_gain": 2.0, "valid": False}, h, h])[0] == "INSUFFICIENT"


def test_load_bases_deterministic(tmp_path):
    for ss, tr in (("BTCUSDT_LONG", 20), ("AAVEUSDT_LONG", 20), ("ZZZUSDT_LONG", 3), ("AAPL_SHORT", 15)):
        (tmp_path / f"{ss}_autopsy_base.json").write_text(json.dumps({"final": {"overrides": {"X": 1}}, "autopsy": {"combo_trades": tr}}))
    b = cr.load_bases(str(tmp_path), 1, salt="W1")
    assert b == cr.load_bases(str(tmp_path), 1, salt="W1")
    assert b["CRYPTO_LONG"][0][0] in ("AAVEUSDT_LONG", "BTCUSDT_LONG")  # ZZZ (3 trades) never picked
    assert [s for s, _ in b["STOCKS_SHORT"]] == ["AAPL_SHORT"]
    picks = {cr.load_bases(str(tmp_path), 1, salt=f"W{i}")["CRYPTO_LONG"][0][0] for i in range(20)}
    assert picks == {"AAVEUSDT_LONG", "BTCUSDT_LONG"}  # weekly salt rotates the base set


def test_profile_cands_and_bible_fallback():
    prof = {"cat_sides": {"CRYPTO_LONG": {"switch_cand": {"MIN_HOLD_BARS=2": {"n_sym": 5}, "MIN_HOLD_BARS=6": {"n_sym": 50}, "X=1": {"n_sym": 99}}}}}
    assert cr.profile_cands(prof, "CRYPTO_LONG", "MIN_HOLD_BARS", 1) == ["6"]
    bible = {"HTF_MIN_ALIGNED": {"agent_c_registry": {"valid_options": [0, 1, 2]}}}
    assert cr.profile_cands(prof, "CRYPTO_LONG", "HTF_MIN_ALIGNED", 2, bible) == ["0", "1"]


def test_summarise_and_ingest_outcomes(tmp_path):
    results = {
        "AUSDT_LONG": {"evals": {"SW_TF": {"loo": {"d_gain": 1.5, "valid": True, "d_trades": 2}, "naked": {"1h": {"d_gain": -0.5, "valid": True}}}}},
        "BUSDT_LONG": {"evals": {"SW_TF": {"loo": {"d_gain": 0.5, "valid": True}, "naked": {}}}},
        "CUSDT_LONG": {"evals": {"SW_TF": {"loo": {"not_active": True}, "naked": {}}}},
        "DUSDT_LONG": {"base": {"valid": False}, "evals": {"SW_TF": {"loo": {"d_gain": -9.0, "valid": True}, "naked": {}}}},
    }
    s = cr.summarise(results, {"CRYPTO_LONG": ["AUSDT_LONG", "BUSDT_LONG", "CUSDT_LONG", "DUSDT_LONG"]}, ["SW_TF"])
    e = s["SW_TF"]["CRYPTO_LONG"]
    assert e["verdict"] == "INSUFFICIENT" and e["n_loo"] == 2 and e["label"] == "[DIAGNOSTIC ONLY]" and e["n_base_invalid"] == 1
    assert e["naked"]["1h"]["n"] == 1 and e["naked"]["1h"]["pos"] == 0
    out = tmp_path / "o.json"
    out.write_text(json.dumps({"_meta": {"at": "2026-10-13T00:00:00+00:00", "run_id": "r1"}, "switches": s}))
    db = tmp_path / "l.db"
    r = cr.ingest_outcomes(str(out), str(db))
    assert r["fixes_new"] == 1 and r["outcomes"] == 3
    r2 = cr.ingest_outcomes(str(out), str(db))
    assert r2["outcomes"] == 0 and r2["skipped_existing"] == 3
    cx = sqlite3.connect(str(db))
    rows = cx.execute("SELECT f.kind, f.scope, f.target, o.verdict, o.sym_side FROM fix_outcomes o JOIN fixes f ON f.id = o.fix_id ORDER BY o.sym_side").fetchall()
    assert [r[3] for r in rows] == ["ablation_helped", "naked_neg", "ablation_helped"]
    assert rows[1][4] == "AUSDT_LONG|SW_TF=1h" and {r[0] for r in rows} == {"rescue_ablation"}
    # learner's disable_switch confirm logic keys on 'hurt'/'helped' only -> untouched by rescue verdicts
    assert cx.execute("SELECT COUNT(*) FROM fix_outcomes WHERE verdict IN ('helped','hurt')").fetchone()[0] == 0
    assert cx.execute("SELECT status FROM fixes WHERE kind='rescue_ablation'").fetchone()[0] == "measured"


def _seed_db(path, n_lessons=60):
    cx = cl.connect(str(path))
    for i in range(n_lessons):
        lid = cl._add_lesson(cx, f"2026-10-{1 + i % 9:02d}", "sweep_cell", f"S{i}", "BTCUSDT_LONG", "CRYPTO_LONG", {"pad": "x" * 400})
        for j in range(20):
            cl._add_cause(cx, lid, "KEEP_ME" if j == 0 else f"SW{j}", "1", "", -0.1, -0.1, "direct_sweep", "T", "T!SW=1")
    cx.execute("INSERT INTO fixes(kind, scope, target, payload, status, reason, created) VALUES ('disable_switch','global','KEEP_ME','{}','confirmed','', 'x')")
    cx.commit()
    cx.close()


def test_cap_dry_run_and_apply(tmp_path, monkeypatch):
    db = tmp_path / "l.db"
    _seed_db(db)
    size = db.stat().st_size
    max_gb = (size * 0.5) / 1024 ** 3
    plan = cr.cap(str(db), max_gb, apply=False)
    assert plan["action"] == "prune" and plan["apply"] is False and "KEEP_ME" in plan["protected_targets"]
    assert sqlite3.connect(str(db)).execute("SELECT COUNT(*) FROM causes").fetchone()[0] == 1200
    monkeypatch.setattr(cr, "_learner_running", lambda: [])
    res = cr.cap(str(db), max_gb, apply=True)
    cx = sqlite3.connect(str(db))
    assert res["deleted_causes"] > 0 and res["integrity_after"] == "ok"
    assert cx.execute("SELECT COUNT(*) FROM causes WHERE switch_key='KEEP_ME'").fetchone()[0] == 60
    assert cx.execute("SELECT COUNT(*) FROM lessons").fetchone()[0] == 60  # tombstones kept -> no re-ingest
    assert cx.execute("SELECT COUNT(*) FROM fixes").fetchone()[0] == 1
    assert res["db_bytes_after"] < size


def test_cap_refuses_while_learner_runs(tmp_path, monkeypatch):
    db = tmp_path / "l.db"
    _seed_db(db, 5)
    monkeypatch.setattr(cr, "_learner_running", lambda: ["123"])
    assert "refused" in cr.cap(str(db), 0.0000001, apply=True)


def test_cap_noop_under_limit(tmp_path):
    db = tmp_path / "l.db"
    _seed_db(db, 2)
    assert cr.cap(str(db), 10.0)["action"] == "none"


def test_naked_verdict_when_loo_not_active():
    res = {f"S{i}USDT_LONG": {"evals": {"SW": {"loo": {"not_active": True}, "naked": {"2": {"d_gain": 0.5 if i < 3 else 0.0, "valid": True}}}}} for i in range(4)}
    e = cr.summarise(res, {"CRYPTO_LONG": list(res)}, ["SW"])["SW"]["CRYPTO_LONG"]
    assert e["verdict"] == "NAKED_RESCUE_CANDIDATE" and e["naked_pos"] == 3
    assert cr.naked_verdict([{"d_gain": 0.0, "valid": True}])[0] == "INERT"
    assert cr.naked_verdict([{"d_gain": -1.0, "valid": True}])[0] == "CONFIRM_NEGATIVE"
    assert cr.naked_verdict([])[0] == "NO_EVIDENCE"
