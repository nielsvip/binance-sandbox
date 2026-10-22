#!/usr/bin/env python3
"""Targeted pure-board pass over step-cache-warmed symbols (USER 2026-10-22 parity).
Same code path as tools/pure_board_trader.py --once (run_bar), paper-only: CONVERGE
rows go to the Mac intents ledger where tradier_manage's board_state_converge_loop
picks them up. Read-only vs live. Covers accounts with no loop lane (tra/trc);
trb is covered by the loop lanes (this pass would duplicate them). Scheduled via
cron (CACHED_SYMS_PASS_TRA_TRC); pgrep singleton, skips silently when a pass runs."""
import datetime as dt
import os
import sys
import time
from pathlib import Path

ROOT = "/Users/niels/Documents/binance"


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--accounts", default="trb")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    accts = [x.strip() for x in a.accounts.split(",") if x.strip()]
    os.environ.setdefault("PURE_BOARD_STEP_CACHE_DIR", ROOT + "/data/pure_board/step_cache")
    os.environ.setdefault("NPZ_STOCK_STEP_WORKERS", "8")
    sys.path.insert(0, ROOT)
    from tools import pure_board_trader as P

    cached = {p.stem for p in Path(ROOT, "data/pure_board/step_cache").glob("*.pkl")}
    print(f"cached symbols: {sorted(cached)}", flush=True)
    uni = [(x, ss) for (x, ss) in P.universe(accts) if ss.rsplit("_", 1)[0] in cached]
    print(f"{accts} keys on cached symbols: {len(uni)}", flush=True)
    bds = P.boards()
    held = P.live_positions(accts)
    nheld = sum(1 for v in held.values() if (v or {}).get("qty", 0) > 0)
    print(f"live trb positions held: {nheld}", flush=True)
    book = P.PaperBook(P.OUT / "paper_state.json")
    bar = P.last_closed_bar()
    day = dt.datetime.fromtimestamp(bar, dt.timezone.utc).strftime("%Y-%m-%d")
    t0 = time.time()
    stats, intents = P.run_bar(uni, bds, book, bar, ledger=P.OUT / f"intents_{day}.jsonl",
                               workers=a.workers, held=held)
    print(f"done in {time.time()-t0:.0f}s stats={dict(stats)}", flush=True)
    for r in intents:
        if r.get("action") in ("CONVERGE_OPEN", "CONVERGE_CLOSE"):
            print(f"  {r['acct']}:{r['sym_side']} {r['action']} vec_qty={r.get('vec_qty')} why={r.get('reason','')[:100]}",
                  flush=True)


if __name__ == "__main__":
    main()
