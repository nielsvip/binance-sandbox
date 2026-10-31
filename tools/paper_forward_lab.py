#!/usr/bin/env python3
"""Plan distributed paper scenarios and audit input readiness; never submit orders."""
import argparse
import hashlib
import json
import math
import socket
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    from .paper_fleet_budget import HOSTS_BY_VENUE, collect_observed, decide_admission, stable_host
    from .paper_scenario_catalog import inventory
except ImportError:
    from paper_fleet_budget import HOSTS_BY_VENUE, collect_observed, decide_admission, stable_host
    from paper_scenario_catalog import inventory

ROOT = Path(__file__).resolve().parents[1]
TIMEFRAMES = {"1m": 60, "3m": 180, "5m": 300, "15m": 900}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def build_plan(root):
    root = Path(root)
    catalog = inventory(root)
    allowed_path = root / "data" / "v15_best_first.json"
    allowed_bytes = allowed_path.read_bytes()
    allowed = json.loads(allowed_bytes)["allowed"]
    if not isinstance(allowed, list) or any(not isinstance(value, str) for value in allowed):
        raise ValueError("allowed universe must be a list of sym_side strings")
    database = root / "data" / "hourly_reconfig" / "per_sym_store.db"
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    groups, missing, counts = [], [], Counter()
    try:
        connection.execute("BEGIN")
        for sym_side in sorted(set(allowed)):
            symbol, side = sym_side.rsplit("_", 1)
            row = connection.execute("SELECT cat_side,updated_at,full_config_json FROM per_sym_active WHERE sym_side=?", (sym_side,)).fetchone()
            if not row:
                missing.append({"sym_side": sym_side, "reason": "NO_ACTIVE_SETTINGS"})
                continue
            cat_side, updated_at, raw = row
            venue = {"CRYPTO_LONG": "crypto", "CRYPTO_SHORT": "crypto", "STOCKS_LONG": "stocks", "STOCKS_SHORT": "stocks"}.get(cat_side)
            if venue is None or cat_side.rsplit("_", 1)[1] != side:
                missing.append({"sym_side": sym_side, "reason": "INVALID_CAT_SIDE"})
                continue
            settings = json.loads(raw)
            if not isinstance(settings, dict) or not settings:
                missing.append({"sym_side": sym_side, "reason": "EMPTY_ACTIVE_SETTINGS"})
                continue
            selected = [item for item in catalog["scenarios"] if item["venue"] == venue and item["side"] == side]
            host = stable_host(venue, symbol, side)
            group = {"sym_side": sym_side, "venue": venue, "side": side, "host": host, "settings_updated_at": updated_at, "settings_sha256": digest(settings), "scenario_ids": [item["scenario_id"] for item in selected], "scenario_count": len(selected), "status": "AWAITING_REAL_MANAGER_ADAPTER"}
            group["epoch_id"] = digest({"sym_side": sym_side, "settings": group["settings_sha256"], "sources": catalog["file_sha256"]})
            groups.append(group)
            counts[host] += len(selected)
    finally:
        connection.close()
    return {"schema_version": 1, "planned_at": utc_now(), "status": "PLAN_ONLY", "running_scenarios": 0, "promotion_enabled": False, "catalog": catalog, "universe_sha256": hashlib.sha256(allowed_bytes).hexdigest(), "universe_source": str(allowed_path), "settings_source": str(database), "groups": groups, "uncovered": missing, "planned_scenarios_by_host": dict(counts), "requirements": ["Real ez_manage/tradier_manage incremental lifecycle adapter with independent durable state for each scenario", "Paper execution sink; no broker credentials or live-store writes", "Causal 1m/3m/5m inputs with explicit bar-close times and field availability", "Resolve candidate values from venue/side templates and effective per-symbol settings; catalog literals are not authoritative defaults", "Portfolio and cross-symbol switches require account-level scenario books, not isolated symbol experiments", "Baseline and candidate consume identical input sequence; missing or delayed frames invalidate the paired interval", "Persist settings and code epochs; never merge old observations into a new configuration", "Measure adapter memory, sustained throughput and backtest interference before deployment", "Promotion waits for exact live/vector parity, verified forward lifecycles, costs and required evidence floors"]}

def _epoch(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = float(value)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp must include timezone")
        value = parsed.timestamp()
    else:
        raise ValueError("missing timestamp")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("invalid timestamp")
    return value

def audit_snapshot(path, symbols, now, max_bytes=32 * 1024**2):
    path = Path(path)
    before = path.stat()
    if before.st_size > max_bytes:
        raise ValueError("snapshot exceeds bounded reader size")
    with path.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    after = path.stat()
    if len(raw) > max_bytes or (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("snapshot changed during read")
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("expected symbol-keyed indicator snapshot")
    rows, counts = [], Counter()
    for symbol in sorted(set(symbols)):
        values = data.get(symbol)
        for timeframe, seconds in TIMEFRAMES.items():
            field = "timestamp_" + timeframe
            stamp = values.get(field) if isinstance(values, dict) else None
            try:
                age = now - _epoch(stamp)
                status = "FUTURE_TIMESTAMP" if age < 0 else ("STALE" if age > 2 * seconds else "TIMESTAMP_RECENT")
            except (ValueError, TypeError, OverflowError):
                status, age = "MISSING_OR_INVALID_TIMESTAMP", None
            rows.append({"symbol": symbol, "timeframe": timeframe, "field": field, "source_timestamp": stamp, "age_seconds": age, "status": status})
            counts[status] += 1
    return {"source": str(path), "source_sha256": hashlib.sha256(raw).hexdigest(), "observed_at": datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "status": "OBSERVATION_ONLY", "causal_inputs_verified": False, "note": "Recent field timestamps do not prove closed-bar causality or complete indicators. File mtime is not used as market-data time.", "counts": dict(counts), "rows": rows}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan")
    readiness = commands.add_parser("readiness")
    readiness.add_argument("--host", choices=("s1", "s2", "s5", "s6"), required=True, help="Allocation label; run this command on that host. Readings are local, not SSH.")
    readiness.add_argument("--venue", choices=tuple(HOSTS_BY_VENUE), required=True)
    readiness.add_argument("--snapshot", type=Path)
    readiness.add_argument("--symbol", action="append", default=[])
    args = parser.parse_args()
    if args.command == "plan":
        result = build_plan(args.root)
    else:
        result = {"observed_at": utc_now(), "allocation_host": args.host, "probe_machine": socket.gethostname(), "probe_scope": "local", "venue": args.venue, "resources": decide_admission(args.venue, args.host, collect_observed(args.root)), "execution_ready": False, "reason": "REAL_MANAGER_ADAPTER_NOT_IMPLEMENTED"}
        if args.snapshot:
            if not args.symbol:
                parser.error("--snapshot requires at least one --symbol")
            result["feed"] = audit_snapshot(args.snapshot, args.symbol, datetime.now(timezone.utc).timestamp())
    rendered = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        with args.output.open("x") as handle:
            handle.write(rendered)
    else:
        print(rendered, end="")

if __name__ == "__main__":
    main()
