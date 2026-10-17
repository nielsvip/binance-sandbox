"""paper_nonvec — live-only (non-vectorizable) decisions are switched OFF in live and paper-traded next to the vector trades.

USER 2026-10-17: "live needs to 100% reflect vector decisions right now no discrepancies tolerated - they need to be under the non
vecorizable switch and switched off then paper traded next to the live 100% vector parity trades and any function that is useful to keep
(pos delta on most sym_sides) gets to override vector in a week or so".

Under PARITY_DISABLE_NON_VECTORIZABLE every order the vector did not decide is refused at the existing gates (crypto hermetic +
STRICT_VEC_PARITY in ez_manage.execute_now, stocks hermetic in tradier execute_now + STRICT_VEC_PARITY in queue_trade_action). Each
refusal calls record() here instead of vanishing; tools/paper_nonvec_eval.py scores every function per sym_side against what the vector
actually did (exits: close now vs the vector's real close; entries: forward return net of costs) and writes
data/paper_nonvec/verdict.json — a function positive on most sym_sides after >= 7 days is a PROMOTE candidate. Promotion is a decision,
never automatic. Zero live impact: append-only, deduplicated per (key, function, kind) per 15-minute bar, every error swallowed.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIR = ROOT / "data" / "paper_nonvec"
_SEEN = {}
_LOCK = threading.Lock()


def record(venue, account, position_key, kind, action, reason, gate, price=None, qty=None):
    """kind = "entry" | "exit". gate = the refusing gate (HERMETIC / STRICT_VEC_PARITY)."""
    try:
        from loss_prone import fn_family

        fam = fn_family(reason)
        now = time.time()
        bar = int(now // 900)
        k = (str(position_key), fam, kind)
        with _LOCK:
            if _SEEN.get(k) == bar:
                return
            _SEEN[k] = bar
            if len(_SEEN) > 20000:
                _SEEN.clear()
        row = {"ts": datetime.fromtimestamp(now, timezone.utc).isoformat(), "venue": venue, "account": str(account or str(position_key).split(":")[0]),
               "position_key": str(position_key), "kind": kind, "action": str(action or ""), "fn": fam, "reason": str(reason or "")[:160], "gate": gate}
        if price:
            row["price"] = float(price)
        if qty:
            row["qty"] = float(qty)
        DIR.mkdir(parents=True, exist_ok=True)
        with open(DIR / f"events_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass


def kind_of(action, side=None, position_side=None) -> str:
    a = str(action or "").upper()
    if "CLOSE" in a or "REDUCE" in a:
        return "exit"
    if side and position_side:
        s, ps = str(side).upper(), str(position_side).upper()
        if (ps == "LONG" and s == "SELL") or (ps == "SHORT" and s == "BUY"):
            return "exit"
    return "entry"


def enabled(cfg) -> bool:
    return bool(getattr(cfg, "PARITY_DISABLE_NON_VECTORIZABLE", True)) and bool(getattr(cfg, "PAPER_NONVEC_ENABLED", True))


if __name__ == "__main__":
    print(os.path.relpath(DIR, ROOT))
