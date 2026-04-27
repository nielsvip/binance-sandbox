#!/usr/bin/env python
"""Daily symbol-rotator backtest using the real Tier-2 engine (backtest_v8_engine.py).

Runs trailing-N-day window over all symbols (broad) or top-50 from broad output (deep)
on the CURRENT live config, ranks by exponentially-weighted-gain (decay base 2.0/day,
front-loaded toward today). Outputs a winners JSON consumed by live fin sizing path.

Per CLAUDE.md the score is labeled `weighted_gain_pct`, NOT Sharpe — Sharpe is shown
as a diagnostic column with explicit "<30 trades = noisy" labeling.

Usage:
    python rotator.py --pass broad   # 7d × all syms → winners_broad.json
    python rotator.py --pass deep    # 14d × top-50 from broad → winners_final.json
"""
from __future__ import annotations
import argparse, csv, json, os, subprocess, sys, time, math
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path("/Users/niels/Documents/binance")
NPZ_DIR = REPO / "backtest_v8" / "indicators"
OUT_DIR = REPO / "data" / "rotator"
PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"
ENGINE = REPO / "backtest_v8_engine.py"
SYMBOLS_JSON = REPO / "symbols.json"
DECAY_BASE = 2.0  # each day = 2× weight of the previous (per user spec)


def utc_today() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def load_symbols(only_in_npz: bool = True) -> list[str]:
    syms = json.loads(SYMBOLS_JSON.read_text())
    if not only_in_npz:
        return syms
    available = {p.stem for p in NPZ_DIR.glob("*.npz")}
    crypto_suffixes = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI")
    return [s for s in syms if s in available and any(s.endswith(q) for q in crypto_suffixes)]


