"""Daily parity masters + forward paper ON vs live OFF must stay enforced.

- PARITY masters OFF for live (15m, True) gives max parity; ON would be 1/3/5m.
- Forward harness P3 (3m/5m micro) is the paper ON vs live OFF comparison; cron must be scheduled.
- Daily parity fix must be scheduled and recent.

This test is the durable keeper for the 2026-09-24 parity program.
"""
import pathlib
import subprocess
import datetime

def test_parity_masters_off_for_live():
    import config
    cfg = config.Config()
    assert getattr(cfg, "PARITY_MIN_DECISION_TF", "15m") == "15m"
    assert getattr(cfg, "V12_PARITY_MIN_TF", "15m") == "15m"
    assert bool(getattr(cfg, "PARITY_DISABLE_NON_VECTORIZABLE", True)) is True
    assert bool(getattr(cfg, "V12_PARITY_DISABLE_NON_VECTORIZABLE", True)) is True

def test_forward_paper_on_vs_live_off_is_scheduled():
    # forward harness cron is the paper ON (P3 3m/5m) vs live OFF comparison
    try:
        cron = subprocess.check_output(["crontab", "-l"], text=True)
    except Exception:
        cron = pathlib.Path("/tmp/crontab").read_text() if pathlib.Path("/tmp/crontab").exists() else ""
    assert "forward_live_vs_vector/cron_hourly.sh" in cron, "forward paper ON vs live OFF harness not scheduled"
    # paths.yaml must have P3 micro 3m5m as the ON vs OFF delta
    p = pathlib.Path("tools/forward_live_vs_vector/paths.yaml")
    assert p.exists()
    txt = p.read_text()
    assert "P3_micro_3m5m" in txt and "BASE_TF" in txt

def test_daily_parity_fix_and_tests_keep_running_scheduled():
    try:
        cron = subprocess.check_output(["crontab", "-l"], text=True)
    except Exception:
        cron = ""
    assert "daily_parity_fix.py" in cron, "daily parity auto-fix not scheduled (needs 03:30 UTC)"
    assert "ensure_tests_running.sh" in cron, "tests keep-running not scheduled (needs */30)"
    # recent daily fix output exists
    reports = list(pathlib.Path("data/reports").glob("daily_parity_fix_*.json"))
    assert reports, "no daily_parity_fix report yet — run tools/daily_parity_fix.py once"
    latest = max(reports, key=lambda pp: pp.stat().st_mtime)
    age_h = (datetime.datetime.now().timestamp() - latest.stat().st_mtime) / 3600
    assert age_h < 48, f"daily_parity_fix report stale {age_h:.1f}h ago ({latest.name}) — daily job not running"
