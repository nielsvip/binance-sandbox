#!/usr/bin/env python3
"""Plot recorded forward execution events from the exact published state.

Historical NPZ bytes can change after a state is written. A state without a saved ledger is
unverifiable: quarantine its old chart and record the failure in manifest.json. The chart plots
only recorded execution prices; the price path between events was not preserved.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import sys
import time
from pathlib import Path


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


def _render_recorded_events(symside: str, state: dict, target: Path) -> None:
    """Plot recorded execution prices only; no later NPZ is read."""
    events = [(float(e["ts"]), float(e["price"]), str(e.get("type") or ""), str(e.get("reason") or e.get("exit_reason") or ""))
              for e in state["ledger"] if isinstance(e, dict) and e.get("ts") is not None and e.get("price") is not None]
    if not events:
        raise ValueError("recorded ledger has no priced events")
    times = [e[0] for e in events]
    prices = [e[1] for e in events]
    lo_t, hi_t = min(times), max(times)
    lo_p, hi_p = min(prices), max(prices)
    span_t, span_p = max(1.0, hi_t - lo_t), max(1e-9, hi_p - lo_p)
    xy = [(70 + 1060 * (t - lo_t) / span_t, 650 - 570 * (p - lo_p) / span_p) for t, p, _, _ in events]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    dots = []
    for (x, y), (ts, price, typ, reason) in zip(xy, events):
        color = "#16a34a" if typ == "OPEN" else "#dc2626" if typ == "CLOSE" else "#eab308"
        label = html.escape(f"{_stamp(ts)} {typ} {price:.8g} {reason}")
        dots.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{color}"><title>{label}</title></circle>')
    title = html.escape(f"{symside} — recorded forward execution events")
    subtitle = html.escape(f"State {_stamp(_epoch(state['ts']))} · bar {_stamp(float(state['npz_tail']))} · {state.get('trades')} trades · gain {state.get('gain_pct')}%")
    doc = f'<!doctype html><meta charset="utf-8"><title>{title}</title><style>body{{font:16px system-ui;background:#0b1220;color:#e5e7eb;margin:32px}}svg{{max-width:100%;background:#111827;border:1px solid #374151}}p{{color:#9ca3af}}</style><h1>{title}</h1><p>{subtitle}</p><p>Recorded event prices only. The price path between events was not preserved.</p><svg viewBox="0 0 1200 720" role="img" aria-label="Recorded execution event prices"><polyline fill="none" stroke="#93c5fd" stroke-width="2" points="{line}"/>{"".join(dots)}<text x="70" y="35" fill="#e5e7eb">{hi_p:.8g}</text><text x="70" y="690" fill="#e5e7eb">{lo_p:.8g}</text></svg>'
    target.write_text(doc)


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
    rows = []
    t0 = time.time()
    syms = _sym_sides(a.venue, a.only)
    print(f"[daily-charts] venue={a.venue} sym_sides={len(syms)} cut_close={_stamp(cut_close)} cut_bar_open={_stamp(cut_open)}", flush=True)
    for i, sym in enumerate(syms):
        row = {"symside": sym, "cut_close": _stamp(cut_close)}
        tag = f"[{i + 1}/{len(syms)}] {sym}"
        try:
            state = json.loads((FS_DIR / f"{sym}.json").read_text())
            use_open = float(state["npz_tail"]) if a.verify else cut_open
            recorded = _recorded_state(sym, use_open, state)
            target = out / f"{sym}_30d_daily.html"
            if recorded is None or not recorded.get("ok") or not isinstance(recorded.get("ledger"), list):
                if target.exists():
                    target.rename(target.with_name(target.name + ".unverified"))
                row.update(ok=False, reason="NO_STATE_TIME_LEDGER: historical replay cannot be verified", cut_bar_open=_stamp(use_open))
                print(f"{tag} SKIP {row['reason']}", flush=True)
                rows.append(row)
                continue
            digest = hashlib.sha256(json.dumps(recorded["ledger"], sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
            if digest != recorded.get("ledger_sha256"):
                raise ValueError("recorded ledger hash mismatch")
            _render_recorded_events(sym, recorded, target)
            row.update(ok=True, trades=recorded.get("trades"), ledger_rows=len(recorded["ledger"]), gain_pct=recorded.get("gain_pct"), cut_bar_open=_stamp(use_open),
                       ledger_source="published_state", settings_sha256=recorded.get("settings_sha256"), ledger_sha256=digest)
            if a.verify:
                row["match"] = int(recorded["npz_tail"]) == int(state["npz_tail"]) and digest == state.get("ledger_sha256")
            print(f"{tag} OK recorded trades={row['trades']} gain={row['gain_pct']}", flush=True)
        except Exception as exc:
            row.update(ok=False, reason=f"{type(exc).__name__}: {str(exc)[:200]}")
            print(f"{tag} FAIL {row['reason']}", flush=True)
        rows.append(row)

    (out / "manifest.json").write_text(json.dumps({"venue": a.venue, "cut_close": _stamp(cut_close), "verify": a.verify,
                                                  "generated": _stamp(time.time()), "rows": rows}, indent=1, default=str))
    ok = sum(1 for r in rows if r.get("ok"))
    print(f"[daily-charts] done ok={ok} total={len(rows)} elapsed={time.time() - t0:.0f}s manifest={out / 'manifest.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
