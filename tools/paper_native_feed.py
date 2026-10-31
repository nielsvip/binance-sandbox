#!/usr/bin/env python3
"""Bounded, read-only native indicator snapshot transport for the paper lab."""

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


MAX_BYTES = 32 * 1024 * 1024
RSYNC_TIMEOUT_S = 20
SSH_CONNECT_TIMEOUT_S = 5
SOURCE_BY_HOST = {
    "s1": ("crypto", "10.0.0.4", "/home/niels/binance/ez_s2_stack/data/latest_market_data.json"),
    "s2": ("crypto", "10.0.0.4", "/home/niels/binance/ez_s2_stack/data/latest_market_data.json"),
    "s5": ("stocks", "10.0.0.3", "/home/niels/binance/data/live_failover/mac_state/root/data/tradier/tradier_indicators_latest.json"),
    "s6": ("stocks", "10.0.0.3", "/home/niels/binance/data/live_failover/mac_state/root/data/tradier/tradier_indicators_latest.json"),
}
TIMEFRAMES = ("1m", "3m", "5m", "15m", "1h", "4h", "D", "W", "M")
SYMBOL_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,40}$")
TIMEFRAME_PROVENANCE = {
    "crypto": {
        "1m": {"status": "KNOWN_ALIAS", "source_field": "timestamp_3m", "reason": "ez_indicators IndicatorCalculator assigns timestamp_1m from its 3m calculation", "producer_source_path": "/home/niels/binance/ez_s2_stack/ez_indicators.py", "producer_source_sha256": "f5a013f5a7461539b75fd761fef13d4a74b961052308083920ba439bc31e7e9e"},
        "3m": {"status": "NATIVE_SOURCE", "source_field": "timestamp_3m", "producer": "ez_indicators IndicatorCalculator", "producer_source_path": "/home/niels/binance/ez_s2_stack/ez_indicators.py", "producer_source_sha256": "f5a013f5a7461539b75fd761fef13d4a74b961052308083920ba439bc31e7e9e"},
        "5m": {"status": "UNSUPPORTED", "source_field": None, "reason": "ez_indicators TIMEFRAMES has no 5m calculation", "producer_source_path": "/home/niels/binance/ez_s2_stack/ez_indicators.py", "producer_source_sha256": "f5a013f5a7461539b75fd761fef13d4a74b961052308083920ba439bc31e7e9e"},
    },
    "stocks": {
        "1m": {"status": "WALLCLOCK_HEARTBEAT", "source_field": None, "reason": "tradier_indicators stamps timestamp_1m with utc_now", "producer_source_path": "/home/niels/binance/tradier_indicators.py", "producer_source_sha256": "1e582f05e1cd33cd43d0e659591363c9cee0d8af29276bff9c2767790a4d1811"},
        "3m": {"status": "NATIVE_SOURCE", "source_field": "timestamp_3m", "producer": "tradier_indicators IndicatorCalculator", "producer_source_path": "/home/niels/binance/tradier_indicators.py", "producer_source_sha256": "1e582f05e1cd33cd43d0e659591363c9cee0d8af29276bff9c2767790a4d1811"},
        "5m": {"status": "NATIVE_SOURCE", "source_field": "timestamp_5m", "producer": "tradier_indicators IndicatorCalculator", "producer_source_path": "/home/niels/binance/tradier_indicators.py", "producer_source_sha256": "1e582f05e1cd33cd43d0e659591363c9cee0d8af29276bff9c2767790a4d1811"},
    },
}


