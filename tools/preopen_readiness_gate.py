#!/usr/bin/env python3
"""preopen_readiness_gate — the pre-open contract, measured. Reports only; never writes live state.

USER 2026-10-20: "the fleet scheduler has to run and always recalculate TEMPLATE_* and all defaults
before market open ... if mac is off it just syncs the new templates and defaults as soon as it comes
back on and before it starts trading old data".

WHAT WENT WRONG 2026-10-20 (the reason this file exists). The board side worked: 91 boards were filled,
86 of them before 13:30Z, and data/published_selection.json rebuilt at 12:52:16Z with n=474 of which 38
were published that day. The APPLY side silently did not: tools/v15_golive_refresh_published.sh wraps
tools/v15_persym_golive.py in `timeout 5400`, and every run that day hit it —

    00:50:22 -> 02:20:33   02:50:26 -> 04:20:35   04:50:20 -> 06:20:36
    06:50:21 -> 08:20:37   08:50:19 -> 10:20:33   10:50:19 -> 12:20:35   (all 5409-5416 s)

so v15_persym_golive was SIGKILLed before it wrote --out, no data/daily_chain/persym_golive_20261020_*
report existed, data/persym_size_tiers.json was still stamped 10-19 22:34, and the apply was left
partial: of the 38 boards published that day only 5 were fully live in per_sym_store, 5 diverged
materially (RS_LONG 64 switches, WPM_SHORT 71, APO_SHORT 31, ZCSH_LONG 16, SAP_LONG 8) and 2 had no live
row at all. Nothing anywhere alarmed, because the only completion signal anyone looked at was the
wrapper's own "=== done" line, which a timeout also prints.

THE CONTRACT, measured against the REAL live reader (per_sym_live_set.full_set -> per_sym_store SQL,
NOT data/per_sym_full_sets.json, which is the applier's own book and was 24/38 "fine" while live was
5/38):
  boards_recomputed          TEMPLATE_* clones were filled today before the open
  selection_fresh            published_selection.json was rebuilt within SELECTION_MAX_AGE_S of the open
  apply_report_written       a go-live report for today exists, written before the open, + fresh tiers
  live_set_matches_selection every published board resolves in live exactly as published
  indicators_fresh           timestamp_15m is inside the live gate's own 90-min window
  s1_sets_synced             S1 carries the same per_sym store it would copytrade with (optional)

A divergence is only MATERIAL when it is not one of the applier's own documented strippings: the
ARTEFACT_KEYS/ABLATION_* that tools/v15_persym_golive.sanitize removes, a key migrate() renamed or
dropped, or one of the 8 crypto sizing knobs that per_sym_live_set clamps DOWN (USER 2026-10-16 "sizing
caps beat the board"). Those are imported from the real modules, never re-listed here, so this gate can
never disagree with the code it audits.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
REPORT = ROOT / "data" / "reports" / "preopen_readiness.json"
SELECTION = ROOT / "data" / "published_selection.json"
TIERS = ROOT / "data" / "persym_size_tiers.json"
BOARD_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
CHAIN_DIR = ROOT / "data" / "daily_chain"
SNAPSHOT = ROOT / "data" / "latest_market_data.json"
SELECTION_MAX_AGE_S = float(os.environ.get("PREOPEN_SELECTION_MAX_AGE_S", "7200"))
IND_STALE_MIN = float(os.environ.get("PREOPEN_IND_STALE_MIN", "90"))
IND_STALE_MAX_PCT = float(os.environ.get("PREOPEN_IND_STALE_MAX_PCT", "10"))
APPLY_GRACE_S = float(os.environ.get("PREOPEN_APPLY_GRACE_S", "1800"))


def _now():
    return datetime.datetime.now(datetime.timezone.utc).timestamp()


def _iso(epoch):
    if not epoch:
        return None
    return datetime.datetime.fromtimestamp(float(epoch), datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _mtime(path):
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return None


def session_open(now=None):
    """Today's NYSE open epoch from the failover module itself (holidays included), or None."""
    try:
        import live_failover_common as common

        session = common.nyse_session(now)
        return session[0] if session else None
    except Exception:
        return None


