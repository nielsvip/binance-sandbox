import json
import gzip
import hashlib
from pathlib import Path

import chart_server
from tools import results_dashboard_lib as dashboard
from tools import results_digest_email as digest

CURRENT_CONTRACT = next(iter(dashboard.CURRENT_STOCK_CONTRACTS))


def _write_current_matrix_bundle(reports, body):
    if "\n## Current c5 vector-first bundle cycle" not in body:
        body += "\n## Current c5 vector-first bundle cycle\n"
    digest_path = reports / "SWITCH_MATRIX_TRB_DIGEST.md"
    digest_path.write_text(body)
    matrix_path = reports / "SWITCH_MATRIX_TRB.csv.gz"
    with gzip.open(matrix_path, "wt") as handle:
        handle.write("# CURRENT_CAMPAIGN=stocks_repaired_20260730_c5\n")
        handle.write(f"# CURRENT_CONTRACT_VERSION={CURRENT_CONTRACT}\n")
        handle.write(
            "# CURRENT_MATRIX_SCOPE="
            "CURRENT_CAMPAIGN_CURRENT_CODE_NPZ_SIDE_FINGERPRINTS_ONLY\n"
        )
        handle.write(
            "# CANONICAL_MATRIX=data/reports/SWITCH_MATRIX_TRB.csv.gz\n"
        )
        handle.write("main_switch,sub_setting,value,description,status\n")
    policy = {}
    for side in ("LONG", "SHORT"):
        for rank in range(1, 12):
            key = f"{side[0]}{rank}_{side}"
            low, high = ((50.0, 80.0) if rank <= 10 else (20.0, 60.0))
            policy[key] = {
                "rank": rank,
                "cohort": "TOP_10" if rank <= 10 else "REMAINDER",
                "tim_min_pct": low,
                "tim_max_pct": high,
                "source": f"symbols_trb_{side.lower()}.json",
            }
    provenance = {
        "schema": "switch-matrix-trb-current-digest-v1",
        "campaign": "stocks_repaired_20260730_c5",
        "contract_version": CURRENT_CONTRACT,
        "matrix_scope": (
            "CURRENT_CAMPAIGN_CURRENT_CODE_NPZ_SIDE_FINGERPRINTS_ONLY"
        ),
        "canonical_matrix": "data/reports/SWITCH_MATRIX_TRB.csv.gz",
        "canonical_matrix_sha256": hashlib.sha256(
            matrix_path.read_bytes()
        ).hexdigest(),
        "digest_sha256": hashlib.sha256(
            digest_path.read_bytes()
        ).hexdigest(),
        "active_key_count": len(policy),
        "tim_policy": policy,
    }
    digest_path.with_suffix(
        digest_path.suffix + ".provenance.json"
    ).write_text(json.dumps(provenance))


def test_winner_requires_real_two_x_multiple_not_percentage_points() -> None:
    base = {"real_sharpe": 1.0}
    assert dashboard._bh_multiple({**base, "gain_vs_bh": 9.01}) is None
    assert not dashboard._winner_floor(
        {**base, "gain_vs_bh": 9.01}, "crypto"
    )
    assert not dashboard._winner_floor(
        {**base, "bh_multiple": 1.999}, "stock"
    )
    assert dashboard._winner_floor(
        {**base, "bh_multiple": 2.0}, "stock"
    )
    assert dashboard._winner_floor(
        {
            **base,
            "real_gain_pct": 250.0,
            "bh_gain_pct": 100.0,
        },
        "crypto",
    )


def test_stock_record_requires_both_current_campaign_and_c4_contract() -> None:
    base = {
        "mode": "stock",
        "campaign": "stocks_repaired_20260730_c5",
    }
    assert not dashboard._current_mode_record(base, "stock")
    assert not dashboard._current_mode_record(
        {**base, "matrix_contract_version": "tradier-matrix-exec-c3"},
        "stock",
    )
    assert dashboard._current_mode_record(
        {
            **base,
            "matrix_contract_version": (
                CURRENT_CONTRACT
            ),
        },
        "stock",
    )
    assert not dashboard._current_mode_record(
        {
            **base,
            "campaign": "STOCKS_BASELINE_V2_S4H",
            "matrix_contract_version": (
                CURRENT_CONTRACT
            ),
        },
        "stock",
    )


