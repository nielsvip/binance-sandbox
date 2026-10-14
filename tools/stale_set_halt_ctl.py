#!/usr/bin/env python3
"""stale_set_halt_ctl — BACKTEST_BIBLE §102 STALE-SET 2-LOSS HALT operator tool.

  --dry-run      read-only: which sym_sides WOULD be halted now (venue ez + tradier), with vector age, anchor and the losers
  --list         show persisted halts (data/stale_set_halt/halts_*.json)
  --sync-pins    retry S1 operator-queue pins for halts whose pin is still PENDING (ssh s1-int, JSON-validated, md5 printed)
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import stale_set_halt as ssh  # noqa: E402


class _Cfg:
    BASE_PATH = str(ROOT)
    STALE_SET_HALT_ENABLED = True
    STALE_SET_HALT_DAYS = 10.0
    STALE_SET_HALT_LOSSES = 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--sync-pins", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cfg = _Cfg()
    if a.dry_run:
        rep = {}
        for venue in ("ez", "tradier"):
            rows = ssh.status_all(venue, cfg)
            would = [r for r in rows if r["action"] in ("HALT", "KEEP")]
            stale = [r for r in rows if r["age_days"] > cfg.STALE_SET_HALT_DAYS]
            rep[venue] = {"sym_sides_with_closes": len(rows), "stale_gt_days": len(stale), "would_halt": [{"sym_side": r["sym_side"], "age_days": r["age_days"], "anchor": r["anchor"].get("source"), "recalc": ssh._iso(r["anchor"].get("recalc_epoch")), "losers": [(c.get("ts"), c.get("account"), c.get("net_pct"), c.get("src"), c.get("reason", "")[:60]) for c in r.get("losers") or []]} for r in would],
                          "stale_not_halted": sorted((r["sym_side"], r["age_days"], r.get("n_closes_since_apply")) for r in stale if r["action"] == "NONE")}
        print(json.dumps(rep, indent=1, default=str))
        return 0
    if a.list:
        for venue in ("ez", "tradier"):
            print(venue, json.dumps(ssh.load_halts(venue, cfg), indent=1, default=str))
        return 0
    if a.sync_pins:
        for venue in ("ez", "tradier"):
            for ss, h in ssh.load_halts(venue, cfg).items():
                if (h.get("pin") or {}).get("state") != "PINNED":
                    res = ssh.pin_now(ss, "add")
                    ssh._set_pin_state(venue, ss, {"state": "PINNED" if res.get("ok") else "PENDING", "op": "add", "at": ssh._iso(__import__("time").time()), "result": res}, cfg)
                    print(venue, ss, res)
        return 0
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
