#THIS IS THE ONLY ONE THAT WORKS!!!

import argparse

import json

import os

import sys

import time

from pathlib import Path

from typing import Any, Dict, List, Optional, Tuple

# Allow running from repo root or scripts/

BASE = Path(__file__).resolve().parents[1]

if str(BASE) not in sys.path:
    sys.path.append(str(BASE))

from config import Config

from utils import construct_position_key, orjson_default

PREFERRED_FIELD_ORDER = [ "symbol", "position_side", "entry_price", "mark_price", "positionAmt", "initial_quantity", "gain", "max_gain", "prev_gain", "max_quantity", "last_augmentation_amount", "last_augmentation_price", "last_augmentation_time", "last_reduction_amount", "last_reduction_price", "last_reduction_time", "max_positionSize", "opened_at", "last_updated", "last_signal", "realized_pnl", "unrealized_pnl", "was_reentered", "was_reduced", "prev_gain_last_updated", "augment_reason", "reduction_reason", "mark_price_last_updated", ]

def read_json(path: Path) -> Any:
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        return json.loads(text) if text.strip() else {}
    except Exception:
        return None

def atomic_write_pretty(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)

def backup_file(path: Path) -> Optional[Path]:
    try:
        if not path.exists():
            return None
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dst = path.parent / "backups" / f"{path.stem}.backup_{stamp}{path.suffix}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(path.read_bytes())
        return dst
    except Exception:
        return None

def norm_symbol(x: Any) -> str:
    s = str(x or "").strip().upper()
    if ":" in s:
        s = s.split(":", 1)[-1].strip().upper()
    if s.endswith("_LONG"):
        s = s[:-5]
    elif s.endswith("_SHORT"):
        s = s[:-6]
    return s

def coerce_float(v: Any) -> float:
    try:
        return float(v)
    except Exception:
        return 0.0

def parse_timestamp(d: Dict[str, Any]) -> Tuple[Optional[str], float]:
    # Prefer last_updated, then updated_at, then opened_at
    for key in ("last_updated", "updated_at", "opened_at"):
        val = d.get(key)
        if isinstance(val, str) and val:
            return key, 1.0
    return None, 0.0

def better_timestamp(d1: Dict[str, Any], d2: Dict[str, Any]) -> bool:
    # Return True if d1 is "newer" than d2 based on presence of preferred timestamp keys; if both present, compare lexically
    order = ("last_updated", "updated_at", "opened_at")
    for k in order:
        v1 = d1.get(k)
        v2 = d2.get(k)
        if v1 and not v2:
            return True
        if not v1 and v2:
            return False
        if v1 and v2:
            # Compare lexicographically (ISO strings sort correctly)
            try:
                return str(v1) > str(v2)
            except Exception:
                pass
    return False

def sanitize_entry(entry: Dict[str, Any], symbol: str, side: str) -> Dict[str, Any]:
    e = dict(entry or {})
    e["symbol"] = symbol
    e["position_side"] = side
    # Normalize numeric fields to floats where applicable
    for fld in ( "entry_price", "mark_price", "positionAmt", "initial_quantity", "gain", "max_gain", "prev_gain", "max_quantity", "last_augmentation_amount", "last_augmentation_price", "last_reduction_amount", "last_reduction_price", "max_positionSize", "realized_pnl", "unrealized_pnl", ):
        if fld in e:
            try:
                e[fld] = float(e.get(fld) or 0.0)
            except Exception:
                e[fld] = 0.0
    # Reorder fields
    ordered: Dict[str, Any] = {}
    for key in PREFERRED_FIELD_ORDER:
        if key in e:
            ordered[key] = e[key]
    # Include any remaining keys at the end to avoid data loss
    for k, v in e.items():
        if k not in ordered:
            ordered[k] = v
    return ordered

