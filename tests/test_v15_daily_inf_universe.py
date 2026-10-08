import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import v15_daily_inf_universe as U  # noqa: E402

NOW = dt.datetime(2026, 10, 6, 13, 0, tzinfo=dt.timezone.utc)


def rec(sym_side, gain=10.0, trades=100, tim=30.0, dd=5.0, valid=True, **kw):
    r = {
        "sym_side": sym_side,
        "mtime": NOW.timestamp() - 3600,
        "size": 1000,
        "result_start_utc": (NOW - dt.timedelta(hours=2)).isoformat(),
        "result_done_utc": (NOW - dt.timedelta(hours=1)).isoformat(),
        "cumulative_gain": gain,
        "final_gain_fresh_vec": gain,
        "has_cumulative_overrides": True,
        "simple_price_gt0": None,
        "vec_only_keys": [],
        "vec_only_true": [],
        "diagnose_repair": {"accepted": True, "after": {"gain": gain, "trades": trades, "tim": tim, "dd": dd, "wr": 50.0, "valid": valid, "reason": "", "bh": 1.0}},
        "fetched_from": "s5",
    }
    r.update(kw)
    return r


def allowed_for(*recs):
    return {r["sym_side"] for r in recs}


def ev(r, allowed=None, **kw):
    return U.evaluate(r, allowed if allowed is not None else {r["sym_side"]}, NOW, 36.0, **kw)


def test_eligible_record_passes():
    ok, reasons, m, flags = ev(rec("XTZUSDT_LONG", gain=31.2))
    assert ok, reasons
    assert m["gain_pct"] == 31.2
    assert "365D: no verdict" in flags


@pytest.mark.parametrize(
    "kw,needle",
    [
        ({"gain": 0.0}, "gain"),
        ({"gain": -3.0}, "gain"),
        ({"tim": 19.9}, "TIM"),
        ({"tim": 80.1}, "TIM"),
        ({"dd": 30.5}, "DD"),
        ({"trades": 9}, "trades 9<10"),
        ({"valid": False}, "invalid"),
    ],
)
def test_30d_gates(kw, needle):
    ok, reasons, _m, _f = ev(rec("AAVEUSDC_LONG", **kw))
    assert not ok
    assert any(needle in r for r in reasons), reasons


def test_gate_boundaries_inclusive():
    ok, reasons, _m, _f = ev(rec("AAVEUSDC_LONG", tim=20.0, dd=30.0, trades=10))
    assert ok, reasons
    ok, reasons, _m, _f = ev(rec("AAVEUSDC_LONG", tim=80.0))
    assert ok, reasons


def test_not_tradeable_and_not_crypto():
    ok, reasons, _m, _f = ev(rec("GOOGLUSDT_SHORT"), allowed=set())
    assert not ok and any("tradeable" in r for r in reasons)
    ok, reasons, _m, _f = ev(rec("AAPL_LONG"))
    assert not ok and reasons == ["not a crypto sym_side"]


def test_stale_record_rejected():
    old = (NOW - dt.timedelta(hours=40)).isoformat()
    r = rec("ZENUSDT_SHORT", result_start_utc=old, result_done_utc=old)
    ok, reasons, _m, _f = ev(r)
    assert not ok and any(x.startswith("stale") for x in reasons)


def test_stale_done_stamp_does_not_hide_fresh_start():
    r = rec("ALGOUSDT_LONG", result_done_utc="2026-09-25T08:45:00+00:00")
    ok, reasons, _m, _f = ev(r)
    assert ok, reasons


def test_simple_price_gt0_set_rejected():
    ok, reasons, _m, _f = ev(rec("ETCUSDT_SHORT", simple_price_gt0=True))
    assert not ok and any("SIMPLE_PRICE_GT0" in r for r in reasons)


def test_legacy_switch_names_are_migrated_not_refused():
    # 2026-10-06 USER: old switch names are a relic; go-live migrates them with the alias map, selection never refuses on them
    r = rec("RVNUSDT_LONG", legacy_renamed={"OLD_NAME": "NEW_NAME"}, legacy_dropped=[])
    ok, reasons, _m, flags = ev(r)
    assert ok, reasons
    assert any("alias map" in f for f in flags)


