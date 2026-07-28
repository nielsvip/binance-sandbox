#!/usr/bin/env python3
"""Paper (shadow) options email report — morning + afternoon.

Reactivated 2026-06-04 (the shadow paper-options system had been disabled since
2026-04-26). Reports the hypothetical option trades the paper system WOULD have
placed, plus a running hypothetical P&L. NO-LIES: every number here is read from
the real shadow decision/score files — when data is missing it says so rather
than inventing a figure. Run: `python options_paper_email.py [--session morning|afternoon]`.

Sends via morning_email's existing Gmail plumbing (send_email/get_gmail_password).
"""
import argparse
import glob
import json
import os
from datetime import datetime, timezone, timedelta

from morning_email import send_email, TO_EMAIL  # reuse SMTP + recipients

BASE = os.path.dirname(os.path.abspath(__file__))
SHADOW_DIR = os.path.join(BASE, "data", "options_shadow")
LOOKBACK_DAYS = 30


def _utc_now():
    return datetime.now(timezone.utc)


def _load_recent_decision_files():
    """Return list of (variant, path, mtime) for decisions_*.jsonl within LOOKBACK_DAYS."""
    out = []
    cutoff = _utc_now() - timedelta(days=LOOKBACK_DAYS)
    for path in glob.glob(os.path.join(SHADOW_DIR, "*", "decisions_*.jsonl")):
        try:
            mt = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        except OSError:
            continue
        if mt >= cutoff:
            variant = os.path.basename(os.path.dirname(path))
            out.append((variant, path, mt))
    return sorted(out, key=lambda x: x[2], reverse=True)


def _latest_cycle_with_trades(path):
    """Return the most recent JSONL cycle that has opportunities/decisions, else last cycle."""
    last = None
    last_with_trades = None
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                last = rec
                if rec.get("opportunities") or rec.get("decisions"):
                    last_with_trades = rec
    except OSError:
        return None
    return last_with_trades or last


def _collect_proposals():
    """Across variants, gather the latest proposed paper trades + total premium + window."""
    files = _load_recent_decision_files()
    proposals = []
    total_premium = 0.0
    newest_ts = None
    oldest_ts = None
    seen_variants = set()
    for variant, path, mt in files:
        if variant in seen_variants:
            continue
        seen_variants.add(variant)
        rec = _latest_cycle_with_trades(path)
        if not rec:
            continue
        ts = rec.get("ts")
        if ts:
            newest_ts = ts if newest_ts is None or ts > newest_ts else newest_ts
            oldest_ts = ts if oldest_ts is None or ts < oldest_ts else oldest_ts
        summ = rec.get("summary") or {}
        prem = float(summ.get("total_proposed_premium", 0) or 0)
        total_premium += prem
        for opp in (rec.get("opportunities") or rec.get("decisions") or []):
            proposals.append({
                "variant": variant,
                "symbol": opp.get("symbol") or opp.get("underlying") or "?",
                "type": opp.get("type") or opp.get("option_type") or opp.get("side") or "?",
                "strike": opp.get("strike", ""),
                "expiry": opp.get("expiry") or opp.get("expiration") or "",
                "premium": opp.get("premium") or opp.get("cost") or opp.get("budget") or "",
                "delta": opp.get("delta", ""),
            })
    return proposals, total_premium, newest_ts, oldest_ts, sorted(seen_variants)


def _earliest_shadow_data_date():
    """Oldest decisions file mtime — to be honest about the real data window."""
    paths = glob.glob(os.path.join(SHADOW_DIR, "*", "decisions_*.jsonl"))
    if not paths:
        return None
    return min(datetime.fromtimestamp(os.path.getmtime(p), tz=timezone.utc) for p in paths)


