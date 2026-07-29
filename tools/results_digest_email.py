#!/usr/bin/env python3
"""12-hour digest email for the vec-screen -> reopt/gating -> real-engine
optimization pipeline. Cron: 0 0,12 * * * (00:00 and 12:00 UTC).

Reuses the existing Gmail send mechanism in morning_email.py (Keychain /
~/.gmail_app_pw password lookup + smtplib SSL/STARTTLS fallback) — does not
rebuild auth. Data comes from results_dashboard_lib.py, the same aggregation
the /results chart_server dashboard uses, so the email and the dashboard never
disagree.

Delta window: [max(last_sent_ts, now - 12h), now] read from
data/results_digest_state.json, so a digest that runs a bit late still shows
a clean non-overlapping window since the last one actually sent (capped at 12h
lookback if a run was ever missed by more than that).

NO-LIES MANDATE: see results_dashboard_lib.py docstring — all real_sharpe_1sym
values are single-symbol (n_syms=1) real-engine confirmations, DIAGNOSTIC per
CLAUDE.md rule 5, never a promotion signal. This email is a monitoring digest.
"""
import argparse
import html as html_lib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_PATH))
sys.path.insert(0, str(BASE_PATH / "tools"))
import results_dashboard_lib as lib  # noqa: E402
import morning_email as me  # noqa: E402  (reused: get_gmail_password, FROM_EMAIL, TO_EMAIL, CSS)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("results_digest_email")

STATE_PATH = BASE_PATH / "data" / "results_digest_state.json"
DASHBOARD_URL = "http://localhost:5077/results"
XLS_ATTACH = ["SYMBOL_OVERVIEW_crypto.xlsx", "SYMBOL_OVERVIEW_stocks.xlsx"]
XLS_LINK_ONLY = ["PERSYM_APPLIED_REVIEW.xlsx", "FULL_PARAM_MATRIX_crypto.xlsx", "FULL_PARAM_MATRIX_stocks.xlsx"]
TWELVE_H = 12 * 3600
MATRIX_FILL_STAGES = (
    "VEC_STATE_AWARE_ENTRY_EXIT_BEAM_UNTOUCHED_OOS",
    "VEC_REGIME_ENTRY_EXIT_BEAM_UNTOUCHED_OOS",
    "VEC_ENTRY_EXIT_BEAM_UNTOUCHED_OOS",
)
MATRIX_FILL_STAGE_PLACEHOLDERS = ",".join("?" for _ in MATRIX_FILL_STAGES)
MATRIX_FILL_PRIORITY = ("MU", "ARM", "PBF")
MATRIX_GUARD_SCRIPT = BASE_PATH / "tools" / "matrix_guard.py"
MATRIX_STALE_H = 24.0


def _matrix_guard_totals_section():
    """USER 2026-07-29: S1 now runs ONLY SWITCH_MATRIX_TRB work + these digests,
    so this section leads the email. Runs tools/matrix_guard.py verbatim -- the
    ONE canonical counter per CLAUDE.md's matrix_guard warning -- so this digest
    never invents a second/competing number for the same fill state."""
    import subprocess
    try:
        proc = subprocess.run(
            [sys.executable, str(MATRIX_GUARD_SCRIPT)],
            cwd=str(BASE_PATH), capture_output=True, text=True, timeout=60,
        )
        body = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        if not body.strip():
            body = "(matrix_guard.py produced no output; exit=%s)" % proc.returncode
    except Exception as exc:
        body = "matrix_guard.py failed to run: %s" % exc
    return (
        "<h2>MATRIX FILL PROGRESS (canonical &mdash; tools/matrix_guard.py, run live at digest-build time)</h2>"
        "<p>Reused verbatim from <code>tools/matrix_guard.py</code> / <code>./matrix_progress.sh</code> -- "
        "never a third counter. Canonical artifact: "
        "<code>data/reports/SWITCH_MATRIX_TRB_ENGINE_HIST_STOCKS_BASELINE_V2_S4H.csv.gz</code>. "
        "NOT the matrix: <code>data/matrix_npz/*</code>, <code>band_ladder_walkforward_*</code>, "
        "<code>data/handle_priority/*.json</code>, <code>SWITCH_MATRIX_TRB.csv.gz</code> (no _ENGINE_HIST_ suffix).</p>"
        "<pre style='font-size:11px;white-space:pre-wrap'>%s</pre>"
        "<p><b>Live promotion status:</b> 0 matrix-derived configs are currently deployed to any live "
        "trb/trc override -- said honestly rather than implying attribution. The live-fills tables "
        "below (from data/history/, the trade ledger) run on pre-existing configs, NOT matrix output.</p>"
    ) % html_lib.escape(body)


def _switch_matrix_digest_section():
    """Embed the truthful ENGINE matrix progress report in the Gmail digest.

    The mailer normally runs from the live checkout while the matrix is produced in
    binance-sandbox, so prefer an explicit env path, then the local checkout, then S1's
    canonical sandbox path.  Missing/stale data is visible instead of silently omitted.

    2026-07-29: this file's sole producer cron (watchdog_lab_matrix.sh, */10) was
    disabled when S1 was narrowed to matrix-only work, so it would otherwise age
    forever. The generator (tools/switch_matrix_digest.py) is a read-only report
    writer -- not param_matrix_daemon/the manifest/backtest_v8_engine.py -- so if
    the file is stale (>1h) this regenerates it in place (~0.6s) before reading.
    """
    candidates = [
        Path(os.environ.get("SWITCH_MATRIX_DIGEST_PATH", "")) if os.environ.get("SWITCH_MATRIX_DIGEST_PATH") else None,
        BASE_PATH / "data" / "reports" / "SWITCH_MATRIX_TRB_DIGEST.md",
        Path("/home/niels/binance-sandbox/data/reports/SWITCH_MATRIX_TRB_DIGEST.md"),
    ]
    path = next((p for p in candidates if p and p.exists()), None)
    generator = BASE_PATH / "tools" / "switch_matrix_digest.py"
    if path is not None and generator.exists():
        try:
            age_h = (time.time() - path.stat().st_mtime) / 3600.0
        except OSError:
            age_h = 999.0
        if age_h > 1.0:
            import subprocess
            try:
                subprocess.run(
                    [sys.executable, str(generator), "--output", str(path)],
                    cwd=str(BASE_PATH), capture_output=True, text=True, timeout=120,
                )
            except Exception:
                pass
    if path is None:
        return "<h2>SWITCH_MATRIX_TRB progress</h2><p class='r'><b>MISSING:</b> matrix digest was not generated.</p>"
    try:
        age_h = max(0.0, (time.time() - path.stat().st_mtime) / 3600.0)
        body = path.read_text(errors="replace")
    except Exception as exc:
        return "<h2>SWITCH_MATRIX_TRB progress</h2><p class='r'>read failed: %s</p>" % html_lib.escape(str(exc))
    stale = " class='r'" if age_h > 1.0 else ""
    return (
        "<h2>SWITCH_MATRIX_TRB progress</h2>"
        "<p%s>source <code>%s</code> &middot; age %.1fh</p>"
        "<p style='color:#666'>Note: the pilot table inside this report tracks the "
        "<code>stocks_repaired_20260725_c2</code> campaign's own key set (currently MU_LONG/VT_LONG/HAO_SHORT), "
        "which can differ from the canonical 6-pilot list in the MATRIX FILL PROGRESS section above "
        "(MU_LONG, NVDA_LONG, VT_LONG, TTD_SHORT, ACN_SHORT, LAC_SHORT) -- treat the section above as the bar.</p>"
        "<pre style='font-size:11px;white-space:pre-wrap'>%s</pre>"
    ) % (stale, html_lib.escape(str(path)), age_h, html_lib.escape(body[:24000]))


