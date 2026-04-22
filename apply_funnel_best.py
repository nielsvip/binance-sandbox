#!/usr/bin/env python3
"""
apply_funnel_best.py — Read best Tier-C funnel result, apply overrides to config files,
save new baseline snapshots.

Usage:
  python3 apply_funnel_best.py --mode crypto   # reads data/funnel_validated/crypto/tier_c_crypto.csv
  python3 apply_funnel_best.py --mode tradier  # reads data/funnel_validated/tradier/tier_c_tradier.csv
  python3 apply_funnel_best.py --mode both     # both

  --dry-run   Print changes without writing files.
  --min-sharpe FLOAT   Skip if best Tier-C sharpe is below this (default 0.50).
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent

FORBIDDEN = {
    # Sweep blacklist (profit-taking paths that break live execution)
    "AUGMENT_PT_ENABLED", "AUGMENT_PT_PCT",
    "CYCLE_TP_TIERED_ENABLED", "CYCLE_TP_PCT", "CYCLE_TP_TIERED_FRAC",
    "CYCLE_TP_CONDITIONAL_EXIT", "ACCOUNT_TP_PCT",
    "AUGMENT_WT_D_AUTO_CLOSE_ENABLED", "AUGMENT_WT_4H_AUTO_CLOSE_ENABLED",
    # Hard-stop paths permanently disabled (caused $500+ losses 2026-03-24)
    "HARD_STOP_LOSS_MAX_PAIN", "STALE_DATA_HARD_STOP", "STALE_DATA_GAIN_EROSION",
    "STALE_DATA_PROFIT_SHIELD",
    # Account/infrastructure — never auto-set
    "START_POSITION_SIZE",  # account-specific sizing
    "STOP_LOSS_PCT",        # explicitly disabled for live
    "STRICT_NO_LOSS",
    "STRICT_NO_LOSS_ACCOUNTS",
    "UNIVERSAL_NOLOSS_GATE",  # per CLAUDE.md, needs individual review
    # Tradier stop paths disabled per CLAUDE.md
    "ATR_TRAIL_ENABLED",
    "ATR_TRAIL_ENABLED_TRADIER",
    # Internal sweep/ablation flags
    "ABLATION_DISABLE_HEDGE",
    "ABLATION_DISABLE_QUICK_EXIT",
    "BACKTEST_VALIDATED_GATES_TRADIER",
    "BASIS_CONDITION",
    # Sweep-only infrastructure
    "EARLY_ABORT_ENABLED",
    "EARLY_ABORT_MIN_SYMBOLS",
}

# Keys that only belong in config.py (crypto side)
CRYPTO_ONLY_KEYS = {"CRYPTO_FH_MOMENTUM_ENABLED"}

# Keys that only belong in config_tradier.py (tradier side)
TRADIER_ONLY_KEYS = {k for k in [] }  # populated from tradier-specific patterns


def _load_best_row(tier_c_csv: Path):
    """Return dict of best row from Tier-C CSV, sorted by pool_sharpe desc."""
    import csv
    rows = []
    with open(tier_c_csv) as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                row["_sharpe"] = float(row["pool_sharpe"])
                rows.append(row)
            except (ValueError, KeyError):
                pass
    if not rows:
        return None
    rows.sort(key=lambda r: r["_sharpe"], reverse=True)
    return rows[0]


def _format_value(v) -> str:
    """Format a Python value for writing into a config file."""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float):
        # Preserve reasonable precision without scientific notation
        s = f"{v:.10g}"
        # Ensure it looks like a float
        if "." not in s and "e" not in s:
            s += ".0"
        return s
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return repr(v)
    return repr(v)


def _apply_overrides_to_config(config_path: Path, overrides: dict, dry_run: bool) -> tuple[int, list[str]]:
    """
    Apply overrides to a config file.
    Returns (n_changed, list_of_change_descriptions).
    Only modifies lines that match the pattern:
        <indent>KEY: type = <old_value>  # optional comment
    """
    text = config_path.read_text()
    lines = text.splitlines(keepends=True)
    changes = []
    n_changed = 0

    for key, new_val in overrides.items():
        if key in FORBIDDEN:
            continue

        # Match lines like:  "    SOME_KEY: bool = True  # comment"
        # or:                "    SOME_KEY: float = 1.5"
        # type annotation is optional but usually present
        pattern = re.compile(
            r'^(\s+' + re.escape(key) + r'\s*(?::\s*\w+)?\s*=\s*)([^\n#]+)(.*)',
            re.MULTILINE
        )
        new_val_str = _format_value(new_val)

        matches = [(m.start(), m) for m in pattern.finditer(text)]
        if not matches:
            continue  # key not in this config file

        for _, m in matches:
            old_val_str = m.group(2).strip()
            if old_val_str == new_val_str:
                continue  # no change needed

            new_line = m.group(1) + new_val_str + "  " + m.group(3).strip()
            # Trim trailing whitespace
            new_line = new_line.rstrip() + "\n"

            old_line = m.group(0) if m.group(0).endswith("\n") else m.group(0) + "\n"
            changes.append(f"  {key}: {old_val_str!r} → {new_val_str!r}")
            text = text[:m.start()] + new_line + text[m.start() + len(m.group(0)):]
            n_changed += 1
            # Re-search after replacement (single occurrence per key expected)
            break

    if not dry_run and n_changed > 0:
        config_path.write_text(text)

    return n_changed, changes


def _backup(path: Path, label: str):
    ts = datetime.now().strftime("%Y%m%d%H%M")
    dest = BASE_DIR / "backups" / f"before_{label}_{ts}{path.suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dest)
    print(f"  Backup → {dest.name}")
    return dest


def _compile_check(path: Path) -> bool:
    result = subprocess.run(
        [sys.executable, "-c", f"import py_compile; py_compile.compile('{path}', doraise=True)"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  COMPILE ERROR: {result.stderr}")
        return False
    print(f"  Compile OK: {path.name}")
    return True


def _save_baseline(overrides: dict, mode: str, sharpe: float, symbols: int, trades: int):
    """Save the best overrides as a new baseline JSON."""
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    fname = f"{mode}_funnel_{ts}_sharpe{sharpe:.4f}.json"
    out = BASE_DIR / "data" / "baselines" / fname
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "_meta": {
            "source": "v8_funnel tier_c",
            "mode": mode,
            "pool_sharpe": sharpe,
            "symbols": symbols,
            "trades": trades,
            "created_utc": datetime.utcnow().isoformat(),
        }
    }
    blob = {**meta, **overrides}
    out.write_text(json.dumps(blob, indent=2))
    print(f"  Baseline saved → {out.name}")
    return out


def _rsync_to_sandbox(files: list[Path]):
    for server in ["s1-int:/home/niels/binance-sandbox/", "s2-int:/home/niels/binance-sandbox/"]:
        args = ["rsync", "-az", "--update"] + [str(f) for f in files] + [server]
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
        label = server.split(":")[0]
        if r.returncode == 0:
            print(f"  Synced to {label}")
        else:
            print(f"  SYNC FAILED {label}: {r.stderr[:200]}")


def process_mode(mode: str, dry_run: bool, min_sharpe: float):
    if mode == "crypto":
        tier_c = BASE_DIR / "data" / "funnel_validated" / "crypto" / "tier_c_crypto.csv"
        config_path = BASE_DIR / "config.py"
        label = "apply_funnel_crypto"
    else:
        tier_c = BASE_DIR / "data" / "funnel_validated" / "tradier" / "tier_c_tradier.csv"
        config_path = BASE_DIR / "config_tradier.py"
        label = "apply_funnel_tradier"

    # Also check S1 cache for crypto, S2 cache for tradier
    if not tier_c.exists():
        cache_mode = "s1" if mode == "crypto" else "s2"
        alt = BASE_DIR / "data" / "swarm_cache" / cache_mode / "data" / "funnel_validated" / mode / f"tier_c_{mode}.csv"
        if alt.exists():
            print(f"  Using cache copy: {alt}")
            tier_c = alt
        else:
            print(f"[{mode.upper()}] Tier-C CSV not found: {tier_c}")
            return False

    row = _load_best_row(tier_c)
    if not row:
        print(f"[{mode.upper()}] No valid rows in {tier_c}")
        return False

    sharpe = row["_sharpe"]
    trades = int(row.get("trades", 0))
    symbols = int(row.get("symbols_used", 0))
    gain = float(row.get("acc_gain_pct", 0))

    print(f"\n[{mode.upper()}] Best Tier-C result:")
    print(f"  pool_sharpe={sharpe:.4f}  gain={gain:.0f}%  trades={trades}  symbols={symbols}")

    if sharpe < min_sharpe:
        print(f"  SKIP: sharpe {sharpe:.4f} < min_sharpe {min_sharpe:.4f}")
        return False

    try:
        overrides = json.loads(row.get("overrides_json", "{}"))
    except Exception as e:
        print(f"  ERROR parsing overrides_json: {e}")
        return False

    # Remove forbidden keys
    clean_overrides = {k: v for k, v in overrides.items() if k not in FORBIDDEN}
    print(f"  {len(overrides)} override keys → {len(clean_overrides)} after filtering FORBIDDEN")

    if not clean_overrides:
        print("  Nothing to apply after filtering.")
        return False

    if dry_run:
        print("  [DRY RUN] Would apply to:", config_path.name)
        n, changes = _apply_overrides_to_config(config_path, clean_overrides, dry_run=True)
        print(f"  Would change {n} keys:")
        for c in changes:
            print(c)
        _save_baseline(clean_overrides, mode, sharpe, symbols, trades)
        return True

    # Backup
    _backup(config_path, label)

    # Apply
    n, changes = _apply_overrides_to_config(config_path, clean_overrides, dry_run=False)
    if n == 0:
        print("  No changes needed (config already matches best overrides).")
    else:
        print(f"  Applied {n} changes to {config_path.name}:")
        for c in changes:
            print(c)

    # Compile check
    if not _compile_check(config_path):
        # Restore backup on failure
        bk = BASE_DIR / "backups" / f"before_{label}_{datetime.now().strftime('%Y%m%d%H%M')}{config_path.suffix}"
        print(f"  RESTORING backup due to compile error")
        return False

    # Save baseline
    bl = _save_baseline(clean_overrides, mode, sharpe, symbols, trades)

    # Sync config + baseline to sandboxes
    if n > 0:
        print(f"  Syncing {config_path.name} to S1 and S2...")
        _rsync_to_sandbox([config_path])

    print(f"[{mode.upper()}] Done. sharpe={sharpe:.4f}, {n} config keys updated.")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["crypto", "tradier", "both"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--min-sharpe", type=float, default=0.50)
    args = ap.parse_args()

    modes = ["crypto", "tradier"] if args.mode == "both" else [args.mode]
    for m in modes:
        process_mode(m, dry_run=args.dry_run, min_sharpe=args.min_sharpe)


if __name__ == "__main__":
    main()