def test_bad_verdicts_and_flags():
    for kw, needle in (({"verdict": "IMPOSSIBLE"}, "IMPOSSIBLE"), ({"verdict": "NO_TRADES"}, "NO_TRADES"), ({"diagnostic_only": "trades=0"}, "diagnostic_only"), ({"needs_redo": True}, "needs_redo"), ({"has_cumulative_overrides": False}, "cumulative_overrides")):
        ok, reasons, _m, _f = ev(rec("DOTUSDT_LONG", **kw))
        assert not ok and any(needle in r for r in reasons), (kw, reasons)


def test_metrics_must_describe_final_set():
    r = rec("COTIUSDT_LONG", gain=12.0)
    r["cumulative_gain"] = 15.0
    ok, reasons, m, _f = ev(r)
    assert not ok and m is None and any("another set" in x for x in reasons)
    r2 = rec("COTIUSDT_LONG")
    r2["diagnose_repair"] = {"skipped": "budget 0s"}
    ok, reasons, m, _f = ev(r2)
    assert not ok and any("no fresh full-set metrics" in x for x in reasons)


def test_365d_verdict_gate():
    good = {"gain_pct": 3.4, "trades": 1505, "valid": True, "span_days": 407}
    ok, reasons, _m, flags = ev(rec("ALGOUSDT_LONG", final_365d=good))
    assert ok, reasons
    assert "365D: no verdict" not in flags
    for bad in ({"gain_pct": -1.0, "trades": 500, "valid": True, "span_days": 400}, {"gain_pct": 5.0, "trades": 500, "valid": False, "invalid_reason": "DD"}, {"gain_pct": 5.0, "trades": 79, "valid": True, "span_days": 400}):
        ok, reasons, _m, _f = ev(rec("ALGOUSDT_LONG", final_365d=bad))
        assert not ok and any(r.startswith("365D") for r in reasons), bad
    ok, _r = U.qualifies_365d({"gain_pct": 5.0, "trades": 20, "valid": True, "span_days": 80})
    assert ok
    ok, _r = U.qualifies_365d({"gain_pct": 5.0, "trades": 15, "valid": True, "span_days": 80})
    assert not ok


def _evaluated(recs):
    return {r["sym_side"]: ev(r) for r in recs}


def test_ranking_top_n_by_gain():
    recs = [rec(f"S{i:02d}USDT_LONG", gain=float(i)) for i in range(1, 31)] + [rec("ZZZUSDT_SHORT", gain=2.0), rec("YYYUSDT_SHORT", gain=9.0)]
    res = U.build_books(_evaluated(recs), {"LONG": set(), "SHORT": set()}, top=25)
    assert len(res["books"]["LONG"]) == 25
    assert res["books"]["LONG"][0] == "S30USDT" and res["books"]["LONG"][-1] == "S06USDT"
    assert res["books"]["SHORT"] == ["YYYUSDT", "ZZZUSDT"]


def test_never_pads_with_ineligible():
    recs = [rec("AUSDT_LONG", gain=5.0), rec("BUSDT_LONG", gain=-1.0), rec("CUSDT_LONG", tim=95.0)]
    res = U.build_books(_evaluated(recs), {"LONG": set(), "SHORT": set()}, top=25)
    assert res["books"]["LONG"] == ["AUSDT"]
    assert res["books"]["SHORT"] == []


def test_open_position_retention():
    recs = [rec("AUSDT_LONG", gain=5.0), rec("BUSDT_SHORT", gain=-1.0)]
    open_pos = {"LONG": {"OLDUSDT"}, "SHORT": {"BUSDT"}}
    res = U.build_books(_evaluated(recs), open_pos, top=25)
    assert res["books"]["LONG"] == ["AUSDT", "OLDUSDT"]
    assert res["books"]["SHORT"] == ["BUSDT"]
    assert res["kept_open"] == {"LONG": ["OLDUSDT"], "SHORT": ["BUSDT"]}


def test_open_position_kept_beyond_top():
    recs = [rec(f"S{i}USDT_LONG", gain=float(i)) for i in range(1, 4)]
    res = U.build_books(_evaluated(recs), {"LONG": {"S1USDT"}, "SHORT": set()}, top=2)
    assert res["books"]["LONG"] == ["S3USDT", "S2USDT", "S1USDT"]


