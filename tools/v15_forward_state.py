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
import ast
import hashlib
import sqlite3
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "forward_state"


def _load_overrides(symside):
    """Read the published full set from the explicitly selected active SQL store."""
    import per_sym_live_set
    db = os.environ.get("PER_SYM_STORE_DB")
    if not db or not Path(db).is_file():
        return {}, "missing_explicit_per_sym_store", {}
    con = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True, timeout=5)
    try:
        row = con.execute("SELECT updated_at,full_config_json,template_md5,defaults_round FROM per_sym_active WHERE sym_side=?", (symside,)).fetchone()
    finally:
        con.close()
    if not row or not row[1]:
        return {}, "missing_published_per_sym_row", {}
    full = json.loads(row[1])
    if not isinstance(full, dict) or not full:
        return {}, "invalid_published_per_sym_row", {}
    raw = json.dumps(full, sort_keys=True, separators=(",", ":"), default=str).encode()
    effective = per_sym_live_set._apply_caps(symside, full)
    return dict(effective), "per_sym_store.active", {"settings_updated_at": row[0], "settings_sha256": hashlib.sha256(raw).hexdigest(), "template_md5": row[2], "defaults_round": row[3]}


@lru_cache(maxsize=1)
def _engine_ast_sha256():
    source = (ROOT / "v12_quick_engine.py").read_text()
    tree = ast.parse(source)
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


def _open_ledger_position(ledger, tail):
    """Find the last opening still held at the final bar, ignoring the MTM mark."""
    openings = [row for row in ledger if str(row.get("type", "")).upper() in ("OPEN", "AUGMENT")]
    closes = [int(row.get("bar_exit")) for row in ledger
              if str(row.get("type", "")).upper() == "CLOSE"
              and not row.get("unrealised") and row.get("bar_exit") is not None]
    current = None
    for row in sorted(openings, key=lambda item: int(item.get("bar", item.get("bar_entry")) or 0)):
        entry_bar = row.get("bar", row.get("bar_entry"))
        if entry_bar is None:
            continue
        entry_bar = int(entry_bar)
        if entry_bar <= tail and not any(exit_bar > entry_bar for exit_bar in closes):
            current = row
    return current


def state_for(symside, window_days=30):
    from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized
    ov, src, setting_meta = _load_overrides(symside)
    if not ov:
        return {"ts": time.strftime("%FT%TZ", time.gmtime()), "sym_side": symside,
                "ok": False, "reason": "missing live settings", "src": src}
    prep = prepare_batch(symside, window_days)
    if prep is None or (isinstance(prep, dict) and prep.get("_stale")):
        return {"ts": time.strftime("%FT%TZ", time.gmtime()), "sym_side": symside,
                "ok": False, "reason": "prepare failed/stale", "src": src}
    _np = prep.get("npz_prepared")
    _np = {} if _np is None else _np
    tails = _np.get("timestamps")
    tails = [] if tails is None else tails
    tail = int(tails[-1]) if len(tails) else 0
    symbol = symside.rsplit("_", 1)[0]
    is_crypto = symbol.endswith(("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI"))
    expected_tail = (int(time.time()) // 900 - 1) * 900
    if not tail or (is_crypto and tail < expected_tail):
        return {"ts": time.strftime("%FT%TZ", time.gmtime()), "sym_side": symside, "ok": False,
                "reason": "npz_tail_behind_last_closed_bar", "src": src, "npz_tail": tail,
                "expected_npz_tail": expected_tail, **setting_meta}
    res = evaluate_prepared_sanitized(prep, ov, window_days, include_ledger=True) or {}
    if res.get("valid") is not True:
        return {"ts": time.strftime("%FT%TZ", time.gmtime()), "sym_side": symside, "ok": False,
                "reason": "invalid_30d_replay", "src": src, "npz_tail": tail, **setting_meta}
    led = [t for t in (res.get("ledger") or []) if isinstance(t, dict)]
    cur = _open_ledger_position(led, len(tails) - 1)
    out = {"ts": time.strftime("%FT%TZ", time.gmtime()), "sym_side": symside, "ok": True,
           "src": src, "npz_tail": tail, "window_days": window_days,
           "engine_ast_sha256": _engine_ast_sha256(), **setting_meta,
           "gain_pct": res.get("gain_pct"), "trades": res.get("trades"),
           "valid": res.get("valid"), "open": cur is not None,
           "entry_bar": int(tails[int(cur.get("bar", cur.get("bar_entry")))]) if cur is not None and cur.get("bar", cur.get("bar_entry")) is not None else None,
           "entry_px": cur.get("price", cur.get("entry_price")) if cur is not None else None,
           "audit_only": False}
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
    # the serial loop parallelizes with identical outputs. Workers=4 MAX
    # (USER 2026-10-25: max 4 syms in RAM at a time — 8-worker passes helped
    # OOM-reboot S1 twice); single worker on tiny boxes.
    import os as _os
    workers = 4
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