def collect_latest_for_side(acc_dir: Path, side: str, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    candidates: List[Path] = []
    stem = "long_positions" if side == "LONG" else "short_positions"
    base = acc_dir.parent if acc_dir.name in ("ang", "inf", "men", "flz", "fin") else acc_dir
    # Search in account backups directory
    backups = acc_dir / "backups"
    if backups.exists():
        candidates += sorted(backups.glob(f"{stem}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Search in parent backups directory
    parent_backups = base / "backups"
    if parent_backups.exists():
        candidates += sorted(parent_backups.glob(f"{acc_dir.name}/{stem}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        candidates += sorted(parent_backups.glob(f"**/{acc_dir.name}/{stem}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Search in machine subfolders
    for machine in ("gateway", "macbook", "server"):
        mdir = acc_dir / machine / "backups"
        if mdir.exists():
            candidates += sorted(mdir.glob(f"{stem}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        mdir2 = base / machine / acc_dir.name / "backups"
        if mdir2.exists():
            candidates += sorted(mdir2.glob(f"{stem}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        mdir3 = base / machine / "backups" / acc_dir.name
        if mdir3.exists():
            candidates += sorted(mdir3.glob(f"{stem}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Search in experimental folders
    exp_base = base / "experimental"
    if exp_base.exists():
        for exp_dir in exp_base.glob("*"):
            if exp_dir.is_dir():
                exp_backups = exp_dir / "backups"
                if exp_backups.exists():
                    candidates += sorted(exp_backups.glob(f"{stem}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                candidates += sorted(exp_dir.glob(f"{stem}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                if acc_dir.name in str(exp_dir):
                    sub_acc = exp_dir / acc_dir.name
                    if sub_acc.exists():
                        candidates += sorted(sub_acc.glob(f"{stem}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
                        sub_backups = sub_acc / "backups"
                        if sub_backups.exists():
                            candidates += sorted(sub_backups.glob(f"{stem}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Search for BACKUP_BEFORE_SAVE and WARNING_INCOMPLETE_BACKUP files in account directory
    candidates += sorted(acc_dir.glob(f"{stem}_BACKUP_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    candidates += sorted(acc_dir.glob(f"{stem}_WARNING_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Search recursively in base directory for any backup files
    if base.exists():
        candidates += sorted(base.glob(f"**/{acc_dir.name}/**/{stem}*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Finally, consider the current main file as a candidate
    main_file = acc_dir / (f"{stem}.json")
    if main_file.exists():
        candidates.append(main_file)
    # Remove duplicates while preserving order (newest first)
    seen = set()
    unique_candidates = []
    for path in candidates:
        try:
            real_path = path.resolve()
            if real_path not in seen:
                seen.add(real_path)
                unique_candidates.append(path)
        except:
            continue
    # Iterate candidates newest first; keep the newest per symbol
    for path in unique_candidates:
        try:
            data = read_json(path)
            if not isinstance(data, dict) or not data:
                continue
            for k, v in data.items():
                if not isinstance(v, dict):
                    continue
                # Handle both position_key format (account:symbol_SIDE) and symbol format (symbol)
                pk_upper = str(k).upper()
                if ":" in pk_upper and ("_LONG" in pk_upper or "_SHORT" in pk_upper):
                    # Position key format: extract symbol
                    sym = norm_symbol(k)
                else:
                    # Symbol format: use key as symbol
                    sym = norm_symbol(k or v.get("symbol"))
                if not sym or sym not in symbols:
                    continue
                candidate = sanitize_entry(v, sym, side)
                if sym not in result:
                    result[sym] = candidate
                else:
                    if better_timestamp(candidate, result[sym]):
                        result[sym] = candidate
        except Exception as e:
            print(f"[warn] Failed to read {path}: {e}")
            continue
    return result

def restore_from_backups(dry_run: bool = False, accounts: Optional[List[str]] = None, sides: Optional[List[str]] = None) -> int:
    cfg = Config()
    base = Path(getattr(cfg, "BASE_PATH", str(BASE)))
    # Load symbols
    symbols_path = Path(getattr(cfg, "SYMBOLS_FILE", base / "symbols.json"))
    symbols_data = read_json(symbols_path)
    symbols: List[str] = []
    if isinstance(symbols_data, list):
        symbols = [norm_symbol(s) for s in symbols_data if s]
    elif isinstance(symbols_data, dict) and "symbols" in symbols_data:
        symbols = [norm_symbol(s) for s in symbols_data["symbols"] if s]
    symbols = sorted({s for s in symbols if s})
    if not symbols:
        print("No symbols found in symbols.json; aborting.")
        return 2
    accs = accounts if accounts else list(getattr(cfg, "ACCOUNT_KEYS", []))
    target_sides = [s.upper() for s in (sides if sides else ["LONG", "SHORT"])]
    total_written = 0
    for account in accs:
        acc_dir = base / account
        if not acc_dir.exists():
            print(f"[warn] account dir missing: {acc_dir}")
            continue
        for side in target_sides:
            latest_by_symbol = collect_latest_for_side(acc_dir, side, symbols)
            # Build output dict with position_key format (account:symbol_SIDE) - CRITICAL for system to read correctly
            output: Dict[str, Any] = {}
            for sym in symbols:
                if sym in latest_by_symbol:
                    position_key = construct_position_key(account, sym, side)
                    output[position_key] = latest_by_symbol[sym]
            # CRITICAL: Filter out any symbols NOT in symbols.json (in case backups have old symbols)
            filtered_output = {}
            for pk, pos_data in output.items():
                # Extract symbol from position_key to verify it's in symbols.json
                pk_upper = pk.upper()
                sym_from_key = pk_upper.split(":")[-1].replace("_LONG", "").replace("_SHORT", "") if ":" in pk_upper else pk_upper.replace("_LONG", "").replace("_SHORT", "")
                if sym_from_key in symbols:
                    filtered_output[pk] = pos_data
            missing_count = len(symbols) - len(filtered_output)
            if missing_count > 0:
                print(f"[warn] {account} {side}: {missing_count} symbols not found in any backup (expected {len(symbols)}, found {len(filtered_output)})")
            target_file = acc_dir / ("long_positions.json" if side == "LONG" else "short_positions.json")
            if dry_run:
                print(f"[dry] {account} {side} would write {len(filtered_output)} positions with position_key format (filtered to symbols.json) -> {target_file}")
                continue
            backup_file(target_file)
            atomic_write_pretty(target_file, filtered_output)
            print(f"[write] {account} {side} wrote {len(filtered_output)} positions with position_key format (filtered to symbols.json) -> {target_file}")
            total_written += 1
    print(f"Done. files_written={total_written}")
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description="Restore latest positions per symbol from backups for each account and side.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write files; only report.")
    parser.add_argument("--accounts", nargs="*", default=None, help="Limit to specific accounts.")
    parser.add_argument("--sides", nargs="*", default=None, help="Limit to LONG/SHORT.")
    args = parser.parse_args()
    return restore_from_backups(dry_run=bool(args.dry_run), accounts=args.accounts, sides=args.sides)

if __name__ == "__main__":
    raise SystemExit(main())
