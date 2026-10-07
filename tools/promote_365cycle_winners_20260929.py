#!/usr/bin/env python3
"""Promote BIBLE §58 cycle-verified winners to LIVE per-sym books (run by the operator — production deploy).

Evidence: data/reports/promotion_365cycle_20260929/passes_all.jsonl — per sym_side, the final set after the v15 30D sheet +
tools/v15_365_cycle.py, evaluated on engine v12_quick fd93da9a / v12_pilot a91005cf (honest gain, TIM<=80 / DD<=30 gates):
  * 30D valid AND positive, 365D valid AND positive, 365D slice covering >= 330 days of real NPZ history;
  * USER 2026-09-29: the new set replaces the book UNLESS the book itself passes both windows on the current engine/NPZ
    (valid, positive, >=10 trades 30D, >=80 trades 365D) AND is >= the new set on 365D. A book whose big 365D number
    comes with a negative/invalid 30D (OKE, VALE, XLE, AGI, BMNR, UEC, ...) is replaced — it does not reproduce on 30D.
CAVEAT: backtest_v12_engine (scalar) parity is still NOT passing — live will not track these backtests exactly.

Usage: python3 tools/promote_365cycle_winners_20260929.py [--dry-run]
Then restart the live managers (config is read at start; per-sym books are re-read on change).
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVID = ROOT / "data" / "reports" / "promotion_365cycle_20260929" / "passes_all.jsonl"
BOOKS = {"stocks": ROOT / "data/hourly_reconfig/per_sym_active_config_stocks.json", "crypto": ROOT / "data/hourly_reconfig/per_sym_active_config.json"}


def main():
    dry = "--dry-run" in sys.argv
    stamp = time.strftime("%Y%m%d%H%M")
    rows = [json.loads(l) for l in open(EVID) if l.strip()]
    for kind, path in BOOKS.items():
        book = json.loads(path.read_text())
        changed = 0
        for r in rows:
            ss = r["sym_side"]
            is_crypto = ss.split("_")[0].endswith(("USDT", "USDC", "USD1"))
            if (kind == "crypto") != is_crypto:
                continue
            b30, b365 = r["book30"], r["book365"]
            book_both_ok = bool(b30.get("valid")) and (b30.get("gain_pct") or 0) > 0 and int(b30.get("trades") or 0) >= 10 and bool(b365.get("valid")) and (b365.get("gain_pct") or 0) > 0 and int(b365.get("trades") or 0) >= 80
            if book_both_ok and (b365.get("gain_pct") or 0) >= (r["w365"]["gain_pct"] or 0):
                print(f"KEEP BOOK {ss}: book passes both windows (30D {b30['gain_pct']:+.2f} / 365D {b365['gain_pct']:+.2f}) and is >= new on 365D ({r['w365']['gain_pct']:+.2f})")
                continue
            w30, w365 = r["w30"], r["w365"]
            assert w30["valid"] and w365["valid"] and w30["gain_pct"] > 0 and w365["gain_pct"] > 0 and float(r["span_365_days"]) >= 330, f"{ss} evidence not a verified pass"
            assert int(w30.get("trades") or 0) >= 10 and int(w365.get("trades") or 0) >= 80, f"{ss} below USER trade floor (>=10/30D, >=80/365D)"
            old = book.get(ss, {})
            book[ss] = {"overrides": dict(r["overrides"]), "winning_tag": "v15_365cycle_20260929_BOTH_POS", "trades": int(w30.get("trades") or 0),
                        "acc_gain_pct": round(float(w30["gain_pct"]), 4), "gain_365_pct": round(float(w365["gain_pct"]), 4),
                        "tim_365_pct": w365.get("tim_pct"), "dd_365_pct": w365.get("max_dd_pct"),
                        "prev_book_365_pct": r["book365"].get("gain_pct"), "prev_book_365_valid": r["book365"].get("valid"),
                        "prev_winning_tag": old.get("winning_tag"), "engine": "v12_quick fd93da9a + v12_pilot a91005cf",
                        "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            changed += 1
            print(f"{kind} {ss}: {len(r['overrides'])} overrides | 30D {w30['gain_pct']:+.2f} | 365D {w365['gain_pct']:+.2f} (TIM {w365.get('tim_pct')}, DD {w365.get('max_dd_pct'):.1f}) | current book 365D {r['book365'].get('gain_pct')} valid={r['book365'].get('valid')}")
        if changed and not dry:
            shutil.copy2(path, ROOT / "backups" / f"before_365cycle_promotion_{stamp}_{path.name}")
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(book, indent=1, default=str))
            json.loads(tmp.read_text())
            os.replace(tmp, path)
            print(f"wrote {path.name} ({changed} entries), backup before_365cycle_promotion_{stamp}_{path.name}")
        elif changed:
            print(f"[dry-run] {kind}: {changed} entries would be written")


if __name__ == "__main__":
    main()
