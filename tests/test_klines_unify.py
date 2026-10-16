"""klines_unify — gap detection, join plan, and reclaim-only-when-subsumed."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import klines_unify as U


def _rows(*stamps):
    return [{"timestamp": f"{s}.000000Z", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0} for s in stamps]


def _write(p, *stamps):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_rows(*stamps)))
    return p


def test_crypto_gap_is_a_hole():
    ks = {"2026-10-01T00:00:00", "2026-10-01T00:15:00", "2026-10-01T01:00:00"}
    h = U.holes(ks, "15m", is_stock=False)
    assert (h["missing_bars"], h["holes"], h["biggest_hole"]) == (2, 1, 2)


def test_contiguous_file_has_no_holes():
    ks = {f"2026-10-01T{h:02d}:{m:02d}:00" for h in range(4) for m in (0, 15, 30, 45)}
    assert U.holes(ks, "15m", is_stock=False)["missing_bars"] == 0


def test_stock_overnight_and_weekend_are_not_holes():
    """A stock has no 15m bars between sessions — counting those would flag every file forever."""
    ks = {"2026-10-09T19:45:00", "2026-10-12T13:30:00", "2026-10-12T13:45:00"}
    assert U.holes(ks, "15m", is_stock=True)["missing_bars"] == 0


def test_empty_pre_market_slots_are_not_holes():
    """Measured 2026-10-16 on OLED/U: ~70% of the flagged gaps sat at 12:30-13:15 UTC (08:30-09:15 ET), where an
    illiquid name simply never printed a trade. Calling those gaps makes "no gaps" unreachable by construction."""
    ks = {"2026-10-12T12:30:00", "2026-10-12T13:30:00", "2026-10-12T13:45:00"}
    assert U.holes(ks, "15m", is_stock=True)["missing_bars"] == 0


def test_a_whole_missing_session_is_reported():
    """The worst gap: the D file proves the day traded and the intraday file has nothing for it."""
    ks = {"2026-10-12T13:30:00", "2026-10-12T13:45:00", "2026-10-14T13:30:00", "2026-10-14T13:45:00"}
    h = U.holes(ks, "15m", is_stock=True, expect_days={"2026-10-12", "2026-10-13", "2026-10-14"})
    assert h["missing_sessions"] == 1 and h["missing_bars"] == 0


def test_missing_bar_inside_a_stock_session_is_a_hole():
    ks = {"2026-10-12T13:30:00", "2026-10-12T14:00:00"}
    h = U.holes(ks, "15m", is_stock=True)
    assert (h["missing_bars"], h["holes"]) == (1, 1)


def test_audit_reports_what_a_mirror_still_holds(tmp_path, monkeypatch):
    kc = tmp_path / "klines_cache"
    _write(kc / "tradier" / "T_15m.json", "2026-10-12T13:30:00", "2026-10-12T14:00:00")
    _write(kc / "tradier" / "T_D.json", "2026-10-12T00:00:00")
    mir = tmp_path / "mirror"
    _write(mir / "tradier" / "T_15m.json", "2026-10-12T13:30:00", "2026-10-12T13:45:00")
    monkeypatch.setattr(U, "KC", kc)
    monkeypatch.setattr(U, "MIRRORS", [("mir", mir, False)])
    recs, tally = U.audit()
    assert recs[0]["mirror_unique"] == {"mir": 1}, recs
    assert recs[0]["missing_bars"] == 1 and tally["with_holes"] == 1
    plan = U.build_plan(recs)
    assert plan == [{"target": "tradier/T_15m.json", "tf": "15m", "sources": [{"kind": "json", "path": str(mir / "tradier" / "T_15m.json")}]}]


def test_dedupe_holds_a_root_that_still_has_unique_bars(tmp_path, monkeypatch):
    """USER 2026-10-16 — join first, then reclaim: a copy holding one unseen timestamp is never deleted."""
    kc = tmp_path / "klines_cache"
    _write(kc / "A_15m.json", "2026-10-12T13:30:00")
    mir = tmp_path / "old_backup"
    _write(mir / "A_15m.json", "2026-10-12T13:30:00", "2026-10-11T13:30:00")
    monkeypatch.setattr(U, "KC", kc)
    monkeypatch.setattr(U, "MIRRORS", [("old", mir, False)])
    out = U.dedupe(apply=True)
    assert out[0]["unique_stamps"] == 1 and out[0]["verdict"].startswith("HOLD")
    assert mir.exists(), "a copy with unique bars must survive --apply"


def test_dedupe_reclaims_a_fully_subsumed_root(tmp_path, monkeypatch):
    kc = tmp_path / "klines_cache"
    _write(kc / "A_15m.json", "2026-10-12T13:30:00", "2026-10-12T13:45:00")
    mir = tmp_path / "old_backup"
    _write(mir / "A_15m.json", "2026-10-12T13:30:00")
    monkeypatch.setattr(U, "KC", kc)
    monkeypatch.setattr(U, "MIRRORS", [("old", mir, False)])
    assert U.dedupe(apply=False)[0]["verdict"].startswith("would reclaim")
    assert mir.exists(), "dry run must not delete"
    assert U.dedupe(apply=True)[0]["verdict"].startswith("reclaimed")
    assert not mir.exists()


def test_a_file_the_store_does_not_have_blocks_reclaim(tmp_path, monkeypatch):
    """An orphan is the one case a join cannot fix (no target to grow), so the root stays until it is placed."""
    kc = tmp_path / "klines_cache"
    _write(kc / "A_15m.json", "2026-10-12T13:30:00")
    mir = tmp_path / "old_backup"
    _write(mir / "ZZZ_15m.json", "2026-10-12T13:30:00")
    monkeypatch.setattr(U, "KC", kc)
    monkeypatch.setattr(U, "MIRRORS", [("old", mir, False)])
    out = U.dedupe(apply=True)
    assert out[0]["orphan_files"] == 1 and out[0]["verdict"].startswith("HOLD")
    assert mir.exists()


def test_keep_roots_are_never_touched(tmp_path, monkeypatch):
    kc = tmp_path / "klines_cache"
    _write(kc / "A_15m.json", "2026-10-12T13:30:00")
    feed = tmp_path / "klines_cache_macbook"
    _write(feed / "A_15m.json", "2026-10-12T13:30:00")
    monkeypatch.setattr(U, "KC", kc)
    monkeypatch.setattr(U, "MIRRORS", [("macbook_feed", feed, True)])
    assert U.dedupe(apply=True)[0]["verdict"] == "keep"
    assert feed.exists()


def test_gzipped_vault_copy_is_read_as_a_source(tmp_path, monkeypatch):
    import gzip

    kc = tmp_path / "klines_cache"
    _write(kc / "A_15m.json", "2026-10-12T13:45:00")
    vault = tmp_path / "klines_vault"
    vault.mkdir()
    (vault / "A_15m.json.gz").write_bytes(gzip.compress(json.dumps(_rows("2026-10-12T13:30:00")).encode()))
    monkeypatch.setattr(U, "KC", kc)
    monkeypatch.setattr(U, "MIRRORS", [("vault", vault, True)])
    recs, _ = U.audit()
    assert recs[0]["mirror_unique"] == {"vault": 1}, recs


def test_store_and_mirror_can_be_named_on_the_command_line(tmp_path, monkeypatch, capsys):
    """The external-drive copies live on the Mac, so the same subset proof has to run against named paths."""
    store = tmp_path / "e3t_backup" / "klines_cache"
    _write(store / "A_15m.json", "2026-10-12T13:30:00", "2026-10-12T13:45:00")
    old = tmp_path / "toshiba_master"
    _write(old / "A_15m.json", "2026-10-12T13:30:00")
    monkeypatch.setattr(sys, "argv", ["klines_unify.py", "--dedupe", "--store", str(store), "--mirror", f"toshiba={old}"])
    U.main()
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["name"] == "toshiba" and out["verdict"].startswith("would reclaim"), out
    assert old.exists()


def test_an_older_layout_is_matched_not_called_an_orphan(tmp_path, monkeypatch):
    """The 2026-03 drive master keeps stocks at the cache ROOT; the store keeps them under tradier/. Without this the
    whole stock half of that copy reads as orphan and its 17.2M unique bars could never be joined."""
    kc = tmp_path / "klines_cache"
    _write(kc / "tradier" / "AAPL_15m.json", "2026-10-12T13:45:00")
    mir = tmp_path / "drive_master"
    _write(mir / "AAPL_15m.json", "2026-10-12T13:30:00", "2026-10-12T13:45:00")
    monkeypatch.setattr(U, "KC", kc)
    monkeypatch.setattr(U, "MIRRORS", [("drive", mir, False)])
    recs, _ = U.audit()
    assert recs[0]["mirror_unique"] == {"drive": 1}, recs
    assert U.build_plan(recs)[0]["sources"][0]["path"] == str(mir / "AAPL_15m.json")
    out = U.dedupe(apply=False)[0]
    assert out["orphan_files"] == 0 and out["unique_stamps"] == 1, out


def test_a_perp_under_tradier_is_not_measured_as_a_stock(tmp_path, monkeypatch):
    """The store keeps tradier/TRBUSDT_15m.json and tradier/CHZUSDT_15m.json — Binance perps. Measuring those 24/7
    files on an RTH grid invented 1324 missing sessions for CHZUSDT alone."""
    assert U.is_crypto("tradier/TRBUSDT_15m.json") and U.is_crypto("CHZUSDT_15m.json")
    assert not U.is_crypto("tradier/AAPL_15m.json")
    kc = tmp_path / "klines_cache"
    _write(kc / "tradier" / "TRBUSDT_15m.json", "2026-10-11T02:00:00", "2026-10-11T02:15:00")
    _write(kc / "tradier" / "TRBUSDT_D.json", "2026-10-11T00:00:00", "2026-10-12T00:00:00")
    monkeypatch.setattr(U, "KC", kc)
    monkeypatch.setattr(U, "MIRRORS", [])
    recs, tally = U.audit(with_mirrors=False)
    r = [x for x in recs if x["rel"].endswith("TRBUSDT_15m.json")][0]
    assert r["missing_sessions"] == 0 and r["missing_bars"] == 0, r


def test_rolling_fast_caches_are_not_counted_as_history_gaps(tmp_path, monkeypatch):
    """A 3m live cache holding 9318 bars across a 1005-day span is not a 473448-bar hole; it rolls by design. It is
    still audited so the join can recover what other copies hold."""
    kc = tmp_path / "klines_cache"
    _write(kc / "XUSDT_3m.json", "2026-01-01T00:00:00", "2026-10-11T02:15:00")
    _write(kc / "XUSDT_15m.json", "2026-10-11T02:00:00", "2026-10-11T03:00:00")
    monkeypatch.setattr(U, "KC", kc)
    monkeypatch.setattr(U, "MIRRORS", [])
    recs, tally = U.audit(with_mirrors=False)
    three = [r for r in recs if r["rel"] == "XUSDT_3m.json"][0]
    assert three["rolling_tf"] is True and three["missing_bars"] > 1000
    assert tally["missing_bars"] == 3, tally
    assert tally["history_files"] == 1 and tally["files"] == 2


def test_the_join_leaves_the_live_rolling_caches_alone(tmp_path, monkeypatch):
    """klines_cache/*_3m.json is what ez_prices/ez_indicators read every tick. consolidated_klines holds ~8100 bars
    per 3m file that the store lacks, but that history belongs in data/klines_3m_hist_w2, not in the live cache."""
    kc = tmp_path / "klines_cache"
    _write(kc / "XUSDT_3m.json", "2026-10-11T02:00:00")
    _write(kc / "XUSDT_15m.json", "2026-10-11T02:00:00")
    mir = tmp_path / "consolidated"
    _write(mir / "XUSDT_3m.json", "2026-10-11T02:00:00", "2026-01-01T00:00:00")
    _write(mir / "XUSDT_15m.json", "2026-10-11T02:00:00", "2026-10-11T02:15:00")
    monkeypatch.setattr(U, "KC", kc)
    monkeypatch.setattr(U, "MIRRORS", [("consolidated", mir, True)])
    recs, _ = U.audit()
    assert len(U.build_plan(recs)) == 1
    assert U.build_plan(recs)[0]["target"] == "XUSDT_15m.json"


def test_run_backups_are_capped(tmp_path, monkeypatch):
    """The nightly join gzips each file it grows; unbounded that is a new copy of the same file every night."""
    monkeypatch.setattr(U, "ROOT", tmp_path)
    for d in ("before_klines_unify_202610100000", "before_klines_unify_202610110000", "before_klines_unify_202610120000", "before_klines_unify_202610130000", "before_klines_unify_202610140000"):
        (tmp_path / "backups" / d).mkdir(parents=True)
        (tmp_path / "backups" / d / "A_15m.json.gz").write_bytes(b"x")
    (tmp_path / "backups" / "before_something_else").mkdir()
    U.prune_run_backups(keep=3)
    left = sorted(p.name for p in (tmp_path / "backups").glob("before_klines_unify_*"))
    assert left == ["before_klines_unify_202610120000", "before_klines_unify_202610130000", "before_klines_unify_202610140000"], left
    assert (tmp_path / "backups" / "before_something_else").exists()


def _et(day, slot):
    import datetime as D
    from zoneinfo import ZoneInfo
    loc = D.datetime(int(day[:4]), int(day[5:7]), int(day[8:10]), int(slot[:2]), int(slot[3:]), tzinfo=ZoneInfo("America/New_York"))
    return loc.astimezone(D.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _grid():
    return [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in (0, 15, 30, 45) if (h, m) >= (9, 30)]


def test_30d_window_counts_a_missing_day_as_a_hole_and_a_thin_day_as_thin():
    """USER 2026-10-16 "at least have complete 30D trading worthy data". Measured the same day: Tradier returns 13
    bars for LEXX on 2026-10-12 while the cache holds 17 and Tradier has nothing to add — a micro-cap does not print
    every 15 minutes, so a thin session is not a hole. A trading day with NO bars is."""
    import datetime as D
    now = D.datetime(2026, 10, 16, tzinfo=D.timezone.utc)
    ks = {_et("2026-10-12", s) for s in _grid()} | {_et("2026-10-13", s) for s in _grid()[:17]}
    r = U.recent_window(ks, expect_days={"2026-10-12", "2026-10-13", "2026-10-14"}, days=30, now=now)
    assert r["recent_sessions"] == 2
    assert r["recent_thin_sessions"] == 1 and r["recent_thin_bars"] == len(_grid()) - 17
    assert r["recent_missing_sessions"] == 1 and r["recent_missing_days"] == ["2026-10-14"]


def test_a_complete_30d_window_is_clean():
    import datetime as D
    now = D.datetime(2026, 10, 16, tzinfo=D.timezone.utc)
    ks = {_et(d, s) for d in ("2026-10-14", "2026-10-15") for s in _grid()}
    r = U.recent_window(ks, expect_days={"2026-10-14", "2026-10-15"}, days=30, now=now)
    assert r["recent_thin_sessions"] == 0 and r["recent_missing_sessions"] == 0


def test_old_history_does_not_enter_the_window():
    import datetime as D
    now = D.datetime(2026, 10, 16, tzinfo=D.timezone.utc)
    ks = {_et("2025-01-15", s) for s in _grid()[:10]}
    r = U.recent_window(ks, expect_days={"2025-01-15"}, days=30, now=now)
    assert r == {"recent_sessions": 0, "recent_thin_sessions": 0, "recent_thin_bars": 0, "recent_missing_sessions": 0, "recent_worst": [], "recent_missing_days": []}


def test_weekend_rows_in_a_D_file_are_not_expected_sessions(tmp_path):
    """COIN read as missing 2026-09-20, 09-27, 10-04 and 10-11 — every one a Sunday, because its D file carries
    weekend rows."""
    d = tmp_path / "tradier"
    d.mkdir(parents=True)
    _write(d / "COIN_D.json", "2026-10-09T20:00:00", "2026-10-11T20:00:00", "2026-10-12T20:00:00")
    _write(d / "COIN_15m.json", "2026-10-09T13:30:00", "2026-10-12T13:30:00")
    got = U.expected_days(d / "COIN_15m.json", "2026-10-09T13:30:00", "2026-10-12T13:30:00")
    assert got == {"2026-10-09", "2026-10-12"}, got