def _usable_fleet_db():
    """Return the first path-fleet DB that actually contains beam rows."""
    candidates = [
        Path(os.environ["PATH_FLEET_DB_PATH"])
        if os.environ.get("PATH_FLEET_DB_PATH")
        else None,
        BASE_PATH / "data" / "reports" / "path_fleet" / "queue.db",
        Path(
            "/home/niels/binance-sandbox/data/reports/path_fleet/queue.db"
        ),
    ]
    for path in candidates:
        if path is None or not path.exists():
            continue
        try:
            con = sqlite3.connect(str(path))
            count = con.execute(
                "SELECT COUNT(*) FROM results WHERE stage IN (%s)"
                % MATRIX_FILL_STAGE_PLACEHOLDERS,
                MATRIX_FILL_STAGES,
            ).fetchone()[0]
            con.close()
            if count:
                return path
        except (OSError, sqlite3.Error):
            continue
    return None


def _vec_research_root():
    candidates = [
        Path(os.environ["VEC_RESEARCH_ROOT"])
        if os.environ.get("VEC_RESEARCH_ROOT")
        else None,
        BASE_PATH / "data" / "reports" / "vec_research",
        Path("/home/niels/binance-sandbox/data/reports/vec_research"),
    ]
    return next((p for p in candidates if p is not None and p.exists()), None)


def _load_campaign_manifest(root, campaign_id):
    """Load the campaign's immutable/compact manifest, never a hardcoded result."""
    if (
        root is None
        or not campaign_id
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", str(campaign_id))
    ):
        return {}, None
    campaign_dir = root / str(campaign_id)
    for name in (
        "campaign_manifest.json",
        "compact_result.json",
        "campaign_result.json",
    ):
        path = campaign_dir / name
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text()), path
        except (OSError, ValueError):
            continue
    return {}, None


def _manifest_counts(manifest):
    results = manifest.get("results") or []
    counts = manifest.get("counts") or {}
    candidates = counts.get("candidate_rows")
    if not isinstance(candidates, (int, float)):
        vals = [
            r.get("candidate_count")
            for r in results
            if isinstance(r, dict)
            and isinstance(r.get("candidate_count"), (int, float))
        ]
        candidates = sum(vals) if vals else None
    schedules = counts.get("entry_schedules")
    if not isinstance(schedules, (int, float)):
        schedules = len(results) if results else None
    exact = manifest.get("exact_replay_queue")
    exact_count = len(exact) if isinstance(exact, list) else None
    if exact_count is None:
        exact_count = counts.get("exact_replay_queue")
    return candidates, schedules, exact_count


def _manifest_candidate_counts(manifest):
    out = {}
    for row in manifest.get("results") or []:
        if not isinstance(row, dict):
            continue
        symbol, side = row.get("symbol"), row.get("side")
        count = row.get("candidate_count")
        if (
            symbol
            and side
            and isinstance(count, (int, float))
        ):
            key = (str(symbol), str(side).upper())
            out[key] = out.get(key, 0) + int(count)
    return out


def _fold_failure_reasons(fold, tim_low, tim_high):
    reasons = []
    alpha_bh = fold.get("alpha_vs_bh_pp")
    alpha_control = fold.get(
        "alpha_vs_same_entry_e02_pp",
        fold.get("alpha_vs_control_pp"),
    )
    tim = fold.get("weighted_tim_pct", fold.get("tim_pct"))
    exits = fold.get("exit_fills", fold.get("actual_exit_fills"))
    if isinstance(alpha_bh, (int, float)) and alpha_bh <= 0:
        reasons.append("&le;B&amp;H")
    if isinstance(alpha_control, (int, float)) and alpha_control <= 0:
        reasons.append("&le;control")
    if isinstance(tim, (int, float)):
        if tim < tim_low:
            reasons.append("TIM&lt;%.0f" % tim_low)
        elif tim > tim_high:
            reasons.append("TIM&gt;%.0f" % tim_high)
    if isinstance(exits, (int, float)) and exits < 1:
        reasons.append("no exit")
    if int(fold.get("future_htf_source_count") or 0):
        reasons.append("future HTF")
    if fold.get("entry_capacity_breach"):
        reasons.append("capacity")
    if fold.get("insolvent"):
        reasons.append("insolvent")
    return reasons


def _discovery_cell(payload, tim_low, tim_high):
    evidence = payload.get("discovery_fold_evidence") or []
    gates = payload.get("discovery_fold_gate_pass") or []
    parts = []
    for idx, fold in enumerate(evidence):
        if not isinstance(fold, dict):
            continue
        passed = bool(gates[idx]) if idx < len(gates) else False
        tim = fold.get("weighted_tim_pct", fold.get("tim_pct"))
        tim_text = "%.1f%%" % tim if isinstance(tim, (int, float)) else "TIM?"
        reasons = _fold_failure_reasons(fold, tim_low, tim_high)
        suffix = "" if passed or not reasons else " (%s)" % ", ".join(reasons)
        parts.append(
            "F%s%s %s%s"
            % (
                html_lib.escape(str(fold.get("fold", idx + 1))),
                "&#10003;" if passed else "&#10007;",
                tim_text,
                suffix,
            )
        )
    return "<br>".join(parts) if parts else "no discovery-fold evidence"


def _gray_reason(row, payload, tim_low, tim_high):
    reasons = []
    gates = payload.get("discovery_fold_gate_pass") or []
    if gates and not all(bool(v) for v in gates):
        reasons.append("discovery fold failed")
    strategy = row.get("strategy_return_pct")
    bh = row.get("bh_return_pct")
    control = row.get("same_entry_control_return_pct")
    tim = row.get("tim_pct")
    if isinstance(strategy, (int, float)) and isinstance(bh, (int, float)):
        if strategy <= bh:
            reasons.append("final &le; B&amp;H")
    if (
        isinstance(strategy, (int, float))
        and isinstance(control, (int, float))
        and strategy <= control
    ):
        reasons.append("final &le; control")
    if isinstance(tim, (int, float)):
        if tim < tim_low:
            reasons.append("final TIM&lt;%.0f" % tim_low)
        elif tim > tim_high:
            reasons.append("final TIM&gt;%.0f" % tim_high)
    if not row.get("exact_replay"):
        reasons.append("not exact-validated")
    return "; ".join(reasons) or html_lib.escape(str(row.get("status") or "research only"))


def _discovery_rank(item, tim_low, tim_high):
    """Display representative chosen without looking at the untouched final."""
    payload = item["payload"]
    gates = payload.get("discovery_fold_gate_pass") or []
    evidence = payload.get("discovery_fold_evidence") or []
    tim_distance = 0.0
    for fold in evidence:
        if not isinstance(fold, dict):
            continue
        tim = fold.get("weighted_tim_pct", fold.get("tim_pct"))
        if isinstance(tim, (int, float)):
            tim_distance += abs(float(tim) - (tim_low + tim_high) / 2.0)
        else:
            tim_distance += 1e6
    return (sum(bool(v) for v in gates), -tim_distance, item["row"]["id"])


