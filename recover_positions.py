#!/usr/bin/env python3
"""
recover_positions.py — Full position recovery from best available backups.
Restores ALL destroyed fields — both active positions (positionAmt>0) and
closed template positions that had historical data (max_gain, opened_at, etc).

DRY RUN by default. Pass --apply to actually write.
"""
import json
import os
import sys
import glob
from pathlib import Path
from datetime import datetime, timezone

BASE_PATH = Path("/home/niels/binance")
ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
RESTORE_FIELDS = [
    "entry_price", "positionAmt", "initial_quantity", "gain", "max_gain",
    "max_quantity", "opened_at", "last_augmentation_amount", "last_augmentation_price",
    "last_augmentation_time", "last_reduction_amount", "last_reduction_price",
    "last_reduction_time", "max_positionSize", "was_reentered", "was_reduced",
    "realized_pnl", "unrealized_pnl", "prev_gain", "augment_reason", "reduction_reason",
    "is_reduced", "reduced_at", "prev_gain_last_updated",
]


def load_all_backups(acct, side):
    """Load ALL backup files and merge — newest wins per field."""
    backup_dir = BASE_PATH / acct / "backups"
    if not backup_dir.exists():
        return {}
    patterns = [f"{side}_positions_backup_*.json", f"{side}_positions.json_backup_*.json"]
    all_files = []
    for pat in patterns:
        all_files.extend(glob.glob(str(backup_dir / pat)))
    all_files.sort()  # oldest first — so newest overwrites
    merged = {}
    for f in all_files:
        try:
            with open(f, "r") as fh:
                data = json.load(fh)
            for pk, pos_data in data.items():
                if not isinstance(pos_data, dict):
                    continue
                if pk not in merged:
                    merged[pk] = {}
                for field in RESTORE_FIELDS:
                    val = pos_data.get(field)
                    if val is not None and val != 0 and val != 0.0 and val != "":
                        merged[pk][field] = val
        except Exception:
            continue
    return merged


def position_has_real_data(pos_data):
    """Check if backup position has any meaningful historical data."""
    amt = abs(float(pos_data.get("positionAmt", 0) or 0))
    ep = float(pos_data.get("entry_price", 0) or 0)
    mg = float(pos_data.get("max_gain", 0) or 0)
    iq = float(pos_data.get("initial_quantity", 0) or 0)
    oa = pos_data.get("opened_at")
    return amt > 0 or ep > 0 or mg != 0 or iq > 0 or oa is not None


def position_is_wiped(current_pos):
    """Check if a position has been wiped (all critical fields null/zero)."""
    ep = float(current_pos.get("entry_price", 0) or 0)
    iq = float(current_pos.get("initial_quantity", 0) or 0)
    mg = float(current_pos.get("max_gain", 0) or 0)
    mq = float(current_pos.get("max_quantity", 0) or 0)
    oa = current_pos.get("opened_at")
    return ep == 0 and iq == 0 and mg == 0 and mq == 0 and oa is None


def main():
    apply = "--apply" in sys.argv
    print("=" * 70)
    print(f"  {'APPLYING RECOVERY' if apply else 'DRY RUN'} — Full field restoration from all backups")
    print("=" * 70)

    total_restored = 0
    total_fields_fixed = 0

    for acct in ACCOUNTS:
        for side in ["long", "short"]:
            pos_file = BASE_PATH / acct / f"{side}_positions.json"
            if not pos_file.exists():
                continue
            with open(pos_file, "r") as f:
                current = json.load(f)

            backup_merged = load_all_backups(acct, side)
            if not backup_merged:
                print(f"  [{acct}] {side}: No backups found")
                continue

            restored_count = 0
            fields_fixed = 0
            changes = []

            for pk, curr_pos in current.items():
                if not isinstance(curr_pos, dict):
                    continue
                backup_pos = backup_merged.get(pk)
                if not backup_pos:
                    continue
                if not position_has_real_data(backup_pos):
                    continue

                # Case 1: Active position with missing fields
                curr_amt = abs(float(curr_pos.get("positionAmt", 0) or 0))
                curr_ep = float(curr_pos.get("entry_price", 0) or 0)

                # Case 2: Template position that was wiped but had history
                is_active = curr_amt > 0 or curr_ep > 0
                is_wiped = position_is_wiped(curr_pos)

                if not is_active and not is_wiped:
                    continue  # template with some data — leave it alone

                # Restore fields from backup
                pos_changes = []
                for field in RESTORE_FIELDS:
                    curr_val = curr_pos.get(field)
                    backup_val = backup_pos.get(field)
                    if backup_val is None:
                        continue
                    # Only restore if current is null/zero/empty AND backup has real data
                    curr_is_empty = (curr_val is None or curr_val == 0 or curr_val == 0.0 or curr_val == "")
                    backup_is_real = (backup_val is not None and backup_val != 0 and backup_val != 0.0 and backup_val != "")
                    if curr_is_empty and backup_is_real:
                        pos_changes.append((field, curr_val, backup_val))
                        if apply:
                            curr_pos[field] = backup_val

                if pos_changes:
                    restored_count += 1
                    fields_fixed += len(pos_changes)
                    tag = "ACTIVE" if is_active else "TEMPLATE"
                    changes.append(f"    [{tag}] {pk}: {len(pos_changes)} fields restored")
                    if len(changes) <= 5:  # show first 5
                        for field, old, new in pos_changes[:3]:
                            changes.append(f"      {field}: {old} -> {new}")
                        if len(pos_changes) > 3:
                            changes.append(f"      ... and {len(pos_changes)-3} more")

            if restored_count > 0:
                print(f"\n  [{acct}] {side}: {restored_count} positions restored, {fields_fixed} fields fixed")
                for c in changes[:20]:
                    print(c)
                if len(changes) > 20:
                    print(f"    ... and {len(changes)-20} more entries")

                if apply:
                    curr_pos_data = current  # already modified in place
                    backup_path = pos_file.parent / f"{pos_file.name}.pre_fullrecovery_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    with open(backup_path, "w") as f:
                        json.dump(current, f, indent=2)
                    with open(pos_file, "w") as f:
                        json.dump(current, f, indent=2)
                    print(f"    WRITTEN: {pos_file}")

                total_restored += restored_count
                total_fields_fixed += fields_fixed
            else:
                print(f"  [{acct}] {side}: all OK")

    print(f"\n{'=' * 70}")
    print(f"  Total: {total_restored} positions, {total_fields_fixed} fields restored")
    if not apply and total_restored > 0:
        print(f"  Run with --apply to write changes")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
