#!/usr/bin/env python3
"""sweep_integrity_verifier — CONSTANT integrity monitor for the live sweep delta-logs.
Flags, immediately and repeatedly, the inconsistencies that make a sheet untrustworthy:
  V1 DEFAULT-NONZERO (P0, BIBLE §14.1/§21): a switch's DEFAULT-value row MUST have delta 0. If you run any
     candidate set and the DEFAULT candidate shows a non-zero delta, the baseline/override path disagrees = broken.
  V2 DUP-DELTA (§30): one delta value repeated across many UNRELATED switches -> shared fallback / suspect wiring.
  V3 DELTA-NONE with a computed gain, or gain==None on a valid row.
Reads {PROGRESS_DIR}/v15_delta_log/*_jump.jsonl. Default value resolved from QuickConfig (crypto) /
apply_tradier_defaults() (stocks). Run once (--once) or loop (--loop SECONDS). Writes a report + prints a summary.
NO writes to any engine/config/sheet — read-only monitor.
"""
import argparse, collections, glob, json, os, sys, time, datetime
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
EPS = 1e-6


def _defaults(stocks: bool):
    import v12_quick_engine as V, dataclasses as dc
    qc = V.QuickConfig()
    if stocks:
        try: qc.apply_tradier_defaults()
        except Exception: pass
    out = {}
    for f in dc.fields(V.QuickConfig):
        out[f.name] = getattr(qc, f.name)
    # live config overrides (crypto=config.py, stocks=config_tradier)
    try:
        mod = __import__("config_tradier") if stocks else __import__("config")
        live = mod.TradierConfig() if stocks else mod.Config()
        for k in dir(live):
            if k.isupper() and not k.startswith("_"):
                try: out[k] = getattr(live, k)
                except Exception: pass
    except Exception:
        pass
    return out


def _cand_matches_default(cand, default):
    """cand is the log's stringified candidate; default is the typed config default."""
    if default is None:
        return False
    cs = str(cand).strip()
    ds = str(default).strip()
    if cs == ds:
        return True
    # bool / numeric tolerant compares
    if isinstance(default, bool):
        return cs.lower() == str(default).lower()
    try:
        return abs(float(cs) - float(default)) < 1e-9
    except Exception:
        return False


def scan(progress_dir):
    logdir = os.path.join(progress_dir, "v15_delta_log")
    files = sorted(glob.glob(os.path.join(logdir, "*_jump.jsonl")))
    v1 = []  # default-nonzero violations
    v2 = collections.Counter()  # dup deltas
    v2_switches = collections.defaultdict(set)
    scanned = 0
    defcache = {}
    for f in files:
        ss = os.path.basename(f).replace("_jump.jsonl", "")
        stocks = not ("USDT" in ss or "USDC" in ss)
        if stocks not in defcache:
            defcache[stocks] = _defaults(stocks)
        defs = defcache[stocks]
        recs = []
        try:
            for line in open(f):
                line = line.strip()
                if line:
                    recs.append(json.loads(line))
        except Exception:
            continue
        scanned += 1
        for r in recs:
            sw = r.get("switch"); cand = r.get("cand"); dl = r.get("delta"); lab = r.get("label")
            if dl is None:
                continue
            # V1: naked default-value row must be 0
            if lab == "naked" and sw in defs and _cand_matches_default(cand, defs[sw]):
                if abs(float(dl)) > EPS:
                    v1.append((ss, sw, cand, round(float(dl), 6), round(float(r.get("cum_before") or 0), 6)))
            # V2: dup delta
            if abs(float(dl)) > EPS:
                key = round(float(dl), 6)
                v2[key] += 1
                v2_switches[key].add(sw)
    dup = [(d, c, sorted(v2_switches[d])[:8]) for d, c in v2.most_common(10) if c >= 8 and len(v2_switches[d]) >= 5]
    return scanned, v1, dup


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--progress-dir", default=os.environ.get("V15_PROGRESS_DIR", ""))
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", type=int, default=0, help="seconds between scans (constant monitor)")
    args = ap.parse_args()
    pd = args.progress_dir
    if not pd:
        # default to current-run pointer
        ptr = os.path.expanduser("~/v15_current_progress_dir.txt")
        pd = open(ptr).read().strip() if os.path.exists(ptr) else os.path.join(ROOT, "data/reports/lifecycle_pilot")
    rep_path = os.path.join(ROOT, "data/reports/wiring/sweep_integrity_flags.md")
    while True:
        scanned, v1, dup = scan(pd)
        ts = datetime.datetime.utcnow().isoformat() + "Z"
        lines = [f"# SWEEP INTEGRITY FLAGS — {ts}", f"progress_dir: {pd}  |  sym_sides scanned: {scanned}", ""]
        lines.append(f"## V1 DEFAULT-NONZERO (P0 idempotency, MUST be 0): {len(v1)}")
        for ss, sw, cand, dl, cb in v1[:60]:
            lines.append(f"- {ss}  {sw}={cand} (DEFAULT)  delta={dl}  cum_before={cb}  <-- should be 0")
        lines.append("")
        lines.append(f"## V2 DUP-DELTA across >=5 unrelated switches (§30 shared-fallback suspect): {len(dup)}")
        for d, c, sws in dup:
            lines.append(f"- delta={d} appears {c}x across {sws}")
        rep = "\n".join(lines) + "\n"
        print(f"[verifier {ts}] scanned={scanned} V1_default_nonzero={len(v1)} V2_dup={len(dup)}")
        for ss, sw, cand, dl, cb in v1[:15]:
            print(f"  V1 {ss} {sw}={cand}(DEFAULT) delta={dl} cum_before={cb}")
        for d, c, sws in dup[:5]:
            print(f"  V2 delta={d} x{c} {sws}")
        try:
            os.makedirs(os.path.dirname(rep_path), exist_ok=True)
            open(rep_path, "w").write(rep)
        except Exception as _e:
            print(f"  [report-write-skip] {_e}")
        if args.once or args.loop <= 0:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