def _benign_classifier():
    """(artefact_keys, renamer, sizing_caps) taken from the applier and the live reader themselves."""
    artefacts, renamer, caps = set(), None, {}
    try:
        import v15_persym_golive as golive

        artefacts = set(golive.ARTEFACT_KEYS)
        renamer = getattr(golive, "migrate", None)
    except Exception:
        pass
    try:
        import per_sym_live_set as live

        caps = dict(live.CRYPTO_MIN_SIZE_CAPS)
    except Exception:
        pass
    return artefacts, renamer, caps


def _is_crypto(sym_side):
    try:
        import per_sym_live_set as live

        return sym_side.rpartition("_")[0].endswith(live.CRYPTO_SUFFIXES)
    except Exception:
        return sym_side.rpartition("_")[0].endswith(("USDT", "USDC"))


def _float_or_none(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def classify_divergence(sym_side, switch, want, got, present, artefacts, renamed, caps):
    """'benign' when the applier or the live reader is documented to change this key, else 'material'."""
    if switch in artefacts or switch.startswith("ABLATION_"):
        return "benign_artefact"
    if switch in renamed:
        return "benign_migrated"
    if switch in caps and _is_crypto(sym_side):
        want_f, got_f = _float_or_none(want), _float_or_none(got)
        if present and want_f is not None and got_f is not None and got_f <= want_f:
            return "benign_sizing_cap"
    return "material"


def check_boards(open_epoch, now):
    """TEMPLATE_* clones recalculated today and before the open."""
    day_start = open_epoch - 13.5 * 3600 if open_epoch else now - 86400
    total, before_open = 0, 0
    for path in BOARD_DIR.glob("*_30d_matrix.xlsx"):
        mtime = _mtime(path)
        if mtime is None or mtime < day_start:
            continue
        total += 1
        if open_epoch and mtime <= open_epoch:
            before_open += 1
    ok = before_open > 0
    return {
        "name": "boards_recomputed",
        "ok": ok,
        "critical": True,
        "measured": {"filled_today": total, "filled_before_open": before_open},
        "detail": f"{before_open} boards filled before the open ({total} today)" if ok else "no board was filled before the open",
    }


def check_selection(open_epoch, now):
    """published_selection.json rebuilt close enough to the open to carry the morning's boards."""
    try:
        meta = json.loads(SELECTION.read_text()).get("_meta") or {}
    except Exception as exc:
        return {"name": "selection_fresh", "ok": False, "critical": True, "measured": {}, "detail": f"unreadable: {exc}"}
    built = _epoch(meta.get("built_at"))
    ref = open_epoch or now
    age = None if built is None else ref - built
    ok = bool(meta.get("n")) and age is not None and age <= SELECTION_MAX_AGE_S
    return {
        "name": "selection_fresh",
        "ok": ok,
        "critical": True,
        "measured": {"built_at": meta.get("built_at"), "n": meta.get("n"), "age_at_open_s": None if age is None else round(age)},
        "detail": f"built {meta.get('built_at')} n={meta.get('n')}" + ("" if ok else f" — {round(age or -1)}s before the open > {round(SELECTION_MAX_AGE_S)}s"),
    }


def check_apply_report(open_epoch, now):
    """The apply COMPLETED: it wrote its --out report, and the size tiers were rebuilt from it.

    This is the check that 2026-10-20 had nowhere: six consecutive `timeout 5400` kills left the live set
    two-thirds applied and the only visible signal was the wrapper's own "=== done"."""
    day = datetime.datetime.fromtimestamp(open_epoch or now, datetime.timezone.utc).strftime("%Y%m%d")
    reports = sorted(CHAIN_DIR.glob(f"persym_golive_{day}*.json"), key=lambda p: _mtime(p) or 0)
    newest = reports[-1] if reports else None
    newest_mtime = _mtime(newest) if newest else None
    deadline = (open_epoch + APPLY_GRACE_S) if open_epoch else now
    tiers_mtime = _mtime(TIERS)
    day_start = (open_epoch - 13.5 * 3600) if open_epoch else now - 86400
    tiers_today = tiers_mtime is not None and tiers_mtime >= day_start
    ok = newest_mtime is not None and newest_mtime <= deadline and tiers_today
    return {
        "name": "apply_report_written",
        "ok": ok,
        "critical": True,
        "measured": {
            "reports_today": len(reports),
            "newest_report": newest.name if newest else None,
            "newest_report_at": _iso(newest_mtime),
            "tiers_at": _iso(tiers_mtime),
        },
        "detail": f"{newest.name if newest else 'NO REPORT'} at {_iso(newest_mtime)}, tiers {_iso(tiers_mtime)}"
        + ("" if ok else " — the apply never finished (no report / stale tiers): the live set is PARTIAL"),
    }


def scan_selection(selection_path=SELECTION, limit=None):
    """(items, exact, benign_only, offenders, missing_rows) — the published-vs-live scan, shared with
    tools/golive_delta_selection.py so the delta applier and this gate can never disagree on what is live."""
    import per_sym_live_set as live

    selection = json.loads(Path(selection_path).read_text())["sym_sides"]
    artefacts, renamer, caps = _benign_classifier()
    offenders, benign_only, exact, missing_rows = [], 0, 0, []
    items = sorted(selection.items(), key=lambda kv: kv[1].get("published_at") or "")
    for sym_side, entry in items[: limit or len(items)]:
        overrides = entry.get("overrides") or {}
        if not overrides:
            continue
        renamed = set()
        if renamer is not None:
            try:
                _, ren, dropped = renamer(dict(overrides))
                renamed = set(ren or {}) | set(dropped or [])
            except Exception:
                renamed = set()
        try:
            row = live.full_set(sym_side)
        except Exception:
            row = None
        if not row:
            missing_rows.append(sym_side)
            continue
        material = []
        for switch, want in overrides.items():
            present = switch in row
            got = row.get(switch)
            if present and got == want:
                continue
            if classify_divergence(sym_side, switch, want, got, present, artefacts, renamed, caps) == "material":
                material.append(switch)
        if material:
            offenders.append({"sym_side": sym_side, "published_at": entry.get("published_at"), "material": len(material), "sample": sorted(material)[:6]})
        elif all(row.get(s) == v for s, v in overrides.items()):
            exact += 1
        else:
            benign_only += 1
    offenders.sort(key=lambda o: -o["material"])
    return items, exact, benign_only, offenders, missing_rows


def check_live_matches_selection(open_epoch, now, limit=None):
    """Every published board resolves in the live store exactly as published, modulo documented strippings."""
    try:
        items, exact, benign_only, offenders, missing_rows = scan_selection(limit=limit)
    except Exception as exc:
        return {"name": "live_set_matches_selection", "ok": False, "critical": True, "measured": {}, "detail": f"unreadable: {exc}"}
    ok = not offenders and not missing_rows
    return {
        "name": "live_set_matches_selection",
        "ok": ok,
        "critical": True,
        "measured": {
            "selection_n": len(items),
            "exact": exact,
            "benign_divergence_only": benign_only,
            "material_divergence": len(offenders),
            "no_live_row": missing_rows[:20],
            "worst": offenders[:10],
        },
        "detail": f"{exact} exact, {benign_only} benign-only, {len(offenders)} with material divergence, {len(missing_rows)} with no live row",
    }


def check_indicators(now):
    """timestamp_15m age against the SAME 90-min window ez_positions_quick:3498 blocks entries on.

    S1 held crypto+stocks across the 2026-10-20 open with every entry refused as
    `BLOCKED - STALE_INDICATORS (259m old, ts=2026-10-20T09:30:00)` while its snapshot FILE was minutes
    old, so a file-mtime check could not see it. The age that matters is inside the payload."""
    try:
        snapshot = json.loads(SNAPSHOT.read_text())
    except Exception as exc:
        return {"name": "indicators_fresh", "ok": False, "critical": True, "measured": {}, "detail": f"unreadable: {exc}"}
    ages, stale = [], []
    for symbol, payload in snapshot.items():
        if symbol.startswith("_") or not isinstance(payload, dict):
            continue
        bar = _epoch(payload.get("timestamp_15m") or payload.get("timestamp"))
        if bar is None:
            continue
        tick = _float_or_none(payload.get("_tick_ts")) or 0.0
        if tick > 0 and now - tick < 120.0:
            ages.append(0.0)
            continue
        age_min = (now - bar) / 60.0
        ages.append(age_min)
        if age_min > IND_STALE_MIN:
            stale.append(symbol)
    pct = 100.0 * len(stale) / len(ages) if ages else 100.0
    ok = bool(ages) and pct <= IND_STALE_MAX_PCT
    ages_sorted = sorted(ages)
    median = ages_sorted[len(ages_sorted) // 2] if ages_sorted else None
    return {
        "name": "indicators_fresh",
        "ok": ok,
        "critical": True,
        "measured": {
            "symbols": len(ages),
            "stale_over_%dmin" % int(IND_STALE_MIN): len(stale),
            "stale_pct": round(pct, 1),
            "median_age_min": None if median is None else round(median, 1),
            "sample": sorted(stale)[:8],
        },
        "detail": f"{len(stale)}/{len(ages)} symbols past the live {int(IND_STALE_MIN)}-min entry gate ({pct:.1f}%)",
    }


def check_s1_synced(host):
    """S1 carries the same per_sym store it would copytrade with (md5 of the store file)."""
    if not host:
        return None
    local = ROOT / "data" / "per_sym_overrides.sqlite3"
    candidates = [local, ROOT / "data" / "per_sym_store.sqlite3", ROOT / "data" / "per_sym_full_sets.json"]
    local = next((p for p in candidates if p.exists()), None)
    if local is None:
        return {"name": "s1_sets_synced", "ok": False, "critical": False, "measured": {}, "detail": "no local per_sym store found"}
    rel = local.relative_to(ROOT)
    try:
        mine = subprocess.run(["md5", "-q", str(local)], capture_output=True, text=True, timeout=120).stdout.strip()
    except Exception as exc:
        return {"name": "s1_sets_synced", "ok": False, "critical": False, "measured": {}, "detail": f"local md5 failed: {exc}"}
    cmd = ["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ControlPath=none", host, f"md5sum ~/binance/{rel} 2>/dev/null | cut -d' ' -f1"]
    try:
        theirs = subprocess.run(cmd, capture_output=True, text=True, timeout=120).stdout.strip()
    except Exception as exc:
        return {"name": "s1_sets_synced", "ok": False, "critical": False, "measured": {}, "detail": f"{host} unreachable: {exc}"}
    ok = bool(theirs) and theirs == mine
    return {
        "name": "s1_sets_synced",
        "ok": ok,
        "critical": False,
        "measured": {"file": str(rel), "mac_md5": mine[:12], "s1_md5": (theirs or "")[:12]},
        "detail": f"{rel} {'identical' if ok else 'DIFFERS'} on {host}",
    }


def run(host=None, limit=None, as_of=None):
    now = as_of or _now()
    open_epoch = session_open(now)
    checks = [
        check_boards(open_epoch, now),
        check_selection(open_epoch, now),
        check_apply_report(open_epoch, now),
        check_live_matches_selection(open_epoch, now, limit=limit),
        check_indicators(now),
    ]
    synced = check_s1_synced(host)
    if synced is not None:
        checks.append(synced)
    critical_failed = [c["name"] for c in checks if c["critical"] and not c["ok"]]
    payload = {
        "generated_utc": _iso(now),
        "host": os.uname().nodename,
        "session_open_utc": _iso(open_epoch),
        "market_open": bool(open_epoch) and now >= open_epoch,
        "verdict": "PASS" if not critical_failed else "FAIL",
        "failed_critical": critical_failed,
        "checks": checks,
    }
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.environ.get("PREOPEN_S1_HOST", ""), help="peer to compare the per_sym store with (e.g. s1-pub); empty skips")
    parser.add_argument("--limit", type=int, default=None, help="only the N oldest-published selection keys (debug)")
    parser.add_argument("--out", default=str(REPORT))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    payload = run(host=args.host or None, limit=args.limit)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str))
    tmp.replace(out)
    if not args.quiet:
        print(f"[preopen] {payload['verdict']} open={payload['session_open_utc']} host={payload['host']}")
        for check in payload["checks"]:
            print(f"  {'OK  ' if check['ok'] else 'FAIL'} {check['name']:28s} {check['detail']}")
    return 0 if payload["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
