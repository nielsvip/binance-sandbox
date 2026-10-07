"""Snapshot {} baselines (gain_pct, trades, ledger hash) for a fixed symbol set so an engine edit's
baseline impact is measured, never assumed.  Diagnostic only (30d single window).

  python tools/grey_switch_baseline_snapshot.py OUT.json [SYM_SIDE,...]
  python tools/grey_switch_baseline_snapshot.py --diff A.json B.json
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import os
if os.environ.get("V12_ENGINE_DIR"):  # measure an older engine copy against the same NPZ/tools
    sys.path.insert(0, os.environ["V12_ENGINE_DIR"])
DEFAULT = "AAPL_LONG,AAPL_SHORT,AMD_LONG,AMD_SHORT,AMZN_LONG,AXTI_LONG,AXTI_SHORT,SOLUSDC_LONG,SOLUSDC_SHORT,LINKUSDC_LONG,LINKUSDC_SHORT,BNBUSDC_LONG,UNIUSDC_SHORT"


def _h(res):
    led = res.get("ledger") or []
    s = json.dumps([[t.get(k) for k in ("type", "bar", "bar_entry", "bar_exit", "reason", "qty")] for t in led if isinstance(t, dict)], default=str)
    return hashlib.md5(s.encode()).hexdigest()[:12]


def snap(out, syms):
    from tools.opt import v12_pilot as P
    res = {}
    for s in syms:
        p = P.prepare_batch(s, 30)
        if p is None:
            res[s] = {"error": "no npz"}
            continue
        r = P.evaluate_prepared_sanitized(p, {}, 30, include_ledger=True)
        res[s] = {"gain_pct": round(float(r.get("gain_pct") or 0.0), 6), "trades": r.get("trades"), "ledger_md5": _h(r)}
        print(s, res[s], flush=True)
    Path(out).write_text(json.dumps(res, indent=1))


def diff(a, b):
    A, B = json.loads(Path(a).read_text()), json.loads(Path(b).read_text())
    for s in A:
        x, y = A[s], B.get(s, {})
        same = x.get("ledger_md5") == y.get("ledger_md5")
        print(f"{s:16s} {'SAME' if same else 'CHANGED'} gain {x.get('gain_pct')} -> {y.get('gain_pct')} trades {x.get('trades')} -> {y.get('trades')}")


if __name__ == "__main__":
    if sys.argv[1] == "--diff":
        diff(sys.argv[2], sys.argv[3])
    else:
        snap(sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else DEFAULT).split(","))
