#!/usr/bin/env python3
"""v15_persym_size_tiers — daily chain: data/persym_size_tiers.json for PERF_TIER_SIZING (USER 2026-10-06: risk carried by SIZE).

Input: the day's per-sym go-live report (data/daily_chain/persym_golive_<date>*.json; every evaluated row carries the FRESH
30D evidence of that sym_side's latest final set) + 365D (data/confirmed_365d.json gain_365d, else the progress final_365d).
Rules (per venue, crypto / stocks ranked separately):
  30D < 0 and 365D <= 0 or unknown  -> MIN      mult = --min-mult (default 0.25)
  30D < 0 and 365D > 0              -> NEG30    mult = --neg30-mult (default 0.5)
  30D > 0                           -> RANKED   mult = 1 + (--max-mult - 1) * min(g30, --gain-cap) / --gain-cap (default cap 20.0, max-mult 3.0);
                                                bigger gain = bigger size; capped at 1.0 when 365D <= 0 (anti-overfit)
  no fresh row + book acc_gain > 0  -> BOOK_POS_HOLD  mult 1.0 (proven winner awaiting recalc keeps trading)
  no fresh row + book acc_gain <= 0 -> BOOK_NEG  mult = min-mult (monitored, minimal money)
  in tradeable universe, nowhere    -> UNLISTED_MIN  mult = min-mult (monitored, minimal money)
  USER 2026-10-09: no per_sym_store.db row (live would trade DEFAULTS) -> NOSTORE_MIN mult = min-mult, regardless of
  the tier above. Defaults are for testing the waters — defaults at max size is ridiculous. Original tier kept in was_tier.
Live floors/caps (exchange minimum, 1 share, MAX_ORDER_VALUE) are applied by perf_tier_sizing at order time.
  python tools/v15_persym_size_tiers.py --report data/daily_chain/persym_golive_<date>.json [--out data/persym_size_tiers.json] [--dry-run]
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD")


def _ranked_mult(g30, g365, max_mult=3.0, gain_cap=20.0):
    """USER 2026-10-09: magnitude-proportional size — bigger gain = bigger START_POSITION_SIZE.
    scale = min(g30, cap)/cap; 365D <= 0 caps at 1.0 (a 30D gainer that loses on 365D is overfit, not a winner)."""
    try:
        scale = min(float(g30), float(gain_cap)) / float(gain_cap)
    except (TypeError, ValueError, ZeroDivisionError):
        return 1.0
    scale = max(0.0, min(1.0, scale))
    m = 1.0 + (float(max_mult) - 1.0) * scale
    try:
        if g365 is not None and float(g365) <= 0:
            m = min(m, 1.0)
    except (TypeError, ValueError):
        pass
    return round(m, 3)


def _venue_of(ss):
    return "crypto" if str(ss).rsplit("_", 1)[0].endswith(CRYPTO_SUFFIX) else "stocks"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--out", default=str(ROOT / "data" / "persym_size_tiers.json"))
    ap.add_argument("--min-mult", type=float, default=0.25)
    ap.add_argument("--neg30-mult", type=float, default=0.5)
    ap.add_argument("--max-mult", type=float, default=3.0)
    ap.add_argument("--gain-cap", type=float, default=20.0)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    rep = json.loads(Path(a.report).read_text())
    conf = {}
    try:
        conf = json.loads((ROOT / "data" / "confirmed_365d.json").read_text())
    except Exception:
        pass
    rows = []
    for ss, r in (rep.get("sym_sides") or {}).items():
        ev = r.get("evidence") or {}
        g30 = ev.get("gain_pct")
        if g30 is None or int(ev.get("trades") or 0) == 0:
            continue
        c = conf.get(ss)
        if c is not None:
            g365, src365 = c.get("gain_365d"), "confirmed_365d"
        elif isinstance(r.get("final_365d"), dict):
            g365, src365 = r["final_365d"].get("gain_pct"), "progress final_365d"
        else:
            g365, src365 = None, "none"
        rows.append({"ss": ss, "venue": "crypto" if ss.rsplit("_", 1)[0].endswith(CRYPTO_SUFFIX) else "stocks", "g30": float(g30), "g365": (float(g365) if g365 is not None else None), "src365": src365, "trades_30d": ev.get("trades"), "tim_30d": ev.get("tim_pct")})
    tiers = {}
    for venue in ("crypto", "stocks"):
        pos = sorted([x for x in rows if x["venue"] == venue and x["g30"] > 0], key=lambda x: x["g30"])
        n = len(pos)
        for i, x in enumerate(pos):
            m = _ranked_mult(x["g30"], x["g365"], a.max_mult, a.gain_cap)
            tier = "RANKED_NEG365_CAP1" if (x["g365"] is not None and x["g365"] <= 0) else "RANKED"
            tiers[x["ss"]] = dict(x, mult=m, tier=tier, rank=f"{i + 1}/{n}")
        for x in rows:
            if x["venue"] != venue or x["g30"] >= 0:
                continue
            if x["g365"] is not None and x["g365"] > 0:
                tiers[x["ss"]] = dict(x, mult=a.neg30_mult, tier="NEG30_POS365")
            else:
                tiers[x["ss"]] = dict(x, mult=a.min_mult, tier="MIN")
    for book in ("per_sym_active_config.json", "per_sym_active_config_stocks.json"):  # live NEG-block is relaxed under tier sizing: every
        try:                                                                          # book entry with acc_gain_pct <= 0 and no fresh row -> MIN
            bk = json.loads((ROOT / "data" / "hourly_reconfig" / book).read_text())
        except Exception:
            continue
        for ss, e in bk.items():
            if ss in tiers or not isinstance(e, dict) or ss.startswith("_"):
                continue
            try:
                g = e.get("acc_gain_pct")
                if g is None:
                    continue
                g = float(g)
                if g <= 0:
                    tiers[ss] = {"venue": "crypto" if book.endswith("config.json") else "stocks", "g30": g, "g365": None, "src365": "book acc_gain_pct (no fresh row)", "trades_30d": e.get("trades"), "tim_30d": e.get("tim_pct"), "mult": a.min_mult, "tier": "BOOK_NEG"}
                else:
                    tiers[ss] = {"venue": "crypto" if book.endswith("config.json") else "stocks", "g30": g, "g365": None, "src365": "book acc_gain_pct (no fresh row)", "trades_30d": e.get("trades"), "tim_30d": e.get("tim_pct"), "mult": 1.0, "tier": "BOOK_POS_HOLD"}
            except Exception:
                continue
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import v15_universe as _U
        _u, _how = _U.load_or_build(root=ROOT, write=False)
        _allowed = set((_u or {}).get("allowed_sym_sides") or [])
    except Exception:
        _allowed = set()
    for ss in sorted(_allowed):
        if ss not in tiers:
            tiers[ss] = {"venue": _venue_of(ss), "g30": 0.0, "g365": None, "src365": "tradeable universe (no evidence)", "trades_30d": 0, "tim_30d": None, "mult": a.min_mult, "tier": "UNLISTED_MIN"}
    try:
        sys.path.insert(0, str(ROOT))
        import per_sym_store as _pss
        _stored = set(_pss.all_sym_sides()) if hasattr(_pss, "all_sym_sides") else {_r[0] for _r in __import__("sqlite3").connect(str(_pss.DB_PATH)).execute("SELECT sym_side FROM per_sym_active")}
        _nostore = sorted(ss for ss, v in tiers.items() if v["mult"] > a.min_mult and ss not in _stored)
        for ss in _nostore:
            tiers[ss]["was_tier"] = tiers[ss]["tier"]
            tiers[ss]["tier"] = "NOSTORE_MIN"
            tiers[ss]["mult"] = a.min_mult
        print(f"[tiers] NOSTORE_MIN demotions (no per_sym_store row -> defaults test waters): {len(_nostore)} {','.join(_nostore[:12])}{'...' if len(_nostore) > 12 else ''}")
    except Exception as _nse:
        _nostore = []
        print(f"[tiers] WARNING: per_sym_store check failed, keeping ranked sizes (fail-open): {_nse}")
    for v in tiers.values():
        v.pop("ss", None)
    out = {"_meta": {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "report": str(a.report), "rules": {"min_mult": a.min_mult, "neg30_mult": a.neg30_mult, "max_mult": a.max_mult, "gain_cap": a.gain_cap},
                     "counts": {t: sum(1 for v in tiers.values() if v["tier"] == t) for t in ("MIN", "NEG30_POS365", "RANKED", "RANKED_NEG365_CAP1", "BOOK_NEG", "BOOK_POS_HOLD", "UNLISTED_MIN", "NOSTORE_MIN")}, "consumer": "perf_tier_sizing.py (PERF_TIER_SIZING_ENABLED)"}, "tiers": tiers}
    srt = sorted(tiers.items(), key=lambda kv: -kv[1]["mult"])
    print(json.dumps(out["_meta"]["counts"]))
    print("TOP 10:")
    for k, v in srt[:10]:
        print(f"  {k:22s} x{v['mult']:.2f} {v['tier']:20s} 30D={v['g30']:+.2f} 365D={v['g365']} ({v['src365']}) trades={v['trades_30d']} tim={v['tim_30d']}")
    print("BOTTOM 10:")
    for k, v in srt[-10:]:
        print(f"  {k:22s} x{v['mult']:.2f} {v['tier']:20s} 30D={v['g30']:+.2f} 365D={v['g365']} ({v['src365']}) trades={v['trades_30d']} tim={v['tim_30d']}")
    if a.dry_run:
        print("[tiers] DRY-RUN: nothing written")
        return 0
    p = Path(a.out)
    if p.exists():
        bk = ROOT / "backups" / f"before_size_tiers_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M')}_{p.name}"
        bk.write_bytes(p.read_bytes())
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=1, default=str))
    json.loads(tmp.read_text())
    os.replace(tmp, p)
    print(f"[tiers] {len(tiers)} sym_sides -> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
