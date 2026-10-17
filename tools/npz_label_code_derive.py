#!/usr/bin/env python3
"""npz_label_code_derive — DERIVE (never guess) the live-label -> builder-code table for NPZ categorical keys.

USER 2026-10-17 (the Mac-computes / S1-appends trickle): the live calculator emits categorical keys as STRINGS
("BULL", "LH", "IMPULSE_UP", "EXPANDING", "green", "inside_bar"), while backtest_v8_precompute stores the same
keys as int8 codes. `backtest_v8_precompute.py:2278` states it outright: "In live `wt_peak_structure` is string
'LH' / 'HH' — in precompute it's int8 (-1/+1)". Its own STR_MAP covers green/red/BUY/SELL/bullish/bearish/
higher/lower/none/bull_cross/bear_cross, but NOT the vocabulary the calculator actually produces, and for
wt_momentum_state (codes -2,-1,1,2) and wt_divergence (-2,-1,0,1,2) a guess would silently write the wrong code
into every board's input. So the table is MEASURED instead.

METHOD (exact, and self-checking): for a past bar the NPZ already holds, recompute the live row for that same
close with `npz_live_generator._compute_row_for_15m_close` and read the builder's stored code at that exact
timestamp. Each observation is one (key, label) -> code pair. Over many symbols and bars, a mapping is PINNED
only when every observation agrees; any disagreement is reported as a CONFLICT and nothing is written for it.

Run on S1 (it holds both the NPZs and the klines):
  python3 tools/npz_label_code_derive.py --symbols BNBUSDC,ETHUSDC,AAPL --bars 40
  python3 tools/npz_label_code_derive.py --auto 12 --bars 40 --write
"""
import argparse
import collections
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("NPZ_REFRESH_ROOT", os.path.expanduser("~/binance-sandbox")))
if not (ROOT / "backtest_v8").exists():
    ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
IND = ROOT / "backtest_v8" / "indicators"
OUT = ROOT / "data" / "npz_label_codes.json"
REPORT = ROOT / "data" / "reports" / "npz_label_code_derive.json"


def log(msg):
    print(f"[label_derive {time.strftime('%H:%M:%S', time.gmtime())}Z] {msg}", flush=True)


def is_crypto(s):
    return str(s).upper().endswith(("USDT", "USDC", "BUSD"))


