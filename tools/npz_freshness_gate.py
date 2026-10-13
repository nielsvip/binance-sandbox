"""NPZ freshness gate (USER 2026-10-12 standing rule).

Every NPZ must hold >=365 days of data AND end at the latest 15m bar
(crypto) / latest RTH session (stocks) BEFORE any test rerun on it or
any sync to s2/s5/s6. Exit 0 = all pass. Exit 1 = violations listed.

Usage (S1, the NPZ source):
  python3 tools/npz_freshness_gate.py [--dir backtest_v8/indicators] [--fix-cmd]
  python3 tools/npz_freshness_gate.py --refresh [--dry-run] [--workers 6]
  python3 tools/npz_freshness_gate.py --purge-variants [--yes]   # USER 2026-10-12:
    delete variant-suffixed NPZs (S3/_LONG/_SHORT) matching no klines (CPU/money
    waste). Lists only unless --yes; with --yes moves them to .purged_variants/<ts>/
    (out of the *.npz scan; rm -rf the dir to reclaim disk).

Refresh mapping: STALE (has history, old tail) -> tools/npz_bar_refresh.py
--symbols (fast tail append); SHORT (<365d) -> backtest_v8_precompute.py
--symbols --mode (full rebuild). Variant-suffixed files (_S3*, _LONG,
_SHORT combos) match no klines and are reported UNREFRESHABLE.
"""
import numpy as np, os, re, glob, sys, time, subprocess

MIN_DAYS = 365
MAX_LAG_BARS_CRYPTO = 2
STOCK_MAX_LAG_DAYS = 4
VARIANT_RE = re.compile(r"S3|_LONG$|_SHORT$", re.IGNORECASE)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_crypto(stem):
    s = stem.upper()
    return "USDT" in s or "USDC" in s or "BUSD" in s


def is_variant(stem):
    return bool(VARIANT_RE.search(stem))


DERIVED_SUFFIXES = ("_365", "_BF2", "_BF", "_CYCLE", "_W2B", "_S3")  # unread + unrefreshable derived files (no klines match)


def is_derived(stem):
    return is_variant(stem) or stem.endswith(DERIVED_SUFFIXES)


def tradeable_syms(root=None):
    """Set of base syms from tradeable_keys.json (ang:SYM_SIDE format); missing file -> None (fail-closed: gate everything)."""
    try:
        import json as _js

        tk = _js.load(open(os.path.join(root or ROOT, "tradeable_keys.json")))
        out = set()
        for k in tk:
            s = k.split(":", 1)[-1]
            s = re.sub(r"_(LONG|SHORT)$", "", s)
            out.add(s)
        return out
    except Exception:
        return None


def is_tradeable_file(path_or_stem, tsyms):
    """Suffix-tolerant on venue (NPZ AG.npz matches key AGUSDT; crypto direct). Derived-suffixed stems never match (purged, never refreshed). tsyms None -> True (fail-closed: gate everything)."""
    if tsyms is None:
        return True
    stem = os.path.basename(str(path_or_stem))
    stem = stem[:-4] if stem.endswith(".npz") else stem
    return stem in tsyms or stem + "USDT" in tsyms or stem + "USDC" in tsyms