def test_build_report_excludes_historical_and_sub_two_x_rows(
    tmp_path, monkeypatch
) -> None:
    gating = tmp_path / "gating.jsonl"
    rows = [
        {
            "mode": "stock",
            "key": "OLD_LONG",
            "campaign": "STOCKS_BASELINE_V2_S4H",
            "matrix_contract_version": CURRENT_CONTRACT,
            "real_baseline_sharpe": 5.0,
            "bh_multiple": 20.0,
            "action": "CURRENT",
            "ts": 1,
        },
        {
            "mode": "stock",
            "key": "SUB2_LONG",
            "campaign": "stocks_repaired_20260730_c5",
            "matrix_contract_version": CURRENT_CONTRACT,
            "real_baseline_sharpe": 2.0,
            "bh_multiple": 1.99,
            "action": "CURRENT",
            "ts": 2,
        },
        {
            "mode": "stock",
            "key": "GREEN_LONG",
            "campaign": "stocks_repaired_20260730_c5",
            "matrix_contract_version": CURRENT_CONTRACT,
            "real_baseline_sharpe": 1.0,
            "bh_multiple": 2.1,
            "action": "CURRENT",
            "ts": 3,
        },
    ]
    gating.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    rescues = tmp_path / "rescues.jsonl"
    rescues.write_text("")
    vec = tmp_path / "vec.json"
    vec.write_text(
        json.dumps(
            {
                "GREEN_LONG": {
                    "campaign": "stocks_repaired_20260730_c5",
                    "matrix_contract_version": (
                        CURRENT_CONTRACT
                    ),
                },
                "OLD_LONG": {
                    "campaign": "STOCKS_BASELINE_V2_S4H",
                    "matrix_contract_version": (
                        CURRENT_CONTRACT
                    ),
                },
            }
        )
    )
    active = tmp_path / "active.json"
    active.write_text("{}")
    monkeypatch.setattr(dashboard, "GATING_CORRECTIONS", gating)
    monkeypatch.setattr(dashboard, "RESCUES", rescues)
    monkeypatch.setitem(dashboard.VEC_BASELINES, "stock", vec)
    monkeypatch.setitem(dashboard.ACTIVE_CONFIG, "stock", active)

    report = dashboard.build_report("stock")

    assert {row["key"] for row in report["all_confirmed"]} == {
        "SUB2_LONG",
        "GREEN_LONG",
    }
    assert [row["key"] for row in report["winners"]] == ["GREEN_LONG"]
    assert report["excluded_legacy_records"] == 1
    assert report["round_trip_cost_pct"] == 0.05
    assert report["minimum_bh_multiple"] == 2.0


def test_current_matrix_digest_fails_closed_without_quarantine_markers(
    tmp_path, monkeypatch
) -> None:
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    digest = reports / "SWITCH_MATRIX_TRB_DIGEST.md"
    digest.write_text(
        "STOCKS_BASELINE_V2_S4H\nOLD_LONG 99x CURRENT WINNER\n"
    )
    monkeypatch.setattr(chart_server, "BASE_PATH", tmp_path)

    html = chart_server._current_stock_matrix_html()

    assert "CURRENT C5 DIGEST REJECTED" in html
    assert "OLD_LONG 99x CURRENT WINNER" not in html
    assert "STOCKS_BASELINE_V2_S4H" not in html


