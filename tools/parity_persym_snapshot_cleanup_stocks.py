#!/usr/bin/env python3
"""Parity lane C 2026-10-06 — STOCKS twin of tools/parity_persym_snapshot_cleanup.py (lane B rule, reused verbatim).

Lists (and with --apply removes) STALE per-sym DEFAULTS-SNAPSHOT values for stock sym_sides so the current cat_side baseline
(data/per_sym_settings.json STOCKS_LONG/STOCKS_SHORT, else config_tradier global) is what tradier_manage._cfg resolves.

Decision rule per (store, sym_side, switch) — identical to lane B:
  PROMOTION  key in that row's overrides (SQLite overrides_json / active_config 'overrides')  -> KEEP (never touched)
  STALE      key not in overrides AND full_config[key] != baseline                          -> CLEAN (remove from full_config)
  OK         key not in overrides AND full_config value == baseline (or key absent)          -> nothing to do

Stores scanned: data/hourly_reconfig/per_sym_store.db stock rows (cat_side STOCKS_*), data/hourly_reconfig/{trb,trc}/active_config.json
('full_config' section; tradier_manage reads only 'overrides' from these JSONs, so their full_config is listed for hygiene only).
DRY-RUN by default (read-only). --apply backs up DB + JSONs to backups/before_persym_snapshot_cleanup_stocks_<ts>_* first.
Never edits overrides, defaults_snapshot, cat_side defaults or unlisted keys. Do not run --apply without deploy sign-off.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from parity_persym_snapshot_cleanup import _same  # noqa: E402  (lane B rule — single definition)

DEFAULT_SWITCHES = (
    # lane C entry FILTER_TFs + sources + exit-confirm + augment + newborn + masters
    "MOM3_FILTER_TF", "MOM3_LONG_THRESHOLD", "MOM3_SHORT_THRESHOLD", "MOMENTUM_BREAKOUT_FILTER_TF", "BB_PULLBACK_GATE_FILTER_TF",
    "BB_PULLBACK_GATE_ENABLED", "BB_PULLBACK_GATE_LONG_MAX", "BB_PULLBACK_GATE_SHORT_MIN", "BB_RECOVERY_ENTRY_FILTER_TF",
    "BB_RECOVERY_FILTER_TF", "DC_BREAK_FILTER_TF", "BT_WT_CROSS_LADDER_FILTER_TF", "BAR_PATTERNS_FILTER_TF", "BREAKOUT_RETEST_FILTER_TF",
    "BREAKOUT_RETEST_ARMED_ENABLED", "BB_BOUNCE_ENTRY_TF", "EMA50_15M_ENTRY_FILTER_ENABLED", "EMA50_15M_ENTRY_FILTER_PCT",
    "WT_15M_BOUNCE_OPEN_ENABLED", "WT_15M_BOUNCE_BB_MIN", "WT_15M_BOUNCE_BB_MAX", "WT_15M_BOUNCE_REQUIRE_BOTH_HTF",
    "WT_15M_BOUNCE_FILTER_HL_ENABLED", "WT_15M_BOUNCE_FILTER_HH_ENABLED", "WT_15M_BOUNCE_FILTER_MODE", "WT_15M_BOUNCE_VOLUME_FILTER_ENABLED",
    "WT_15M_BOUNCE_VOLUME_MODE", "WT_15M_BOUNCE_VOLUME_THRESHOLD", "WT_15M_BOUNCE_LOW_1H_GT_PREV", "WT_15M_BOUNCE_HIGH_1H_GT_PREV",
    "WT_15M_BOUNCE_REL_VOL_GT_1", "EXIT_TOP_FADE_FILTER_TF", "CANDLE_PATTERN_STOPS_FILTER_TF", "FAST_RISER_FILTER_TF",
    "NEWBORN_LOSS_KILL_FILTER_TF", "NEWBORN_LOSS_KILL_VEL_TF", "NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST", "EXIT_VELOCITY_WT_ENABLED",
    "STOCKS_WTDC_SCORER_EXIT_ENABLED", "MULTI_TF_EXIT_ENABLED_TRADIER", "KG_STOCKS_HARD_VETO_ENABLED", "DELTA_GATE_OPEN",
    # order-path params reached by the _cfg_auto side fix (only via promotions now, listed for hygiene)
    "AUGMENTATION_COOLDOWN_SECONDS", "BB_RECOVERY_EXIT_ENABLED_TRADIER", "COUNTER_TREND_SMA200_BYPASS_ENABLED", "EXECUTE_NOW_MAX_MARK_AGE_S",
    "GOLDEN_RULE_HTF_MIN_TFS", "GOLDEN_RULE_MIN_IND", "HTF_TREND_VETO_BYPASS_ENABLED", "LIVE_VEC_EMERGENCY_BRAKE_ENABLED",
    "MTF_FILTER_STRONG_BUY_QUICK_BYPASS", "NEW_POSITION_MAX_LOSS_THRESHOLD", "RECENT_REDUCTION_GUARD_WINDOW_S", "TRADES_PER_SYM_PER_DAY_MAX",
    "UNIVERSAL_NOLOSS_GATE",
)


def _stock_cat(sym_side: str, cat: str | None) -> str | None:
    if cat and str(cat).startswith("STOCKS_"):
        return cat
    side = sym_side.rsplit("_", 1)[-1].upper()
    if side not in ("LONG", "SHORT"):
        return None
    try:
        import cat_side_defaults as csd
        c = csd.cat_side_of(sym_side.rsplit("_", 1)[0], side, venue="tradier")
        return c if c.startswith("STOCKS_") else None
    except Exception:
        return None


def _baseline(cat_side: str, key: str, cfg):
    import cat_side_defaults as csd
    miss = object()
    v = csd.get(key, cat_side, miss)
    if v is not miss:
        return v, "cat_side"
    return getattr(cfg, key, None), "config_tradier"


def _classify(store, ss, cat, ov, fc, ds, switches, cfg, rows):
    for k in switches:
        if k in ov:
            rows.append(dict(store=store, sym_side=ss, cat_side=cat, key=k, verdict="PROMOTION", snapshot=fc.get(k), override=ov[k], baseline=None, baseline_src="", snap_eq_defaults_snapshot=""))
            continue
        if k not in fc:
            continue
        base, src = _baseline(cat, k, cfg)
        verdict = "OK" if _same(fc[k], base) else "STALE"
        rows.append(dict(store=store, sym_side=ss, cat_side=cat, key=k, verdict=verdict, snapshot=fc[k], override="", baseline=base, baseline_src=src, snap_eq_defaults_snapshot=(k in ds and _same(ds[k], fc[k]))))


def scan(switches):
    import config_tradier
    import per_sym_store as pss
    cfg = config_tradier.TradierConfig()
    rows = []
    con = sqlite3.connect(f"file:{pss.DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        for r in con.execute("SELECT sym_side, cat_side, overrides_json, defaults_snapshot_json, full_config_json FROM per_sym_active"):
            ss = r["sym_side"]
            cat = _stock_cat(ss, r["cat_side"])
            if not cat:
                continue
            ov = json.loads(r["overrides_json"] or "{}")
            fc = json.loads(pss._maybe_decompress(r["full_config_json"]) or "{}") if r["full_config_json"] else {}
            ds = json.loads(pss._maybe_decompress(r["defaults_snapshot_json"]) or "{}") if r["defaults_snapshot_json"] else {}
            _classify("sqlite", ss, cat, ov, fc, ds, switches, cfg, rows)
    finally:
        con.close()
    for acct in ("trb", "trc"):
        jpath = ROOT / "data" / "hourly_reconfig" / acct / "active_config.json"
        if not jpath.exists():
            continue
        raw = json.loads(jpath.read_text())
        for ss, e in raw.items():
            if not isinstance(e, dict):
                continue
            cat = _stock_cat(ss, e.get("cat_side"))
            if not cat:
                continue
            _classify(f"active_config_{acct}", ss, cat, e.get("overrides") or {}, e.get("full_config") or {}, e.get("defaults_snapshot") or {}, switches, cfg, rows)
    return rows


def apply(rows):
    import per_sym_store as pss
    ts = time.strftime("%Y%m%d%H%M")
    bdir = ROOT / "backups"
    bdir.mkdir(exist_ok=True)
    shutil.copy2(pss.DB_PATH, bdir / f"before_persym_snapshot_cleanup_stocks_{ts}_per_sym_store.db")
    stale = {}
    for r in rows:
        if r["verdict"] == "STALE":
            stale.setdefault(r["store"], {}).setdefault(r["sym_side"], set()).add(r["key"])
    n = Counter()
    con = sqlite3.connect(str(pss.DB_PATH), timeout=30.0)
    try:
        for ss, keys in (stale.get("sqlite") or {}).items():
            row = con.execute("SELECT full_config_json FROM per_sym_active WHERE sym_side=?", (ss,)).fetchone()
            if not row or not row[0]:
                continue
            fc = json.loads(pss._maybe_decompress(row[0]) or "{}")
            for k in keys:
                if fc.pop(k, None) is not None:
                    n["sqlite"] += 1
            con.execute("UPDATE per_sym_active SET full_config_json=? WHERE sym_side=?", (pss._maybe_compress(json.dumps(fc, sort_keys=True)), ss))
        con.commit()
    finally:
        con.close()
    for acct in ("trb", "trc"):
        st = stale.get(f"active_config_{acct}") or {}
        jpath = ROOT / "data" / "hourly_reconfig" / acct / "active_config.json"
        if not st or not jpath.exists():
            continue
        shutil.copy2(jpath, bdir / f"before_persym_snapshot_cleanup_stocks_{ts}_{acct}_active_config.json")
        raw = json.loads(jpath.read_text())
        for ss, keys in st.items():
            fc = (raw.get(ss) or {}).get("full_config")
            if isinstance(fc, dict):
                for k in keys:
                    if fc.pop(k, None) is not None:
                        n[acct] += 1
        tmp = jpath.with_suffix(".json.tmp_cleanup_stocks")
        tmp.write_text(json.dumps(raw))
        json.loads(tmp.read_text())
        tmp.replace(jpath)
    return dict(n)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="remove STALE snapshot keys (backs up first). Default: dry-run")
    ap.add_argument("--switches", default="", help="comma list (default: lane-C stocks switches)")
    ap.add_argument("--csv", default="", help="write the full row report to this CSV path")
    a = ap.parse_args(argv)
    switches = tuple(s.strip() for s in a.switches.split(",") if s.strip()) or DEFAULT_SWITCHES
    rows = scan(switches)
    c = Counter((r["store"], r["key"], r["cat_side"], r["verdict"], str(r["snapshot"]), str(r["override"]), str(r["baseline"]), r["baseline_src"]) for r in rows if r["verdict"] != "OK")
    print(f"{'DRY-RUN' if not a.apply else 'APPLY'} — {len(rows)} (store,sym_side,switch) rows checked; non-OK groups:")
    print("store | switch | cat_side | verdict | snapshot | override | baseline (src) | count")
    for (st, k, cat, v, snap, ovv, base, src), cnt in sorted(c.items()):
        print(f"{st} | {k} | {cat} | {v} | {snap} | {ovv} | {base} ({src}) | {cnt}")
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["store"])
            w.writeheader()
            w.writerows(rows)
    if a.apply:
        print(f"APPLIED: removed {apply(rows)} stale snapshot keys (backups in backups/before_persym_snapshot_cleanup_stocks_*)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
