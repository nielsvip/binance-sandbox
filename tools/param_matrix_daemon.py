#!/usr/bin/env python3
"""param_matrix_daemon.py — 24/7 parallel filler of the REAL param matrix (USER 2026-07-21).

"whoever writes to LAB_MATRIX changes to the other matrix that tests useful params" —
this daemon fills param_results_stocks.db (param_cells) — the store behind
SWITCH_MATRIX_TRB.xlsx and PARAM_BASELINE_STOCKS.xlsx — with FAITHFUL Tier-2 engine
runs (persym_baseline_campaign.run_symbol → real backtest_v8_engine, real live code),
one (symbol, param, value) unit at a time, N workers in parallel.

Differences vs the */30 cron campaign (which stays on):
  * parallel workers instead of one serialized pass (the cron's flock+run_seq was the
    6-hours-per-few-rows bottleneck)
  * PRIORITY symbols first — ARM MU NVDA ROKU AXTI HAO MNTS TTD get EVERY cell before
    the rest of symbols_trb_long/short
  * FULL manifest (no --param-limit 24)
Same store, same campaign name, same already_tested dedupe, same honest fail semantics
(engine died -> no cell, retried later). Canonical pooled CSVs remain the cron's job.

Usage (S1): PSC_CAMPAIGN=stocks_baseline_v2_s4h python tools/param_matrix_daemon.py --tag w1
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

os.environ.setdefault("PSC_CAMPAIGN", "stocks_baseline_v2_s4h")
SBX = Path(os.environ.get("V8_SBX", "/home/niels/binance-sandbox"))
if not SBX.exists():
    SBX = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SBX))
sys.path.insert(0, str(SBX / "tools"))
import persym_baseline_campaign as psc  # noqa: E402  (the faithful runner + metrics)
import param_results_store as prs  # noqa: E402
import universe_registry as ur  # noqa: E402
from backtest_data_contract import audit_npz  # noqa: E402

CLAIM_DB = SBX / "data" / "param_matrix_claims.db"
PRIORITY_SYMS = ["MU", "HAO", "NVDA", "VT"]  # USER 2026-07-21 evening: the first four keys are
# MU_LONG, HAO_SHORT, NVDA_LONG, VT_LONG — worked ONE ticker at a time (see tools/matrix_focus.py)
BASELINE_FAIL_LIMIT = 5  # consecutive safe-baseline failures before this worker gives up
BASELINE_BACKOFF_CAP_S = 900  # 15min cap (2026-07-29: was a flat 60s spin for HOURS on TTD_SHORT)
# psc.run_symbol (persym_baseline_campaign.py) raises SystemExit from TWO distinct checks, both
# reachable from this daemon's only two run_symbol call sites (ensure_safe_baseline, run_unit):
#   STARTUP_GAP        — source/NPZ changed BEFORE this call even started (checked at the top of
#                         run_symbol, no subprocess spawned yet, nothing to quarantine).
#   MID_RUN_QUARANTINE — source/NPZ changed WHILE the engine subprocess was running (checked
#                         after it exits; the stale jsonl/audit/stamp files are renamed with a
#                         .contract_changed_during_run. suffix BEFORE this raises).
# 2026-07-29: confirmed via /home/niels/logs/param_matrix_rm1.log that a worker started BEFORE
# this fix was deployed (pid 2688641, running the pre-reexec code in memory) hit STARTUP_GAP and
# died with the bare uncaught message (no re-exec) — expected for a process that predates the
# fix, not a gap in the fix itself. Both reasons are named here explicitly so a future reader
# never has to guess which one fired from a bare exception string.
CONTRACT_EXIT_STARTUP_GAP = "matrix contract source changed after worker start"
CONTRACT_EXIT_MID_RUN = "matrix contract changed during engine run"


def reexec_self(reason, worker):
    """Catch-and-reload for BOTH psc.run_symbol contract-exit reasons (STARTUP_GAP and
    MID_RUN_QUARANTINE above). Whichever fired, persym_baseline_campaign.py has already done
    the correct thing on its side (MID_RUN_QUARANTINE: renamed the stale in-flight result before
    raising; STARTUP_GAP: nothing ran yet, nothing to quarantine) — this function's only job is
    making sure the WORKER does not exit and stay dead (2026-07-28 23:06: 8 workers died this way
    on a config_tradier.py sync and nothing relaunched them). _MATRIX_PROCESS_SOURCE_SIGNATURE
    and matrix_contract_fingerprint() are computed once at module import, so the only correct
    reload is a fresh interpreter — os.execv keeps the same PID (claims in CLAIM_DB stay valid)
    and re-imports every contract file from disk, so the next pass is fingerprinted against the
    NEW contract, never the stale one."""
    if CONTRACT_EXIT_STARTUP_GAP in reason:
        kind = "STARTUP_GAP"
    elif CONTRACT_EXIT_MID_RUN in reason:
        kind = "MID_RUN_QUARANTINE"
    else:
        kind = "UNKNOWN_SYSTEMEXIT"
    print(
        f"[{worker}] contract-reload trigger={kind}: {reason} — any in-flight result was already "
        "quarantined by persym_baseline_campaign.py before this raised; re-exec'ing a fresh "
        "interpreter to reload the contract (never continuing under a stale fingerprint)",
        flush=True,
    )
    sys.stdout.flush()
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except OSError as exc:
        print(f"[{worker}] re-exec FAILED ({exc!r}) — exiting for the watchdog to relaunch", flush=True)
        sys.exit(1)


def claims():
    con = sqlite3.connect(str(CLAIM_DB), timeout=120)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS claims (unit TEXT PRIMARY KEY, worker TEXT, ts REAL)")
    return con


def release_dead_claims(ccon):
    """Drop claims held by workers that no longer exist.

    2026-07-21: claims are named 'pmx:<tag>:<pid>' and honoured for 1800s. After every fleet
    restart the dead generation's claims kept the whole queue locked for up to 30 minutes, so
    a freshly launched fleet sat idle instead of running engines (measured: 14 workers, 2 live
    engines). A claim whose PID is gone is not work in progress — it is debris."""
    try:
        rows = ccon.execute("SELECT unit, worker FROM claims").fetchall()
    except sqlite3.OperationalError:
        return 0
    dead = []
    for unit, worker in rows:
        pid = str(worker).rsplit(":", 1)[-1]
        if pid.isdigit() and not Path(f"/proc/{pid}").exists():
            dead.append(unit)
    for i in range(0, len(dead), 500):
        try:
            chunk = dead[i:i + 500]
            ccon.execute(f"DELETE FROM claims WHERE unit IN ({','.join('?' * len(chunk))})", chunk)
            ccon.commit()
        except sqlite3.OperationalError:
            break
    return len(dead)


def ordered_syms(first=None):
    syms = [s for s in (first or [])]
    for s in PRIORITY_SYMS + _universe_syms():
        if s not in syms:
            syms.append(s)
    return syms


def _universe_syms():
    out = []
    for fn in ("symbols_trb_long.json", "symbols_trb_short.json"):
        p = SBX / fn
        if p.exists():
            for s in json.loads(p.read_text()):
                if s.upper() not in out:
                    out.append(s.upper())
    return out


def drop_verdicts():
    """USER 2026-07-21: params useless on all tested keys move to the BACKGROUND of the
    queue (still filled eventually — never removed). Source: PARAM_KEEP_DROP.csv."""
    p = SBX / "data" / "reports" / "PARAM_KEEP_DROP.csv"
    dropped = set()
    if p.exists():
        import csv as _csv
        try:
            for r in _csv.DictReader(open(p)):
                if r.get("verdict") in ("DROP_INERT", "DROP_NEG"):
                    dropped.add(r["param"])
        except Exception:
            pass
    return dropped


def useless_knobs():
    """Switches proven unable to return a number (tools/prune_useless_knobs.py).

    RECONNECT = every value bit-identical to baseline (unwired); DEGENERATE = values differ
    from baseline but not from each other. Skipped entirely — running their remaining grid
    burns ~12 engine-minutes per cell to re-learn that the knob does nothing."""
    p = SBX / "data" / "useless_knobs.json"
    if not p.exists():
        return set()
    try:
        d = json.loads(p.read_text())
        return set(d.get("reconnect", [])) | set(d.get("degenerate", []))
    except Exception:
        return set()


DIAGNOSTIC_ONLY_PARAMS = {
    "DC_LOW4_STOP_ENABLED",
    "R1_DC_LOW4_3M_EMERGENCY_ENABLED",
}


def all_cells(manifest_path, all_tiers=False, side=None, include_diagnostics=False):
    cells = [("STOP_PACK", name, cfg) for name, cfg in psc.STOP_PACKS.items()]
    cells += [("TF_EXCLUDE", name.replace("TF_EXCLUDE_", ""), cfg) for name, cfg in psc.TF_EXCLUDE_PACKS.items()]
    # USER 2026-07-21 vectorize-everything: VEC_SCREEN-tier params are screened by the
    # vectorized lane (vec_screen_daemon via v8_vec_sweep) — the slow Tier-2 engine only
    # spends minutes-per-cell on params that NEED the real engine (ENGINE_SCREEN/LIVECALL).
    # --all-tiers overrides that split: the vec screen has been caught silently no-opping knobs
    # it does not implement (WT_DC_ENTRY_THRESHOLD defaults to 0.0 there vs 45 live, so every
    # override >=35 returned 0 trades at every value), so a key that must be COMPLETE and
    # trustworthy gets every manifest param measured by the real engine.
    import json as _json
    tiers = {}
    try:
        tiers = {k: (v.get("sweep_tier") if isinstance(v, dict) else None)
                 for k, v in _json.loads(Path(manifest_path).read_text()).get("params", {}).items()}
    except Exception:
        pass
    dead = useless_knobs()
    for pname, values in psc.load_params(manifest_path, 0):
        if pname in DIAGNOSTIC_ONLY_PARAMS and not include_diagnostics:
            continue  # preserved in reports; explicit opt-in only, never blind matrix fill
        if not all_tiers and tiers.get(pname) == "VEC_SCREEN":
            continue
        if "OPTION" in pname:
            continue  # USER 2026-07-21: not trading options — do not test options params yet
        if pname in dead:
            continue  # proven unable to return a number — see tools/prune_useless_knobs.py
        # A LONG-only focus key cannot be moved by a SHORT-side knob (and vice versa): the
        # engine run costs the same 12 minutes and informs the other side only.
        if side and f"_{'SHORT' if side == 'LONG' else 'LONG'}" in pname.upper():
            continue
        for v in values:
            cells.append((pname, str(v), {pname: v}))
    return cells


def _audit_db_fields(audit):
    result = (audit or {}).get("result", {})
    return {
        "validation_status": (audit or {}).get("status"),
        "contract_fingerprint": (audit or {}).get("contract_fingerprint"),
        "real_closes": int(float(result.get("real_closes", 0) or 0)),
        "mtm_count": int(float(result.get("mtm_count", 0) or 0)),
        "opens_long": int(float(result.get("opens_long", 0) or 0)),
        "opens_short": int(float(result.get("opens_short", 0) or 0)),
        "requested_fill_ratio": float(result.get("requested_fill_ratio", 0) or 0),
        "size_clamp_count": int(float(result.get("size_clamp_count", 0) or 0)),
        "reentry_pending": int(float(result.get("reentry_pending", 0) or 0)),
        "reentry_violations": int(float(result.get("reentry_violations", 0) or 0)),
        "result_audit_json": json.dumps(audit, sort_keys=True),
    }


def ensure_safe_baseline(ctx, sym, side):
    """Create the exact-contract, side-isolated B&H-seeded baseline for one key."""
    con = ctx["con"]
    fp = psc.matrix_contract_fingerprint(sym, side)
    row = con.execute(
        "SELECT gain_per_mo,trades_fingerprint FROM key_baseline "
        "WHERE mode=? AND campaign=? AND symbol=? AND side=? "
        "AND validation_status IN "
        "('PASS','PASS_WITH_CAPACITY_CLAMPS','INCOMPLETE_NO_REAL_CLOSE') "
        "AND contract_fingerprint=?",
        (psc.MODE, psc.CAMPAIGN, sym, side, fp),
    ).fetchone()
    if row:
        ctx["base"][f"{sym}_{side}"] = row[0]
        ctx["base_fp"][f"{sym}_{side}"] = row[1]
        return True
    if not claim(ctx, f"BASELINE|{sym}|{side}|{fp}"):
        return False
    tag = f"__BASELINE_SAFE__{side}"
    by_side = psc.run_symbol(
        sym,
        {},
        tag,
        timeout=ctx["args"].timeout,
        min_avail=ctx["args"].min_avail,
        side=side,
        require_matrix_contract=True,
    )
    if by_side is None:
        return False
    audit = psc.load_matrix_run_audit(tag, sym)
    trades = by_side[side]
    years, bh_long = psc.sym_years_and_bh(sym, psc.START)
    if years is None or bh_long is None:
        print(f"[{ctx['worker']}] {sym}_{side}: baseline missing B&H inputs", flush=True)
        return False
    m = psc.capital_key_metrics(
        trades, years, bh_long, side, (audit or {}).get("result", {})
    )
    trade_fp = prs.trades_fingerprint(trades)
    prs.upsert_baseline(
        con,
        {
            "mode": psc.MODE,
            "account": psc.ACCOUNT,
            "symbol": sym,
            "side": side,
            "campaign": psc.CAMPAIGN,
            "ts": psc.now_iso(),
            "window_start": psc.START,
            "years": round(years, 6),
            **m,
            "overrides_json": "{}",
            "stamp": psc.stamp(),
            "source_file": f"param_matrix_daemon/{psc.CAMPAIGN}/{tag}",
            "tier": "ENGINE",
            "trades_fingerprint": trade_fp,
            **_audit_db_fields(audit),
        },
    )
    con.commit()
    ctx["base"][f"{sym}_{side}"] = m["gain_per_mo"]
    ctx["base_fp"][f"{sym}_{side}"] = trade_fp
    print(
        f"[{ctx['worker']}] baseline {sym}_{side}: gain/mo={m['gain_per_mo']} "
        f"bh/mo={m['bh_per_mo']} status={audit.get('status') if audit else 'MISSING'}",
        flush=True,
    )
    return True


def run_unit(ctx, sym, pname, vlabel, ovr):
    """One faithful Tier-2 engine unit: claim, run, ingest both sides. Returns wrote-any.

    Every row is stamped tier='ENGINE' and carries the realised trade fingerprint, so the
    exporter can tell knob effect from tier gap and from an override the code never read."""
    a = ctx["args"]
    sides = [a.side] if a.safe_contract else [
        s for s in ("LONG", "SHORT") if not a.side or s == a.side
    ]
    need = [s for s in sides if not ctx["tested_local"](s, pname, vlabel)]
    if not need:
        return False
    tag = f"{pname}__{vlabel}".replace("/", "_")[:120]
    if not claim(ctx, f"{sym}|{tag}"):
        return False
    by_side = psc.run_symbol(
        sym,
        ovr,
        tag,
        timeout=a.timeout,
        min_avail=a.min_avail,
        side=(a.side if a.safe_contract else None),
        require_matrix_contract=a.safe_contract,
    )
    if by_side is None:
        return False
    wrote = ingest(ctx, sym, by_side, need, pname, vlabel, ovr, tag)
    return commit_unit(ctx, sym, tag, wrote)


def claim(ctx, unit):
    """Take the work claim for one unit. False = someone else has it (or the DB is busy)."""
    ccon, worker = ctx["ccon"], ctx["worker"]
    try:
        # The old SELECT + INSERT OR REPLACE was not atomic. Two workers could both observe
        # "missing", then each replace the row and run the same full-history engine against
        # the same cache files. BEGIN IMMEDIATE serializes the decision; never steal a live
        # worker's unit.
        ccon.execute("BEGIN IMMEDIATE")
        row = ccon.execute("SELECT worker, ts FROM claims WHERE unit=?", (unit,)).fetchone()
        if row and row[0] != worker and time.time() - row[1] < 1800:
            ccon.rollback()
            return False
        if row:
            ccon.execute(
                "UPDATE claims SET worker=?,ts=? WHERE unit=?",
                (worker, time.time(), unit),
            )
        else:
            ccon.execute(
                "INSERT INTO claims(unit,worker,ts) VALUES (?,?,?)",
                (unit, worker, time.time()),
            )
        ccon.commit()
        return True
    except sqlite3.OperationalError:
        try:
            ccon.rollback()
        except sqlite3.OperationalError:
            pass
        time.sleep(2)
        return False


def commit_unit(ctx, label, tag, wrote):
    """Commit a unit's rows. A commit that never lands is a FAILURE, never a result."""
    con, worker = ctx["con"], ctx["worker"]
    try:
        prs.busy_retry(con.commit, tries=120, delay=5)
    except sqlite3.OperationalError as exc:
        print(f"[{worker}] {label} {tag}: COMMIT FAILED after 10min ({exc}) — "
              f"{wrote} row(s) DISCARDED, unit will be retried", flush=True)
        return False
    print(f"[{worker}] {label} {tag}: rows={wrote}", flush=True)
    return wrote > 0


