#!/usr/bin/env python3
"""Rsync changed complete per-sym SQL rows as JSON, then apply them to fleet stores."""
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
SHARDS = ROOT / "data" / "per_sym_rows_current"
WATERMARK = ROOT / "data" / "per_sym_delta_broadcast_watermark.json"
COLS = "sym_side,cat_side,updated_at,winning_tag,wsharpe,pool_sharpe,trades,max_dd_pct,acc_gain_pct,gain_vs_bh,bh_pct,delta_365,delta_30d,gain_30d,baseline_30d,campaign_ts,overrides_json,defaults_snapshot_json,full_config_json,template_md5,defaults_round,meta_json"
HOSTS = {
    "s1-pub": ("/home/niels/binance/data/hourly_reconfig/per_sym_store.db", "/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_store.db"),
    "s2": ("/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_forward_master.db", "/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_store.db"),
    "s5": ("/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_forward_master.db", "/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_store.db"),
    "s6": ("/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_forward_master.db", "/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_store.db"),
}
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o", "ControlMaster=auto", "-o", "ControlPersist=120", "-o", "ControlPath=/tmp/per_sym_delta_%r@%h:%p"]


def _load_watermark():
    try:
        data = json.loads(WATERMARK.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_watermark(data):
    tmp = WATERMARK.with_name(WATERMARK.name + f".new.{os.getpid()}")
    tmp.write_text(json.dumps(data, sort_keys=True))
    os.replace(tmp, WATERMARK)


def _needed(rows, mark):
    stamp = str(mark.get("ts") or "")
    seen = set(mark.get("seen") or [])
    return [row for row in rows if row[2] > stamp or (row[2] == stamp and row[0] not in seen)]


def _send(host, dbs, rows):
    names = [row[0] + ".json" for row in rows]
    rsync = ["rsync", "-az", "--timeout=20", "-e", "ssh " + " ".join(SSH_OPTS), "--files-from=-", str(SHARDS) + "/", f"{host}:binance-sandbox/data/per_sym_rows_current/"]
    sent = subprocess.run(rsync, input="\n".join(names) + "\n", text=True, capture_output=True, timeout=25)
    if sent.returncode:
        return host, False, sent.stderr.strip()[-180:]
    remote = "cd /home/niels/binance-sandbox && python3 tools/per_sym_delta_apply.py " + " ".join("--db " + db for db in dbs)
    applied = subprocess.run(["ssh", *SSH_OPTS, host, remote], input=json.dumps(names), text=True, capture_output=True, timeout=25)
    if applied.returncode:
        return host, False, applied.stderr.strip()[-180:]
    return host, True, applied.stdout.strip()[-180:]


def main():
    marks = _load_watermark()
    if not DB.is_file():
        raise SystemExit("missing Mac per_sym_store.db")
    stamp = min((str((marks.get(host) or {}).get("ts") or "") for host in HOSTS), default="")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    try:
        rows = [list(row) for row in con.execute(f"SELECT {COLS} FROM per_sym_active WHERE updated_at>=? ORDER BY updated_at,sym_side", (stamp,))]
    finally:
        con.close()
    need = {host: _needed(rows, marks.get(host) or {}) for host in HOSTS}
    union = {row[0]: row for batch in need.values() for row in batch}
    SHARDS.mkdir(parents=True, exist_ok=True)
    for symbol_side, row in union.items():
        name = symbol_side + ".json"
        target = SHARDS / name
        tmp = target.with_name(name + f".new.{os.getpid()}")
        tmp.write_text(json.dumps({"sym_side": symbol_side, "updated_at": row[2], "row": row}, default=str))
        os.replace(tmp, target)
    results = []
    with ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:
        futures = {pool.submit(_send, host, HOSTS[host], batch): host for host, batch in need.items() if batch}
        for future in as_completed(futures):
            try:
                host, ok, detail = future.result()
            except Exception as exc:
                host, ok, detail = futures[future], False, str(exc)
            results.append((host, ok, detail))
            if ok:
                batch = need[host]
                latest = max(row[2] for row in batch)
                previous = marks.get(host) or {}
                seen = set(previous.get("seen") or []) if previous.get("ts") == latest else set()
                seen.update(row[0] for row in batch if row[2] == latest)
                marks[host] = {"ts": latest, "seen": sorted(seen)}
    _save_watermark(marks)
    print(json.dumps({"local_rows": len(rows), "queued": {host: len(batch) for host, batch in need.items()}, "results": results}, sort_keys=True), flush=True)
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