def _latest_matrix_filling_campaigns_section():
    """Latest vector matrix work, with untouched-final rows kept research-only."""
    db_path = _usable_fleet_db()
    if db_path is None:
        return (
            "<h2>Latest matrix-filling campaigns</h2>"
            "<p class='r'><b>MISSING:</b> no append-only path-fleet beam rows.</p>"
        )
    try:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        raw_rows = con.execute(
            """SELECT * FROM results
               WHERE stage IN (%s)
               ORDER BY id""" % MATRIX_FILL_STAGE_PLACEHOLDERS,
            MATRIX_FILL_STAGES,
        ).fetchall()
        con.close()
    except (OSError, sqlite3.Error) as exc:
        return (
            "<h2>Latest matrix-filling campaigns</h2>"
            "<p class='r'>fleet read failed: %s</p>"
            % html_lib.escape(str(exc))
        )

    parsed = []
    for raw in raw_rows:
        row = dict(raw)
        try:
            payload = json.loads(row.get("payload_json") or "{}")
        except ValueError:
            continue
        campaign_id = payload.get("campaign_id")
        if not campaign_id:
            continue
        parsed.append({"row": row, "payload": payload, "campaign_id": campaign_id})

    # For each stage/key, report only its newest append-only campaign. Multiple
    # entry schedules in that campaign are represented using discovery data only.
    latest_campaign = {}
    for item in parsed:
        row = item["row"]
        key = (row["stage"], row["symbol"], row["side"])
        marker = (float(row.get("created_at") or 0), int(row["id"]))
        if key not in latest_campaign or marker > latest_campaign[key][0]:
            latest_campaign[key] = (marker, item["campaign_id"])
    parsed = [
        item
        for item in parsed
        if item["campaign_id"]
        == latest_campaign[
            (
                item["row"]["stage"],
                item["row"]["symbol"],
                item["row"]["side"],
            )
        ][1]
    ]

    research_root = _vec_research_root()
    campaign_ids = sorted({item["campaign_id"] for item in parsed})
    manifests = {}
    manifest_paths = {}
    for campaign_id in campaign_ids:
        manifests[campaign_id], manifest_paths[campaign_id] = (
            _load_campaign_manifest(research_root, campaign_id)
        )

    campaign_rows = []
    for campaign_id in sorted(
        campaign_ids,
        key=lambda c: max(
            item["row"]["created_at"]
            for item in parsed
            if item["campaign_id"] == c
        ),
        reverse=True,
    ):
        manifest = manifests[campaign_id]
        candidates, schedules, exact = _manifest_counts(manifest)
        contract = manifest.get("contract") or {}
        strategy_cap = contract.get("strategy_capacity_usd")
        bh_cap = contract.get("bh_capital_usd")
        keys = {
            (item["row"]["symbol"], item["row"]["side"])
            for item in parsed
            if item["campaign_id"] == campaign_id
        }
        stage_names = {
            item["row"]["stage"]
            for item in parsed
            if item["campaign_id"] == campaign_id
        }
        source = manifest_paths.get(campaign_id)
        campaign_rows.append(
            "<tr><td><code>%s</code></td><td>%s</td><td>%d</td>"
            "<td>%s / %s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                html_lib.escape(campaign_id),
                "<br>".join(
                    "<code>%s</code>" % html_lib.escape(stage)
                    for stage in sorted(stage_names)
                ),
                len(keys),
                "$%s" % "{:,.0f}".format(strategy_cap)
                if isinstance(strategy_cap, (int, float))
                else "&mdash;",
                "$%s" % "{:,.0f}".format(bh_cap)
                if isinstance(bh_cap, (int, float))
                else "&mdash;",
                "{:,}".format(int(schedules))
                if isinstance(schedules, (int, float))
                else "&mdash;",
                "{:,}".format(int(candidates))
                if isinstance(candidates, (int, float))
                else "&mdash;",
                "{:,}".format(int(exact))
                if isinstance(exact, (int, float))
                else "&mdash;",
                html_lib.escape(source.name) if source else "manifest missing",
            )
        )

    grouped = {}
    for item in parsed:
        row = item["row"]
        key = (
            row["stage"],
            row["symbol"],
            row["side"],
            item["campaign_id"],
        )
        grouped.setdefault(key, []).append(item)

    details = []
    for stage in MATRIX_FILL_STAGES:
        stage_groups = [
            (key, values)
            for key, values in grouped.items()
            if key[0] == stage
        ]
        if not stage_groups:
            continue
        for side in ("LONG", "SHORT"):
            side_groups = [
                (key, values)
                for key, values in stage_groups
                if str(key[2]).upper() == side
            ]
            if not side_groups:
                continue
            side_groups.sort(
                key=lambda pair: (
                    MATRIX_FILL_PRIORITY.index(pair[0][1])
                    if pair[0][1] in MATRIX_FILL_PRIORITY
                    else len(MATRIX_FILL_PRIORITY),
                    pair[0][1],
                )
            )
            rows = []
            for key, items in side_groups:
                campaign_id = key[3]
                manifest = manifests.get(campaign_id) or {}
                contract = manifest.get("contract") or {}
                tim_gate = contract.get("every_fold_tim_gate_pct") or [70.0, 80.0]
                try:
                    tim_low, tim_high = float(tim_gate[0]), float(tim_gate[1])
                except (TypeError, ValueError, IndexError):
                    tim_low, tim_high = 70.0, 80.0
                representative = max(
                    items,
                    key=lambda item: _discovery_rank(item, tim_low, tim_high),
                )
                row, payload = representative["row"], representative["payload"]
                per_key_candidates = _manifest_candidate_counts(manifest).get(
                    (str(row["symbol"]), str(row["side"]).upper())
                )
                strategy, bh, control = (
                    row.get("strategy_return_pct"),
                    row.get("bh_return_pct"),
                    row.get("same_entry_control_return_pct"),
                )
                final_text = (
                    "%s vs B&amp;H %s<br>vs control %s"
                    % (_fmt_pct(strategy), _fmt_pct(bh), _fmt_pct(control))
                )
                rows.append(
                    "<tr><td><b>%s_%s</b></td><td>%s<br>%s</td>"
                    "<td>%s</td><td>%s<br>%s exits</td><td>%s<br>%s</td>"
                    "<td>%s / %d fleet rows</td></tr>"
                    % (
                        html_lib.escape(str(row["symbol"])),
                        html_lib.escape(str(row["side"])),
                        html_lib.escape(str(payload.get("entry_family") or "?")),
                        html_lib.escape(str(payload.get("exit_family") or "?")),
                        _discovery_cell(payload, tim_low, tim_high),
                        final_text,
                        html_lib.escape(str(row.get("trades") or 0)),
                        _fmt_pct(row.get("tim_pct")),
                        _gray_reason(row, payload, tim_low, tim_high),
                        "{:,}".format(int(per_key_candidates))
                        if isinstance(per_key_candidates, (int, float))
                        else "&mdash;",
                        len(items),
                    )
                )
            details.append(
                "<h3><code>%s</code> &mdash; %s (kept separate)</h3>%s"
                % (
                    html_lib.escape(stage),
                    side,
                    _table(
                        [
                            "key",
                            "discovery-selected entry / exit",
                            "discovery folds (pass + TIM)",
                            "untouched final capital return",
                            "TIM / why gray",
                            "candidates / rows",
                        ],
                        rows,
                    ),
                )
            )

    return (
        "<h2>Latest matrix-filling campaigns "
        "<span style='color:#666'>[RESEARCH ONLY &mdash; NOT ACCEPTED/LIVE]</span></h2>"
        "<p><b>Scope/units:</b> returns are capital-return percent on one final "
        "chronological outer-validation fold (no fold summing); TIM is percent. "
        "Strategy capacity and B&amp;H capital are shown from each campaign contract. "
        "Control is the identical frozen entry schedule with E02. "
        "Representative rows are selected only by discovery-fold gate coverage "
        "and TIM proximity; the untouched final is displayed afterward. "
        "Gray rows remain rejected and cannot be promoted without every discovery "
        "gate plus exact-engine validation.</p>"
        "<h3>Append-only campaign manifests</h3>%s%s"
        % (
            _table(
                [
                    "campaign",
                    "fleet stage",
                    "keys",
                    "strategy / B&amp;H capital",
                    "entry schedules",
                    "vector candidates",
                    "exact queue",
                    "manifest",
                ],
                campaign_rows,
            ),
            "".join(details),
        )
    )


