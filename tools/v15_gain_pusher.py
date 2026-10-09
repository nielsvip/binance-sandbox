#!/usr/bin/env python3
"""v15_gain_pusher — continuous second-pass gain maximizer for completed 30D sheets.

Per completed sym_side: re-anchor -> repair (0/low trades) -> diagnose every trade
(MFE/MAE, early/late/bad classification) -> greedy diagnosis-driven flips (priority
registry P0/P1 + ledger-driven) -> optional honest v15_pilot rerun from improved set.

NO-LIES: promote iff ledger changed AND gain improved AND still valid. Fresh evals only.
Publishes to a stable dir (never CELL_BY_CELL churn). Isolated pilot dirs per run.

Usage:
  python tools/v15_gain_pusher.py --sym UNIUSDC_LONG [--rerun-sheet]
  python tools/v15_gain_pusher.py --all [--max-syms 20] [--no-rerun-sheet]
  python tools/v15_gain_pusher.py --all --fetch-s1   # stream NPZs from S1 one by one:
      prefetch next while processing current, delete pulled NPZ after its sym_sides done
"""
import argparse, concurrent.futures as cf, json, os, pathlib, shutil, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROG_DIR = ROOT / "data" / "reports" / "lifecycle_pilot"
NPZ_DIR = ROOT / "backtest_v8" / "indicators"
PUSH_DIR = ROOT / "data" / "reports" / "gain_pusher"
REGISTRY = PUSH_DIR / "PRIORITY_SWITCHES.json"
TRACK_LOG = PUSH_DIR / "TRACKING_LOG.md"
EVAL_TIMEOUT = 60
MAX_ROUNDS = 6
_CRASHERS = []


def log(msg):
    print(f"[pusher] {msg}", flush=True)


def valid_npz(sym):
    p = NPZ_DIR / f"{sym}.npz"
    if not p.exists() or p.stat().st_size < 100000:
        return False
    try:
        import zipfile
        z = zipfile.ZipFile(str(p))
        ok = len(z.namelist()) >= 50
        z.close()
        return ok
    except Exception:
        return False


def load_registry():
    # Fleet worker override (coordinator-merged master) wins over everything.
    _ov = os.environ.get("V15_PUSHER_REGISTRY")
    if _ov:
        try:
            return json.load(open(_ov))
        except Exception:
            pass
    # SQL-primary → JSON fallback
    if os.environ.get("PER_SYM_STORE_SQLITE_DISABLED") != "1":
        try:
            import per_sym_store as _pss
            v = _pss.kv_get(_pss.KV_GAIN_PUSHER_PRIORITY)
            if isinstance(v, dict):
                return v
        except Exception:
            pass
    try:
        return json.load(open(REGISTRY))
    except Exception:
        return {}