def _utc_iso(epoch):
    return dt.datetime.fromtimestamp(float(epoch), dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(dt.timezone.utc)


def _reject_json_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def validate_snapshot_bytes(raw):
    """Parse bounded JSON and require a non-empty mapping of symbols to records."""
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("snapshot must be bytes")
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("snapshot size is empty or exceeds 32 MiB")
    try:
        snapshot = json.loads(raw, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("snapshot is not valid JSON") from exc
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("snapshot root must be a non-empty object")
    symbols = 0
    for key, value in snapshot.items():
        if not isinstance(key, str):
            raise ValueError("snapshot contains a non-string key")
        if key.startswith("_"):
            continue
        if not SYMBOL_KEY_RE.fullmatch(key) or not isinstance(value, dict):
            raise ValueError(f"malformed symbol record: {key[:40]}")
        symbols += 1
    if symbols == 0:
        raise ValueError("snapshot contains no symbol records")
    return snapshot


def summarize_timestamps(snapshot, captured_epoch):
    """Summarize source timestamps without changing fields or assigning freshness."""
    captured_epoch = float(captured_epoch)
    if not math.isfinite(captured_epoch) or captured_epoch < 0:
        raise ValueError("captured_epoch must be finite and non-negative")
    captured = dt.datetime.fromtimestamp(captured_epoch, dt.timezone.utc)
    result = {}
    for timeframe in TIMEFRAMES:
        parsed = []
        invalid = 0
        field = f"timestamp_{timeframe}"
        for symbol, row in snapshot.items():
            if symbol.startswith("_") or not isinstance(row, dict) or field not in row:
                continue
            stamp = _parse_timestamp(row[field])
            if stamp is None or stamp > captured:
                invalid += 1
                continue
            parsed.append(stamp)
        ages = sorted((captured - stamp).total_seconds() for stamp in parsed)
        median_age = ages[len(ages) // 2] if ages else None
        result[timeframe] = {
            "count": len(parsed),
            "invalid_count": invalid,
            "min_source_timestamp": min(parsed).isoformat(timespec="seconds").replace("+00:00", "Z") if parsed else None,
            "max_source_timestamp": max(parsed).isoformat(timespec="seconds").replace("+00:00", "Z") if parsed else None,
            "median_content_age_s": round(median_age, 3) if median_age is not None else None,
            "max_content_age_s": round(ages[-1], 3) if ages else None,
        }
    return result


def _output_dir(base):
    base_path = Path(base).expanduser().resolve()
    if not base_path.is_absolute() or not base_path.is_dir():
        raise ValueError("base must be an existing directory")
    output_dir = base_path / "data" / "paper_forward_lab" / "feed"
    current = base_path
    for component in ("data", "paper_forward_lab", "feed"):
        current = current / component
        if current.is_symlink():
            raise ValueError("paper feed output path may not contain symlinks")
    resolved = output_dir.resolve()
    if not resolved.is_relative_to(base_path):
        raise ValueError("paper feed output escapes base")
    return base_path, output_dir


def source_for(host):
    host = str(host).strip().lower()
    if host not in SOURCE_BY_HOST:
        raise ValueError("host must be one of s1, s2, s5, s6")
    venue, address, source_path = SOURCE_BY_HOST[host]
    return {"host": host, "venue": venue, "source_host": address, "source_path": source_path}


def capture_once(host, base, runner=subprocess.run, clock=time.time):
    """Fetch one native snapshot, validate it, and atomically publish snapshot then receipt."""
    source = source_for(host)
    base_path, output_dir = _output_dir(base)
    output_dir.mkdir(parents=True, exist_ok=True)
    resolved_output = output_dir.resolve()
    if not resolved_output.is_relative_to(base_path):
        raise ValueError("paper feed output escapes base")
    venue = source["venue"]
    target = resolved_output / f"{venue}.json"
    receipt_target = resolved_output / f"{venue}.receipt.json"
    remote = f"niels@{source['source_host']}:{source['source_path']}"
    with tempfile.TemporaryDirectory(prefix=".capture-", dir=resolved_output) as stage_name:
        stage = Path(stage_name)
        source_file = stage / "source.json"
        cmd = ["rsync", "-a", "--max-size=32M", f"--timeout={RSYNC_TIMEOUT_S}"]
        if source["host"] == "s2":
            cmd.extend((source["source_path"], str(source_file)))
        else:
            rsync_ssh = f"ssh -o BatchMode=yes -o ConnectTimeout={SSH_CONNECT_TIMEOUT_S}"
            cmd.extend(("-e", rsync_ssh, remote, str(source_file)))
        try:
            result = runner(cmd, capture_output=True, text=True, timeout=RSYNC_TIMEOUT_S)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("native snapshot transfer timed out") from exc
        if result.returncode != 0:
            raise RuntimeError(f"native snapshot transfer failed (rc={result.returncode})")
        try:
            size = source_file.stat().st_size
        except OSError as exc:
            raise RuntimeError("transfer completed without a snapshot file") from exc
        if size <= 0 or size > MAX_BYTES:
            raise ValueError("snapshot size is empty or exceeds 32 MiB")
        with source_file.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("snapshot exceeds 32 MiB")
        if len(raw) != size:
            raise RuntimeError("snapshot changed while being read")
        snapshot = validate_snapshot_bytes(raw)
        captured_epoch = float(clock())
        if not math.isfinite(captured_epoch) or captured_epoch < 0:
            raise ValueError("capture clock returned an invalid time")
        digest = hashlib.sha256(raw).hexdigest()
        receipt = {
            "captured_at_utc": _utc_iso(captured_epoch),
            "host": source["host"],
            "venue": venue,
            "source_host": source["source_host"],
            "source_path": source["source_path"],
            "source_sha256": digest,
            "source_bytes": len(raw),
            "symbol_count": sum(1 for key in snapshot if not key.startswith("_")),
            "timeframes": summarize_timestamps(snapshot, captured_epoch),
            "verified_native_1m": False,
            "timeframe_provenance": TIMEFRAME_PROVENANCE[venue],
        }
        snapshot_stage = stage / f"{venue}.json"
        shutil.copyfile(source_file, snapshot_stage)
        if hashlib.sha256(snapshot_stage.read_bytes()).hexdigest() != digest:
            raise RuntimeError("staged snapshot digest mismatch")
        receipt_stage = stage / f"{venue}.receipt.json"
        receipt_stage.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(snapshot_stage, target)
        published_digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if published_digest != digest:
            raise RuntimeError("published snapshot digest mismatch")
        os.replace(receipt_stage, receipt_target)
        published_receipt = json.loads(receipt_target.read_text(encoding="utf-8"))
        if published_receipt.get("source_sha256") != hashlib.sha256(target.read_bytes()).hexdigest():
            raise RuntimeError("published receipt and snapshot digest mismatch")
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, choices=tuple(SOURCE_BY_HOST))
    parser.add_argument("--base", required=True, help="explicit existing ~/binance-sandbox base directory")
    args = parser.parse_args(argv)
    try:
        receipt = capture_once(args.host, args.base)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), flush=True)
        return 1
    print(json.dumps({"ok": True, "host": receipt["host"], "venue": receipt["venue"],
                      "captured_at_utc": receipt["captured_at_utc"],
                      "source_sha256": receipt["source_sha256"],
                      "symbols": receipt["symbol_count"]}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