def _load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2))


def _window_start(state, now):
    last_sent = state.get("last_sent_ts")
    if not last_sent:
        return now - TWELVE_H
    return max(float(last_sent), now - TWELVE_H)


def _fmt_sharpe(v):
    return "%.4f" % v if isinstance(v, (int, float)) else "&mdash;"


def _fmt_pct(v):
    return "%.2f%%" % v if isinstance(v, (int, float)) else "&mdash;"


def _rows_in_window(records, win_start, win_end):
    out = []
    for r in records:
        ts = r.get("ts") or 0
        if win_start <= ts <= win_end:
            out.append(r)
    return out


def _table(headers, rows, empty_msg="none"):
    if not rows:
        return "<p style='color:#888;margin:2px 0 10px 0'>%s</p>" % empty_msg
    th = "".join("<th>%s</th>" % h for h in headers)
    return "<table><tr>%s</tr>%s</table>" % (th, "".join(rows))


MANDATORY_8 = ["MU_LONG", "NVDA_LONG", "SNDK_LONG", "MRVL_LONG", "VLO_LONG", "INTC_LONG", "MSTR_SHORT", "WDAY_SHORT"]

STALE_PROGRESS_THRESHOLD_S = 12 * 3600  # USER 2026-07-15: alert when neither best real_sharpe_1sym
# nor best gain_vs_bh has improved in >12h -- a live "we are testing the wrong params" signal
# rather than something a human has to notice by eyeballing consecutive digests.


def _stale_progress_check(prev_state, reports, now):
    """Track the best real_sharpe_1sym and best gain_vs_bh seen so far, per mode, across
    digest runs (persisted in results_digest_state.json alongside the existing window
    tracking). If NEITHER has improved for >STALE_PROGRESS_THRESHOLD_S, that mode is
    flagged stale -- returns (html_banner_or_empty_string, tracking_dict_to_merge_into_state).
    NO-LIES note: real_sharpe_1sym is single-symbol [DIAGNOSTIC] per CLAUDE.md rule 5, never
    a promotion signal -- this uses it only as a relative trend indicator (is the search
    moving at all), not to claim a real Sharpe achievement.
    """
    prev_tracking = prev_state.get("stale_progress_tracking") or {}
    tracking = {}
    stale_modes = []
    for mode, rep in reports.items():
        winners = rep.get("winners") or []
        cur_best_sharpe = winners[0].get("real_sharpe") if winners else None
        gvbh_vals = [w.get("gain_vs_bh") for w in winners if isinstance(w.get("gain_vs_bh"), (int, float))]
        cur_best_gvbh = max(gvbh_vals) if gvbh_vals else None
        prev = prev_tracking.get(mode) or {}
        prev_sharpe = prev.get("best_sharpe")
        prev_sharpe_ts = prev.get("best_sharpe_ts", now)
        prev_gvbh = prev.get("best_gvbh")
        prev_gvbh_ts = prev.get("best_gvbh_ts", now)
        sharpe_improved = isinstance(cur_best_sharpe, (int, float)) and (
            not isinstance(prev_sharpe, (int, float)) or cur_best_sharpe > prev_sharpe + 1e-6)
        gvbh_improved = isinstance(cur_best_gvbh, (int, float)) and (
            not isinstance(prev_gvbh, (int, float)) or cur_best_gvbh > prev_gvbh + 1e-6)
        new_best_sharpe = cur_best_sharpe if sharpe_improved else prev_sharpe
        new_sharpe_ts = now if sharpe_improved else prev_sharpe_ts
        new_best_gvbh = cur_best_gvbh if gvbh_improved else prev_gvbh
        new_gvbh_ts = now if gvbh_improved else prev_gvbh_ts
        tracking[mode] = {
            "best_sharpe": new_best_sharpe, "best_sharpe_ts": new_sharpe_ts,
            "best_gvbh": new_best_gvbh, "best_gvbh_ts": new_gvbh_ts,
        }
        sharpe_stale_s = (now - new_sharpe_ts) if isinstance(new_best_sharpe, (int, float)) else 0
        gvbh_stale_s = (now - new_gvbh_ts) if isinstance(new_best_gvbh, (int, float)) else 0
        # Stale only if BOTH metrics have gone >12h without improvement (either one moving
        # forward means the search is still finding something).
        if sharpe_stale_s > STALE_PROGRESS_THRESHOLD_S and gvbh_stale_s > STALE_PROGRESS_THRESHOLD_S:
            stale_modes.append((mode, sharpe_stale_s / 3600.0, new_best_sharpe, new_best_gvbh))
    if not stale_modes:
        return "", tracking
    rows = "".join(
        "<li><b>%s</b>: no improvement in %.1fh (best real_sharpe_1sym=%s, best gain_vs_bh=%s) &mdash; "
        "the current sweep grid is very likely testing the wrong params. Check switch_priority.csv "
        "for untested high-signal knobs, or widen the param range.</li>" % (
            mode.upper(), hrs, _fmt_sharpe(best_s), _fmt_pct(best_g))
        for mode, hrs, best_s, best_g in stale_modes)
    banner = (
        "<div style='background:#fff3cd;border:1px solid #ffc107;border-radius:4px;padding:10px 14px;margin:6px 0 14px 0'>"
        "<b>&#9888; STALE PROGRESS &mdash; testing wrong params?</b>"
        "<ul style='margin:6px 0 0 0'>%s</ul></div>" % rows)
    return banner, tracking


