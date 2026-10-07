#!/usr/bin/env python3
"""batch5 RESEARCH-gate replay (D8: _apply_research_only_live_gates is_entry on real recent stock opens). Derived from live_parity_gate_replay. batch5 replay (Agent D): for REAL recent stock OPEN events (data/history/trb|trc/*.jsonl) rebuild the indicator dict at the event time from klines_cache
(staged batch1 tradier_indicators => has ema_9_above_21_*), run each live_entry_gates predicate with the CURRENT TradierConfig defaults and report how many of the
real opens each gate WOULD HAVE BLOCKED, split fresh-entry vs reentry/HARDCODED_RALLY. usage: python tools/live_parity_gate_replay.py [N=120] [days=30]"""
import collections, datetime, glob, importlib.util, json, os, random, sys
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); os.environ.setdefault("EZ_LOG_DIR", "/tmp/ez_log_parity")
import live_entry_gates as G
import importlib.util as _iu
_sp = _iu.spec_from_file_location("tm_staged", ROOT / "data/live_parity/staged/batch5_live_gates/tradier_manage.py"); TM = _iu.module_from_spec(_sp); _sp.loader.exec_module(TM)
import vec_decisions.check_entry_candidates_stocks__lh_hl_filter as LH
import vec_decisions.check_entry_candidates_stocks__ema_alignment_trend_htf_gates as EG
spec = importlib.util.spec_from_file_location("ti_staged", ROOT / "data/live_parity/staged/batch1/files/tradier_indicators.py"); TI = importlib.util.module_from_spec(spec); spec.loader.exec_module(TI)
import config_tradier as CT
cfgobj = CT.TradierConfig()
get = lambda k, d: getattr(cfgobj, k, d)
N = int(sys.argv[1]) if len(sys.argv) > 1 else 120; DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 30
cut = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=DAYS)).isoformat()
ev = []
for acct in ("trb", "trc"):
    for f in glob.glob(str(ROOT / f"data/history/{acct}/*.jsonl")):
        sym_side = Path(f).stem
        if "_" not in sym_side: continue
        sym, side = sym_side.rsplit("_", 1)
        for l in open(f, errors="ignore"):
            try: e = json.loads(l)
            except Exception: continue
            if e.get("type") == "OPEN" and str(e.get("ts", "")) >= cut and "BROKER_SYNC" not in str(e.get("reason", "")):
                ev.append((sym, side, e["ts"], str(e.get("reason", ""))))
random.seed(7); random.shuffle(ev); ev = ev[:N]
cache = {}
def kl(sym, tf):
    k = (sym, tf)
    if k not in cache:
        p = ROOT / "klines_cache" / f"{sym}USDT_{tf}.json"
        if not p.exists(): cache[k] = None
        else:
            d = pd.DataFrame(json.load(open(p)))
            for c in ("open", "high", "low", "close", "volume"): d[c] = pd.to_numeric(d[c], errors="coerce")
            d["t"] = pd.to_datetime(d["timestamp"], utc=True); cache[k] = d.dropna(subset=["close"]).reset_index(drop=True)
    return cache[k]
res = collections.Counter(); n2 = collections.Counter(); n = collections.Counter(); skipped = 0; reasons = collections.Counter()
for sym, side, ts, reason in ev:
    t = pd.Timestamp(ts)
    ind = {}
    ok = True
    for tf in ("15m", "1h", "4h", "D"):
        d = kl(sym, tf)
        if d is None: ok = False; break
        w = d[d["t"] <= t].copy()
        if len(w) < 60: ok = False; break
        try: ind.update(TI.IndicatorCalculator().compute(w, sym, tf, None, None, False))
        except Exception: ok = False; break
    if not ok: skipped += 1; continue
    is_long = side == "LONG"
    cls = "REENTRY/RALLY" if ("REENTRY" in reason.upper() or "RALLY" in reason.upper()) else "FRESH"
    n[cls] += 1; reasons[reason[:34]] += 1
    try:
        blk, why = TM._apply_research_only_live_gates("trb", sym, side, ind, True)
    except Exception as e:
        blk, why = False, f"ERR {type(e).__name__}"
    n2[(cls, "research_block" if blk else "pass")] += 1
    if blk: res[(cls, why.split("(")[0])] += 1
print(json.dumps({"events_sampled": len(ev), "replayed": dict(n), "skipped_no_klines": skipped, "would_block": {f"{c}|{g}": v for (c, g), v in sorted(res.items())}, "split": {f"{c}|{g}": v for (c, g), v in sorted(n2.items())}, "top_reasons": reasons.most_common(6),
                  "defaults": {k: get(k, None) for k in ("LH_HL_FILTER_ENABLED", "EMA_9_21_FILTER_ENABLED", "EMA_9_21_TIMEFRAME", "ALIGNMENT_GATE_MIN", "TREND_GATES", "HTF1_CONF", "HTF4_CONF")}}, indent=1))
