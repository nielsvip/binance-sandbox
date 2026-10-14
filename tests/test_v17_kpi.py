"""tests for tools/v17_kpi.py (KPI extraction, rollups, regression guard, bounded auto-tuner, rollback), the
tools/v15_dispatch_job.py reader hook and the daily-learn packet KPI section."""
import json, os, sys, time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import v17_kpi as K  # noqa: E402


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _progress(ss, t0, wall, final, bh, bs_gain, tier="WINNER", pilot="aaaa1111", phases=None, legacy=True):
    v17 = {"on": True, "started_utc": _iso(t0 + 60), "p0_wall_s": 60.0, "phases": {}}
    t = t0 + 60 + 600  # P2 (legacy, unrecorded) 600 s, then P3
    for name, (w, lift, entry) in (phases or {}).items():
        t += w
        v17["phases"][name] = {"wall_s": w, "evals": 10, "lift": lift, "entry_gain": entry, "exit_gain": entry + lift, "promotions": 1 if lift > 0 else 0, "at": _iso(t)}
    if not legacy:
        v17["phases"]["P2_WORST_FIRST"] = {"wall_s": 600.0, "evals": 99, "lift": 3.0, "entry_gain": bs_gain, "exit_gain": bs_gain + 3, "promotions": 2, "at": _iso(t0 + 660)}
    return {"symside": ss, "bh": bh, "final_gain_fresh_vec": final, "final_trades": 20, "tier": {"tier": tier}, "best_start": {"chosen": "history_0", "gain": bs_gain},
            "initial_baseline_gain": bs_gain, "best_seen": {"gain": final, "tim": 30.0, "dd": 4.0}, "v17": v17, "result_start_utc": _iso(t0), "result_done_utc": _iso(t0 + wall),
            "result_pilot_md5": pilot, "result_engine_md5": "eng1", "final_365d": {"gain_pct": 12.0, "max_dd_pct": 9.0, "tim_pct": 22.0, "trades": 90, "valid": True}}


def _write_board(pdir, d, dl_rows=()):
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{d['symside']}_v14_progress.json").write_text(json.dumps(d))
    (pdir / "v15_delta_log").mkdir(exist_ok=True)
    with open(pdir / "v15_delta_log" / f"{d['symside']}_jump.jsonl", "w") as f:
        for r in dl_rows:
            f.write(json.dumps(r) + "\n")


PH = {"P3_COMBO": (300.0, 1.0, 11.0), "P3_SURGEON_1": (120.0, 2.0, 12.0), "P3_ABLATION_1": (60.0, 0.0, 14.0), "P3_RETEST_1": (60.0, 0.5, 14.0), "P3_FOCUS_1": (10.0, 0.1, 14.5)}


def test_board_record_phases_cpu_and_p2_derived(tmp_path):
    t0 = 1_791_900_000.0
    d = _progress("HOOD_LONG", t0, 1800.0, 14.6, 2.0, 10.0, phases=PH)
    rows = [{"ts": _iso(t0 + 100), "gain_pct": 11.0, "secs": 1.5, "cached": False, "tim": 25.0}, {"ts": _iso(t0 + 200), "gain_pct": 11.0, "secs": 0.001, "cached": True},
            {"ts": _iso(t0 + 1700), "gain_pct": 14.6, "secs": 2.5, "cached": False, "tim": 30.0}, {"ts": _iso(t0 + 5000), "gain_pct": 1.0, "secs": 99.0, "cached": False}]
    _write_board(tmp_path / "v15_run9" / "progress", d, rows)
    r = K.extract([str(tmp_path / "v15_run*" / "progress")], "s1", t0 - 10, 2)["boards"][0]
    assert r["wall_s"] == 1800.0 and r["workers"] == 2 and r["core_min_est"] == 60.0
    assert r["evals"] == 2 and r["evals_cached"] == 1 and abs(r["eval_core_min"] - 4.0 / 60) < 0.01  # row outside [start, done] ignored
    assert r["gain_bh"] == pytest.approx(7.3) and r["tim"] == 30.0 and r["dd"] == 4.0 and r["g365"] == 12.0 and r["dd365"] == 9.0
    ph = r["phases"]
    assert ph["combo"]["lift"] == 1.0 and ph["surgeon"]["wall_s"] == 120.0 and ph["p0"]["wall_s"] == 60.0
    assert ph["p2"]["derived"] and ph["p2"]["wall_s"] == pytest.approx(600.0) and ph["p2"]["lift"] == pytest.approx(1.0)  # first P3 entry 11 - best_start 10
    assert "p2_derived" in r["flags"] and "cpu_est_wall_x_workers" in r["flags"] and "workers_assumed" in r["flags"]
    assert not r["final_below_best_start"]


