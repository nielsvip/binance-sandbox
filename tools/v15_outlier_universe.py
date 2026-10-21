#!/usr/bin/env python3
"""Outlier-vs-BTC universe classifier (men D / fin 1h).

USER 2026-10-21: men and fin must trade outliers vs BTCUSDC — long the winners,
short the losers — instead of an indiscriminate both-sides list. men runs on D
vs BTC SMA_200_D; fin runs on 1h vs BTC SMA_200_1h. The directional books
(symbols_men_long/short.json, symbols_fin_long/short.json) are the output;
ez_positions_service.cleanup_positions honors them since the 2026-10-21 split.

Score per symbol (single number, trend-adjusted):
    gap(sym) = close / sma_200 - 1        (on the account TF, last closed bar)
    score    = gap(sym) - gap(BTCUSDC)     (outperformance vs BTC)
z-scored across the measured universe; top N -> LONG, bottom N -> SHORT,
side-exclusive by |z| (a symbol takes exactly one side).

Dry-run by default; --apply = backup to backups/ + atomic write
(tmp + fsync + os.replace) + re-read verify. Mirrors the
tools/v15_daily_inf_universe.py apply discipline.
"""

import argparse
import datetime as _dt
import json
import math
import os
import shutil
import statistics
import sys
from pathlib import Path

ROOT = Path(
    os.environ.get("OUTLIER_UNIVERSE_ROOT") or Path(__file__).resolve().parents[1]
)
OUT_DIR = ROOT / "data" / "outlier_universe"
BOOKS = {
    "men": {
        "LONG": ROOT / "symbols_men_long.json",
        "SHORT": ROOT / "symbols_men_short.json",
    },
    "fin": {
        "LONG": ROOT / "symbols_fin_long.json",
        "SHORT": ROOT / "symbols_fin_short.json",
    },
}
TF = {"men": "D", "fin": "1h"}
BENCHMARK = "BTCUSDC"
MIN_BARS_DEFAULT = 210
TOP_DEFAULT = 25


def jload(path, default=None):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default


def closes_for(symbol, tf, min_bars):
    data = jload(ROOT / "klines_cache" / f"{symbol}_{tf}.json", None)
    if not isinstance(data, list) or len(data) < min_bars:
        return None
    out = []
    for bar in data:
        try:
            out.append(float(bar.get("close")))
        except Exception:
            return None
    if len(out) < min_bars or any(c <= 0 for c in out):
        return None
    return out


def sma(values, n):
    return sum(values[-n:]) / n


def universe(tf):
    syms = set()
    for path in (ROOT / "klines_cache").glob(f"*_{tf}.json"):
        name = path.name[: -len(f"_{tf}.json")]
        if name == BENCHMARK:
            continue
        if not (name.endswith("USDT") or name.endswith("USDC")):
            continue
        syms.add(name)
    excl = jload(ROOT / "data" / "universe_exclude.json", {}) or {}
    banned = set()
    for key in ("exclude_symbols", "exclude_sym_sides"):
        for entry in excl.get(key, []) or []:
            banned.add(str(entry).upper().split("_")[0])
    return sorted(syms - banned - {BENCHMARK})


def measure(account, min_bars):
    tf = TF[account]
    bench = closes_for(BENCHMARK, tf, min_bars)
    if bench is None:
        return None, {"error": f"benchmark {BENCHMARK}_{tf} unavailable or short"}
    bench_gap = bench[-2] / sma(bench[:-1], 200) - 1
    bench_px = bench[-2]
    bench_sma = sma(bench[:-1], 200)
    rows = []
    skipped = 0
    for symbol in universe(tf):
        closes = closes_for(symbol, tf, min_bars)
        if closes is None:
            skipped += 1
            continue
        gap = closes[-2] / sma(closes[:-1], 200) - 1
        rows.append(
            {
                "symbol": symbol,
                "close": closes[-2],
                "sma200": sma(closes[:-1], 200),
                "gap": gap,
                "score": gap - bench_gap,
            }
        )
    if not rows:
        return None, {"error": "no measurable symbols", "skipped": skipped}
    mean = statistics.fmean(r["score"] for r in rows)
    stdev = statistics.pstdev(r["score"] for r in rows) or 1.0
    for row in rows:
        row["z"] = (row["score"] - mean) / stdev
    meta = {
        "tf": tf,
        "n": len(rows),
        "skipped": skipped,
        "mean": mean,
        "stdev": stdev,
        "btc_close": bench_px,
        "btc_sma200": bench_sma,
        "btc_above_sma200": bench_px > bench_sma,
    }
    return rows, meta


def pick(rows, top):
    longs = sorted(rows, key=lambda r: r["z"], reverse=True)[:top]
    shorts = sorted(rows, key=lambda r: r["z"])[:top]
    long_syms = [r["symbol"] for r in longs]
    short_syms = [r["symbol"] for r in shorts]
    clash = set(long_syms) & set(short_syms)
    if clash:
        by_sym = {r["symbol"]: r for r in rows}
        for symbol in clash:
            row = by_sym[symbol]
            if row["z"] >= 0:
                short_syms.remove(symbol)
            else:
                long_syms.remove(symbol)
    return long_syms, short_syms


