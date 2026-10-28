#!/usr/bin/env python3
"""npz_row_incremental_audit — off-hot-path safety net for the incremental NPZ row (USER 2026-10-28).

Every run picks a few owned symbols, and for their newest closed 15m bar computes the NPZ row twice from the SAME
kline files at the same moment: the full whole-history computation (NPZ_ROW_MODE=full path of
npz_live_generator._compute_row_for_15m_close) and the incremental row (_compute_row_incremental). Every key is
compared: discrete values must be identical, floats within 1e-9 relative. Any difference or refusal is an ALERT line
in logs/npz_row_incremental_audit.jsonl and data/reports/npz_row_incremental_audit.json. Runs mid-bar (phase 300-780 s)
so it never competes with the bar-close burst. Reports only; writes no NPZ.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WALL_CLOCK_KEYS = {"1m_updated_at", "timestamp_1m"}


def compare(full, inc):
    out = {"keys": 0, "exact": 0, "float_close": 0, "max_rel": 0.0, "max_rel_key": None, "diffs": []}
    for key in sorted((set(full) | set(inc)) - WALL_CLOCK_KEYS):
        if key not in full or key not in inc:
            out["diffs"].append([key, "missing", key in full, key in inc])
            continue
        a, b = full[key], inc[key]
        out["keys"] += 1
        numeric = all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, b))
        if not numeric or isinstance(a, int) and isinstance(b, int):
            if a == b and type(a) is type(b):
                out["exact"] += 1
            else:
                out["diffs"].append([key, repr(a), repr(b)])
            continue
        a, b = float(a), float(b)
        if a == b or (math.isnan(a) and math.isnan(b)):
            out["exact"] += 1
            continue
        rel = abs(a - b) / max(abs(a), abs(b))
        if rel > out["max_rel"]:
            out["max_rel"], out["max_rel_key"] = rel, key
        if rel <= 1e-9 and type(full[key]) is type(inc[key]):
            out["float_close"] += 1
        else:
            out["diffs"].append([key, repr(full[key]), repr(inc[key]), rel])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=str(ROOT))
    ap.add_argument("--cat-side", default="crypto", choices=("crypto", "stocks"))
    ap.add_argument("--symbols", type=int, default=4)
    ap.add_argument("--any-phase", action="store_true", help="do not wait for the mid-bar phase")
    a = ap.parse_args()
    phase = time.time() % 900
    if not a.any_phase and not 300 <= phase <= 780:
        return 0
    os.chdir(a.base)
    import logging
    logging.disable(logging.WARNING)
    import npz_live_generator as gen
    from tools.npz_tail_service import kline_last_close, load_universe, owned_symbols
    mode = "crypto" if a.cat_side == "crypto" else "tradier"
    symbols = sorted({s.rsplit("_", 1)[0] for s in owned_symbols(load_universe(a.base), a.cat_side)})
    random.shuffle(symbols)
    manager = types.SimpleNamespace(base_path=Path(a.base))
    report = {"t": time.time(), "host": os.uname().nodename, "cat_side": a.cat_side, "checked": [], "alerts": 0}
    log = Path(a.base) / "logs" / "npz_row_incremental_audit.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    for symbol in symbols[: a.symbols]:
        close = kline_last_close(a.base, symbol, mode)
        if close is None:
            continue
        ts = datetime.fromtimestamp(int(close), timezone.utc)
        t0 = time.time()
        inc = gen._compute_row_incremental(symbol, ts, manager, mode)
        t1 = time.time()
        os.environ["NPZ_ROW_MODE"] = "full"
        try:
            full = gen._compute_row_for_15m_close(symbol, ts, manager, None, mode)
        finally:
            os.environ.pop("NPZ_ROW_MODE", None)
        t2 = time.time()
        rec = {"t": time.time(), "symbol": symbol, "bar_ts": int(close), "inc_s": round(t1 - t0, 3), "full_s": round(t2 - t1, 3)}
        if inc is None:
            rec.update(status="REFUSED_FELL_BACK_TO_FULL")
        elif full is None:
            rec.update(status="FULL_NONE")
        else:
            cmp = compare(full, inc)
            rec.update(status="OK" if not cmp["diffs"] else "ALERT_DIFF", keys=cmp["keys"], exact=cmp["exact"], float_close=cmp["float_close"],
                       max_rel=cmp["max_rel"], max_rel_key=cmp["max_rel_key"], diffs=cmp["diffs"][:20])
        if rec["status"] not in ("OK", "REFUSED_FELL_BACK_TO_FULL"):
            report["alerts"] += 1
            print(f"ALERT npz_row_incremental_audit {symbol} {rec['status']} {rec.get('diffs', [])[:3]}", file=sys.stderr, flush=True)
        report["checked"].append(rec)
        with log.open("a") as stream:
            stream.write(json.dumps(rec) + "\n")
    out = Path(a.base) / "data" / "reports" / f"npz_row_incremental_audit_{a.cat_side}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=1))
    os.replace(tmp, out)
    print(json.dumps({k: v for k, v in report.items() if k != "checked"} | {"n": len(report["checked"]), "statuses": [r["status"] for r in report["checked"]]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