def test_recorded_p2_not_derived_and_below_best_start(tmp_path):
    t0 = 1_791_900_000.0
    d = _progress("ETHUSDC_SHORT", t0, 900.0, 4.0, -1.0, 5.0, legacy=False, phases={"P3_FOCUS_1": (5.0, 0.0, 8.0)})
    _write_board(tmp_path / "v15_run9" / "progress", d)
    r = K.extract([str(tmp_path / "v15_run*" / "progress")], "s2", t0 - 10, 2)["boards"][0]
    assert r["cat_side"] == "CRYPTO_SHORT" and r["gain_bh"] is None  # bh <= 0 -> no ratio
    assert r["phases"]["p2"]["lift"] == 3.0 and not r["phases"]["p2"].get("derived")
    assert r["final_below_best_start"] and "no_delta_log" not in r["flags"]


def test_workers_from_sched_and_dispatch_logs(tmp_path):
    sl = tmp_path / "sched.log"
    sl.write_text("\n".join(json.dumps({"now": "2026-10-13T12:00:00+00:00", "hosts": {"s1": {"workers_per_side": w}}}) if i == 0 else json.dumps({"now": f"2026-10-14T00:{i:02d}:00+00:00", "hosts": {"s1": {"workers_per_side": w}}}) for i, w in enumerate([4, 2])) + "\n")
    wm = K.sched_workers(sl)
    assert wm[("s1", "*")] == [(K.parse_ts("2026-10-13T12:00:00Z"), 4), (K.parse_ts("2026-10-14T00:01:00Z"), 2)]
    assert K._workers_for(wm, "s1", "HOOD_LONG", K.parse_ts("2026-10-13T20:00:00Z"), 9) == (4, "sched_log")
    assert K._workers_for(wm, "s1", "HOOD_LONG", K.parse_ts("2026-10-14T01:00:00Z"), 9) == (2, "sched_log")
    dl = tmp_path / "dispatch.log"
    dl.write_text(json.dumps({"event": "launched", "symbol": "HOOD", "tag": "s1:HOOD:L30D", "workers": 3, "ts": "2026-10-14T00:30:00Z"}) + "\n")
    wm.update(K.dispatch_workers(dl))
    assert K._workers_for(wm, "s1", "HOOD_LONG", K.parse_ts("2026-10-14T00:31:00Z"), 9) == (3, "dispatch_log")
    assert K._workers_for({}, "s9", "X_LONG", 0, 7) == (7, "assumed")


def _rec(i, now, host="s1", wall=1800.0, gain_bh=3.0, tier="WINNER", pilot="aaaa1111", below=False, ph=None, workers=2):
    bh = 2.0
    return {"key": f"{host}|S{i}_LONG|{i}", "sym_side": f"S{i}_LONG", "cat_side": "STOCKS_LONG", "tier": tier, "host": host, "run": "r", "pilot_md5": pilot, "engine_md5": "e", "config_md5": "c",
            "start_utc": _iso(now - wall), "done_utc": _iso(now), "start_ts": now - wall, "done_ts": now, "wall_s": wall, "workers": workers, "workers_src": "sched_log",
            "core_min_est": wall * workers / 60, "eval_core_min": wall / 120, "evals": 100, "evals_cached": 10, "final_gain": gain_bh * bh, "bh": bh, "gain_bh": gain_bh, "trades": 20,
            "tim": 30.0, "dd": 3.0, "g365": 10.0, "dd365": 8.0, "tim365": 20.0, "trades365": 80, "valid365": True, "best_start": "h0", "best_start_gain": gain_bh * bh - (-1 if below else 1),
            "final_below_best_start": below, "v17_on": True, "phases": ph or {}, "flags": ["cpu_est_wall_x_workers"], "run_class": "QUALIFIED"}


