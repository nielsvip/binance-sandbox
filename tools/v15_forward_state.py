#!/usr/bin/env python3
"""Recompute the current 30D open/closed state for affected sym_sides.

Reads the active published full switch set from SQL and evaluates it on the
current NPZ through tools/opt/v12_pilot and v12_quick_engine. `ok`/`valid`
describe whether a usable state decision was computed; `performance_valid`
separately records the 30D metric gates. A completed replay can still produce
a valid flat/open state when its trade count or performance metrics fail a
promotion/reporting floor.
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
# FWDSTATE_OUT_DIR: the trc 7D lane writes its own states (data/forward_state_7d) from its own store; unset = live 30D dir
OUT = Path(os.environ.get("FWDSTATE_OUT_DIR") or ROOT / "data" / "forward_state")


DEFAULT_STORE_DBS = (Path.home() / "binance" / "data" / "hourly_reconfig" / "per_sym_store.db", ROOT / "data" / "hourly_reconfig" / "per_sym_store.db")


def store_db():
    """PER_SYM_STORE_DB when set and present, else the canonical store. 2026-10-27: S2's quarter lane ran this tool with
    no PER_SYM_STORE_DB and rsynced 64 "missing live settings" states over S1's good ones (22:09:50Z); a missing store
    now resolves to the canonical one, and when none exists main() refuses to publish anything."""
    db = os.environ.get("PER_SYM_STORE_DB")
    if db and Path(db).is_file():
        return str(db)
    for cand in DEFAULT_STORE_DBS:
        if cand.is_file():
            return str(cand)
    return None


def _load_overrides(symside):
    """Read the published full set from the active SQL store."""
    import per_sym_live_set
    db = store_db()
    if not db:
        return {}, "missing_explicit_per_sym_store", {}
    con = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True, timeout=5)
    try:
        row = con.execute("SELECT updated_at,full_config_json,template_md5,defaults_round FROM per_sym_active WHERE sym_side=?", (symside,)).fetchone()
    finally:
        con.close()
    if not row or not row[1]:
        return _best_existing_settings(symside, db)
    full = json.loads(row[1])
    if not isinstance(full, dict) or not full:
        return _best_existing_settings(symside, db)
    raw = json.dumps(full, sort_keys=True, separators=(",", ":"), default=str).encode()
    effective = per_sym_live_set._apply_caps(symside, full)
    return dict(effective), "per_sym_store.active", {"settings_updated_at": row[0], "settings_sha256": hashlib.sha256(raw).hexdigest(), "template_md5": row[2], "defaults_round": row[3]}


def _best_existing_settings(symside, db):
    """USER LAW "SKIP_NO_PUBLISHED_BOARD destroyed — all still traded on their best existing settings" and "a held
    sym_side with no board keeps its legacy exit, never trapped": a side without a published per_sym_active row still
    gets a state, from (in order) its latest per_sym_history full set, its data/per_sym_full_sets.json full set, or its
    cat_side defaults (the same last resort tools/pure_board_trader.best_settings uses). `src` names which one, with a
    `fallback:` prefix and settings_fallback=True, so it is never mistaken for a published board (fwdstate_verified_publish
    only accepts src == per_sym_store.active). Only reached when the explicit store is present."""
    import per_sym_live_set
    meta = {"settings_fallback": True}
    try:
        con = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True, timeout=5)
        try:
            row = con.execute("SELECT updated_at,full_config_json,template_md5,defaults_round FROM per_sym_history WHERE sym_side=? AND full_config_json IS NOT NULL ORDER BY epoch DESC, id DESC LIMIT 1", (symside,)).fetchone()
        finally:
            con.close()
        full = json.loads(row[1]) if row and row[1] else None
        if isinstance(full, dict) and full:
            raw = json.dumps(full, sort_keys=True, separators=(",", ":"), default=str).encode()
            return dict(per_sym_live_set._apply_caps(symside, full)), "fallback:per_sym_store.history_latest", {**meta, "settings_updated_at": row[0], "settings_sha256": hashlib.sha256(raw).hexdigest(), "template_md5": row[2], "defaults_round": row[3]}
    except Exception:
        pass
    try:
        full = per_sym_live_set._json_full_sets().get(symside)
        if isinstance(full, dict) and full:
            raw = json.dumps(full, sort_keys=True, separators=(",", ":"), default=str).encode()
            return dict(per_sym_live_set._apply_caps(symside, full)), "fallback:per_sym_full_sets.json", {**meta, "settings_sha256": hashlib.sha256(raw).hexdigest()}
    except Exception:
        pass
    try:
        import per_sym_settings
        symbol, side = symside.rsplit("_", 1)
        cat_side = per_sym_settings.cat_side_of(symbol, side)
        full = per_sym_settings.defaults(cat_side)
        if isinstance(full, dict) and full:
            raw = json.dumps(full, sort_keys=True, separators=(",", ":"), default=str).encode()
            return dict(per_sym_live_set._apply_caps(symside, full)), f"fallback:cat_side_defaults:{cat_side}", {**meta, "settings_sha256": hashlib.sha256(raw).hexdigest()}
    except Exception:
        pass
    return {}, "missing_published_per_sym_row", {}


RETIRED_KEYS = tuple(k for k in ("MIN_HOLD_BARS_BEFORE_EXIT," + os.environ.get("FWDSTATE_RETIRED_KEYS", "")).split(",") if k)


def _drop_retired(ov):
    """2026-10-27 22:32Z v12_quick_engine retired MIN_HOLD_BARS_BEFORE_EXIT (QuickConfig now exposes it as bool False)
    while every published set still carries it as an int, so evaluate_v12 rejected EVERY state ("override
    MIN_HOLD_BARS_BEFORE_EXIT=10 incompatible with bool field"). A retired key the engine no longer accepts with that
    value is dropped before eval and named in the state (`dropped_retired_keys`); any other incompatible key still
    fails loudly in evaluate_v12."""
    dropped = {}
    try:
        import v12_quick_engine as V
        from tools.opt.evaluate_v12 import _coerce_override
        cfg = V.QuickConfig()
        for k in RETIRED_KEYS:
            if k in ov and hasattr(cfg, k) and not _coerce_override(k, ov[k], getattr(cfg, k))[0]:
                dropped[k] = ov.pop(k)
    except Exception:
        pass
    return dropped


def _stale_anchor(symside):
    """USER 2026-10-27 operator ruling ("board opens : vectorized should have CIRCUIT_BREAKER/STALE_SET_HALT and trade on
    it"): the active set's recalc/apply anchor, computed by stale_set_halt.recalc_anchor - the SAME anchor the live halt
    uses - so the engine's STALE_SET_HALT twin (vec_decisions/stale_set_halt_twin) can halt board opens. Returns
    {} when there is no active row or anything fails (fail-open = twin inert, exactly the pre-2026-10-27 behaviour)."""
    try:
        import stale_set_halt as _ssh
        db = store_db()
        if not db:
            return {}
        con = sqlite3.connect(f"file:{Path(db).resolve()}?mode=ro", uri=True, timeout=5)
        try:
            r = con.execute("SELECT updated_at, campaign_ts, winning_tag, meta_json, overrides_json FROM per_sym_active WHERE sym_side=?", (symside,)).fetchone()
            if not r:
                return {}
            row = {"updated_at": r[0], "campaign_ts": r[1], "winning_tag": r[2], "meta_json": r[3], "overrides_md5": _ssh._overrides_md5(r[4])}
            hist = None
            if _ssh.tag_epoch(row.get("winning_tag")) is None:
                hist = [{"epoch": e, "updated_at": u, "winning_tag": t} for e, u, t in con.execute("SELECT epoch, updated_at, winning_tag FROM per_sym_history WHERE sym_side=? ORDER BY id DESC LIMIT 200", (symside,))]
        finally:
            con.close()
        anc = _ssh.recalc_anchor(row, _ssh.load_published().get(symside), hist)
        # live treats an anchor-less row as infinitely old (age from epoch 0); 1.0 keeps that while staying "anchored"
        return {"STALE_SET_RECALC_EPOCH": max(1.0, float(anc.get("recalc_epoch") or 0.0)), "STALE_SET_APPLY_EPOCH": float(anc.get("apply_epoch") or 0.0), "_source": str(anc.get("source") or "")}
    except Exception:
        return {}


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


def _vector_acts(ledger, last_index, tails):
    """USER 2026-10-27 vector_direct: the engine's own acts AT the last closed bar, with quantity, plus the
    position it holds after them. Read out of the same ledger as `open`; FINAL_MTM marks are never acts."""
    rows = []
    for n, row in enumerate(ledger):
        typ = str(row.get("type", "")).upper()
        if typ not in ("OPEN", "AUGMENT", "REDUCE", "CLOSE") or row.get("unrealised") or str(row.get("reason") or "").startswith("FINAL_MTM"):
            continue
        bar = row.get("bar_exit") if typ == "CLOSE" else row.get("bar", row.get("bar_entry"))
        if bar is not None:
            rows.append((int(bar), n, typ, row))
    rows.sort(key=lambda item: (item[0], item[1]))
    qty = open_qty = open_px = 0.0
    open_bar = None
    acts = []
    for bar, _n, typ, row in rows:
        q = abs(float(row.get("qty") or 0.0))
        px = float(row.get("price") or row.get("exit_price") or 0.0)
        before = qty
        if typ == "OPEN" or (typ == "AUGMENT" and qty <= 1e-12):
            qty, open_qty, open_px, open_bar = q, q, px, bar
        elif typ == "AUGMENT":
            qty += q
        elif typ == "REDUCE":
            qty = max(0.0, qty - q)
        else:
            qty = 0.0
        if qty <= 1e-12:
            qty, open_qty, open_px, open_bar = 0.0, 0.0, 0.0, None
        if bar == last_index:
            acts.append({"type": typ, "qty": q, "price": px, "reason": str(row.get("reason") or row.get("exit_reason") or "")[:80],
                         "qty_before": before, "qty_after": qty, "qty_frac": (1.0 if typ == "CLOSE" else round(min(1.0, q / before), 6) if typ == "REDUCE" and before > 0 else None)})
    pos = {"engine_qty": qty, "engine_open_qty": open_qty, "engine_open_px": open_px,
           "engine_open_bar_ts": int(tails[open_bar]) if open_bar is not None and 0 <= open_bar < len(tails) else None}
    return pos, acts


def state_for(symside, window_days=30):
    from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized
    ov, src, setting_meta = _load_overrides(symside)
    _dropped = _drop_retired(ov) if ov else {}
    if _dropped:
        setting_meta = {**setting_meta, "dropped_retired_keys": _dropped}
    if not ov:
        return {"ts": time.strftime("%FT%TZ", time.gmtime()), "sym_side": symside,
                "ok": False, "reason": "missing live settings", "src": src}
    # 2026-10-27: read the zip PLUS its .tail sidecar (npz_tail_service appends the closed bar there within seconds; the
    # zip only absorbs it at 4 rows) and never reuse a store cached by an earlier job in a long-lived pool worker.
    try:
        from tools.opt import evaluate_v12 as _ev
        _ev._STORES.clear()
        _sym = symside.rsplit("_", 1)[0]
        _names = {_sym}
        try:
            _names.add(_ev.resolve_tokenised(_sym)[0])
        except Exception:
            pass
        _paths = [ROOT / _pfx / "indicators" / f"{_n}.npz" for _pfx in ("backtest_v8", "backtest_v7") for _n in _names]
        from tools.npz_merged_view import merged_npz_reads
        with merged_npz_reads(_paths):
            prep = prepare_batch(symside, window_days)
    except ImportError:
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
    _anc = _stale_anchor(symside) if src == "per_sym_store.active" else {}
    if _anc:
        ov = {**ov, "STALE_SET_RECALC_EPOCH": _anc["STALE_SET_RECALC_EPOCH"], "STALE_SET_APPLY_EPOCH": _anc["STALE_SET_APPLY_EPOCH"]}
        setting_meta = {**setting_meta, "stale_anchor": _anc}
    res = evaluate_prepared_sanitized(prep, ov, window_days, include_ledger=True) or {}
    perf_valid = res.get("valid") is True
    perf_reason = res.get("invalid_reason")
    completed_performance_gate = perf_valid or bool(
        perf_reason
        and (
            perf_reason.startswith("trades ")
            or perf_reason.startswith("TIM ")
            or perf_reason.startswith("DD ")
        )
    )
    if not completed_performance_gate:
        return {"ts": time.strftime("%FT%TZ", time.gmtime()), "sym_side": symside, "ok": False,
                "reason": perf_reason or "invalid_30d_replay", "src": src, "npz_tail": tail, **setting_meta}
    led = [t for t in (res.get("ledger") or []) if isinstance(t, dict)]
    # Preserve the exact replay inputs and acts used for this published verdict.
    import numpy as _npmod
    input_hashes = {}
    for key in ("timestamps", "close"):
        values = _np.get(key)
        if values is not None:
            array = _npmod.ascontiguousarray(values)
            input_hashes[key] = hashlib.sha256(array.view(_npmod.uint8)).hexdigest()
    ledger_hash = hashlib.sha256(json.dumps(led, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    cur = _open_ledger_position(led, len(tails) - 1)
    out = {"ts": time.strftime("%FT%TZ", time.gmtime()), "sym_side": symside, "ok": True,
           "src": src, "npz_tail": tail, "window_days": window_days,
           "engine_ast_sha256": _engine_ast_sha256(), **setting_meta,
           "input_sha256": input_hashes, "ledger_sha256": ledger_hash, "ledger": led,
           "gain_pct": res.get("gain_pct"), "trades": res.get("trades"),
           "valid": True, "performance_valid": perf_valid,
           "performance_invalid_reason": None if perf_valid else perf_reason,
           "open": cur is not None,
           "entry_bar": int(tails[int(cur.get("bar", cur.get("bar_entry")))]) if cur is not None and cur.get("bar", cur.get("bar_entry")) is not None else None,
           "entry_px": cur.get("price", cur.get("entry_price")) if cur is not None else None,
           "audit_only": False}
    if _anc:
        try:  # audit only: does the board's STALE_SET_HALT twin hold opens at the current bar? (same predicate the engine used)
            from vec_decisions import stale_set_halt_twin as _ssh_twin
            _cl = [(float(t.get("ts") or 0.0), float(t.get("pnl_pct", 0.0) or 0.0)) for t in led if str(t.get("type", "")).upper() == "CLOSE"]
            _h, _why = _ssh_twin.halt_fires(float(tail), _anc["STALE_SET_RECALC_EPOCH"], _anc["STALE_SET_APPLY_EPOCH"], _cl, float(ov.get("STALE_SET_HALT_DAYS", 10.0) or 10.0), int(ov.get("STALE_SET_HALT_LOSSES", 2) or 2))
            out["stale_set_halt"] = {"halted": bool(_h), "why": _why}
        except Exception as exc:
            out["stale_set_halt"] = {"error": f"{type(exc).__name__}: {exc}"[:120]}
    try:
        _pos, _acts = _vector_acts(led, len(tails) - 1, tails)
        out.update(_pos)
        out["acts_bar_ts"] = tail
        out["acts_last_bar"] = _acts
        if out.get("open") and float(_pos.get("engine_qty") or 0.0) <= 1e-12 and any(
                str(x.get("type")) in ("CLOSE", "REDUCE") and float(x.get("qty_after") or 0.0) <= 1e-12 for x in _acts):
            out["open"], out["entry_bar"], out["entry_px"] = False, None, None
            out["flat_reason"] = "zero_qty_after_same_bar_close"
    except Exception as exc:
        out["acts_error"] = f"{type(exc).__name__}: {exc}"[:160]
    return out


def _one(ss):
    try:
        r = state_for(ss.strip().upper())
    except Exception as e:
        r = {"ts": time.strftime("%FT%TZ", time.gmtime()), "sym_side": ss, "ok": False, "reason": f"{e}"[:160]}
    return r


def main(argv):
    if not store_db():
        print(f"REFUSED: no per_sym store (PER_SYM_STORE_DB unset/missing and none of {[str(p) for p in DEFAULT_STORE_DBS]} exists); publishing nothing", file=sys.stderr, flush=True)
        return 3
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
    def publish(ss, result):
        nonlocal n
        target = OUT / f"{result.get('sym_side', ss).upper()}.json"
        tmp = target.with_name(target.name + f".new.{os.getpid()}")
        tmp.write_text(json.dumps(result, indent=1))
        os.replace(tmp, target)
        print(f"{result.get('sym_side')}: ok={result.get('ok')} open={result.get('open')} src={result.get('src')}", flush=True)
        n += 1
    if workers <= 1 or len(syms) <= 1:
        for ss in syms:
            publish(ss, _one(ss))
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_one, ss): ss for ss in syms}
            for future in as_completed(futures):
                publish(futures[future], future.result())
    return 0 if n else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
