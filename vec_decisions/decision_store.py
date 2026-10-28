"""Decision/intent journal vs broker-fill history — one place for the paths.

USER 2026-10-28: decisions are recorded under data/tradier/decisions/ (or data/decisions
for crypto), NEVER in history/. history/ holds only broker-confirmed trades (fills).
Readers that need the full picture (peak quantity, same-day buy counter, lifecycle log)
use read_paths(): history first, then decisions, so nothing they relied on disappears.
"""
from pathlib import Path


def decisions_dir(data_dir, account_key):
    return Path(data_dir) / "decisions" / str(account_key)


def decisions_file(data_dir, account_key, symbol_side):
    return decisions_dir(data_dir, account_key) / f"{symbol_side}.jsonl"


def history_file(data_dir, account_key, symbol_side):
    return Path(data_dir) / "history" / str(account_key) / f"{symbol_side}.jsonl"


def read_paths(data_dir, account_key, symbol_side):
    """history (fills) + decisions for one sym_side, existing files only."""
    return [p for p in (history_file(data_dir, account_key, symbol_side), decisions_file(data_dir, account_key, symbol_side)) if p.exists()]


def read_lines(data_dir, account_key, symbol_side):
    lines = []
    for p in read_paths(data_dir, account_key, symbol_side):
        try:
            lines.extend(p.read_text(errors="replace").splitlines())
        except Exception:
            continue
    return lines


def account_files(data_dir, account_key):
    """All sym_side jsonl files in history and decisions for one account (names may repeat)."""
    out = []
    for sub in ("history", "decisions"):
        d = Path(data_dir) / sub / str(account_key)
        if d.is_dir():
            out.extend(sorted(d.glob("*.jsonl")))
    return out
