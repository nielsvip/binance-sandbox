#!/usr/bin/env python3
"""Switch OFF live per-sym sides whose CURRENT book settings lose money on the last 30D (run by the operator — production deploy).

Evidence: data/reports/per_sym_recheck_20260929/book_eval_{stocks,crypto}.json — every live book entry re-evaluated on the last 30D
with the current authoritative engine (v12_quick 7383699d) and rebuilt NPZs. A side is switched off when gain <= 0 or it makes no trade.
Mechanism = the existing live gate (tradier_manage.py:379 / ez_manage.py:2976): acc_gain_pct <= 0 or '_NEG_BLOCK' in winning_tag
-> the side cannot open. Overrides are kept untouched; a later positive promotion rewrites the entry and re-enables it.
Sides that could not be evaluated (NPZ problems) are NOT touched — they are reported only.

Usage: python3 tools/switch_off_negative_per_sym_20260929.py [--stocks] [--crypto] [--dry-run]
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "data" / "reports" / "per_sym_recheck_20260929"
BOOKS = {"stocks": ROOT / "data/hourly_reconfig/per_sym_active_config_stocks.json", "crypto": ROOT / "data/hourly_reconfig/per_sym_active_config.json"}


def main():
    dry = "--dry-run" in sys.argv
    kinds = [k for k in ("stocks", "crypto") if f"--{k}" in sys.argv] or ["stocks", "crypto"]
    stamp = time.strftime("%Y%m%d%H%M")
    for kind in kinds:
        ev_path = EVID / f"book_eval_{kind}.json"
        if not ev_path.exists():
            print(f"{kind}: no evidence file {ev_path.name} — skipped")
            continue
        ev = json.loads(ev_path.read_text())
        book = json.loads(BOOKS[kind].read_text())
        off, skipped = [], []
        for ss, r in ev.items():
            g, t = r.get("gain"), r.get("trades") or 0
            if not isinstance(g, (int, float)):
                skipped.append(ss)
                continue
            if g > 0 and t > 0:
                continue
            e = book.get(ss)
            if not isinstance(e, dict):
                continue
            if str(e.get("winning_tag", "")).startswith(("v15_iso4_authoritative", "v15_365cycle_")):
                continue  # freshly promoted verified winner (iso4 / §58 365-cycle) — never switch off on the pre-promotion evaluation
            e["acc_gain_pct"] = round(float(g), 4)
            if "_NEG_BLOCK" not in str(e.get("winning_tag", "")):
                e["winning_tag"] = f"{e.get('winning_tag', '')}_NEG_BLOCK"
            e["neg_block_reason"] = f"30D recheck {time.strftime('%Y-%m-%d')} gain {g:+.2f}% trades {t} (current engine)"
            off.append((round(g, 2), ss))
        print(f"{kind}: switching OFF {len(off)} sides; {len(skipped)} unevaluable left untouched")
        for g, ss in sorted(off):
            print(f"  OFF {ss:18s} {g:+.2f}%")
        if off and not dry:
            shutil.copy2(BOOKS[kind], ROOT / "backups" / f"before_neg_switch_off_{stamp}_{BOOKS[kind].name}")
            tmp = BOOKS[kind].with_suffix(".json.tmp")
            tmp.write_text(json.dumps(book, indent=1, default=str))
            json.loads(tmp.read_text())
            os.replace(tmp, BOOKS[kind])
            print(f"  wrote {BOOKS[kind].name}; backup before_neg_switch_off_{stamp}_{BOOKS[kind].name}")


if __name__ == "__main__":
    main()