def classify(path, now=None):
    """Return (verdict, detail): verdict in ok/short/stale/bad(+variant flag)."""
    now = now or time.time()
    latest_15m = (now // 900) * 900
    name = os.path.basename(path)
    stem = name[:-4] if name.endswith(".npz") else name
    try:
        z = np.load(path, allow_pickle=False)
        keys = [k for k in z.files if "time" in k.lower() or k in ("ts", "t")]
        ts = np.asarray(z[keys[0]], dtype=np.float64).ravel() if keys else np.array([])
        ts = ts[np.isfinite(ts)]
        if ts.size == 0:
            return ("bad", "no-timestamps")
        if ts[-1] > 1e12:
            ts = ts / 1000.0
        span_days = (ts[-1] - ts[0]) / 86400.0
        if span_days < MIN_DAYS:
            return ("short", f"{span_days:.0f}d")
        if is_crypto(stem):
            lag = (latest_15m - ts[-1]) / 900.0
            if lag > MAX_LAG_BARS_CRYPTO:
                return ("stale", f"lag{lag:.0f}bars")
        else:
            lagd = (now - ts[-1]) / 86400.0
            if lagd > STOCK_MAX_LAG_DAYS:
                return ("stale", f"stock-lag{lagd:.1f}d")
        return ("ok", "")
    except Exception as e:
        return ("bad", f"ERR {e}")


def audit(d):
    short, stale, bad, ok = [], [], [], 0
    files = sorted(glob.glob(os.path.join(d, "*.npz")))
    for f in files:
        v, det = classify(f)
        name = os.path.basename(f)
        if v == "ok":
            ok += 1
        elif v == "short":
            short.append(f"{name} {det}")
        elif v == "stale":
            stale.append(f"{name} {det}")
        else:
            bad.append(f"{name} {det}")
    return {"files": files, "ok": ok, "short": short, "stale": stale, "bad": bad}


def plan(short, stale):
    """Split violators into bar-refresh (stale), rebuild (short), unrefreshable (variants/derived: match no klines)."""
    refresh, rebuild, unref = [], [], []
    for entry in stale:
        stem = entry.split()[0].replace(".npz", "")
        (unref if is_derived(stem) else refresh).append(stem)
    for entry in short:
        stem = entry.split()[0].replace(".npz", "")
        (unref if is_derived(stem) else rebuild).append(stem)
    return sorted(set(refresh)), sorted(set(rebuild)), sorted(set(unref))


def mode_of(stem):
    return "crypto" if is_crypto(stem) else "tradier"


def variant_files(d):
    out = []
    for f in sorted(glob.glob(os.path.join(d, "*.npz"))):
        stem = os.path.basename(f)[:-4]
        if is_variant(stem):
            out.append(f)
    return out


def purge_variants(d, do_it=False):
    import datetime as _dt
    targets = variant_files(d)
    for f in targets:
        print(f"  PURGE_TARGET {os.path.basename(f)}")
    if not do_it:
        print(f"PURGE dry-run: {len(targets)} file(s) listed, nothing moved (add --yes)")
        return True
    if not targets:
        print("PURGE nothing to move")
        return True
    trash = os.path.join(d, ".purged_variants", _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d%H%M%S"))
    os.makedirs(trash, exist_ok=True)
    moved = 0
    for f in targets:
        try:
            os.rename(f, os.path.join(trash, os.path.basename(f)))
            moved += 1
        except Exception as e:
            print(f"  PURGE_FAIL {os.path.basename(f)}: {e}")
            return False
    print(f"PURGED {moved} file(s) -> {trash}")
    return True


def run_refresh(refresh, rebuild, workers=6, dry_run=False):
    cmds = []
    if refresh:
        cmds.append([sys.executable, os.path.join(ROOT, "tools", "npz_bar_refresh.py"),
                     "--symbols", ",".join(refresh), "--workers", str(workers)])
    rc, tc = [s for s in rebuild if is_crypto(s)], [s for s in rebuild if not is_crypto(s)]
    if rc:
        cmds.append([sys.executable, os.path.join(ROOT, "backtest_v8_precompute.py"),
                     "--symbols", ",".join(rc), "--mode", "crypto", "--workers", str(workers)])
    if tc:
        cmds.append([sys.executable, os.path.join(ROOT, "backtest_v8_precompute.py"),
                     "--symbols", ",".join(tc), "--mode", "tradier", "--workers", str(workers)])
    for c in cmds:
        print("  " + " ".join(c))
        if not dry_run:
            r = subprocess.run(c, cwd=ROOT)
            if r.returncode != 0:
                return False
    return True


def main(argv=None):
    argv = argv if argv is not None else sys.argv
    d = argv[argv.index("--dir") + 1] if "--dir" in argv else os.path.join(ROOT, "backtest_v8", "indicators")
    if not os.path.isdir(d):
        print(f"GATE_FAIL dir missing: {d}")
        return 1
    res = audit(d)
    short, stale, bad = res["short"], res["stale"], res["bad"]
    print(f"GATE files={len(res['files'])} ok={res['ok']} short={len(short)} stale={len(stale)} bad={len(bad)}")
    for lst, tag in ((short, "SHORT"), (stale, "STALE"), (bad, "BAD")):
        for s in lst[:60]:
            print(f"  {tag} {s}")
    if "--fix-cmd" in argv and (short or stale):
        refresh, rebuild, unref = plan(short, stale)
        print("--- S1 refresh commands ---")
        if refresh:
            print(f"  python3 tools/npz_bar_refresh.py --symbols {','.join(refresh)}")
        rc = [s for s in rebuild if is_crypto(s)]
        tc = [s for s in rebuild if not is_crypto(s)]
        if rc:
            print(f"  python3 backtest_v8_precompute.py --symbols {','.join(rc)} --mode crypto")
        if tc:
            print(f"  python3 backtest_v8_precompute.py --symbols {','.join(tc)} --mode tradier")
        for s in unref:
            print(f"  UNREFRESHABLE {s} (variant suffix matches no klines)")
    if "--purge-variants" in argv:
        ok_p = purge_variants(d, do_it="--yes" in argv)
        return 0 if ok_p else 1
    if "--refresh" in argv and (short or stale):
        refresh, rebuild, unref = plan(short, stale)
        for s in unref:
            print(f"  UNREFRESHABLE {s} (variant suffix matches no klines)")
        workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else 6
        dry = "--dry-run" in argv
        if not (refresh or rebuild):
            print("REFRESH nothing runnable (all violators are variants)")
        elif not run_refresh(refresh, rebuild, workers, dry):
            print("REFRESH FAILED mid-run")
            return 1
        if not dry:
            res2 = audit(d)
            left = len(res2["short"]) + len(res2["stale"]) + len(res2["bad"])
            print(f"RE-AUDIT files={len(res2['files'])} ok={res2['ok']} residual={left}")
            return 0 if left == 0 else 1
    return 0 if not (short or stale or bad) else 1


if __name__ == "__main__":
    sys.exit(main())
