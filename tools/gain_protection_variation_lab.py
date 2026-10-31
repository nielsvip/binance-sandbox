#!/usr/bin/env python3
"""Diagnostic KEEP-versus-template gain-protection replay on the real v12 engine.

This reads published per-sym full configs, inventory loss counts, and frozen NPZs.
It never edits configs or live code and never changes the mandatory dc15 loss stop.
Only template-backed PPL gain-lock combinations are compared. Results are
diagnostic and are not a promotion signal.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))
PPL_FIELDS = (
    "PARTIAL_PROFIT_LOCK_ENABLED",
    "PARTIAL_PROFIT_LOCK_GAIN_PCT",
    "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT",
    "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT",
    "PARTIAL_PROFIT_LOCK_FRAC",
)
TEMPLATE_COMBOS = (
    (1.5, 1.75, 0.10),
    (2.25, 2.625, 0.15),
    (3.0, 3.5, 0.20),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_json(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def confirmed_stop_loser_counts(inventory: dict, minimum: int) -> dict[str, int]:
    counts = {}
    for symbol, symbol_row in (inventory.get("per_symbol") or {}).items():
        for side, side_row in (symbol_row.get("position_sides") or {}).items():
            count = int(side_row.get("fixed_stop_exit_fill_loss_count") or 0)
            if side in ("LONG", "SHORT") and count >= minimum:
                counts[f"{symbol}_{side}"] = count
    return dict(sorted(counts.items()))


def active_configs(db_path: Path, symsides: list[str]) -> dict:
    uri = f"file:{db_path.resolve()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        result = {}
        for ss in symsides:
            row = con.execute(
                "select sym_side,cat_side,updated_at,winning_tag,overrides_json,full_config_json,template_md5 "
                "from per_sym_active where sym_side=?", (ss,),
            ).fetchone()
            if row is None:
                result[ss] = {"error": "NO_ACTIVE_PUBLISHED_SET"}
                continue
            try:
                full = json.loads(row["full_config_json"] or "{}")
                overrides = json.loads(row["overrides_json"] or "{}")
            except Exception as exc:
                result[ss] = {"error": f"BAD_ACTIVE_JSON:{type(exc).__name__}"}
                continue
            result[ss] = {
                "cat_side": row["cat_side"], "updated_at": row["updated_at"],
                "winning_tag": row["winning_tag"], "template_md5": row["template_md5"],
                "published_overrides_sha256": sha256_json(overrides),
                "published_full_config_sha256": sha256_json(full), "full_config": full,
            }
        return result
    finally:
        con.close()


def candidate_configs(base: dict, fee_floor_pct: float) -> list[dict]:
    """Only combinations with a fee-positive theoretical lifecycle stop are eligible."""
    out = []
    frac = 0.5
    for gain, arm, buffer in TEMPLATE_COMBOS:
        gross_lifecycle_floor = frac * gain + (1.0 - frac) * buffer
        if gross_lifecycle_floor <= fee_floor_pct:
            continue
        values = {
            "PARTIAL_PROFIT_LOCK_ENABLED": True,
            "PARTIAL_PROFIT_LOCK_GAIN_PCT": gain,
            "PARTIAL_PROFIT_LOCK_ARM_GAIN_PCT": arm,
            "PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT": buffer,
            "PARTIAL_PROFIT_LOCK_FRAC": frac,
        }
        if any(base.get(key) != value for key, value in values.items()):
            out.append({"label": f"PPL_{gain:g}_{arm:g}_BE{buffer:g}_F{frac:g}", "overrides": values,
                        "theoretical_gross_lifecycle_floor_pct": gross_lifecycle_floor})
    return out


def summarize_run(result: dict, round_trip_fee_pct: float, window_days: int) -> dict:
    ledger = result.get("execution_ledger") or []
    actions = [e for e in ledger if isinstance(e, dict) and "pnl_dollars" not in e
               and str(e.get("type") or "").upper() in ("OPEN", "AUGMENT", "REDUCE", "CLOSE")]
    trades = [e for e in ledger if isinstance(e, dict) and "pnl_dollars" in e and not e.get("seeded_prewindow")]
    action_turnover = sum(abs(float(e.get("qty") or 0) * float(e.get("price") or 0)) for e in actions)
    missing_close_turnover = 0.0
    for trade in trades:
        ty = str(trade.get("type") or "CLOSE").upper()
        if ty not in ("CLOSE", "REDUCE"):
            continue
        bar, qty, price = int(trade.get("bar_exit", -1)), abs(float(trade.get("qty") or 0)), abs(float(trade.get("exit_price") or trade.get("price") or 0))
        matching = any(
            str(event.get("type") or "").upper() in ("CLOSE", "REDUCE")
            and int(event.get("bar", -2)) == bar
            and abs(abs(float(event.get("qty") or 0)) - qty) <= max(1e-9, qty * 1e-8)
            and abs(abs(float(event.get("price") or 0)) - price) <= max(1e-9, price * 1e-8)
            for event in actions
        )
        if not matching:
            missing_close_turnover += qty * price
    turnover = action_turnover + missing_close_turnover
    fee_est = turnover * round_trip_fee_pct / 200.0
    net = sum(float(e.get("pnl_dollars") or 0) for e in trades)
    opens = [e for e in actions if str(e.get("type") or "").upper() == "OPEN"]
    ppl_closes = [e for e in trades if str(e.get("exit_reason") or e.get("reason") or "").upper().startswith("PPL_SL_CLOSE")]
    rows = []
    for trade in trades:
        entry_bar, exit_bar = int(trade.get("bar_entry", -1)), int(trade.get("bar_exit", -1))
        rows.append({key: trade.get(key) for key in (
            "bar_entry", "bar_exit", "entry_price", "exit_price", "qty",
            "entry_reason", "exit_reason", "bars_held")})
        rows[-1]["net_pnl_engine_quote_units"] = float(trade.get("pnl_dollars") or 0)
    days = float(result.get("data_days") or 0)
    from metrics_guard import pool_sharpe_from_ledger, validate_and_format_sharpe
    _guarded_sample = validate_and_format_sharpe(
        pool_sharpe_from_ledger(trades), label="pool_sharpe", n_syms=1,
        years=max(days / 365.0, 0.0), trades=len(trades), mode="crypto",
    )
    guarded_diagnostic = "[DIAGNOSTIC ONLY" in _guarded_sample
    return {
        "status": "DIAGNOSTIC_ONLY", "valid": bool(result.get("valid")),
        "invalid_reason": result.get("invalid_reason"), "window_days": window_days,
        "data_days": days, "trades": len(trades), "entry_count": len(opens),
        "reentry_count": max(0, len(opens) - 1), "net_pnl_engine": net,
        "turnover_estimate": turnover, "estimated_fee_at_configured_round_trip_rate": fee_est,
        "commission_model_round_trip_pct": round_trip_fee_pct,
        "fee_estimate_basis": "all execution action turnover plus any realized close trade notional missing an action event; RT fee rate/2 applied to each side; engine net P&L remains authoritative",
        "action_turnover_estimate": action_turnover,
        "unmatched_realized_close_turnover_added": missing_close_turnover,
        "ppl_remainder_close_count": len(ppl_closes),
        "ppl_remainder_close_losing_count": sum(float(e.get("pnl_dollars") or 0) <= 0 for e in ppl_closes),
        "all_losing_close_count": sum(float(e.get("pnl_dollars") or 0) < 0 for e in trades),
        "max_dd_pct_engine": result.get("max_dd_pct"),
        "metric_guard_sample_status": "DIAGNOSTIC_ONLY" if guarded_diagnostic else "VALIDATED",
        "trade_rows": rows,
        "trade_lifecycle_sha256": sha256_json([{k: r.get(k) for k in ("bar_entry", "bar_exit", "entry_price", "exit_price", "net_pnl_engine", "exit_reason")} for r in rows]),
    }


def run_side(ss: str, loss_count: int, meta: dict, prepared, window_days: int, npz_dir: Path | None) -> dict:
    from tools.opt import evaluate_v12 as engine

    base = meta["full_config"]
    base_cfg = prepared.get("base_cfg_dict") or {}
    round_trip_fee = float(base.get("CRYPTO_ROUND_TRIP_COMMISSION_PCT", base_cfg.get("CRYPTO_ROUND_TRIP_COMMISSION_PCT", 0.08)) or 0.0)
    commission_buffer = float(base.get("COMMISSION_BUFFER_PCT", base_cfg.get("COMMISSION_BUFFER_PCT", 0.08)) or 0.0)
    fee_floor = max(round_trip_fee, commission_buffer)
    variants = candidate_configs(base, fee_floor)
    sym = str(prepared.get("sym") or ss.rsplit("_", 1)[0])
    source_npz = engine._npz_path_for(sym)
    npz_path = (npz_dir / f"{sym}.npz") if npz_dir else source_npz
    npz_sha_before = sha256_file(npz_path) if npz_path and npz_path.exists() else None
    keep = summarize_run(engine.evaluate_prepared(prepared, dict(base), include_ledger=True), round_trip_fee, window_days)
    rows = [{"variant": "KEEP", "overrides": {}, "result": keep}]
    for variant in variants:
        changed = dict(base)
        changed.update(variant["overrides"])
        observed_floor = (float(changed["PARTIAL_PROFIT_LOCK_FRAC"]) * float(changed["PARTIAL_PROFIT_LOCK_GAIN_PCT"])
                          + (1.0 - float(changed["PARTIAL_PROFIT_LOCK_FRAC"])) * float(changed["PARTIAL_PROFIT_LOCK_BE_BUFFER_PCT"]))
        if observed_floor <= fee_floor:
            raise ValueError(f"fee-positive PPL invariant failed: {ss}/{variant['label']}")
        res = summarize_run(engine.evaluate_prepared(prepared, changed, include_ledger=True), round_trip_fee, window_days)
        res["delta_vs_keep"] = {key: res[key] - keep[key] for key in (
            "net_pnl_engine", "turnover_estimate", "estimated_fee_at_configured_round_trip_rate",
            "entry_count", "reentry_count", "trades", "ppl_remainder_close_count", "all_losing_close_count")}
        res["ledger_identical_to_keep"] = res["trade_lifecycle_sha256"] == keep["trade_lifecycle_sha256"]
        rows.append({"variant": variant["label"], "template_values": variant["overrides"],
                     "theoretical_gross_lifecycle_floor_pct": variant["theoretical_gross_lifecycle_floor_pct"],
                     "required_fee_floor_pct": fee_floor, "result": res})
    npz = prepared.get("npz_prepared") or {}
    ts = npz.get("timestamps")
    return {
        "sym_side": ss, "inventory_confirmed_losing_fixed_stop_fill_count": loss_count,
        "active_set": {k: meta[k] for k in ("cat_side", "updated_at", "winning_tag", "template_md5", "published_overrides_sha256", "published_full_config_sha256")},
        "active_ppl_settings": {k: base.get(k) for k in PPL_FIELDS},
        "fee_policy": {"engine_round_trip_commission_pct": round_trip_fee,
                        "commission_buffer_pct": commission_buffer,
                        "effective_fee_floor_pct": fee_floor},
        "real_engine": "tools.opt.evaluate_v12.evaluate_prepared -> v12_quick_engine.simulate_one",
        "evaluation_api_sha256": sha256_file(Path(engine.__file__)),
        "quick_engine_sha256": sha256_file(ROOT / "v12_quick_engine.py"),
        "npz_file": str(npz_path) if npz_path else None, "npz_file_sha256_before": npz_sha_before,
        "npz_file_sha256_after": sha256_file(npz_path) if npz_path and npz_path.exists() else None,
        "npz_file_unchanged_during_side": bool(npz_sha_before and npz_sha_before == sha256_file(npz_path)),
        "npz_source_file": str(source_npz) if source_npz else None,
        "npz_provenance": prepared.get("npz_provenance") or {},
        "npz_rows": int(len(ts)) if ts is not None else 0,
        "npz_first_ts": float(ts[0]) if ts is not None and len(ts) else None,
        "npz_last_ts": float(ts[-1]) if ts is not None and len(ts) else None,
        "evaluations": rows,
        "interpretation_limit": "Current published full_config is replayed over one frozen NPZ window. This is not exchange-fill attribution; historical config transitions, actual weighted entry changes, actual maker/taker fees, and slippage are not reconstructed. PPL trigger/stop fills use vector close prices. Mandatory dc15 loss stop remains the unchanged engine default.",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--db", default="data/hourly_reconfig/per_sym_store.db")
    ap.add_argument("--out", required=True)
    ap.add_argument("--window-days", type=int, default=7)
    ap.add_argument("--min-losing-stop-fills", type=int, default=3)
    ap.add_argument("--symsides", default="", help="optional comma-list intersected with inventory selection")
    ap.add_argument("--npz-dir", default="", help="immutable copied NPZ directory passed to v12_quick_engine.load_npz")
    args = ap.parse_args()
    if args.window_days not in (7, 30, 365):
        ap.error("--window-days must be 7, 30 or 365")
    inventory_path, db_path, out_path = Path(args.inventory), Path(args.db), Path(args.out)
    inventory = json.loads(inventory_path.read_text())
    if int(inventory.get("schema_version") or 0) != 1:
        raise ValueError("unsupported inventory schema_version")
    selected = confirmed_stop_loser_counts(inventory, args.min_losing_stop_fills)
    if args.symsides:
        wanted = set(filter(None, args.symsides.split(",")))
        selected = {ss: n for ss, n in selected.items() if ss in wanted}
    if not selected:
        raise ValueError("no sym_sides meet the minimum confirmed losing stop-fill count")
    active = active_configs(db_path, list(selected))
    from tools.opt import evaluate_v12 as engine
    npz_dir = Path(args.npz_dir).resolve() if args.npz_dir else None
    if npz_dir:
        from v12_quick_engine import load_npz
        missing_npz = [ss.rsplit("_", 1)[0] for ss in selected if not (npz_dir / f"{ss.rsplit('_', 1)[0]}.npz").is_file()]
        if missing_npz:
            raise FileNotFoundError(f"frozen NPZ snapshot missing: {sorted(set(missing_npz))}")
        snapshot_store = {}

        def _store_from_snapshot(sym, mode, start):
            key = (sym, mode, start)
            if key not in snapshot_store:
                stores = load_npz(mode, [sym], start, npz_dir=str(npz_dir))
                data = stores.get(sym) if isinstance(stores, dict) else stores
                if data is None and isinstance(stores, dict) and stores:
                    data = next(iter(stores.values()))
                snapshot_store[key] = data
            return snapshot_store[key]

        engine._store = _store_from_snapshot

    sides, errors = {}, {}
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    db_hash = sha256_file(db_path)
    for index, (ss, loss_count) in enumerate(selected.items(), 1):
        print(f"[gain-protection-lab] {index}/{len(selected)} {ss} losing_stop_fills={loss_count}", flush=True)
        if active[ss].get("error"):
            errors[ss] = active[ss]["error"]
            continue
        try:
            prepared = engine.prepare(ss, args.window_days)
            if not prepared:
                errors[ss] = "ENGINE_PREPARE_FAILED"
                continue
            sides[ss] = run_side(ss, loss_count, active[ss], prepared, args.window_days, npz_dir)
        except Exception as exc:
            errors[ss] = f"{type(exc).__name__}:{exc}"
        if index % 5 == 0 or index == len(selected):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps({
                "schema_version": 1, "status": "RUNNING", "started_at": started,
                "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "inventory": str(inventory_path), "inventory_sha256": sha256_file(inventory_path),
                "db": str(db_path), "db_file_sha256": db_hash, "window_days": args.window_days,
                "selected_sym_sides": selected, "completed_sym_sides": sorted(sides),
                "frozen_npz_dir": str(npz_dir) if npz_dir else None,
                "errors": errors, "sides": sides,
            }, indent=2))
            print(f"[gain-protection-lab] checkpoint sides={len(sides)} errors={len(errors)}", flush=True)
    out_path.write_text(json.dumps({
        "schema_version": 1, "status": "COMPLETE", "started_at": started,
        "completed_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "inventory": str(inventory_path), "inventory_sha256": sha256_file(inventory_path),
        "db": str(db_path), "db_file_sha256": db_hash, "window_days": args.window_days,
        "selected_sym_sides": selected, "completed_sym_sides": sorted(sides),
        "frozen_npz_dir": str(npz_dir) if npz_dir else None,
        "errors": errors, "sides": sides,
    }, indent=2))
    print(f"[gain-protection-lab] COMPLETE sides={len(sides)}/{len(selected)} errors={len(errors)} out={out_path}", flush=True)


if __name__ == "__main__":
    main()
