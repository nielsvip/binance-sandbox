from datetime import datetime, timezone

import options_paper_email as ope


def test_runner_status_reports_running_when_cycles_are_fresh(monkeypatch):
    monkeypatch.setattr(
        ope,
        "_latest_variant_cycles",
        lambda: {
            "live": {
                "ts": "2026-08-04T17:22:51+00:00",
                "market_open": True,
                "error": None,
                "summary": {
                    "n_buy": 0,
                    "n_opportunities": 0,
                    "total_proposed_premium": 0.0,
                },
            }
        },
    )
    snap = ope._runner_status_snapshot(datetime(2026, 8, 4, 17, 30, tzinfo=timezone.utc))
    assert snap["state"] == "RUNNING"
    assert snap["live_locked"] is True


def test_status_html_exposes_paper_only_lock():
    html = ope._status_html(
        {
            "state": "RUNNING",
            "latest_ts": "2026-08-04T17:22:51+00:00",
            "age_minutes": 7.0,
            "live_locked": True,
            "variant_count": 7,
            "active_variants": 7,
            "error_variants": 0,
            "buy_variants": 0,
            "opp_variants": 0,
            "total_latest_premium": 0.0,
        }
    )
    assert "Paper runner status: RUNNING" in html
    assert "LIVE OPTIONS DISABLED" in html

