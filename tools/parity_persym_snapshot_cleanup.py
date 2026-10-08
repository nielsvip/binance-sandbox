#!/usr/bin/env python3
"""Parity lane B 2026-10-06 — list (and with --apply, remove) STALE per-sym DEFAULTS-SNAPSHOT values for the switches newly
wired into live crypto, so the current cat_side baseline (data/per_sym_settings.json / per_sym_store kv_json, built from the
TEMPLATE_* bolds by v15_vector_delta / build_cat_side_defaults_4) is what live resolves.

Why: ez_manage._psym_get returns per_sym_store full_config FIRST. full_config = defaults snapshot taken at promotion time +
overrides. A snapshot value that is NOT a promotion record silently overrides the cat_side baseline forever.

Decision rule per (sym_side, switch) — printed in the report so it can be audited:
  PROMOTION  key is in that row's overrides (SQLite overrides_json or JSON 'overrides')            -> KEEP (never touched)
  STALE      key not in overrides AND full_config[key] != current baseline                           -> CLEAN (remove key from
             (baseline = cat_side default for the row's cat_side, else the global config.py value)      full_config so lookups fall
             'snap==defaults_snapshot' column says whether the value equals the row's own                through to cat_side/global)
             defaults_snapshot (i.e. it was captured as a default, not chosen)
  OK         key not in overrides AND full_config value already equals the baseline (or key absent)  -> nothing to do

DRY-RUN by default (read-only). --apply writes: backs up the SQLite DB + JSON first (backups/before_persym_snapshot_cleanup_*),
then removes the STALE keys from SQLite full_config_json and from JSON full_config. Never edits overrides, cat_side defaults,
defaults_snapshot, or any non-listed key. Do not run --apply on a live host without the deploy step's sign-off.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_SWITCHES = (
    "RSI_ENTRY_VETO_ENABLED", "MOM3_FILTER_TF", "MOMENTUM_BREAKOUT_FILTER_TF", "DC_BREAK_FILTER_TF", "BB_BOUNCE_ENTRY_TF",
    "BREAKOUT_RETEST_ARMED_ENABLED", "BREAKOUT_RETEST_FILTER_TF", "HTF_DIRECTION_GATE_ENABLED", "HTF_GATE_APPLY_TO_OPEN",
    "HTF_GATE_MIN_CONFIRMATIONS", "HTF_GATE_D_MANDATORY", "HTF_GATE_SIGNALS_SMA200D", "OI_CONFIRM_ENABLED",
    "OI_CONFIRM_MIN_CHANGE_PCT", "OI_CONFIRM_MIN_PRICE_PCT", "DC_PRIOR_BAR_CHANNEL", "DAYTRADE_DC_STOP_TF", "TECHNICAL_DC_STOP_TF",
    "EXIT_TOP_FADE_FILTER_TF", "CANDLE_PATTERN_STOPS_FILTER_TF", "MULTI_TF_EXIT_ENABLED", "WT_DIV_EXIT_ENABLED",
    "WT_ACCEL_EXIT_ENABLED", "WT_15M_LH_WAIT_EXIT_ENABLED", "WT_15M_BOUNCE_OPEN_ENABLED", "DC_EDGE_SIZING_ENABLED",
    "DC_EDGE_SIZING_MIN_MULT", "DC_EDGE_SIZING_MAX_MULT", "STDEV_SLOPE_SIZING_ENABLED", "SLOPE_SIZING_LIVE_TWIN_ENABLED",
    "EXIT_VELOCITY_WT_ENABLED", "CRYPTO_REENTRY_PATHWAYS_ENABLED", "HAIKU_WINNER_AUGMENT_ENABLED", "HAIKU_WINNER_ENABLED",
    "KEY_LEVEL_CRASH_EXIT_ENABLED", "KEY_LEVEL_CRASH_ENABLED",
    "HARDCODED_RALLY_REENTRY_ENABLED", "HTF_WT_CHURN_REENTRY_ENABLED", "TARGET_DC_IMMEDIATE_REENTRY_ENABLED", "REENTRY_MANDATORY",
    "REENTRY_BLANKET_FIRE_ENABLED",
)
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")


def _norm(v):
    if isinstance(v, str):
        s = v.strip()
        if s.lower() in ("true", "false"):
            return s.lower() == "true"
        try:
            return float(s)
        except ValueError:
            return s
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return v


def _same(a, b) -> bool:
    return _norm(a) == _norm(b)


def _baseline(cat_side: str, key: str, cfg):
    import cat_side_defaults as csd
    miss = object()
    v = csd.get(key, cat_side, miss)
    if v is not miss:
        return v, "cat_side"
    return getattr(cfg, key, None), "global"


def _is_crypto(sym_side: str) -> bool:
    return sym_side.rsplit("_", 1)[0].upper().endswith(CRYPTO_SUFFIXES)


def scan(switches, include_stocks: bool = False):
    import config as config_mod
    import per_sym_store as pss
    cfg = config_mod.Config()
    rows = []
    con = sqlite3.connect(str(pss.DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        for r in con.execute("SELECT sym_side, cat_side, overrides_json, defaults_snapshot_json, full_config_json FROM per_sym_active"):
            ss = r["sym_side"]
            if not include_stocks and not _is_crypto(ss):
                continue
            ov = json.loads(r["overrides_json"] or "{}")
            fc = json.loads(pss._maybe_decompress(r["full_config_json"]) or "{}") if r["full_config_json"] else {}
            ds = json.loads(pss._maybe_decompress(r["defaults_snapshot_json"]) or "{}") if r["defaults_snapshot_json"] else {}
            cat = r["cat_side"] or pss._cat_side_of(ss.rsplit("_", 1)[0], ss.rsplit("_", 1)[-1])
            for k in switches:
                if k in ov:
                    rows.append(dict(store="sqlite", sym_side=ss, cat_side=cat, key=k, verdict="PROMOTION", snapshot=fc.get(k), baseline=None, baseline_src="", snap_eq_defaults_snapshot=""))
                    continue
                if k not in fc:
                    continue
                base, src = _baseline(cat, k, cfg)
                verdict = "OK" if _same(fc[k], base) else "STALE"
                rows.append(dict(store="sqlite", sym_side=ss, cat_side=cat, key=k, verdict=verdict, snapshot=fc[k], baseline=base, baseline_src=src, snap_eq_defaults_snapshot=(k in ds and _same(ds[k], fc[k]))))
    finally:
        con.close()
    jpath = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
    if jpath.exists():
        raw = json.loads(jpath.read_text())
        for ss, e in raw.items():
            if ss == "_meta" or not isinstance(e, dict):
                continue
            if not include_stocks and not _is_crypto(ss):
                continue
            ov = e.get("overrides") or {}
            fc = e.get("full_config") or {}
            ds = e.get("defaults_snapshot") or {}
            cat = e.get("cat_side") or pss._cat_side_of(ss.rsplit("_", 1)[0], ss.rsplit("_", 1)[-1])
            for k in switches:
                if k in ov:
                    rows.append(dict(store="json", sym_side=ss, cat_side=cat, key=k, verdict="PROMOTION", snapshot=fc.get(k), baseline=None, baseline_src="", snap_eq_defaults_snapshot=""))
                    continue
                if k not in fc:
                    continue
                base, src = _baseline(cat, k, cfg)
                verdict = "OK" if _same(fc[k], base) else "STALE"
                rows.append(dict(store="json", sym_side=ss, cat_side=cat, key=k, verdict=verdict, snapshot=fc[k], baseline=base, baseline_src=src, snap_eq_defaults_snapshot=(k in ds and _same(ds[k], fc[k]))))
    return rows


def apply(rows):
    import per_sym_store as pss
    ts = time.strftime("%Y%m%d%H%M")
    bdir = ROOT / "backups"
    bdir.mkdir(exist_ok=True)
    shutil.copy2(pss.DB_PATH, bdir / f"before_persym_snapshot_cleanup_{ts}_per_sym_store.db")
    jpath = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
    if jpath.exists():
        shutil.copy2(jpath, bdir / f"before_persym_snapshot_cleanup_{ts}_per_sym_active_config.json")
    stale_sql, stale_json = {}, {}
    for r in rows:
        if r["verdict"] != "STALE":
            continue
        (stale_sql if r["store"] == "sqlite" else stale_json).setdefault(r["sym_side"], set()).add(r["key"])
    con = sqlite3.connect(str(pss.DB_PATH), timeout=30.0)
    try:
        for ss, keys in stale_sql.items():
            row = con.execute("SELECT full_config_json FROM per_sym_active WHERE sym_side=?", (ss,)).fetchone()
            if not row or not row[0]:
                continue
            fc = json.loads(pss._maybe_decompress(row[0]) or "{}")
            for k in keys:
                fc.pop(k, None)
            con.execute("UPDATE per_sym_active SET full_config_json=? WHERE sym_side=?", (pss._maybe_compress(json.dumps(fc, sort_keys=True)), ss))
        con.commit()
    finally:
        con.close()
    if stale_json and jpath.exists():
        raw = json.loads(jpath.read_text())
        for ss, keys in stale_json.items():
            fc = (raw.get(ss) or {}).get("full_config")
            if isinstance(fc, dict):
                for k in keys:
                    fc.pop(k, None)
        tmp = jpath.with_suffix(".json.tmp_cleanup")
        tmp.write_text(json.dumps(raw))
        json.loads(tmp.read_text())
        tmp.replace(jpath)
    return sum(len(v) for v in stale_sql.values()), sum(len(v) for v in stale_json.values())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="remove STALE snapshot keys (backs up first). Default: dry-run")
    ap.add_argument("--switches", default="", help="comma list (default: lane-B wired switches)")
    ap.add_argument("--include-stocks", action="store_true")
    ap.add_argument("--csv", default="", help="write the full row report to this CSV path")
    a = ap.parse_args(argv)
    switches = tuple(s.strip() for s in a.switches.split(",") if s.strip()) or DEFAULT_SWITCHES
    rows = scan(switches, a.include_stocks)
    from collections import Counter
    c = Counter((r["store"], r["key"], r["cat_side"], r["verdict"], str(r["snapshot"]), str(r["baseline"]), r["baseline_src"], str(r["snap_eq_defaults_snapshot"])) for r in rows if r["verdict"] != "OK")
    print(f"{'DRY-RUN' if not a.apply else 'APPLY'} — {len(rows)} (sym_side,switch) rows checked; non-OK groups:")
    print("store | switch | cat_side | verdict | snapshot -> baseline (src) | snap==defaults_snapshot | count")
    for (st, k, cat, v, snap, base, src, eqds), n in sorted(c.items()):
        print(f"{st} | {k} | {cat} | {v} | {snap} -> {base} ({src}) | {eqds} | {n}")
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["store"])
            w.writeheader()
            w.writerows(rows)
    if a.apply:
        n_sql, n_json = apply(rows)
        print(f"APPLIED: removed {n_sql} SQLite + {n_json} JSON stale snapshot keys (backups in backups/before_persym_snapshot_cleanup_*)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