def test_both_side_conflict_resolution():
    recs = [rec("GRTUSDT_LONG", gain=5.0), rec("GRTUSDT_SHORT", gain=8.0), rec("AUSDT_SHORT", gain=1.0)]
    res = U.build_books(_evaluated(recs), {"LONG": set(), "SHORT": set()}, top=25)
    assert res["books"]["SHORT"] == ["GRTUSDT", "AUSDT"] and res["books"]["LONG"] == []
    assert res["conflicts"][0]["kept_side"] == "SHORT"
    res = U.build_books(_evaluated(recs), {"LONG": {"GRTUSDT"}, "SHORT": set()}, top=25)
    assert "GRTUSDT" in res["books"]["LONG"] and "GRTUSDT" not in res["books"]["SHORT"]
    res = U.build_books(_evaluated(recs), {"LONG": set(), "SHORT": set()}, top=25, both_sides="keep")
    assert res["books"]["LONG"] == ["GRTUSDT"] and "GRTUSDT" in res["books"]["SHORT"]


def test_dedupe_prefers_newest_mtime_and_readable():
    a = rec("ALGOUSDT_LONG", fetched_from="s1-pub", mtime=100.0, size=10)
    b = rec("ALGOUSDT_LONG", fetched_from="s5", mtime=200.0, size=10, final_365d={"gain_pct": 3.0})
    c = {"sym_side": "ALGOUSDT_LONG", "fetched_from": "s2", "mtime": 300.0, "size": 5, "error": "unreadable"}
    out = U.dedupe_records([a, b, c])
    assert out["ALGOUSDT_LONG"]["fetched_from"] == "s5"


def test_open_inf_positions_reader(tmp_path):
    lp, sp, tr = tmp_path / "long_positions.json", tmp_path / "short_positions.json", tmp_path / "tracker.json"
    lp.write_text(json.dumps({"inf:AUSDT_LONG": {"positionAmt": 3.0}, "inf:BUSDT_LONG": {"positionAmt": 0.0}, "men:CUSDT_LONG": {"positionAmt": 1.0}}))
    sp.write_text(json.dumps({"inf:DUSDT_SHORT": {"positionAmt": -2.0}}))
    tr.write_text(json.dumps({"active_hedges": [{"position_key": "inf:EUSDT_SHORT"}]}))
    out, problems = U.open_inf_positions({"LONG": lp, "SHORT": sp}, tr)
    assert out == {"LONG": {"AUSDT"}, "SHORT": {"DUSDT", "EUSDT"}}
    assert problems == []
    os.utime(lp, (time.time() - 3600, time.time() - 3600))
    _out, problems = U.open_inf_positions({"LONG": lp, "SHORT": sp}, tr)
    assert any("stale" in p for p in problems)
    _out, problems = U.open_inf_positions({"LONG": tmp_path / "nope.json", "SHORT": sp}, tr)
    assert any("missing" in p for p in problems)


def _sandbox(tmp_path, monkeypatch, prev_long, prev_short, positions_long=None):
    (tmp_path / "inf").mkdir()
    (tmp_path / "backups").mkdir()
    books = {"LONG": tmp_path / "symbols_inf_long.json", "SHORT": tmp_path / "symbols_inf_short.json"}
    books["LONG"].write_text(json.dumps(prev_long))
    books["SHORT"].write_text(json.dumps(prev_short))
    pos = {"LONG": tmp_path / "inf" / "long_positions.json", "SHORT": tmp_path / "inf" / "short_positions.json"}
    pos["LONG"].write_text(json.dumps(positions_long or {}))
    pos["SHORT"].write_text(json.dumps({}))
    monkeypatch.setattr(U, "ROOT", tmp_path)
    monkeypatch.setattr(U, "BOOKS", books)
    monkeypatch.setattr(U, "POSITIONS", pos)
    monkeypatch.setattr(U, "TRACKER", tmp_path / "inf" / "tracker.json")
    monkeypatch.setattr(U, "OUT_DIR", tmp_path / "data" / "inf_universe")
    monkeypatch.setattr(U, "CONFIRMED_365D", tmp_path / "none.json")
    monkeypatch.setattr(U, "now_utc", lambda: NOW)
    return books


