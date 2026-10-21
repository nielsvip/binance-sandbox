#!/usr/bin/env python3
"""NPZ inventory watch — USER 2026-10-21: the fleet must never discover a missing NPZ late.

Watches the live trading universe (symbols_trb_long/short.json +
tradeable_keys.json) against the local NPZ inventory
(backtest_v8/indicators/<SYM>*.npz). Symbols with no NPZ — or an NPZ older
than --max-age-s — get rebuilt via backtest_v8_precompute.py, oldest first,
bounded by --max-builds per run. Freshness upkeep stays the refresh loop's
job; this tool closes the *coverage* gap the moment a new name enters the
books (the 2026-10-21 case: 20 tradeable names with no NPZ anywhere).

Stocks cannot build outside market hours (no klines) — failed builds are
logged and skipped, never fatal. Dry-run by default; --apply builds.
Runs on either host (ROOT = cwd); S1 cron owns the source inventory.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("NPZ_WATCH_ROOT") or Path.cwd())
QUOTE_CRYPTO = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
DEFAULT_MAX_AGE_S = 6 * 3600
DEFAULT_MAX_BUILDS = 4
DEFAULT_BUILD_TIMEOUT_S = 900


def jload(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def universe(root=ROOT):
    syms = set()
    for name in ("symbols_trb_long.json", "symbols_trb_short.json"):
        for s in jload(root / name, []) or []:
            syms.add(str(s).strip().upper())
    for k in jload(root / "tradeable_keys.json", []) or []:
        if ":" in str(k):
            syms.add(str(k).split(":")[1].rsplit("_", 1)[0].strip().upper())
    return sorted(syms)


def inventory(root=ROOT):
    inv = {}
    for f in glob.glob(str(root / "backtest_v8" / "indicators" / "*.npz")):
        sym = os.path.basename(f).split("_")[0].split(".")[0].upper()
        try:
            mt = os.path.getmtime(f)
        except OSError:
            continue
        if sym not in inv or mt > inv[sym]:
            inv[sym] = mt
    return inv


def mode_of(sym):
    return "crypto" if sym.endswith(QUOTE_CRYPTO) else "tradier"


def classify(root=ROOT, max_age_s=DEFAULT_MAX_AGE_S, now=None):
    now = float(now if now is not None else time.time())
    inv = inventory(root=root)
    missing, stale, fresh = [], [], []
    for sym in universe(root=root):
        mt = inv.get(sym)
        if mt is None:
            missing.append(sym)
        elif now - mt > max_age_s:
            stale.append((sym, now - mt))
        else:
            fresh.append(sym)
    stale.sort(key=lambda kv: -kv[1])
    return missing, [s for s, _ in stale], fresh


def build(sym, root=ROOT, timeout_s=DEFAULT_BUILD_TIMEOUT_S, builder=None):
    cmd = (
        list(builder)
        if builder
        else [
            sys.executable,
            "backtest_v8_precompute.py",
            "--symbol",
            sym,
            "--mode",
            mode_of(sym),
        ]
    )
    try:
        proc = subprocess.run(
            cmd, cwd=str(root), capture_output=True, text=True, timeout=timeout_s
        )
        tail = (proc.stderr or proc.stdout or "")[-600:]
        return proc.returncode == 0, tail
    except Exception as e:
        return False, str(e)[:300]


def watch(
    max_age_s=DEFAULT_MAX_AGE_S,
    max_builds=DEFAULT_MAX_BUILDS,
    apply=False,
    root=ROOT,
    builder=None,
):
    missing, stale, fresh = classify(root=root, max_age_s=max_age_s)
    targets = missing + stale
    res = {
        "universe": len(missing) + len(stale) + len(fresh),
        "missing": missing,
        "stale": stale,
        "fresh_n": len(fresh),
        "built": [],
        "failed": [],
        "skipped_cap": [],
        "applied": bool(apply),
    }
    if not apply:
        res["would_build"] = targets[:max_builds]
        res["would_build_truncated"] = len(targets) > max_builds
        return res
    for sym in targets[:max_builds]:
        ok, tail = build(sym, root=root, builder=builder)
        (res["built"] if ok else res["failed"]).append({"sym": sym, "tail": tail})
    res["skipped_cap"] = targets[max_builds:]
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Rebuild missing/stale NPZs for the live trading universe."
    )
    ap.add_argument("--max-age-s", type=float, default=DEFAULT_MAX_AGE_S)
    ap.add_argument("--max-builds", type=int, default=DEFAULT_MAX_BUILDS)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    res = watch(max_age_s=args.max_age_s, max_builds=args.max_builds, apply=args.apply)
    tag = "APPLY" if args.apply else "DRY-RUN"
    print(
        f"[npz-watch:{tag}] universe={res['universe']} missing={len(res['missing'])} stale={len(res['stale'])} fresh={res['fresh_n']} built={len(res['built'])} failed={len(res['failed'])} capped={len(res['skipped_cap'])}"
    )
    for f in res["failed"][:10]:
        print(f"[npz-watch] BUILD-FAILED {f['sym']}: {f['tail'][:160]}")
    outdir = ROOT / "data" / "npz_inventory_watch"
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / f"{time.strftime('%Y%m%d')}.jsonl").open("a").write(
            json.dumps(
                {
                    "ts": time.time(),
                    **{k: v for k, v in res.items() if k != "failed"},
                    "failed": [f["sym"] for f in res["failed"]],
                }
            )
            + "\n"
        )
    except Exception:
        pass
    return 1 if res["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
