#!/usr/bin/env python3
"""vector_direct_history_import — every REAL S1/S5 vector_direct fill lands ONCE in the Mac's data/history/<acct>/<SYM_SIDE>.jsonl.

USER 2026-10-27 (verbatim): "Every REAL trade has to be added to the /history/ folder on macbook with the quantity $ value and
reason for the trade."

Since 2026-10-27 real orders come from tools/vector_direct_trader.py on S1 (crypto) and S5 (stocks). Their ledgers carry the
reason (`why`) but no fill price; the Mac positions service only saw broker position CHANGES and logged them as
AUGMENT/REDUCE echoes with stale reasons (and again after a restart). This importer, once per cycle and host:
  1. one ssh to the host runs tools/vector_direct_fill_export.py there (reads the ledger, resolves each order's real fill ONCE
     from the broker, cached — steady state makes zero broker calls);
  2. appends one row per filled order, deduped by broker order id (state file + a scan of the target file):
     {"ts": fill time, "type": OPEN|CLOSE|AUGMENT|REDUCE, "qty": executed qty, "price": broker avg fill price,
      "value": $ (= Binance cumQuote / qty x fill price), "reason": the trader's why, "order_id": broker id,
      "indicators": {"price": fill price}, "prov": {"v": 1, "src": "S1_VECTOR_DIRECT"|"S5_VECTOR_DIRECT", ...}, + audit fields}.
Rows are only ever appended (history is audit). The positions-service echoes are left on disk; readers drop them through
vec_decisions/history_canon.py, and vec_decisions/execution_ledger.external_echo stops new ones at the writer.
An order whose fill price is not yet known is NOT written with a guessed price: it stays pending (status file) until resolved.

launchd: ~/Library/LaunchAgents/com.binance.vector-direct-history-import.plist (StartInterval 30, Nice 10). Kill switch:
data/vector_direct_history/DISABLED. Audit: `--audit` prints the shadow classification of every history file with order rows.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "vector_direct_history"
HOSTS = {"crypto": ("s1", "S1_VECTOR_DIRECT"), "stocks": ("s5", "S5_VECTOR_DIRECT")}
SSH = [
    "ssh",
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=10",
    "-o",
    "ServerAliveInterval=10",
    "-o",
    "ServerAliveCountMax=2",
]
ACCT_RE = re.compile(r"^[a-z]{2,8}$")
SS_RE = re.compile(r"^[A-Z0-9]{1,30}_(LONG|SHORT)$")
ACTS = ("OPEN", "CLOSE", "AUGMENT", "REDUCE")
PENDING_ALERT_S = 900.0
SINCE_S = 2 * 86400.0
MAX_DECISION_STATES_PER_HOST = 32


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()


def read_json(p: Path, default):
    try:
        return json.loads(p.read_text())
    except Exception:
        return default


def atomic_write_json(p: Path, payload) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=1, default=str))
    os.replace(tmp, p)


def remote_cmd(venue: str, since: float, max_resolve: int) -> str:
    return (
        "cd ~/binance && P=python3; [ -x ~/.conda/envs/binance_env_mac/bin/python ] && P=~/.conda/envs/binance_env_mac/bin/python; "
        f"$P tools/vector_direct_fill_export.py --venue {venue} --since {since:.0f} --max-resolve {int(max_resolve)}"
    )


def remote_state_cmd(venue: str, hashes: list[str]) -> str:
    safe_hashes = [value for value in hashes if re.fullmatch(r"[0-9a-f]{64}", value)]
    if not safe_hashes or len(safe_hashes) > MAX_DECISION_STATES_PER_HOST:
        raise ValueError("decision state request must contain 1..32 valid SHA-256 hashes")
    joined = ",".join(safe_hashes)
    return (
        "cd ~/binance && P=python3; [ -x ~/.conda/envs/binance_env_mac/bin/python ] && P=~/.conda/envs/binance_env_mac/bin/python; "
        f"$P tools/vector_direct_fill_export.py --venue {venue} --decision-state-hashes {joined}"
    )


def fetch(venue: str, since: float, max_resolve: int, timeout: float = 90.0) -> tuple:
    host = HOSTS[venue][0]
    p = subprocess.run(
        SSH + [host, remote_cmd(venue, since, max_resolve)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if p.returncode != 0:
        raise RuntimeError(
            f"ssh {host} rc={p.returncode}: {(p.stderr or p.stdout)[-300:]}"
        )
    recs, stats = [], None
    for line in p.stdout.splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        if isinstance(r, dict) and "_stats" in r:
            stats = r
        elif isinstance(r, dict):
            recs.append(r)
    if stats is None:
        raise RuntimeError(f"ssh {host}: no _stats line (output truncated?)")
    return recs, stats


def fetch_decision_states(venue: str, hashes: list[str], timeout: float = 45.0) -> list[dict]:
    host = HOSTS[venue][0]
    p = subprocess.run(SSH + [host, remote_state_cmd(venue, hashes)], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"ssh {host} state export rc={p.returncode}: {(p.stderr or p.stdout)[-300:]}")
    rows = []
    for line in p.stdout.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("_decision_state") is True:
            rows.append(row)
    return rows


def _canonical_state_bytes(state: dict) -> bytes:
    return json.dumps(state, sort_keys=True, separators=(",", ":"), default=str).encode()


def sync_decision_state_snapshots(recs: list[dict], decision_state_root: Path, state_fetcher=fetch_decision_states) -> dict:
    """Fetch only missing state hashes referenced by these fills, verify, then cache immutable bodies locally."""
    requested = {venue: set() for venue in HOSTS}
    root = Path(decision_state_root)
    root_resolved = root.resolve()
    invalid_refs = 0
    for venue, rec in (("crypto" if rec.get("venue") == "crypto" else "stocks", rec) for rec in recs):
        proof = rec.get("decision_provenance")
        if not isinstance(proof, dict):
            continue
        digest = str(proof.get("state_sha256") or "").lower()
        relative = str(proof.get("state_file") or "")
        expected = f"decision_states/{digest[:2]}/{digest}.json"
        if not re.fullmatch(r"[0-9a-f]{64}", digest) or relative != expected:
            invalid_refs += 1
            continue
        path = (root / relative).resolve()
        if not path.is_relative_to(root_resolved):
            invalid_refs += 1
            continue
        valid = False
        try:
            state = json.loads(path.read_text())
            valid = hashlib.sha256(_canonical_state_bytes(state)).hexdigest() == digest
        except (OSError, ValueError, TypeError):
            pass
        if not valid:
            requested[venue].add(digest)
    counts = {"requested": 0, "verified": 0, "rejected": 0, "invalid_references": invalid_refs}
    for venue, values in requested.items():
        ordered = sorted(values)
        for start in range(0, len(ordered), MAX_DECISION_STATES_PER_HOST):
            batch = ordered[start:start + MAX_DECISION_STATES_PER_HOST]
            counts["requested"] += len(batch)
            try:
                rows = state_fetcher(venue, batch)
            except Exception:
                continue
            for row in rows:
                digest = str(row.get("state_sha256") or "").lower()
                relative = str(row.get("state_file") or "")
                state = row.get("state")
                expected = f"decision_states/{digest[:2]}/{digest}.json"
                if digest not in batch or relative != expected or not isinstance(state, dict):
                    counts["rejected"] += 1
                    continue
                if hashlib.sha256(_canonical_state_bytes(state)).hexdigest() != digest:
                    counts["rejected"] += 1
                    continue
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
                tmp.write_bytes(_canonical_state_bytes(state))
                os.replace(tmp, path)
                counts["verified"] += 1
    return counts


def build_row(rec: dict, src: str) -> dict | None:
    """History row for one exported order, or None when it did not fill (or is not a valid order record). Pure."""
    f = rec.get("fill") or {}
    acct, ss, act = (
        str(rec.get("acct") or ""),
        str(rec.get("ss") or ""),
        str(rec.get("act") or "").upper(),
    )
    if not (ACCT_RE.match(acct) and SS_RE.match(ss) and act in ACTS and f.get("found")):
        return None
    q = float(f.get("exec_qty") or 0.0)
    px = float(f.get("avg_price") or 0.0)
    if q <= 0 or px <= 0:
        return None
    oid = str(f.get("order_id") or "")
    if not oid:
        return None
    t = float(f.get("fill_ms") or 0) / 1000.0 or float(rec.get("ledger_ts") or 0)
    quote = float(f.get("quote") or 0.0) or q * px
    why = str(rec.get("why") or "")
    row = {
        "ts": iso(t),
        "type": act,
        "qty": round(q, 8),
        "price": round(px, 10),
        "value": round(quote, 2),
        "reason": why,
        "order_id": oid,
        "indicators": {"price": round(px, 10)},
        "prov": {
            "v": 1,
            "src": src,
            "label": why[:160],
            "switch": "VECTOR_DIRECT",
            "cand": None,
            "filters": [],
            "tf": "15m",
            "set_id": None,
            "ev": act,
        },
        "venue": rec.get("venue"),
        "client_order_id": f.get("client_order_id"),
        "broker_status": f.get("status"),
        "ledger_key": rec.get("key"),
        "sent_ts": rec.get("ledger_iso"),
        "sent_mark": rec.get("sent_mark"),
        "fill_src": f.get("src"),
    }
    if isinstance(rec.get("decision_provenance"), dict):
        row["decision_provenance"] = rec["decision_provenance"]
    return row


def file_has_order(p: Path, oid: str) -> bool:
    needle = f'"order_id": "{oid}"'
    pieces = [p, p.with_name(p.name + ".bak")]
    try:  # rows rolled into archive/ (vec_decisions.history_archive) within the import's 8-day memory
        sys.path.insert(0, str(ROOT))
        from vec_decisions import history_archive as _ha

        pieces += _ha.recent_segments(p, 9 * 86400)
    except Exception:
        pass
    for f in pieces:
        try:
            with open(f, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                n = fh.tell()
                fh.seek(max(0, n - 1_000_000))
                if needle.encode() in fh.read():
                    return True
        except OSError:
            continue
    return False


def append_row(hist: Path, row: dict) -> Path:
    p = hist / row["_acct"] / f"{row['_ss']}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in row.items() if not k.startswith("_")}
    data = (json.dumps(clean, default=str) + "\n").encode()
    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return p


def ingest(
    venue: str, recs: list, state: dict, hist: Path, now: float, audit_log=None
) -> dict:
    """Append every filled, not yet imported order. Returns counts. Mutates state['imported'] / state['pending']."""
    src = HOSTS[venue][1]
    imported = state.setdefault("imported", {})
    pend = state.setdefault("pending", {})
    out = {"seen": len(recs), "appended": 0, "already": 0, "no_fill": 0, "pending": 0}
    for rec in recs:
        f = rec.get("fill") or {}
        lk = f"{venue}:{rec.get('key')}"
        if not f.get("found") or not rec.get("final"):
            if rec.get("ledger_status") in ("SENT", "PROTECTED_OTO_SUBMITTED"):
                pend.setdefault(
                    lk,
                    {
                        "first_seen": now,
                        "acct": rec.get("acct"),
                        "ss": rec.get("ss"),
                        "act": rec.get("act"),
                        "ledger_iso": rec.get("ledger_iso"),
                        "order_id": (rec.get("resp") or {}).get("orderId"),
                    },
                )
                out["pending"] += 1
            continue
        pend.pop(lk, None)
        row = build_row(rec, src)
        if row is None:
            out["no_fill"] += 1
            continue
        ik = f"{venue}:{rec['acct']}:{row['order_id']}"
        if ik in imported:
            out["already"] += 1
            continue
        row["_acct"], row["_ss"] = rec["acct"], rec["ss"]
        target = hist / rec["acct"] / f"{rec['ss']}.jsonl"
        if file_has_order(target, row["order_id"]):
            imported[ik] = {"ts": now, "how": "found_on_disk"}
            out["already"] += 1
            continue
        append_row(hist, row)
        imported[ik] = {"ts": now, "fill_ts": row["ts"], "ss": rec["ss"]}
        out["appended"] += 1
        if audit_log is not None:
            audit_log(
                {k: v for k, v in row.items() if not k.startswith("_")}
                | {"acct": rec["acct"], "ss": rec["ss"], "imported_at": iso(now)}
            )
    cut = now - 8 * 86400
    for k in [k for k, v in imported.items() if float(v.get("ts") or 0) < cut]:
        imported.pop(k, None)
    return out


def run_once(venues, max_resolve: int, now=None, fetcher=fetch, hist=None, state_fetcher=fetch_decision_states) -> dict:
    now = now or time.time()
    hist = hist or (ROOT / "data" / "history")
    OUT.mkdir(parents=True, exist_ok=True)
    state_p = OUT / "import_state.json"
    state = read_json(state_p, {}) or {}
    status = read_json(OUT / "status.json", {}) or {}
    log_p = (
        OUT / f"import_{dt.datetime.fromtimestamp(now, dt.timezone.utc):%Y-%m-%d}.jsonl"
    )

    def audit_log(r):
        with open(log_p, "a") as fh:
            fh.write(json.dumps(r, default=str) + "\n")

    for venue in venues:
        vs = state.setdefault(venue, {})
        st = {"ts": iso(now), "host": HOSTS[venue][0]}
        try:
            recs, stats = fetcher(venue, now - SINCE_S, max_resolve)
            state_sync = sync_decision_state_snapshots(recs, ROOT / "data" / "vector_direct", state_fetcher)
            res = ingest(venue, recs, vs, hist, now, audit_log)
            st.update(ok=True, remote=stats.get("_stats"), decision_states=state_sync, **res)
            vs["last_ok"] = now
        except Exception as exc:
            st.update(ok=False, error=f"{type(exc).__name__}: {str(exc)[:300]}")
        old = [
            dict(v, key=k)
            for k, v in (vs.get("pending") or {}).items()
            if now - float(v.get("first_seen") or now) > PENDING_ALERT_S
        ]
        st["pending_over_15min"] = old[:20]
        st["last_ok_age_s"] = (
            round(now - float(vs.get("last_ok") or 0)) if vs.get("last_ok") else None
        )
        status[venue] = st
    atomic_write_json(state_p, state)
    atomic_write_json(OUT / "status.json", status)
    return status


def audit(hist=None) -> dict:
    """Shadow classification (vec_decisions.history_canon) of every history file that holds order rows."""
    sys.path.insert(0, str(ROOT))
    from vec_decisions import history_canon as hc

    hist = hist or (ROOT / "data" / "history")
    rep = {"files": 0, "order_rows": 0, "shadows": {}, "detail": []}
    for p in sorted(hist.glob("*/*.jsonl")):
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        if b'"order_id"' not in raw:
            continue
        rows = []
        for line in raw.decode("utf-8", "replace").splitlines():
            try:
                h = json.loads(line)
            except Exception:
                continue
            if isinstance(h, dict):
                rows.append(h)
        sh = hc.classify(rows)
        rep["files"] += 1
        rep["order_rows"] += sum(1 for h in rows if hc.order_id(h))
        for i, (cause, oid) in sorted(sh.items()):
            rep["shadows"][cause] = rep["shadows"].get(cause, 0) + 1
            h = rows[i]
            rep["detail"].append(
                {
                    "file": f"{p.parent.name}/{p.name}",
                    "ts": h.get("ts"),
                    "type": h.get("type"),
                    "qty": h.get("qty"),
                    "price": h.get("price"),
                    "reason": str(h.get("reason") or "")[:60],
                    "cause": cause,
                    "order_id": oid,
                }
            )
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--venues", default="crypto,stocks")
    ap.add_argument("--max-resolve", type=int, default=12)
    ap.add_argument(
        "--audit",
        action="store_true",
        help="print the shadow classification of history files with order rows, then exit",
    )
    a = ap.parse_args(argv)
    if a.audit:
        rep = audit()
        atomic_write_json(OUT / "shadows_latest.json", rep)
        print(json.dumps({k: v for k, v in rep.items() if k != "detail"}, indent=1))
        return 0
    if (OUT / "DISABLED").exists():
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    lock = open(OUT / ".lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0
    venues = [v for v in a.venues.split(",") if v in HOSTS]
    st = run_once(venues, a.max_resolve)
    if any(
        s.get("appended") or s.get("pending") or not s.get("ok")
        for v, s in st.items()
        if v in venues
    ):
        print(
            f"[{iso(time.time())}] "
            + " | ".join(
                f"{v}: ok={s.get('ok')} app={s.get('appended')} already={s.get('already')} pend={s.get('pending')} {s.get('error') or ''}"
                for v, s in st.items()
                if v in venues
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
