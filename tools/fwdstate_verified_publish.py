#!/usr/bin/env python3
"""Publish only forward states matching the current Mac SQL, engine, and NPZ bar."""
import ast
import hashlib
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "data" / "forward_state_inbox"
DEST = ROOT / "data" / "forward_state"
DB = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")


def _sha(full):
    raw = json.dumps(full, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _engine_ast_sha():
    tree = ast.parse((ROOT / "v12_quick_engine.py").read_text())
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


def main():
    now = time.time()
    expected = (int(now) // 900 - 1) * 900
    engine = _engine_ast_sha()
    counts = Counter()
    candidates = {}
    if not DB.is_file():
        print("REFUSED missing Mac per_sym_store.db", flush=True)
        return 2
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    try:
        for path in INBOX.glob("*/*.json"):
            try:
                state = json.loads(path.read_text())
                symbol_side = path.stem.upper()
                if state.get("sym_side") != symbol_side or state.get("ok") is not True or state.get("valid") is not True or state.get("audit_only") is not False:
                    counts["unverified_state"] += 1
                    continue
                if state.get("src") != "per_sym_store.active" or state.get("window_days") != 30 or state.get("engine_ast_sha256") != engine:
                    counts["wrong_source_or_engine"] += 1
                    continue
                tail = int(state.get("npz_tail") or 0)
                symbol = symbol_side.rsplit("_", 1)[0]
                if symbol.endswith(CRYPTO_SUFFIXES):
                    if tail != expected:
                        counts["wrong_crypto_bar"] += 1
                        continue
                elif tail <= 0 or not (0 <= now - tail <= 1800):
                    counts["stale_stock_bar"] += 1
                    continue
                row = con.execute("SELECT updated_at,full_config_json FROM per_sym_active WHERE sym_side=?", (symbol_side,)).fetchone()
                if not row or row[0] != state.get("settings_updated_at") or _sha(json.loads(row[1])) != state.get("settings_sha256"):
                    counts["settings_mismatch"] += 1
                    continue
                old = candidates.get(symbol_side)
                if old is None or (tail, state.get("ts", "")) > (int(old[0].get("npz_tail") or 0), old[0].get("ts", "")):
                    candidates[symbol_side] = (state, path)
            except Exception as exc:
                counts["unreadable_state"] += 1
                if counts["unreadable_state"] <= 3:
                    print(f"state_reject {path}: {type(exc).__name__}: {exc}", flush=True)
                    import traceback
                    traceback.print_exc()
        DEST.mkdir(parents=True, exist_ok=True)
        for symbol_side, (state, path) in candidates.items():
            target = DEST / f"{symbol_side}.json"
            try:
                if target.exists():
                    current = json.loads(target.read_text())
                    if current.get("npz_tail") == state.get("npz_tail") and current.get("settings_sha256") == state.get("settings_sha256") and current.get("engine_ast_sha256") == engine:
                        counts["already_current"] += 1
                        continue
                tmp = target.with_name(target.name + f".new.{os.getpid()}")
                tmp.write_text(json.dumps(state, indent=1))
                os.replace(tmp, target)
                counts["published"] += 1
            except Exception:
                counts["publish_error"] += 1
        expired = ROOT / "data" / "forward_state_expired" / time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))
        for target in DEST.glob("*.json"):
            try:
                state = json.loads(target.read_text())
                symbol_side = target.stem.upper()
                symbol = symbol_side.rsplit("_", 1)[0]
                tail = int(state.get("npz_tail") or 0)
                row = con.execute("SELECT updated_at,full_config_json FROM per_sym_active WHERE sym_side=?", (symbol_side,)).fetchone()
                current = bool(row and state.get("ok") is True and state.get("valid") is True and state.get("audit_only") is False
                               and state.get("src") == "per_sym_store.active" and state.get("engine_ast_sha256") == engine
                               and state.get("settings_updated_at") == row[0] and _sha(json.loads(row[1])) == state.get("settings_sha256"))
                if symbol.endswith(CRYPTO_SUFFIXES):
                    current = current and tail == expected
                else:
                    current = current and 0 <= now - tail <= 1800
                if not current:
                    expired.mkdir(parents=True, exist_ok=True)
                    target.replace(expired / target.name)
                    counts["expired_removed"] += 1
            except Exception:
                expired.mkdir(parents=True, exist_ok=True)
                target.replace(expired / target.name)
                counts["expired_removed"] += 1
    finally:
        con.close()
    print(json.dumps({"ts": time.strftime("%FT%TZ", time.gmtime(now)), "expected_crypto_tail": expected, "candidates": len(candidates), "counts": dict(counts)}, sort_keys=True), flush=True)
    return 0 if not counts["publish_error"] else 1


if __name__ == "__main__":
    sys.exit(main())
