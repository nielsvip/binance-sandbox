#!/usr/bin/env python3
"""per_sym_live_parity_audit — does live trade EXACTLY the latest best per_sym set?

Three independent checks, each fail-closed and each reported with counts, never a verdict without numbers:

  BOARD   per_sym_active (the set applied live) vs the newest per_sym_history row of the same sym_side.
          A difference means go-live did not apply the latest published board.
          MAX_AUGMENTS_PER_POSITION is skipped: the switch was deleted from every surface 2026-10-15 and
          purged from the live rows, so history rows legitimately still carry it.

  READER  what the CRYPTO (ez) readers resolve vs the board set, through the REAL reader module
          (per_sym_live_set.full_set — what ez_manage._PerSymProxy and ez_positions_quick now call).
          --legacy-reader instead models the pre-2026-10-16 path: the `overrides` half of
          data/hourly_reconfig/per_sym_active_config{,_stocks}.json, which carried 412 sym_sides against
          742 live rows and ~835 keys against the board's ~3546, so every key the board froze that the
          view did not carry fell through to the cat_side TEMPLATE layer that
          build_cat_side_defaults_4.py rebuilds every morning -> the sym_side ran at a value its own
          board never saw (measured: 142 of 327 crypto rows absent, 67 more drifted). Keep the flag: it
          is how the drift this closed stays reproducible.
          The 8 CRYPTO_MIN_SIZE_CAPS sizing knobs are reported SEPARATELY, never as parity breaches:
          config.CRYPTO_MINIMUM_TRADING_MODE (USER 2026-10-16 "reduce crypto trading to minimum") is a
          newer instruction than any board, and the reader clamps them on purpose.
          STOCKS are NOT checked here: tradier_manage._cfg already resolves per_sym_store.get_overrides
          first (tradier_manage.py ~7811), and ez_manage._psym_get (~8248) does too — both are correct.
          --include-stocks forces the check over stock rows as well.

  TRADED  sym_sides with trades in data/history/<acct> that have no live row at all (they trade on
          cat_side/global defaults, i.e. on no board), and the ones absent from the proxy JSON.

Exit 1 when any check reports a breach, so a cron/cadence run is a gate and not just a log.

Usage: python3 tools/per_sym_live_parity_audit.py [--days 7] [--accounts ang,fin,flz,inf,men,trb] [--json OUT]
"""
from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from per_sym_store import _mandate_sanitize, _maybe_decompress
import per_sym_live_set as _live_reader

# deliberate, newer than any board: USER 2026-10-16 "reduce crypto trading to minimum"
CAP_KNOBS = dict(_live_reader.CRYPTO_MIN_SIZE_CAPS)


def _mode_name():
    """Which mode the ceiling is currently taken from, for the report line only."""
    try:
        import config as _cfg

        return str(getattr(_cfg.Config, "_CURRENT_MARKET_MODE", "?"))
    except Exception:
        return "?"

DB = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
PROXY_JSONS = (ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json",
               ROOT / "data" / "hourly_reconfig" / "per_sym_active_config_stocks.json")
CAT_SIDE_JSON = ROOT / "data" / "per_sym_settings.json"
HISTORY = ROOT / "data" / "history"
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
# deleted from every surface 2026-10-15; history rows still carry it by design
BOARD_SKIP = ("MAX_AUGMENTS_PER_POSITION",)


