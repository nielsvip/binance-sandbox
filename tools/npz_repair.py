#!/usr/bin/env python3
"""npz_repair — heal the NPZs no 15m top-up can ever fix, by ADOPTING the deepest copy that exists and tailing it.

USER 2026-10-16: "s2 has backups of the npz;s so no complete reruns are ever needed just topups; make sure they
get appended to never overwritten with smaller versions".

tools/npz_bar_refresh.py appends the closed 15m bar to every live NPZ, but three classes refuse forever and were
repaired by nobody (measured on S1, 2026-10-16 20:3xZ):
  TOO_SHORT_TO_APPEND      CDW/ADP/AEM/AXTI — 1-row live stubs written 2026-10-13 by npz_live_generator over full
                           stores; every cycle logged `cannot extend 618 row keys` for 3 days.
  REFUSE_LEGACY_HTF_ALIGN  ASTERUSDT — no htf_align=causal_v3, so appending would shift its causal rows twice.
                           17 days behind on S1 AND s2.
  ERROR / GUARD_REFUSED    a key family the current builder no longer emits (ZECUSDC: the monthly leg; now handled
                           by npz_bar_refresh.drop_keys for crypto, so it reaches the repair queue only if it stays
                           unextendable for another reason).

What this does per symbol: fingerprint every copy that exists (this host, the peer hosts, both hosts' backup
dirs), pick the best by (appendable, rows, last bar), and install it ONLY when it is strictly deeper than what is
there now. Then hand the symbol to npz_bar_refresh --symbols to walk it up to the latest closed bar. Nothing is
ever rebuilt from klines here and nothing is ever replaced by a shorter store.

usage:
  python3 tools/npz_repair.py --queue                 # report what the loop queued, with every candidate found
  python3 tools/npz_repair.py --queue --apply         # adopt + top up (default max 6 per run, nice'd)
  python3 tools/npz_repair.py --symbols CDW,AEM --apply
  python3 tools/npz_repair.py --queue --apply --peers 10.0.0.4
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("NPZ_REFRESH_ROOT", os.path.expanduser("~/binance-sandbox")))
if not (ROOT / "backtest_v8").exists():
    ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
IND = ROOT / "backtest_v8" / "indicators"
REPORT = ROOT / "data" / "vec_live" / "npz_repair_report.json"
ALERT = ROOT / "data" / "alerts" / "npz_unrepairable.json"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new"]
FINGERPRINT = r"""
import glob, json, os, sys
import numpy as np
syms = sys.argv[1].split(",")
roots = [r for r in sys.argv[2].split(":") if r]
out = []
for root in roots:
    for s in syms:
        for p in sorted(glob.glob(os.path.join(os.path.expanduser(root), s + ".npz"))):
            try:
                with np.load(p, allow_pickle=True) as z:
                    if "timestamps" not in z.files:
                        out.append({"symbol": s, "path": p, "error": "no timestamps"})
                        continue
                    ts = np.asarray(z["timestamps"]).astype(np.int64).reshape(-1)
                    ha = str(np.asarray(z["htf_align"]).ravel()[0]) if "htf_align" in z.files else None
                    dtm = float(np.median(np.diff(ts.astype("float64")))) if len(ts) > 1 else 0.0
                    out.append({"symbol": s, "path": p, "rows": int(len(ts)), "first_ts": int(ts[0]), "last_ts": int(ts[-1]),
                                "dt_med": dtm, "keys": int(len(z.files)), "htf_align": ha, "bytes": int(os.path.getsize(p))})
            except Exception as e:
                out.append({"symbol": s, "path": p, "error": type(e).__name__})
