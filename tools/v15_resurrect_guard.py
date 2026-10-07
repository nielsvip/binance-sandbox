#!/usr/bin/env python3
"""v15_resurrect_guard — old bytes must never wear a new date.

A result file whose content predates its filesystem mtime by more than
--max-gap-hours is a resurrected false positive (proven 2026-10-04: Oct-2
HYPEUSDT_SHORT sheet re-placed on the Mac with a fresh mtime, masquerading
as the latest calculation). Such files are moved out of the live result
dirs into V15_QUARANTINE (bytes preserved, never deleted) and logged.

Signals (content-derived, not trust-the-clock):
- xlsx: internal docProps/core.xml dcterms:modified vs fs mtime.
- progress JSON: md5 registry; same bytes + jumped mtime = MTOUCH (log-only).

Mac mirrors only. Never runs against server live dirs.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_DIRS = [ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL",
             ROOT / "SPREADSHEEDS" / "V15_V16_CELL_BY_CELL_FINAL"]
QUARANTINE_DIR = ROOT / "SPREADSHEETS" / "V15_QUARANTINE"
JSON_DIRS = [ROOT / "data" / "reports" / "lifecycle_pilot"]
REGISTRY = ROOT / "data" / "reports" / "resurrect_guard_seen.json"

MODIFIED_RE = re.compile(rb"<dcterms:modified[^>]*>([^<]+)<")
SKIP_SUFFIXES = (".tmp.xlsx", ".bak", ".superseded")


def internal_modified_xlsx(path: Path):
    """Content timestamp of an xlsx (seconds since epoch) or None if unreadable."""
    try:
        with zipfile.ZipFile(path) as z:
            try:
                raw = z.read("docProps/core.xml")
            except KeyError:
                return None
    except Exception:
        return None
    m = MODIFIED_RE.search(raw or b"")
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1).decode().replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def md5_of(path: Path, limit_mb=64):
    h = hashlib.md5()
    n = 0
    with open(path, "rb") as f:
        while True:
            b = f.read(65536)
            if not b:
                break
            h.update(b)
            n += len(b)
            if n > limit_mb * 1048576:
                break
    return h.hexdigest()


def utc_ts():
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def scan_xlsx(dirs, max_gap_hours, quarantine_dir, audit, logf):
    stats = {"checked": 0, "quarantined": 0, "skipped": 0, "no_internal_ts": 0}
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.xlsx")):
            name = p.name
            if name.startswith(".") or ".tmp." in name or name.endswith(SKIP_SUFFIXES) or ".stale_" in name:
                stats["skipped"] += 1
                continue
            try:
                fs_mtime = p.stat().st_mtime
                size = p.stat().st_size
            except OSError:
                stats["skipped"] += 1
                continue
            internal = internal_modified_xlsx(p)
            stats["checked"] += 1
            if internal is None:
                stats["no_internal_ts"] += 1
                continue
            gap_h = (fs_mtime - internal) / 3600.0
            if gap_h <= max_gap_hours:
                if gap_h < -1:
                    logf({"ts": utc_ts(), "action": "ANOMALY_INTERNAL_NEWER", "file": str(p),
                          "fs_mtime": fs_mtime, "internal_modified": internal, "gap_h": round(gap_h, 2)})
                continue
            rec = {"ts": utc_ts(), "action": "QUARANTINED" if not audit else "WOULD_QUARANTINE",
                   "file": name, "dir": str(d), "fs_mtime": fs_mtime,
                   "internal_modified": internal, "gap_h": round(gap_h, 2),
                   "size": size, "md5": md5_of(p)}
            if audit:
                logf(rec)
                stats["quarantined"] += 1
                continue
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            dest = quarantine_dir / f"{name}.RESURRECTED_{utc_ts()}.xlsx"
            try:
                shutil.move(str(p), str(dest))
            except Exception as e:
                rec["action"] = "QUARANTINE_FAILED"
                rec["error"] = str(e)[:120]
                logf(rec)
                continue
            rec["dest"] = str(dest)
            prior = len(list(quarantine_dir.glob(f"{name}.RESURRECTED_*")))
            if prior >= 3:
                rec["alert"] = f"REPEAT_OFFENDER x{prior} — same file re-faked, find the writer"
            logf(rec)
            stats["quarantined"] += 1
    return stats


def scan_json_touch(json_dirs, registry_path, logf):
    try:
        reg = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    except Exception:
        reg = {}
    stats = {"checked": 0, "mtouch": 0}
    for d in json_dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            try:
                st = p.stat()
            except OSError:
                continue
            stats["checked"] += 1
            key = f"{d.name}/{p.name}"
            prev = reg.get(key)
            if prev and prev.get("size") == st.st_size and abs(prev.get("mtime", 0) - st.st_mtime) < 1:
                continue
            cur_md5 = md5_of(p)
            if prev and prev.get("md5") == cur_md5 and st.st_mtime - prev.get("mtime", 0) > 3600:
                stats["mtouch"] += 1
                logf({"ts": utc_ts(), "action": "MTOUCH", "file": key,
                      "old_mtime": prev.get("mtime"), "new_mtime": st.st_mtime,
                      "note": "same bytes, clock jumped >1h — touch/copy without -p"})
            reg[key] = {"md5": cur_md5, "size": st.st_size, "mtime": st.st_mtime}
    try:
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text(json.dumps(reg))
    except Exception:
        pass
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dirs", action="append", default=None)
    ap.add_argument("--quarantine-dir", default=str(QUARANTINE_DIR))
    ap.add_argument("--max-gap-hours", type=float, default=24.0)
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--json-dirs", action="append", default=None)
    ap.add_argument("--registry", default=str(REGISTRY))
    ap.add_argument("--skip-json", action="store_true")
    a = ap.parse_args(argv)
    dirs = [Path(x) for x in a.dirs] if a.dirs else [d for d in LIVE_DIRS if d.is_dir()]
    qdir = Path(a.quarantine_dir)
    events = []

    def logf(rec):
        events.append(rec)
        print(json.dumps(rec), flush=True)

    xs = scan_xlsx(dirs, a.max_gap_hours, qdir, a.audit, logf)
    js = {"checked": 0, "mtouch": 0}
    if not a.skip_json:
        jdirs = [Path(x) for x in a.json_dirs] if a.json_dirs else [d for d in JSON_DIRS if d.is_dir()]
        js = scan_json_touch(jdirs, Path(a.registry), logf)
    summary = {"ts": utc_ts(), "mode": "AUDIT" if a.audit else "ENFORCE",
               "xlsx": xs, "json": js, "quarantine_dir": str(qdir)}
    print(json.dumps(summary), flush=True)
    try:
        qdir.mkdir(parents=True, exist_ok=True)
        (qdir / "last_run.json").write_text(json.dumps(summary, indent=1))
        with open(qdir / "quarantine.log", "a") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
    except Exception as e:
        print(json.dumps({"ts": utc_ts(), "action": "STATUS_WRITE_FAILED", "error": str(e)[:120]}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