def ingest(ctx, sym, by_side, need, pname, vlabel, ovr, tag):
    """Score one symbol's trades and write its cells. Returns rows written (uncommitted)."""
    con = ctx["con"]
    row_st = psc.artifact_stamp(tag, sym, ctx["st"])
    sym_years, bh_long = psc.sym_years_and_bh(sym, psc.START)
    sym_years = sym_years or ctx["years"]
    wrote = 0
    for side in need:
        sym_intervals = psc.collapse_intervals(ctx["intervals"].get((sym, side), []), ctx["censor"])
        uni_trades = [t for t in by_side[side] if psc.is_in_intervals(t.get("entry_ts", 0), sym_intervals)]
        if not sym_intervals and f"{sym}_{side}" not in ctx["keys"]:
            uni_trades = by_side[side]  # priority key outside registry universe: record full-window trades
        uni_rets = [float(t["pnl_pct"]) for t in uni_trades]
        if sym_intervals:
            u_start = max(psc.START, str(sym_intervals[0][0])[:10])
            u_years, u_bh = psc.sym_years_and_bh(sym, u_start)
            u_years = u_years or sym_years
        else:
            u_years, u_bh = sym_years, bh_long
        audit = psc.load_matrix_run_audit(tag, sym) if ctx["args"].safe_contract else None
        if ctx["args"].safe_contract:
            m = psc.capital_key_metrics(
                uni_trades,
                u_years,
                u_bh if u_bh is not None else bh_long,
                side,
                (audit or {}).get("result", {}),
            )
        else:
            m = psc.hold_metrics(
                uni_trades,
                u_years,
                psc.key_metrics(
                    uni_rets,
                    u_years,
                    u_bh if u_bh is not None else bh_long,
                    side,
                ),
            )
        bkey = sym + "_" + side
        dvb = (m["gain_per_mo"] - ctx["base"][bkey]) if bkey in ctx["base"] else None
        fp = prs.trades_fingerprint(uni_trades)
        # An override that leaves the trade list bit-identical to the baseline did not reach the
        # code. Report 0, flag inert=1 — never let a stale-baseline offset masquerade as a gain.
        is_inert = 1 if ctx["base_fp"].get(bkey) and fp == ctx["base_fp"][bkey] else 0
        if is_inert:
            dvb = 0.0
        try:
            vnum = float(vlabel)
        except ValueError:
            vnum = None
        prs.insert_cell(con, {"mode": psc.MODE, "symbol": sym, "side": side, "campaign": psc.CAMPAIGN,
                              "param": pname, "value_json": vlabel, "value_num": vnum, **m,
                              "delta_vs_baseline_gain_mo": (round(dvb, 4) if dvb is not None else None),
                              "ts": psc.now_iso(), "overrides_json": json.dumps(ovr), "stamp": row_st,
                              "tier": "ENGINE", "baseline_stamp": ctx["base_fp"].get(bkey),
                              "trades_fingerprint": fp, "inert": is_inert,
                              **(_audit_db_fields(audit) if audit else {}),
                              "source_file": f"param_matrix_daemon/{psc.CAMPAIGN}/{tag}"})
        wrote += 1
    return wrote


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="w1")
    ap.add_argument("--first", default="", help="comma-separated symbols this worker processes first (dedicated lane)")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--min-avail", type=int, default=8000)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--all-tiers", action="store_true",
                    help="also measure VEC_SCREEN-tier params with the real engine (a key that "
                         "must be COMPLETE cannot rely on a screen that silently no-ops knobs)")
    ap.add_argument(
        "--include-diagnostics",
        action="store_true",
        help=(
            "explicitly retest diagnostic-only dc_low4 emergency paths; excluded by default "
            "because their preserved evidence is losing churn, not profit-taking research"
        ),
    )
    ap.add_argument("--side", default="", choices=["", "LONG", "SHORT"],
                    help="focus side: drops opposite-side knobs from the work-list")
    ap.add_argument("--only", default="",
                    help="restrict this worker to these symbols entirely (default: whole universe "
                         "after the --first lane drains)")
    ap.add_argument(
        "--safe-contract",
        action="store_true",
        help=(
            "required repaired lane: side-isolated, B&H-seeded, current NPZ contract, "
            "capital-weighted accounting and mandatory re-entry telemetry"
        ),
    )
    a = ap.parse_args()
    first = [s.strip().upper() for s in a.first.split(",") if s.strip()]
    only = [s.strip().upper() for s in a.only.split(",") if s.strip()]
    if a.safe_contract:
        if not a.side or len(only) != 1:
            raise SystemExit("--safe-contract requires exactly one --only symbol and --side")
        if not psc.CAMPAIGN.startswith("stocks_repaired_20260725_"):
            raise SystemExit(
                "--safe-contract refuses historical campaign; set "
                "PSC_CAMPAIGN=stocks_repaired_20260725_<version>"
            )
        contract = audit_npz(
            only[0],
            str(psc.MATRIX_NPZ_DIR / f"{only[0]}.npz"),
            profile="ladder",
            start=psc.START,
        )
        if not contract.valid:
            quarantine = SBX / "data" / "reports" / f"MATRIX_QUARANTINE_{only[0]}_{a.side}.json"
            quarantine.parent.mkdir(parents=True, exist_ok=True)
            quarantine.write_text(
                json.dumps(
                    {
                        "ts": psc.now_iso(),
                        "symbol": only[0],
                        "side": a.side,
                        "status": "QUARANTINED_INVALID_NPZ",
                        "errors": list(contract.errors),
                        "warnings": list(contract.warnings),
                        "stats": contract.stats,
                    },
                    sort_keys=True,
                    indent=2,
                )
                + "\n"
            )
            raise SystemExit(f"{only[0]}_{a.side} quarantined: {contract.errors}")
    worker = f"pmx:{a.tag}:{os.getpid()}"
    manifest = str(SBX / f"data/param_sweep_manifest_{psc.MODE}.json")
    baseline_fail_count = 0
    while True:
        # NEVER die on a lock (2026-07-21: hourly param_matrix rebuild holds a minutes-long
        # write txn; workers must outwait it, not crash-loop through the watchdog)
        try:
            con = prs.connect()
            ccon = claims()
        except sqlite3.OperationalError as e:
            print(f"[{worker}] connect busy ({e}) — retry in 30s", flush=True)
            time.sleep(30)
            continue
        try:
            base = {r[0] + "_" + r[1]: r[2] for r in con.execute(
                "SELECT symbol, side, gain_per_mo FROM key_baseline WHERE mode=? AND campaign=?", (psc.MODE, psc.CAMPAIGN))}
        except sqlite3.OperationalError as e:
            print(f"[{worker}] db busy at pass start ({e}) — retry in 30s", flush=True)
            con.close()
            ccon.close()
            time.sleep(30)
            continue
        freed = release_dead_claims(ccon)
        if freed:
            print(f"[{worker}] released {freed} claims held by dead workers", flush=True)
        keys = (
            {f"{only[0]}_{a.side}"}
            if a.safe_contract
            else set(psc.universe_keys())
        )
        intervals = ur.membership_intervals(psc.ACCOUNT)
        censor = psc.registry_censor_start(intervals)
        years = psc.years_since(psc.START)
        st = psc.stamp()
        cells = all_cells(manifest, a.all_tiers, a.side, a.include_diagnostics)
        dropped = drop_verdicts()
        cells.sort(key=lambda c: c[0] in dropped)  # DROP_* params -> background of the queue
        did_any = False
        import json as _json

        # --all-tiers asks for ENGINE coverage of every param, so a Tier-1 vec cell sitting in
        # the slot must NOT count as tested — otherwise the flag is inert (2026-07-21: 14 workers
        # sat idle because 560 MU_LONG vec cells made every vec-screened param look done).
        tier_clause = " AND tier='ENGINE'" if a.all_tiers else ""

        def tested_local_for(symbol, s, pname, vlabel):
            # local-only dedupe — prs.already_tested falls through to a per-call JOIN on the
            # 1.9GB central DB on every miss, which starved the workers (2026-07-21).
            for _ in range(3):
                try:
                    if a.safe_contract:
                        return con.execute(
                            "SELECT 1 FROM param_cells WHERE mode=? AND campaign=? "
                            "AND symbol=? AND side=? AND param=? AND value_json IN (?,?) "
                            "AND contract_fingerprint=? "
                            "AND validation_status IN "
                            "('PASS','PASS_WITH_CAPACITY_CLAMPS','INCOMPLETE_NO_REAL_CLOSE') "
                            f"{tier_clause} LIMIT 1",
                            (
                                psc.MODE,
                                psc.CAMPAIGN,
                                symbol,
                                s,
                                pname,
                                _json.dumps(vlabel),
                                str(vlabel),
                                psc.matrix_contract_fingerprint(symbol, s),
                            ),
                        ).fetchone() is not None
                    return con.execute(
                        "SELECT 1 FROM param_cells WHERE mode=? AND symbol=? AND side=? AND param=? "
                        f"AND value_json IN (?,?){tier_clause} LIMIT 1",
                        (psc.MODE, symbol, s, pname, _json.dumps(vlabel), str(vlabel))).fetchone() is not None
                except sqlite3.OperationalError:
                    time.sleep(5)
            return True  # db persistently busy — treat as tested THIS PASS, retried next pass
        base_fp = {r[0] + "_" + r[1]: r[2] for r in con.execute(
            "SELECT symbol, side, trades_fingerprint FROM key_baseline WHERE mode=? AND campaign=?",
            (psc.MODE, psc.CAMPAIGN))}
        ctx = {"con": con, "ccon": ccon, "base": base, "base_fp": base_fp, "keys": keys,
               "intervals": intervals, "censor": censor, "years": years, "st": st,
               "worker": worker, "tested_local_for": tested_local_for, "args": a}
        if a.safe_contract:
            try:
                baseline_ok = ensure_safe_baseline(ctx, only[0], a.side)
            except SystemExit as exc:
                # ensure_safe_baseline's psc.run_symbol call is the earliest per-pass chance to
                # hit CONTRACT_EXIT_STARTUP_GAP (contract changed while this worker was idle
                # between passes, before any subprocess even started this pass) as well as
                # CONTRACT_EXIT_MID_RUN (changed while the baseline engine run was in flight).
                con.close()
                ccon.close()
                reexec_self(str(exc), worker)
            if baseline_ok:
                baseline_fail_count = 0
            else:
                baseline_fail_count += 1
                con.close()
                ccon.close()
                if baseline_fail_count >= BASELINE_FAIL_LIMIT:
                    # --safe-contract enforces exactly one --only symbol + --side (argparse
                    # check above), so a broken baseline for that key IS this worker's entire
                    # job — there is no "other queued work for this tag" to fall back to.
                    # 2026-07-28: matrix_rt1 (TTD_SHORT) spun "retry in 60s" on a permanently
                    # broken baseline ("no intended-side trade/MTM record") for HOURS, burning
                    # a slot on known-broken work instead of surfacing the failure. Exit with a
                    # distinct message so the watchdog log makes the root cause (baseline data,
                    # not transient contention) visible instead of relaunching blindly forever.
                    raise SystemExit(
                        f"BASELINE_PERSISTENTLY_UNAVAILABLE: {only[0]}_{a.side} failed "
                        f"{baseline_fail_count} consecutive passes — this worker's only job is "
                        "this key; investigate the baseline (not a transient retry), do not "
                        "just relaunch"
                    )
                backoff = min(60 * (2 ** (baseline_fail_count - 1)), BASELINE_BACKOFF_CAP_S)
                print(
                    f"[{worker}] safe baseline unavailable for {only[0]}_{a.side} "
                    f"(consecutive fail {baseline_fail_count}/{BASELINE_FAIL_LIMIT}); "
                    f"backoff {backoff}s",
                    flush=True,
                )
                time.sleep(backoff)
                continue
        for sym in (only or ordered_syms(first)):
            def tested_local(s, pname, vlabel, _sym=sym):
                return tested_local_for(_sym, s, pname, vlabel)
            ctx["tested_local"] = tested_local
            for pname, vlabel, ovr in cells:
                try:
                    did_any |= run_unit(ctx, sym, pname, vlabel, ovr)
                except SystemExit as exc:
                    # run_unit's psc.run_symbol call can hit either CONTRACT_EXIT_STARTUP_GAP
                    # (contract already stale by the time this cell's call started) or
                    # CONTRACT_EXIT_MID_RUN (changed while this cell's engine subprocess ran) —
                    # both propagate here uncaught (SystemExit is not an Exception subclass) —
                    # 2026-07-28: this killed 8 workers permanently on one config sync.
                    # Quarantine (MID_RUN_QUARANTINE case) already happened inside run_symbol;
                    # this only reloads the worker so it doesn't stay dead.
                    reexec_self(str(exc), worker)
                except Exception as exc:
                    # NEVER die on one bad unit — a single uncaught OperationalError out of
                    # insert_cell took down all 10 workers for hours (2026-07-21, 0 engine cells).
                    print(f"[{worker}] {sym} {pname}={vlabel}: EXC {exc!r} — unit skipped", flush=True)
                    time.sleep(2)
        con.close()
        ccon.close()
        if a.once:
            break
        if not did_any:
            # 2026-07-21: this was a flat 1800s. With the whole fleet on ONE ticker, most workers
            # lose the claim race every pass and slept 30 MINUTES while units freed up around them
            # (measured: 14 workers alive 60min with 3s of CPU each, 3 engines running, memory
            # fine). A pass that finds nothing claimable is normal contention, not an empty queue:
            # re-scan promptly while work remains, and only back off when the ticker is genuinely
            # done. The scan is a few thousand indexed lookups — cheap next to a 9-minute run.
            remaining = 0
            try:
                rcon = prs.connect()
                for sym in (only or ordered_syms(first))[:1]:
                    remaining = sum(
                        1 for pname, vlabel, _o in cells
                        if not rcon.execute(
                            "SELECT 1 FROM param_cells WHERE mode=? AND campaign=? "
                            "AND symbol=? AND side=? AND param=? "
                            f"AND value_json IN (?,?){tier_clause} LIMIT 1",
                            (
                                psc.MODE,
                                psc.CAMPAIGN,
                                sym,
                                a.side,
                                pname,
                                _json.dumps(vlabel),
                                str(vlabel),
                            )).fetchone())
                rcon.close()
            except sqlite3.OperationalError:
                remaining = 1  # unknown -> assume work remains and re-scan soon
            nap = 60 if remaining else 1800
            print(f"[{worker}] pass claimed nothing; {remaining} units still open — sleeping {nap}s", flush=True)
            time.sleep(nap)


if __name__ == "__main__":
    main()