print(json.dumps(out))
"""


def log(msg):
    print(f"[npz_repair {time.strftime('%H:%M:%S', time.gmtime())}Z] {msg}", flush=True)


def is_crypto(symbol):
    return str(symbol).upper().endswith(("USDT", "USDC", "BUSD"))


def local_roots():
    """every store on THIS host that may hold a deeper copy, newest-authority last.

    NPZ_REPAIR_EXTRA_ROOTS (colon-separated) is how an off-host backup joins the search: the Mac's external
    drives hold copies S1 has never seen (MEASURED 2026-10-16: /Volumes/SSD2T/tmp_s1_relay/ADP.npz = 15895 rows
    on a clean 900 s grid over 737 days, against a 1-ROW stub on S1 — and the same drive's CDW copy has 43904
    rows with dt_med=0 s over a 20708-day span, which is why grid_ok() exists). The Mac is NAT'd, so it pushes
    into a staging dir named here; nothing is adopted without passing the same fingerprint checks."""
    out = [str(IND), str(Path.home() / "binance" / "backtest_v8" / "indicators")]
    # 2026-10-17: the QUARANTINE and PURGE dirs are searched too. They are not junk -- they are where the deepest
    # stores on the host turned out to be sitting. MEASURED that day against the 566-symbol tradeable union: S1's
    # live dir held 332 NPZs while IND/_quarantine held 482 and IND/.purged_npz/20261013002948 held 191, and of the
    # 566, 63 symbols were MISSING from live while a 400-1875 day copy sat in the purge dir (C98USDT 180002 rows /
    # 1875 d, CELOUSDT 176738 / 1841 d) and 89 more were live but SHALLOWER than an existing copy (STORJUSDT live
    # 207076 rows vs 1011590 purged; PLTR live 3924 vs 123832 on s2). Adoption still has to earn it: better()
    # installs a candidate only when it is strictly deeper AND grid_ok() AND appendable(), and topup() walks the
    # adopted store back up to the latest closed bar, so a deep-but-old copy never costs a recent bar.
    out += [str(IND / "_quarantine")]
    for parent in (".purged_npz", ".purged_variants"):
        out += [str(d) for d in sorted((IND / parent).glob("*")) if d.is_dir()]
    out += [str(p) for p in sorted((ROOT / "backups").glob("npz*")) if p.is_dir()]
    out += [r for r in os.environ.get("NPZ_REPAIR_EXTRA_ROOTS", "").split(":") if r.strip()]
    out += [str(ROOT / "data" / "vec_live" / "npz_adopt_inbox")]
    return [r for r in dict.fromkeys(out) if Path(os.path.expanduser(r)).is_dir()]


def peer_roots():
    return ["~/binance-sandbox/backtest_v8/indicators", "~/binance-sandbox/backups/npz*",
            "~/binance/backtest_v8/indicators",
            # s2 keeps its own quarantine (482 files) and the pre-rebuild backup that holds the only deep stock
            # stores: PLTR 123832 rows / USO 112565 / MP 101104 against 2.4-5.2k live on S1.
            "~/binance-sandbox/backtest_v8/indicators/_quarantine",
            "~/binance-sandbox/backups/npz_before_history_rebuild_*"]


def fingerprint_local(symbols):
    py = str(ROOT / ".venv" / "bin" / "python")
    py = py if Path(py).exists() else sys.executable
    r = subprocess.run([py, "-c", FINGERPRINT, ",".join(symbols), ":".join(local_roots())], capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        log(f"local fingerprint failed rc={r.returncode}: {(r.stderr or '')[-200:]}")
        return []
    rows = json.loads(r.stdout.strip().splitlines()[-1])
    for d in rows:
        d["host"] = "local"
    return rows


def fingerprint_peer(host, symbols):
    cmd = SSH + [host, "cd ~/binance-sandbox && .venv/bin/python -c " + json_quote(FINGERPRINT) + " " + ",".join(symbols) + " " + ":".join(peer_roots())]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        log(f"{host} fingerprint failed rc={r.returncode}: {(r.stderr or '')[-200:]}")
        return []
    try:
        rows = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception as e:
        log(f"{host} fingerprint unparsable: {type(e).__name__}")
        return []
    for d in rows:
        d["host"] = host
    return rows


def json_quote(s):
    return "'" + s.replace("'", "'\"'\"'") + "'"


def grid_ok(c):
    """is this copy on the 15m grid at all? (npz_guard's own test: 600 < median dt < 1200)

    MEASURED 2026-10-16: /Volumes/SSD2T/tmp_s1_relay/CDW.npz has 43904 rows — more than ANY live store — over a
    20708-DAY span, i.e. garbage timestamps. Ranking by row count alone would have adopted it over the 1-row stub
    and called CDW repaired. A candidate has to be on the grid before its depth means anything."""
    dtm = float(c.get("dt_med") or 0.0)
    if not (600.0 < dtm < 1200.0 if dtm else False):
        return False
    span_d = (int(c.get("last_ts") or 0) - int(c.get("first_ts") or 0)) / 86400.0
    return 0 < span_d <= float(os.environ.get("NPZ_MAX_SPAN_DAYS", "4000"))  # 20708 d = junk rows at the head


def appendable(c, symbol):
    """can tools/npz_bar_refresh.py extend this copy at all? (its two hard refusals, same thresholds)"""
    if c.get("error") or not c.get("rows"):
        return False
    if int(c["rows"]) < int(os.environ.get("NPZ_MIN_APPENDABLE_ROWS", "8")):
        return False
    if int(c["last_ts"]) > 1e11:
        return False
    if not grid_ok(c):
        return False
    if is_crypto(symbol) and c.get("htf_align") != "causal_v3":
        return False
    return True


def rank(c, symbol):
    return (1 if appendable(c, symbol) else 0, int(c.get("rows") or 0), int(c.get("last_ts") or 0), int(c.get("keys") or 0))


def better(cand, cur, symbol):
    """strictly deeper, never shorter: more rows, or the same rows with a newer tail and no loss of appendability."""
    if cand is None:
        return False, "no candidate"
    if cur is None:
        return True, "no current store"
    if int(cand.get("rows") or 0) < int(cur.get("rows") or 0):
        return False, f"candidate shorter ({cand.get('rows')} < {cur.get('rows')} rows) — never shrink"
    if rank(cand, symbol) <= rank(cur, symbol):
        return False, "current copy already the best available"
    return True, f"rows {cur.get('rows')} -> {cand.get('rows')}, last_ts {cur.get('last_ts')} -> {cand.get('last_ts')}, appendable {appendable(cur, symbol)} -> {appendable(cand, symbol)}"


def fetch(cand, symbol, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if cand["host"] == "local":
        shutil.copy2(cand["path"], dest)
        return True
    r = subprocess.run(["rsync", "-az", "--timeout=120", "-e", " ".join(SSH), f"{cand['host']}:{cand['path']}", str(dest)], capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        log(f"{symbol} rsync from {cand['host']} failed rc={r.returncode}: {(r.stderr or '')[-200:]}")
        return False
    return True


def verify_file(path, symbol, expect_rows):
    """fingerprint one concrete file in its own directory (the staged copy, or the installed store)."""
    py = str(ROOT / ".venv" / "bin" / "python")
    py = py if Path(py).exists() else sys.executable
    r = subprocess.run([py, "-c", FINGERPRINT, symbol, str(path.parent)], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return None
    for d in json.loads(r.stdout.strip().splitlines()[-1]):
        if Path(d["path"]).resolve() == path.resolve() and int(d.get("rows") or 0) >= int(expect_rows):
            return d
    return None


def rebuild(symbol):
    """last resort, explicit only: no host holds a deeper copy, so the store is rebuilt from this host's klines.

    MEASURED 2026-10-16: adoption healed AEM (1 -> 25083 rows from s2's backup, DEEPER than AEM's own 19177
    klines rows — which is exactly why adoption is tried first) and AXTI (1 -> 464). It could do nothing for
    CDW, ADP (1-row stubs with no deeper copy on s1/s1-live/s1-backups/s2/s2-backups/Mac) or ASTERUSDT (36009
    rows but legacy htf_align, same on both hosts). Their klines ARE full and current (CDW 15175 rows since
    2024-08, ADP 14418 since 2024-09, ASTERUSDT 37668 since 2025-09), so a rebuild is the only path and it is
    never silent: --rebuild is off by default so the 2-hourly cron can never start one under board load."""
    py = str(ROOT / ".venv" / "bin" / "python")
    py = py if Path(py).exists() else sys.executable
    mode = "crypto" if is_crypto(symbol) else "tradier"
    t0 = time.time()
    r = subprocess.run(["nice", "-n", "19", py, str(ROOT / "backtest_v8_precompute.py"), "--symbols", symbol, "--mode", mode],
                       capture_output=True, text=True, timeout=float(os.environ.get("NPZ_REPAIR_REBUILD_TIMEOUT_S", "5400")), cwd=str(ROOT))
    return {"rc": r.returncode, "secs": round(time.time() - t0, 1), "tail": ((r.stdout or "") + (r.stderr or ""))[-300:]}


def last_ts_of(symbol):
    d = verify_file(IND / f"{symbol}.npz", symbol, 1)
    return int(d["last_ts"]) if d else None


def topup(symbol):
    """walk the store forward to the latest closed bar, in as many passes as the appender's per-pass limit needs.

    refresh_symbol refuses a gap over 1400 bars (GAP_TOO_LARGE), so one pass cannot close the hole an adoption
    leaves: AEM came back from s2's backup 25083 rows deep but 3369 bars (35 days) behind, and a single top-up
    returned GAP_TOO_LARGE and left it there. Passes stop the moment a pass stops advancing the last bar."""
    py = str(ROOT / ".venv" / "bin" / "python")
    py = py if Path(py).exists() else sys.executable
    mode = "crypto" if is_crypto(symbol) else "stocks"
    cmd = [py, str(ROOT / "tools" / "npz_bar_refresh.py"), "--symbols", symbol, "--mode", mode, "--w15", "2500", "--workers", "1", "--no-sentiment", "--no-fng"]
    passes, out = int(os.environ.get("NPZ_REPAIR_TOPUP_PASSES", "6")), []
    step = int(os.environ.get("NPZ_REPAIR_STEP_BARS", "1300"))  # under refresh_symbol's gap>1400 refusal
    before = last_ts_of(symbol)
    for i in range(max(1, passes)):
        # each pass asks for a CAPPED cutoff. Without it a hole bigger than the per-pass limit can never close:
        # every pass computes its gap from the wall clock, so AEM (3282 bars short of its own klines) answered
        # GAP_TOO_LARGE six times in a row and stayed where it was.
        latest = (int(time.time()) // 900) * 900 - 900
        target = min(latest, int(before) + step * 900) if before else latest
        r = subprocess.run(cmd + (["--cutoff", str(target)] if before and target < latest else []), capture_output=True, text=True,
                           timeout=float(os.environ.get("NPZ_REPAIR_TOPUP_TIMEOUT_S", "1800")), cwd=str(ROOT))
        tail = [l for l in ((r.stdout or "") + (r.stderr or "")).splitlines() if '"status"' in l]
        now_ts = last_ts_of(symbol)
        out.append({"pass": i + 1, "rc": r.returncode, "last_ts": now_ts, "status": (tail[-1][-220:] if tail else (r.stdout or "")[-120:])})
        if now_ts is None or now_ts == before:
            break
        before = now_ts
    return {"passes": out, "last_ts": before, "rc": out[-1]["rc"] if out else None, "last": out[-1]["status"] if out else ""}


def queue_symbols():
    try:
        q = json.loads((ROOT / "data" / "vec_live" / "npz_repair_queue.json").read_text()).get("syms", {})
    except Exception:
        return []
    return sorted(q, key=lambda s: -float(q[s].get("cycles") or 0))


def repair(symbol, cands, apply_it, allow_rebuild=False):
    cur = next((c for c in cands if c["host"] == "local" and Path(c["path"]).parent == IND and not c.get("error")), None)
    pool = [c for c in cands if not c.get("error")]
    best = max(pool, key=lambda c: rank(c, symbol)) if pool else None
    out = {"symbol": symbol, "current": cur, "best": best, "candidates": sorted(pool, key=lambda c: -rank(c, symbol)[1])[:8]}
    ok, why = better(best, cur, symbol)
    out["decision"] = why
    if not ok:
        out["action"] = "none"
        if apply_it and cur is not None and appendable(cur, symbol):
            # queued for a reason other than a bad store — almost always GAP_TOO_LARGE after an adoption (the
            # appender closes at most 1400 bars per pass). Walk it forward instead of leaving it in the queue.
            out["topup"] = topup(symbol)
            out["after"] = verify_file(IND / f"{symbol}.npz", symbol, 1)
            out["action"] = "topped_up (gap walk)"
            return out
        if cur is not None and not appendable(cur, symbol):
            out["action"] = "UNREPAIRABLE_BY_ADOPTION"
            out["note"] = "no deeper copy on any host — this symbol needs a real rebuild (backtest_v8_precompute), the only case where a top-up cannot work"
            if apply_it and allow_rebuild:
                from tools.npz_bar_refresh import frozen_syms
                if symbol.upper() in frozen_syms():
                    out["action"] = "rebuild skipped: board in flight (RULE#4 freeze)"
                    return out
                before = int((cur or {}).get("rows") or 0)
                out["rebuild"] = rebuild(symbol)
                after = verify_file(IND / f"{symbol}.npz", symbol, 1)
                out["after"] = after
                grew = after is not None and int(after.get("rows") or 0) > before
                out["action"] = "rebuilt" if grew else "rebuild did NOT produce a deeper store — left as it was"
        return out
    if not apply_it:
        out["action"] = "would adopt (dry-run)"
        return out
    from tools.npz_bar_refresh import sym_lock, frozen_syms
    if symbol.upper() in frozen_syms():
        out["action"] = "skipped: board in flight (RULE#4 freeze)"
        return out
    fd = sym_lock(symbol)
    if fd is None:
        out["action"] = "skipped: another refresh holds this symbol"
        return out
    try:
        dest = ROOT / "data" / "vec_live" / "npz_adopt_stage" / f"{symbol}.npz"
        if not fetch(best, symbol, dest):
            out["action"] = "fetch failed"
            return out
        got = verify_file(dest, symbol, best.get("rows") or 0)
        if got is None:
            dest.unlink(missing_ok=True)
            out["action"] = "adopted copy failed verification — discarded"
            return out
        live = IND / f"{symbol}.npz"
        if live.exists():
            bk = ROOT / "backups" / f"npz_before_adopt_{time.strftime('%Y%m%d%H%M')}"
            bk.mkdir(parents=True, exist_ok=True)
            shutil.copy2(live, bk / f"{symbol}.npz")
            out["backup"] = str(bk / f"{symbol}.npz")
        os.replace(dest, live)
        out["adopted"] = {"from": f"{best['host']}:{best['path']}", "rows": got.get("rows"), "last_ts": got.get("last_ts")}
    finally:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        except OSError:
            pass
    out["topup"] = topup(symbol)
    after = verify_file(IND / f"{symbol}.npz", symbol, 1)
    out["after"] = after
    # Say what the top-up ACTUALLY did. This label used to be an unconditional "adopted+topped_up", which reported
    # a success that had not happened: MEASURED 2026-10-17, AXLUSDT and AWEUSDT were adopted from the purge dir and
    # logged "adopted+topped_up" while topup() had returned REFUSE_LEGACY_HTF_ALIGN both times -- the adopted stores
    # carry no htf_align key at all, so npz_bar_refresh refuses them forever (appending would double-shift their
    # causal rows) and they sat 422 h stale wearing a success label. A deep store that cannot be tailed is only
    # half a repair, and the only fix is a real rebuild, so it is named as such and reported as needing one.
    tstat = str((out.get("topup") or {}).get("last") or "")
    reached = after is not None and int(after.get("last_ts") or 0) >= int((out.get("topup") or {}).get("last_ts") or 0)
    refused = any(k in tstat for k in ("REFUSE_LEGACY_HTF_ALIGN", "REFUSE_MS_TIMESTAMPS", "TOO_SHORT_TO_APPEND", "GUARD_REFUSED", "ERROR"))
    if refused or not appendable(got, symbol):
        out["action"] = "ADOPTED BUT NOT TAILABLE — needs rebuild"
        out["note"] = (f"adopted a deeper store ({got.get('rows')} rows) but npz_bar_refresh cannot extend it "
                       f"({tstat[:120] or 'not appendable: htf_align=' + str(got.get('htf_align'))}); it will stay at its "
                       "adopted tail until rebuilt from klines")
    elif reached:
        out["action"] = "adopted+topped_up"
    else:
        out["action"] = "adopted, top-up incomplete (gap remains)"
    # A DEEPER copy can have an OLDER tail (ADP: 14405 rows ending today -> 15895 rows ending 10-07, then walked
    # back to 16101 ending today). Depth is what we adopt for, but a 30D board reads the RECENT window, so if the
    # walk did not restore the tail the adoption made the store worse for its actual use — put the old one back.
    prev_last = int((cur or {}).get("last_ts") or 0)
    now_last = int((after or {}).get("last_ts") or 0)
    if prev_last and now_last < prev_last and out.get("backup"):
        try:
            shutil.copy2(out["backup"], IND / f"{symbol}.npz")
            out["action"] = f"ROLLED BACK — adopted copy was deeper but its tail never caught up ({now_last} < {prev_last})"
            out["after"] = verify_file(IND / f"{symbol}.npz", symbol, 1)
        except OSError as e:
            out["action"] = f"adopted+topped_up but tail REGRESSED and rollback failed: {type(e).__name__}"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols")
    ap.add_argument("--queue", action="store_true", help="take the symbols tools/npz_bar_refresh.py queued as unfixable by a top-up")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--peers", default=os.environ.get("NPZ_REPAIR_PEERS", "10.0.0.4"), help="comma-separated hosts that hold NPZ copies/backups (default: s2)")
    ap.add_argument("--max", type=int, default=int(os.environ.get("NPZ_REPAIR_MAX", "6")))
    ap.add_argument("--rebuild", action="store_true", help="LAST RESORT, explicit only: rebuild from klines when no host holds a deeper copy (never used by the cron)")
    a = ap.parse_args()
    syms = [s.strip().upper() for s in (a.symbols or "").split(",") if s.strip()]
    if a.queue:
        syms += [s for s in queue_symbols() if s not in syms]
    if not syms:
        log("nothing to repair (empty queue, no --symbols)")
        return 0
    syms = syms[: max(1, a.max)]
    log(f"repair set ({len(syms)}): {','.join(syms)} | apply={a.apply} peers={a.peers}")
    cands = fingerprint_local(syms)
    for h in [p.strip() for p in a.peers.split(",") if p.strip()]:
        cands += fingerprint_peer(h, syms)
    reports, unrepairable = [], []
    for s in syms:
        r = repair(s, [c for c in cands if c["symbol"] == s], a.apply, allow_rebuild=a.rebuild)
        reports.append(r)
        log(f"{s}: {r['action']} — {r['decision']}" + (f" | topup {r.get('topup', {}).get('last', '')[:160]}" if r.get("topup") else ""))
        if r["action"] == "UNREPAIRABLE_BY_ADOPTION":
            unrepairable.append(s)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps({"at": time.time(), "apply": a.apply, "reports": reports}, indent=1, sort_keys=True, default=str))
    if unrepairable:
        ALERT.parent.mkdir(parents=True, exist_ok=True)
        ALERT.write_text(json.dumps({"at": time.time(), "syms": unrepairable, "why": "no deeper NPZ copy on any host — needs backtest_v8_precompute"}, indent=1, sort_keys=True))
        log(f"ALERT {len(unrepairable)} symbol(s) cannot be healed by adoption: {','.join(unrepairable)} -> {ALERT}")
    log(f"report -> {REPORT}")
    return 1 if unrepairable else 0


if __name__ == "__main__":
    sys.exit(main())