def atomic_write_json(path, obj):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    try:
        with open(tmp, "w") as fh:
            fh.write(json.dumps(obj, indent=2) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise
    if json.loads(path.read_text()) != obj:
        raise RuntimeError(f"post-write verify failed for {path}")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Outlier-vs-BTC universe classifier (men D / fin 1h)."
    )
    ap.add_argument("--account", default="both", choices=["men", "fin", "both"])
    ap.add_argument("--top", type=int, default=TOP_DEFAULT)
    ap.add_argument("--min-bars", type=int, default=MIN_BARS_DEFAULT)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--allow-empty-side", action="store_true")
    args = ap.parse_args(argv)
    min_bars = args.min_bars
    accounts = ["men", "fin"] if args.account == "both" else [args.account]
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d")
    now_iso = _dt.datetime.now(_dt.timezone.utc).isoformat()
    report = {
        "generated_utc": now_iso,
        "applied": False,
        "top": args.top,
        "accounts": {},
    }
    refused = []
    for account in accounts:
        rows, meta = measure(account, min_bars)
        if rows is None:
            report["accounts"][account] = {
                "error": meta.get("error"),
                "books": {"LONG": [], "SHORT": []},
            }
            refused.append(f"{account}: {meta.get('error')}")
            continue
        longs, shorts = pick(rows, args.top)
        prev = {
            side: jload(BOOKS[account][side], []) or [] for side in ("LONG", "SHORT")
        }
        table = {r["symbol"]: r for r in rows}
        report["accounts"][account] = {
            "meta": meta,
            "books": {"LONG": sorted(longs), "SHORT": sorted(shorts)},
            "detail": {
                "LONG": [
                    {
                        "symbol": s,
                        "z": round(table[s]["z"], 3),
                        "score_pct": round(table[s]["score"] * 100, 2),
                    }
                    for s in sorted(longs)
                ],
                "SHORT": [
                    {
                        "symbol": s,
                        "z": round(table[s]["z"], 3),
                        "score_pct": round(table[s]["score"] * 100, 2),
                    }
                    for s in sorted(shorts)
                ],
            },
            "diff": {
                side: {
                    "added": sorted(
                        set(sorted(longs if side == "LONG" else shorts))
                        - set(prev[side])
                    ),
                    "removed": sorted(
                        set(prev[side]) - set(longs if side == "LONG" else shorts)
                    ),
                    "kept": sorted(
                        set(prev[side]) & set(longs if side == "LONG" else shorts)
                    ),
                }
                for side in ("LONG", "SHORT")
            },
        }
        for side in ("LONG", "SHORT"):
            if (
                not report["accounts"][account]["books"][side]
                and not args.allow_empty_side
            ):
                refused.append(
                    f"{account} {side} book would be empty — pass --allow-empty-side to write an empty book"
                )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jp, mp = OUT_DIR / f"{stamp}.json", OUT_DIR / f"{stamp}.md"
    for cur, prev_path in (
        (jp, OUT_DIR / f"{stamp}.prev.json"),
        (mp, OUT_DIR / f"{stamp}.prev.md"),
    ):
        try:
            if cur.exists():
                shutil.copy2(cur, prev_path)
        except Exception:
            pass
    if args.apply and refused:
        report["refused"] = refused
        jp.write_text(json.dumps(report, indent=1, default=str))
        print(f"[outlier-universe] APPLY REFUSED: {' | '.join(refused)}")
        return 2

    def _summary():
        parts = []
        for a in accounts:
            nl = len(report["accounts"][a]["books"]["LONG"])
            ns = len(report["accounts"][a]["books"]["SHORT"])
            parts.append(f"{a} LONG={nl} SHORT={ns}")
        return ", ".join(parts)

    if args.apply:
        (ROOT / "backups").mkdir(exist_ok=True)
        tag = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d%H%M")
        for account in accounts:
            for side in ("LONG", "SHORT"):
                book = report["accounts"][account]["books"][side]
                dest = BOOKS[account][side]
                try:
                    if dest.exists():
                        shutil.copy2(
                            dest,
                            ROOT
                            / "backups"
                            / f"before_outlier_universe_{tag}_{dest.name}",
                        )
                except Exception:
                    pass
                atomic_write_json(dest, book)
        report["applied"] = True
        print(f"[outlier-universe] APPLIED {_summary()}")
    else:
        if refused:
            print(
                f"[outlier-universe] (dry-run) an --apply now would be refused: {' | '.join(refused)}"
            )
        else:
            print(f"[outlier-universe] (dry-run) books ready: {_summary()}")
    lines = [
        f"# outlier universe {stamp} ({'APPLIED' if report['applied'] else 'DRY-RUN'})",
        "",
        f"- generated {now_iso}; top {args.top} per side; score = sym_sma200_gap - btc_sma200_gap, z-scored",
        "",
    ]
    for account in accounts:
        entry = report["accounts"][account]
        if "error" in entry and "books" in entry and not entry.get("meta"):
            lines += [f"## {account} ({TF[account]}): ERROR {entry['error']}", ""]
            continue
        meta = entry["meta"]
        lines += [
            f"## {account} ({meta['tf']}): n={meta['n']} skipped={meta['skipped']} BTC {'ABOVE' if meta['btc_above_sma200'] else 'BELOW'} sma200",
            "",
        ]
        for side in ("LONG", "SHORT"):
            names = entry["books"][side]
            lines.append(f"### {side} ({len(names)})")
            for row in entry["detail"][side][: args.top]:
                lines.append(
                    f"- {row['symbol']} z={row['z']} score={row['score_pct']}%"
                )
            lines.append("")
    mp.write_text("\n".join(lines))
    jp.write_text(json.dumps(report, indent=1, default=str))
    print(f"[outlier-universe] report {jp} {mp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