def pick_symbols(n):
    import numpy as np
    out = []
    for p in sorted(IND.glob("*.npz")):
        s = p.stem
        if "_" in s:
            continue
        try:
            with np.load(p, allow_pickle=True) as z:
                if "timestamps" not in z.files or len(np.asarray(z["timestamps"]).reshape(-1)) < 500:
                    continue
        except Exception:
            continue
        out.append(s)
        if len(out) >= n * 3:
            break
    # mix crypto and stocks
    cry = [s for s in out if is_crypto(s)][: max(1, n // 2)]
    stk = [s for s in out if not is_crypto(s)][: max(1, n - len(cry))]
    return cry + stk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols")
    ap.add_argument("--auto", type=int, default=10)
    ap.add_argument("--bars", type=int, default=30, help="past bars per symbol to recompute")
    ap.add_argument("--write", action="store_true", help="write data/npz_label_codes.json")
    a = ap.parse_args()
    import numpy as np
    import npz_live_generator as gen
    import datetime as dt

    syms = [s.strip().upper() for s in a.symbols.split(",")] if a.symbols else pick_symbols(a.auto)
    log(f"deriving from {len(syms)} symbols x {a.bars} bars: {','.join(syms)}")

    calcs = {}

    def calc_for(mode):
        if mode in calcs:
            return calcs[mode]
        if mode == "tradier":
            from tradier_indicators import IndicatorCalculator as C
        else:
            from ez_indicators import IndicatorCalculator as C
        calcs[mode] = C()
        return calcs[mode]

    obs = collections.defaultdict(collections.Counter)   # (key,label) -> Counter(code)
    numeric_keys = {}
    n_rows = 0
    for sym in syms:
        p = IND / f"{sym}.npz"
        if not p.exists():
            continue
        mode = "crypto" if is_crypto(sym) else "tradier"
        try:
            with np.load(p, allow_pickle=True) as z:
                ts = np.asarray(z["timestamps"]).astype(np.int64).reshape(-1)
                n = len(ts)
                cols = {}
                for k in z.files:
                    arr = np.asarray(z[k])
                    if arr.ndim >= 1 and len(arr) == n and arr.dtype.kind in "biu":
                        cols[k] = arr
                        numeric_keys[k] = str(arr.dtype)
        except Exception as e:
            log(f"{sym}: NPZ unreadable ({type(e).__name__})")
            continue
        idx = {int(t): i for i, t in enumerate(ts.tolist())}
        # skip the newest bar (it may be the one still being appended)
        targets = [int(t) for t in ts[-(a.bars + 2):-2].tolist()]
        try:
            c = calc_for(mode)
        except Exception as e:
            log(f"cannot build {mode} calculator: {type(e).__name__}: {e}")
            continue
        got = 0
        for t in targets:
            close_ts = dt.datetime.fromtimestamp(t, tz=dt.timezone.utc)
            try:
                row = gen._compute_row_for_15m_close(sym, close_ts, None, c, mode)
            except Exception as e:
                continue
            if not row:
                continue
            got += 1
            n_rows += 1
            i = idx.get(t)
            if i is None:
                continue
            for k, v in row.items():
                if not isinstance(v, str) or k not in cols:
                    continue
                obs[(k, v)][int(cols[k][i])] += 1
        log(f"{sym} ({mode}): recomputed {got}/{len(targets)} bars")

    pinned, conflicts = {}, {}
    for (k, label), counter in sorted(obs.items()):
        codes = dict(counter)
        if len(codes) == 1:
            pinned.setdefault(k, {})[label] = next(iter(codes))
        else:
            conflicts.setdefault(k, {})[label] = codes
    log(f"observations: {n_rows} rows | pinned {sum(len(v) for v in pinned.values())} (key,label) pairs across {len(pinned)} keys | CONFLICTS {sum(len(v) for v in conflicts.values())}")
    for k in sorted(pinned):
        log(f"   {k}: {pinned[k]}")
    for k in sorted(conflicts):
        log(f"   CONFLICT {k}: {conflicts[k]}")
    payload = {"at": time.time(), "rows_observed": n_rows, "symbols": syms, "bars_per_symbol": a.bars,
               "pinned": pinned, "conflicts": conflicts,
               "note": "label -> int8 code, measured by recomputing the live row for bars the builder already stored. A pair is pinned only when every observation agreed."}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str))
    log(f"report -> {REPORT}")
    # The table is written for BASE-TF (15m) keys ONLY, and the HTF conflicts above are the measured reason why.
    # A 1h/4h/D/W/M value stored on a 15m row is the builder's AS-OF BROADCAST of the last closed HTF bar
    # (_broadcast_asof_indices); recomputing the live row gets that TF's own latest close instead, so the two
    # disagree wherever the broadcast index differs. MEASURED 2026-10-17 over 75 rows: every 15m categorical pinned
    # unanimously, while wt_cross_4h BEAR split -1/+1 (64:11), ha_1h green split +1/-1 (28:13) and wt_wave_phase_4h
    # EXPANDING split +1/-1 (57:10). That is not resolvable by more observations -- it is the wrong question. So the
    # trickled row only ever moves 15m keys and HTF carries forward (the true value until the bucket closes), with
    # tools/npz_bar_refresh.py's builder tail pass the only thing that moves an HTF key.
    # timestamp_* is excluded too: it is an ISO-string -> epoch CONVERSION, not a label code, and the ingest does it
    # arithmetically instead of from a lookup of every timestamp ever seen.
    base_tfs = ("_15m", "_5m", "_3m", "_1m")
    base_pinned = {k: v for k, v in pinned.items()
                   if k.endswith(base_tfs) and not k.startswith("timestamp_")}
    base_conflicts = {k: v for k, v in conflicts.items()
                      if k.endswith(base_tfs) and not k.startswith("timestamp_")}
    if a.write and base_pinned:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({
            "pinned": base_pinned,
            "omitted_conflicted_pairs": base_conflicts,
            "htf_unmappable": sorted(k for k in conflicts if not k.endswith(base_tfs)),
            "derived_at": time.time(), "rows_observed": n_rows, "symbols": syms,
            "scope": "base-TF keys only; a (key,label) pair is present ONLY if every observation agreed. An absent "
                     "label is NOT guessed by the ingest -- it drops the key for that bar, so the NPZ carries the "
                     "last value forward and npz_bar_refresh's builder tail corrects it.",
        }, indent=1, sort_keys=True))
        log(f"table -> {OUT}  ({sum(len(v) for v in base_pinned.values())} base-TF pairs across {len(base_pinned)} keys"
            f"; omitted {sum(len(v) for v in base_conflicts.values())} conflicted base pairs"
            f"; {len([k for k in conflicts if not k.endswith(base_tfs)])} HTF keys unmappable by design)")
        for k, v in sorted(base_conflicts.items()):
            log(f"   OMITTED (not guessed) {k}: {v}")
    elif a.write:
        log("REFUSING to write: no base-TF pair reached unanimity")
    return 1 if conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
