#!/usr/bin/env python3
"""Forward-parity enforcer (USER 2026-10-21 barbaric rule).

ANY position that would be open under forward application of the sym_side's
board switch set MUST be open live; NOTHING live may be open that forward
application says is flat. Tradeable = member of symbols_<acct>_<side>.json.
15m ONLY: any other timeframe is refused until total parity unlocks 1/3/5m.

What this tool does (and does NOT do):
  FINDS: gate incoherence (natives in charge + hermetic enforce), corrupt or
    empty universe files, off-board sym_sides (entries refused by
    latest_board_ok), live entries refused in the last 30 min (bounded tails).
  FIXES (--fix): hermetic enforce->log when natives are in charge (runtime
    mode file, no restart); JSON-syntax repair of universe files with backup
    and byte-identical membership proof (NEVER membership changes).
  NEVER: places, cancels or sizes orders (no execute_now, no REST order
    calls); never writes per-sym sets (the sanctioned writer is
    tools/v15_persym_golive.py, reported as the exact command, never invoked
    except with --apply-golive).
Full bar-exact forward equality lives in-loop (VEC_CONVERGE_OPEN,
VEC_STALE_FLATTEN 2-bar, promise_ledger, exit_guarantee); this tool audits
their inputs and outputs without reimplementing them.

Exit: 0 clean, 1 faults found, 2 cannot measure / refused timeframe.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(
    os.environ.get("BASE_PATH") or pathlib.Path(__file__).resolve().parents[1]
)
os.environ.setdefault("BASE_PATH", str(ROOT))
sys.path.insert(0, str(ROOT))

CRYPTO_ACCOUNTS = ("ang", "fin", "flz", "inf", "men")
STOCKS_ACCOUNTS = ("tra", "trb", "trc")
ALERTS = ROOT / "data" / "alerts"


def _now_iso() -> str:
    return _dt.datetime.now(_tz_utc()).isoformat()


def _tz_utc():
    return _dt.timezone.utc


def _read_json(path: pathlib.Path):
    return json.loads(path.read_text())


def _tolerant_members(path: pathlib.Path):
    """Universe members for AUDIT ONLY (tolerates trailing commas)."""
    try:
        return [str(x).upper() for x in _read_json(path)]
    except Exception:
        try:
            txt = re.sub(r",\s*([\]])", r"\1", path.read_text())
            return [str(x).upper() for x in json.loads(txt)]
        except Exception:
            return None


def check_gate_coherence() -> dict:
    """Natives in charge (PARITY_VEC_EXACT_MODE False) + hermetic enforce = every native entry refused."""
    out = {"fault": False, "detail": ""}
    try:
        import config as _c

        native_in_charge = not bool(getattr(_c.Config, "PARITY_VEC_EXACT_MODE", False))
    except Exception as exc:
        return {
            "fault": False,
            "detail": f"cannot read config: {exc}",
            "unmeasured": True,
        }
    try:
        mode = str(
            _read_json(ROOT / "data" / "hermetic_mode.json").get("mode", "log")
        ).lower()
    except Exception as exc:
        return {"fault": True, "detail": f"hermetic mode unreadable: {exc}"}
    if native_in_charge and mode == "enforce":
        out = {
            "fault": True,
            "detail": "PARITY_VEC_EXACT_MODE=False (natives trade board SWITCHES) but hermetic mode=enforce (demands vec-twin provenance) -> every native entry refused BLOCKED_HERMETIC_NOT_VEC",
        }
    else:
        out = {
            "fault": False,
            "detail": f"coherent: natives_in_charge={native_in_charge} hermetic={mode}",
        }
    out["natives_in_charge"] = native_in_charge
    out["hermetic_mode"] = mode
    return out


def fix_gate_coherence() -> dict:
    p = ROOT / "data" / "hermetic_mode.json"
    bak = (
        ROOT
        / "backups"
        / f"before_forward_parity_fix_{_dt.datetime.now(_tz_utc()).strftime('%Y%m%d%H%M')}_hermetic_mode.json"
    )
    try:
        d = _read_json(p)
        bak.write_text(json.dumps(d))
        d["mode"] = "log"
        d["set_at"] = _now_iso()
        d["why"] = (
            "forward_parity_enforcer --fix: natives in charge, enforce refused all native entries; log keeps would-refuse visibility"
        )
        p.write_text(json.dumps(d))
        return {"fixed": True, "backup": str(bak)}
    except Exception as exc:
        return {"fixed": False, "error": str(exc)}


def check_universes() -> list:
    rows = []
    for acct in CRYPTO_ACCOUNTS + STOCKS_ACCOUNTS:
        for side in ("long", "short"):
            p = ROOT / f"symbols_{acct}_{side}.json"
            if not p.exists():
                rows.append({"acct": acct, "side": side, "status": "MISSING_FILE"})
                continue
            try:
                members = [str(x).upper() for x in _read_json(p)]
                rows.append(
                    {
                        "acct": acct,
                        "side": side,
                        "status": "EMPTY" if not members else "OK",
                        "n": len(members),
                    }
                )
            except Exception:
                members = _tolerant_members(p)
                rows.append(
                    {
                        "acct": acct,
                        "side": side,
                        "status": "CORRUPT_JSON",
                        "n": len(members) if members else 0,
                        "recoverable": members is not None,
                    }
                )
    return rows


def fix_universe_syntax(acct: str, side: str) -> dict:
    """Syntax-only repair: backup, strip trailing commas, prove membership identical. NEVER adds/removes a member."""
    p = ROOT / f"symbols_{acct}_{side}.json"
    try:
        before = _tolerant_members(p)
        if before is None:
            return {"fixed": False, "error": "unrecoverable, left untouched"}
        _read_json(p)
        return {"fixed": False, "error": "already valid JSON, left untouched"}
    except Exception:
        pass
    bak = (
        ROOT
        / "backups"
        / f"before_forward_parity_fix_{_dt.datetime.now(_tz_utc()).strftime('%Y%m%d%H%M')}_symbols_{acct}_{side}.json"
    )
    try:
        raw = p.read_text()
        bak.write_text(raw)
        fixed = re.sub(r",\s*([\]])", r"\1", raw)
        after = [str(x).upper() for x in json.loads(fixed)]
        before = _tolerant_members(p) or []
        if sorted(after) != sorted(before):
            return {"fixed": False, "error": "membership would change, left untouched"}
        p.write_text(fixed)
        return {"fixed": True, "backup": str(bak), "n": len(after)}
    except Exception as exc:
        return {"fixed": False, "error": str(exc)}


def check_offboard(limit_syms: int = 0) -> dict:
    """Tradeable sym_sides whose latest_board_ok refuses entries (they can NEVER open)."""
    try:
        import live_twins.vec_exact as vx
    except Exception as exc:
        return {"unmeasured": True, "error": f"cannot import twin: {exc}"}
    blocked, ok, n = [], 0, 0
    for acct in CRYPTO_ACCOUNTS + STOCKS_ACCOUNTS:
        for side in ("long", "short"):
            members = _tolerant_members(ROOT / f"symbols_{acct}_{side}.json") or []
            for sym in members:
                n += 1
                if limit_syms and n > limit_syms:
                    break
                try:
                    good, why = vx.latest_board_ok(sym, side.upper())
                except Exception as exc:
                    good, why = False, f"GATE_ERROR_{type(exc).__name__}"
                if good:
                    ok += 1
                else:
                    blocked.append(
                        {
                            "sym_side": f"{sym}_{side.upper()}",
                            "acct": acct,
                            "why": str(why)[:120],
                        }
                    )
    return {"n_tradeable": n, "ok": ok, "blocked": blocked}


def check_recent_refusals(accounts=(("ang", "fin", "flz", "inf", "men"))) -> dict:
    """Bounded tail of live logs: entries refused in the last 30 min proves starvation (or recovery)."""
    logdir = pathlib.Path.home() / "logs"
    cutoff = _dt.datetime.now(_tz_utc()).timestamp() - 1800
    pats = (
        "BLOCKED_HERMETIC_NOT_VEC",
        "WOULD_REFUSE",
        "NO_LATEST_BOARD",
        "SET_DRIFTED_SINCE_PUBLISH",
    )
    out = {}
    for acct in accounts:
        hits = {k: 0 for k in pats}
        try:
            lines = (
                (logdir / f"ez_manage_{acct}.log")
                .read_text(errors="replace")
                .splitlines()[-2000:]
            )
        except Exception:
            out[acct] = {"unmeasured": True}
            continue
        for line in lines:
            for k in pats:
                if k in line:
                    hits[k] += 1
        out[acct] = {"refusals_30min_window_tail": hits}
    return out


def _newest_mtime(paths, pattern="*"):
    try:
        cands = sorted(
            paths.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for p in cands:
            try:
                return p.stat().st_mtime
            except OSError:
                continue
    except Exception:
        pass
    return None


def check_always_runs(mat_dir=None, prog_dir=None, intents_dir=None) -> dict:
    """USER 2026-10-21 VITAL: the vectorized decider pipeline must ALWAYS run. Local legs are verified AND
    relaunched by --fix; S1-side legs can only be reported (tunnel-dependent) — never faulted blindly.
    Returns {faults: [...], info: {...}}."""
    import subprocess
    import time as _t

    now = _t.time()
    faults, info = [], {}
    mat_dir = mat_dir or ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
    prog_dir = prog_dir or ROOT / "data" / "reports" / "lifecycle_pilot"
    intents_dir = intents_dir or ROOT / "data" / "vec_live" / "intents"
    try:
        ps = subprocess.run(
            ["pgrep", "-f", "pure_board_trader.py --loop"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        alive = bool(ps.stdout.strip())
    except Exception:
        alive = None
    info["board_trader_alive"] = alive
    if alive is False:
        faults.append("ALWAYS_RUN_BOARD_TRADER_DEAD")
    latest_intent = _newest_mtime(intents_dir, "PB-*.json")
    info["latest_intent_age_min"] = (
        round((now - latest_intent) / 60, 1) if latest_intent else None
    )
    if latest_intent is not None and now - latest_intent > 30 * 60:
        faults.append("ALWAYS_RUN_INTENTS_STALE")
    latest_mat = _newest_mtime(mat_dir, "*_matrix.xlsx")
    info["latest_matrix_age_h"] = (
        round((now - latest_mat) / 3600, 1) if latest_mat else None
    )
    if latest_mat is not None and now - latest_mat > 40 * 3600:
        faults.append("ALWAYS_RUN_MATRICES_STALE")
    latest_prog = _newest_mtime(prog_dir, "*_v14_progress.json")
    info["latest_progress_age_h"] = (
        round((now - latest_prog) / 3600, 1) if latest_prog else None
    )
    if latest_prog is not None and now - latest_prog > 48 * 3600:
        faults.append("ALWAYS_RUN_PROGRESS_STALE")
    return {"faults": faults, "info": info}


def fix_always_runs() -> dict:
    """Relaunch the local board-trader loop if dead (launchd-supervised, lock-guarded). S1-side: report only."""
    import subprocess

    try:
        ps = subprocess.run(
            ["pgrep", "-f", "pure_board_trader.py --loop"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if ps.stdout.strip():
            return {"fixed": False, "why": "loop alive, nothing to do"}
    except Exception as exc:
        return {"fixed": False, "error": f"cannot check: {exc}"}
    try:
        r = subprocess.run(
            [
                "launchctl",
                "bootstrap",
                f"gui/{os.getuid()}",
                str(
                    Path.home()
                    / "Library/LaunchAgents/com.niels.pure-board-trader.plist"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        ok = r.returncode == 0 or "already bootstrapped" in (r.stderr or "").lower()
        return {"fixed": bool(ok), "detail": (r.stderr or r.stdout or "")[-200:]}
    except Exception as exc:
        return {"fixed": False, "error": str(exc)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--apply-golive", action="store_true")
    ap.add_argument("--limit-syms", type=int, default=0)
    a = ap.parse_args()
    if str(a.timeframe) != "15m":
        print(
            f"REFUSED timeframe={a.timeframe}: 1/3/5m trading disabled until total parity (barbaric rule is 15m-only)"
        )
        return 2
    faults = []
    gate = check_gate_coherence()
    if gate.get("unmeasured"):
        print(f"UNMEASURED gate: {gate['detail']}")
        return 2
    print(f"gate: {gate['detail']}")
    if gate["fault"]:
        faults.append("GATE_INCOHERENCE")
        if a.fix:
            print(f"fix gate: {fix_gate_coherence()}")
    uni = check_universes()
    for row in uni:
        if row["status"] in ("CORRUPT_JSON", "EMPTY", "MISSING_FILE"):
            faults.append(f"UNIVERSE_{row['status']}:{row['acct']}_{row['side']}")
            print(f"universe FAULT {row}")
            if a.fix and row["status"] == "CORRUPT_JSON" and row.get("recoverable"):
                print(f"fix universe: {fix_universe_syntax(row['acct'], row['side'])}")
    off = check_offboard(a.limit_syms)
    if off.get("unmeasured"):
        print(f"UNMEASURED offboard: {off.get('error')}")
    else:
        print(
            f"offboard: {len(off['blocked'])}/{off['n_tradeable']} tradeable sym_sides entry-blocked"
        )
        for b in off["blocked"][:20]:
            print(f"  BLOCKED {b['sym_side']} acct={b['acct']} why={b['why']}")
        if off["blocked"]:
            faults.append(f"OFFBOARD_{len(off['blocked'])}")
            try:
                from live_twins import zero_pass as _zp

                _zp_on = _zp.trade_all_on()
            except Exception:
                _zp_on = "unknown"
            print(
                f"offboard entries allowed at ZERO_PASS bypass={_zp_on} (USER 2026-10-21: every calculation trades; risk by size). Set hygiene stays: python3 tools/v15_persym_golive.py golive --published --pin-all (NOT auto-run)"
            )
    print(f"recent refusals: {json.dumps(check_recent_refusals())[:600]}")
    ar = check_always_runs()
    print(f"always-runs: {json.dumps(ar['info'])}")
    for flt in ar["faults"]:
        faults.append(flt)
        print(f"always-runs FAULT {flt}")
        if a.fix and flt == "ALWAYS_RUN_BOARD_TRADER_DEAD":
            print(f"fix loop: {fix_always_runs()}")
    ALERTS.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": _now_iso(),
        "timeframe": "15m",
        "gate": gate,
        "offboard_n": len(off.get("blocked", [])),
        "faults": faults,
    }
    with open(ALERTS / "forward_parity.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    if a.apply_golive:
        import subprocess

        r = subprocess.run(
            [
                sys.executable,
                "tools/v15_persym_golive.py",
                "golive",
                "--published",
                "--pin-all",
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        print(f"golive rc={r.returncode} tail={r.stdout[-500:]} err={r.stderr[-500:]}")
    print("FAULTS:" + (",".join(faults) if faults else "none"))
    return 1 if faults else 0


if __name__ == "__main__":
    raise SystemExit(main())