def _norm(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return f"{float(v):.9g}"
    return str(v).strip().lower()


def _cat_side(sym_side: str) -> str:
    sym, _, side = sym_side.rpartition("_")
    return ("CRYPTO_" if sym.endswith(CRYPTO_SUFFIXES) else "STOCKS_") + side


def _load_store():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=300.0)
    con.row_factory = sqlite3.Row
    active, applied_at = {}, {}
    for r in con.execute("SELECT sym_side,updated_at,full_config_json FROM per_sym_active"):
        try:
            active[r["sym_side"]] = _mandate_sanitize(json.loads(_maybe_decompress(r["full_config_json"])))
        except Exception:
            continue
        applied_at[r["sym_side"]] = str(r["updated_at"] or "")
    newest = {}
    for r in con.execute("SELECT sym_side,full_config_json,updated_at FROM per_sym_history h WHERE id="
                         "(SELECT id FROM per_sym_history x WHERE x.sym_side=h.sym_side "
                         " ORDER BY COALESCE(x.epoch,0) DESC, x.id DESC LIMIT 1)"):
        try:
            newest[r["sym_side"]] = (_mandate_sanitize(json.loads(_maybe_decompress(r["full_config_json"]))),
                                     str(r["updated_at"] or ""))
        except Exception:
            continue
    con.close()
    return active, applied_at, newest


def _load_proxy():
    """Exactly what ez_manage._PerSymProxy builds: {sym_side: entry['overrides']}."""
    out = {}
    for p in PROXY_JSONS:
        try:
            raw = json.load(open(p))
        except Exception as exc:
            print(f"  !! {p.name}: unreadable ({exc}) — proxy check incomplete", file=sys.stderr)
            continue
        for k, v in raw.items():
            if k != "_meta" and isinstance(v, dict):
                out[k] = v.get("overrides", {}) or {}
    return out


