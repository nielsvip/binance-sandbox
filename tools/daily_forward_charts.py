#!/usr/bin/env python3
"""daily_forward_charts — 30D hires charts of the FORWARD state, cut at the day's 20:00Z close.

Runs ON S5 (stocks) or S1 (crypto), from the host's own NPZ store. For every sym_side the host's forward
state (data/forward_state/<SYM_SIDE>.json, tools/v15_forward_state.py) decides for, it:

  1. takes the switch set the forward state uses: the ACTIVE published set in per_sym_store.db
     (tools/v15_forward_state._load_overrides), with the same retired-key drop and stale-set anchors as
     state_for() — not a progress json, not a board sheet;
  2. cuts the NPZ at the 20:00Z close: a 15m bar is stamped at its OPEN, so the close-20:00 bar is
     stamped 19:45 (the forward state's npz_tail convention); bars stamped later are never seen;
  3. evaluates the trades through the forward path itself (tools/opt/evaluate_v12.prepare +
     evaluate_prepared_sanitized, the call state_for() makes) on that cut NPZ;
  4. renders the same hires chart (tools/opt/hires_chart.generate_hires) over that ledger into
     data/reports/charts_daily_30d/<SYM_SIDE>_30d_daily.html. Re-running overwrites the previous day's file.

--verify: cuts at the tail the forward state itself was computed on (its npz_tail) and checks the forward
ledger's trade count and gain against the state's own trades / gain_pct. A mismatch is reported, never hidden.

Usage (on the host):  .venv/bin/python tools/daily_forward_charts.py --venue stock|crypto [--cut 2026-10-29T20:00Z]
                      [--only AAPL_LONG] [--verify] [--out DIR]
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
HISTORY_DIR = ROOT / "data" / "forward_state_history"
BAR_S = 900
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
WINDOW_DAYS = 30


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


def _recorded_state(symside: str, cut_open: float, current: dict) -> dict | None:
    if int(current.get("npz_tail") or 0) == int(cut_open) and isinstance(current.get("ledger"), list):
        return current
    records = sorted((HISTORY_DIR / symside).glob(f"{int(cut_open)}_*.json"), reverse=True)
    for path in records:
        try:
            state = json.loads(path.read_text())
            if state.get("ok") and isinstance(state.get("ledger"), list):
                return state
        except (OSError, ValueError):
            continue
    return None


def _install_cut(E, cut_open_s: float):
    """Wrap evaluate_v12._store so every NPZ the forward path or the chart loads ends at the cut bar (inclusive).
    Both prepare() and build_cfg_npz() load through _store, so the cut applies before any slicing."""
    original = E._store

    def cut_store(*args, **kwargs):
        npz = original(*args, **kwargs)
        if npz is None:
            return None
        ts = np.asarray(npz.get("timestamps"), dtype="float64")
        finite = np.flatnonzero(np.isfinite(ts) & (ts > 0))
        scale = 1000.0 if float(ts[finite[-1]]) > 1e11 else 1.0
        n = len(ts)
        keep = int(np.searchsorted(ts / scale, cut_open_s, side="right"))
        cut = dict(npz)
        for k, v in npz.items():
            if isinstance(v, np.ndarray) and v.shape[:1] == (n,):
                cut[k] = v[:keep]
        return cut

    E._store = cut_store
    return original


def _npz_paths(symside: str, E) -> list:
    """The NPZ files state_for() reads through merged_npz_reads (zip plus its .tail sidecar)."""
    sym = symside.rsplit("_", 1)[0]
    names = {sym}
    try:
        names.add(E.resolve_tokenised(sym)[0])
    except Exception:
        pass
    return [ROOT / pfx / "indicators" / f"{n}.npz" for pfx in ("backtest_v8", "backtest_v7") for n in names]


def _forward_month(E, sym: str, overrides: dict, window_days: int = WINDOW_DAYS) -> dict:
    """The forward path, exactly as state_for() calls it, returned in the shape generate_hires reads."""
    from tools.opt.v12_pilot import evaluate_prepared_sanitized
    prep = E.prepare(sym, window_days)
    if prep is None:
        raise ValueError("prepare returned None (no NPZ)")
    res = dict(evaluate_prepared_sanitized(prep, overrides, window_days, include_ledger=True) or {})
    ledger = res.get("ledger") or []
    res["execution_ledger"] = ledger
    res["ledger"] = ledger
    res.setdefault("bh_pct", prep.get("bh"))
    res["window"] = prep.get("window") or {}
    res["_prep_window"] = prep.get("window") or {}
    return res


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", choices=("stock", "crypto"), required=True)
    ap.add_argument("--cut", default=None, help="20:00Z close, e.g. 2026-10-29T20:00Z (default: today 20:00Z)")
    ap.add_argument("--only", default=None)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args(argv)

    if a.cut:
        cut_close = _epoch(a.cut if a.cut.endswith("Z") else a.cut + "Z")
    else:
        today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        cut_close = _epoch(f"{today}T20:00:00Z")
    cut_open = cut_close - BAR_S

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    import tools.opt.evaluate_v12 as E
    from tools.v15_forward_state import _load_overrides, _drop_retired, _stale_anchor
    from tools.opt.hires_chart import generate_hires
    import tools.opt.lifecycle_pilot as LP

    rows = []
    t0 = time.time()
    syms = _sym_sides(a.venue, a.only)
    print(f"[daily-charts] venue={a.venue} sym_sides={len(syms)} cut_close={_stamp(cut_close)} cut_bar_open={_stamp(cut_open)}", flush=True)
    for i, sym in enumerate(syms):
        row = {"symside": sym, "cut_close": _stamp(cut_close)}
        tag = f"[{i + 1}/{len(syms)}] {sym}"
        original = None
        try:
            state = json.loads((FS_DIR / f"{sym}.json").read_text())
            overrides, src, meta = _load_overrides(sym)
            if not overrides:
                row.update(ok=False, reason=f"no active published set ({src})")
                print(f"{tag} SKIP {row['reason']}", flush=True)
                rows.append(row)
                continue
            dropped = _drop_retired(overrides)
            overrides = {k: v for k, v in overrides.items() if k not in dropped}
            anc = _stale_anchor(sym) if src == "per_sym_store.active" else {}
            if anc:
                overrides = {**overrides, "STALE_SET_RECALC_EPOCH": anc["STALE_SET_RECALC_EPOCH"], "STALE_SET_APPLY_EPOCH": anc["STALE_SET_APPLY_EPOCH"]}
            use_open = float(state["npz_tail"]) if a.verify else cut_open
            E._STORES.clear()
            _paths = _npz_paths(sym, E)
            from tools.npz_merged_view import merged_npz_reads
            with merged_npz_reads(_paths):
                original = _install_cut(E, use_open)
                fwd = _forward_month(E, sym, overrides)
            recorded = _recorded_state(sym, use_open, state)
            if recorded is not None:
                fwd.update(ledger=recorded["ledger"], execution_ledger=recorded["ledger"],
                           gain_pct=recorded.get("gain_pct"), trades=recorded.get("trades"))
            led = fwd["ledger"]
            row.update(ok=True, trades=fwd.get("trades"), ledger_rows=len(led), gain_pct=fwd.get("gain_pct"), cut_bar_open=_stamp(use_open),
                       ledger_source="published_state" if recorded is not None else "recomputed_current_npz",
                       src=src, settings_sha256=meta.get("settings_sha256"), valid=fwd.get("valid"),
                       invalid_reason=fwd.get("invalid_reason"))
            if a.verify:
                row.update(state_trades=state.get("trades"), state_gain_pct=state.get("gain_pct"),
                           state_open=state.get("open"), state_tail=state.get("npz_tail"))
                row["match"] = bool(row["trades"] == state.get("trades")
                                    and row["gain_pct"] is not None and state.get("gain_pct") is not None
                                    and abs(float(row["gain_pct"]) - float(state["gain_pct"])) < 0.01)
            # generate_hires pulls its evaluation from lifecycle_pilot.evaluate_month: hand it the forward ledger
            LP.evaluate_month = lambda s, ov, include_ledger=False, window_days=WINDOW_DAYS, _f=fwd: dict(_f)
            label_end = _stamp(use_open + BAR_S)
            with merged_npz_reads(_paths):
                generate_hires(sym, dict(overrides), WINDOW_DAYS, out_name=f"{sym}_30d_daily.html",
                               heading=f"{sym} — 30D window ending {label_end}", out_dir=str(out))
            print(f"{tag} OK trades={row['trades']} gain={row['gain_pct']}" + (f" MATCH={row['match']} (state trades={row['state_trades']} gain={row['state_gain_pct']})" if a.verify else ""), flush=True)
        except Exception as exc:
            row.update(ok=False, reason=f"{type(exc).__name__}: {str(exc)[:200]}")
            print(f"{tag} FAIL {row['reason']}", flush=True)
        finally:
            if original is not None:
                E._store = original
        rows.append(row)

    (out / "manifest.json").write_text(json.dumps({"venue": a.venue, "cut_close": _stamp(cut_close), "verify": a.verify,
                                                  "generated": _stamp(time.time()), "rows": rows}, indent=1, default=str))
    ok = sum(1 for r in rows if r.get("ok"))
    print(f"[daily-charts] done ok={ok} total={len(rows)} elapsed={time.time() - t0:.0f}s manifest={out / 'manifest.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