def test_current_and_historical_panels_are_separate_and_costs_are_exact(
    tmp_path, monkeypatch
) -> None:
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    _write_current_matrix_bundle(
        reports,
        "# current\n"
        "- Current repaired-contract ENGINE rows: **1**\n"
        "- Historical/pre-fix ENGINE rows quarantined from current rankings\n"
        "- Current contract: campaign `stocks_repaired_20260730_c5`\n"
        "GREEN_LONG capture 2.1x\n"
    )
    monkeypatch.setattr(chart_server, "BASE_PATH", tmp_path)

    current = chart_server._current_stock_matrix_html()
    historical = chart_server._historical_stock_quarantine_html()
    crypto = chart_server._results_section_html(
        {
            "mode": "crypto",
            "total_keys": 1,
            "enabled_keys": 1,
            "gated_off_keys": 0,
            "winners_count": 1,
            "mid_count": 0,
            "rescued_count": 0,
            "real_engine_confirmed_keys": 1,
            "vec_screened_keys": 1,
            "vec_only_keys": 0,
            "winners": [
                {
                    "key": "BTCUSDC_LONG",
                    "real_sharpe": 1.0,
                    "bh_multiple": 2.1,
                    "trades": 5,
                    "date": "2026-07-29",
                }
            ],
            "gated": [],
            "rescued": [],
            "round_trip_cost_pct": 0.08,
            "minimum_bh_multiple": 2.0,
        }
    )

    assert CURRENT_CONTRACT in current
    # The digest is now integrity metadata only: result values must come from
    # the canonical provenance-backed reporting adapter, never copied prose.
    assert "GREEN_LONG capture 2.1x" not in current
    assert "Current canonical matrix lineage" in current
    assert "STOCKS_BASELINE_V2_S4H" not in current
    assert "STOCKS_BASELINE_V2_S4H" in historical
    assert "NOT CURRENT C5" in historical
    assert "0.05% round trip" in current
    assert "0.05%%" not in historical
    assert "0.08% round trip" in crypto
    assert "0.8% round trip" not in current + historical + crypto
    assert "2.100x" in crypto


def test_backtest_review_route_uses_asset_specific_cost_label() -> None:
    client = chart_server.app.test_client()
    crypto = client.get("/backtest-review/crypto/top").get_data(as_text=True)
    stocks = client.get("/backtest-review/stocks/top").get_data(as_text=True)
    assert "~0.08% per trade" in crypto
    assert "~0.05% per trade" in stocks
    assert "~0.8% per trade" not in crypto


def test_backtest_top_fails_closed_without_two_x_bh(monkeypatch) -> None:
    rows = [
        {
            "run": "no_bh",
            "pool_sharpe": 2.0,
            "sym_sharpe": 1.0,
            "gain_per_yr": 100.0,
            "trades": 3000,
            "n_syms": 100,
            "n_years": 2.0,
            "syms": ["MU"],
        },
        {
            "run": "sub2",
            "pool_sharpe": 2.0,
            "sym_sharpe": 1.0,
            "gain_per_yr": 100.0,
            "trades": 3000,
            "n_syms": 100,
            "n_years": 2.0,
            "syms": ["MU"],
            "bh_multiple": 1.99,
        },
        {
            "run": "green",
            "pool_sharpe": 2.0,
            "sym_sharpe": 1.0,
            "gain_per_yr": 100.0,
            "trades": 3000,
            "n_syms": 100,
            "n_years": 2.0,
            "syms": ["MU"],
            "bh_multiple": 2.1,
        },
    ]
    monkeypatch.setitem(
        chart_server._precomputed, "runs_ranked", json.dumps(rows)
    )
    client = chart_server.app.test_client()
    payload = client.get(
        "/backtest_review_top?asset=stocks"
    ).get_json()
    by_run = {row["run"]: row for row in payload["rows"]}

    assert not by_run["no_bh"]["promotable"]
    assert "BH_MULTIPLE_MISSING" in by_run["no_bh"]["promotion_blockers"]
    assert not by_run["sub2"]["promotable"]
    assert "BELOW_2X_BH" in by_run["sub2"]["promotion_blockers"]
    assert by_run["green"]["promotable"]
    assert payload["promotion_contract"]["min_bh_multiple"] == 2.0