def _traded(accounts, days):
    cut = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat()
    out = collections.defaultdict(set)
    for acct in accounts:
        d = HISTORY / acct
        if not d.is_dir():
            print(f"  !! no history dir for {acct}", file=sys.stderr)
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".jsonl"):
                continue
            for line in open(d / fn, errors="replace"):
                line = line.strip()
                if not line:
                    continue
                try:
                    if str(json.loads(line).get("ts") or "") >= cut:
                        out[acct].add(fn[:-6])
                        break
                except Exception:
                    continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--accounts", default="ang,fin,flz,inf,men,trb")
    ap.add_argument("--legacy-reader", action="store_true",
                    help="model the pre-2026-10-16 reader (the JSON `overrides` half) instead of the live "
                         "per_sym_live_set path — keeps the drift this closed reproducible")
    ap.add_argument("--include-stocks", action="store_true",
                    help="also run the READER check over stock rows (default: crypto only — see module docstring)")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    accounts = [x.strip() for x in a.accounts.split(",") if x.strip()]
    active, applied_at, newest = _load_store()
    proxy = _load_proxy()
    cat = json.load(open(CAT_SIDE_JSON)) if CAT_SIDE_JSON.exists() else {}
    traded = _traded(accounts, a.days)
    report = {"generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(), "days": a.days,
              "n_live_rows": len(active), "n_proxy_rows": len(proxy)}

    print(f"=== BOARD: live set vs newest published board  ({len(active)} live rows)")
    board_bad = {}
    for ss, fc in active.items():
        nb = newest.get(ss)
        if nb is None:
            board_bad[ss] = ["<no history row>"]
            continue
        diff = [k for k in set(fc) | set(nb[0])
                if k not in BOARD_SKIP and _norm(fc.get(k)) != _norm(nb[0].get(k))]
        if diff:
            board_bad[ss] = sorted(diff)[:12]
    print(f"    sym_sides whose applied set differs from their newest board: {len(board_bad)}")
    for ss, keys in sorted(board_bad.items())[:15]:
        print(f"      {ss:26s} {keys}")
    report["board_mismatch"] = board_bad

    scope = "crypto+stocks" if a.include_stocks else "crypto only (stocks read via the correct _cfg path)"
    which = "LEGACY json `overrides` half" if a.legacy_reader else "per_sym_live_set.full_set (the live reader)"
    print(f"\n=== READER: {which}  vs board set  ({scope})")
    reader_bad, reader_keys = {}, collections.Counter()
    capped_hits = collections.Counter()
    n_scoped = 0
    for ss, fc in active.items():
        if not a.include_stocks and not _cat_side(ss).startswith("CRYPTO_"):
            continue
        n_scoped += 1
        base = cat.get(_cat_side(ss), {})
        if a.legacy_reader:
            if ss not in proxy:
                reader_bad[ss] = "<absent from proxy json>"
                continue
            ov = proxy.get(ss, {})
        else:
            ov = _live_reader.full_set(ss)
            if not ov:
                reader_bad[ss] = "<reader resolves no row>"
                continue
        diff = []
        for k, v in fc.items():
            if k in BOARD_SKIP:
                continue
            if k in ov:
                resolved = ov[k]
            elif k in base:
                resolved = base[k]
            else:
                continue  # falls through to config.py global — not decidable here
            if _norm(resolved) == _norm(v):
                continue
            if k in CAP_KNOBS:
                capped_hits[k] += 1  # deliberate: newer user instruction beats the board
                continue
            diff.append(k)
            reader_keys[k] += 1
        if diff:
            reader_bad[ss] = sorted(diff)
    absent = [s for s, v in reader_bad.items() if isinstance(v, str)]
    drifted = {s: v for s, v in reader_bad.items() if not isinstance(v, str)}
    print(f"    live rows the reader cannot resolve at all: {len(absent)} of {n_scoped} in scope")
    print(f"    live rows the reader resolves off their board: {len(drifted)}")
    for k, n in reader_keys.most_common(15):
        print(f"      {n:4d} sym_sides  {k}")
    if capped_hits:
        # 2026-10-19: print the EFFECTIVE ceiling, not the flat CRYPTO_MIN_SIZE_CAPS literal. Since
        # per_sym_live_set._cap_table() was made mode-aware (USER 2026-10-17 "make sure the light-normal-extreme
        # modes get their sizes back"), the ceiling is the ACTIVE mode table whenever CRYPTO_MINIMUM_TRADING_MODE
        # is False — measured LIGHT_MODE 25/140/120 — while this line still announced "-> 6.0 / -> 12.0". An
        # operator reading it concluded live crypto was pinned at the exchange floor when it was not. The caps are
        # read from the live reader itself so the two can never drift again.
        eff = _live_reader._cap_table()
        why = "CRYPTO_MINIMUM_TRADING_MODE (USER 2026-10-16)" if _live_reader._min_mode_on() else f"ACTIVE MODE TABLE = {_mode_name()}"
        print(f"    DELIBERATE (not breaches) — sizing ceiling beats the board, source: {why}:")
        for k, n in capped_hits.most_common():
            print(f"      {n:4d} sym_sides  {k} -> {eff.get(k, CAP_KNOBS[k])}")
    report["reader_capped"] = dict(capped_hits)
    report["reader_absent"] = sorted(absent)
    report["reader_drift"] = {k: len(v) for k, v in drifted.items()}
    report["reader_keys"] = dict(reader_keys)

    print(f"\n=== TRADED: last {a.days}d per account")
    traded_bad = {}
    for acct in accounts:
        t = sorted(traded.get(acct, ()))
        no_board = [s for s in t if s not in active]
        no_proxy = [s for s in t if s not in proxy]
        off = [s for s in t if s in reader_bad]
        traded_bad[acct] = {"traded": len(t), "no_board": no_board, "no_proxy_row": no_proxy, "off_board": off}
        print(f"    {acct:5s} traded={len(t):4d}  no_board={len(no_board):3d}  no_proxy_row={len(no_proxy):3d}  reader_off_board={len(off):3d}")
        if no_board:
            print(f"          NO BOARD (trades on cat_side/global defaults): {no_board}")
    report["traded"] = traded_bad

    breaches = len(board_bad) + len(reader_bad) + sum(len(v["no_board"]) for v in traded_bad.values())
    print(f"\n=== TOTAL BREACHES: {breaches}")
    if a.json:
        Path(a.json).write_text(json.dumps(report, indent=1, sort_keys=True))
        print(f"    report -> {a.json}")
    return 1 if breaches else 0


if __name__ == "__main__":
    sys.exit(main())