def test_main_apply_end_to_end(tmp_path, monkeypatch):
    books = _sandbox(tmp_path, monkeypatch, ["OLDUSDT", "KEEPUSDT"], ["SOLUSDC"], {"inf:KEEPUSDT_LONG": {"positionAmt": 1.0}})
    recs = [rec("XTZUSDT_LONG", gain=31.0), rec("AAVEUSDC_LONG", gain=27.0), rec("ZENUSDT_SHORT", gain=18.0), rec("BADUSDT_LONG", gain=-2.0)]
    monkeypatch.setattr(U, "allowed_sym_sides", lambda root=None: allowed_for(*recs))
    rp = tmp_path / "recs.json"
    rp.write_text(json.dumps(recs))
    assert U.main(["--records", str(rp)]) == 0
    assert json.loads(books["LONG"].read_text()) == ["OLDUSDT", "KEEPUSDT"]
    assert U.main(["--records", str(rp), "--apply", "--allow-thin-fetch"]) == 0
    assert json.loads(books["LONG"].read_text()) == ["XTZUSDT", "AAVEUSDC", "KEEPUSDT"]
    assert json.loads(books["SHORT"].read_text()) == ["ZENUSDT"]
    rep = json.loads((tmp_path / "data" / "inf_universe" / "20261006.json").read_text())
    assert rep["applied"] and rep["kept_open"]["LONG"] == ["KEEPUSDT"]
    assert rep["diff"]["LONG"]["removed"] == ["OLDUSDT"] and rep["diff"]["SHORT"]["removed"] == ["SOLUSDC"]
    assert len(list((tmp_path / "backups").glob("before_inf_universe_*"))) == 2
    assert (tmp_path / "data" / "inf_universe" / "20261006.md").exists()


def test_main_apply_refuses_empty_side(tmp_path, monkeypatch):
    books = _sandbox(tmp_path, monkeypatch, ["OLDUSDT"], ["SOLUSDC"])
    recs = [rec("XTZUSDT_LONG", gain=31.0)]
    monkeypatch.setattr(U, "allowed_sym_sides", lambda root=None: allowed_for(*recs))
    rp = tmp_path / "recs.json"
    rp.write_text(json.dumps(recs))
    assert U.main(["--records", str(rp), "--apply"]) == 2
    assert json.loads(books["SHORT"].read_text()) == ["SOLUSDC"]
    assert json.loads(books["LONG"].read_text()) == ["OLDUSDT"]
    assert U.main(["--records", str(rp), "--apply", "--allow-empty-side", "--allow-thin-fetch"]) == 0
    assert json.loads(books["SHORT"].read_text()) == []


def test_build_books_default_top_is_15():
    import inspect
    assert inspect.signature(U.build_books).parameters["top"].default == 15


def test_cli_default_top_is_15(tmp_path, monkeypatch):
    # USER 2026-10-08: the daily chain writes the top-15 30D gainers per side (was 25)
    books = _sandbox(tmp_path, monkeypatch, [], [])
    recs = [rec(f"L{i:02d}USDT_LONG", gain=float(30 - i)) for i in range(20)] + [rec(f"S{i:02d}USDT_SHORT", gain=float(30 - i)) for i in range(20)]
    monkeypatch.setattr(U, "allowed_sym_sides", lambda root=None: allowed_for(*recs))
    rp = tmp_path / "recs.json"
    rp.write_text(json.dumps(recs))
    assert U.main(["--records", str(rp), "--apply"]) == 0
    assert len(json.loads(books["LONG"].read_text())) == 15
    assert len(json.loads(books["SHORT"].read_text())) == 15


def test_remote_extract_sets_tools_sys_path():
    # 2026-10-07: s2/s5 extracts died with ModuleNotFoundError (remote cwd=$HOME) -> 0 records -> refused apply
    assert "binance-sandbox/tools" in U.REMOTE_EXTRACT
    assert U.REMOTE_EXTRACT.index("sys.path.insert(0, _cand)") < U.REMOTE_EXTRACT.index("import v15_persym_golive")