class S1Streamer:
    """One-by-one NPZ streaming: prefetch next while processing current, delete after use.

    Only targets lacking a valid local NPZ trigger a pull. Pulled files are tracked
    with (size, md5) and deleted only if unchanged and no remaining target needs the sym.
    Pre-existing local NPZs are never deleted; corrupt ones are quarantined, not dropped.
    """
    def __init__(self, host, remaining_syms):
        self.hosts = [host] if host == "s1-pub" else [host, "s1-pub"]
        self.need = {}
        for s in remaining_syms:
            self.need[s] = self.need.get(s, 0) + 1
        self.fetched = {}
        self.futures = {}
        self.ex = cf.ThreadPoolExecutor(max_workers=1)
        for tmp in NPZ_DIR.glob(".fetch_*.tmp"):
            try:
                tmp.unlink()
            except Exception:
                pass

    def _pull(self, sym):
        import hashlib, zipfile
        tmp = NPZ_DIR / f".fetch_{sym}.tmp"
        dst = NPZ_DIR / f"{sym}.npz"
        t0 = time.time()
        try:
            ok = False
            for host in self.hosts:
                for attempt in (1, 2):
                    cmd = ["rsync", "-azL", "--timeout=120", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", f"{host}:~/binance-sandbox/backtest_v8/indicators/{sym}.npz", str(tmp)]
                    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    if p.returncode == 0 and tmp.exists():
                        ok = True
                        break
                    time.sleep(5)
                if ok:
                    break
            if not ok:
                return False
            z = zipfile.ZipFile(str(tmp))
            ok = len(z.namelist()) >= 50
            z.close()
            if not ok:
                tmp.unlink(missing_ok=True)
                return False
            os.chmod(tmp, 0o644)
            if dst.exists():
                q = ROOT / "backups" / "corrupt_npz"
                q.mkdir(parents=True, exist_ok=True)
                try:
                    dst.rename(q / f"{sym}.npz.{time.strftime('%Y%m%d_%H%M%S')}")
                except Exception:
                    pass
            os.replace(tmp, dst)
            h = hashlib.md5(open(dst, "rb").read()).hexdigest()
            self.fetched[sym] = (dst.stat().st_size, h)
            log(f"s1-stream: pulled {sym} via {host} ({dst.stat().st_size / 1e6:.1f}MB in {time.time() - t0:.0f}s)")
            return True
        except Exception as e:
            log(f"s1-stream: pull {sym} failed: {e}")
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return False

    def prefetch(self, sym):
        if valid_npz(sym) or sym in self.futures:
            return
        self.futures[sym] = self.ex.submit(self._pull, sym)

    def ensure(self, sym):
        if valid_npz(sym):
            return True
        fut = self.futures.get(sym)
        if fut is None:
            return self._pull(sym)
        try:
            return bool(fut.result(timeout=660))
        except Exception:
            return False

    def release(self, sym):
        self.need[sym] = self.need.get(sym, 1) - 1
        if self.need[sym] > 0 or sym not in self.fetched:
            return
        import hashlib
        p = NPZ_DIR / f"{sym}.npz"
        try:
            size, h = self.fetched.pop(sym)
            if p.exists() and p.stat().st_size == size and hashlib.md5(open(p, "rb").read()).hexdigest() == h:
                p.unlink()
                log(f"s1-stream: released {sym}.npz (pulled copy, no remaining targets)")
        except Exception as e:
            log(f"s1-stream: release {sym} skipped: {e}")

    def close(self):
        self.ex.shutdown(wait=False)


def timed_eval(prep, overrides, window_days=30, include_ledger=False):
    from tools.opt.v12_pilot import evaluate_prepared_sanitized
    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(evaluate_prepared_sanitized, prep, dict(overrides), window_days, include_ledger)
        try:
            r = fut.result(timeout=EVAL_TIMEOUT)
        except cf.TimeoutError:
            return {"gain_pct": 0.0, "trades": 0, "valid": False, "invalid_reason": f"pusher stall>{EVAL_TIMEOUT}s", "stalled": True}
        except Exception as e:
            return {"gain_pct": 0.0, "trades": 0, "valid": False, "invalid_reason": f"pusher eval raised {type(e).__name__}: {e}"[:160]}
    if not isinstance(r, dict):
        return {"gain_pct": 0.0, "trades": 0, "valid": False, "invalid_reason": "non-dict result"}
    r.setdefault("gain_pct", 0.0)
    r.setdefault("trades", 0)
    r.setdefault("valid", False)
    return r


def diagnose_ledger(symside, prep, overrides):
    import numpy as np
    r = timed_eval(prep, overrides, 30, include_ledger=True)
    side = symside.rsplit("_", 1)[1]
    is_long = side == "LONG"
    led = r.get("ledger") or []
    npz = prep["npz_prepared"]
    close = np.asarray(npz["close_15m"] if "close_15m" in npz else npz["close"])
    n = len(close)
    rows = []
    for t in led:
        if t.get("type") != "CLOSE" or not t.get("qty", 1):
            continue
        be, bx = int(t.get("bar_entry", -1)), int(t.get("bar_exit", -1))
        if be < 0 or bx < 0 or be >= n or bx >= n:
            continue
        ep = float(t.get("entry_price", 0))
        xp = float(t.get("exit_price", t.get("price", 0)))
        if ep <= 0:
            continue
        seg = close[be:bx + 1]
        if is_long:
            mfe = (float(seg.max()) - ep) / ep * 100
            mae = (float(seg.min()) - ep) / ep * 100
            pnl = (xp - ep) / ep * 100
        else:
            mfe = (ep - float(seg.min())) / ep * 100
            mae = (ep - float(seg.max())) / ep * 100
            pnl = (ep - xp) / ep * 100
        post = close[bx:min(n, bx + 97)]
        if len(post) > 1:
            left = (float(post.max()) - xp) / xp * 100 if is_long else (xp - float(post.min())) / xp * 100
        else:
            left = 0.0
        rows.append({"pnl": pnl, "mfe": mfe, "mae": mae, "left": left, "er": str(t.get("entry_reason", ""))[:50], "xr": str(t.get("exit_reason", t.get("reason", "")))[:60]})
    wins = [x for x in rows if x["pnl"] > 0]
    early = [x for x in wins if x["left"] > max(1.0, 2 * x["pnl"])]
    late = [x for x in rows if x["mae"] < -1.0 and x["pnl"] > 0]
    bad = [x for x in rows if x["pnl"] <= 0 and x["mfe"] < 0.3]
    from collections import Counter
    return {"gain": r.get("gain_pct", 0.0), "trades": r.get("trades", 0), "valid": r.get("valid", False), "invalid_reason": r.get("invalid_reason", ""), "tim": r.get("tim_pct"), "dd": r.get("max_dd_pct"), "bh": r.get("bh_pct"), "n": len(rows), "wins": len(wins), "early": early, "late": late, "bad": bad, "exit_top": Counter(x["xr"][:40] for x in rows).most_common(6), "entry_top": Counter(x["er"][:40] for x in rows).most_common(6)}


_UNI_CACHE = {}
DIAG_BLACKLIST = {"SIMPLE_PRICE_GT0_ENABLED", "WT_SIMPLE_GUARANTEE_ENABLED"}


def load_universe(cat_side):
    if cat_side in _UNI_CACHE:
        return _UNI_CACHE[cat_side]
    # SQL-primary → JSON fallback keeps pusher alive if universe files vanish
    p = PUSH_DIR / f"universe_{cat_side}.json"
    u = None
    if os.environ.get("PER_SYM_STORE_SQLITE_DISABLED") != "1":
        try:
            import per_sym_store as _pss
            u = _pss.kv_get(f"gain_pusher/universe_{cat_side}")
        except Exception:
            pass
    if u is None:
        u = json.load(open(p))
    cands = []
    seen = set()
    def emit(name, sw, opt):
        if sw in DIAG_BLACKLIST:
            return
        if isinstance(opt, str) and (opt.startswith("=") or len(opt) > 24):
            return
        key = f"{sw}={json.dumps(opt)}"
        if key in seen:
            return
        seen.add(key)
        cands.append((name, {sw: opt}))
    for sw, opts in u.get("entry_switches", {}).items():
        for o in opts:
            emit(f"E:{sw}={o}", sw, json.loads(o))
    for y in u.get("entry_yellows", []):
        if "=" in y:
            f, o = y.split("=", 1)
            emit(f"Y:{y}", f.strip(), _coerce_opt(o.strip()))
    for sw, opts in u.get("exit_switches", {}).items():
        for o in opts:
            emit(f"X:{sw}={o}", sw, json.loads(o))
    _UNI_CACHE[cat_side] = (cands, u.get("defaults", {}))
    return _UNI_CACHE[cat_side]


def _coerce_opt(s):
    if s == "True":
        return True
    if s == "False":
        return False
    if s in ("", "None"):
        return None
    try:
        return int(s) if "." not in s else float(s)
    except ValueError:
        return s


def _trade_keys(ledger):
    out = {}
    for t in ledger or []:
        if t.get("type") != "CLOSE" or not t.get("qty", 1):
            continue
        try:
            be, bx = int(t.get("bar_entry", -1)), int(t.get("bar_exit", -1))
        except Exception:
            continue
        if be >= 0:
            out[(be, bx)] = float(t.get("pnl_pct", 0) or 0)
    return out


def screen_all(symside, prep, cur, cands, effective, base_gain, base_labels):
    scored = []
    for name, flip in cands:
        skip = True
        for k, v in flip.items():
            if effective.get(k, None) != v and cur.get(k, None) != v:
                skip = False
        if skip:
            continue
        trial = dict(cur)
        trial.update(flip)
        r = timed_eval(prep, trial, 30, include_ledger=True)
        if r.get("stalled"):
            continue
        if "v12 prepared" in str(r.get("invalid_reason", "")) or "pusher eval raised" in str(r.get("invalid_reason", "")):
            _CRASHERS.append(f"{name} :: {r.get('invalid_reason', '')}"[:160])
        d = r["gain_pct"] - base_gain
        ck = _trade_keys(r.get("ledger"))
        killed = [p for k, p in base_labels.items() if k not in ck]
        lk = sum(1 for p in killed if p <= 0)
        wk = sum(1 for p in killed if p > 0)
        wins = sum(1 for p in ck.values() if p > 0)
        wr = wins / max(1, len(ck))
        scored.append({"name": name, "flip": flip, "gain": r["gain_pct"], "delta": d, "trades": r["trades"], "valid": r["valid"], "winrate": round(wr, 3), "losers_killed": lk, "winners_killed": wk, "killed_pnl": round(sum(killed), 2)})
    return scored


def parse_candidate_value(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    try:
        return float(s) if "." in s else int(s)
    except ValueError:
        return s


def p2_topups(possym, cat_side, k=40, floor=3):
    """Top pos_sym SWITCH-kind (switch=value) trials for a cat_side: highest
    pos_sym first, avg_delta breaks ties. USER 2026-10-09: second round tries
    the highest pos_sym switches again (per-sym retest; fleet avg only ranks)."""
    out = []
    try:
        entries = (possym.get("cat_sides") or {}).get(cat_side) or {}
    except AttributeError:
        return out
    cands = []
    for name, m in entries.items():
        if (m or {}).get("kind") != "switch":
            continue
        ps = (m or {}).get("pos_sym", 0) or 0
        if ps < floor:
            continue
        try:
            sw, val = name.split("!", 1)[1].split("=", 1)
        except ValueError:
            continue
        cands.append((ps, (m or {}).get("avg_delta") or 0, sw, parse_candidate_value(val)))
    cands.sort(key=lambda t: (-t[0], -(t[1] or 0)))
    seen = set()
    for ps, ad, sw, val in cands:
        if (sw, json.dumps(val, sort_keys=True)) in seen:
            continue
        seen.add((sw, json.dumps(val, sort_keys=True)))
        out.append({"switch": sw, "value": val, "pos_sym": ps, "avg_delta": round(ad or 0, 3)})
        if len(out) >= k:
            break
    return out


def ablation_keys(cur, filter_keys):
    """Filter-ablation candidates: filter-kind keys currently IN the set."""
    fks = set(filter_keys or [])
    return [k for k in cur if k in fks]


def greedy_push(symside, prep, start_ov, registry, cat_side):
    uni_cands, uni_defaults = load_universe(cat_side)
    cur = dict(start_ov)
    base = timed_eval(prep, cur, 30, include_ledger=True)
    base_labels = _trade_keys(base.get("ledger"))
    log(f"{symside}: start gain={base['gain_pct']:.3f} tr={base['trades']} valid={base['valid']} wr={sum(1 for p in base_labels.values() if p > 0) / max(1, len(base_labels)):.2f}")
    moves, tested, shortlist, tabu, priors = [], 0, None, {}, {}
    converged = False
    for rnd in range(1, 11):
        effective = dict(uni_defaults)
        effective.update(cur)
        pool = uni_cands if rnd == 1 else shortlist
        scored = screen_all(symside, prep, cur, pool, effective, base["gain_pct"], base_labels)
        tested += len(scored)
        promotable = [s for s in scored if s["valid"] and s["delta"] > 1e-9]
        promotable.sort(key=lambda s: (s["delta"], s["winrate"], s["losers_killed"]), reverse=True)
        promotable = [s for s in promotable if rnd - tabu.get(next(iter(s["flip"])), -99) > 3]
        if rnd == 1:
            shortlist = [(s["name"], s["flip"]) for s in promotable[:40]]
            core = [(n, f) for n, f in uni_cands if any(k in n for k in ("P0:", "STOP_LOSS", "WT_LOWER_CROSS", "PARTIAL_PROFIT_LOCK", "MIN_HOLD_BARS", "RALLY"))]
            for c in core:
                if c not in shortlist:
                    shortlist.append(c)
            for sw, meta in (registry.get("P0_TEMPLATE_GAPS") or {}).items():
                for v in meta.get("test_values", []):
                    c = (f"P0:{sw}={v}", {sw: v})
                    if c not in shortlist:
                        shortlist.append(c)
            for sw, meta in (registry.get("P1_MUST_TEST") or {}).items():
                for v in meta.get("test_values", []):
                    c = (f"P1:{sw}={v}", {sw: v})
                    if c not in shortlist:
                        shortlist.append(c)
            for t in (registry.get("P2_POSSYM_TOP") or {}).get(cat_side, [])[:40]:
                c = (f"P2:{t['switch']}={t['value']}", {t["switch"]: t["value"]})
                if c not in shortlist:
                    shortlist.append(c)
        if not promotable:
            log(f"{symside} r{rnd}: converged (tested {tested} total)")
            converged = True
            break
        best = promotable[0]
        for k in best["flip"]:
            if k not in priors:
                priors[k] = cur.get(k, None) if k in cur else "__MISSING__"
            tabu[k] = rnd
        cur.update(best["flip"])
        base_labels = _base_labels_after(symside, prep, cur)
        moves.append({"round": rnd, "name": best["name"], "flip": best["flip"], "delta": round(best["delta"], 3), "gain": round(best["gain"], 3), "trades": best["trades"], "winrate": best["winrate"], "losers_killed": best["losers_killed"], "winners_killed": best["winners_killed"]})
        base = {"gain_pct": best["gain"]}
        log(f"{symside} r{rnd}: PROMOTE {best['name']} d={best['delta']:+.3f} -> {best['gain']:.3f} wr={best['winrate']:.2f} lk={best['losers_killed']}/wk={best['winners_killed']}")
    cur_gain = timed_eval(prep, cur, 30)["gain_pct"]
    for m in list(reversed(moves)):
        k = next(iter(m["flip"]))
        trial = dict(cur)
        if priors.get(k, "__MISSING__") == "__MISSING__":
            trial.pop(k, None)
        else:
            trial[k] = priors[k]
        r = timed_eval(prep, trial, 30)
        tested += 1
        if r["valid"] and r["gain_pct"] > cur_gain + 1e-9:
            d = r["gain_pct"] - cur_gain
            cur = trial
            cur_gain = r["gain_pct"]
            moves.append({"round": "elim", "name": f"DROP:{k}", "flip": {}, "delta": round(d, 3), "gain": round(cur_gain, 3), "trades": r["trades"], "winrate": 0, "losers_killed": 0, "winners_killed": 0})
            log(f"{symside} elim: DROP {k} d={d:+.3f} -> {cur_gain:.3f} (order artifact removed)")
    ablation = []
    for k in ablation_keys(cur, registry.get("P2_FILTER_KEYS") or [])[:60]:
        trial = dict(cur)
        trial.pop(k, None)
        r = timed_eval(prep, trial, 30)
        tested += 1
        d = r["gain_pct"] - cur_gain
        keep = bool(r["valid"] and d > 1e-9)
        if keep:
            cur = trial
            cur_gain = r["gain_pct"]
            moves.append({"round": "ablate", "name": f"ABL:{k}", "flip": {}, "delta": round(d, 3), "gain": round(cur_gain, 3), "trades": r["trades"], "winrate": 0, "losers_killed": 0, "winners_killed": 0})
            log(f"{symside} ablate: DROP {k} d={d:+.3f} -> {cur_gain:.3f} (filter not pulling weight)")
        ablation.append({"key": k, "delta": round(d, 3), "dropped": keep})
    fin = timed_eval(prep, cur, 30, include_ledger=True)
    fin2 = timed_eval(prep, cur, 30)
    assert abs(fin["gain_pct"] - fin2["gain_pct"]) < 1e-9, f"determinism break {fin['gain_pct']} vs {fin2['gain_pct']}"
    fw = _trade_keys(fin.get("ledger"))
    rep_diag = {"winrate": round(sum(1 for p in fw.values() if p > 0) / max(1, len(fw)), 3), "losers_left": sum(1 for p in fw.values() if p <= 0), "converged": converged, "ablation": ablation}
    return cur, fin, moves, tested, rep_diag


def _base_labels_after(symside, prep, cur):
    r = timed_eval(prep, cur, 30, include_ledger=True)
    return _trade_keys(r.get("ledger"))


def repair_zero(symside, prep, start_ov):
    cur = dict(start_ov)
    r = timed_eval(prep, cur, 30)
    if r["trades"] >= 10 and r["valid"]:
        return cur, r, []
    log(f"{symside}: repair needed (tr={r['trades']} valid={r['valid']})")
    gates = [k for k in cur if "KINDERGARTEN" in k]
    gates += [k for k in cur if k not in gates and any(s in k for s in ("FILTER", "GATE", "BLOCK", "VETO", "REQUIRE", "CONFIRM", "GUARD"))]
    dropped = []
    for k in gates[:40]:
        t = dict(cur)
        del t[k]
        x = timed_eval(prep, t, 30)
        if x["trades"] > r["trades"]:
            dropped.append(k)
            cur = t
            r = x
            log(f"{symside}: repair dropped {k} -> tr={x['trades']} gain={x['gain_pct']:.2f}")
            if x["trades"] >= 10:
                break
    if r["trades"] < 10:
        log(f"{symside}: repair fell back to empty baseline")
        cur = {}
        r = timed_eval(prep, cur, 30)
    return cur, r, dropped


def cat_side_of(symside):
    sym, side = symside.rsplit("_", 1)
    crypto = sym.endswith(("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USD1")) or sym in ("BTC", "ETH")
    return f"{'CRYPTO' if crypto else 'STOCKS'}_{side}"


def template_for(symside):
    return ROOT / "SPREADSHEETS" / f"TEMPLATE_{cat_side_of(symside)}.xlsx"


def rerun_sheet(symside, improved_json, runout, workers):
    out_iso = runout / f"pilot_{symside}"
    prog_iso = runout / f"progress_{symside}"
    out_iso.mkdir(parents=True, exist_ok=True)
    prog_iso.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["V15_PROGRESS_DIR"] = str(prog_iso)
    env["V15_OUT_DIR"] = str(out_iso)
    env["V15_FRESH_RUN"] = "1"
    env["V15_START_OVERRIDES"] = str(improved_json)
    env["V15_SKIP_LIVE_AT_DONE"] = "1"
    cmd = [sys.executable, str(ROOT / "v15_pilot.py"), "--sym-side", symside, "--template", str(template_for(symside)), "--seq-mode", "worst2best", "--window-days", "30", "--vector-only", "--workers", str(workers), "--allow-mac"]
    t0 = time.time()
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=3600)
    (runout / f"{symside}_pilot.log").write_text((p.stdout or "")[-20000:] + "\n--- STDERR ---\n" + (p.stderr or "")[-5000:])
    final = None
    for q in sorted(out_iso.glob(f"{symside}_bh*_30d_matrix.xlsx")):
        final = q
    prog = prog_iso / f"{symside}_v14_progress.json"
    secs = round(time.time() - t0, 1)
    if final and final.stat().st_size > 500000:
        shutil.copy2(final, runout / final.name)
        if prog.exists():
            shutil.copy2(prog, runout / f"{symside}_rerun_progress.json")
        chart = ROOT / "SPREADSHEETS" / f"{symside}_30D_REAL_ZOOMABLE_jump.html"
        if chart.exists() and time.time() - chart.stat().st_mtime < 3600:
            shutil.copy2(chart, runout / chart.name)
        log(f"{symside}: rerun OK {final.name} ({secs}s)")
        return {"rerun": final.name, "secs": secs}
    log(f"{symside}: rerun FAILED rc={p.returncode} ({secs}s) — see {symside}_pilot.log")
    return {"rerun": None, "secs": secs, "rc": p.returncode}


def audit_xlsx(path):
    import zipfile
    from openpyxl import load_workbook
    z = zipfile.ZipFile(str(path))
    entries, tz = len(z.namelist()), z.testzip()
    z.close()
    if entries < 10 or tz is not None:
        return {"zip_ok": False}
    wb = load_workbook(str(path), read_only=True, data_only=True)
    tabs, bad = 0, []
    for sn in wb.sheetnames:
        if "BASELINE" in sn or sn in ("LEGEND_FILTERS", "INSTRUCTIONS", "FILTERS_EXPLAINED", "INSTRUCTIONS_V2", "Results_Deltas", "FILTER_DICTIONARY_V2", "Results_30d_Deltas", "WIRING_INVENTORY", "FINAL_FILTER_RECHECK", "COMPLIANCE_REPAIR"):
            continue
        tabs += 1
        e3 = wb[sn].cell(row=3, column=5).value
        if not isinstance(e3, (int, float)) and e3 not in (None, ""):
            bad.append(sn)
    wb.close()
    return {"zip_ok": True, "entries": entries, "tabs": tabs, "bad_tabs": bad}


_ENGINE_MD5 = None
def engine_md5():
    global _ENGINE_MD5
    if _ENGINE_MD5 is None:
        try:
            import hashlib
            _ENGINE_MD5 = hashlib.md5(open(ROOT / "v12_quick_engine.py", "rb").read()).hexdigest()[:12]
        except Exception:
            _ENGINE_MD5 = "unknown"
    return _ENGINE_MD5


def push_one(symside, runout, registry, workers, do_rerun, streamer=None, anchor_overrides=None, extra=None):
    from tools.opt.v12_pilot import prepare_batch
    import socket
    sym = symside.rsplit("_", 1)[0]
    rep = {"symside": symside}
    if extra:
        rep.update(extra)
    rep.setdefault("host", socket.gethostname())
    rep["engine_md5"] = engine_md5()
    if anchor_overrides is None:
        pj = PROG_DIR / f"{symside}_v14_progress.json"
        if not pj.exists():
            return {**rep, "skip": "no progress json"}
    if streamer is not None:
        if not streamer.ensure(sym):
            return {**rep, "skip": "s1 pull failed"}
        rep["streamed"] = sym in streamer.fetched
    elif not valid_npz(sym):
        return {**rep, "skip": "npz missing/corrupt"}
    if anchor_overrides is None:
        d = json.load(open(pj))
        start_ov = d.get("cumulative_overrides") or {}
        rep["anchor_source"] = "progress_cumulative"
    else:
        start_ov = dict(anchor_overrides)
        rep["anchor_source"] = "anchor_json"
    try:
        _np = NPZ_DIR / f"{sym}.npz"
        rep["npz_id"] = {"mtime": int(_np.stat().st_mtime), "size": _np.stat().st_size}
    except Exception:
        rep["npz_id"] = {}
    prep = prepare_batch(symside, 30)
    if prep is None:
        return {**rep, "skip": "prepare failed"}
    anchor = timed_eval(prep, start_ov, 30)
    rep["anchor"] = {"gain": round(anchor["gain_pct"], 3), "trades": anchor["trades"], "valid": anchor["valid"]}
    cur, rcur, dropped = repair_zero(symside, prep, start_ov)
    rep["dropped"] = dropped
    diag = diagnose_ledger(symside, prep, cur)
    rep["diag"] = {"n": diag["n"], "wins": diag["wins"], "early": len(diag["early"]), "late": len(diag["late"]), "bad": len(diag["bad"]), "entry_top": diag["entry_top"][:3], "exit_top": diag["exit_top"][:3]}
    del _CRASHERS[:]
    cur, fin, moves, tested, rep_diag = greedy_push(symside, prep, cur, registry, cat_side_of(symside))
    rep["moves"] = moves
    rep["tested"] = tested
    if _CRASHERS:
        rep["crashed"] = _CRASHERS[:10]
        log(f"{symside}: {len(_CRASHERS)} poison candidates skipped (see report)")
    rep["winrate"] = rep_diag["winrate"]
    rep["losers_left"] = rep_diag["losers_left"]
    rep["converged"] = rep_diag["converged"]
    rep["ablation"] = rep_diag.get("ablation", [])
    try:
        prep365 = prepare_batch(symside, 365)
        r365 = timed_eval(prep365, cur, 365) if prep365 else None
        if r365:
            import numpy as _np
            _ts = _np.asarray(prep365["npz_prepared"].get("timestamps", []))
            span = float((_ts[-1] - _ts[0]) / 86400.0) if len(_ts) > 1 else 0.0
            rep["y365"] = {"gain": round(r365["gain_pct"], 2), "trades": r365["trades"], "valid": r365["valid"], "reason": r365.get("invalid_reason", ""), "span_days": round(span, 1)}
            log(f"{symside}: 365D gain={r365['gain_pct']:.2f} tr={r365['trades']} valid={r365['valid']} span={span:.0f}d")
    except Exception as e:
        rep["y365"] = {"error": str(e)[:120]}
    rep["final"] = {"gain": round(fin["gain_pct"], 3), "trades": fin["trades"], "valid": fin["valid"], "tim": fin.get("tim_pct"), "dd": fin.get("max_dd_pct")}
    improved = fin["gain_pct"] - anchor["gain_pct"] if anchor["trades"] else fin["gain_pct"]
    rep["improved_by"] = round(improved, 3)
    if moves or dropped:
        ij = runout / f"{symside}_improved.json"
        json.dump(cur, open(ij, "w"), indent=1)
        rep["improved_json"] = ij.name
        if do_rerun and fin["valid"]:
            rep.update(rerun_sheet(symside, ij, runout, workers))
            fx = runout / (rep.get("rerun") or "")
            if fx.exists():
                rep["audit"] = audit_xlsx(fx)
    return rep


def completed_symsides():
    out = []
    for p in sorted(PROG_DIR.glob("*_v14_progress.json")):
        ss = p.name[:-len("_v14_progress.json")]
        try:
            d = json.load(open(p))
            done = d.get("done") or {}
            n = len(done) if isinstance(done, dict) else 0
            if n > 0:
                out.append((ss, n))
        except Exception:
            continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--max-syms", type=int, default=0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--rerun-sheet", dest="rerun", action="store_true", default=True)
    ap.add_argument("--no-rerun-sheet", dest="rerun", action="store_false")
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fetch-s1", action="store_true", help="stream missing NPZs from S1 one by one (prefetch next, delete after use)")
    ap.add_argument("--s1-host", default="s1-int")
    ap.add_argument("--resume", default=None, help="run dir to continue (skips syms with *_report.json, appends)")
    ap.add_argument("--anchor-json", default=None, help="fleet worker mode: start overrides from this file instead of progress JSON (requires --sym)")
    ap.add_argument("--report-out", default=None, help="fleet worker mode: write ONLY this report file (no TRACK_LOG/SUMMARY append)")
    ap.add_argument("--no-lock", action="store_true", help="fleet worker mode: skip the single-instance flock (supervisor owns concurrency)")
    ap.add_argument("--round", type=int, default=0, help="fleet worker mode: round number stamped into the report")
    a = ap.parse_args()
    import fcntl
    _lockfh = None
    if not a.no_lock:
        _lockfh = open(PUSH_DIR / "gain_pusher.lock", "w")
        try:
            fcntl.flock(_lockfh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            print("[pusher] another instance holds the lock — exiting")
            return
    if a.anchor_json:
        if not a.sym:
            print("[pusher] --anchor-json requires --sym", flush=True)
            return
        anchor = json.load(open(a.anchor_json))
        registry = load_registry()
        runout = pathlib.Path(a.out) if a.out else PUSH_DIR / "runs" / "FLEET_R"
        runout.mkdir(parents=True, exist_ok=True)
        try:
            rep = push_one(a.sym, runout, registry, a.workers, a.rerun, None, anchor, {"round": a.round})
        except Exception as e:
            rep = {"symside": a.sym, "round": a.round, "error": f"{type(e).__name__}: {e}"[:200]}
            log(f"{a.sym}: ERROR {rep['error']}")
        dest = a.report_out or str(runout / f"{a.sym}_report.json")
        json.dump(rep, open(dest, "w"), indent=1, default=str)
        log(f"worker done: {a.sym} round={a.round} improved_by={rep.get('improved_by')} -> {dest}")
        return
    ts = time.strftime("%Y%m%d_%H%M%S")
    runout = pathlib.Path(a.resume) if a.resume else (pathlib.Path(a.out) if a.out else PUSH_DIR / "runs" / ts)
    runout.mkdir(parents=True, exist_ok=True)
    done_already = set()
    if a.resume:
        for rp in runout.glob("*_report.json"):
            done_already.add(rp.name[:-len("_report.json")])
        log(f"resume {runout}: {len(done_already)} already done")
    registry = load_registry()
    targets = [a.sym] if a.sym else [ss for ss, _ in completed_symsides()]
    if a.max_syms:
        targets = targets[:a.max_syms]
    if done_already:
        targets = [ss for ss in targets if ss not in done_already]
    if not targets:
        log("nothing to do (all resumed targets complete) — idle tick, no log write")
        return
    if a.dry_run:
        print(f"targets={len(targets)} out={runout}")
        print(targets[:20])
        return
    log(f"run {ts}: {len(targets)} sym_sides -> {runout}")
    if TRACK_LOG.exists():
        shutil.copy2(TRACK_LOG, ROOT / "backups" / f"before_gainpusher_run_{ts}.md")
    streamer = None
    if a.fetch_s1:
        targets = sorted(targets, key=lambda ss: ss.rsplit("_", 1)[0])
        streamer = S1Streamer(a.s1_host, [ss.rsplit("_", 1)[0] for ss in targets])
        log(f"s1-stream: ON via {a.s1_host}, {len(set(s.rsplit('_', 1)[0] for s in targets))} syms")
    results = []
    for rp in sorted(runout.glob("*_report.json")):
        try:
            results.append(json.load(open(rp)))
        except Exception:
            pass
    for i, ss in enumerate(targets):
        log(f"[{i + 1}/{len(targets)}] {ss}")
        if streamer is not None and i + 1 < len(targets):
            streamer.prefetch(targets[i + 1].rsplit("_", 1)[0])
        try:
            rep = push_one(ss, runout, registry, a.workers, a.rerun, streamer)
        except Exception as e:
            rep = {"symside": ss, "error": f"{type(e).__name__}: {e}"[:200]}
            log(f"{ss}: ERROR {rep['error']}")
        results.append(rep)
        json.dump(rep, open(runout / f"{ss}_report.json", "w"), indent=1, default=str)
        if streamer is not None:
            streamer.release(ss.rsplit("_", 1)[0])
    if streamer is not None:
        streamer.close()
    improved = [r for r in results if r.get("improved_by", 0) > 1e-9]
    with open(TRACK_LOG, "a") as f:
        f.write(f"\n## RUN {ts} — {len(results)} sym_sides, {len(improved)} improved\n")
        for r in sorted(results, key=lambda x: x.get("improved_by", 0), reverse=True)[:25]:
            if r.get("skip") or r.get("error"):
                f.write(f"- {r['symside']}: {r.get('skip') or r.get('error')}\n")
            else:
                mv = "; ".join(f"{m['name']} {m['delta']:+.2f}" for m in r.get("moves", [])) or "no moves"
                f.write(f"- {r['symside']}: {r['anchor']['gain']:+.2f}({r['anchor']['trades']}tr) -> {r['final']['gain']:+.2f}({r['final']['trades']}tr) [{mv}] rerun={r.get('rerun')}\n")
    json.dump(results, open(runout / "SUMMARY.json", "w"), indent=1, default=str)
    log(f"done: {len(improved)}/{len(results)} improved. SUMMARY in {runout}")


if __name__ == "__main__":
    main()
