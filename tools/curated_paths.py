"""curated_paths — minimal stub for v12 pilot runner.
Original file was empty (0 bytes) on 2026-09-01; backtest_v12_engine requires require_allowlist().
This stub reads data/reports/ALL_PATHS_ALLOWLIST.csv and returns the allowed field set.
If CSV is missing/unreadable, returns empty set (caller will raise, which is correct per Bible
curated contract: no allowlist == no pilot)."""
from pathlib import Path
import csv

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_CSV = ROOT / "data" / "reports" / "ALL_PATHS_ALLOWLIST.csv"

def require_allowlist() -> set[str]:
    if not ALLOWLIST_CSV.exists():
        raise FileNotFoundError(f"allowlist missing {ALLOWLIST_CSV}")
    allowed: set[str] = set()
    with ALLOWLIST_CSV.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            f = (row.get("field") or "").strip()
            if f:
                allowed.add(f)
    if not allowed:
        raise ValueError("allowlist empty")
    return allowed

def is_allowed(field: str) -> bool:
    try:
        return field in require_allowlist()
    except Exception:
        return False
