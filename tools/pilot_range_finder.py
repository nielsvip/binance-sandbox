#!/usr/bin/env python3
"""Per-switch RANGE-FINDER for ONE pilot key (e.g. BTCUSDC_SHORT / MU_LONG). For every
sweepable switch in the mode manifest, sweep its candidate values on the single pilot
symbol via the FOTEST Tier-2 engine, record pool_sharpe/pnl/trades/gain_vs_bh per value,
derive the USEFUL value band toward ~20x b&h, and — crucially — for any switch whose
values produce NO change, INVESTIGATE and record WHY (not a config attr / config attr but
engine never reads it / genuinely inert this sym+window). Single-sym => DIAGNOSTIC only.
Durable (result.json per cell), memory-gated, parallel, un-stallable."""
import os, sys, json, re, subprocess, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np

SBX = Path("/home/niels/binance-sandbox")
PY = "/home/niels/.conda/envs/binance_env/bin/python"
ENGINE = str(SBX / "backtest_v8_engine_FOTEST.py")
NPZ = str(SBX / "backtest_v8" / "indicators")


def free_mb():
    try:
        for ln in subprocess.run(["free", "-m"], capture_output=True, text=True).stdout.splitlines():
            if ln.startswith("Mem:"):
                return int(ln.split()[6])
    except Exception:
        pass
    return 0


def bh_pct(symbol, start):
    """buy&hold % over [start, end] from NPZ close+timestamps."""
    try:
        z = np.load(f"{NPZ}/{symbol}.npz", mmap_mode="r", allow_pickle=True)
        close = z["close"]
        ts = z["timestamps"] if "timestamps" in z.files else (z["ts"] if "ts" in z.files else None)
        import datetime as _dt
        s_ep = _dt.datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc).timestamp()
        i0 = 0
        if ts is not None:
            arr = np.asarray(ts, dtype=float)
            arr = arr / 1000.0 if arr.max() > 1e12 else arr
            w = np.where(arr >= s_ep)[0]
            i0 = int(w[0]) if len(w) else 0
        c0 = float(close[i0]); c1 = float(close[-1])
        return (c1 / c0 - 1.0) * 100.0 if c0 else 0.0
    except Exception as e:
        return None