def test_thin_fetch_refuses_apply(tmp_path, monkeypatch):
    # 2026-10-08: a collapsed fleet view (campaign rotation) must keep yesterday's books, never a skeleton
    books = _sandbox(tmp_path, monkeypatch, ["OLDUSDT"], ["SOLUSDC"])
    recs = [rec("XTZUSDT_LONG", gain=31.0), rec("ZENUSDT_SHORT", gain=18.0)]
    monkeypatch.setattr(U, "allowed_sym_sides", lambda root=None: allowed_for(*recs))
    rp = tmp_path / "recs.json"
    rp.write_text(json.dumps(recs))
    assert U.main(["--records", str(rp), "--apply"]) == 2
    assert json.loads(books["LONG"].read_text()) == ["OLDUSDT"]
    assert json.loads(books["SHORT"].read_text()) == ["SOLUSDC"]
    rep = json.loads((tmp_path / "data" / "inf_universe" / "20261006.json").read_text())
    assert "thin fleet view" in rep["apply_refused"]
    assert U.main(["--records", str(rp), "--apply", "--allow-thin-fetch"]) == 0
    assert json.loads(books["LONG"].read_text()) == ["XTZUSDT"]
    assert json.loads(books["SHORT"].read_text()) == ["ZENUSDT"]


def test_remote_extract_covers_all_campaign_dirs(tmp_path):
    # 2026-10-08: pointers rotate fleet-wide; the extract must union pointer + all v15_* dirs + lifecycle_pilot
    import subprocess
    home = tmp_path / "home"
    (home / "v15_old").mkdir(parents=True)
    (home / "v15_new" / "progress").mkdir(parents=True)
    (home / "binance-sandbox" / "data" / "reports" / "lifecycle_pilot").mkdir(parents=True)
    tools = home / "binance-sandbox" / "tools"
    tools.mkdir(parents=True)
    (tools / "v15_persym_golive.py").write_text("def migrate(co):\n    return dict(co), {}, []\n")
    for p in (home / "v15_old" / "AAVEUSDC_LONG_v14_progress.json", home / "v15_new" / "progress" / "ZENUSDT_SHORT_v14_progress.json",
              home / "binance-sandbox" / "data" / "reports" / "lifecycle_pilot" / "DOTUSDT_LONG_v14_progress.json"):
        p.write_text(json.dumps({"cumulative_overrides": {"X": 1}}))
    (home / "v15_old" / "readme.txt").write_text("ignored")
    env = dict(os.environ, HOME=str(home))
    r = subprocess.run([sys.executable, "-", str(home / "v15_new" / "progress"), str(72 * 3600), json.dumps(list(U.KEEP_FIELDS))],
                       input=U.REMOTE_EXTRACT, capture_output=True, text=True, timeout=60, cwd=str(tmp_path), env=env)
    assert r.returncode == 0, r.stderr[-500:]
    assert {rec["sym_side"] for rec in json.loads(r.stdout)} == {"AAVEUSDC_LONG", "ZENUSDT_SHORT", "DOTUSDT_LONG"}


def test_report_rotation_keeps_previous(tmp_path, monkeypatch):
    books = _sandbox(tmp_path, monkeypatch, [], [])
    recs = [rec("XTZUSDT_LONG", gain=31.0), rec("ZENUSDT_SHORT", gain=18.0)]
    monkeypatch.setattr(U, "allowed_sym_sides", lambda root=None: allowed_for(*recs))
    rp = tmp_path / "recs.json"
    rp.write_text(json.dumps(recs))
    assert U.main(["--records", str(rp)]) == 0
    assert U.main(["--records", str(rp)]) == 0
    assert (tmp_path / "data" / "inf_universe" / "20261006.prev.json").exists()
    assert (tmp_path / "data" / "inf_universe" / "20261006.prev.md").exists()


def test_main_apply_refuses_stale_positions(tmp_path, monkeypatch):
    books = _sandbox(tmp_path, monkeypatch, ["OLDUSDT"], ["SOLUSDC"])
    old = time.time() - 7200
    os.utime(U.POSITIONS["LONG"], (old, old))
    recs = [rec("XTZUSDT_LONG", gain=31.0), rec("ZENUSDT_SHORT", gain=18.0)]
    monkeypatch.setattr(U, "allowed_sym_sides", lambda root=None: allowed_for(*recs))
    rp = tmp_path / "recs.json"
    rp.write_text(json.dumps(recs))
    assert U.main(["--records", str(rp), "--apply"]) == 2
    assert json.loads(books["LONG"].read_text()) == ["OLDUSDT"]