def build_html(session):
    proposals, total_premium, newest_ts, oldest_ts, variants = _collect_proposals()
    now_et = _utc_now() - timedelta(hours=4)
    title = f"Paper Options — {session.title()} Report — {now_et.strftime('%a %b %d %H:%M ET')}"
    earliest = _earliest_shadow_data_date()
    rows = ""
    for p in proposals[:60]:
        rows += (f"<tr><td>{p['variant']}</td><td>{p['symbol']}</td><td>{p['type']}</td>"
                 f"<td>{p['strike']}</td><td>{p['expiry']}</td><td>{p['premium']}</td><td>{p['delta']}</td></tr>")
    # 2026-07-28: distinguish "producer is dead" from "gates filtered everything".
    # LOOKBACK_DAYS excluded every file on disk (newest 52 days old), so the empty
    # result was being explained as a benign market condition. Measure the real
    # newest decision file, unfiltered, and say which case this is.
    _all_decisions = glob.glob(os.path.join(SHADOW_DIR, "*", "decisions_*.jsonl"))
    _newest_mtime = max((os.path.getmtime(p) for p in _all_decisions), default=None)
    _stale_days = ((_utc_now().timestamp() - _newest_mtime) / 86400.0) if _newest_mtime else None
    _producer_dead = _stale_days is not None and _stale_days > 1.5
    if not rows:
        if _producer_dead:
            rows = ("<tr><td colspan='7'><b>PRODUCER DEAD</b> &mdash; no shadow decision file has been "
                    f"written for {_stale_days:.1f} days (newest: "
                    f"{datetime.fromtimestamp(_newest_mtime, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC). "
                    "This is not a market condition: tradier_options_shadow_runner is not running.</td></tr>")
        elif _newest_mtime is None:
            rows = ("<tr><td colspan='7'><b>NO SHADOW DATA AT ALL</b> &mdash; "
                    f"{SHADOW_DIR} contains no decisions files.</td></tr>")
        else:
            rows = ("<tr><td colspan='7'>No hypothetical trades proposed in the most recent "
                    "market cycle (market may be closed, or gates filtered everything).</td></tr>")
    data_window = "no shadow data found"
    if newest_ts:
        data_window = f"latest cycle {newest_ts}"
    if _producer_dead:
        pnl_note = (
            "<b style='color:#c62828'>&#9888; STALE REPORT &mdash; the paper-options producer is not "
            f"running.</b> The newest shadow decision on disk is <b>{_stale_days:.1f} days old</b>. "
            "Every number below is historical or zero; nothing here describes what the paper system "
            "would do today. There is no realized-P&amp;L tracker, so no trailing-30-day figure exists "
            "either. To revive it, start <code>tradier_options_shadow_runner.py</code> "
            "(its launchd plist is currently .disabled)."
        )
    else:
        pnl_note = (
            "<b>Hypothetical 30-day P&amp;L:</b> the paper system records proposals but has no "
            "realized-P&amp;L tracker, so a truthful trailing-30-day realized figure does not exist. "
            "This report shows the trades the paper system WOULD place right now and the premium it "
            "WOULD deploy &mdash; real, not invented."
        )
    if earliest:
        pnl_note += f"<br><small>Oldest shadow data on disk: {earliest.strftime('%Y-%m-%d')}. {data_window}.</small>"
    html = f"""<html><body style="font-family:Arial,sans-serif">
    <h2>{title}</h2>
    <p style="background:#fff3cd;padding:10px;border-left:4px solid #ffc107">{pnl_note}</p>
    <h3>Hypothetical trades the paper system would place ({len(proposals)} proposals · variants: {', '.join(variants) or 'none'})</h3>
    <p><b>Total proposed premium (capital it would deploy): ${total_premium:,.0f}</b></p>
    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse">
      <tr style="background:#eee"><th>Variant</th><th>Symbol</th><th>Type</th><th>Strike</th><th>Expiry</th><th>Premium</th><th>Delta</th></tr>
      {rows}
    </table>
    <p style="color:#888;font-size:12px">Generated {_utc_now().isoformat()} by options_paper_email.py. Paper/shadow only — no real orders.</p>
    </body></html>"""
    return title, html


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="morning", choices=["morning", "afternoon"])
    ap.add_argument("--dry-run", action="store_true", help="print HTML, do not send")
    args = ap.parse_args()
    subject, html = build_html(args.session)
    if args.dry_run:
        print(subject)
        print(html)
        return
    ok = send_email(html, to=TO_EMAIL, subject=subject)
    print("SENT" if ok else "SEND_FAILED")


if __name__ == "__main__":
    main()
