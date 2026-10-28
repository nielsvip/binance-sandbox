#!/usr/bin/env python3
"""history_archive_offload — full trade history, never deleted, offloaded only with verified copies.

USER 2026-10-27 (verbatim): "Once a history file passes 200 KB, the oldest history is deleted <- this makes no sense it
should at least keep ~10mb then offload to ext drvie when availble but we need full history stored somewhere pls make
sure thiswill happen."

Ledgers: <root>/<acct>/<SYM_SIDE>.jsonl for root in data/history and data/tradier/history (vec_decisions/history_archive.py).

  fold      legacy rotations (<SS>.jsonl.bak, <SS>.jsonl.<ts>.bak) quiet for >= 1 h -> archive/<SS>.<mtime-stamp>.legacy.jsonl
            (os.rename on the same filesystem; mtime kept so windowed readers still see recent rows)
  compress  uncompressed archive segments quiet for >= 8 days -> <seg>.zst; the original is removed only after
            `zstd -t` passes AND the decompressed sha256 equals the original's
  offload   each .zst -> <ext>/binance_archive/history/<host>/<root>/<acct>/<name> (sha256 verified after copy) and
            gs://v15-cold-329002141504/history/<host>/<root>/<acct>/<name> (md5 verified by GCS, tools/gcs_cold_store.py).
            The local .zst is removed only when >= 2 verified off-Mac copies exist and the ext copy re-verifies.
            No verified copy -> the segment stays local. Manifest: <acct>/archive/offload_manifest.jsonl.
  recover   fold rows from other copies of history (S1 pulls, backups/, ext drives) that the local ledger lacks into
            archive/<SS>.<now>.recovered.jsonl (rows verbatim, oldest first, rows older than 8 days only; the 7-day
            Mac<->S1 sync owns the recent window). Idempotent: identity = sorted-key row sha256, plus a (type, qty) match
            within 900 s (the writers' own dedup window) so another host's echo of the same fill is not added twice.

Ext drives: WD25 first (probed healthy 2026-10-27, 3.7 TiB free), then SSD2T/E3T when they have room. TOSHIBA_EXT is
skipped unless --allow-toshiba (2026-10-22: a fresh copy failed sha256 there). A drive must be a real mount (st_dev
differs from the boot data volume), have >= 2 GB free and pass a write/read sha256 probe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from vec_decisions import history_archive as HA  # noqa: E402

ROOTS = {"history": ROOT / "data" / "history", "tradier_history": ROOT / "data" / "tradier" / "history"}
ACCOUNTS = ("ang", "fin", "flz", "inf", "men", "tra", "trb", "trc")
EXT_ORDER = ("/Volumes/WD25", "/Volumes/SSD2T", "/Volumes/E3T", "/Volumes/TOSHIBA_EXT")
LEGACY_QUIET_S = 3600.0
COMPRESS_AFTER_S = 8 * 86400.0
RECOVER_OLDER_THAN_S = 8 * 86400.0
MIN_EXT_FREE = 2 * 1024**3
ZENV = {**os.environ, "PATH": "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "/usr/bin:/bin")}
REPORT = ROOT / "data" / "reports" / "history_archive"


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def sha256_file(p) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _log(event, **kw):
    REPORT.mkdir(parents=True, exist_ok=True)
    row = {"ts": _now_iso(), "event": event, **kw}
    with open(REPORT / f"log_{datetime.now(timezone.utc):%Y%m%d}.jsonl", "a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")
    print(json.dumps(row, default=str))


def ledgers(root: Path):
    for acct in ACCOUNTS:
        d = root / acct
        if d.is_dir() and not d.is_symlink():  # data/history/tr* are symlinks to data/tradier/history/tr*
            yield acct, d


# ------------------------------------------------------------------ fold legacy .bak
def fold_legacy(root: Path, dry=False, now=None) -> int:
    now = time.time() if now is None else now
    n = 0
    for acct, d in ledgers(root):
        for f in sorted(d.glob("*.jsonl*.bak")):
            m = HA.LEGACY_RE.match(f.name)
            if not m or not f.is_file():
                continue
            st = f.stat()
            if now - st.st_mtime < LEGACY_QUIET_S:
                continue
            live = d / f"{m.group('ss')}.jsonl"
            dest = HA.segment_path(live, datetime.fromtimestamp(st.st_mtime, timezone.utc), tag="legacy")
            _log("fold", src=str(f), dest=str(dest), bytes=st.st_size, dry=dry)
            if not dry:
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.rename(f, dest)
                os.utime(dest, (st.st_atime, st.st_mtime))
            n += 1
    return n


# ------------------------------------------------------------------ compress
def compress_segment(seg: Path, dry=False) -> Path | None:
    src_sha = sha256_file(seg)
    out = Path(str(seg) + ".zst")
    tmp = Path(str(seg) + ".zst.tmp")
    if dry:
        _log("compress", seg=str(seg), dry=True)
        return None
    subprocess.run(["zstd", "-q", "-19", "--check", "-f", "-o", str(tmp), str(seg)], check=True, env=ZENV)
    subprocess.run(["zstd", "-q", "-t", str(tmp)], check=True, env=ZENV)
    dec = subprocess.run(["zstd", "-dc", str(tmp)], check=True, capture_output=True, env=ZENV).stdout
    if hashlib.sha256(dec).hexdigest() != src_sha:
        tmp.unlink()
        _log("compress_FAILED_VERIFY", seg=str(seg))
        return None
    st = seg.stat()
    os.rename(tmp, out)
    os.utime(out, (st.st_atime, st.st_mtime))
    _manifest(seg.parent, {"ledger": HA.ledger_name(seg), "segment": out.name, "raw_sha256": src_sha, "raw_bytes": st.st_size, "sha256": sha256_file(out), "bytes": out.stat().st_size, "event": "compressed"})
    seg.unlink()
    _log("compress", seg=str(seg), out=str(out), raw_bytes=st.st_size, zst_bytes=out.stat().st_size, raw_sha256=src_sha)
    return out


def compress_all(root: Path, dry=False, now=None) -> int:
    now = time.time() if now is None else now
    n = 0
    for acct, d in ledgers(root):
        a = d / HA.ARCHIVE_DIRNAME
        if not a.is_dir():
            continue
        for seg in sorted(a.glob("*.jsonl")):
            if HA.SEGMENT_RE.match(seg.name) and now - seg.stat().st_mtime >= COMPRESS_AFTER_S:
                if compress_segment(seg, dry) is not None or dry:
                    n += 1
    return n


# ------------------------------------------------------------------ manifest
def _manifest(archive_dir: Path, row: dict):
    archive_dir.mkdir(parents=True, exist_ok=True)
    with open(archive_dir / HA.MANIFEST, "a") as fh:
        fh.write(json.dumps({"ts": _now_iso(), **row}, default=str) + "\n")


def _manifest_rows(archive_dir: Path):
    try:
        with open(archive_dir / HA.MANIFEST) as fh:
            return [json.loads(x) for x in fh if x.strip()]
    except (OSError, ValueError):
        return []


# ------------------------------------------------------------------ ext drives
def ext_health(path: str, allow_toshiba=False) -> dict:
    r = {"path": path, "ok": False}
    try:
        if "TOSHIBA" in path and not allow_toshiba:
            r["why"] = "TOSHIBA_EXT suspect since 2026-10-22 (sha256 failure); --allow-toshiba to use"
            return r
        if not os.path.ismount(path):
            r["why"] = "not mounted"
            return r
        if os.stat(path).st_dev == os.stat("/System/Volumes/Data" if os.path.exists("/System/Volumes/Data") else "/").st_dev:
            r["why"] = "phantom dir on the boot volume"
            return r
        free = shutil.disk_usage(path).free
        r["free"] = free
        if free < MIN_EXT_FREE:
            r["why"] = f"only {free / 1e9:.2f} GB free"
            return r
        probe = Path(path) / "binance_archive" / f".history_probe_{os.getpid()}"
        probe.parent.mkdir(parents=True, exist_ok=True)
        blob = os.urandom(4 << 20)
        probe.write_bytes(blob)
        okp = hashlib.sha256(probe.read_bytes()).hexdigest() == hashlib.sha256(blob).hexdigest()
        probe.unlink()
        if not okp:
            r["why"] = "write/read probe sha256 mismatch"
            return r
        r["ok"] = True
    except Exception as e:
        r["why"] = repr(e)
    return r


def healthy_ext(allow_toshiba=False):
    out = []
    for p in EXT_ORDER:
        h = ext_health(p, allow_toshiba)
        _log("ext_health", **h)
        if h["ok"]:
            out.append(p)
    return out


# ------------------------------------------------------------------ offload
def _gcs():
    try:
        from tools import gcs_cold_store as G

        if not G.KEY.exists():
            return None, None
        return G, G.mint_token()
    except Exception as e:
        _log("gcs_unavailable", why=repr(e))
        return None, None


def offload_root(root_name: str, root: Path, host="mac", drives=None, use_gcs=True, min_copies=2, dry=False) -> dict:
    drives = drives if drives is not None else healthy_ext()
    G, tok = _gcs() if use_gcs and not dry else (None, None)
    stat = {"segments": 0, "ext_copies": 0, "gcs_copies": 0, "local_removed": 0, "kept_local": 0}
    for acct, d in ledgers(root):
        a = d / HA.ARCHIVE_DIRNAME
        if not a.is_dir():
            continue
        done = {}
        for r in _manifest_rows(a):
            if r.get("event") == "offloaded":
                done[r["segment"]] = r
        for seg in sorted(a.glob("*.jsonl.zst")):
            stat["segments"] += 1
            sha = sha256_file(seg)
            prev = done.get(seg.name) or {}
            copies = [c for c in prev.get("copies") or [] if c.get("verified") and c.get("sha256", sha) == sha]
            rel = f"{root_name}/{acct}/{seg.name}"
            have_ext = {c["path"] for c in copies if c["kind"] == "ext"}
            for drv in drives:
                dest = Path(drv) / "binance_archive" / "history" / host / rel
                if str(dest) in have_ext:
                    continue
                if dry:
                    _log("offload_ext", seg=str(seg), dest=str(dest), dry=True)
                    continue
                if str(drv).startswith("/Volumes/") and not os.path.ismount(drv):  # 2026-10-28 01:5xZ: WD25 + TOSHIBA_EXT were unmounted mid-run
                    continue
                tmp = dest.with_name(dest.name + ".part")
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(seg, tmp)
                    if sha256_file(tmp) == sha:
                        os.replace(tmp, dest)
                        copies.append({"kind": "ext", "path": str(dest), "sha256": sha, "verified": True, "at": _now_iso()})
                        stat["ext_copies"] += 1
                        have_ext.add(str(dest))
                        break  # one ext copy per run is enough; GCS is the second
                    tmp.unlink()
                    _log("offload_ext_FAILED_VERIFY", seg=str(seg), dest=str(dest))
                except OSError as e:
                    _log("offload_ext_FAILED", seg=str(seg), dest=str(dest), why=repr(e))
            if G is not None and not any(c["kind"] == "gcs" for c in copies):
                obj = f"history/{host}/{rel}"
                try:
                    pres = G.object_present(obj, tok)
                    if pres is not None:
                        import base64

                        md5 = base64.b64encode(hashlib.md5(seg.read_bytes()).digest()).decode()
                        ok = pres.get("md5Hash") == md5
                        res = {"remote_md5": pres.get("md5Hash"), "verified": ok}
                    else:
                        res = G.push_local(str(seg), obj, tok, raw=True)
                    if res.get("verified"):
                        copies.append({"kind": "gcs", "object": f"gs://{G.BUCKET}/{obj}", "md5": res.get("remote_md5"), "sha256": sha, "verified": True, "at": _now_iso()})
                        stat["gcs_copies"] += 1
                except Exception as e:
                    _log("offload_gcs_FAILED", seg=str(seg), why=repr(e))
            if dry:
                continue
            row = {"event": "offloaded", "ledger": HA.ledger_name(seg), "segment": seg.name, "sha256": sha, "bytes": seg.stat().st_size, "copies": copies, "local_removed": False}
            ext_ok = []
            for c in copies:
                try:
                    if c["kind"] == "ext" and Path(c["path"]).is_file() and sha256_file(c["path"]) == sha:
                        ext_ok.append(c)
                except OSError:
                    pass
            if len(copies) >= min_copies and ext_ok:
                row["local_removed"] = True
                _manifest(a, row)
                seg.unlink()
                stat["local_removed"] += 1
            else:
                _manifest(a, row)
                stat["kept_local"] += 1
            _log("offload", seg=str(seg), copies=len(copies), local_removed=row["local_removed"])
    return stat


# ------------------------------------------------------------------ recover
def _identity(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


ECHO_WINDOW_S = 900.0  # the writers' own dedup window: one ledger never logs the same type+qty twice inside 900 s


def _sig(row: dict):
    """(type, qty): a copy of a row in another host's ledger (synced copy, or that host's echo of the same broker fill,
    13-857 s late at a different mark price) carries the same type and qty inside ECHO_WINDOW_S."""
    try:
        return (str(row.get("type") or "").upper(), round(abs(float(row.get("qty"))), 6))
    except (TypeError, ValueError):
        return None


def _ts(row: dict) -> float:
    from vec_decisions.history_canon import ts_of

    return ts_of(row)


def _read_lines(f: Path):
    try:
        return HA._open_lines(f)
    except Exception as e:
        _log("recover_read_FAILED", file=str(f), why=repr(e))
        return []


def _src_label(src: str) -> str:
    """Provenance tag for a recovered segment name: which copy of history the rows came from."""
    if "/s1x/" in src or "/history_recovery_" in src:
        return "s1"
    if "/Volumes/E3T" in src:
        return "e3t"
    if "TOSHIBA" in src:
        return "toshiba"
    if "/backups/" in src or src.startswith("backups/"):
        return "macbak"
    return "other"


def recover(target_root: Path, sources: list, dry=False, now=None) -> dict:
    """Fold rows found in source history roots but absent from target_root's ledger (live + every local piece +
    offloaded pieces reachable on a mounted drive) into one archive/<SS>.<now>.recovered.jsonl per ledger."""
    now = time.time() if now is None else now
    cutoff = now - RECOVER_OLDER_THAN_S
    rep = {"ledgers": 0, "rows_added": 0, "by_source": {}, "files": []}
    index = {}  # (acct, ss) -> [(source, file)] in source priority order; each source dir is listed once
    for s in sources:
        for acct in ACCOUNTS:
            d = Path(s) / acct
            if not d.is_dir() or d.is_symlink():
                continue
            files = []
            for dd in (d, d / "archive"):
                try:
                    files += [Path(e.path) for e in os.scandir(dd) if e.is_file() and ".jsonl" in e.name]
                except OSError:
                    pass
            for f in sorted(files, key=lambda x: (0 if x.name.endswith(".jsonl") and x.parent == d else 1, x.name)):
                ss = HA.ledger_name(f)
                if ss and re.fullmatch(r"[A-Za-z0-9_.-]+_(?:LONG|SHORT)", ss):
                    index.setdefault((acct, ss), []).append((str(s), f))
    for acct, ss in sorted(index):
        live = target_root / acct / f"{ss}.jsonl"
        have, sigs = set(), {}
        for r in HA.iter_rows(live):
            have.add(_identity(r))
            g = _sig(r)
            if g:
                sigs.setdefault(g, []).append(_ts(r))
        add = []
        for s, f in index[(acct, ss)]:
            for line in _read_lines(f):
                line = line.strip()
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict):
                    continue
                t = _ts(r)
                if t <= 0 or t >= cutoff:
                    continue
                k = _identity(r)
                if k in have:
                    continue
                g = _sig(r)
                if g and any(abs(t - x) < ECHO_WINDOW_S for x in sigs.get(g, [])):
                    continue
                have.add(k)
                if g:
                    sigs.setdefault(g, []).append(t)
                add.append((t, line, str(s)))
        if not add:
            continue
        add.sort(key=lambda x: x[0])
        rep["ledgers"] += 1
        rep["rows_added"] += len(add)
        for _, _, s in add:
            rep["by_source"][s] = rep["by_source"].get(s, 0) + 1
        groups = {}
        for t, line, src in add:
            groups.setdefault(_src_label(src), []).append((t, line, src))
        for label, rows in sorted(groups.items()):
            seg = HA.segment_path(live, tag=f"recovered-{label}")
            rep["files"].append({"ledger": f"{acct}/{ss}", "segment": str(seg), "rows": len(rows), "first": datetime.fromtimestamp(rows[0][0], timezone.utc).isoformat(), "last": datetime.fromtimestamp(rows[-1][0], timezone.utc).isoformat()})
            if dry:
                continue
            seg.parent.mkdir(parents=True, exist_ok=True)
            tmp = seg.with_name(seg.name + ".tmp")
            with open(tmp, "w") as fh:
                for _, line, _ in rows:
                    fh.write(line + "\n")
            os.rename(tmp, seg)
            os.utime(seg, (rows[-1][0], rows[-1][0]))  # the segment is dated by its newest row, never by today
            _manifest(seg.parent, {"event": "recovered", "ledger": ss, "segment": seg.name, "rows": len(rows), "sha256": sha256_file(seg), "sources": sorted({x for _, _, x in rows})})
    _log("recover", target=str(target_root), dry=dry, ledgers=rep["ledgers"], rows_added=rep["rows_added"], by_source=rep["by_source"])
    return rep


# ------------------------------------------------------------------ cli
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["fold", "compress", "offload", "cycle", "recover", "ext-health"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--host", default="mac")
    ap.add_argument("--no-gcs", action="store_true")
    ap.add_argument("--min-copies", type=int, default=2)
    ap.add_argument("--allow-toshiba", action="store_true")
    ap.add_argument("--target", choices=list(ROOTS), help="recover: which local root receives the rows")
    ap.add_argument("--source", action="append", default=[], help="recover: a history root (dir holding <acct>/ dirs); repeatable")
    a = ap.parse_args()
    if a.command in ("fold", "compress", "offload", "cycle", "recover") and not a.dry_run:
        import fcntl

        REPORT.mkdir(parents=True, exist_ok=True)
        lock = open(REPORT / ".offload.lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("another history_archive_offload run holds the lock; exiting")
            return 0
    if a.command == "ext-health":
        print(json.dumps([ext_health(p, a.allow_toshiba) for p in EXT_ORDER], indent=1, default=str))
        return 0
    if a.command == "recover":
        if not a.target or not a.source:
            raise SystemExit("recover needs --target and --source")
        print(json.dumps(recover(ROOTS[a.target], a.source, a.dry_run), indent=1, default=str))
        return 0
    out = {}
    drives = healthy_ext(a.allow_toshiba) if a.command in ("offload", "cycle") else None
    for name, root in ROOTS.items():
        r = {}
        if a.command in ("fold", "cycle"):
            r["folded"] = fold_legacy(root, a.dry_run)
        if a.command in ("compress", "cycle"):
            r["compressed"] = compress_all(root, a.dry_run)
        if a.command in ("offload", "cycle"):
            r["offload"] = offload_root(name, root, a.host, drives, not a.no_gcs, a.min_copies, a.dry_run)
        out[name] = r
    _log("cycle_done", command=a.command, result=out, drives=drives)
    return 0


if __name__ == "__main__":
    sys.exit(main())
