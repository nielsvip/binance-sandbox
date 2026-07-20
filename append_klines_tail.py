#!/usr/bin/env python3
"""
Tail-freshness append: copy new bars from the LIVE klines caches (klines_cache +
klines_cache_gateway) into klines_cache_backtest, for both crypto (tradeable_keys) and
stocks (symbols_trb_long/short). Per CLAUDE.md NPZ REGEN rule: get the 15m base to within
minutes of NOW before regenerating NPZ.

Sources merged per (symbol, tf), newest-wins, deduped by timestamp:
  klines_cache/  +  klines_cache_gateway/   (live repo on S1: /home/niels/binance/...)
Dest:
  klines_cache_backtest/  (sandbox: /home/niels/binance-sandbox/...)

Append-only: never deletes/rewrites existing bars, only appends bars strictly newer than
the dest's current last bar. Safe to run constantly.

  --no-backup   skip per-file backups (for the constant idle loop — append is non-destructive)
  --mode crypto|tradier|both   (default both)
"""
import json
import os
import shutil
import datetime
import argparse

BASE_PATH = os.environ.get("BINANCE_BASE")
if not BASE_PATH:
    for _cand in ("/Users/niels/Documents/binance", "/home/niels/binance-sandbox", "/home/niels/binance"):
        if os.path.isdir(_cand):
            BASE_PATH = _cand
            break

# On S1 the fresh LIVE + GATEWAY caches live in the live repo (/home/niels/binance);
# the backtest dest is the sandbox. On the Mac everything is under one tree.
if BASE_PATH == "/home/niels/binance-sandbox":
    LIVE_REPO = "/home/niels/binance"
else:
    LIVE_REPO = BASE_PATH

SRC_CRYPTO = [os.path.join(LIVE_REPO, "klines_cache"), os.path.join(LIVE_REPO, "klines_cache_gateway")]
SRC_TRADIER = [os.path.join(LIVE_REPO, "klines_cache", "tradier"), os.path.join(LIVE_REPO, "klines_cache_gateway", "tradier")]
DEST_CRYPTO = os.path.join(BASE_PATH, "klines_cache_backtest")
DEST_TRADIER = os.path.join(BASE_PATH, "klines_cache_backtest", "tradier")
BACKUP_DIR = os.path.join(BASE_PATH, "backups")
TS_LABEL = datetime.datetime.utcnow().strftime("%Y%m%d%H%M")


def backup_file(path):
    dest = os.path.join(BACKUP_DIR, f"before_klines_tail_append_{TS_LABEL}_{os.path.basename(path)}")
    try:
        shutil.copy2(path, dest)
    except Exception:
        pass


def _bar_ts(bar):
    return bar.get("timestamp") or bar.get("close_time") or bar.get("t")


def normalize_bar(bar):
    ts = _bar_ts(bar)
    return {
        "timestamp": ts,
        "open": bar.get("open", bar.get("o")),
        "high": bar.get("high", bar.get("h")),
        "low": bar.get("low", bar.get("l")),
        "close": bar.get("close", bar.get("c")),
        "volume": bar.get("volume", bar.get("v", 0)),
    }


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def append_new_bars(bt_path, src_paths, sym, tf, do_backup):
    """Append bars newer than dest's last bar, merged across all src_paths. Returns n_appended."""
    if not os.path.exists(bt_path):
        return 0  # no backtest base to extend (don't fabricate from scratch here)
    bt_data = _load(bt_path)
    if not bt_data:
        return 0
    bt_last_ts = _bar_ts(bt_data[-1])
    if not bt_last_ts:
        return 0
    merged_new = {}
    for sp in src_paths:
        live_data = _load(sp)
        if not live_data:
            continue
        for bar in live_data:
            ts = _bar_ts(bar)
            if ts and ts > bt_last_ts and ts not in merged_new:
                nb = normalize_bar(bar)
                if nb["close"] is not None:
                    merged_new[ts] = nb
    if not merged_new:
        return 0
    new_bars = [merged_new[k] for k in sorted(merged_new)]
    if do_backup:
        backup_file(bt_path)
    bt_data.extend(new_bars)
    tmp = bt_path + f".tmp.{os.getpid()}"
    with open(tmp, "w") as f:
        json.dump(bt_data, f, separators=(",", ":"))
    os.replace(tmp, bt_path)  # atomic
    return len(new_bars)


def crypto_symbols():
    tk_path = os.path.join(BASE_PATH, "tradeable_keys.json")
    if os.path.exists(tk_path):
        tk = _load(tk_path) or []
        syms = sorted({
            k.split(":", 1)[1].rsplit("_", 1)[0] if ":" in k else k.rsplit("_", 1)[0]
            for k in tk if (k.endswith("_LONG") or k.endswith("_SHORT"))
        })
        syms = [s for s in syms if s.endswith("USDT") or s.endswith("USDC")]
        if syms:
            return syms
    return ["BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC", "XRPUSDC", "ADAUSDC",
            "LINKUSDC", "LTCUSDC", "AVAXUSDC", "UNIUSDC", "DOGEUSDC", "BTCDOMUSDT"]


def tradier_symbols():
    syms = set()
    for f in ("symbols_trb_long.json", "symbols_trb_short.json"):
        p = os.path.join(BASE_PATH, f)
        d = _load(p)
        if isinstance(d, list):
            syms.update(d)
    return sorted(s for s in syms if len(s) <= 10)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["crypto", "tradier", "both"], default="both")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()
    do_backup = not args.no_backup
    total = 0
    if args.mode in ("tradier", "both"):
        syms = tradier_symbols()
        n = 0
        for sym in syms:
            for tf in ("15m", "5m"):
                bt = os.path.join(DEST_TRADIER, f"{sym}_{tf}.json")
                srcs = [os.path.join(d, f"{sym}_{tf}.json") for d in SRC_TRADIER]
                n += append_new_bars(bt, srcs, sym, tf, do_backup)
        print(f"tradier: {len(syms)} syms, appended {n} bars", flush=True)
        total += n
    if args.mode in ("crypto", "both"):
        syms = crypto_symbols()
        n = 0
        for sym in syms:
            bt = os.path.join(DEST_CRYPTO, f"{sym}_15m.json")
            srcs = [os.path.join(d, f"{sym}_15m.json") for d in SRC_CRYPTO]
            n += append_new_bars(bt, srcs, sym, "15m", do_backup)
        print(f"crypto: {len(syms)} syms, appended {n} bars", flush=True)
        total += n
    print(f"TOTAL appended: {total}", flush=True)


if __name__ == "__main__":
    main()