def _qualifying_rows():
    rows = []
    try:
        for mode in ("stock", "crypto"):
            rep = lib.build_report(mode)
            for w in rep.get("winners", []):
                gvbh = w.get("gain_vs_bh")
                try:
                    gvbh_f = float(gvbh)
                except (TypeError, ValueError):
                    continue
                # 2026-07-28: `real_gain_vs_bh` in gating_corrections.jsonl is a
                # DIFFERENCE in percentage points, not a ratio — provable from the
                # rows themselves (real_gain_pct 0.03 - bh 0.028 = real_gain_vs_bh
                # 0.002). It was rendered "%.2fx", so +9.01pp was published as
                # "9.01x b&h". The same value is printed as "9.01%" ten lines away
                # by _fmt_pct, so the email contradicted itself. Render as points.
                if gvbh_f >= 2.0:
                    rows.append("<tr><td>%s</td><td>%s</td><td class='g'><b>%s</b></td><td class='g'><b>%+.2f pp</b></td><td>%s</td><td>%s</td></tr>" % (
                        mode.upper(), w.get("key"), _fmt_sharpe(w.get("real_sharpe")), gvbh_f, w.get("trades", "&mdash;"), (w.get("date") or "")[:10]))
    except Exception as e:
        rows.append("<tr><td colspan=6>qualifier scan failed: %s</td></tr>" % e)
    return rows


_GATE_STATE_PATH = Path("/Users/niels/logs/.digest_gate_state.json")


def _stocks_bh_capture_section():
    base = Path(__file__).resolve().parent.parent
    try:
        d = json.loads((base / "data/reports/stocks_bh_capture.json").read_text())
        rows = []
        for r in (d.get("rows") or [])[:25]:
            cap = r.get("capture_vs_bh")
            ccap = r.get("best_cell_capture")
            rows.append("<tr><td>%s</td><td>%.2f</td><td>%.3f</td><td class='%s'><b>%s</b></td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                r.get("key"), float(r.get("bh_per_mo") or 0), float(r.get("gain_per_mo") or 0),
                "g" if isinstance(cap, (int, float)) and cap >= 1.0 else "r", cap, r.get("trades"),
                r.get("time_in_mkt_pct"), r.get("best_cell") or "&mdash;", ccap))
        return ("<h2>STOCKS B&amp;H WINNERS &mdash; capture scoreboard (param_results_stocks.db; capture&lt;1 = DEFECT per Bible &sect;12.1) [DIAGNOSTIC n_syms=1 each]</h2>"
                + _table(["key", "b&amp;h/mo", "gain/mo", "capture", "trades", "tim%", "best cell", "cell capture"], rows,
                         "no key_baseline rows yet") + "<p>as of %s</p>" % d.get("ts", "?"))
    except Exception as e:
        return "<p>b&amp;h capture scoreboard unavailable: %s</p>" % e


def _persym_latest_sections():
    base = Path(__file__).resolve().parent.parent
    out = []
    prev = {}
    try:
        prev = json.loads(_GATE_STATE_PATH.read_text())
    except Exception:
        prev = {}
    new_state = {"keys": {}, "scoreboard": {}}
    try:
        sb = json.loads((base / "data/_diagnostic/trade_gate_scoreboard.json").read_text())
        psb = prev.get("scoreboard") or {}
        line = " &middot; ".join("%s=%s%s" % (k, sb.get(k), (" (%+d)" % (int(sb.get(k, 0)) - int(psb.get(k, 0))) if isinstance(sb.get(k), int) and psb.get(k) is not None and int(sb.get(k, 0)) != int(psb.get(k, 0)) else "")) for k in ("universe", "verdicted", "qualified_long", "qualified_short", "benched", "awaiting") if k in sb)
        new_state["scoreboard"] = {k: v for k, v in sb.items() if isinstance(v, int)}
        out.append("<h2>TRADE-GATE SCOREBOARD (delta vs last digest)</h2><p><b>%s</b> &middot; as of %s</p>" % (line or str(sb)[:200], sb.get("ts", "?")))
    except Exception as e:
        out.append("<p>scoreboard unavailable: %s</p>" % e)
    rows_imp, rows_all = [], []
    try:
        import glob as _g
        summaries = []
        for f in _g.glob(str(base / "data/_diagnostic/capture/*.summary.json")):
            try:
                d = json.loads(open(f).read())
                if d.get("key"):
                    summaries.append(d)
            except Exception:
                continue
        latest = {}
        for d in summaries:
            k = d["key"]
            if k not in latest or (d.get("ts") or 0) > (latest[k].get("ts") or 0):
                latest[k] = d
        for k, d in latest.items():
            new_state["keys"][k] = round(float(d.get("gain_vs_bh") or 0.0), 4)
        movers = []
        for k, d in latest.items():
            cur = float(d.get("gain_vs_bh") or 0.0)
            old = prev.get("keys", {}).get(k)
            if old is not None and abs(cur - float(old)) > 1e-9:
                movers.append((cur - float(old), k, d, float(old)))
        movers.sort(reverse=True)
        for delta, k, d, old in movers[:25]:
            rows_imp.append("<tr><td>%s</td><td>%s</td><td class='%s'><b>%.3f &rarr; %.3f</b></td><td>%+.2f%%</td><td>%s</td><td>%s</td><td>%.2f%%</td></tr>" % (
                k, d.get("tier", "?"), "g" if delta > 0 else "r", old, cur, float(d.get("total_gain_pct") or 0), d.get("trades", "?"), "%s" % d.get("pool_sharpe", "?"), float(d.get("coverage_pct") or 0)))
        top = sorted(latest.values(), key=lambda d: float(d.get("gain_vs_bh") or 0.0), reverse=True)[:20]
        # 2026-07-28: this column is a RATIO (capture vs b&h), so <1.0 means the
        # key LOST to buy-and-hold. The class was hardcoded 'g', painting 17 of 20
        # losing rows green. Colour by the same >=1.0 rule the capture section uses.
        for d in top:
            _gv = float(d.get("gain_vs_bh") or 0)
            rows_all.append("<tr><td>%s</td><td>%s</td><td class='%s'><b>%.3f</b></td><td>%+.2f%%</td><td>%+.2f%%</td><td>%s</td><td>%s</td><td>%.2f%%</td></tr>" % (
                d.get("key"), d.get("tier", "?"), "g" if _gv >= 1.0 else "r", _gv, float(d.get("total_gain_pct") or 0), float(d.get("bh_key") or 0), d.get("trades", "?"), d.get("pool_sharpe", "?"), float(d.get("coverage_pct") or 0)))
        out.append("<h2>PER-SYMBOL IMPROVEMENTS since last digest [DIAGNOSTIC n_syms=1 each]</h2>" + _table(
            ["key", "tier", "gain_vs_bh old&rarr;new", "gain", "trades", "pool_sharpe(1sym)", "ceiling coverage"], rows_imp,
            "no per-key verdict changed this window (%d keys tracked)" % len(latest)))
        out.append("<h2>TOP-20 KEYS by gain_vs_bh (latest post-parity verdicts, %d keys total)</h2>" % len(latest) + _table(
            ["key", "tier", "gain_vs_bh", "gain", "b&amp;h", "trades", "pool_sharpe(1sym)", "coverage"], rows_all))
    except Exception as e:
        out.append("<p>per-symbol section failed: %s</p>" % e)
    try:
        _GATE_STATE_PATH.write_text(json.dumps(new_state))
    except Exception:
        pass
    return "".join(out)


PARITY_FILES = ["config.py", "config_tradier.py", "ez_manage.py", "tradier_manage.py",
                "ez_positions_quick.py", "wt_dc_delta.py", "backtest_v8_engine.py",
                "ez_indicators.py", "ez_reentry.py"]