def test_results_route_orders_current_c4_then_quarantine_then_crypto(
    tmp_path, monkeypatch
) -> None:
    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    _write_current_matrix_bundle(
        reports,
        "- Current repaired-contract ENGINE rows: **1**\n"
        "- Historical/pre-fix ENGINE rows quarantined from current rankings\n"
        "- Current contract: campaign `stocks_repaired_20260730_c5`\n"
    )
    crypto_report = {
        "mode": "crypto",
        "total_keys": 0,
        "enabled_keys": 0,
        "gated_off_keys": 0,
        "winners_count": 0,
        "mid_count": 0,
        "rescued_count": 0,
        "real_engine_confirmed_keys": 0,
        "vec_screened_keys": 0,
        "vec_only_keys": 0,
        "winners": [],
        "gated": [],
        "rescued": [],
        "round_trip_cost_pct": 0.08,
        "minimum_bh_multiple": 2.0,
        "vec_mtime": None,
        "gating_mtime": None,
        "rescues_mtime": None,
    }
    monkeypatch.setattr(chart_server, "BASE_PATH", tmp_path)
    monkeypatch.setattr(
        chart_server.results_dashboard_lib,
        "build_report",
        lambda mode: crypto_report,
    )
    monkeypatch.setattr(
        chart_server.results_dashboard_lib,
        "get_s1_liveness",
        lambda: {"cpu_line": "test", "mem_line": "", "progress": {}},
    )
    html = (
        chart_server.app.test_client()
        .get("/results")
        .get_data(as_text=True)
    )

    current_at = html.index("CURRENT REPAIRED MATRIX")
    quarantine_at = html.index("HISTORICAL QUARANTINE")
    crypto_at = html.index("CRYPTO (USDT/USDC)")
    assert current_at < quarantine_at < crypto_at
    assert CURRENT_CONTRACT in html
    assert "0.05% round trip" in html
    assert "0.08% round trip" in html
    assert "0.8% round trip" not in html


def test_current_matrix_api_and_stock_chart_use_provenance_selector(monkeypatch) -> None:
    expected = {
        "schema": "current-matrix-best-v2",
        "selection_rule": "no baseline or vector proxy",
        "rows": [{"key": "MU_LONG", "recipe_overrides": {"X": True}}],
    }
    monkeypatch.setattr(
        chart_server.current_matrix_reporting,
        "dashboard_payload",
        lambda base: expected,
    )
    response = chart_server.app.test_client().get("/current_matrix_best")
    assert response.status_code == 200
    assert response.get_json() == expected
    chart = Path("chart_static/chart.html").read_text()
    assert "/trb_best_backtest_trades" in chart
    assert "no proxy markers" in chart
    assert "__CURRENT_MATRIX__" in chart
    assert "buildCanonicalActionMarkers" in chart
    assert "canonical.actions" in chart
    assert 'id="priceChartStatus"' in chart
    assert "timeScale().fitContent()" in chart
    assert "/trb_klines_live" in chart
    assert "Tradier archive" in chart
    assert "auto-focused latest exact trade window" in chart
    assert "snapCanonicalMarkersToBars" in chart
    assert "_exactTime: marker.time" in chart
    assert "marker._action" in chart
    assert 'fallbackParams.set("start", startTs)' in chart
    assert 'fallbackParams.set("end", endTs)' in chart
    for action in ("ENTRY", "REENTRY", "AUGMENT", "REDUCE", "EXIT"):
        assert action in chart
    review = Path("chart_static/trb_review.html").read_text()
    assert "btResp.actions" in review
    assert "btActions:showBt?btActions:[]" in review
    assert "no proxy/vector markers used" in review
    for action in ("ENTRY", "REENTRY", "AUGMENT", "REDUCE", "EXIT"):
        assert f'{action}:{{color:' in review


