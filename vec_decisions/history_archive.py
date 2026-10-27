"""history_archive — trade history is never deleted; a full live file rolls over into timestamped archive segments.

USER 2026-10-27 (verbatim): "Once a history file passes 200 KB, the oldest history is deleted <- this makes no sense it
should at least keep ~10mb then offload to ext drvie when availble but we need full history stored somewhere pls make
sure thiswill happen."

Layout (one ledger = <DATA_DIR>/history/<acct>/<SYM_SIDE>.jsonl, crypto and data/tradier/history alike):
  live      <acct>/<SYM_SIDE>.jsonl                                  appended by the positions writers, rolls at 10 MB
  archive   <acct>/archive/<SYM_SIDE>.<YYYYmmddTHHMMSSffffffZ>.jsonl rolled-over segment (rename only, same filesystem)
            <acct>/archive/<SYM_SIDE>.<stamp>.jsonl.zst               the same segment after tools/history_archive_offload.py
                                                                       compressed it (only once older than 8 days)
  legacy    <acct>/<SYM_SIDE>.jsonl.bak, <SYM_SIDE>.jsonl.<ts>.bak    pre-2026-10-27 rotations; folded into archive/
  offsite   <ext>/binance_archive/history/<host>/<rel>  + gs://v15-cold-329002141504/history/<host>/<rel>
            (tools/history_archive_offload.py; a local segment is removed only after TWO verified off-Mac copies)

rotate_if_needed() never unlinks. If anything fails it leaves the live file in place (it keeps growing; no byte lost).
iter_rows() is the reader for tools that need the FULL history: legacy .bak + archive segments + live, oldest first.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

LIVE_MAX_BYTES = 10 * 1024 * 1024
ARCHIVE_DIRNAME = "archive"
SEGMENT_RE = re.compile(r"^(?P<ss>.+?)\.(?P<stamp>\d{8}T\d{6}\d*Z)(?:\.(?P<tag>[A-Za-z0-9_-]+))?\.jsonl(?P<zst>\.zst)?$")
LEGACY_RE = re.compile(r"^(?P<ss>.+?)\.jsonl(?:\.(?P<ts>[^/]+))?\.bak$")
MANIFEST = "offload_manifest.jsonl"


def live_max_bytes() -> int:
    try:
        v = int(os.environ.get("HISTORY_LIVE_MAX_BYTES", "") or LIVE_MAX_BYTES)
        return max(v, 1024 * 1024)
    except ValueError:
        return LIVE_MAX_BYTES


def archive_dir(live_path) -> Path:
    return Path(live_path).parent / ARCHIVE_DIRNAME


def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%S%fZ")


def segment_path(live_path, now: datetime | None = None, tag: str | None = None) -> Path:
    p = Path(live_path)
    ss = p.name[: -len(".jsonl")] if p.name.endswith(".jsonl") else p.name
    d = archive_dir(p)
    base = f"{ss}.{_stamp(now)}" + (f".{tag}" if tag else "")
    cand = d / f"{base}.jsonl"
    n = 1
    while cand.exists() or Path(str(cand) + ".zst").exists():
        cand = d / f"{base}_{n}.jsonl"
        n += 1
    return cand


def rotate_if_needed(live_path, max_bytes: int | None = None, now: datetime | None = None) -> Path | None:
    """Roll a live ledger over max_bytes into archive/<SYM_SIDE>.<stamp>.jsonl. Rename only — never unlink, never
    truncate. Returns the segment path, or None when nothing rotated (including on any error: the file just grows)."""
    try:
        p = Path(live_path)
        limit = live_max_bytes() if max_bytes is None else int(max_bytes)
        if not p.is_file() or p.stat().st_size <= limit:
            return None
        dest = segment_path(p, now)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            return None
        os.rename(p, dest)
        return dest
    except Exception:
        return None


def ledger_name(path) -> str | None:
    """SYM_SIDE of a live / legacy / archive file name, else None."""
    name = Path(path).name
    m = SEGMENT_RE.match(name)
    if m:
        return m.group("ss")
    m = LEGACY_RE.match(name)
    if m:
        return m.group("ss")
    if name.endswith(".jsonl"):
        return name[: -len(".jsonl")]
    return None


def local_segments(live_path, include_compressed: bool = True) -> list[Path]:
    """Every rolled-over piece of this ledger still on this host (legacy .bak siblings + archive/), oldest first."""
    p = Path(live_path)
    ss = p.name[: -len(".jsonl")] if p.name.endswith(".jsonl") else p.name
    out = []
    try:
        for f in p.parent.glob(f"{ss}.jsonl*.bak"):
            m = LEGACY_RE.match(f.name)
            if m and m.group("ss") == ss and f.is_file():
                out.append(f)
    except OSError:
        pass
    d = archive_dir(p)
    try:
        for f in d.glob(f"{ss}.*.jsonl*"):
            m = SEGMENT_RE.match(f.name)
            if m and m.group("ss") == ss and f.is_file() and (include_compressed or not m.group("zst")):
                out.append(f)
    except OSError:
        pass

    def _key(f: Path):
        try:
            return (f.stat().st_mtime, f.name)
        except OSError:
            return (0.0, f.name)

    return sorted(out, key=_key)


def recent_segments(live_path, max_age_s: float, now_ts: float | None = None) -> list[Path]:
    """Uncompressed rolled-over pieces modified within max_age_s — what windowed guards (echo / conservation / 7d sync)
    must read beside the live file so a rotation can never make a recent row look absent."""
    import time

    now_ts = time.time() if now_ts is None else now_ts
    out = []
    for f in local_segments(live_path, include_compressed=False):
        try:
            if now_ts - f.stat().st_mtime <= max_age_s:
                out.append(f)
        except OSError:
            continue
    return out


def _open_lines(path: Path):
    if path.name.endswith(".zst"):
        proc = subprocess.run(["zstd", "-dc", str(path)], capture_output=True, check=True,
                              env={**os.environ, "PATH": "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "/usr/bin:/bin")})
        return proc.stdout.decode("utf-8", "replace").splitlines()
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read().splitlines()


def offsite_segments(live_path) -> list[Path]:
    """Segments already offloaded (local copy removed) that are reachable on a mounted ext drive, from the manifest."""
    p = Path(live_path)
    man = archive_dir(p) / MANIFEST
    ss = ledger_name(p)
    out = []
    try:
        with open(man, encoding="utf-8") as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("ledger") != ss or not r.get("local_removed"):
                    continue
                for c in r.get("copies") or []:
                    if c.get("kind") == "ext" and c.get("verified") and Path(c.get("path", "")).is_file():
                        out.append(Path(c["path"]))
                        break
    except OSError:
        pass
    return out


def iter_rows(live_path, include_offsite: bool = True):
    """FULL history of one ledger, oldest piece first: offloaded segments on a mounted drive, local segments, live.
    Yields dicts; unparseable lines are skipped. Rows are verbatim (dedupe with vec_decisions.history_canon)."""
    pieces = (offsite_segments(live_path) if include_offsite else []) + local_segments(live_path) + [Path(live_path)]
    seen = set()
    for f in pieces:
        if str(f) in seen or not f.is_file():
            continue
        seen.add(str(f))
        try:
            lines = _open_lines(f)
        except Exception:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                yield r
