#!/usr/bin/env python3
"""daily_forward_charts — 30D hires charts of the FORWARD state, cut at the day's 20:00Z close.

Runs ON S5 (stocks) or S1 (crypto), from the host's own NPZ store. For every sym_side the host's forward
state (data/forward_state/<SYM_SIDE>.json, tools/v15_forward_state.py) decides for, it:

  1. takes the switch set the forward state uses: the ACTIVE published set in per_sym_store.db
     (tools/v15_forward_state._load_overrides) — not a progress json, not a board sheet;
  2. cuts the NPZ at the 20:00Z close: a 15m bar is stamped at its OPEN, so the close-20:00 bar is
     stamped 19:45 (the forward state's npz_tail convention); bars stamped later are never seen;
  3. renders the same hires chart (tools/opt/hires_chart.generate_hires) over the rolling 30D window
     that ends at that cut, into data/reports/charts_daily_30d/<SYM_SIDE>_30d_daily.html.
     Re-running overwrites the previous day's file for the same sym_side.

--verify: instead of the 20:00 cut, cuts at the tail the forward state itself was computed on and checks
trades and gain against it. A mismatch is reported, never papered over.

Usage (on the host):  python3 tools/daily_forward_charts.py --venue stock|crypto [--cut 2026-10-29T20:00Z]
                      [--only AAPL_LONG] [--verify]
Writes manifest.json in the output dir: one row per sym_side with its cut, trades, gain and any skip reason.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
FS_DIR = ROOT / "data" / "forward_state"
OUT_DIR = ROOT / "data" / "reports" / "charts_daily_30d"
BAR_S = 900
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")


def _epoch(iso: str) -> float:
    return dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()


def _stamp(sec: float) -> str:
    return dt.datetime.fromtimestamp(sec, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_crypto(symside: str) -> bool:
    return symside.rsplit("_", 1)[0].endswith(CRYPTO_SUFFIXES)


def _sym_sides(venue: str, only: str | None) -> list[str]:
    out = []
    for p in sorted(FS_DIR.glob("*.json")):
        name = p.name[: -len(".json")]
        if not (name.endswith("_LONG") or name.endswith("_SHORT")):
            continue
        if only and name != only:
            continue
        if (venue == "crypto") != _is_crypto(name):
            continue
        out.append(name)
    return out


def _cut_patch(cut_open_s: float):
    """Wrap evaluate_v12.build_cfg_npz so the NPZ it hands the engine ends at the cut bar (inclusive)."""
    import tools.opt.evaluate_v12 as E

    original = E.build_cfg_npz

    def cut_build(symside, overrides, *args, **kwargs):
        cfg, npz, sym, is_long, mode, tokenised = original(symside, overrides, *args, **kwargs)
        if npz is None:
            return cfg, npz, sym, is_long, mode, tokenised
        ts = np.asarray(npz.get("timestamps"), dtype="float64")
        scale = 1000.0 if float(ts[np.flatnonzero(np.isfinite(ts) & (ts > 0))[-1]]) > 1e11 else 1.0
        n = len(ts)
        keep = int(np.searchsorted(ts / scale, cut_open_s, side="right"))
        cut_npz = dict(npz)
        for k, v in npz.items():
            if isinstance(v, np.ndarray) and v.shape[:1] == (n,):
                cut_npz[k] = v[:keep]
        return cfg, cut_npz, sym, is_long, mode, tokenised

    E.build_cfg_npz = cut_build
    return E, original


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", choices=("stock", "crypto"), required=True)
    ap.add_argument("--cut", default=None, help="20:00Z close, e.g. 2026-10-29T20:00Z (default: today 20:00Z)")
    ap.add_argument("--only", default=None)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args(argv)

    if a.cut:
        cut_close = _epoch(a.cut)
    else:
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        cut_close = _epoch(f"{today}T20:00:00Z")
    cut_open = cut_close - BAR_S

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from tools.v15_forward_state import _load_overrides, _drop_retired, _stale_anchor
    from tools.opt.hires_chart import generate_hires
    from tools.opt.lifecycle_pilot import evaluate_month

    rows = []
    t0 = time.time()
    syms = _sym_sides(a.venue, a.only)
    print(f"[daily-charts] venue={a.venue} sym_sides={len(syms)} cut_close={_stamp(cut_close)} cut_bar_open={_stamp(cut_open)}", flush=True)
    for i, sym in enumerate(syms):
        row = {"symside": sym, "cut_close": _stamp(cut_close)}
        tag = f"[{i + 1}/{len(syms)}] {sym}"
        try:
            state = json.loads((FS_DIR / f"{sym}.json").read_text())
            overrides, src, meta = _load_overrides(sym)
            if not overrides:
                row.update(ok=False, reason=f"no active published set ({src})")
                print(f"{tag} SKIP {row['reason']}", flush=True)
                rows.append(row)
                continue
            # the forward state's exact override set: retired keys dropped, stale-set anchors added (state_for)
            _dropped = _drop_retired(overrides)
            overrides = {k: v for k, v in overrides.items() if k not in _dropped}
            _anc = _stale_anchor(sym) if src == "per_sym_store.active" else {}
            if _anc:
                overrides = {**overrides, "STALE_SET_RECALC_EPOCH": _anc["STALE_SET_RECALC_EPOCH"], "STALE_SET_APPLY_EPOCH": _anc["STALE_SET_APPLY_EPOCH"]}
            use_open = float(state["npz_tail"]) if a.verify else cut_open
            E, original = _cut_patch(use_open)
            try:
                r = evaluate_month(sym, overrides, include_ledger=True, window_days=30)
                ledger = r.get("execution_ledger") or r.get("ledger") or []
                row.update(ok=bool(r.get("valid", True)), trades=len(ledger), gain_pct=r.get("gain_pct", r.get("gain")),
                           cut_bar_open=_stamp(use_open), settings_sha256=meta.get("settings_sha256"), src=src)
                if a.verify:
                    row.update(state_trades=state.get("trades"), state_gain_pct=state.get("gain_pct"),
                               state_open=state.get("open"), state_tail=state.get("npz_tail"))
                    row["match"] = (row["trades"] == state.get("trades")) and (
                        row["gain_pct"] is not None and state.get("gain_pct") is not None
                        and abs(float(row["gain_pct"]) - float(state["gain_pct"])) < 0.01)
                generate_hires(sym, dict(overrides), 30, out_name=f"{sym}_30d_daily.html",
                               heading=f"{sym} — 30D window ending {row['cut_close'] if not a.verify else _stamp(use_open + BAR_S)}",
                               out_dir=str(out))
            finally:
                E.build_cfg_npz = original
            print(f"{tag} OK trades={row.get('trades')} gain={row.get('gain_pct')} {'MATCH' if row.get('match') else ''}".rstrip(), flush=True)
        except Exception as exc:
            row.update(ok=False, reason=f"{type(exc).__name__}: {str(exc)[:200]}")
            print(f"{tag} FAIL {row['reason']}", flush=True)
        rows.append(row)

    (out / "manifest.json").write_text(json.dumps({"venue": a.venue, "cut_close": _stamp(cut_close), "generated": _stamp(time.time()), "rows": rows}, indent=1, default=str))
    ok = sum(1 for r in rows if r.get("ok"))
    print(f"[daily-charts] done ok={ok} total={len(rows)} elapsed={time.time() - t0:.0f}s manifest={out / 'manifest.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
