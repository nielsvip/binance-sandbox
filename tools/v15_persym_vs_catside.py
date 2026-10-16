#!/usr/bin/env python3
"""v15_persym_vs_catside — does a sym_side's stored per_sym set still beat its cat_side defaults?

USER 2026-10-15: "if a symbol was not tradeable for a long time (like 2 weeks) its per_sym settings might make it
trade worse than using the cat_side defaults is there a way to test this?"

Yes: run both configs through the SAME engine over the SAME recent bars and compare. Per sym_side,
  A = the stored per_sym full set   (per_sym_active, else the newest per_sym_history row)
  B = its cat_side defaults         (data/per_sym_settings.json -> CRYPTO_LONG / CRYPTO_SHORT / STOCKS_LONG / STOCKS_SHORT)
are both evaluated with tools.dc_simple_8_sweep.eval_gain (QuickConfig + typed coercion + V.simulate_one), which is
the same vector path the pilots use. Several windows are measured, because that is the whole point: a set that was
best over 30 days can have decayed over the last 7, and only the recent window shows it.

NO-LIES: every number comes from simulate_one on frozen NPZ, nothing is annualized or extrapolated, and any sym_side
with fewer than MIN_TRADES in a window is printed [LOW_SAMPLE] and never counted as a win for either side.

MUST RUN ON S1/S4/S5 — the Mac has only 134 of 473 NPZ stores and returns "0 trades DATA_ERROR" for the rest
(CLAUDE.md). Read-only: it never writes a per_sym set, it only reports.

Usage:
  python3 tools/v15_persym_vs_catside.py --tradeable --windows 30,7
  python3 tools/v15_persym_vs_catside.py --sym-sides VETUSDT_LONG,ENSUSDT_SHORT --windows 30,14,7
  python3 tools/v15_persym_vs_catside.py --min-age-days 7 --limit 40 --out data/reports/persym_vs_catside.json
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MIN_TRADES = 30  # CLAUDE.md sample floor per sym_side; below this a window is diagnostic only
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD")


def is_crypto(symbol):
    return symbol.upper().endswith(CRYPTO_SUFFIX)


def cat_side_of(sym_side):
    sym, side = sym_side.rsplit("_", 1)
    return ("CRYPTO_" if is_crypto(sym) else "STOCKS_") + side.upper(), sym, side.upper() == "LONG"


def age_days(updated_at, now):
    try:
        return (now - datetime.fromisoformat(str(updated_at).replace("Z", "+00:00")).timestamp()) / 86400.0
    except Exception:
        return None


def load_sets(db, keys_wanted, now):
    """{sym_side: (full_config, source, age_days)} — applied set first, else newest history row."""
    from per_sym_store import _maybe_decompress
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=120.0)
    con.row_factory = sqlite3.Row
    out = {}
    for r in con.execute("SELECT sym_side, updated_at, full_config_json FROM per_sym_active "
                         "WHERE full_config_json IS NOT NULL AND length(full_config_json) > 100"):
        if keys_wanted and r["sym_side"] not in keys_wanted:
            continue
        try:
            out[r["sym_side"]] = (json.loads(_maybe_decompress(r["full_config_json"])), "per_sym_active",
                                  age_days(r["updated_at"], now))
        except Exception:
            pass
    try:
        hist = con.execute("SELECT sym_side, updated_at, full_config_json, MAX(COALESCE(epoch,0)) AS _e "
                           "FROM per_sym_history WHERE full_config_json IS NOT NULL AND length(full_config_json) > 100 "
                           "GROUP BY sym_side").fetchall()
    except sqlite3.OperationalError:
        # the live-only export pushed to S1 carries per_sym_active + kv_json and deliberately drops per_sym_history
        hist = []
    for r in hist:
        if r["sym_side"] in out or (keys_wanted and r["sym_side"] not in keys_wanted):
            continue
        try:
            out[r["sym_side"]] = (json.loads(_maybe_decompress(r["full_config_json"])), "per_sym_history",
                                  age_days(r["updated_at"], now))
        except Exception:
            pass
    con.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"))
    ap.add_argument("--cat-side-file", default=str(ROOT / "data" / "per_sym_settings.json"))
    ap.add_argument("--sym-sides", default="", help="comma list; default = all with a stored set")
    ap.add_argument("--tradeable", action="store_true", help="only sym_sides in tradeable_keys.json")
    ap.add_argument("--min-age-days", type=float, default=0.0, help="only sets at least this stale")
    ap.add_argument("--windows", default="30,7", help="comma list of window lengths in days")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default=str(ROOT / "data" / "reports" / "persym_vs_catside.json"))
    ap.add_argument("--allow-mac", action="store_true", help="run anyway on a host without the full NPZ set")
    a = ap.parse_args()

    import socket
    host = socket.gethostname()
    if "mac" in host.lower() or os.uname().sysname == "Darwin":
        if not a.allow_mac:
            print("REFUSING: the Mac holds only 134 of 473 NPZ stores, so most sym_sides would report a FALSE "
                  "'0 trades' and make the per_sym set look worthless. Run on S1 (CLAUDE.md), or pass --allow-mac "
                  "to measure only the symbols this host actually has.", file=sys.stderr)
            return 2
        print("WARNING --allow-mac: symbols without a local NPZ are reported as NPZ_MISSING, never as a loss.")

    import v12_quick_engine as V
    from tools import dc_simple_8_sweep as DC

    now = time.time()
    cat_defaults = json.load(open(a.cat_side_file))
    wanted = {s.strip() for s in a.sym_sides.split(",") if s.strip()}
    if a.tradeable:
        tk = {k.split(":", 1)[1] if ":" in k else k for k in json.load(open(ROOT / "tradeable_keys.json"))}
        wanted = (wanted & tk) if wanted else tk
    sets = load_sets(a.db, wanted, now)
    keys = sorted(sets)
    if a.min_age_days > 0:
        keys = [k for k in keys if (sets[k][2] or 0) >= a.min_age_days]
    keys.sort(key=lambda k: -(sets[k][2] or 0))
    if a.limit:
        keys = keys[: a.limit]
    windows = [int(w) for w in a.windows.split(",") if w.strip()]
    print(f"host={host}  sym_sides={len(keys)}  windows={windows}  min_trades={MIN_TRADES}\n")

    hdr = f"{'sym_side':22s} {'age_d':>6s} {'win':>4s} {'persym_gain':>12s} {'catside_gain':>13s} {'delta_pp':>9s} {'ps_tr':>6s} {'cs_tr':>6s}  verdict"
    print(hdr)
    print("-" * len(hdr))
    results = []
    for key in keys:
        full, src, age = sets[key]
        cat_side, sym, is_long = cat_side_of(key)
        base = cat_defaults.get(cat_side)
        if not isinstance(base, dict):
            print(f"{key:22s} {'':>6s} {'':>4s} no cat_side defaults for {cat_side}")
            continue
        try:
            stores = V.load_npz("crypto" if is_crypto(sym) else "tradier", [sym], "2024-01-01")
            npz = stores.get(sym) if isinstance(stores, dict) else stores
        except Exception as exc:
            npz = None
            err = repr(exc)[:60]
        if npz is None or len(npz.get("timestamps", [])) < 100:
            print(f"{key:22s} {age or -1:6.1f} {'':>4s} NPZ_MISSING — not measured (never counted as a loss)")
            results.append({"sym_side": key, "age_days": age, "source": src, "error": "NPZ_MISSING"})
            continue
        row = {"sym_side": key, "cat_side": cat_side, "age_days": age, "source": src, "windows": {}}
        for w in windows:
            ps, e1 = DC.eval_gain(npz, sym, is_long, full, w)
            cs, e2 = DC.eval_gain(npz, sym, is_long, base, w)
            if ps is None or cs is None:
                print(f"{key:22s} {age or -1:6.1f} {w:4d}  eval failed: {e1 or e2}")
                row["windows"][w] = {"error": e1 or e2}
                continue
            g_ps = float(ps.get("gain_pct_2000norm", 0.0) or 0.0)
            g_cs = float(cs.get("gain_pct_2000norm", 0.0) or 0.0)
            t_ps, t_cs = int(ps.get("trades", 0) or 0), int(cs.get("trades", 0) or 0)
            low = min(t_ps, t_cs) < MIN_TRADES
            delta = g_ps - g_cs
            verdict = "[LOW_SAMPLE]" if low else ("per_sym WINS" if delta > 1e-9 else
                                                  "cat_side WINS" if delta < -1e-9 else "tie")
            row["windows"][w] = {"persym_gain_pct": g_ps, "catside_gain_pct": g_cs, "delta_pp": delta,
                                 "persym_trades": t_ps, "catside_trades": t_cs,
                                 "persym_sharpe_per_trade": ps.get("sharpe_per_trade"),
                                 "catside_sharpe_per_trade": cs.get("sharpe_per_trade"),
                                 "persym_max_dd_pct": ps.get("max_dd_pct"), "catside_max_dd_pct": cs.get("max_dd_pct"),
                                 "persym_tim_pct": ps.get("tim_pct"), "catside_tim_pct": cs.get("tim_pct"),
                                 "low_sample": low, "verdict": verdict}
            print(f"{key:22s} {age or -1:6.1f} {w:4d} {g_ps:12.3f} {g_cs:13.3f} {delta:+9.3f} {t_ps:6d} {t_cs:6d}  {verdict}")
        results.append(row)

    tally = {}
    for r in results:
        for w, v in (r.get("windows") or {}).items():
            if not isinstance(v, dict) or v.get("error") or v.get("low_sample"):
                continue
            t = tally.setdefault(str(w), {"per_sym": 0, "cat_side": 0, "tie": 0, "sum_delta_pp": 0.0})
            t["sum_delta_pp"] += v["delta_pp"]
            t["per_sym" if v["delta_pp"] > 1e-9 else "cat_side" if v["delta_pp"] < -1e-9 else "tie"] += 1
    print("\nTALLY (sym_sides meeting the >=%d-trade floor only):" % MIN_TRADES)
    for w, t in sorted(tally.items(), key=lambda kv: -int(kv[0])):
        n = t["per_sym"] + t["cat_side"] + t["tie"]
        print(f"  {w:>3s}d  per_sym better {t['per_sym']:3d} | cat_side better {t['cat_side']:3d} | tie {t['tie']:3d}"
              f"   mean delta {t['sum_delta_pp']/n:+.3f} pp" if n else f"  {w}d  no qualifying sym_sides")
    out = {"_meta": {"built_at": datetime.now(timezone.utc).isoformat(), "host": host, "windows": windows,
                     "min_trades": MIN_TRADES, "n_sym_sides": len(results), "tally": tally,
                     "method": "same NPZ window, same engine (dc_simple_8_sweep.eval_gain -> V.simulate_one); "
                               "A=stored per_sym full set, B=cat_side defaults from per_sym_settings.json"},
           "results": results}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1, sort_keys=True, default=str))
    print(f"\nreport -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