def test_db_upsert_idempotent_and_rollups(tmp_path):
    now = 1_791_950_000.0
    con = K.db_open(tmp_path / "k.sqlite")
    recs = [_rec(i, now - 600 * i, gain_bh=g) for i, g in enumerate([0.5, 1.5, 2.5, 6.0, 12.0])]
    assert K.db_upsert(con, recs) == 5 and K.db_upsert(con, recs) == 0
    roll = K.rollups(con, now + 1)
    w = roll["windows"]["24h"]
    assert w["n"] == 5 and w["share_ge_1x_bh"] == 0.8 and w["share_ge_2x_bh"] == 0.6 and w["share_ge_5x_bh"] == 0.4 and w["share_ge_10x_bh"] == 0.2
    assert w["median_gain_bh"] == 2.5 and w["p90_gain_bh"] == 12.0 and w["hosts"]["s1"]["boards_per_h"] == round(5 / 24, 2)
    assert roll["n_db"] == 5


def test_regression_guard_names_md5_and_writes_alert(tmp_path):
    now = 1_791_950_000.0
    con = K.db_open(tmp_path / "k.sqlite")
    base = [_rec(i, now - 86400 - 3600 * (i + 1), wall=1800.0, gain_bh=3.0, pilot="old00000") for i in range(8)]
    cur = [_rec(100 + i, now - 1800 * (i + 1), wall=2400.0, gain_bh=2.0, pilot="new11111", below=(i == 0)) for i in range(8)]
    K.db_upsert(con, base + cur)
    reg = K.regression_check(con, now)
    w = reg["windows"]["24h"]
    hits = {c["metric"] for c in w["checks"] if c["regression"]}
    assert reg["regression"] and {"median_wall_min", "median_core_min_est", "median_gain_bh"} <= hits
    assert w["pilot_md5_new_vs_baseline"] == ["new11111"] and len(w["finals_below_best_start"]) == 1
    p = K.write_alert(reg, tmp_path / "alerts")
    assert p and json.loads(Path(p).read_text())["regression"]
    assert K.write_alert({"regression": False}, tmp_path / "alerts") is None
    assert not (tmp_path / "alerts" / "V17_KPI_REGRESSION.json").exists() and list((tmp_path / "alerts").glob("V17_KPI_REGRESSION.resolved_*.json"))


def test_regression_insufficient_sample_no_verdict(tmp_path):
    now = 1_791_950_000.0
    con = K.db_open(tmp_path / "k.sqlite")
    K.db_upsert(con, [_rec(1, now - 100, wall=99999.0)])
    reg = K.regression_check(con, now)
    assert reg["windows"]["24h"]["insufficient"] and not reg["regression"]


def _ph(combo, surgeon, ablation, retest=(60, 0.5), focus=(10, 0.1), p2=(600, 3.0)):
    mk = lambda wl: {"wall_s": float(wl[0]), "lift": float(wl[1]), "evals": 1, "promotions": 0, "n": 1}
    return {"combo": mk(combo), "surgeon": mk(surgeon), "ablation": mk(ablation), "retest": mk(retest), "focus": mk(focus), "p2": mk(p2)}