def _parity_sections():
    import subprocess, hashlib
    base = Path(__file__).resolve().parent.parent
    rows = []
    try:
        local = {}
        for f in PARITY_FILES:
            try:
                local[f] = hashlib.md5((base / f).read_bytes()).hexdigest()
            except Exception:
                local[f] = "missing"
        out = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "s1-int",
            "cd /home/niels/binance-sandbox && md5sum " + " ".join(PARITY_FILES) + " 2>/dev/null"],
            capture_output=True, text=True, timeout=25)
        remote = {}
        for line in (out.stdout or "").splitlines():
            parts = line.split()
            if len(parts) == 2:
                remote[parts[1]] = parts[0]
        n_ok = 0
        for f in PARITY_FILES:
            ok = local.get(f) == remote.get(f) and local.get(f) != "missing"
            n_ok += 1 if ok else 0
            if not ok:
                rows.append("<tr><td>%s</td><td class='r'><b>OUT OF SYNC</b></td><td>%s</td><td>%s</td></tr>" % (
                    f, (local.get(f) or "?")[:10], (remote.get(f) or "ABSENT")[:10]))
        head = "<p class='%s'><b>%d/%d parity-critical files in sync (Mac live == S1 sandbox)</b>%s</p>" % (
            "g" if n_ok == len(PARITY_FILES) else "r", n_ok, len(PARITY_FILES),
            "" if n_ok == len(PARITY_FILES) else " — DRIFT DETECTED: backtests are testing different code than live trades!")
        sync_html = head + (_table(["file", "status", "mac md5", "s1 md5"], rows) if rows else "")
    except Exception as e:
        sync_html = "<p>sync check failed: %s</p>" % e
    act_rows = []
    try:
        import glob as _g
        from datetime import datetime as _dt
        cutoff = time.time() - 24 * 3600
        for acct in ("trb", "trc", "ang", "inf", "fin", "flz", "men"):
            counts = {}
            for f in _g.glob(str(base / ("data/history/%s/*.jsonl" % acct))):
                try:
                    for line in open(f):
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue
                        ts = r.get("ts")
                        try:
                            tsv = _dt.fromisoformat(ts).timestamp() if isinstance(ts, str) else float(ts or 0)
                        except Exception:
                            continue
                        if tsv < cutoff:
                            continue
                        t = str(r.get("type") or "?")
                        if "SYNC" in str(r.get("reason") or "") and t == "OPEN":
                            t = "SYNC_NOISE"
                        counts[t] = counts.get(t, 0) + 1
                except Exception:
                    continue
            act = ", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "no fills"
            act_rows.append("<tr><td>%s</td><td>%s</td></tr>" % (acct, act))
    except Exception as e:
        act_rows.append("<tr><td colspan=2>%s</td></tr>" % e)
    par_tail = ""
    try:
        pf = Path("/Users/niels/logs/parity_diff_nightly.log")
        # 2026-07-28: mtime alone was the only check, so a 36-byte log containing
        # "/bin/sh: timeout: command not found" was rendered under the parity
        # heading as if the diff had run and found nothing. Validate the content.
        if pf.exists() and (time.time() - pf.stat().st_mtime) < 2 * 86400:
            _body = pf.read_text()
            _lines = [ln for ln in _body.splitlines() if ln.strip()]
            _broken = (
                not _lines
                or len(_body) < 200
                or any(tok in _body for tok in (
                    "command not found", "No such file or directory",
                    "Traceback (most recent call last)", "Permission denied"))
            )
            if _broken:
                par_tail = ("<p class='r'><b>&#9888; PARITY DIFF DID NOT RUN.</b> "
                            "The nightly log contains a launch failure, not parity output "
                            "&mdash; live-vs-backtest drift is currently UNCHECKED.</p>"
                            "<pre style='font-size:11px'>%s</pre>"
                            % ("\n".join(_lines[-6:]) or "(empty log)").replace("<", "&lt;"))
            else:
                par_tail = "<pre style='font-size:11px'>%s</pre>" % ("\n".join(_lines[-12:])).replace("<", "&lt;")
        else:
            par_tail = "<p class='r'>no recent parity_diff run (nightly cron) &mdash; drift UNCHECKED</p>"
    except Exception:
        pass
    return ("<h2>LIVE vs SANDBOX SYNC (post-update drift guard)</h2>%s"
            "<h3>Live fills last 24h (per account, from /history/ — signal-price ledger)</h3>%s"
            "<h3>Latest live-vs-backtest trade parity diff (nightly)</h3>%s") % (
        sync_html, _table(["account", "fills by type (24h)"], act_rows), par_tail)


def _gainmo_sections():
    import subprocess
    base = Path(__file__).resolve().parent.parent
    rows = []
    try:
        book = json.loads((base / "data/persym_final_book.json").read_text())
        tr, dis = book.get("tradeable", {}), set(book.get("disabled", []))
        longs = set(json.loads((base / "symbols_trb_long.json").read_text()))
        shorts = set(json.loads((base / "symbols_trb_short.json").read_text()))
        for k in MANDATORY_8:
            sym, side = k.rsplit("_", 1)
            uni = sym in (longs if side == "LONG" else shorts)
            st = "TRADEABLE" if k in tr else ("DISABLED!" if k in dis else "book-absent")
            ok = uni and k in tr
            rows.append("<tr><td>%s</td><td class='%s'>%s</td><td class='%s'>%s</td></tr>" % (
                k, "g" if ok else "r", st, "g" if uni else "r", "in universe" if uni else "NOT in universe"))
    except Exception as e:
        rows.append("<tr><td colspan=3>mandatory-8 check failed: %s</td></tr>" % e)
    m8 = _table(["mandatory key (USER 2026-07-10)", "final book", "trb universe"], rows)
    q = []
    try:
        out = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "s1-int",
            "tail -4 /home/niels/logs/armq_20260709.log 2>/dev/null; "
            "echo -n 'baseline batches done: '; ls /home/niels/binance-sandbox/data/sweep_results/t2base_tradier_*/ 2>/dev/null | grep -c '.done$' ; "
            "test -f /home/niels/logs/STOCKS_GATE_MET && echo GATE_MET || echo GATE_NOT_MET; "
            "tail -6 /home/niels/binance-sandbox/data/_knob_audit/gainmo_followups.md 2>/dev/null"],
            capture_output=True, text=True, timeout=25)
        q.append("<pre style='font-size:11px'>%s</pre>" % (out.stdout or "no S1 data").replace("<", "&lt;"))
    except Exception as e:
        q.append("<p>S1 queue status unavailable: %s</p>" % e)
    bh = []
    try:
        import glob as _g
        from datetime import datetime as _dt
        cutoff = time.time() - 7 * 86400
        for acct in ("trb", "flz", "ang"):
            counts = {}
            for f in _g.glob(str(base / ("data/history/%s/*.jsonl" % acct))):
                try:
                    for line in open(f):
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue
                        ts = r.get("ts")
                        try:
                            tsv = _dt.fromisoformat(ts).timestamp() if isinstance(ts, str) else float(ts or 0)
                        except Exception:
                            continue
                        if tsv < cutoff:
                            continue
                        t = str(r.get("type") or "?")
                        counts[t] = counts.get(t, 0) + 1
                except Exception:
                    continue
            act = ", ".join("%s=%d" % kv for kv in sorted(counts.items())) or "NO ACTIVITY (7d)"
            bench = "n/a"
            try:
                if acct in ("flz", "ang"):
                    kl = json.loads((base / "klines_cache/BTCUSDC_1h.json").read_text())
                    closes = [float(b["close"]) for b in kl[-200:]]
                    if len(closes) > 168:
                        bench = "BTC %+.2f%%" % ((closes[-1] / closes[-169] - 1) * 100.0)
                else:
                    bench = "SPY klines not on Mac - check manually"
            except Exception:
                pass
            bh.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (acct, act, bench))
    except Exception as e:
        bh.append("<tr><td colspan=3>tracker failed: %s</td></tr>" % e)
    bh_html = _table(["account", "ledger activity 7d (fills by type)", "benchmark 7d"], bh)
    qual = _table(["mode", "key", "pool_sharpe (1sym)", "gain_vs_bh", "trades", "when"], _qualifying_rows(),
                  "NONE yet - the 8-winners bar is unmet")
    return ((_stocks_bh_capture_section() + _persym_latest_sections() + _parity_sections()).replace("%", "%%") + "<h2>QUALIFYING KEYS - EVERY key at the bar (pool_sharpe&gt;0.5 AND &gt;=2x b&amp;h, post-parity) [DIAGNOSTIC n_syms=1 each]</h2>%s"
            "<h2>GAINMO test queue + stocks gate (S1)</h2>%s"
            "<h2>Mandatory-8 trading status</h2>%s"
            "<h2>2x buy&amp;hold target (USER: trb + flz/ang by Monday)</h2>%s"
            "<p style='font-size:11px;color:#666'>True PnL is NOT in the ledgers (signal prices, no commissions) - "
            "verify 2x-b&amp;h against EXCHANGE INCOME (crypto) / Tradier account balance (stocks). Benchmark = single-symbol proxy. "
            "Target: account PnL &gt;= 2x benchmark.</p>") % (qual, q[0], m8, bh_html)