def run_cell(symbol, mode, account, start, override, cell_dir, timeout, min_avail):
    marker = cell_dir / "result.json"
    if marker.exists():
        try:
            return json.loads(marker.read_text())
        except Exception:
            pass
    cell_dir.mkdir(parents=True, exist_ok=True)
    ovr = cell_dir / "ovr.json"; ovr.write_text(json.dumps(override))
    while free_mb() < min_avail:
        time.sleep(10)
    env = dict(os.environ)
    env.update({"V8_OVERRIDE_FILE": str(ovr), "V8_RATE_GUARD_DISABLED": "1",
                "V8_SWEEP_MODE": "1", "V8_BACKTEST_DISK_CACHE": "1"})
    cmd = ["timeout", str(timeout), "nice", "-n", "18", PY, ENGINE, "--mode", mode,
           "--account", account, "--start", start, "--symbols", symbol, "--npz-dir", NPZ]
    try:
        r = subprocess.run(cmd, cwd=str(SBX), env=env, capture_output=True, text=True, timeout=timeout + 60)
        out = (r.stdout or "") + (r.stderr or "")
    except Exception:
        return {"sharpe": None, "trades": 0, "pnl": None, "err": "timeout"}
    m = re.search(r"V8_RESULT:.*?pool_sharpe=([+-]?[0-9.]+).*?pnl=([+-]?[0-9.]+).*?trades=([0-9]+)", out, re.S)
    if not m:
        res = {"sharpe": None, "trades": 0, "pnl": None,
               "err": "early_abort" if "EARLY_ABORT" in out else "no_result"}
    else:
        res = {"sharpe": float(m.group(1)), "pnl": float(m.group(2)), "trades": int(m.group(3))}
    marker.write_text(json.dumps(res))
    return res


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True)
    ap.add_argument("--account", required=True)
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--side", required=True)
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--max-par", type=int, default=4)
    ap.add_argument("--min-avail", type=int, default=4000)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    key = f"{a.symbol}_{a.side}"
    root = SBX / "data" / "pilot_progress" / key
    root.mkdir(parents=True, exist_ok=True)
    man = json.loads(Path(a.manifest).read_text())["params"]
    params = [(n, v) for n, v in man.items() if v.get("sweepable") and v.get("test_values")]
    if a.limit:
        params = params[: a.limit]
    # config attr membership (is the switch even applied by the engine's from-module override?)
    sys.path.insert(0, str(SBX))
    try:
        cfg_mod = __import__("config" if a.mode == "crypto" else "config_tradier")
        cfg_attrs = set(dir(cfg_mod))
        if hasattr(cfg_mod, "Config"):
            cfg_attrs.update(dir(cfg_mod.Config))
        if hasattr(cfg_mod, "TradierConfig"):
            cfg_attrs.update(dir(cfg_mod.TradierConfig))
    except Exception:
        cfg_attrs = set()
    bh = bh_pct(a.symbol, a.start)
    bh_key = (-bh if a.side.upper() == "SHORT" else bh) if bh is not None else None
    print(f"[pilot] key={key} params={len(params)} long_bh={bh} side_bh={bh_key} start={a.start}", flush=True)
    base = run_cell(a.symbol, a.mode, a.account, a.start, {}, root / "__BASELINE__", a.timeout, a.min_avail)
    print(f"[pilot] baseline={base}", flush=True)
    base_s = base.get("sharpe"); base_p = base.get("pnl")

    def gvbh(pnl):
        # Never divide SHORT results by abs(LONG B&H).  A bull sample has a
        # non-positive short-and-hold benchmark; cash is then the floor and a
        # B&H multiple is undefined.
        if pnl is None or bh_key is None or bh_key <= 0:
            return None
        return round(pnl / bh_key, 3)

    def do(nv):
        n, meta = nv
        vals = [str(x) for x in meta["test_values"]]
        rows = []
        for v in vals:
            # coerce
            if v in ("True", "False"):
                cv = (v == "True")
            else:
                try:
                    cv = int(v) if ("." not in v and "e" not in v.lower()) else float(v)
                except ValueError:
                    try:
                        cv = float(v)
                    except ValueError:
                        cv = v
            r = run_cell(a.symbol, a.mode, a.account, a.start,
                         {n: cv}, root / f"{n}__{v}".replace("/", "_"), a.timeout, a.min_avail)
            rows.append({"value": v, "sharpe": r.get("sharpe"), "pnl": r.get("pnl"),
                         "trades": r.get("trades"), "gain_vs_bh": gvbh(r.get("pnl")), "err": r.get("err")})
        # zero-delta investigation
        sset = {row["sharpe"] for row in rows}
        pset = {row["pnl"] for row in rows}
        moved = not (len(sset) <= 1 and len(pset) <= 1)
        zreason = None
        if not moved:
            if n not in cfg_attrs:
                zreason = "NOT_A_CONFIG_ATTR: override never applied (add to config or it is dead)"
            elif all(row["trades"] == 0 for row in rows):
                zreason = "ZERO_TRADES_ALL: pilot key never trades under any value (check entry paths / window)"
            else:
                zreason = "CONFIG_ATTR_BUT_INERT: engine does not read it in this path OR genuinely no effect on this sym+window (needs code-path check)"
        # useful range = contiguous values with sharpe >= max(sharpe)-eps and positive gain_vs_bh
        scored = [(row, row["sharpe"]) for row in rows if row["sharpe"] is not None]
        best = max(scored, key=lambda t: t[1], default=(None, None))
        useful = [row["value"] for row in rows if row["sharpe"] is not None and best[1] is not None and row["sharpe"] >= best[1] - 0.05]
        return n, {"values": rows, "moved": moved, "zero_delta_reason": zreason,
                   "best_value": best[0]["value"] if best[0] else None,
                   "best_sharpe": best[1], "best_gain_vs_bh": gvbh(best[0]["pnl"]) if best[0] else None,
                   "useful_range": useful, "n_tested": len(rows), "live_refs": meta.get("live_refs", "")}
    out = {"_meta": {"pilot_key": key, "mode": a.mode, "start": a.start,
                     "long_bh_pct": bh, "bh_pct": bh_key, "cash_benchmark_pct": 0.0,
                     "baseline_sharpe": base_s, "baseline_pnl": base_p, "n_params": len(params),
                     "DIAGNOSTIC": "single-symbol range-finder — NOT for promotion"}, "switches": {}}
    done = 0
    with ThreadPoolExecutor(max_workers=a.max_par) as ex:
        futs = {ex.submit(do, nv): nv for nv in params}
        for f in as_completed(futs):
            try:
                n, rec = f.result()
            except Exception as e:
                n, rec = futs[f][0], {"error": str(e)}
            out["switches"][n] = rec
            done += 1
            if done % 10 == 0:
                Path(a.out).write_text(json.dumps(out, indent=1))
                print(f"[pilot] {done}/{len(params)} switches done", flush=True)
    Path(a.out).write_text(json.dumps(out, indent=1))
    moved = sum(1 for r in out["switches"].values() if r.get("moved"))
    print(f"[pilot] DONE key={key} switches={len(out['switches'])} moved={moved} zero={len(out['switches'])-moved} → {a.out}", flush=True)


if __name__ == "__main__":
    main()