def test_trb_kline_fallback_passes_requested_historical_window(monkeypatch) -> None:
    observed = {}

    def merged(sym, interval="5m", max_bars=5000, start=None, end=None):
        observed.update(
            sym=sym, interval=interval, max_bars=max_bars,
            start=start, end=end,
        )
        return ([{"t": start, "o": 1, "h": 2, "l": 1, "c": 2, "v": 3}], "fixture")

    monkeypatch.setattr(chart_server, "_merged_stock_klines", merged)
    response = chart_server.app.test_client().get(
        "/trb_klines_live?sym=lac&interval=5m&max=123"
        "&start=1704067200&end=1704153600"
    )
    assert response.status_code == 200
    assert observed == {
        "sym": "LAC", "interval": "5m", "max_bars": 123,
        "start": 1704067200, "end": 1704153600,
    }
    assert response.get_json()["bars"][0]["t"] == 1704067200


def test_focus4_is_explicitly_historical_not_current_c4(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(chart_server, "BASE_PATH", tmp_path)
    html = (
        chart_server.app.test_client()
        .get("/focus4")
        .get_data(as_text=True)
    )
    assert "HISTORICAL PRE-C4 DIAGNOSTIC" in html
    assert "NOT CURRENT MATRIX RANKING" in html


def test_email_digest_calls_current_rows_qualifiers_and_prints_true_multiple(
    monkeypatch,
) -> None:
    def report(mode):
        return {
            "mode": mode,
            "total_keys": 1,
            "enabled_keys": 1,
            "gated_off_keys": 0,
            "winners_count": 1,
            "mid_count": 0,
            "rescued_count": 0,
            "real_engine_confirmed_keys": 1,
            "vec_screened_keys": 1,
            "vec_only_keys": 0,
            "all_confirmed": [],
            "gated": [],
            "rescued": [],
            "winners": [
                {
                    "key": "MU_LONG" if mode == "stock" else "BTCUSDC_LONG",
                    "real_sharpe": 1.2,
                    "bh_multiple": 2.345,
                    "trades": 42,
                    "date": "2026-07-29",
                    "ts": 1,
                }
            ],
        }

    monkeypatch.setattr(digest.lib, "build_report", report)
    monkeypatch.setattr(
        digest.lib,
        "get_s1_liveness",
        lambda: {"cpu_line": "test", "mem_line": "", "progress": {}},
    )
    monkeypatch.setattr(digest, "_stale_progress_check", lambda *_: ("", {}))
    monkeypatch.setattr(digest, "_matrix_guard_totals_section", lambda: "")
    monkeypatch.setattr(digest, "_gainmo_sections", lambda: "")
    monkeypatch.setattr(digest, "_latest_matrix_filling_campaigns_section", lambda: "")
    monkeypatch.setattr(digest, "_switch_matrix_digest_section", lambda: "")
    monkeypatch.setattr(digest, "_legacy_reopt_staleness_note", lambda: "")

    html, subject, _ = digest.build_digest({}, 100)

    assert "Top 10 &ge;2x B&amp;H qualifiers" in html
    assert "2.345x" in html
    assert "gain_vs_bh</th>" not in html
    assert "winners/" not in subject
    assert "qualifiers/" in subject
def test_current_matrix_reporting_hot_reloads_after_source_change(
    monkeypatch, tmp_path
) -> None:
    source = tmp_path / "current_matrix_reporting.py"
    source.write_text("# changed reporting adapter\n")
    original = chart_server.current_matrix_reporting
    replacement = object()
    monkeypatch.setattr(chart_server, "current_matrix_reporting", original)
    monkeypatch.setattr(chart_server, "_CMR_SOURCE", source)
    monkeypatch.setattr(chart_server, "_CMR_MTIME_NS", 0)
    monkeypatch.setattr(chart_server.importlib, "reload", lambda module: replacement)

    assert chart_server._fresh_current_matrix_reporting() is replacement
    assert chart_server._CMR_MTIME_NS == source.stat().st_mtime_ns
