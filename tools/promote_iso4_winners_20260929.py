#!/usr/bin/env python3
"""Promote the 2026-09-29 verified v15 winners to LIVE (run by the operator — production deploy).

Winners = v12_quick authoritative engine (7383699d), iso4 fresh runs, 30D final == fresh == independent re-eval,
365D KEEP and better than the CURRENT live book on both 30D and 365D:
  AAPL_LONG  30D +6.01  365D +23.71 (live book 365D +3.88)
  CRWD_LONG  30D +9.93  365D  +9.10 (live book  +6.29)
  MSFT_SHORT 30D +6.09  365D  +6.09 (live book  +4.03)
  GDX_SHORT  30D +0.71  365D  +0.29 (live book  -3.77)
Stocks also get DAYTRADE_DC_STOP/TARGET_TF = "15m,1h" (what the backtest ran; ON beat OFF on 8/10 stock sheets 30D+365D).
--global also flips config_tradier.py DAYTRADE_DC_*_TF OFF -> "15m,1h" (stock-wide; crypto stays OFF: no measured effect).
CAVEAT: backtest_v12_engine parity is NOT yet passing for stocks (live GR_HTF_DIRECT entries / GAP_RISK exits not modelled
in the sweep) — live results will not track these backtests exactly.

Usage:  python3 tools/promote_iso4_winners_20260929.py [--global] [--dry-run]
Then restart the live managers (config is read at start; per-sym books are re-read on change).
"""
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "reports" / "promotion_iso4_20260929"
BOOKS = {"stocks": ROOT / "data/hourly_reconfig/per_sym_active_config_stocks.json", "crypto": ROOT / "data/hourly_reconfig/per_sym_active_config.json"}
WINNERS = {"AAPL_LONG": 3.88, "CRWD_LONG": 6.29, "MSFT_SHORT": 4.03, "GDX_SHORT": -3.77}  # SOLUSDC_LONG dropped 2026-09-29 19:40: engine fd93da9a 365D -37.81% TIM 96.4% invalid; GDX_LONG dropped 2026-09-29 15:30: 30D TIM 80.2% >80% = invalid


def main():
    dry = "--dry-run" in sys.argv
    stamp = time.strftime("%Y%m%d%H%M")
    # 2026-09-29 19:55: authoritative evidence = re-verification on the CURRENT engine (fd93da9a: honest gain,
    # frequent exits OFF, EMA_BLANKET OFF). Old-engine verify_results.jsonl kept for history only.
    verify = {json.loads(line)["sym_side"]: json.loads(line) for line in open(SRC / "verify_engine_fd93da9a.jsonl")}
    for kind, path in BOOKS.items():
        book = json.loads(path.read_text())
        changed = 0
        for ss, prev365 in WINNERS.items():
            is_crypto = "USD" in ss.split("_")[0]
            if (kind == "crypto") != is_crypto:
                continue
            prog = json.loads((SRC / f"{ss}_v14_progress.json").read_text())
            ver = verify[ss]
            assert ver["verdict"] == "KEEP" and ver["beats_book_365"] and ver["vec30"].get("valid") and ver["vec365"].get("valid") and ver["vec30"]["gain_pct"] > 0, f"{ss} not verified on current engine"
            ov = dict(prog["cumulative_overrides"])
            if not is_crypto:
                ov.setdefault("DAYTRADE_DC_STOP_TF", "15m,1h")
                ov.setdefault("DAYTRADE_DC_TARGET_TF", "15m,1h")
            old = book.get(ss, {})
            book[ss] = {"overrides": ov, "winning_tag": "v15_iso4_authoritative_20260929_365D_KEEP", "trades": int(ver["vec30"].get("trades") or 0),
                        "acc_gain_pct": round(float(ver["vec30"]["gain_pct"]), 4), "bh_pct": round(float(prog.get("bh") or 0), 4),
                        "gain_365_pct": round(float(ver["vec365"]["gain_pct"]), 4), "prev_book_365_pct": round(float(ver["base365"]["gain_pct"]), 4), "engine": "v12_quick fd93da9a",
                        "prev_winning_tag": old.get("winning_tag"), "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            changed += 1
            print(f"{kind} {ss}: {len(ov)} overrides, 30D {ver['vec30']['gain_pct']:+.2f} 365D {ver['vec365']['gain_pct']:+.2f} (current live book 365D {ver['base365']['gain_pct']:+.2f})")
        if changed and not dry:
            shutil.copy2(path, ROOT / "backups" / f"before_iso4_promotion_{stamp}_{path.name}")
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(book, indent=1, default=str))
            json.loads(tmp.read_text())
            os.replace(tmp, path)
            print(f"wrote {path.name} ({changed} entries), backup before_iso4_promotion_{stamp}_{path.name}")
    if "--global" in sys.argv:
        cfg = ROOT / "config_tradier.py"
        text = cfg.read_text()
        new = re.sub(r'^(\s+DAYTRADE_DC_(STOP|TARGET)_TF: str = )"OFF"', r'\1"15m,1h"', text, flags=re.M)
        n = sum(1 for a, b in zip(text.splitlines(), new.splitlines()) if a != b)
        print(f"config_tradier.py: {n} DAYTRADE_DC_*_TF lines OFF -> 15m,1h")
        if n and not dry:
            shutil.copy2(cfg, ROOT / "backups" / f"before_iso4_promotion_{stamp}.config_tradier.py")
            cfg.write_text(new)
            import py_compile
            py_compile.compile(str(cfg), doraise=True)
            print("config_tradier.py written + compiles; restart live managers to load it")


if __name__ == "__main__":
    main()
