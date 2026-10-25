#!/usr/bin/env python3
"""Forward-state recompute (audit-only). After an NPZ install, recompute the
forward vectorized open/closed state for affected sym_sides using the REAL
board artifacts (stored override VALUES from progress JSONs) and the REAL
vector engine (tools/opt/v12_pilot prepare_batch + evaluate with ledger).

Writes data/forward_state/<SYM_SIDE>.json — READ ONLY output. No live-trading
effect. Enforcement lives behind the gated+paper-proof spec in
data/forward_state_SPEC.md and is NOT implemented here.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "forward_state"


def _load_overrides(symside):
    """Stored override VALUES, newest progress first. No re-derivation."""
    cands = [
        Path.home() / "v15_run36_20261024" / "progress" / f"{symside}_v14_progress.json",
        ROOT / "data" / "reports" / "lifecycle_pilot" / f"{symside}_v14_progress.json",
    ]
    for p in cands:
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        for k in ("hustler_overrides", "cumulative_overrides"):
            ov = d.get(k)
            if isinstance(ov, dict) and ov:
                return ov, f"{p.name}:{k}"
    return {}, "none"


def state_for(symside, window_days=30):
    from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized
    ov, src = _load_overrides(symside)
    prep = prepare_batch(symside, window_days)
    if prep is None or (isinstance(prep, dict) and prep.get("_stale")):
        return {"ts": time.strftime("%FT%TZ", time.gmtime()), "sym_side": symside,
                "ok": False, "reason": "prepare failed/stale", "src": src}
    _np = prep.get("npz_prepared")
    _np = {} if _np is None else _np
    tails = _np.get("timestamps")
    tails = [] if tails is None else tails
    tail = int(tails[-1]) if len(tails) else 0
    res = evaluate_prepared_sanitized(prep, ov, window_days, include_ledger=True) or {}
    led = [t for t in (res.get("ledger") or []) if isinstance(t, dict)]
    open_rows = [t for t in led if str(t.get("type", "")).upper() in ("OPEN", "AUGMENT")]
    close_bars = sorted(int(t.get("bar_exit") or 0) for t in led if t.get("bar_exit") is not None)
    cur = None
    for t in sorted(open_rows, key=lambda r: int(r.get("bar_entry") or 0)):
        be = int(t.get("bar_entry") or 0)
        closed_after = any(bx > be for bx in close_bars)
        if be <= tail and not closed_after:
            cur = t
    out = {"ts": time.strftime("%FT%TZ", time.gmtime()), "sym_side": symside, "ok": True,
           "src": src, "npz_tail": tail, "window_days": window_days,
           "gain_pct": res.get("gain_pct"), "trades": res.get("trades"),
           "valid": res.get("valid"), "open": cur is not None,
           "entry_bar": int(cur.get("bar_entry")) if cur is not None and cur.get("bar_entry") is not None else None,
           "entry_px": cur.get("entry_price") if cur is not None else None,
           "audit_only": True}
    return out


def _one(ss):
    try:
        r = state_for(ss.strip().upper())
    except Exception as e:
        r = {"ts": time.strftime("%FT%TZ", time.gmtime()), "sym_side": ss, "ok": False, "reason": f"{e}"[:160]}
    return r


def main(argv):
    OUT.mkdir(parents=True, exist_ok=True)
    syms = [ss.strip().upper() for ss in argv[1:]]
    n = 0
    # 2026-10-25 USER "STATE calculated within seconds of any 15m bar": the
    # per-sym eval is pure (own NPZ + own progress JSON in, own file out), so
    # the serial loop parallelizes with identical outputs. Workers=8 keeps
    # half the S1 box free for herds; single worker on tiny boxes.
    import os as _os
    workers = 8
    try:
        if (_os.cpu_count() or 8) < 4:
            workers = 1
    except Exception:
        pass
    if workers <= 1 or len(syms) <= 1:
        results = [_one(ss) for ss in syms]
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=workers) as _ex:
            results = list(_ex.map(_one, syms))
    for ss, r in zip(syms, results):
        (OUT / f"{r.get('sym_side', ss).upper()}.json").write_text(json.dumps(r, indent=1))
        print(f"{r.get('sym_side')}: ok={r.get('ok')} open={r.get('open')} src={r.get('src')}", flush=True)
        n += 1
    return 0 if n else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