def build_digest(prev_state, now):
    win_start = _window_start(prev_state, now)
    reports = {"crypto": lib.build_report("crypto"), "stock": lib.build_report("stock")}
    liveness = lib.get_s1_liveness()
    prog = liveness.get("progress") or {}
    sections = {}
    coverage_lines = []
    top10_html = {}
    new_state = {"last_sent_ts": now, "last_sent_iso": datetime.now(timezone.utc).isoformat(),
                 "vec_screened_keys": {}, "real_engine_confirmed_keys": {}}
    stale_banner, stale_tracking = _stale_progress_check(prev_state, reports, now)
    new_state["stale_progress_tracking"] = stale_tracking
    all_gated_new, all_reenabled, all_rescued, all_winners_new = [], [], [], []
    for mode, rep in reports.items():
        new_state["vec_screened_keys"][mode] = rep["vec_screened_keys"]
        new_state["real_engine_confirmed_keys"][mode] = rep["real_engine_confirmed_keys"]
        prev_vec = (prev_state.get("vec_screened_keys") or {}).get(mode, rep["vec_screened_keys"])
        prev_conf = (prev_state.get("real_engine_confirmed_keys") or {}).get(mode, rep["real_engine_confirmed_keys"])
        gated_new = _rows_in_window(rep["gated"], win_start, now)
        reenabled_new = [v for v in rep["all_confirmed"] if v.get("source") == "gate" and v.get("action") == "REENABLED_POSITIVE" and win_start <= (v.get("ts") or 0) <= now]
        rescued_new = _rows_in_window(rep["rescued"], win_start, now)
        winners_new = _rows_in_window(rep["winners"], win_start, now)
        for v in gated_new: all_gated_new.append((mode, v))
        for v in reenabled_new: all_reenabled.append((mode, v))
        for v in rescued_new: all_rescued.append((mode, v))
        for v in winners_new: all_winners_new.append((mode, v))
        p = prog.get(mode, {})
        coverage_lines.append(
            "<li><b>%s</b>: %d/%d vec-screened keys real-engine-confirmed (%+d since last digest) &middot; "
            "keys baselined delta (vec-screen) %+d &middot; reopt queue %s/%s done (rescued=%s enabled=%s hopeless=%s)</li>" % (
                mode.upper(), rep["real_engine_confirmed_keys"], rep["vec_screened_keys"],
                rep["real_engine_confirmed_keys"] - prev_conf, rep["vec_screened_keys"] - prev_vec,
                p.get("done", "?"), p.get("queue_total", "?"), p.get("rescued", 0), p.get("enabled", 0), p.get("hopeless", 0)))
        top10 = rep["winners"][:10]
        top10_html[mode] = _table(
            ["key", "real_sharpe_1sym", "gain_vs_bh", "trades"],
            ["<tr><td>%s</td><td class='g'><b>%s</b></td><td>%s</td><td>%s</td></tr>" % (
                w["key"], _fmt_sharpe(w.get("real_sharpe")), _fmt_pct(w.get("gain_vs_bh")), w.get("trades", "&mdash;"))
             for w in top10], "no real-engine winners (&gt;0.5) yet")
    gated_rows = ["<tr><td>%s</td><td>%s</td><td class='r'>%s</td><td>%s</td></tr>" % (
        m.upper(), v["key"], _fmt_sharpe(v.get("real_sharpe")), (v.get("date") or "")[:19]) for m, v in all_gated_new]
    reenabled_rows = ["<tr><td>%s</td><td>%s</td><td class='g'>%s</td><td>%s</td></tr>" % (
        m.upper(), v["key"], _fmt_sharpe(v.get("real_sharpe")), (v.get("date") or "")[:19]) for m, v in all_reenabled]
    rescued_rows = ["<tr><td>%s</td><td>%s</td><td>%s &rarr; <b class='g'>%s</b></td><td>%s</td></tr>" % (
        m.upper(), v["key"], _fmt_sharpe(v.get("before_sharpe")), _fmt_sharpe(v.get("real_sharpe")), (v.get("date") or "")[:19])
        for m, v in all_rescued]
    winners_new_rows = ["<tr><td>%s</td><td>%s</td><td class='g'>%s</td><td>%s</td></tr>" % (
        m.upper(), v["key"], _fmt_sharpe(v.get("real_sharpe")), (v.get("date") or "")[:19]) for m, v in all_winners_new]
    win_start_iso = datetime.fromtimestamp(win_start, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    win_end_iso = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_confirmed = sum(r["real_engine_confirmed_keys"] for r in reports.values())
    total_screened = sum(r["vec_screened_keys"] for r in reports.values())
    cov_pct = (100.0 * total_confirmed / total_screened) if total_screened else 0.0
    html = """
    <html><head><style>%s
    table{border-collapse:collapse;width:100%%;margin:4px 0 12px 0}
    th{background:#1a1a2e;color:#fff;padding:5px 7px;text-align:left;font-size:11px}
    td{padding:4px 7px;border-bottom:1px solid #eee;font-size:12px}
    .g{color:#2e7d32}.r{color:#c62828}
    </style></head><body>
    <h1>Results digest &mdash; %s &rarr; %s</h1>
    <p>Window covers the last %.1fh since the previous digest (capped at 12h).
    Live dashboard: <a href="%s">%s</a></p>

    %s
    %s
    %s

    <h2>What changed (LEGACY reopt store — capture-era sections above are the live view)</h2>
    <h3>Newly gated off (real-engine confirmed negative &mdash; NOT trading live)</h3>
    %s
    <h3>Re-enabled (vec false-negative corrected by real engine)</h3>
    %s
    <h3>Rescued to real-engine &gt;0.5 (reopt search found a config fix)</h3>
    %s
    <h3>New winners this window (real-engine &gt;0.5)</h3>
    %s

    <h2>Progress / liveness</h2>
    <ul>%s</ul>
    <p>S1: %s &middot; %s</p>

    <h2>Top 10 winners &mdash; CRYPTO (LEGACY reopt store)</h2>
    %s
    <h2>Top 10 winners &mdash; STOCKS (LEGACY reopt store)</h2>
    %s

    <h2>Links</h2>
    <ul>
      <li><a href="%s">Live dashboard (/results)</a></li>
      <li><a href="http://localhost:5077/trb_review/crypto">/trb_review/crypto</a></li>
      <li><a href="http://localhost:5077/trb_review/trb">/trb_review/trb</a></li>
      %s
    </ul>

    <div class="box" style="font-size:11.5px;color:#666;line-height:1.5">
    <b>Coverage:</b> %d/%d keys (%.0f%%) are real-engine-confirmed across both systems; the rest
    are still vec-screen-only [DIAGNOSTIC]. Every real_sharpe_1sym figure above is a
    SINGLE-SYMBOL (n_syms=1) real-engine backtest &mdash; below the 48-crypto/100-stock sample
    floor, so per CLAUDE.md this is monitoring-only, never a promotion signal.
    </div>
    </body></html>""" % (
        me.CSS, win_start_iso, win_end_iso, (now - win_start) / 3600.0, DASHBOARD_URL, DASHBOARD_URL,
        _gainmo_sections(), stale_banner,
        _latest_matrix_filling_campaigns_section()
        + _switch_matrix_digest_section(),
        _table(["mode", "key", "real_sharpe_1sym", "gated"], gated_rows, "none this window"),
        _table(["mode", "key", "real_sharpe_1sym", "when"], reenabled_rows, "none this window"),
        _table(["mode", "key", "before &rarr; after", "when"], rescued_rows, "none this window"),
        _table(["mode", "key", "real_sharpe_1sym", "when"], winners_new_rows, "none this window"),
        "".join(coverage_lines),
        (liveness.get("cpu_line") or "S1 unreachable"), (liveness.get("mem_line") or ""),
        top10_html.get("crypto", ""), top10_html.get("stock", ""),
        DASHBOARD_URL,
        "".join("<li><a href='http://localhost:5077/spreadsheets/%s'>%s</a> (also attached to this email)</li>" % (f, f) for f in XLS_ATTACH) +
        "".join("<li><a href='http://localhost:5077/spreadsheets/%s'>%s</a></li>" % (f, f) for f in XLS_LINK_ONLY),
        total_confirmed, total_screened, cov_pct)
    subject = "%sResults digest %s UTC &mdash; crypto %d winners/%d gated | stocks %d winners/%d gated" % (
        "[STALE >12h] " if stale_banner else "",
        datetime.now(timezone.utc).strftime("%H:%M"),
        reports["crypto"]["winners_count"], reports["crypto"]["gated_off_keys"],
        reports["stock"]["winners_count"], reports["stock"]["gated_off_keys"])
    # strip stray HTML entity from subject (plain-text header)
    subject = subject.replace("&mdash;", "-")
    return html, subject, new_state


def send_with_attachments(html_body, subject, to=None, attach_paths=None):
    pwd = me.get_gmail_password()
    if not pwd:
        logger.error("No Gmail password available (Keychain + ~/.gmail_app_pw both failed)")
        return False
    recipients = to if isinstance(to, list) else [to or me.TO_EMAIL]
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = me.FROM_EMAIL
    msg["To"] = ", ".join(recipients)
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("Open in HTML email client. Dashboard: %s" % DASHBOARD_URL, "plain"))
    alt.attach(MIMEText(html_body, "html"))
    msg.attach(alt)
    for p in (attach_paths or []):
        try:
            data = Path(p).read_bytes()
        except Exception as e:
            logger.warning("Could not attach %s: %s", p, e)
            continue
        part = MIMEApplication(data, Name=Path(p).name)
        part["Content-Disposition"] = 'attachment; filename="%s"' % Path(p).name
        msg.attach(part)
    import smtplib
    last_err = None
    for mode, port in (("ssl", 465), ("starttls", 587)):
        try:
            if mode == "ssl":
                server = smtplib.SMTP_SSL("smtp.gmail.com", port, timeout=30)
            else:
                server = smtplib.SMTP("smtp.gmail.com", port, timeout=30)
                server.starttls()
            server.login(me.FROM_EMAIL, pwd)
            server.sendmail(me.FROM_EMAIL, recipients, msg.as_string())
            server.quit()
            logger.info("Digest sent to %s via %s:%s", ", ".join(recipients), mode, port)
            return True
        except Exception as e:
            last_err = e
            logger.warning("Send via %s:%s failed: %s", mode, port, e)
    logger.error("Send failed on all transports: %s", last_err)
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--html-only", action="store_true", help="Write HTML to disk but do not send / do not advance state")
    parser.add_argument("--force", action="store_true", help="Send even if run again soon after the last one")
    args = parser.parse_args()
    now = time.time()
    prev_state = _load_state()
    html, subject, new_state = build_digest(prev_state, now)
    out_path = BASE_PATH / "data" / "results_digest_latest.html"
    out_path.write_text(html)
    logger.info("Digest HTML written to %s", out_path)
    if args.html_only:
        logger.info("--html-only: not sending, not advancing state")
        return
    attach_paths = [str(BASE_PATH / "SPREADSHEETS" / f) for f in XLS_ATTACH]
    # 2026-07-14 USER: spreadsheets must be readable on Android — embed first rows as
    # HTML tables in the body and attach .csv twins (xlsx viewers on mobile are unreliable).
    try:
        import pandas as _pd
        previews = []
        for f in XLS_ATTACH:
            fp = BASE_PATH / "SPREADSHEETS" / f
            if not fp.exists():
                continue
            try:
                df = _pd.read_excel(fp, nrows=25)
                previews.append("<h3>%s (first %d rows)</h3>%s" % (f, min(25, len(df)), df.to_html(index=False, border=0, na_rep="")))
                csv_p = fp.with_suffix(".csv")
                _pd.read_excel(fp).to_csv(csv_p, index=False)
                attach_paths.append(str(csv_p))
            except Exception as _pe:
                previews.append("<p>%s preview failed: %s</p>" % (f, _pe))
        if previews:
            html = html.replace("</body></html>", "<h2>SPREADSHEET PREVIEWS (mobile-readable)</h2>" + "".join(previews) + "</body></html>")
    except Exception:
        pass
    ok = send_with_attachments(html, subject, to=me.TO_EMAIL, attach_paths=attach_paths)
    if ok:
        _save_state(new_state)
        logger.info("State advanced: %s", new_state)
    else:
        logger.error("Digest NOT sent — state left unchanged so next run re-covers this window")
        sys.exit(1)


if __name__ == "__main__":
    main()