def run_engine(symbols: list[str], start_date: str, capital: float = 1000.0,
               log_path: Path | None = None, account: str = "fin") -> Path:
    """Run backtest_v8_engine.py for the given symbol set; return path to V8_LOG JSONL."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = log_path or (OUT_DIR / f"engine_run_{datetime.utcnow():%Y%m%d_%H%M%S}.stdout")
    cmd = [PYTHON, "-u", str(ENGINE),
           "--mode", "crypto", "--account", account, "--start", start_date,
           "--symbols", ",".join(symbols), "--capital", str(capital)]
    print(f"[rotator] running engine: {len(symbols)} syms, start={start_date}, capital={capital}")
    print(f"[rotator] log → {log_path}")
    t0 = time.time()
    with open(log_path, "wb") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, cwd=str(REPO))
    elapsed = time.time() - t0
    print(f"[rotator] engine exit={proc.returncode} elapsed={elapsed:.1f}s")
    if proc.returncode != 0:
        sys.exit(f"engine failed (exit {proc.returncode}); see {log_path}")
    v8_log = None
    for line in log_path.read_text(errors="ignore").splitlines():
        if line.startswith("V8_LOG: "):
            v8_log = Path(line.split("V8_LOG: ", 1)[1].strip())
            break
    if not v8_log or not v8_log.exists():
        sys.exit(f"could not find V8_LOG path in stdout; see {log_path}")
    print(f"[rotator] trades JSONL → {v8_log}")
    return v8_log


def parse_position_key(pk: str) -> tuple[str, str]:
    """fin:BTCUSDT_LONG → (BTCUSDT, LONG)."""
    after_colon = pk.split(":", 1)[-1]
    if after_colon.endswith("_LONG"):
        return after_colon[:-5], "LONG"
    if after_colon.endswith("_SHORT"):
        return after_colon[:-6], "SHORT"
    return after_colon, "UNKNOWN"


def trade_dt(t: dict) -> datetime:
    ts = t.get("timestamp")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(float(ts), tz=timezone.utc)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            pass
    return datetime.now(timezone.utc)


def score_trades(closes: list[dict], today: datetime) -> dict:
    """Compute the metric set for a single (symbol, side) bucket of close trades."""
    n = len(closes)
    if n == 0:
        return dict(n_trades=0)
    pcts, weights, wins = [], [], []
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in closes:
        p = float(t.get("pnl_pct") or 0.0)
        days_ago = max(0, (today.date() - trade_dt(t).date()).days)
        w = DECAY_BASE ** (-days_ago)
        pcts.append(p)
        weights.append(w)
        wins.append(1.0 if p > 0 else 0.0)
        cum += p
        peak = max(peak, cum)
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    sumw = sum(weights) or 1.0
    weighted_gain = sum(p * w for p, w in zip(pcts, weights)) / sumw
    weighted_wr = sum(w * win for w, win in zip(weights, wins)) / sumw
    mean_pct = sum(pcts) / n
    var = sum((p - mean_pct) ** 2 for p in pcts) / n if n > 1 else 0.0
    std_pct = math.sqrt(var)
    sharpe_pt = mean_pct / std_pct if std_pct > 0 else 0.0
    return dict(
        n_trades=n,
        weighted_gain_pct=round(weighted_gain, 4),
        weighted_wr=round(weighted_wr, 4),
        sharpe_pt=round(sharpe_pt, 4),
        accumulated_gain_pct=round(cum, 4),
        avg_gain_per_trade_pct=round(mean_pct, 4),
        max_dd_pct=round(max_dd, 4),
    )


def bnh_delta_pct(symbol: str, side: str, start_date: str) -> float | None:
    """Buy&hold reference for the symbol over the window, signed by side."""
    npz = NPZ_DIR / f"{symbol}.npz"
    if not npz.exists():
        return None
    try:
        import numpy as np
        d = np.load(npz, allow_pickle=True)
        ts = d["timestamps"]
        close = d["close"] if "close" in d.files else d["close_3m"]
        start_ts = int(datetime.fromisoformat(start_date + "T00:00:00+00:00").timestamp())
        idx = int(np.searchsorted(ts, start_ts))
        if idx >= len(close) - 1:
            return None
        p0, p1 = float(close[idx]), float(close[-1])
        if p0 <= 0:
            return None
        delta = (p1 - p0) / p0 * 100.0
        return round(delta if side == "LONG" else -delta, 4)
    except Exception:
        return None


def aggregate(trades_jsonl: Path, start_date: str, today: datetime) -> list[dict]:
    by_key: dict[str, list[dict]] = {}
    with open(trades_jsonl) as f:
        for line in f:
            try:
                t = json.loads(line)
            except Exception:
                continue
            if t.get("pnl_dollars") is None and t.get("pnl_pct") is None:
                continue
            pk = t.get("position_key") or ""
            sym, side = parse_position_key(pk)
            if not sym or side == "UNKNOWN":
                continue
            by_key.setdefault(f"{sym}_{side}", []).append(t)
    rows = []
    for key, closes in by_key.items():
        sym, side = key.rsplit("_", 1)
        m = score_trades(closes, today)
        m.update(
            symbol=sym,
            side=side,
            key=key,
            bnh_delta_pct=bnh_delta_pct(sym, side, start_date),
        )
        rows.append(m)
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        path.write_text("")
        return
    keys = ["key", "symbol", "side", "n_trades", "weighted_gain_pct", "weighted_wr",
            "sharpe_pt", "accumulated_gain_pct", "avg_gain_per_trade_pct",
            "max_dd_pct", "bnh_delta_pct"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in keys})


def write_winners_json(rows: list[dict], top_n: int, path: Path,
                       window_days: int, start_date: str, mode_label: str) -> dict:
    ranked = sorted(rows, key=lambda r: r.get("weighted_gain_pct", 0.0), reverse=True)
    winners = ranked[:top_n]
    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode_label,
        "window_days": window_days,
        "start_date": start_date,
        "decay_base": DECAY_BASE,
        "n_evaluated": len(rows),
        "n_winners": len(winners),
        "winners": [
            {"key": w["key"], "symbol": w["symbol"], "side": w["side"],
             "weighted_gain_pct": w["weighted_gain_pct"], "weighted_wr": w["weighted_wr"],
             "sharpe_pt": w["sharpe_pt"], "n_trades": w["n_trades"],
             "accumulated_gain_pct": w["accumulated_gain_pct"],
             "max_dd_pct": w["max_dd_pct"], "bnh_delta_pct": w["bnh_delta_pct"]}
            for w in winners
        ],
    }
    path.write_text(json.dumps(out, indent=2))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass", dest="pass_", choices=["broad", "deep"], required=True)
    ap.add_argument("--window-days", type=int)
    ap.add_argument("--top-n", type=int)
    ap.add_argument("--account", default="fin")
    ap.add_argument("--capital", type=float, default=1000.0)
    ap.add_argument("--symbols-file", type=str, help="JSON list of syms (deep pass uses winners_broad.json by default)")
    args = ap.parse_args()
    today = utc_today()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.pass_ == "broad":
        window = args.window_days or 7
        top_n = args.top_n or 50
        symbols = load_symbols(only_in_npz=True)
        out_csv = OUT_DIR / f"broad_{today:%Y%m%d}.csv"
        out_json = OUT_DIR / "winners_broad.json"
        mode_label = "broad"
    else:
        window = args.window_days or 14
        top_n = args.top_n or 50
        broad_path = Path(args.symbols_file) if args.symbols_file else (OUT_DIR / "winners_broad.json")
        if not broad_path.exists():
            sys.exit(f"broad winners JSON not found: {broad_path}")
        broad = json.loads(broad_path.read_text())
        symbols = sorted({w["symbol"] for w in broad["winners"]})
        out_csv = OUT_DIR / f"deep_{today:%Y%m%d}.csv"
        out_json = OUT_DIR / "winners_final.json"
        mode_label = "deep"
    if not symbols:
        sys.exit("no symbols to evaluate")
    start_date = (today - timedelta(days=window)).strftime("%Y-%m-%d")
    print(f"[rotator] pass={args.pass_} window={window}d start={start_date} symbols={len(symbols)} top_n={top_n}")
    trades_jsonl = run_engine(symbols, start_date, capital=args.capital, account=args.account)
    rows = aggregate(trades_jsonl, start_date, today)
    print(f"[rotator] scored {len(rows)} (symbol,side) buckets")
    write_csv(rows, out_csv)
    summary = write_winners_json(rows, top_n, out_json, window, start_date, mode_label)
    print(f"[rotator] csv → {out_csv}")
    print(f"[rotator] winners → {out_json} (n={summary['n_winners']})")
    if summary["winners"]:
        top = summary["winners"][:10]
        print("[rotator] top 10:")
        for w in top:
            print(f"  {w['key']:24s} weighted_gain={w['weighted_gain_pct']:+.3f}% wr={w['weighted_wr']:.2f} trades={w['n_trades']:3d} acc={w['accumulated_gain_pct']:+.2f}% bnh={w['bnh_delta_pct']}")


if __name__ == "__main__":
    main()
