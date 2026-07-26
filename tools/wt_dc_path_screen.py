#!/usr/bin/env python3
"""Fast isolated WT_DC parameter screen.

This is a Tier-1 shortlist, not a promotion test.  It evaluates only the
WT_DC entry family, deduplicates contiguous signal episodes, and reports:

* signal count and 5/20-session forward return;
* win rate;
* first-signal hold return versus side-and-hold over the same window;
* how many qualifying bars the old exact-engine numeric-cross bug lost.

Exact confirmation must use backtest_v8_engine with every other entry family
explicitly disabled and compare against both B&H and the same-entry control.
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path
import sys

import numpy as np

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from vec_paths.wt_dc_entry import compute_wt_dc_entry_vec, score_entry_multitf_vec


def _load(path: Path, days: int) -> dict:
    with np.load(path, allow_pickle=True) as z:
        ts = np.asarray(z["timestamps"], dtype=np.int64)
        start = int(np.searchsorted(ts, ts[-1] - days * 86400))
        return {
            k: z[k][start:]
            for k in z.files
            if isinstance(z[k], np.ndarray)
            and z[k].ndim == 1
            and len(z[k]) == len(ts)
        }


def _episode_starts(mask: np.ndarray) -> np.ndarray:
    return np.flatnonzero(mask & ~np.r_[False, mask[:-1]])


def _side_return(start: float, end: float, side: str) -> float:
    if start <= 0 or end <= 0:
        return 0.0
    return (
        ((end / start) - 1.0) * 100.0
        if side == "LONG"
        else ((start - end) / start) * 100.0
    )


def screen_symbol(path: Path, side: str, days: int) -> list[dict]:
    data = _load(path, days)
    n = len(data["timestamps"])
    close = np.asarray(data.get("close_5m", data.get("close")), dtype=np.float64)
    is_long = side == "LONG"
    fixed_score = score_entry_multitf_vec(data, n, is_long).astype(np.float64)
    cross = np.asarray(data.get("wt_cross_1h", np.zeros(n)))
    old_score = fixed_score - 30.0 * ((cross > 0) if is_long else (cross < 0))
    rows = []
    for threshold, htf_gate, align, stoch in itertools.product(
        (20.0, 35.0, 45.0, 60.0, 75.0, 85.0),
        ("none", "1h", "4h", "4h_D"),
        (0, 1, 2, 3),
        (40.0, 60.0, 80.0, 100.0),
    ):
        mask, score = compute_wt_dc_entry_vec(
            data,
            n,
            is_long,
            threshold=threshold,
            htf_gate=htf_gate,
            htf_align_required=align,
            combined_stoch_gate=stoch,
        )
        starts = _episode_starts(mask)
        if not len(starts):
            continue
        out = {
            "symbol": path.stem,
            "side": side,
            "days": days,
            "threshold": threshold,
            "htf_gate": htf_gate,
            "htf_align_required": align,
            "combined_stoch_gate": stoch,
            "episodes": int(len(starts)),
            "qualifying_bars": int(mask.sum()),
            "old_exact_lost_bars": int((mask & (old_score < threshold)).sum()),
        }
        for sessions in (5, 20):
            horizon = 78 * sessions
            idx = starts[starts + horizon < n]
            if len(idx):
                rets = (
                    (close[idx + horizon] / close[idx] - 1.0) * 100.0
                    if is_long
                    else (close[idx] - close[idx + horizon]) / close[idx] * 100.0
                )
                out[f"fwd_{sessions}s_n"] = int(len(rets))
                out[f"fwd_{sessions}s_mean_pct"] = float(np.mean(rets))
                out[f"fwd_{sessions}s_median_pct"] = float(np.median(rets))
                out[f"fwd_{sessions}s_wr_pct"] = float(np.mean(rets > 0) * 100.0)
            else:
                out[f"fwd_{sessions}s_n"] = 0
                out[f"fwd_{sessions}s_mean_pct"] = None
                out[f"fwd_{sessions}s_median_pct"] = None
                out[f"fwd_{sessions}s_wr_pct"] = None
        first = int(starts[0])
        out["first_signal_hold_pct"] = _side_return(close[first], close[-1], side)
        out["side_hold_full_window_pct"] = _side_return(close[0], close[-1], side)
        out["first_signal_delay_bars"] = first
        rows.append(out)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz-dir", default="backtest_v8/indicators")
    ap.add_argument("--keys", default="MU_LONG,VT_LONG,HAO_SHORT,NVDA_LONG")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    root = Path(args.npz_dir)
    all_rows = []
    for key in args.keys.split(","):
        key = key.strip().upper()
        if not key:
            continue
        symbol, side = key.rsplit("_", 1)
        path = root / f"{symbol}.npz"
        if path.exists():
            all_rows.extend(screen_symbol(path, side, args.days))
    ranked = sorted(
        (
            r
            for r in all_rows
            if r.get("fwd_5s_n", 0) >= 8 and r.get("fwd_5s_mean_pct") is not None
        ),
        key=lambda r: (
            r["fwd_5s_mean_pct"],
            r["fwd_5s_wr_pct"],
            r["episodes"],
        ),
        reverse=True,
    )
    payload = {
        "contract": {
            "tier": "VEC_DIAGNOSTIC",
            "promotion_eligible": False,
            "entry_family": "WT_DC_ONLY",
            "dc_low4_used_as_exit": False,
            "same_entry_control": "first qualifying WT_DC episode then hold",
        },
        "rows": all_rows,
        "top": ranked[: args.top],
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(payload["top"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
