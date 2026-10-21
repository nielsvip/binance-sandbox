#!/usr/bin/env python3
"""Zombie exterminator — USER 2026-10-21: stale vec intents must never jeopardize trading again.

Background: data/vec_live/intents/ accumulated 10,726 files (Oct 6-21). The
consumer (ez_manage vec-driven lane) reads them ALL every cycle, and the
2026-10-21 zero-pass late-dictate bypass admits days-old bars as live orders —
~1,300 refusal lines and 0 fills on 2026-10-21 before the manual archive of
7,905 zombies. This tool makes that archive recurring: any spool file whose
newest inner bar_ts is older than --max-age-s (default 24h — far beyond the
300s live horizon and the ~15min shadow settle, far short of forensics needs,
which the archive preserves) moves to data/vec_live/intents_archive/ with an
appended manifest entry.

Safety: archive preserves every byte (move, never delete; sha16-recorded).
Unparseable files are left in place and reported (load_intents skips them
anyway). Dry-run by default; --apply moves. Single-flight via
tools/v15_lock_run.sh in cron, like the other watchers.
"""

import argparse
import glob
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(
    os.environ.get("VEC_EXTERMINATOR_ROOT") or Path(__file__).resolve().parents[1]
)
SPOOL = ROOT / "data" / "vec_live" / "intents"
ARCHIVE = ROOT / "data" / "vec_live" / "intents_archive"
DEFAULT_MAX_AGE_S = 24 * 3600


def records_of(path):
    try:
        raw = json.load(open(path))
    except Exception:
        return None
    if isinstance(raw, dict) and isinstance(raw.get("intents"), list):
        recs = raw["intents"]
    elif isinstance(raw, list):
        recs = raw
    else:
        recs = [raw]
    return recs


def max_bar(recs):
    bars = []
    for r in recs:
        if not isinstance(r, dict):
            continue
        try:
            bars.append(float(r.get("bar_ts") or 0))
        except Exception:
            pass
    return max(bars) if bars else None


def classify(spool=SPOOL, max_age_s=DEFAULT_MAX_AGE_S, now=None):
    now = float(now if now is not None else time.time())
    stale, fresh, odd = [], [], []
    for f in sorted(glob.glob(str(spool / "*.json"))):
        recs = records_of(f)
        if recs is None:
            odd.append(f)
            continue
        newest = max_bar(recs)
        if newest is None or newest <= 0:
            odd.append(f)
        elif now - newest > max_age_s:
            stale.append((f, newest))
        else:
            fresh.append(f)
    return stale, fresh, odd


def exterminate(max_age_s=DEFAULT_MAX_AGE_S, apply=False, spool=SPOOL, archive=ARCHIVE):
    stale, fresh, odd = classify(spool=spool, max_age_s=max_age_s)
    result = {
        "max_age_s": max_age_s,
        "stale": len(stale),
        "fresh": len(fresh),
        "unparseable": len(odd),
        "moved": [],
        "applied": bool(apply),
    }
    if not apply:
        result["would_move"] = [os.path.basename(f) for f, _ in stale[:20]]
        result["would_move_truncated"] = len(stale) > 20
        return result
    archive = Path(archive)
    archive.mkdir(parents=True, exist_ok=True)
    manifest_path = archive / "MANIFEST.jsonl"
    moved = []
    with open(manifest_path, "a") as mf:
        for f, newest in stale:
            try:
                with open(f, "rb") as fh:
                    sha = hashlib.sha256(fh.read()).hexdigest()[:16]
                dest = archive / os.path.basename(f)
                if dest.exists():
                    dest = archive / (os.path.basename(f) + ".dup")
                shutil.move(f, dest)
                mf.write(
                    json.dumps(
                        {
                            "archived_utc": time.strftime(
                                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                            ),
                            "file": os.path.basename(f),
                            "max_bar_ts": newest,
                            "sha16": sha,
                        }
                    )
                    + "\n"
                )
                moved.append(os.path.basename(f))
            except Exception as e:
                result.setdefault("errors", []).append(f"{os.path.basename(f)}: {e}")
    result["moved"] = moved
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description="Archive stale vec intent spool files.")
    ap.add_argument("--max-age-s", type=float, default=DEFAULT_MAX_AGE_S)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    res = exterminate(max_age_s=args.max_age_s, apply=args.apply)
    tag = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"[exterminator:{tag}] stale={res['stale']} fresh={res['fresh']} unparseable={res['unparseable']} moved={len(res['moved'])}"
    )
    if res.get("errors"):
        for e in res["errors"][:10]:
            print(f"[exterminator] ERROR {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
