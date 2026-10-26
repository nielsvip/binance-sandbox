#!/usr/bin/env python3
"""Verified cold store on Google Cloud Storage for fleet corpora.

Why GCS and not Drive: the service account 329002141504-compute@developer.gserviceaccount.com cannot store a single
byte in Drive. Measured 2026-10-26 21:59Z, rclone copy of a 1 MB probe into its own My Drive:
    Error 403: Service Accounts do not have storage quota. ... storageQuotaExceeded
Consumer Drive has no shared drives and gmail.com has no domain-wide delegation, so every Drive path for this key
stores nothing. The same key DOES authenticate to GCS in project civil-ripple-439101-j3, so the cold store is the
bucket v15-cold-329002141504 (europe-north1, next to Hetzner hel1; NEARLINE -> COLDLINE at 30 d -> ARCHIVE at 120 d).

Topology (the point of this tool): a worker uploads to GCS DIRECTLY. Nothing stages through the Mac, which has the
least free disk in the fleet and would double every byte over the wire. The private key never leaves the Mac: the Mac
mints a 1-hour OAuth access token, opens a GCS resumable session, and hands the worker only the session URI, which is
its own capability and outlives the token. The worker streams `tar | zstd` into that session and hashes the exact
bytes it sends; GCS returns its own md5 and crc32c, and the upload is only called verified when they agree.

Commands: token | session | push --host H --source P | push --local P | verify --name OBJ | ls [--prefix P] | stat
Every verified push appends a receipt to data/reports/gcs_cold_store.jsonl (object, bytes, sha256, md5, class).
Nothing in this tool deletes anything, on any host or in the bucket.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUCKET = os.environ.get("V15_GCS_BUCKET", "v15-cold-329002141504")
KEY = Path(os.environ.get("V15_ARCHIVE_GOOGLE_KEY", Path.home() / "Downloads/civil-ripple-439101-j3-d50fb6537cdb.json"))
RECEIPTS = Path(os.environ.get("V15_GCS_RECEIPTS", ROOT / "data/reports/gcs_cold_store.jsonl"))
SCOPE = "https://www.googleapis.com/auth/devstorage.read_write"
HOSTS = {"s1": "s1-pub", "s1-int": "s1-int", "s2": "s2", "s5": "s5", "s6": "s6", "gateway": "gateway"}
REMOTE_UPLOADER = ".cache/gcs_cold_upload.py"
CHUNK = 64 * 1024 * 1024
API = "https://storage.googleapis.com/storage/v1/b"
UPLOAD_API = "https://storage.googleapis.com/upload/storage/v1/b"


def _log(event: str, **fields) -> None:
    row = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    print(json.dumps(row, sort_keys=True), flush=True)


def _receipt(row: dict) -> None:
    RECEIPTS.parent.mkdir(parents=True, exist_ok=True)
    with RECEIPTS.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **row}, sort_keys=True) + "\n")


def mint_token(scope: str = SCOPE) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key = json.loads(KEY.read_text())
    now = int(time.time())
    encode = lambda part: base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=")
    signing_input = encode({"alg": "RS256", "typ": "JWT"}) + b"." + encode({"iss": key["client_email"], "scope": scope, "aud": key["token_uri"], "iat": now, "exp": now + 3600})
    private = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    signature = base64.urlsafe_b64encode(private.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())).rstrip(b"=")
    body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": (signing_input + b"." + signature).decode()}).encode()
    with urllib.request.urlopen(urllib.request.Request(key["token_uri"], data=body), timeout=60) as response:
        return json.load(response)["access_token"]


def _api(method: str, url: str, token: str, body: bytes | None = None, ctype: str = "application/json") -> tuple[int, str, dict]:
    request = urllib.request.Request(url, data=body, method=method, headers={"Authorization": f"Bearer {token}", "Content-Type": ctype})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, response.read().decode(errors="replace"), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode(errors="replace"), dict(exc.headers or {})


def open_session(name: str, token: str, bucket: str = BUCKET, content_type: str = "application/zstd") -> str:
    meta = json.dumps({"name": name, "contentType": content_type}).encode()
    url = f"{UPLOAD_API}/{bucket}/o?uploadType=resumable&name={urllib.parse.quote(name, safe='')}"
    status, body, headers = _api("POST", url, token, meta)
    if status != 200 or not headers.get("Location"):
        raise RuntimeError(f"could not open resumable session for {name}: {status} {body[:300]}")
    return headers["Location"]


def _put_chunk(session: str, block: bytes, offset: int, total: int | None) -> tuple[int, int]:
    end = offset + len(block) - 1
    span = f"bytes {offset}-{end}/{total}" if total is not None else f"bytes {offset}-{end}/*"
    if not block and total is not None:
        span = f"bytes */{total}"
    request = urllib.request.Request(session, data=block, method="PUT", headers={"Content-Range": span, "Content-Length": str(len(block))})
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                return response.status, int(offset + len(block))
        except urllib.error.HTTPError as exc:
            if exc.code == 308:
                received = (dict(exc.headers or {})).get("Range", "")
                return 308, int(received.split("-")[-1]) + 1 if "-" in received else offset + len(block)
            if exc.code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(2**attempt)
                continue
            raise RuntimeError(f"chunk upload failed at offset {offset}: {exc.code} {exc.read()[:300]!r}") from exc
        except (urllib.error.URLError, TimeoutError):
            if attempt < 5:
                time.sleep(2**attempt)
                continue
            raise
    raise RuntimeError(f"chunk upload exhausted retries at offset {offset}")


def cancel_session(session: str) -> bool:
    """Cancel a resumable session. No object is ever created, so this destroys no stored data."""
    try:
        urllib.request.urlopen(urllib.request.Request(session, method="DELETE", headers={"Content-Length": "0"}), timeout=60)
        return True
    except urllib.error.HTTPError as exc:
        return exc.code in (200, 204, 499)
    except Exception:
        return False


def stream_to_session(session: str, stream, chunk: int = CHUNK, on_eof=None) -> dict:
    """Upload every byte `stream` yields, hashing exactly what is sent.

    `on_eof` is checked after the producer signals EOF and BEFORE the final chunk is sent. If it raises, the session is
    cancelled and no object comes into existence — a producer that died mid-stream can never leave a short archive in
    the bucket for a later rebuild to mistake for the whole corpus.
    """
    sha256 = hashlib.sha256()
    md5 = hashlib.md5()
    crc = 0
    offset = 0
    pending = b""
    final = None
    while True:
        want = chunk - len(pending)
        block = stream.read(want) if want > 0 else b""
        if block:
            pending += block
            if len(pending) < chunk:
                continue
        if not block and on_eof is not None:
            try:
                on_eof()
            except Exception:
                cancel_session(session)
                raise
        sha256.update(pending)
        md5.update(pending)
        crc = zlib.crc32(pending, crc)
        total = None if block else offset + len(pending)
        try:
            status, offset = _put_chunk(session, pending, offset, total)
        except Exception:
            cancel_session(session)
            raise
        pending = b""
        if not block:
            if status == 308:
                status, offset = _put_chunk(session, b"", offset, offset)
            final = status
            break
    if final not in (200, 201):
        cancel_session(session)
        raise RuntimeError(f"resumable upload did not finalise: status {final}")
    return {"bytes": offset, "sha256": sha256.hexdigest(), "md5_b64": base64.b64encode(md5.digest()).decode(), "crc32c_local_zlib": crc}


def _producer_guard(proc, source: str):
    def check():
        if proc.wait(timeout=120) != 0:
            raise RuntimeError(f"tar|zstd failed for {source} (rc={proc.returncode}); nothing was stored")

    return check


def _tar_zstd(source: str, level: int = 3, threads: int = 2):
    """tar|zstd at the lowest CPU and IO priority available, so a drain can never outrank a pilot.

    `ionice` exists on the Linux workers and not on macOS, so it is probed rather than assumed: hardcoding it made the
    Mac-local path exit 127 (`command not found`), which the upload guard correctly turned into "nothing was stored".
    """
    parent, leaf = os.path.split(source.rstrip("/"))
    idle = "ionice -c3 " if shutil.which("ionice") else ""
    command = "set -o pipefail; nice -n 19 %star -C %s -cf - -- %s | nice -n 19 %szstd -T%d -%d -c" % (idle, shlex.quote(parent or "/"), shlex.quote(leaf), idle, threads, level)
    env = os.environ.copy()
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "/usr/bin:/bin")
    return subprocess.Popen(["bash", "-lc", command], stdout=subprocess.PIPE, bufsize=0, env=env)


def push_local(source: str, name: str, token: str, bucket: str = BUCKET, raw: bool = False) -> dict:
    session = open_session(name, token, bucket)
    if raw:
        with open(source, "rb") as handle:
            result = stream_to_session(session, handle)
    else:
        proc = _tar_zstd(source)
        result = stream_to_session(session, proc.stdout, on_eof=_producer_guard(proc, source))
    verified = verify(name, token, bucket)
    if verified.get("md5Hash") != result["md5_b64"]:
        raise RuntimeError(f"md5 mismatch for {name}: local {result['md5_b64']} remote {verified.get('md5Hash')}")
    return {**result, "object": name, "bucket": bucket, "storage_class": verified.get("storageClass"), "remote_md5": verified.get("md5Hash"), "verified": True}


def verify(name: str, token: str, bucket: str = BUCKET) -> dict:
    status, body, _ = _api("GET", f"{API}/{bucket}/o/{urllib.parse.quote(name, safe='')}", token)
    if status != 200:
        raise RuntimeError(f"object not found after upload: {name}: {status} {body[:200]}")
    return json.loads(body)


SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "ControlPath=none", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=40", "-o", "TCPKeepAlive=yes"]


def object_present(name: str, token: str, bucket: str = BUCKET) -> dict | None:
    try:
        return verify(name, token, bucket)
    except Exception:
        return None


def _remote_push_once(target: str, source: str, name: str, token: str, bucket: str, level: int, threads: int, raw: bool = False) -> dict:
    session = open_session(name, token, bucket)
    raw_arg = " --raw" if raw else ""
    command = 'cd "$HOME" && python3 %s remote-stream --source %s --level %d --threads %d%s' % (shlex.quote(REMOTE_UPLOADER), shlex.quote(source), level, threads, raw_arg)
    proc = subprocess.run(["ssh", *SSH_OPTS, target, "bash", "-lc", shlex.quote(command)], input=session + "\n", capture_output=True, text=True, timeout=86400)
    if proc.returncode:
        raise RuntimeError(f"rc={proc.returncode} {proc.stderr[-300:].strip()}")
    lines = [line for line in proc.stdout.splitlines() if line.startswith("{")]
    if not lines:
        raise RuntimeError(f"remote uploader returned no result: {proc.stdout[-200:]}")
    return json.loads(lines[-1])


def _remote_push(host: str, source: str, name: str, token: str, bucket: str, level: int, threads: int, attempts: int = 2, raw: bool = False) -> dict:
    """Push from the worker, retrying once. A dropped ssh is NOT evidence that nothing was stored: if the worker had
    already finalised the object, GCS keeps it while the driver never saw the local md5. Such an object is UNVERIFIED
    and must never count as a proof copy, so the retry overwrites it and verifies the bytes it just sent."""
    target = HOSTS.get(host, host)
    subprocess.run(["ssh", *SSH_OPTS, target, f"mkdir -p ~/{Path(REMOTE_UPLOADER).parent}"], check=True, capture_output=True, timeout=60)
    sync = subprocess.run(["rsync", "-a", "-e", "ssh " + " ".join(SSH_OPTS), str(Path(__file__).resolve()), f"{target}:{REMOTE_UPLOADER}"], capture_output=True, text=True, timeout=300)
    if sync.returncode:
        raise RuntimeError(f"could not install the uploader on {host}: {sync.stderr[-200:]}")
    last = ""
    for attempt in range(1, attempts + 1):
        try:
            result = _remote_push_once(target, source, name, token, bucket, level, threads, raw)
        except Exception as exc:
            last = str(exc)
            stranded = object_present(name, token, bucket)
            _log("push_attempt_failed", host=host, source=source, object=name, attempt=attempt, error=last, unverified_object_bytes=int(stranded.get("size", 0)) if stranded else 0)
            if attempt < attempts:
                time.sleep(15)
                continue
            raise RuntimeError(f"remote upload failed on {host} after {attempts} attempts: {last}" + (f" — an UNVERIFIED {int(stranded['size']):,}-byte object is present at {name} and must be re-pushed, not trusted" if stranded else ""))
        remote = verify(name, token, bucket)
        if remote.get("md5Hash") != result["md5_b64"]:
            raise RuntimeError(f"md5 mismatch for {name}: {host} sent {result['md5_b64']}, bucket holds {remote.get('md5Hash')}")
        return {**result, "object": name, "bucket": bucket, "host": host, "source": source, "attempts": attempt, "storage_class": remote.get("storageClass"), "remote_md5": remote.get("md5Hash"), "verified": True}
    raise RuntimeError(f"remote upload failed on {host}: {last}")


def main() -> int:
    parser = argparse.ArgumentParser(description="verified GCS cold store for fleet corpora")
    parser.add_argument("command", choices=["token", "session", "push", "remote-stream", "verify", "ls", "stat"])
    parser.add_argument("--host", help="fleet host that holds the source (uploads directly from there)")
    parser.add_argument("--source", help="remote or local path to archive")
    parser.add_argument("--local", help="local file or directory to archive from this machine")
    parser.add_argument("--name", help="object name in the bucket, e.g. s6/s2_archive_20261026.tar.zst")
    parser.add_argument("--bucket", default=BUCKET)
    parser.add_argument("--prefix", default="")
    parser.add_argument("--raw", action="store_true", help="upload the file as-is instead of tar|zstd")
    parser.add_argument("--level", type=int, default=3)
    parser.add_argument("--threads", type=int, default=1)
    args = parser.parse_args()

    if args.command == "remote-stream":
        session = sys.stdin.readline().strip()
        if not session.startswith("https://"):
            raise SystemExit("remote-stream expects the resumable session URI on stdin")
        source = Path(args.source)
        if source.is_file() and args.raw:
            with source.open("rb") as handle:
                result = stream_to_session(session, handle)
        else:
            proc = _tar_zstd(args.source, args.level, args.threads)
            result = stream_to_session(session, proc.stdout, on_eof=_producer_guard(proc, args.source))
        print(json.dumps(result, sort_keys=True))
        return 0

    if args.command == "token":
        print(mint_token())
        return 0
    token = mint_token()
    if args.command == "session":
        if not args.name:
            raise SystemExit("--name is required")
        print(open_session(args.name, token, args.bucket))
        return 0
    if args.command == "ls":
        status, body, _ = _api("GET", f"{API}/{args.bucket}/o?prefix={urllib.parse.quote(args.prefix)}&maxResults=1000", token)
        items = json.loads(body).get("items", []) if status == 200 else []
        total = sum(int(item.get("size", 0)) for item in items)
        for item in items:
            print(f"{int(item.get('size', 0)):>14d}  {item.get('storageClass', ''):>9s}  {item.get('updated', '')[:19]}  {item.get('name')}")
        print(f"{total:>14d}  TOTAL      {len(items)} objects in gs://{args.bucket}/{args.prefix}")
        return 0
    if args.command in ("verify", "stat"):
        if not args.name:
            raise SystemExit("--name is required")
        meta = verify(args.name, token, args.bucket)
        print(json.dumps({k: meta.get(k) for k in ("name", "size", "md5Hash", "crc32c", "storageClass", "updated", "timeCreated")}, indent=1))
        return 0

    if not args.name or not (args.source or args.local):
        raise SystemExit("push needs --name and either --host/--source or --local")
    started = time.time()
    if args.local:
        _log("push_start", target="local", source=args.local, object=args.name, bucket=args.bucket)
        result = push_local(args.local, args.name, token, args.bucket, args.raw)
    else:
        if not args.host:
            raise SystemExit("--source needs --host (use --local for this machine)")
        _log("push_start", target=args.host, source=args.source, object=args.name, bucket=args.bucket)
        result = _remote_push(args.host, args.source, args.name, token, args.bucket, args.level, args.threads, raw=args.raw)
    result["seconds"] = round(time.time() - started, 1)
    result["mb_per_s"] = round(result["bytes"] / 1048576 / max(result["seconds"], 0.001), 2)
    _receipt({"event": "push_verified", **result})
    _log("push_verified", **{k: v for k, v in result.items() if k != "crc32c_local_zlib"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