def test_autotune_bounded_and_laws_untouched():
    now = 1_791_950_000.0
    # combo eats time without lift, surgeon lifts hard (WINNER) -> combo share cut <=20 %, surgeon share raised <=20 %
    boards = [_rec(i, now - 3600 * i, ph=_ph(combo=(600, 0.0), surgeon=(120, 4.0), ablation=(120, 0.0))) for i in range(10)]
    doc = K.autotune(boards, {}, now)
    kb = doc["knobs"]
    assert kb["V15_V17_COMBO_SHARE_WINNER"]["value"] == pytest.approx(0.5 * 0.8)
    assert kb["V15_V17_SHARE_SURGEON_WINNER"]["value"] == pytest.approx(min(0.9, 0.75 * 1.2))
    assert kb["V15_V17_SHARE_ABLATION"]["value"] == pytest.approx(0.6 * 0.8)
    for k, v in kb.items():
        lo, hi = v["bounds"]
        assert lo <= v["value"] <= hi and abs(v["value"] - v["prev"]) <= 0.2 * v["prev"] + 1e-9
    assert "V15_V17_MIN_LIFT_PM" not in doc["env"] and "V15_V17_P3_MAX_S" not in doc["env"]
    # non-winner knobs have no evidence (no MEDIOCRE/LOSER boards) -> held at default
    assert kb["V15_V17_COMBO_SHARE"]["value"] == 0.35 and kb["V15_V17_COMBO_SHARE"]["why"].startswith("hold")
    # WINNER P3 lift per wall-min = 4.6 pp / 15.17 min = 0.30.. -> hold band or raise; never a cut while >= 0.3
    assert kb["V15_V17_P3_MAX_S_WINNER"]["value"] >= 1500.0
    # second day compounds from prev but stays inside the hard bounds
    doc2 = K.autotune(boards, doc, now + 86400)
    assert doc2["knobs"]["V15_V17_COMBO_SHARE_WINNER"]["prev"] == pytest.approx(0.4)
    assert doc2["knobs"]["V15_V17_COMBO_SHARE_WINNER"]["value"] == pytest.approx(0.32)


def test_autotune_budget_cut_only_below_rule_and_thin_window_holds():
    now = 1_791_950_000.0
    dead = [_rec(i, now - 3600 * i, tier="LOSER", ph=_ph(combo=(100, 0.0), surgeon=(100, 0.0), ablation=(100, 0.0), retest=(100, 0.0), focus=(10, 0.0))) for i in range(10)]
    doc = K.autotune(dead, {}, now)
    assert doc["knobs"]["V15_V17_P3_MAX_S_LOSER"]["value"] == pytest.approx(240.0)  # 0 pp/min < 0.3 rule -> -20 %
    assert doc["knobs"]["V15_V17_SHARE_SURGEON"]["why"].startswith("hold: no P3 lift")
    dead_w = [dict(b, tier="WINNER") for b in dead]
    assert K.autotune(dead_w, {}, now)["knobs"]["V15_V17_P3_MAX_S_WINNER"]["value"] == 1500.0  # USER: winners never cut
    thin = K.autotune(dead[:3], {}, now)
    assert all(v["value"] == v["prev"] for v in thin["knobs"].values()) and "held" in thin["note"]


def test_autotune_save_and_rollback(tmp_path):
    p, hd, hl = tmp_path / "at.json", tmp_path / "hist", tmp_path / "hist.jsonl"
    d1 = {"generated_utc": "2026-10-14T14:30:00Z", "apply": True, "knobs": {}, "env": {"V15_V17_SHARE_FOCUS": 0.5}}
    d2 = {"generated_utc": "2026-10-15T14:30:00Z", "apply": True, "knobs": {}, "env": {"V15_V17_SHARE_FOCUS": 0.6}}
    K.save_autotune(d1, p, hd, hl)
    time.sleep(1.1)
    K.save_autotune(d2, p, hd, hl)
    time.sleep(1.1)
    back = K.rollback(p, hd, hl)
    assert back["env"] == {"V15_V17_SHARE_FOCUS": 0.5} and back["rolled_back_from"] == "2026-10-15T14:30:00Z"
    assert json.loads(p.read_text())["env"] == {"V15_V17_SHARE_FOCUS": 0.5}
    assert [json.loads(x)["event"] for x in hl.read_text().splitlines()] == ["autotune", "autotune", "rollback"]


def test_dispatch_hook_exports_whitelisted_knobs(tmp_path, monkeypatch):
    import v15_dispatch_job as DJ
    p = tmp_path / "at.json"
    p.write_text(json.dumps({"generated_utc": "x", "apply": True, "env": {"V15_V17_SHARE_FOCUS": 0.4, "V15_V17_P3_MAX_S_WINNER": 1800.0, "V15_V17_MIN_LIFT_PM": 0.1, "V15_V17_P3_MAX_S": 60, "V15_V17_P_MIN": 9.0, "EVIL; rm -rf /": 1}}))
    pre, info = DJ._autotune_exports("s1:HOOD:L30D+S30D", p)
    assert pre == "export V15_V17_P3_MAX_S_WINNER=1800.0; export V15_V17_SHARE_FOCUS=0.4; " and info["n"] == 2
    assert DJ._autotune_exports("s2:VALE:S365D", p) == ("", None)  # no 30D leg
    monkeypatch.setenv("V17_AUTOTUNE_APPLY", "0")
    assert DJ._autotune_exports("s1:HOOD:L30D", p) == ("", None)
    monkeypatch.delenv("V17_AUTOTUNE_APPLY")
    p.write_text(json.dumps({"apply": False, "env": {"V15_V17_SHARE_FOCUS": 0.4}}))
    assert DJ._autotune_exports("s1:HOOD:L30D", p) == ("", None)
    old = time.time() - 4 * 86400
    p.write_text(json.dumps({"apply": True, "env": {"V15_V17_SHARE_FOCUS": 0.4}}))
    os.utime(p, (old, old))
    assert DJ._autotune_exports("s1:HOOD:L30D", p) == ("", None)  # stale file -> pilot defaults
    assert DJ._autotune_exports("s1:HOOD:L30D", tmp_path / "missing.json") == ("", None)


def test_packet_kpi_section(tmp_path):
    import v17_daily_learn_prep as P
    now = 1_791_950_000.0
    con = K.db_open(tmp_path / "k.sqlite")
    K.db_upsert(con, [_rec(i, now - 1800 * i, ph=_ph(combo=(600, 0.0), surgeon=(120, 4.0), ablation=(120, 0.0))) for i in range(6)])
    roll = K.rollups(con, now + 1)
    roll["regression"] = K.regression_check(con, now + 1)
    roll["autotune"] = {"generated_utc": "t", "apply": True, "knobs": {"V15_V17_SHARE_FOCUS": {"prev": 0.5, "value": 0.6}}}
    md = P.kpi_section(roll)
    assert "## C. FLEET KPI" in md and "lift per core-minute" in md and "V15_V17_SHARE_FOCUS 0.5->0.6" in md
    c = P.kpi_compact(roll)
    assert c["best_phases"][0] == "surgeon" and "combo" in c["worst_phases"]
    assert P.kpi_section(None) == ""
    md2, data = P.build_packet(None, None, "20261014", kpi=roll)
    assert "## C. FLEET KPI" in md2 and data["kpi"]["windows"]["24h"]["n"] == 6 and len(md2.encode()) <= P.PACKET_CAP


def test_measured_cpu_preferred(tmp_path):
    t0 = 1_791_900_000.0
    d = _progress("AAPL_LONG", t0, 1200.0, 5.0, 1.0, 4.0, phases=PH)
    d["result_cpu_s"], d["result_workers"] = 1560.0, 2
    _write_board(tmp_path / "v15_run9" / "progress", d)
    r = K.extract([str(tmp_path / "v15_run*" / "progress")], "s1", t0 - 10, 4)["boards"][0]
    assert r["workers"] == 2 and r["workers_src"] == "result_workers" and r["cpu_source"] == "result_cpu_s"
    assert r["core_min"] == 26.0 and r["cores_avg"] == 1.3 and r["core_min_est"] == 40.0 and "cpu_measured" in r["flags"]
    con = K.db_open(tmp_path / "k.sqlite")
    K.db_upsert(con, [r])
    t = K.phase_table(K.db_boards(con, 0))
    assert t["surgeon"]["core_min"] == round(120.0 * 1.3 / 60, 1)  # phase wall x measured average cores
