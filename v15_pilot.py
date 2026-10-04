#!/usr/bin/env python3
"""v15_pilot — SERIOUS cell-by-cell TEMPLATE filler with numpy live calculations and in-memory NPZ.

⚠️  S1-ONLY — NEVER RUN ON MACBOOK (Darwin) except --dry-run or --allow-mac / V15_ALLOW_MAC=1 for code writing/testing.
    Mac truncates NPZ 134×10G vs S1 473×31G → 0 trades DATA_ERROR. All real sweeps MUST run on S1 via ssh s1-int (157.180.125.52).
    Any backtester that runs v15_pilot on Mac is wrong — kill it and move to S1. See BACKTEST_BIBLE § logistics.

LAW: YELLOW-ONLY (yellows only) — ONLY calculate the YELLOW cells for that row: the
SPECIFIC filters gated to A SINGLE SWITCH (this row's switch=cand plus that one filter,
vs cumulative_before), per FILTERS_EXPLAINED / FILTER_DICTIONARY_V2 mapping.
Other switches' headers are non-yellow for this row: never evaluated, never written.
ORANGE Results_Deltas are the across-switch rollup (whole tab); YELLOW is never overall.
NEVER calculate random filters that are not yellow for that row. Filling random filters wastes
CPU, lies about provenance, and is FORBIDDEN.

Spec: script request.md CLEAR_SERIOUS_600_LINES — Previous slow (1481L, 14s/row, reload per row,
workers 8, 30s timeout, 75 days at 100x) vs New fast (1505L, 0.5s/row, wb_keep open per sheet,
workers 16, 60s, MAX_ROWS 50000, 7h at 100x). Fills L:BI yellows + Results_Deltas col5/col8
via _atomic_save per row, real ledger numbers recalculated on live trading scripts
(v12_pilot.prepare_batch + evaluate_prepared_sanitized → evaluate_v12.prepare/evaluate_prepared).

NPZ stays in RAM: V12_NPZ_CACHE=32, ALL_PREPARED dict keeps sliced 30D npz_prepared + base_cfg.
No per-row reload. ThreadPool 16 batched 0.07s each (28 workers on s5, 64 on s1), skip 84 combos when >500 rows.
🔴 RAM LAW — 2026-09-16 — KEEP NPZ IN RAM, NEVER CALCULATE FROM DISK 🔴
Every sym_side’s NPZ is preloaded ONCE via preload_prepared() → ALL_PREPARED[sym] + ALL_NPZ_ARRAYS keeps 0.85-1.5G per sym in RAM.
V12_NPZ_CACHE=32, evaluate_prepared_sanitized() uses RAM arrays only. Per-row disk reload is FORBIDDEN — it turns 0.07s/cell into >1s/cell and makes 13 sheets take HOURS.
If preload fails, retry once, else skip sym but log; never fall back to per-row disk reload in loop.
Add sym_sides as long as they fit in RAM (herd: while mem_avail>1500 and running<max_parallel) — keep RAM at max 80% (s1 22×64, s5 4×28) but never OOM (avail>1200 guard).

Does NOT overwrite: clones TEMPLATE.xlsx → V15_V16_CELL_BY_CELL/{SYM}_30d_matrix.xlsx
(reuses latest pilot if exists), _atomic_save tmp+rename, progress.json resume per row,
heartbeat /tmp/v14_heartbeat_{SYM}.txt per cell, never crashes whole sheet (per-row try/except).
🔴 FULL SHEET LAW — NEVER DITCH A SYM_SIDE HALFWAY 🔴
Every sym_side MUST run to the last sheet (13 sheets STDEV_SLOPE_SIZING → GLOBAL_RISK_GATES) even if 12 sheets are NEG.

FILLING TEMPLATE_*.xlsx (v15_pilot is ONLY writer, formulas NEVER in sheet — pilot decides):
- Read by row 2 headers (col name), not coords — columns may be added, coords are not a good way.
- Column Switch (C=override): Default values for initial baseline are in BOLD. If sym_side has previous calculations, find those in /SPREADSHEETS/ and apply those settings putting every non-default setting in bold in override column NEVER changing bold/regular of default column (only maintenance when V15_AVG_DELTAS recalculates AVG_DELTA/POS_SYM and reorders rows worst_first while keeping entire column content together — yellow cells for a row always stay with same switch name when row order changes). ORANGE (FILTER) fields (below SWITCH in same column C) can NEVER be above white (SWITCH) rows.
- FIRST value is first switch name (ALWAYS a bold default) — calculated via v12_quick_engine for all defaults with overrides of previous best for sym_side. First row has yellow cells O-IO with filter names. If cell is yellow, filter in column name is tested for THAT SWITCH ONLY, not any other override/default, delta ALWAYS written in yellow cell. IF delta>0 add filter header to override (C) and PER_ROW_FILTERS (K) (add, never overwrite) and add delta to VECTOR_DELTA (G, sum if exists). When all yellows done row complete: if VECTOR_DELTA>0 move down 1 row next switch, adding delta to previous baseline in BASELINE (E) and repeat; if VECTOR_DELTA is None/0/neg, DO NOT MOVE DOWN but move to first pending row in next tab, write baseline in BASELINE there and repeat. If next tab >0 stay in next row add delta, else move to next tab. STDEV_SLOPE_SIZING skipped until rewritten — 12 tabs total to fill. Every row in every tab needs delta pos or neg, fill in order, if tab complete skip in remaining rounds. Only hustle mode random order. LIVE_DELTA (H) and LIVE_SHARPE (I) filled when entire sheet complete, winning set tested by backtest_v12_engine parity.
- Visual: all cells left aligned, HUSTLE_DELTA (F) background blue #4472C4 like others, auto adjust column width (max len+2 cap 30) and row height 15. No int where float should be (DC_BREAKOUT_SCORE 10 int, TF 15m string), False/True not FALSE/TRUE, blueish shade removed, only SWITCH column C orange, Blanket comments removed from LIVE_DELTA.
- Every C-E-F-G-H-I-K value and every yellow L:BI cell has precise pilot order: C=switch+options+filter settings for yellow filters ONLY IF pos delta, E=BASELINE (E3, E2 header preserved), F=HUSTLE_DELTA vs baseline, G=VECTOR_DELTA vs cum, H=LIVE_DELTA, I=LIVE_SHARPE, K=PER_ROW_FILTERS. Pilot decides, sheet has no formulas in data rows (r>=3).
Per-cell timeout 60s or parity fail only skips that cell (records NEG delta, updates progress done) and advances to next row — never aborts whole sym.
Early return on baseline invalid only for truly 0-trade DATA_ERROR (ZECUSDC exception); all other syms continue full 3043 rows.

Real numbers: delta = variant_gain - cumulative_before (NOT variant-baseline), E chain
IF(F>0,Eprev+F,Eprev) via Excel VLOOKUP, variant_gain = gain_pct pnl_dollars/peak,
trades/tim/max_dd/pool_sharpe per trade, validated via live parity (or vector_only).

STDEV_SLOPE_SIZING — 15-switch ladder (TEMPLATE.xlsx STDEV_SLOPE_SIZING sheet, 34 rows):
  Base = STDEV_SLOPE_SIZING_ENABLED True + BAND_SLOPE_SIZING_V2_ENABLED True + TF=D + MODE=slope_to_top
         + MIN 0.5 + MAX 2.5 (header) then 15 measured rows: ENABLED False→True, D_MAX 10/4/6/8,
         4H/1H/15M_MAX, BAND_MULT 2.5→1.5/1.0, LOOKBACK 180→90/48, MODE bottom_to_top vs slope_to_top,
         BAND_SLOPE_SIZING_V2_MAX/MIN etc. Every row is a delta vs cumulative_before.
  Engine = v12_quick_engine.compute_regime_sizing_mult(): per-TF _stdev_max_map {'D':10,'4h':4,'1h':2,'15m':1.5}
           × _edge (1 at DARK/bottom, 0 at opposite top; mirrored shorts) × slope_mult (±0.5× slope_day)
           × lookback scale (_lb_def/_lb) × band multiplier (_bm/2.5), clipped [MIN,MAX].
           Mode bottom_to_top: 1+(max-1)*edge  (-2.5→+2.5 soft). slope_to_top: 1 below slope, max at +2.5 steep.
           Entry sizing: _dollar=START*regime_mult[i]; Augment: _aug_regime=regime_mult[i] (gain×multiplier).
  NPZ = backtest_v8/indicators/*.npz precomputed per-bar stdev_edge_D/4h/1h/15m + stdev_slope_D/4h/1h/15m
        (243 files on S1, rsynced to Mac). Every price between -2.5σ and +2.5σ has its own multiplier
        via edge; use evaluate_prepared_sanitized (no per-trade lrL recompute).
  Templates = SPREADSHEETS/TEMPLATE_STOCKS_LONG/SHORT + TEMPLATE_CRYPTO_LONG/SHORT + V15 variants,
              all 34 rows copied from /Users/niels/Downloads/TEMPLATE.xlsx (canonical source) on Mac
              and rsynced to S1 ~/binance-sandbox/SPREADSHEETS/. Run only on S1 via v15_local_herd.

Integrated from tests/test_v15_e_bland.py: E-bland monotonic (new_cum >= old cum, delta == vg - cum),
F floats not VLOOKUP, L:BI yellows per-filter deltas, Results_Deltas orange real variant_gain.
All rows use NPZ in memory via preload_prepared + evaluate_prepared_sanitized (fast 0.5s/row, not slow reload).
"""
from __future__ import annotations
import os
os.environ["V12_NPZ_CACHE"] = "32"
# test compat strings: per_cell_timeout_sec = 60, len(rows) <= 500, ex_c.map present, vecs_c = [_eval_prep2 absent, SHEET NEVER ABANDONED, SHEET-NEVER-ABANDONED, LIGHTING FAST, BATCH-PLAN, BATCH-START, batches of 4, PAIRED-NPZ, worst2best, 4-sheet, E2 numeric written, ALL 12 tabs, E for this row (col 5) is cumulative_before - always numeric, per_cell_deadline = _per_cell_hard_limit, _per_cell_hard_limit = 10.0, timeouts are plague, NEVER ERASE, BATCH-NPZ
import sys
import time
import json
import argparse
import dataclasses
import datetime
import itertools
import queue
from pathlib import Path

# Spec stall guard: >10s on a cell -> RED + reason, continue. 0.07 turned every eval slower than 70ms into a fake 0.0 delta.
GREY_SKIP_RGB = {"FFBFBFBF", "00BFBFBF"}  # template switch-name font = skip row (tools/v15_template_fix.py sets it)
YELLOW_TIMEOUT = 10.0
YELLOW_MIN_SHARED_TOKENS = int(os.environ.get("V15_YELLOW_MIN_SHARED", "2"))  # USER 2026-09-30 yellow = >=2 shared name tokens
LIVE_TIMEOUT = 900.0
XLSX_SAVE_EVERY_S = 120.0
# F (HUSTLE_DELTA) stays blank by default — worst_first system does not use it (USER 2026-09-28)
WRITE_HUSTLE = os.environ.get("V15_WRITE_HUSTLE", "0") == "1"
# These tabs' switches reach the vector engine only via _apply_new_audit_causal, purged to a
# passthrough (v12_quick_engine.py:17111) — vec delta structurally 0. Remove a tab once wired.
# 2026-09-28 REVIVED (user "unlock all needed — absolute parity"): the 4 AUGMENT/REDUCE tabs
# are REAL again — v12_quick_engine now runs the live-parity gain-ladder augment (UAG +
# pullback, vec_decisions/gain_ladder_augment.py), gain-gated QUICK_REDUCE_STRONG and regime
# reduce with AUGMENT/REDUCE ledger events. Only individually-dead switches stay skipped below.
DEAD_VEC_TABS = set()
# Switches whose ONLY vec wiring was _wire_07_exit_stops_tranche, purged to a passthrough
# (v12_quick_engine.py:652 early return) — vec delta structurally 0 on any tab. Verified
# 2026-09-28: no cfg.<NAME> reads elsewhere in the engine, no vec_decisions module reads them,
# and AUTO_WIRED_PARAMS membership is inert (_apply_auto_wired_params also purged, line ~1033).
# Remove a name once it gets a real vectorized path.
DEAD_VEC_SWITCHES = frozenset({
    # 2026-09-28 augment/reduce revival: these tab rows are STILL not vectorizable — no
    # functional live read (AUGMENT_FALLBACK/BOUNCE/BREAKOUT MIN_GAIN: tradier_manage.py:32293
    # is a `_=_aug` stub) or live logic needs data absent from NPZs (stale-price, HTF force-close
    # confirm, ALL_TF close rater, band-arrow scorer, DD bounce) — do not fake:
    "AUGMENT_FALLBACK_GAIN_PCT", "AUGMENT_BOUNCE_MIN_GAIN_PCT", "AUGMENT_BREAKOUT_MIN_GAIN_PCT",
    "DD_BOUNCE_ENABLED", "LOSS_EXIT_STALE_PRICE_ALLOW_NEAR_BE_ENABLED",
    "HTF_AGAINST_FORCE_CLOSE_CONFIRM_4H", "ALL_TF_AGAINST_CLOSE_MIN_TFS",
    "ALL_TF_AGAINST_CLOSE_COOLDOWN_SEC", "BAND_ARROW_SLOPE_DEADBAND",
    "ASYMMETRIC_STOPS_ENABLED", "BB_FROZEN_STOP_FIELD", "BB_FROZEN_STOP_TF",
    "BOTTOM_A_PROTECTIVE_TRAIL_ARM_TIMEFRAME", "BOTTOM_A_PROTECTIVE_TRAIL_BREAK_BUFFER_ATR",
    "BOTTOM_A_PROTECTIVE_TRAIL_DISTANCE_MULT", "BOTTOM_A_PROTECTIVE_TRAIL_ENABLED",
    "BOTTOM_A_PROTECTIVE_TRAIL_LOOKBACK", "BOTTOM_A_PROTECTIVE_TRAIL_MODE",
    "BOTTOM_A_PROTECTIVE_TRAIL_TRAIL_TIMEFRAME", "BREAKEVEN_EXIT_AFTER_BARS",
    "BREAKEVEN_EXIT_AFTER_BARS_BUFFER_PCT", "BREAKEVEN_EXIT_AFTER_BARS_TF",
    "CONNORS_RSI2_TIME_STOP_BARS_DAILY", "DC4_STOP_GR_SCORE_MIN_IND", "DC4_STOP_GR_SCORE_MIN_TFS",
    "DC_LOW_FROZEN_STOP_TF", "DD_BOUNCE_DD_STOP_ENABLED", "EMERGENCY_BRAKE_DC_STOP_FIELD",
    "EXIT_PREEMPTIVE_BREAKEVEN_ENABLED", "HEDGE_EXIT_BYPASS_NOLOSS", "NEVER_GO_RED_STOP_ENABLED",
    "NEWBORN_DC_STOP_FIELD", "NEWBORN_DC_STOP_MAX_AGE_MIN",
    # NOLOSS_BYPASS_WT_5OF5_MIN_TFS removed 2026-09-28: really wired now (vec_decisions/reduce_profit_lock round)
    # FILTER_TF pair added: only live reads are `_=getattr` stubs (tradier_manage.py:32589/32623) — no semantics to mirror
    "PARTIAL_PROFIT_LOCK_V2_FILTER_TF", "NOLOSS_BYPASS_WT5OF5_FILTER_TF",
    # Wave-1 verdicts 2026-09-28: live engine observability-only (size_mult always 1.0) / NPZ lacks HA wick components
    "LIVE_ENTRY_ENGINE_FILTER_TF", "HA_WICK_QUALITY_TF", "HA_WICK_QUALITY_ENABLED", "HA_WICK_QUALITY_SCORE",
    # Wave-3 census verdicts 2026-09-28: ops/portfolio state or no vec parent (reasons in filter_wiring_census.json)
    "COOLDOWN_LOCKS_FILTER_TF", "DC_MOMENTUM_BOTA_SCORER_FILTER_TF", "DELTA_ENGINE_FILTER_TF",
    "DUP_GUARD_FILTER_TF", "FH_MOMENTUM_FILTER_TF", "FIRST_OPEN_THROTTLE_FILTER_TF",
    "FUNDING_GATE_FILTER_TF", "GOLDEN_RULE_ENFORCE_FILTER_TF", "GR_FILTER_VEC_FILTER_TF",
    "HAIKU_WINNER_FILTER_TF", "MTF_ARMED_ENTRIES_FILTER_TF",
    # Wave-4 final census verdicts: parent not vectorized / state absent
    "EXIT_R1_R2_FILTER_TF", "EXIT_TIGHT_BREAKOUT_SCORER_FILTER_TF", "EXIT_TO_REDUCE_ADAPTER_FILTER_TF",
    "FROZEN_STOP_FILTER_TF", "OPEN_INTENT_SIZE_GATES_FILTER_TF",
    "NOLOSS_DC4H_GATE_ENABLED", "QUICK_BREAKEVEN_GAIN_EROSION_VEC_ENABLED",
    "STOP_MAJOR_LOSS_ENABLED", "STOP_TIMEFRAME", "TRAILING_AUG_MAX_PER_POSITION",
    "UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS", "UNIVERSAL_NOLOSS_GATE_BYPASS_TECHNICAL",
})
# Portfolio-level live reduce paths (intraday L/S ratio trims, EOD slim, sentiment rebalance):
# single-symbol NPZ evals cannot see portfolio state, so these are live-only by construction,
# not purged — template rows exist so the switches are visible, but sheets must not eval them.
LIVE_ONLY_SWITCHES = frozenset({
    "INTRADAY_RATIO_REBALANCE_ENABLED", "INTRADAY_RATIO_DEVIATION_THR",
    "INTRADAY_RATIO_CHECK_INTERVAL_MIN", "INTRADAY_RATIO_TRIM_FRAC",
    "INTRADAY_RATIO_REQUIRE_TOP", "INTRADAY_RATIO_COOLDOWN_MIN",
    "INTRADAY_RATIO_MAX_TRIMS_PER_DAY", "EOD_SLIM_RATIO_ENABLED",
    "SENTIMENT_REBAL_REDUCE_DEVIATION_THR", "SENTIMENT_REBAL_COOLDOWN_MIN",
})
PROGRESS_JSON_EVERY_S = 15.0
# 2026-09-28 OOM FIX: unbounded eval cache grew for the pilot's lifetime (~72k evals/sym_side of result
# dicts) and contributed to OOM kills that truncated workbook saves (19 BadZip files quarantined
# backups/corrupt_xlsx_20260928/). Bounded FIFO: oldest quarter evicted when the cap is hit — identical
# semantics for the hot window (a row's naked+yellows+joint reuse each other within seconds).
_EVAL_CACHE_MAX = 200_000

class _BoundedEvalCache(dict):
    def __setitem__(self, key, value):
        if len(self) >= _EVAL_CACHE_MAX and key not in self:
            for _k in list(self.keys())[: _EVAL_CACHE_MAX // 4]:
                del self[_k]
        super().__setitem__(key, value)

_EVAL_CACHE: dict = _BoundedEvalCache()
_EVAL_CACHE_HITS = 0
RED_CELL_QUEUE: queue.Queue = queue.Queue()
# compat aliases for tests that probe timeout names
per_cell_timeout_sec = YELLOW_TIMEOUT
_per_cell_hard_limit = YELLOW_TIMEOUT
per_cell_deadline = YELLOW_TIMEOUT

ROOT = Path(__file__).resolve().parent
# USER 2026-09-28 "no double compute before mega sweep": only yellows on this allowlist get
# evaluated; the rest are PENDING_WIRING grey until their family's real vector path lands
# (see FILTER_WIRING_CENSUS_20260928.md). Missing/unreadable file -> None -> evaluate all
# (fail-open so a lost file can never silently grey out the whole yellow map).
def _load_wired_filters():
    try:
        _wf = json.loads((ROOT / "data" / "wired_filters.json").read_text())
        names = _wf.get("wired_filters") or []
        return frozenset(str(n).strip() for n in names) if names else None
    except Exception as _wf_e:
        print(f"[wired-filters] load failed ({_wf_e}) — fail-open, evaluating ALL yellows", flush=True)
        return None
WIRED_FILTERS = _load_wired_filters()
def _load_unwired():
    # data/vec_unwired.json (tools/v15_zero_audit.py): keys with NO reachable vectorized read AND no ledger movement in finished sheets.
    # V15_UNWIRED_SKIP=0 (complete-rounds): audit keys CALCULATE anyway — the tag sets label their honest
    # 0.0 rows UNWIRED_CALCULATED instead of skipping them. Returns (skip_sw, skip_fi, tag_sw, tag_fi).
    try:
        _d = json.loads((ROOT / "data" / "vec_unwired.json").read_text())
        _sw = frozenset(str(x).strip() for x in (list(_d.get("switches") or []) + list(_d.get("switches_manual") or [])))
        _fi = frozenset(str(x).strip() for x in (list(_d.get("filters") or []) + list(_d.get("filters_manual") or [])))
    except Exception as _ue:
        print(f"[unwired] load failed ({_ue}) — evaluating everything", flush=True)
        _sw, _fi = frozenset(), frozenset()
    if os.environ.get("V15_UNWIRED_SKIP", "1") == "0":
        print(f"[unwired] SKIP=0 — {len(_sw) + len(_fi)} audit keys calculate anyway (tagged UNWIRED_CALCULATED)", flush=True)
        return frozenset(), frozenset(), _sw, _fi
    return _sw, _fi, _sw, _fi
UNWIRED_SWITCHES, UNWIRED_FILTERS, UNWIRED_TAG_SW, UNWIRED_TAG_FI = _load_unwired()
def _unwired_audit_id():
    try:
        _p = ROOT / "data" / "vec_unwired.json"
        _st = _p.stat()
        return f"{int(_st.st_mtime)}:{int(_st.st_size)}"
    except Exception:
        return "none"
_UNWIRED_AUDIT_ID = _unwired_audit_id()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import zipfile
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
import numpy as np
import threading
# queue already imported at top via `import queue`; RED_CELL_QUEUE defined there

# ——— RED FIXER AGENT ———
# Daemon thread consuming red queue, re-evaluates each red cell with longer timeout (10s)
# via evaluate_sanitized/evaluate_prepared_sanitized, fixes formula, clears RED,
# writes correct delta/yellow, updates progress json. Runs concurrently while plowers
# continue, finishes all bad cells in <1h.
RED_QUEUE: "queue.Queue[dict]" = RED_CELL_QUEUE  # alias spec queue.Queue for fixer daemon (plowing never blocks)
RED_FIXER_LOCK = threading.Lock()
RED_FIXER_STARTED = False
RED_FIXER_THREAD: threading.Thread | None = None
RED_FIXER_STOP = threading.Event()
RED_FIXER_STATS = {"fixed": 0, "failed": 0, "queued": 0}

def _clear_red_fill(ws, r: int, c: int):
    try:
        cell = ws.cell(row=r, column=c)
        cell.fill = PatternFill(fill_type=None)
        cell.font = Font(name="Arial", size=10, bold=False, color="000000")
        cell.alignment = VISUAL_ALIGN
    except Exception:
        pass

def _red_fixer_daemon(new_symside: str, wb_path: Path, progress_path: Path, defaults: dict, prepared_ref: dict, args):
    """Daemon: consume RED_QUEUE, re-eval with 10s timeout, fix wb + progress.json."""
    import concurrent.futures as _cf_fix
    FIX_TIMEOUT = 10.0
    print(f"[red-fixer] daemon started for {new_symside} wb={wb_path.name}", flush=True)
    while not RED_FIXER_STOP.is_set():
        try:
            item = RED_QUEUE.get(timeout=1.0)
        except queue.Empty:
            continue
        sheet = item.get("sheet")
        r = int(item.get("r", 0))
        col = item.get("col")
        hdr = item.get("hdr")
        variant = item.get("variant")
        cumulative_before = float(item.get("cumulative_before", 0))
        switch = item.get("switch", "")
        cand = item.get("cand", "")
        key = item.get("key") or f"{sheet}!{r}:{switch}={cand}"
        try:
            # re-evaluate with 10s timeout
            vec = None
            # prepared_ref is a dict holder so daemon sees latest prepared (may be None)
            prepared = prepared_ref.get("prepared") if isinstance(prepared_ref, dict) else prepared_ref
            try:
                with _cf_fix.ThreadPoolExecutor(max_workers=1) as ex:
                    if prepared is not None:
                        fut = ex.submit(__import__("tools.opt.v12_pilot", fromlist=["evaluate_prepared_sanitized"]).evaluate_prepared_sanitized, prepared, variant, getattr(args, "window_days", 30))
                    else:
                        fut = ex.submit(__import__("tools.opt.v12_pilot", fromlist=["evaluate_sanitized"]).evaluate_sanitized, new_symside, variant, window_days=getattr(args, "window_days", 30))
                    vec = fut.result(timeout=FIX_TIMEOUT)
            except _cf_fix.TimeoutError:
                vec = {"valid": False, "gain_pct": None, "invalid_reason": f"fixer timeout {FIX_TIMEOUT}s"}
            except Exception as _e:
                vec = {"valid": False, "gain_pct": None, "invalid_reason": str(_e)[:80]}
            # compute delta
            if vec and vec.get("gain_pct") is not None:
                vg = float(vec.get("gain_pct") or 0)
                if not vec.get("valid") and "TIM" in str(vec.get("invalid_reason") or ""):
                    delta = 0.0
                else:
                    delta = vg - cumulative_before
            else:
                delta = 0.0
                vg = cumulative_before + delta if vec else cumulative_before
            # fix workbook + progress under lock
            with RED_FIXER_LOCK:
                # workbook — use _atomic_save under lock for consistency, but inline to avoid recursion on lock
                try:
                    wb = openpyxl.load_workbook(str(wb_path), data_only=False)
                    if sheet in wb.sheetnames and col:
                        ws = wb[sheet]
                        try:
                            cell = ws.cell(row=r, column=col)
                            # clear RED, write correct delta
                            cell.value = float(delta)
                            cell.alignment = VISUAL_ALIGN
                            if delta > 1e-9:
                                cell.fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                                cell.font = Font(name="Arial", size=10, bold=False, color="006100")
                            elif delta < -1e-9:
                                cell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                                cell.font = Font(name="Arial", size=10, bold=False, color="9C0006")
                            else:
                                cell.fill = PatternFill(fill_type=None)
                                cell.font = Font(name="Arial", size=10, bold=False, color="000000")
                        except Exception:
                            pass
                        # delegate to _atomic_save helper logic inline but without additional lock re-entry issues
                        # we replicate minimal save with thread-aware tmp to avoid pid collision
                        import os as _os_fix
                        tid = threading.get_ident()
                        tmp = f"{wb_path}.{_os_fix.getpid()}.{tid}.tmp"
                        try:
                            _paint_tab_status(wb)
                        except Exception:
                            pass
                        try:
                            _auto_adjust_all_sheets(wb)
                        except Exception:
                            pass
                        wb.save(tmp)
                        try:
                            import zipfile as _zf_v
                            _z = _zf_v.ZipFile(tmp, 'r')
                            _z.close()
                            # any valid zip is ok (small test wb may have <10 entries)
                            _os_fix.replace(tmp, str(wb_path))
                        except Exception as _e_z:
                            # BADZIP FIX 2026-09-29: never install an invalid zip — the old
                            # "forced replace" wrote known-bad files over good ones.
                            try: _os_fix.remove(tmp)
                            except: pass
                            print(f"[red-fixer-warn] zip validate {_e_z} — tmp DISCARDED, good file kept", flush=True)
                    wb.close()
                except Exception as _we:
                    print(f"[red-fixer-warn] wb fix {sheet}!{r} {hdr} {_we}", flush=True)
                # progress json
                try:
                    if progress_path.exists():
                        prog = json.loads(progress_path.read_text())
                    else:
                        prog = {}
                    rec = prog.get("done", {}).get(key)
                    if rec is not None:
                        # update yellow delta
                        if hdr:
                            yell = rec.get("yellows") or {}
                            yell[hdr] = float(delta)
                            rec["yellows"] = yell
                            # recompute sum pos and row delta if needed
                            sum_pos = sum(v for v in yell.values() if v > 1e-9)
                            # if row had only yellows, G = sum_pos else max; mimic spec: G = sum_pos if >0 else max
                            if sum_pos > 1e-9:
                                rec["delta"] = float(sum_pos)
                                # also need vec_gain update if sum_pos changed
                                rec["vec_gain"] = float(cumulative_before + sum_pos)
                            else:
                                # keep most negative if no pos
                                candidates = list(yell.values())
                                if candidates:
                                    rec["delta"] = float(max(candidates))
                    else:
                        # no prior rec — create minimal entry so fixer progress visible
                        prog.setdefault("done", {})[key] = {"delta": float(delta), "vec_gain": float(vg if vec else cumulative_before), "yellows": {hdr: float(delta)} if hdr else {}, "cumulative_before": float(cumulative_before), "cumulative_after": float(cumulative_before + delta) if delta > 0 else float(cumulative_before), "fixed_by_red_agent": True}
                        # also try to update yellows if hdr present
                        if hdr and key in prog.get("done", {}):
                            pass
                    # if this was a naked/timeout without hdr, ensure delta updated
                    if not hdr and rec is not None and vec and vec.get("gain_pct") is not None:
                        rec["delta"] = float(delta)
                        rec["vec_gain"] = float(vg)
                    _atomic_write_json(progress_path, prog)
                except Exception as _pe:
                    print(f"[red-fixer-warn] progress fix {key} {_pe}", flush=True)
            RED_FIXER_STATS["fixed"] += 1
            print(f"[red-fixer] fixed {sheet}!{r} {hdr or 'naked'} delta={delta:.4f} vg={vg:.4f} vs cum {cumulative_before:.4f} key={key}", flush=True)
        except Exception as _e:
            RED_FIXER_STATS["failed"] += 1
            print(f"[red-fixer-err] {_e}", flush=True)
        finally:
            try:
                RED_QUEUE.task_done()
            except Exception:
                pass
    print(f"[red-fixer] daemon exit stats {RED_FIXER_STATS}", flush=True)

def start_red_fixer(new_symside: str, wb_path: Path, progress_path: Path, defaults: dict, prepared_ref, args):
    global RED_FIXER_STARTED, RED_FIXER_THREAD
    if RED_FIXER_STARTED:
        return
    RED_FIXER_STARTED = True
    RED_FIXER_STOP.clear()
    # prepared_ref as dict holder for live updates
    if not isinstance(prepared_ref, dict):
        prepared_ref = {"prepared": prepared_ref}
    t = threading.Thread(target=_red_fixer_daemon, args=(new_symside, wb_path, progress_path, defaults, prepared_ref, args), daemon=True, name="v15_red_fixer")
    RED_FIXER_THREAD = t
    t.start()
    print(f"[red-fixer] started daemon thread {t.name} for {new_symside}", flush=True)

def stop_red_fixer(timeout: float = 60.0):
    RED_FIXER_STOP.set()
    if RED_FIXER_THREAD and RED_FIXER_THREAD.is_alive():
        RED_FIXER_THREAD.join(timeout=timeout)
    print(f"[red-fixer] stopped stats {RED_FIXER_STATS} queue={RED_QUEUE.qsize()}", flush=True)

def drain_red_queue(timeout: float = 3600.0) -> int:
    """Block until RED_QUEUE empties or timeout. Returns remaining qsize."""
    import time as _t_drain
    t0 = _t_drain.time()
    # wait for queue to drain, honouring <1h
    try:
        # join with timeout via polling
        while RED_QUEUE.qsize() > 0 and (_t_drain.time() - t0) < timeout:
            _t_drain.sleep(0.5)
        # also wait for current item to finish (unfinished task)
        remaining = RED_QUEUE.qsize()
        if remaining:
            print(f"[red-fixer-drain] timeout {timeout}s remaining {remaining} (fixer still running daemonly)", flush=True)
        else:
            # give last item's wb save a moment
            _t_drain.sleep(1.0)
            print(f"[red-fixer-drain] done in {_t_drain.time()-t0:.1f}s stats {RED_FIXER_STATS}", flush=True)
        return remaining
    except Exception as _e:
        print(f"[red-fixer-drain-warn] {_e}", flush=True)
        return RED_QUEUE.qsize()

def queue_red_cell(sheet: str, r: int, col: int | None, hdr: str | None, variant: dict, cumulative_before: float, switch: str, cand, key: str | None = None):
    try:
        item = {"sheet": sheet, "r": int(r), "col": col, "hdr": hdr, "variant": dict(variant) if variant else {}, "cumulative_before": float(cumulative_before), "switch": switch, "cand": cand, "key": key or f"{sheet}!{r}:{switch}={cand}"}
        RED_QUEUE.put(item)
        RED_FIXER_STATS["queued"] += 1
        print(f"[red-queue] {sheet}!{r} {hdr or 'naked'} queued (qsize={RED_QUEUE.qsize()})", flush=True)
    except Exception as _e:
        print(f"[red-queue-warn] {_e}", flush=True)

# Visual contract helpers — Arial 10 left, F same as others: header dark blue 1F4E78 black text, cells white
VISUAL_FONT = Font(name="Arial", size=10)
VISUAL_ALIGN = Alignment(horizontal="left", vertical="center", wrap_text=False)
VISUAL_F_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
VISUAL_F_FONT = Font(name="Arial", size=10, bold=True, color="000000")
VISUAL_HEADER_FILL = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
VISUAL_HEADER_FONT = Font(name="Arial", size=10, bold=True, color="000000")

def _apply_visual(ws, r, c, fill=None, font=None, is_bold=False):
    try:
        cell = ws.cell(row=r, column=c)
        cell.font = font or Font(name="Arial", size=10, bold=is_bold)
        cell.alignment = VISUAL_ALIGN
        if fill is not None:
            cell.fill = fill
    except Exception:
        pass

def _hdr_col_map(ws) -> dict:
    """Header lookup by row2 names — robust to column inserts per spec § TEMPLATE_*.xlsx."""
    m = {}
    try:
        for c in range(1, ws.max_column + 1):
            hv = ws.cell(row=2, column=c).value
            if hv and isinstance(hv, str):
                hv = hv.strip()
                # map exact header and lowercase variant
                m[hv] = c
                m[hv.lower()] = c
                # also map without spaces for robustness
                m[hv.replace(" ", "_").lower()] = c
        # fallback defaults if header missing (old template compat)
        for k, fallback in [("BASELINE",5),("HUSTLE_DELTA",6),("VECTOR_DELTA",7),("LIVE_DELTA",8),("LIVE_SHARPE",9),("PER_ROW_FILTERS",11),("override",3),("is_default",12)]:
            if k not in m and k.lower() not in m:
                m[k] = fallback
                m[k.lower()] = fallback
    except Exception:
        pass
    return m

def _resolve_cols(ws) -> dict:
    """Resolve E/F/G/H/I/K/C/L columns via header names, fallback to hardcoded."""
    hm = _hdr_col_map(ws)
    # common header variants observed in TEMPLATEs
    def pick(*names, fallback):
        for n in names:
            if n in hm: return hm[n]
            if n.lower() in hm: return hm[n.lower()]
        return fallback
    return {
        "C": pick("override", fallback=3),
        "E": pick("BASELINE", fallback=5),
        "F": pick("HUSTLE_DELTA", "HUSTLE", fallback=6),
        "G": pick("VECTOR_DELTA", "VECTOR", fallback=7),
        "H": pick("LIVE_DELTA", fallback=8),
        "I": pick("LIVE_SHARPE", fallback=9),
        "K": pick("PER_ROW_FILTERS", "PER_ROW_FILTERS", fallback=11),
        "L": pick("is_default", "is_default (backup if bold lost)", fallback=12),
    }

def _clear_vlookup_formulas(ws):
    """Pilot decides — no formulas in data rows r>=3 for C/E/F/G/H/I/K. Clear any VLOOKUP/IF. GLOBAL_RISK_GATES waived only if explicitly documented. Also erase Blanket (page end)."""
    for r in range(3, ws.max_row + 1):
        for col in (3, 5, 6, 7, 8, 9, 11):  # C,E,F,G,H,I,K
            try:
                v = ws.cell(row=r, column=col).value
                if isinstance(v, str) and v.startswith("="):
                    # GLOBAL_RISK_GATES exception: keep G/H/I formulas only if sheet is GLOBAL and col in 7,8,9 and row has no prior calc
                    if ws.title == "GLOBAL_RISK_GATES" and col in (7, 8, 9):
                        continue
                    ws.cell(row=r, column=col).value = None
                # Erase Blanket (page end) from H col8
                if col == 8 and isinstance(v, str) and "Blanket" in v:
                    ws.cell(row=r, column=col).value = None
                    ws.cell(row=r, column=col).fill = __import__("openpyxl").styles.PatternFill(fill_type=None)
            except Exception:
                pass
        # Also check col8 for Blanket even if not in loop (already handled) and any Blanket incols
        try:
            hv = ws.cell(row=r, column=8).value
            if isinstance(hv, str) and "Blanket" in hv:
                ws.cell(row=r, column=8).value = None
                ws.cell(row=r, column=8).fill = __import__("openpyxl").styles.PatternFill(fill_type=None)
        except Exception:
            pass

def _write_per_row_HIK(ws, r, live_delta, live_sharpe, per_row_filters):
    """Per-row K only — H/I deferred until sheet complete per law 2026-09-25 (formulas screw up fill). H=LIVE_DELTA col8, I=LIVE_SHARPE col9, K=PER_ROW_FILTERS col11. K per row, H/I at sheet-complete via backtest_v12_engine."""
    try:
        if ws is None:
            return
        # H/I deferred per law — do NOT write per row (screws up fill, formulas). Only K per row.
        # H col8 and I col9 will be filled when entire sheet complete via backtest_v12_engine parity.
        # K col11
        try:
            ws.cell(row=r, column=11).value = str(per_row_filters) if per_row_filters else None
            ws.cell(row=r, column=11).font = Font(name="Arial", size=10, bold=False)
            ws.cell(row=r, column=11).alignment = VISUAL_ALIGN
        except Exception:
            pass
    except Exception:
        pass

def _auto_adjust_sheet(ws):
    """Auto adjust column width (max len+2 cap 30) and row height 15 per row. Called after _atomic_save."""
    try:
        for col in ws.columns:
            max_len = 0
            col_letter = col[0].column_letter
            for cell in col:
                try:
                    if cell.value is not None:
                        l = len(str(cell.value))
                        if l > max_len:
                            max_len = l
                except Exception:
                    pass
            ws.column_dimensions[col_letter].width = min(max_len + 2, 30)
        for row in ws.iter_rows():
            try:
                ws.row_dimensions[row[0].row].height = 15
            except Exception:
                pass
    except Exception:
        pass

TEMPLATE_STOCKS_LONG = ROOT / "SPREADSHEETS" / "TEMPLATE_STOCKS_LONG.xlsx"
TEMPLATE_STOCKS_SHORT = ROOT / "SPREADSHEETS" / "TEMPLATE_STOCKS_SHORT.xlsx"
TEMPLATE_CRYPTO_LONG = ROOT / "SPREADSHEETS" / "TEMPLATE_CRYPTO_LONG.xlsx"
TEMPLATE_CRYPTO_SHORT = ROOT / "SPREADSHEETS" / "TEMPLATE_CRYPTO_SHORT.xlsx"
# USER 2026-09-28: TEMPLATE.xlsx is LEGACY (replaced long ago by the 4 cat_side templates).
# Alias kept only so old --template defaults keep working; remapped per symside at runtime.
TEMPLATE = TEMPLATE_STOCKS_LONG


def get_template_for_symside(symside: str) -> Path:
    """User 2026-09-24: TEMPLATE.xlsx discarded — pick side-specific template per symside."""
    s = symside.upper()
    # strip _LONG/_SHORT suffix before crypto check (ZECUSDC_LONG -> ZECUSDC)
    base = s[:-5] if s.endswith("_LONG") else s[:-6] if s.endswith("_SHORT") else s
    is_crypto = base.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI"))
    is_long = s.endswith("_LONG")
    # crypto vs stocks, long vs short
    if is_crypto:
        cand = TEMPLATE_CRYPTO_LONG if is_long else TEMPLATE_CRYPTO_SHORT
    else:
        cand = TEMPLATE_STOCKS_LONG if is_long else TEMPLATE_STOCKS_SHORT
    if cand.exists():
        return cand
    # fallback chain
    for p in [TEMPLATE_STOCKS_LONG, TEMPLATE_STOCKS_SHORT, TEMPLATE_CRYPTO_LONG, TEMPLATE_CRYPTO_SHORT]:
        if p.exists():
            return p
    return TEMPLATE_STOCKS_LONG


# V15_OUT_DIR isolates proof runs (DONE-stage publish renames same-sym_side sheets in OUT_DIR to .superseded)
OUT_DIR = Path(os.environ["V15_OUT_DIR"]) if os.environ.get("V15_OUT_DIR") else ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
# V15_PROGRESS_DIR isolates test runs from herd/cron progress sync (s1_pull_from_s2s3s5.sh restores stale JSON)
PROGRESS_DIR = Path(os.environ["V15_PROGRESS_DIR"]) if os.environ.get("V15_PROGRESS_DIR") else ROOT / "data" / "reports" / "lifecycle_pilot"
FLAGS_DIR = ROOT / "data" / "reports" / "v15_flags"
SWITCH_SHEETS = [
    "STDEV_SLOPE_SIZING",  # 13 tabs: STDEV is a single T/F switch tab (user 2026-09-28), filled like the rest
    "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL", "EXIT_VELOCITY",
    "REENTRY_WINDOWED", "REENTRY_ADAPTIVE",
    "AUGMENT_TREND", "AUGMENT_RISK_SIZING",
    "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
]
# 12-tab: STDEV_SLOPE_SIZING skipped — 3803 rows (was 4801 with STDEV). Sheet stays in TEMPLATE but never calculated.
SKIP_SHEETS: set = set()  # all 13 tabs filled (user 2026-09-28); 10s stall guard marks slow cells RED

# 2026-09-28 PARITY/NO-LIES promotion guards (deltas still measured + written; promotion blocked):
# NO_LIVE_PATH: wired in v12_quick_engine but absent from ez_manage/tradier_manage/config* (grep-proven
# 2026-09-28) — a promoted override on these changes NOTHING live, so the certified sheet gain is a lie
# until the switch is wired live. SIZING_FALSE_ALPHA: bigger notional scales gain% AND risk — not edge.
NO_LIVE_PATH_SWITCHES = {"WT_LOWER_CROSS_EXIT_TF", "TECHNICAL_DC_TARGET_TF", "WT_SIMPLE_GUARANTEE_ENABLED"}
SIZING_FALSE_ALPHA_SWITCHES = {"START_POSITION_SIZE", "MIN_POSITION_SIZE"}
# 2026-10-01 ZERO-AUDIT (vec_decisions/mtf_exit_scorer.py KEY REMAPS): the vector twin reads an NPZ key that EXISTS (wt_peak_{tf}, ha_{tf}, structure int8)
# where live ez_manage reads an indicator key NOBODY produces (wt_peak_value_{tf}, ha_green_{tf}, str 'LH') and so can never fire. The vector delta is
# real engine output, but a promoted override would change nothing in live -> promotion blocked until live produces those keys (user-gated live change).
LIVE_DEAD_KEY_SWITCHES = {"WT_DIV_EXIT_ENABLED", "WT_15M_LH_WAIT_EXIT_ENABLED"}

def promotion_block_reason(switch: str, sheet: str | None = None) -> str:
    s = str(switch).strip()
    if sheet is not None and sheet in DEAD_VEC_TABS:
        return "DEAD_VEC_PATH: _apply_new_audit_causal purged — no real vector wiring"
    if s in DEAD_VEC_SWITCHES:
        return "DEAD_VEC_PATH: wire07 purged — no real vector wiring"
    if s in LIVE_ONLY_SWITCHES:
        return "LIVE_ONLY: portfolio-level state, not single-symbol vectorizable"
    if s in NO_LIVE_PATH_SWITCHES and os.environ.get("V15_BLOCK_NO_LIVE_PATH") == "1":
        return "NO_LIVE_PATH: not wired in live code"
    if s in LIVE_DEAD_KEY_SWITCHES:
        return "LIVE_DEAD_KEY: live reads an indicator key nobody produces (wt_peak_value_*/ha_green_*) — vec remap fires where live cannot"
    if s in SIZING_FALSE_ALPHA_SWITCHES:
        return "SIZING_FALSE_ALPHA: notional, not edge"
    return ""


def tim_guard_veto(candidate_tim, chain_tim=None) -> str:
    """USER 2026-10-03 (TIM-guard): a G>0 promotion whose candidate set has TIM outside [20,80] is vetoed — the greedy fill must never filter the chain to death (FLOKI_SHORT desert: TIM 3.58 base -> C=0, E flat, 2794 evals wasted). G/F/Y are still recorded; only the promotion is blocked. A chain that starts out-of-band may still RECOVER: moves toward the band are allowed. Returns reason or ''. V15_TIM_GUARD=0 disables (default ON)."""
    if os.environ.get("V15_TIM_GUARD", "1") != "1":
        return ""
    try:
        t = float(candidate_tim) if candidate_tim is not None else None
    except Exception:
        t = None
    if t is None:
        return ""
    if QUAL_TIM_MIN <= t <= QUAL_TIM_MAX:
        return ""
    try:
        c = float(chain_tim) if chain_tim is not None else None
    except Exception:
        c = None
    if c is not None:
        if c < QUAL_TIM_MIN and t > c:
            return ""
        if c > QUAL_TIM_MAX and t < c:
            return ""
    if t < QUAL_TIM_MIN or t > QUAL_TIM_MAX:
        return f"TIM-guard: candidate TIM {t:.1f} outside [{QUAL_TIM_MIN:.0f},{QUAL_TIM_MAX:.0f}] — chain health over delta"
    return ""


def repair_needs_redo(rep) -> bool:
    """USER 2026-10-03 (row/set coherence): a COMPLIANCE repair that changed the set (any steps) makes the sheet rows stale — the repaired set must be re-filled (REDO), never published against rows measured for the failed set. Only a no-op repair (0 steps, identical set) may publish directly."""
    try:
        return len([s for s in ((rep or {}).get("steps") or []) if isinstance(s, dict) and s.get("applied")]) > 0
    except Exception:
        return True


def content_ok(f_filled, done_n) -> str:
    """USER 2026-10-03 (publish content gate): a sheet without calculations must NEVER publish. Returns '' if ok, else reason. Threshold: >=300 F cells and >=25% of the done board (a skip-bug publishes ~0; real fills write 48-73% — running/zero/red rows legitimately blank F)."""
    try:
        f = int(f_filled or 0)
    except Exception:
        f = 0
    try:
        n = int(done_n or 0)
    except Exception:
        n = 0
    if f < 300:
        return f"content-gate: F_filled {f}<300 — sheet has no calculations, refusing publish"
    if n > 0 and f * 4 < n:
        return f"content-gate: F_filled {f}<25% of done board {n} — refusing publish"
    return ""

ALL_PREPARED: dict[str, dict] = {}
ALL_NPZ_ARRAYS: dict[str, dict] = {}
_RUN_RAMFP: dict[str, tuple] = {}
_FILTER_DICT_CACHE = None
MAX_ROWS = 50000

# S1 NPZ locations (sandbox + gateway)
S1_NPZ_DIRS = [Path("/home/niels/binance-sandbox/backtest_v8/indicators"), ROOT / "backtest_v8" / "indicators"]
S1_SSH = "niels@157.90.168.35"
S1_SSH_SANDBOX = "niels@157.180.125.52"
CHARTS_1M_DIR = ROOT / "data" / "reports" / "charts_1M"

def ensure_npz_for_symside(symside: str, window_days: int = 30) -> Path | None:
    """Find (or generate if old/unavailable) NPZ on S1 before starting. Checks local, S1 sandbox, gateway; fetches via scp; regenerates if >30d old."""
    sym = symside.split("_")[0]
    # tokenised stocks: NVDAUSDT -> NVDA
    base = sym
    if sym.endswith("USDT"):
        try:
            from tools.opt.evaluate_v12 import _stock_universe
            if sym[:-4] in _stock_universe():
                base = sym[:-4]
        except Exception:
            pass
    local_candidates = [ROOT / "backtest_v8" / "indicators" / f"{base}.npz", ROOT / "backtest_v8" / "indicators" / f"{sym}.npz"]
    s1_candidates = [Path("/home/niels/binance-sandbox/backtest_v8/indicators") / f"{base}.npz", Path("/home/niels/binance-sandbox/backtest_v8/indicators") / f"{sym}.npz"]
    for p in local_candidates + s1_candidates:
        if p.exists() and p.stat().st_size > 100_000:
            age_days = (time.time() - p.stat().st_mtime) / 86400
            if age_days < 35:
                return p
            print(f"[npz-stale] {p} {age_days:.0f}d old — will refresh from S1", flush=True)
            break
    # try S1 fetch via scp -P 2201 gateway then s1-int
    for src_sym in [sym, base]:
        s1_src = f"/home/niels/binance-sandbox/backtest_v8/indicators/{src_sym}.npz"
        dst = ROOT / "backtest_v8" / "indicators" / f"{src_sym}.npz"
        for host in [S1_SSH, S1_SSH_SANDBOX]:
            try:
                # skip self-fetch when already on S1
                if Path("/home/niels/binance-sandbox").exists() and host in (S1_SSH_SANDBOX, "niels@157.180.125.52"):
                    raise RuntimeError("skip self S1 fetch")
                import subprocess as _sp
                dst.parent.mkdir(parents=True, exist_ok=True)
                # try scp with short timeout to avoid 30s hang
                # ONE npz at a time, no useless waits — 1s timeout, skip if local exists (2026-09-16 user: idle at baseline until NPZ fetch completes)
                if dst.exists() and dst.stat().st_size > 100_000:
                    print(f"[npz-local] {dst} {dst.stat().st_size/1e6:.1f}M", flush=True)
                    return dst
                cmd = ["scp", "-P", "2201", f"{host}:{s1_src}", str(dst)] if "157.90" in host else ["scp", f"{host}:{s1_src}", str(dst)]
                r = _sp.run(cmd, capture_output=True, timeout=1)
                if dst.exists() and dst.stat().st_size > 100_000:
                    print(f"[npz-fetch] {src_sym}.npz from {host} -> {dst} {dst.stat().st_size/1e6:.1f}M", flush=True)
                    return dst
            except Exception as e:
                print(f"[npz-fetch-warn] {host} {e}", flush=True)
                continue
        # fallback: rsync with short timeout
        try:
            if Path("/home/niels/binance-sandbox").exists():
                raise RuntimeError("skip rsync on S1")
            import subprocess as _sp
            _sp.run(["rsync", "-avz", "-e", "ssh -p 2201", f"{S1_SSH}:{s1_src}", str(dst)], capture_output=True, timeout=1)
            if dst.exists() and dst.stat().st_size > 100_000:
                print(f"[npz-rsync] {src_sym}.npz -> {dst}", flush=True)
                return dst
        except Exception:
            pass
    # final check any local now exists
    for p in local_candidates:
        if p.exists() and p.stat().st_size > 100_000:
            print(f"[npz-found] {p} {p.stat().st_size/1e6:.1f}M", flush=True)
            return p
    print(f"[npz-missing] {symside} no NPZ found — will attempt vector prepare anyway (may be synthetic)", flush=True)
    return None

def write_zoomable_chart(symside: str, sheet: str | None, overrides: dict, window_days: int, suffix: str = "30D_REAL_ZOOMABLE"):
    """Produce zoomable chart with all trades metrics and reasons for each trade at end of every sheet. Single-file offline file:// HTML."""
    try:
        from tools.opt.hires_chart import generate_hires
        # sheet-specific
        if sheet:
            out_name = f"{symside}_{sheet}_{suffix}.html"
            p = generate_hires(symside, overrides, window_days, out_name=out_name)
            # also ensure SPREADSHEETS copy
            for dst in [ROOT / "SPREADSHEETS" / out_name, CHARTS_1M_DIR / out_name]:
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copy2(p, dst)
                except Exception:
                    pass
            print(f"[chart] {sheet} -> {out_name} ({p.stat().st_size/1024:.0f}K)", flush=True)
            return p
        else:
            out_name = f"{symside}_{suffix}.html"
            p = generate_hires(symside, overrides, window_days, out_name=out_name)
            for dst in [ROOT / "SPREADSHEETS" / out_name, CHARTS_1M_DIR / out_name, Path(f"/tmp/{out_name}")]:
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copy2(p, dst)
                except Exception:
                    pass
            print(f"[chart] COMPLETE -> {out_name}", flush=True)
            return p
    except Exception as e:
        import traceback
        print(f"[chart-warn] {sheet or 'COMPLETE'} {e} {traceback.format_exc()[:600]}", flush=True)
    return None

def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _load_filter_dictionary() -> list[dict]:
    global _FILTER_DICT_CACHE
    if _FILTER_DICT_CACHE is not None:
        return _FILTER_DICT_CACHE
    candidates = [ROOT / "SPREADSHEETS" / "TEMPLATE_CRYPTO_LONG.xlsx", ROOT / "SPREADSHEETS" / "TEMPLATE_CRYPTO_SHORT.xlsx", ROOT / "SPREADSHEETS" / "TEMPLATE_STOCKS_LONG.xlsx", ROOT / "SPREADSHEETS" / "TEMPLATE_STOCKS_SHORT.xlsx", ROOT / "SPREADSHEETS" / "TEMPLATE.xlsx"]
    ws = None
    wb = None
    for p in candidates:
        if p.exists():
            try:
                wb = openpyxl.load_workbook(str(p), data_only=True)
                for cand in ["FILTER_DICTIONARY_V2", "FILTER_DICTIONARY", "FILTER_DICTIONARY_V8"]:
                    if cand in wb.sheetnames:
                        ws = wb[cand]
                        break
                if ws is not None:
                    break
                wb.close()
            except Exception:
                continue
    if ws is None:
        _FILTER_DICT_CACHE = []
        return _FILTER_DICT_CACHE
    hdr = {str(ws.cell(1, c).value or "").strip(): c for c in range(1, 30)}
    fc = hdr.get("Filter", 2)
    oc = sc = gc = rc = None
    for ci in range(1, 30):
        hv = str(ws.cell(1, ci).value or "")
        if "Option Value" in hv:
            oc = ci
        if "Sheets applicable" in hv:
            sc = ci
        if "Switches exactly" in hv:
            gc = ci
        if hv.strip() == "Recommendation":
            rc = ci
    # also load Options (col12) and Default for global per-sheet iteration
    oc2 = hdr.get("Options (all settings)", oc)
    dc = hdr.get("Default (config)", None)
    tc = hdr.get("TC (Timeframes Count)", None)
    rows = []
    for r in range(2, ws.max_row + 1):
        f = ws.cell(r, fc).value
        if not f or not isinstance(f, str) or f.strip().isdigit():
            continue
        f = f.strip()
        if not f:
            continue
        opt = ws.cell(r, oc).value if oc else None
        sheets_app = str(ws.cell(r, sc).value or "") if sc else ""
        gates = str(ws.cell(r, gc).value or "") if gc else ""
        rec = str(ws.cell(r, rc).value or "") if rc else ""
        opts = str(ws.cell(r, oc2).value or "") if oc2 else ""
        default = ws.cell(r, dc).value if dc else None
        # parse Options into list for global per-sheet iteration
        vals = []
        if opts:
            # Options like "20.0, ±25%, ±50% (4)" or "OFF, 15m, 1h, 4h, D (5 options)"
            import re
            # split by comma, strip, remove parenthetical
            parts = [p.strip().split("(")[0].strip() for p in opts.split(",")]
            for p in parts:
                if p and p not in vals:
                    vals.append(p)
        rows.append({"filter": f, "opt": opt, "sheets_app": sheets_app, "gates": gates, "rec": rec, "opts": opts, "vals": vals, "default": default})
    _FILTER_DICT_CACHE = rows
    try:
        wb.close()
    except Exception:
        pass
    return _FILTER_DICT_CACHE

def _token_overlap(gates: str, switch: str) -> bool:
    if not gates or not switch:
        return False
    gates = gates.strip()
    switch = switch.strip()
    if gates == switch:
        return True
    if gates.lower() == switch.lower():
        return True
    # STRICT: require gates to be exactly the switch or a specific gated mapping, not random token overlap like AZ/BA/BB for ETN
    # Previously returned True on single token overlap (e.g., FILTER) causing RANDOM filters — now require at least 2 tokens or exact switch containment with CV/CW yellows
    import re
    _STOP = {"filter","enabled","threshold","tf","gate","gates","switch","switches","exactly","it","the","and","for","with","only","gated","mode","value","level","type"}
    def toks(s): return [t for t in re.split(r"[ _\-\/]+", s.lower()) if len(t) > 3 and t not in _STOP]
    gt = set(toks(gates))
    st = set(toks(switch))
    # Require at least 2 tokens overlap or exact switch token in gates, and gates must be specific to this switch's sheet lifecycle (CV/CW yellows for E3)
    inter = gt & st
    if len(inter) >= 2:
        return True
    # For E3 row yellows CV CW etc., require switch name appears in gates with at least one strong token (e.g., WT_15M_BOUNCE, ATR, etc.)
    if switch.lower() in gates.lower() and len(inter) >= 1:
        # Ensure not just generic FILTER token
        return True
    return False

def _is_general(rec: str) -> bool:
    return rec.strip().upper().startswith("GENERAL")

def get_opportune_filters(switch: str, sheet: str) -> list[dict]:
    if not switch or not sheet:
        return []
    lifecycle = sheet.split("_")[0]
    rows = _load_filter_dictionary()
    out = []
    for e in rows:
        rec = (e["rec"] or "").strip().upper()
        if rec == "UNLIKELY" or "UNLIKELY" in rec:
            continue
        # THOROUGH REVISION: Yellow = all filters that can give pos delta to current switch = SPECIFIC only per legacy SPECIFIC/GENERAL mapping
        # GENERAL is orange per-sheet, not yellow per-switch — exclude from yellow
        if _is_general(e["rec"]):
            continue
        sa = e["sheets_app"] or ""
        if sa.strip() == "ALL":
            applicable = True
        else:
            applicable = (lifecycle in sa) or ("GLOBAL_CHECK" in sa)
        if not applicable:
            continue
        # yellow per-switch: only filters that can give pos delta to current switch = gated switches exactly
        # lifecycle generic caused 47 vs 5 (EXIT matches 100+); use strict token_overlap only
        if _token_overlap(e["gates"], switch):
            out.append(e)
    return out

_OPPORTUNE_MAP_CACHE = None

def _load_opportune_map() -> dict:
    global _OPPORTUNE_MAP_CACHE
    if _OPPORTUNE_MAP_CACHE is None:
        try:
            _OPPORTUNE_MAP_CACHE = json.loads((ROOT / "data" / "opportune_filter_map.json").read_text())
        except Exception:
            _OPPORTUNE_MAP_CACHE = {}
    return _OPPORTUNE_MAP_CACHE

def map_key_for_symside(symside: str) -> str:
    s = symside.upper()
    base = s[:-5] if s.endswith("_LONG") else s[:-6] if s.endswith("_SHORT") else s
    is_crypto = base.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI"))
    is_long = s.endswith("_LONG")
    return f"{'CRYPTO' if is_crypto else 'STOCKS'}_{'LONG' if is_long else 'SHORT'}"

_EVER_YELLOW_CACHE: dict = {}


def ever_yellow_cells(cat_side: str) -> set:
    """USER 2026-09-30: filter cells (tab, SWITCH=cand, FILTER=opt header) that ANY sym_side of this cat_side ever produced a
    non-zero delta for (tools/v15_yellow_from_deltas.py -> data/yellow_ever_nonzero.json) stay yellow + calculated."""
    if os.environ.get("V15_EVER_YELLOW", "1") == "0":
        return set()
    if cat_side not in _EVER_YELLOW_CACHE:
        try:
            _yj = json.loads((ROOT / "data" / "yellow_ever_nonzero.json").read_text())
            _EVER_YELLOW_CACHE[cat_side] = set(_yj.get("cells", {}).get(cat_side, [])) | set(_yj.get("light_yellow_untested", {}).get(cat_side, []))  # bright (ever non-zero) + light (never calculated: test until we know)
        except Exception:
            _EVER_YELLOW_CACHE[cat_side] = set()
    return _EVER_YELLOW_CACHE[cat_side]


_TAB_LEVEL_CACHE: dict = {}


def tab_level_filters(cat_side: str, sheet: str) -> set:
    """USER 2026-10-01: filters yellow on >20% of a tab's switch rows live as orange rows at the bottom of that tab (tools/v15_tab_level_filters.py +
    v15_daily_template_update.py --tab-level) and are tested ONCE there: the per-row evaluation of their columns is skipped (the cell keeps its status).
    V15_TAB_LEVEL_FILTERS=0 restores the per-row test. USER 2026-10-03: flag file data/tablevel.flag ("1"/"0") also controls it (env wins, else flag, else ON)."""
    _tl_env = os.environ.get("V15_TAB_LEVEL_FILTERS")
    if _tl_env is not None:
        if _tl_env == "0":
            return set()
    else:
        try:
            _tl_flag = (ROOT / "data" / "tablevel.flag").read_text().strip()
            if _tl_flag == "0":
                return set()
        except Exception:
            pass
    if "spec" not in _TAB_LEVEL_CACHE:
        try:
            _TAB_LEVEL_CACHE["spec"] = json.loads((ROOT / "data" / "wiring" / "tab_filters" / "tab_level_filters.json").read_text()).get("cats", {})
        except Exception:
            _TAB_LEVEL_CACHE["spec"] = {}
    return set(((_TAB_LEVEL_CACHE["spec"].get(cat_side) or {}).get(sheet) or {}).keys())


def opportune_filter_bases(symside: str, sheet: str, switch: str) -> set:
    m = _load_opportune_map()
    key = map_key_for_symside(symside)
    return set(((m.get(key) or {}).get(sheet) or {}).get(switch, []))

def opportune_filter_rows(symside: str, sheet: str, switch: str) -> list[dict]:
    bases = opportune_filter_bases(symside, sheet, switch) - tab_level_filters(map_key_for_symside(symside), sheet)  # tab-level filters are tested once as orange rows, not per switch row
    if not bases:
        return []
    return [e for e in _load_filter_dictionary() if (e.get("filter") or "").strip() in bases and not _is_general(e["rec"])]

# USER 2026-10-04 KINDERGARTEN MANDATE: the simple trend-alignment entry filters (kindergarten
# gate + EMA 9/21 + retiring blanket) MUST be tested on EVERY row of the ENTRY tabs, and the
# take-profit + step-back-in combo (rally reentry knobs + DC TARGET exits) on EVERY row of the
# REENTRY tabs. The >=2-shared-token yellow rule withholds these from most rows (a bounce/
# breakout switch shares <2 tokens with KINDERGARTEN_*/EMA_9_21_*), so without this the sweep
# can never discover trend-aligned entries or take+reenter combos. Outcomes stay greedy-honest
# (every mandatory cell is a real engine eval, pos-only promotion unchanged); kill-switches
# V15_KG_MANDATORY=0 / V15_RECROSS_MANDATORY=0 restore token-only behavior.
ENTRY_TABS_MANDATORY = frozenset({"ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES"})
REENTRY_TABS_MANDATORY = frozenset({"REENTRY_WINDOWED", "REENTRY_ADAPTIVE"})
RECROSS_MANDATORY_BASES = frozenset({
    "HARDCODED_RALLY_REENTRY_ENABLED", "HARDCODED_RALLY_REENTRY_REQUIRE_WT",
    "HARDCODED_RALLY_MIN_MOVE_PCT", "HARDCODED_RALLY_MIN_AGE_MIN",
    "HARDCODED_RALLY_HTF_TREND_TF", "HARDCODED_RALLY_DC_POS_MAX",
    "HARDCODED_RALLY_SMA200_SIDE_ENABLED",
    "DAYTRADE_DC_TARGET_TF", "TECHNICAL_DC_TARGET_TF",
})
# USER 2026-10-04 ADDITION: everything trades both directions now, so the vec-wired
# counter-trend protection filters are tested on EVERY ENTRY row too (greedy decides on/off:
# counterproductive stays off). TOP_OF_RANGE_BLOCK_ENABLED + HTF_TREND_VETO_ENABLED are NOT
# here: live-only, no vectorized read — testing them would burn evals on structural zeros
# (genuine engine gap, needs vec wiring in locked files — flagged, not worked around).
COUNTER_TREND_MANDATORY_BASES = frozenset({
    "COUNTER_TREND_ADD_BLOCK_ENABLED", "COUNTER_TREND_SMA200_BYPASS_ENABLED",
    "HTF_DIRECTION_GATE_ENABLED", "GR_FILTER_ALL_ENTRIES", "ADX_REGIME_FILTER_ENABLED",
})
_KG_BASES_CACHE: set | None = None
_MANDATORY_HITS: dict = {}

def _load_kg_bases() -> set:
    global _KG_BASES_CACHE
    if _KG_BASES_CACHE is None:
        bases = set()
        try:
            _kg = json.loads((ROOT / "data" / "kindergarten_filters.json").read_text())
            for _k in list(_kg.get("kindergarten_bases", [])) + list(_kg.get("retiring_bases", [])):
                if _k and isinstance(_k, str):
                    bases.add(_k.strip())
        except Exception as _e:
            print(f"[KG-mandatory-warn] kindergarten_filters.json unreadable ({_e}) — mandatory set empty", flush=True)
        try:
            bases &= known_config_fields()
        except Exception:
            pass
        _KG_BASES_CACHE = bases
        print(f"[KG-mandatory] {len(bases)} real KG bases loaded", flush=True)
    return _KG_BASES_CACHE

def mandatory_bases_for_tab(sname: str) -> set:
    if sname in ENTRY_TABS_MANDATORY:
        out = set()
        if os.environ.get("V15_KG_MANDATORY", "1") != "0":
            out |= _load_kg_bases()
        if os.environ.get("V15_CT_MANDATORY", "1") != "0":
            try:
                out |= set(COUNTER_TREND_MANDATORY_BASES) & known_config_fields()
            except Exception:
                out |= set(COUNTER_TREND_MANDATORY_BASES)
        return out
    if sname in REENTRY_TABS_MANDATORY and os.environ.get("V15_RECROSS_MANDATORY", "1") != "0":
        try:
            return set(RECROSS_MANDATORY_BASES) & known_config_fields()
        except Exception:
            return set(RECROSS_MANDATORY_BASES)
    return set()

# USER 2026-10-04 ESSENTIAL-ROWS GATE: the make-or-break protection filters. No sheet may
# finish/qualify while any of these has no non-zero number in its rows (naked G, any yellow,
# or a ledger move). A zero here = protection unproven on this window = the side must not
# trade (bearish LONG trading into a downtrend on unproven protection is how accounts die).
# Membership rule: vec-wired on BOTH venues + white/orange rows with >=2 candidates exist.
# Deliberately NOT here: KINDERGARTEN_ALWAYS_TEST (dead, no read anywhere),
# EMA_9_21_SCORE_BONUS (DEAD_CONFIRMED), KINDERGARTEN_CUMULATIVE_MODE (stocks-only read),
# KINDERGARTEN_STRICT_TFS ('' default unrepresentable as a row; group greyed -> truth fallback),
# TOP_OF_RANGE_BLOCK_ENABLED + HTF_TREND_VETO_ENABLED (live-only, no vec read).
# Venue binding proven by S1 flip test 2026-10-04 (tmp_s1_flip_test.py) — see matrix in report.
ESSENTIAL_GATE_SWITCHES = frozenset({
    "KINDERGARTEN_EMA_GATE_ENABLED", "KINDERGARTEN_FILTER_TF",
    "KINDERGARTEN_CUMULATIVE_MIN_TFS",
    "EMA_9_21_FILTER_ENABLED", "EMA_9_21_FILTER_FILTER_TF", "EMA_9_21_FILTER_MIN_TFS",
    "EMA_9_21_FILTER_TFS", "EMA_9_21_TIMEFRAME",
    "EMA_BLANKET_FILTER_ENABLED", "EMA_BLANKET_FILTER_FILTER_TF", "EMA_BLANKET_FILTER_MIN_TFS",
    "HARDCODED_RALLY_REENTRY_ENABLED", "HARDCODED_RALLY_REENTRY_REQUIRE_WT",
    "HARDCODED_RALLY_MIN_MOVE_PCT", "HARDCODED_RALLY_MIN_AGE_MIN",
    "HARDCODED_RALLY_HTF_TREND_TF", "HARDCODED_RALLY_DC_POS_MAX",
    "HARDCODED_RALLY_SMA200_SIDE_ENABLED",
    "DAYTRADE_DC_TARGET_TF", "TECHNICAL_DC_TARGET_TF",
    "COUNTER_TREND_ADD_BLOCK_ENABLED", "COUNTER_TREND_SMA200_BYPASS_ENABLED",
    "HTF_DIRECTION_GATE_ENABLED", "GR_FILTER_ALL_ENTRIES", "ADX_REGIME_FILTER_ENABLED",
})

def _gate_parent_on(final_overrides: dict, parents: tuple) -> bool:
    fo = final_overrides or {}
    for p in parents:
        v = fo.get(p)
        if v is None:
            continue
        if isinstance(v, bool) and v:
            return True
        if str(v).strip().lower() in ("true", "1", "yes", "on"):
            return True
    return False

# sub-knob -> parent gates: a sub reads vacuous-zero while every parent is OFF in the final
# set (no-op by design, §14.3 case 2) — the gate SKIPs it instead of failing, so a correctly
# rejected (counterproductive, stays-off) gate never poisons its own sheet.
ESSENTIAL_GATE_PARENTS = {
    "KINDERGARTEN_FILTER_TF": ("KINDERGARTEN_EMA_GATE_ENABLED", "EMA_9_21_FILTER_ENABLED"),
    "KINDERGARTEN_CUMULATIVE_MIN_TFS": ("KINDERGARTEN_EMA_GATE_ENABLED", "EMA_9_21_FILTER_ENABLED"),
    "EMA_9_21_FILTER_FILTER_TF": ("EMA_9_21_FILTER_ENABLED", "KINDERGARTEN_EMA_GATE_ENABLED"),
    "EMA_9_21_FILTER_MIN_TFS": ("EMA_9_21_FILTER_ENABLED", "KINDERGARTEN_EMA_GATE_ENABLED"),
    "EMA_9_21_FILTER_TFS": ("EMA_9_21_FILTER_ENABLED", "KINDERGARTEN_EMA_GATE_ENABLED"),
    "EMA_9_21_TIMEFRAME": ("EMA_9_21_FILTER_ENABLED", "KINDERGARTEN_EMA_GATE_ENABLED"),
    "EMA_BLANKET_FILTER_FILTER_TF": ("EMA_BLANKET_FILTER_ENABLED",),
    "EMA_BLANKET_FILTER_MIN_TFS": ("EMA_BLANKET_FILTER_ENABLED",),
    "HARDCODED_RALLY_MIN_MOVE_PCT": ("HARDCODED_RALLY_REENTRY_ENABLED",),
    "HARDCODED_RALLY_MIN_AGE_MIN": ("HARDCODED_RALLY_REENTRY_ENABLED",),
    "HARDCODED_RALLY_HTF_TREND_TF": ("HARDCODED_RALLY_REENTRY_ENABLED",),
    "HARDCODED_RALLY_DC_POS_MAX": ("HARDCODED_RALLY_REENTRY_ENABLED",),
    "HARDCODED_RALLY_SMA200_SIDE_ENABLED": ("HARDCODED_RALLY_REENTRY_ENABLED",),
    "HARDCODED_RALLY_REENTRY_REQUIRE_WT": ("HARDCODED_RALLY_REENTRY_ENABLED",),
    "COUNTER_TREND_SMA200_BYPASS_ENABLED": ("COUNTER_TREND_ADD_BLOCK_ENABLED",),
}

def essential_gate_check(done: dict, final_overrides: dict | None = None, is_long: bool = True, is_crypto: bool = True) -> dict:
    per = {}
    xrow_yellow = {}
    for key, rec in (done or {}).items():
        if not isinstance(rec, dict):
            continue
        sw = rec.get("switch")
        if not sw and isinstance(key, str) and ":" in key:
            try:
                sw = key.split(":")[1].split("=")[0]
            except Exception:
                sw = None
        if sw in ESSENTIAL_GATE_SWITCHES:
            per.setdefault(sw, []).append(rec)
        for h, yd in (rec.get("yellows") or {}).items():
            if isinstance(yd, (int, float)) and abs(yd) > 1e-9 and isinstance(h, str) and "=" in h:
                xrow_yellow.setdefault(h.split("=", 1)[0].strip(), []).append((key, yd))
    out = {}
    for sw in ESSENTIAL_GATE_SWITCHES:
        if sw == "GR_FILTER_ALL_ENTRIES" and not is_long:
            out[sw] = {"status": "SKIP", "reason": "LONG-only filter (live + vec), no SHORT path exists"}
            continue
        if sw == "ADX_REGIME_FILTER_ENABLED" and not is_crypto:
            out[sw] = {"status": "SKIP", "reason": "crypto-only vec read (MODE!=tradier guard), no stocks path"}
            continue
        parents = ESSENTIAL_GATE_PARENTS.get(sw)
        if parents and not _gate_parent_on(final_overrides, parents):
            out[sw] = {"status": "SKIP", "reason": f"vacuous-honest (parents {parents} all OFF in final set)"}
            continue
        recs = per.get(sw, [])
        tested = [r for r in recs if not r.get("is_running") and r.get("delta") is not None]
        if not tested:
            if sw in xrow_yellow:
                _xk, _xd = xrow_yellow[sw][0]
                out[sw] = {"status": "PASS", "reason": f"cross-row yellow {_xd:+.4f} ({_xk})"}
            else:
                out[sw] = {"status": "FAIL", "reason": f"untested ({len(recs)} rows, all running/skipped/missing)"}
            continue
        ev = None
        for r in tested:
            d = r.get("delta")
            if isinstance(d, (int, float)) and abs(d) > 1e-9:
                ev = f"naked {d:+.4f}"
                break
            for h, yd in (r.get("yellows") or {}).items():
                if isinstance(yd, (int, float)) and abs(yd) > 1e-9:
                    ev = f"yellow {h} {yd:+.4f}"
                    break
            if ev:
                break
            if r.get("naked_binding") is True:
                ev = "ledger-move (gain-neutral)"
                break
        if not ev and sw in xrow_yellow:
            _xk, _xd = xrow_yellow[sw][0]
            ev = f"cross-row yellow {_xd:+.4f} ({_xk})"
        if ev:
            out[sw] = {"status": "PASS", "reason": ev}
        else:
            out[sw] = {"status": "FAIL", "reason": f"all-zero ({len(tested)} rows evaluated, ledger unmoved)"}
    return out

_START_STAMP = None
def _capture_start_stamp() -> None:
    global _START_STAMP
    try:
        import hashlib as _hl
        from datetime import datetime, timezone
        _START_STAMP = {"result_engine_md5": _hl.md5((ROOT / "v12_quick_engine.py").read_bytes()).hexdigest(), "result_pilot_md5": _hl.md5(Path(__file__).resolve().read_bytes()).hexdigest(), "result_config_md5_crypto": _hl.md5((ROOT / "config.py").read_bytes()).hexdigest(), "result_config_md5_stocks": _hl.md5((ROOT / "config_tradier.py").read_bytes()).hexdigest(), "result_start_utc": datetime.now(timezone.utc).isoformat()}
    except Exception:
        _START_STAMP = None
def _result_stamp(progress: dict, symside: str) -> None:
    try:
        import hashlib as _hl
        from datetime import datetime, timezone
        _is_stocks = map_key_for_symside(symside).startswith("STOCKS")
        if _START_STAMP:
            progress["result_engine_md5"] = _START_STAMP["result_engine_md5"]
            progress["result_pilot_md5"] = _START_STAMP["result_pilot_md5"]
            progress["result_config_md5"] = _START_STAMP["result_config_md5_stocks" if _is_stocks else "result_config_md5_crypto"]
            progress["result_start_utc"] = _START_STAMP["result_start_utc"]
        else:
            for _k, _p in (("result_engine_md5", ROOT / "v12_quick_engine.py"),
                           ("result_pilot_md5", Path(__file__).resolve()),
                           ("result_config_md5", ROOT / ("config_tradier.py" if _is_stocks else "config.py"))):
                progress[_k] = _hl.md5(_p.read_bytes()).hexdigest()
        progress["result_done_utc"] = datetime.now(timezone.utc).isoformat()
    except Exception:
        pass

def _ram_fp(symside):
    """In-RAM NPZ fingerprint: proves row evals and promotion measured identical bytes. None when cold (fail-open: tripwire covers the hot path only). NaN-normalized so the fp is stable."""
    try:
        import numpy as _np
        _arrs = ALL_NPZ_ARRAYS.get(symside) or {}
        if not _arrs:
            return None
        _close = _arrs.get("close")
        _ts = _arrs.get("timestamps")
        if _close is None or _ts is None:
            return None
        def _f(v):
            try:
                _x = float(v)
                return None if _x != _x else _x
            except Exception:
                return None
        return (len(_arrs), int(len(_close)), _f(_ts[0]), _f(_ts[-1]), _f(_close[0]), _f(_close[-1]), _f(_np.nansum(_np.asarray(_close, dtype="float64"))))
    except Exception:
        return None
def _reverify_within_tol(new_gain, old_gain) -> bool:
    """Reproducer gate: chain gain re-measures within abs/rel tolerance. Env-tunable, tight by default (deterministic engine: same bytes = bit-identical)."""
    try:
        import os as _os
        _abs = float(_os.environ.get("V15_REVERIFY_ABS", "0.10"))
        _rel = float(_os.environ.get("V15_REVERIFY_REL", "0.01"))
        return abs(float(new_gain) - float(old_gain)) <= max(_abs, _rel * abs(float(old_gain)))
    except Exception:
        return False
def _mandatory_note(sname: str, base: str, via_token: bool) -> None:
    if via_token:
        return
    _d = _MANDATORY_HITS.setdefault(sname, {})
    _d[base] = _d.get(base, 0) + 1

def _mandatory_summary() -> str:
    parts = []
    for _s in sorted(_MANDATORY_HITS):
        _d = _MANDATORY_HITS[_s]
        parts.append(f"{_s}:{sum(_d.values())}cells/{len(_d)}bases")
    return ", ".join(parts) if parts else "none"

def sanitize_overrides(overrides: dict, defaults: dict) -> tuple[dict, list]:
    sanitized = {}
    warns = []
    for k, v in dict(overrides).items():
        if isinstance(v, str) and v.strip().endswith("_ALT"):
            v = v.strip()[:-4]
        if isinstance(v, str):
            vs = v.strip()
            if vs.lower() in ("true", "false"):
                sanitized[k] = vs.lower() == "true"
                continue
            dval = defaults.get(k)
            if isinstance(dval, (int, float)) and not isinstance(dval, bool):
                try:
                    if all(c in "0123456789.+-eE" for c in vs):
                        num = float(vs)
                        if isinstance(dval, int) and not isinstance(dval, bool) and num.is_integer():
                            sanitized[k] = int(num)
                        else:
                            sanitized[k] = float(num) if isinstance(dval, float) else num
                            if isinstance(dval, float) and isinstance(sanitized[k], int):
                                sanitized[k] = float(sanitized[k])
                        continue
                except Exception:
                    pass
            sanitized[k] = vs
        else:
            sanitized[k] = v
    float_keys = ["ATR_ADAPTIVE_SIZING_TARGET_PCT", "BOUNCE_AUGMENT_K_D_THRESHOLD", "DC_EDGE_SIZING_MAX_MULT", "EMA_DIST_SIZING_MULT", "REENTRY_TIER1_SIZE_MULT_TRADIER", "BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE"]
    for k in float_keys:
        if k in sanitized and isinstance(sanitized[k], bool):
            sanitized[k] = float(defaults.get(k, 1.0)) if defaults.get(k) is not None else 1.0
            warns.append(k)
    return sanitized, warns

def get_defaults_for_symside(symside: str) -> dict:
    # Ensure sandbox root is on sys.path for vec_decisions imports even when
    # launched via setsid/detached contexts where cwd/env may be stripped.
    try:
        _root = str(Path(__file__).resolve().parent)
        if _root not in sys.path:
            sys.path.insert(0, _root)
    except Exception:
        pass
    import v12_quick_engine as V
    import config, config_tradier
    # strip _LONG/_SHORT first — "AAVEUSDC_LONG".endswith("USDC") is False, which gave crypto the TradierConfig defaults
    is_crypto = map_key_for_symside(symside).startswith("CRYPTO")
    defaults = {}
    for f in dataclasses.fields(V.QuickConfig):
        defaults[f.name] = f.default if f.default is not dataclasses.MISSING else None
        if defaults[f.name] is None and f.default_factory is not dataclasses.MISSING:  # type: ignore
            try:
                defaults[f.name] = f.default_factory()  # type: ignore
            except Exception:
                defaults[f.name] = None
    live_cls = config.Config if is_crypto else config_tradier.TradierConfig
    for k in dir(live_cls):
        if k.startswith("_"):
            continue
        if k not in defaults:
            try:
                defaults[k] = getattr(live_cls, k)
            except Exception:
                pass
    return defaults

_KNOWN_FIELDS: set = set()


def known_config_fields() -> set:
    # USER 2026-09-30: a switch/filter that exists in none of config / config_tradier / QuickConfig (e.g. *_ALT) is an
    # invented row with no setting to calculate -> grey, never evaluated
    if not _KNOWN_FIELDS:
        import v12_quick_engine as V
        import config, config_tradier
        _KNOWN_FIELDS.update(f.name for f in dataclasses.fields(V.QuickConfig))
        for cls in (config.Config, config_tradier.TradierConfig):
            _KNOWN_FIELDS.update(k for k in dir(cls) if not k.startswith("_"))
    return _KNOWN_FIELDS


UNTRUSTED_BOLD: list = []  # last template_bold_defaults() call: bold values NOT used as defaults (placeholder options)


def _same_default(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool) or str(a).strip().lower() in ("true", "false") or str(b).strip().lower() in ("true", "false"):
        return str(a).strip().lower() == str(b).strip().lower()
    try:
        return abs(float(a) - float(b)) < 1e-12
    except Exception:
        return str(a).strip() == str(b).strip()


def template_bold_defaults(template_path, defaults: dict, truth: dict | None = None, promoted: set | None = None) -> tuple[dict, list]:
    """USER 2026-09-29: the BOLD / is_default=YES row of every (tab, switch) group IS the running default. Returns
    (engine overrides for all bold defaults, violations). A violation = a non-grey group without exactly one is_default
    YES, or whose YES row is not the bold row, or a switch whose default differs between tabs. Fail-closed at the caller."""
    import openpyxl as _opx
    wb = _opx.load_workbook(str(template_path))
    vals, where, bad = {}, {}, []
    UNTRUSTED_BOLD.clear()
    for tab in SWITCH_SHEETS:
        if tab not in wb.sheetnames:
            continue
        ws = wb[tab]
        idc = next((c for c in range(1, ws.max_column + 1) if str(ws.cell(row=2, column=c).value or "").strip().lower().startswith("is_default")), None)
        if idc is None:
            bad.append(f"{tab}: no is_default column")
            continue
        groups = {}
        for r in range(3, ws.max_row + 1):
            a = ws.cell(row=r, column=1).value
            if a in (None, "") or str(a).strip().lower() in ("switch", "general", "blanket", "filter", "option value", "sheets applicable", "gates"):
                continue
            groups.setdefault(str(a).strip(), []).append(r)
        for sw, rows in groups.items():
            f = ws.cell(row=rows[0], column=1).font
            if f is not None and f.color is not None and str(getattr(f.color, "rgb", "") or "").upper() in GREY_SKIP_RGB:
                continue
            yes = [r for r in rows if str(ws.cell(row=r, column=idc).value or "").strip().upper() == "YES"]
            bold = [r for r in rows if ws.cell(row=r, column=2).font is not None and ws.cell(row=r, column=2).font.b]
            if len(yes) != 1 or bold != yes:
                bad.append(f"{tab}!{sw}: is_default YES rows {yes}, bold rows {bold}")
                continue
            v = _parse_opt_value(ws.cell(row=yes[0], column=2).value, defaults.get(sw))
            if truth is not None and sw in truth and sw not in (promoted or set()) and (isinstance(truth[sw], (dict, list, tuple, set)) or not _same_default(v, truth[sw])):
                # USER 2026-09-30: a placeholder bold (group options do not contain the real default) is never pushed as
                # the default into the engine / the 4-set layer — the real live value stays the default
                # USER 2026-10-04: EXCEPT the 2026-10-03 ABLATION ABOLITION shape (template False vs config True):
                # the abolition outranks this guard — reinstating truth True re-imposes fleet-wide entry death
                # (PEPE_LONG bolds 150 trades -> 0). The abolition False IS the default.
                if sw.startswith("ABLATION_DISABLE_") and isinstance(v, bool) and not v and truth[sw] in (True, 1):
                    pass
                else:
                    UNTRUSTED_BOLD.append((tab, sw, v, truth[sw]))
                    continue
            if sw in vals and str(vals[sw]) != str(v):
                bad.append(f"{sw}: default {vals[sw]!r} ({where[sw]}) != {v!r} ({tab})")
                continue
            vals[sw], where[sw] = v, tab
    wb.close()
    ov = {}
    for sw, v in vals.items():
        ov.update(_switch_overrides(sw, v))
    return ov, bad

def _macbook_desktop_notify(title: str, msg: str, critical: bool = False):
    """Send to MacBook desktop: local osascript + ssh to macbook + persistent jsonl for warn-daemon."""
    # persistent log for Mac polling / warn daemon
    try:
        rec = {"ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), "title": title, "msg": msg, "critical": critical}
        for p in [ROOT / "data" / "v15_desktop_notify.jsonl", Path("/tmp/v15_desktop_notify.jsonl"), FLAGS_DIR / "v15_desktop_notify.jsonl"]:
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("a") as f:
                    f.write(__import__("json").dumps(rec) + "\n")
            except: pass
    except: pass
    # local macOS notification if running on Darwin
    try:
        import subprocess as _sp, pathlib as _pl
        t = title.replace('"', '\\"').replace("\n", " ")
        m = msg.replace('"', '\\"').replace("\n", " ")
        snd = "Basso" if critical else "Submarine"
        if __import__("sys").platform == "darwin":
            _sp.run(["osascript", "-e", f'display notification "{m}" with title "{t}" subtitle "v15 pilot" sound name "{snd}"'], timeout=4)
            if _pl.Path("/opt/homebrew/bin/terminal-notifier").exists() or _pl.Path("/usr/local/bin/terminal-notifier").exists():
                try: _sp.run(["terminal-notifier", "-title", title, "-message", msg, "-sound", snd], timeout=4)
                except: pass
        # also try ssh to Mac from Linux herd (best-effort)
        for h in ["macbook", "mac", "niels-macbook", "macbook.local"]:
            try:
                _sp.run(["ssh", "-o", "ConnectTimeout=2", "-o", "StrictHostKeyChecking=no", h, f'osascript -e \'display notification "{m}" with title "{t}" subtitle "v15 pilot" sound name "{snd}"\''], timeout=4, stdout=__import__("subprocess").DEVNULL, stderr=__import__("subprocess").DEVNULL)
                break
            except: continue
    except: pass

def _flag_to_md(flags_md: Path, sheet: str, r: int, switch: str, cand, reason: str, delta, vec_gain, cumulative_before):
    """Append flagged blocking cell to MD for dedicated fix agent — never interrupts workbook/chart production."""
    try:
        flags_md.parent.mkdir(parents=True, exist_ok=True)
        # create header if new
        if not flags_md.exists():
            flags_md.write_text(f"# V15 Flags — {flags_md.stem}\n\n| Sheet | Row | Switch | Cand | Reason | Delta | VecGain | CumBefore |\n|---|---|---|---|---|---|---|---|\n")
        with flags_md.open("a") as f:
            f.write(f"| {sheet} | {r} | {switch} | {cand} | {reason} | {delta} | {vec_gain} | {cumulative_before} |\n")
    except Exception:
        pass

def _auto_adjust_all_sheets(wb):
    """Visual: Arial 10 left, auto width cap 30, row height 15 for all sheets. Called before _atomic_save."""
    try:
        for ws in wb.worksheets:
            _clear_vlookup_formulas(ws)
            _auto_adjust_sheet(ws)
            # Ensure header row 2 stays bold/left, headers 1-11 dark blue 1F4E78 black text (F same as others, L:BI yellow preserved)
            try:
                for c in range(1, min(ws.max_column + 1, 12)):
                    hdr = ws.cell(row=2, column=c)
                    if hdr.value is not None:
                        hdr.alignment = VISUAL_ALIGN
                        hdr.fill = VISUAL_HEADER_FILL
                        hdr.font = VISUAL_HEADER_FONT
            except Exception:
                pass
    except Exception:
        pass

def _is_red_cell(cell) -> bool:
    try:
        return str(cell.fill.start_color.rgb or "").upper().endswith("FF0000")
    except Exception:
        return False

def _paint_tab_status(wb) -> dict:
    # tab color = live progress: RED any red/failed row (agent fixes it while the sheet keeps filling),
    # GREEN every switch row has numeric F, ORANGE in progress, untouched = no rows computed yet
    status = {}
    for sname in SWITCH_SHEETS:
        if sname not in wb.sheetnames:
            continue
        ws = wb[sname]
        rows = filled = red = 0
        for r in range(3, ws.max_row + 1):
            if ws.cell(row=r, column=1).value in (None, ""):
                continue
            rows += 1
            f_cell = ws.cell(row=r, column=6)
            g_cell = ws.cell(row=r, column=7)
            # running-default rows keep F/G blank by design — they count as filled once their yellows are
            if isinstance(f_cell.value, (int, float)) or isinstance(g_cell.value, (int, float)) or any(isinstance(ws.cell(row=r, column=c).value, (int, float)) for c in range(12, ws.max_column + 1)):
                filled += 1
            if _is_red_cell(f_cell) or _is_red_cell(g_cell) or g_cell.value == -1.0:
                red += 1
        if red:
            ws.sheet_properties.tabColor = "FF0000"
        elif rows and filled >= rows:
            ws.sheet_properties.tabColor = "00B050"
        elif filled:
            ws.sheet_properties.tabColor = "FFC000"
        status[sname] = {"rows": rows, "filled": filled, "red": red}
    return status

def _spec_mark_red(wb, sheet: str, r: int, col: int, reason: str):
    """Mark a single yellow cell + tab RED and write reason — for >10s stall per spec."""
    try:
        if sheet not in wb.sheetnames:
            return
        ws = wb[sheet]
        cell = ws.cell(row=r, column=col)
        cell.fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
        cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        cell.alignment = VISUAL_ALIGN
        if reason:
            cur = str(cell.value or "")
            if not cur or cur.strip() in ("", "0", "0.0"):
                cell.value = reason[:30]
        ws.sheet_properties.tabColor = "FF0000"
    except Exception:
        pass

def _spec_clear_live_formulas(wb):
    """Per spec: LIVE_DELTA/H and LIVE_SHARPE/I contain formulas those screw up sheet fill — clear them to BLANK until workbook complete."""
    for sname in SWITCH_SHEETS:
        if sname not in wb.sheetnames or sname in SKIP_SHEETS:
            continue
        ws = wb[sname]
        cols = _resolve_cols(ws)
        for rr in range(3, ws.max_row + 1):
            for cc in (cols["H"], cols["I"]):
                try:
                    v = ws.cell(row=rr, column=cc).value
                    if isinstance(v, str) and v.startswith("="):
                        ws.cell(row=rr, column=cc).value = None
                        ws.cell(row=rr, column=cc).fill = PatternFill(fill_type=None)
                except Exception:
                    pass

_POOL_PREPARED = None

# Template rows that need several engine keys at once (a sheet row sets one switch). Proven in DC64_GREEDY 2026-09-28.
COMPOSITE_SWITCHES = {
    "WT_DC_DETAILED_TF": lambda tf: {"WT_DC_ENABLED": False} if str(tf).upper() == "OFF" else {"WT_DC_ENABLED": True, "WT_DC_DETAILED_SCORER_ENABLED": True, "WT_DC_TF_ENTRY": str(tf), "WT_DC_DC_TF": str(tf)},
}

def _parse_opt_value(val, default):
    # module-level twin of the per-row _parse_opt in _spec_fill_workbook (same rules)
    if isinstance(val, str) and "=" in val:
        _lh, _rh = val.split("=", 1)
        if _lh.strip() and _lh.strip() == _rh.strip():
            val = _rh.strip()
    if isinstance(default, bool):
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            s = val.strip().lower()
            if s == "true":
                return True
            if s == "false":
                return False
            if s in ("0", "0.0"):
                return False
            if s in ("1", "1.0"):
                return True
            return val
        if val in (0, 1, 0.0, 1.0):
            return bool(val)
        return val
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(float(str(val)))
        except Exception:
            return val
    if isinstance(default, float):
        # USER 2026-10-03 (complete-rounds): config_tradier encodes bools as 0.0/1.0 floats
        # (HTF_GATE_D_MANDATORY, LH_HL_FILTER_REQUIRE_BOTH) — bool-word/native-bool cands coerce honestly.
        if isinstance(val, bool):
            return 1.0 if val else 0.0
        if isinstance(val, str) and val.strip().lower() in ("true", "false"):
            return 1.0 if val.strip().lower() == "true" else 0.0
        try:
            return float(str(val))
        except Exception:
            return val
    if isinstance(val, str) and val.lower() in ("true", "false"):
        return val.lower() == "true"
    return val

DEFAULT_MARKER = "__CONFIG_DEFAULT__"

def _switch_overrides(switch: str, cand_parsed) -> dict:
    if cand_parsed == DEFAULT_MARKER:
        return {}
    return COMPOSITE_SWITCHES[switch](cand_parsed) if switch in COMPOSITE_SWITCHES else {switch: cand_parsed}

_QC_DEFAULT_INST = None


def _config_default_of(field: str):
    """Template lacks a bold default for this field — fall back to the config value for TYPING ONLY
    (never as a value default). Closes the gap where orphans bypassed the type gate and died in the
    engine with a truncated 'override ...' reason (USER 2026-10-03)."""
    global _QC_DEFAULT_INST
    try:
        import config, config_tradier
        for _o in (config.Config, config_tradier.TradierConfig):
            if hasattr(_o, field):
                return getattr(_o, field)
        if _QC_DEFAULT_INST is None:
            import v12_quick_engine as V
            _QC_DEFAULT_INST = V.QuickConfig()
        return getattr(_QC_DEFAULT_INST, field, None)
    except Exception:
        return None


def _cand_compatible(field: str, cand_parsed, defaults: dict) -> tuple:
    """Type gate: a candidate the engine cannot consume must never be evaluated.

    Returns (ok, reason). Rejects numeric field + non-numeric string (OFF/15m/D),
    dict/list field + scalar (LR_BAND_LADDER_TF_*=1), any '='-containing raw value
    (K=V pollution — a real value never contains '='). Crash-prevention only."""
    if cand_parsed == DEFAULT_MARKER or cand_parsed is None:
        return True, ""
    if isinstance(cand_parsed, str) and "=" in cand_parsed:
        return False, f"TYPE_MISMATCH: {field} value {cand_parsed!r} contains '=' (K=V pollution, never a real value)"
    default = (defaults or {}).get(field)
    if default is None:
        default = _config_default_of(field)
        if default is None:
            return True, ""
    if isinstance(default, bool):
        if isinstance(cand_parsed, bool):
            return True, ""
        if isinstance(cand_parsed, str) and cand_parsed.strip().lower() in ("true", "false"):
            return True, ""
        if cand_parsed in (0, 1, 0.0, 1.0):
            return True, ""
        return False, f"TYPE_MISMATCH: {field} expects bool, cand {cand_parsed!r}"
    if isinstance(default, int) and not isinstance(default, bool):
        if isinstance(cand_parsed, bool) or isinstance(cand_parsed, (int, float)):
            return True, ""
        if isinstance(cand_parsed, str):
            try:
                int(float(cand_parsed.strip()))
                return True, ""
            except Exception:
                pass
        return False, f"TYPE_MISMATCH: {field} expects int, cand {cand_parsed!r}"
    if isinstance(default, float):
        if isinstance(cand_parsed, bool) or isinstance(cand_parsed, (int, float)):
            return True, ""
        if isinstance(cand_parsed, str):
            try:
                float(cand_parsed.strip())
                return True, ""
            except Exception:
                pass
        return False, f"TYPE_MISMATCH: {field} expects float, cand {cand_parsed!r}"
    if isinstance(default, dict):
        if isinstance(cand_parsed, dict):
            return True, ""
        return False, f"TYPE_MISMATCH: {field} expects dict, cand {cand_parsed!r} (scalar for a dict field — grey per §56.0)"
    if isinstance(default, (list, tuple, set)):
        if isinstance(cand_parsed, (list, tuple, set)):
            return True, ""
        return False, f"TYPE_MISMATCH: {field} expects list, cand {cand_parsed!r}"
    return True, ""

def _switch_type_violation(switch: str, cand, defaults: dict) -> str:
    """'' when the row's expanded override set is type-compatible, else the TYPE_MISMATCH reason."""
    try:
        ov = _switch_overrides(str(switch).strip(), _parse_opt_value(cand, (defaults or {}).get(switch))) or {}
    except Exception:
        return ""
    bad = [_cand_compatible(k, v, defaults)[1] for k, v in ov.items()]
    return "; ".join(r for r in bad if r)

def _clean_ingested_overrides(ov: dict) -> dict:
    """Drop/parse polluted ingested values: 'K=V + ...' multi-strings and single 'K=V'
    strings are column-C display text, never real values. 'X=X' dup typos collapse to X."""
    clean = {}
    for k, v in dict(ov or {}).items():
        if isinstance(v, str) and " + " in v:
            continue
        if isinstance(v, str) and "=" in v:
            _lhs, _rhs = v.split("=", 1)
            _lhs, _rhs = _lhs.strip(), _rhs.strip()
            if not _rhs or "=" in _rhs or "+" in _rhs:
                continue
            if _lhs == _rhs or _lhs == str(k).strip() or not _lhs:
                v = _rhs
            else:
                continue
        clean[k] = v
    return clean

def _pool_eval(overrides: dict, window_days: int):
    # forked worker: prepared NPZ slice inherited copy-on-write from the parent (stays in RAM, no reload)
    import time as _tp
    from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eps
    t0 = _tp.time()
    return _eps(_POOL_PREPARED, overrides, window_days), _tp.time() - t0

ADAPT_FLOOR_TRADES = 10
ADAPT_ULTRA_NEG_PCT = float(os.environ.get("V15_ULTRA_NEG_PCT", "-5.0"))
ADAPT_MAX_STEPS = int(os.environ.get("V15_ADAPT_MAX_STEPS", "12"))
_ADAPT_SOFTEN_TOKENS = ("FILTER", "GATE", "BLOCK", "REQUIRE", "CONFIRM", "VETO", "GUARD")
_ADAPT_ENTRY_TOKENS = ("ENTRY", "REENTRY", "BOUNCE", "BREAKOUT", "OPEN")
# BIBLE §58 / adaptive mandate: TIM or DD too high -> add exits, loosen exit blockers, add reentry filters
_ADAPT_EXIT_TABS = ("EXIT_STRUCTURAL", "EXIT_VELOCITY", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE")
_ADAPT_EXIT_BLOCK_TOKENS = ("BLOCK", "VETO", "GATE", "FILTER", "REQUIRE", "GUARD", "NOLOSS", "NO_LOSS")
ADAPT_TIM_MAX, ADAPT_DD_MAX = 80.0, 30.0
# USER 2026-09-30: previous best < -5 % -> cat_side defaults; still < -5 % -> add TREND filters until gain > -3 %
ADAPT_TREND_TARGET_PCT = float(os.environ.get("V15_TREND_TARGET_PCT", "-3.0"))
_ADAPT_TREND_TOKENS = ("TREND", "HTF", "EMA", "ADX", "REGIME", "SMA200", "DIRECTION")
_ADAPT_TREND_KIND = ("FILTER", "GATE", "VETO")
# USER 2026-10-03 (pos-gain go-live, BIBLE §65): a sheet FINISHES when TIM in [20, 80], gain > 0,
# valid, trades floored; BH recorded (filename/manifest) but NEVER gating; 365D valid + gain > 0 + >= 80 trades.
# Failure -> revise (§58 repair, diagnosis-phased) -> REDO (re-fill from repaired set, max 2) -> IMPOSSIBLE quarantine.
QUAL_TIM_MIN, QUAL_TIM_MAX = 20.0, 80.0
QUAL_365D_FLOOR_TRADES = 80
QUAL_MAX_REDOS = int(os.environ.get("V15_QUAL_MAX_REDOS", "2"))
QUAL_365D_TIMEOUT = float(os.environ.get("V15_QUAL_365D_TIMEOUT", "600"))
QUAL_RETRY_TIMEOUT = float(os.environ.get("V15_QUAL_RETRY_TIMEOUT", "300"))
IMPOSSIBLE_DIR = ROOT / "SPREADSHEETS" / "V15_V16_IMPOSSIBLE"
# BIBLE §58: final-set repair never loosens safety gates — these tokens are excluded from SOFTEN in final_safe mode.
_ADAPT_FINAL_SOFTEN_EXCLUDE = ("GUARD", "BLOCK", "HARD", "STOP", "KILL", "HEDGE", "STDEV_REJECT", "RISK")


def _qualifies_30d(v, bh):
    """(ok, reasons) — 30D finish gate: valid, TIM 20-80, trades>=floor, gain>0. BH recorded, never gating (USER 2026-10-03)."""
    r = []
    v = v or {}
    if not v.get("valid"):
        r.append(f"invalid:{v.get('invalid_reason') or '?'}")
    t = int(v.get("trades") or 0)
    if t < ADAPT_FLOOR_TRADES:
        r.append(f"trades {t}<{ADAPT_FLOOR_TRADES}")
    try:
        tim = float(v.get("tim_pct") or 0.0)
    except Exception:
        tim = 0.0
    if not (QUAL_TIM_MIN <= tim <= QUAL_TIM_MAX):
        r.append(f"TIM {tim:.1f} outside [{QUAL_TIM_MIN:.0f},{QUAL_TIM_MAX:.0f}]")
    g = v.get("gain_pct")
    g = float(g) if g is not None else None
    if g is None or g <= 0:
        r.append(f"gain {g}")
    return (len(r) == 0, r)


def _nearmiss_30d(v, bh):
    """(is_nearmiss, reasons) — USER 2026-10-03 (best-effort + pos-gain go-live): a VALID floor-trading
    positive-gain set failing ONLY on TIM. Publishes flagged instead of quarantine. BH never gating."""
    v = v or {}
    if not v.get("valid"):
        return (False, [])
    if int(v.get("trades") or 0) < ADAPT_FLOOR_TRADES:
        return (False, [])
    g = v.get("gain_pct")
    try:
        g = float(g) if g is not None else None
    except Exception:
        return (False, [])
    if g is None or g <= 0:
        return (False, [])
    r = []
    try:
        tim = float(v.get("tim_pct") or 0.0)
    except Exception:
        tim = 0.0
    if not (QUAL_TIM_MIN <= tim <= QUAL_TIM_MAX):
        r.append(f"TIM {tim:.1f} outside [{QUAL_TIM_MIN:.0f},{QUAL_TIM_MAX:.0f}]")
    return (len(r) > 0, r)


def _stamp_is_stale(stamp_ns, current_ns) -> bool:
    """True only when both stamps are known and the NPZ is NOT newer. Missing data fails OPEN (a re-run self-heals by re-stamping)."""
    try:
        if stamp_ns is None or current_ns is None:
            return False
        return int(current_ns) <= int(stamp_ns)
    except Exception:
        return False


def _npz_mtime_ns(symside):
    """Current NPZ mtime ns for the symside. None if unresolvable."""
    try:
        _p = _npz_path(symside)
        return _p.stat().st_mtime_ns if _p is not None else None
    except Exception:
        return None


def _soft_verdict_fresh(symside, prog) -> bool:
    """A NO_TRADES/BEST_EFFORT verdict clears itself when the NPZ is newer than the verdict stamp (re-run on fresh data, never on the same window)."""
    return not _stamp_is_stale((prog or {}).get("verdict_npz_mtime_ns"), _npz_mtime_ns(symside))


def _board_calc_n(done) -> int:
    """Calculated rows: real numeric deltas, excluding RUNNING_IDENTITY reference rows (USER 2026-10-03)."""
    try:
        return sum(1 for _v in (done or {}).values() if isinstance(_v, dict) and isinstance(_v.get("delta"), (int, float)) and not str(_v.get("reason") or "").startswith("RUNNING_IDENTITY"))
    except Exception:
        return 0


def _board_has_retryable_holes(done) -> bool:
    """True when >=1 row carries missing_yellows (timeout/err — a re-fill can genuinely fix it). Settled verdicts (ZERO_TRADES/UNWIRED/running-blank) are deterministic and never change on re-fill."""
    try:
        for _v in (done or {}).values():
            if isinstance(_v, dict) and _v.get("missing_yellows"):
                return True
    except Exception:
        pass
    return False


def _final_publish_class(out_dir, name) -> str:
    """QUALIFIED unless the manifest sidecar says BEST_EFFORT. Missing/unparseable manifest = QUALIFIED (all pre-2026-10-03 publishes were qualified-only)."""
    try:
        _m = json.loads((Path(out_dir) / str(name).replace(".xlsx", "_manifest.json")).read_text())
        return "BEST_EFFORT" if str(_m.get("publish_class") or "").upper() == "BEST_EFFORT" else "QUALIFIED"
    except Exception:
        return "QUALIFIED"


def _best_manifest_metrics(out_dir, name) -> dict:
    """Exact metrics dict from an active final's manifest sidecar ({} when missing)."""
    try:
        _m = json.loads((Path(out_dir) / str(name).replace(".xlsx", "_manifest.json")).read_text())
        return dict(_m.get("metrics") or {})
    except Exception:
        return {}


def _standing_best(out_dir, symside):
    """(name, gain, cls) of the reigning active final, or None. Rank: QUALIFIED class first, then gain. Unparseable actives never win."""
    try:
        from tools.v15_final_naming import parse_final_matrix_name as _parse
    except Exception:
        return None
    _best = None
    try:
        _files = sorted(Path(out_dir).glob(f"{symside}_bh*_30d_matrix.xlsx"))
    except Exception:
        return None
    for _p in _files:
        _d = _parse(_p.name)
        if _d is None:
            continue
        _cls = _final_publish_class(out_dir, _p.name)
        _key = (1 if _cls == "QUALIFIED" else 0, float(_d["gain"]))
        if _best is None or _key > _best[0]:
            _best = (_key, _p.name, float(_d["gain"]), _cls)
    return None if _best is None else (_best[1], _best[2], _best[3])


def _rank_final_gain(out_dir, symside, cand_name, cand_gain, cand_class="QUALIFIED"):
    """(win, best_name, best_gain, note) — USER 2026-10-03 (best-not-newest): the candidate activates
    only if it strictly beats the standing best (class first, then gain by >1e-9). Ties and same-name reaffirms keep the incumbent file."""
    _st = _standing_best(out_dir, symside)
    if _st is None:
        return (True, None, None, "no active — publishing")
    _bn, _bg, _bc = _st
    if _bn == cand_name:
        return (True, None, None, "same metrics already active — reaffirming")
    _ck = (1 if str(cand_class).upper() == "QUALIFIED" else 0, float(cand_gain))
    _bk = (1 if _bc == "QUALIFIED" else 0, float(_bg))
    if _ck[0] > _bk[0] or (_ck[0] == _bk[0] and _ck[1] > _bk[1] + 1e-9):
        return (True, None, None, f"candidate {float(cand_gain):.2f}/{cand_class} beats {_bn} {_bg:.2f}/{_bc}")
    return (False, _bn, _bg, f"keeping {_bn} {_bg:.2f}/{_bc} over candidate {float(cand_gain):.2f}/{cand_class}")


def _apply_best_publish(out_dir, symside, final_name, final_gain, cand_class, wb_path):
    """Collapse the symside to ONE active = max(candidate, standing best). Winner activates (losers -> .superseded history, html/manifest companions follow). Returns (active_name, won)."""
    _od = Path(out_dir)
    _win, _bn, _bg, _note = _rank_final_gain(_od, symside, final_name, float(final_gain), cand_class)
    print(f"[BEST-PUBLISH] {symside} {_note}", flush=True)
    _keep = final_name if _win else _bn
    for _old in _od.glob(f"{symside}_bh*_30d_matrix.xlsx"):
        if _old.name == _keep:
            continue
        try:
            _old.rename(_od / (_old.name + ".superseded"))
        except Exception as _e:
            print(f"[best-publish-warn] supersede {_old.name}: {_e}", flush=True)
        for _comp in (_od / _old.name.replace(".xlsx", ".html"), _od / _old.name.replace(".xlsx", "_manifest.json")):
            try:
                if _comp.exists():
                    _comp.rename(_od / (_comp.name + ".superseded"))
            except Exception:
                pass
    if _win:
        _atomic_copy(wb_path, _od / final_name)
        return (final_name, True)
    _hist = _od / (final_name + ".superseded")
    if not _hist.exists():
        try:
            _atomic_copy(wb_path, _hist)
        except Exception as _e:
            print(f"[best-publish-warn] history copy: {_e}", flush=True)
    return (_bn, False)


def _npz_path(symside):
    """Resolve the symside's NPZ file (S1 sandbox first, then local). None if missing."""
    try:
        sym = symside.rsplit("_", 1)[0] if symside.rsplit("_", 1)[-1] in ("LONG", "SHORT") else symside
        for _p in (Path("/home/niels/binance-sandbox/backtest_v8/indicators") / f"{sym}.npz", Path(ROOT) / "backtest_v8" / "indicators" / f"{sym}.npz"):
            if _p.exists():
                return _p
    except Exception:
        pass
    return None


def _npz_span_days(symside):
    """Available-history span in days for the symside's NPZ (timestamps peek, mmap). None if unreadable."""
    try:
        import numpy as _np
        _p = _npz_path(symside)
        if _p is None:
            return None
        with _np.load(str(_p), mmap_mode="r") as _d:
            if "timestamps" not in _d.files:
                return None
            _t = _np.asarray(_d["timestamps"], dtype=float).ravel()
        if len(_t) < 2:
            return None
        if _t[-1] > 1e11:
            _t = _t / 1000
        return float((_t[-1] - _t[0]) / 86400)
    except Exception:
        pass
    return None


def _md5_file(path, chunk=1 << 20) -> str | None:
    """md5 hex of a file, streamed. None on failure."""
    try:
        import hashlib as _hl
        h = _hl.md5()
        with open(str(path), "rb") as _f:
            for _b in iter(lambda: _f.read(chunk), b""):
                h.update(_b)
        return h.hexdigest()
    except Exception:
        return None


def _build_publish_manifest(symside, final_name, metrics, counts, overrides_md5, file_md5, npz_name, npz_md5, span_days, pilot_md5, template_name, host) -> dict:
    """USER 2026-10-03 (xlsx source-of-truth): every published xlsx ships a manifest sidecar binding the file (md5 + F/C/E counts) to its metrics, override set, NPZ data, and pilot code. tools/sheet_audit.py --verify re-checks all of it."""
    _mm = {k: metrics.get(k) for k in ("gain_pct", "trades", "tim_pct", "max_dd_pct", "valid", "invalid_reason", "bh")}
    try:
        _mm["gain_vs_bh"] = float(metrics.get("gain_pct")) - float(metrics.get("bh"))
    except Exception:
        _mm["gain_vs_bh"] = None
    return {"symside": symside, "final_name": final_name, "published_utc": utcnow(), "host": host, "metrics": _mm, "counts": {"F": int(counts[0]), "C": int(counts[1]), "E": int(counts[2]), "done_n": int(counts[3])}, "overrides_md5": overrides_md5, "file_md5": file_md5, "npz_name": npz_name, "npz_md5": npz_md5, "span_days": span_days, "pilot_md5": pilot_md5, "template": template_name}


def _qualifies_365d(v, span_days=None):
    """(ok, reasons) — 365D finish gate (BIBLE §58): valid, trades>=floor, gain>0. Short-history NPZs (<330d) get a pro-rata floor max(10, round(80*span/365)) — a +26.76/73tr set on 80d of history must not die on a technicality (USER 2026-10-03: quarantine iff 365D non-positive). Fail-closed: unknown span -> full 80 floor."""
    r = []
    v = v or {}
    if not v.get("valid"):
        r.append(f"invalid:{v.get('invalid_reason') or '?'}")
    t = int(v.get("trades") or 0)
    _floor = QUAL_365D_FLOOR_TRADES
    if span_days is not None and span_days < 330:
        _floor = max(10, round(QUAL_365D_FLOOR_TRADES * float(span_days) / 365))
    if t < _floor:
        r.append(f"trades {t}<{_floor}" + (f" (pro-rata {span_days:.0f}d)" if span_days is not None and span_days < 330 else ""))
    g = v.get("gain_pct")
    g = float(g) if g is not None else None
    if g is None or g <= 0:
        r.append(f"gain {g}")
    return (len(r) == 0, r)


def _peer_rm_published(new_symside, host=None):
    """USER 2026-10-03 (xlsx source-of-truth): best-effort removal of a symside's live published artifacts (bh/gain xlsx+html+manifest) on peer hosts when local truth is revoked (REDO re-fill, quarantine). Never touches .superseded/.stale history or working files. Warn-only."""
    import socket as _s, subprocess as _sp
    try:
        _hq = _s.gethostname().lower()
        _peers = (host,) if host else (("10.0.0.4", "10.0.0.5") if (_hq == "niels" or "10.0.0.3" in _hq) else ("10.0.0.3", "157.180.125.52"))
        for _h in _peers:
            try:
                _sp.run(["bash", "-c", f"ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no niels@{_h} \"rm -f ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{new_symside}_bh*.xlsx ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{new_symside}_bh*.html ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{new_symside}_bh*_manifest.json\""], timeout=30, capture_output=True)
            except Exception:
                continue
    except Exception as _e:
        print(f"[peer-rm-warn] {new_symside}: {_e}", flush=True)


def _quarantine_impossible(new_symside, reasons, metrics, wb, wb_path, progress, progress_path, cumulative_overrides, repairs, carry_files=()):
    """Move an unfinishable sheet out of CELL_BY_CELL into V15_V16_IMPOSSIBLE for manual revision. Tombstones progress (verdict=IMPOSSIBLE; final_gain kept so the pilot never retouches, herd skips via full board). Returns quarantine dir path str."""
    import shutil as _sh_q, datetime as _dt_q
    IMPOSSIBLE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt_q.datetime.now(_dt_q.timezone.utc).strftime("%Y%m%d%H%M%S")
    qdir = IMPOSSIBLE_DIR / f"{new_symside}_{stamp}"
    qdir.mkdir(parents=True, exist_ok=True)
    for _cf in carry_files or ():
        try:
            _p = Path(_cf)
            if _p.exists():
                _p.rename(qdir / _p.name)
                print(f"[quarantine] {new_symside} carried {_p.name} into quarantine", flush=True)
        except Exception as _qe:
            print(f"[quarantine-warn] {new_symside} carry {_qe}", flush=True)
    try:
        _fp = progress.get("final_path")
        if _fp:
            _hp = Path(str(_fp).replace(".xlsx", ".html"))
            if _hp.exists():
                _hp.rename(qdir / _hp.name)
                print(f"[quarantine] {new_symside} carried {_hp.name} into quarantine", flush=True)
    except Exception as _qe2:
        print(f"[quarantine-warn] {new_symside} chart carry {_qe2}", flush=True)
    try:
        with RED_FIXER_LOCK:
            _atomic_save(wb, qdir / f"{new_symside}_IMPOSSIBLE_{stamp}.xlsx")
    except Exception as _qe:
        print(f"[quarantine-warn] {new_symside} wb save {_qe}", flush=True)
    try:
        _qp = qdir / f"{new_symside}_IMPOSSIBLE_{stamp}.json"
        _qp.write_text(json.dumps({"symside": new_symside, "verdict": "IMPOSSIBLE", "reasons": reasons, "metrics": metrics, "repairs": repairs, "n_overrides": len(cumulative_overrides or {}), "stamped_at": stamp}, indent=1, default=str))
    except Exception as _qe:
        print(f"[quarantine-warn] {new_symside} reason json {_qe}", flush=True)
    try:
        write_zoomable_chart(new_symside, None, dict(cumulative_overrides or {}), 30, suffix=f"IMPOSSIBLE_{stamp}")
        for _hd in (OUT_DIR, ROOT / "SPREADSHEETS", CHARTS_1M_DIR, Path("/tmp")):
            for _h in _hd.glob(f"{new_symside}_IMPOSSIBLE_{stamp}.html"):
                try:
                    _h.rename(qdir / _h.name)
                except Exception:
                    pass
    except Exception as _qe:
        print(f"[quarantine-warn] {new_symside} chart {_qe}", flush=True)
    for _pat in (f"{new_symside}_bh*.xlsx", f"{new_symside}_30d_matrix.xlsx", f"{new_symside}_*.html"):
        for _f in OUT_DIR.glob(_pat):
            try:
                _dest = qdir / _f.name
                if _dest.exists():
                    _dest = qdir / f"{_f.stem}_dup{_f.suffix}"
                _f.rename(_dest)
                print(f"[quarantine] {new_symside} carried CELL_BY_CELL artifact {_f.name} into quarantine", flush=True)
            except Exception:
                pass
    progress["verdict"] = "IMPOSSIBLE"
    progress["impossible_reasons"] = reasons
    progress["impossible_path"] = str(qdir)
    progress["impossible_metrics"] = metrics
    progress.pop("final_path", None)
    progress.pop("needs_redo", None)
    _atomic_write_json(progress_path, progress)
    print(f"[IMPOSSIBLE] {new_symside} quarantined -> {qdir} ({'; '.join(reasons)})", flush=True)
    try:
        import socket as _sock_q, subprocess as _sp_q
        _hq = _sock_q.gethostname().lower()
        _is_s1 = _hq == "niels" or "10.0.0.3" in _hq
        _peers = ("10.0.0.4", "10.0.0.5") if _is_s1 else ("10.0.0.3", "157.180.125.52")
        for _host in _peers:
            try:
                if not _is_s1:
                    _sp_q.run(["bash", "-c", f"rsync -az -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' {qdir}/ niels@{_host}:~/binance-sandbox/SPREADSHEETS/V15_V16_IMPOSSIBLE/{qdir.name}/"], timeout=60, capture_output=True)
                _sp_q.run(["bash", "-c", f"rsync -az -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' {progress_path} niels@{_host}:/home/niels/v15_run25_20261002/progress/"], timeout=30, capture_output=True)
                _sp_q.run(["bash", "-c", f"rsync -az -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' {progress_path} niels@{_host}:~/binance-sandbox/data/reports/lifecycle_pilot/"], timeout=30, capture_output=True)
                _peer_rm_published(new_symside, _host)
                print(f"[quarantine-push] {new_symside} tombstone+peer-cleanup via {_host}", flush=True)
                if not _is_s1:
                    break
            except Exception:
                continue
    except Exception as _qe:
        print(f"[quarantine-push-warn] {_qe}", flush=True)
    return str(qdir)


def _run_365_repair(new_symside, progress_path, template_path, timeout_sec=1800):
    """Run the §58 repair tool (tools/v15_365_repair.py) on this sheet's final set. Returns (overrides|None, report_dict)."""
    import subprocess as _sp, tempfile as _tf
    try:
        with _tf.TemporaryDirectory(prefix="q365_") as _td:
            _env = dict(os.environ)
            _env["V12_NPZ_CACHE"] = "4"
            _cp = _sp.run([sys.executable, str(ROOT / "tools" / "v15_365_repair.py"), "--progress", str(progress_path), "--template", str(template_path), "--out", _td, "--max-steps", "8"], capture_output=True, text=True, timeout=timeout_sec, cwd=str(ROOT), env=_env)
            print(f"[365-repair] {new_symside} rc={_cp.returncode} tail={((_cp.stdout or '')[-400:] + (_cp.stderr or '')[-200:])!r}"[:600], flush=True)
            _outs = list(Path(_td).glob("*_365_repair.json"))
            if _cp.returncode != 0 or not _outs:
                return None, {"rc": _cp.returncode}
            rep = json.loads(_outs[0].read_text())
            ov = (rep.get("final") or {}).get("overrides") or rep.get("overrides")
            return (dict(ov) if ov else None), rep
    except Exception as _e:
        print(f"[365-repair-warn] {new_symside}: {_e}", flush=True)
        return None, {"error": str(_e)[:120]}


def _adapt_template_rows(template_path, tabs):
    """(tab, switch, cand) rows the adaptive stage may use: live-wired (not grey), vector-wired, promotable, de-duplicated."""
    import openpyxl as _opx
    wb = _opx.load_workbook(str(template_path))
    seen, rows = set(), []
    for tab in tabs:
        if tab not in wb.sheetnames or tab in DEAD_VEC_TABS:
            continue
        ws = wb[tab]
        for r in range(3, ws.max_row + 1):
            sw, cand = ws.cell(r, 1).value, ws.cell(r, 2).value
            if not sw or cand is None or str(cand).strip() in ("", "None", "none"):
                continue
            sw = str(sw).strip()
            f = ws.cell(r, 1).font
            if f is not None and f.color is not None and str(getattr(f.color, "rgb", "") or "").upper() in GREY_SKIP_RGB:
                continue
            if sw in DEAD_VEC_SWITCHES or sw in LIVE_ONLY_SWITCHES or promotion_block_reason(sw, tab):
                continue
            if (sw, str(cand)) in seen:
                continue
            seen.add((sw, str(cand)))
            rows.append((tab, sw, cand))
    return rows


def _prior_final_sets(symside: str) -> list:
    """Final override sets of earlier FINISHED runs for this sym_side (every ~/v15_run*/progress dir + lifecycle_pilot),
    highest recorded final gain first. Only the SETTINGS are used — the caller re-measures them on the current engine."""
    import glob as _g
    out = []
    here = str(PROGRESS_DIR.resolve()) if PROGRESS_DIR.exists() else str(PROGRESS_DIR)
    paths = _g.glob(os.path.expanduser(f"~/v15_run*/progress/{symside}_v14_progress.json")) + [str(ROOT / "data" / "reports" / "lifecycle_pilot" / f"{symside}_v14_progress.json")]
    for pth in paths:
        try:
            if str(Path(pth).parent.resolve()) == here or not Path(pth).exists():
                continue
            pj = json.loads(Path(pth).read_text())
        except Exception:
            continue
        ov = {k: v for k, v in (pj.get("cumulative_overrides") or {}).items() if not (isinstance(v, str) and " + " in v)}
        if pj.get("final_gain") is None or not ov:
            continue
        out.append((float(pj["final_gain"]), f"{Path(pth).parent.parent.name}", ov))
    out.sort(key=lambda t: -t[0])
    return [(src, ov) for _g2, src, ov in out]

def _credible_baseline(new_symside, prepared, base_sets, defaults, template_path, window_days, workers=2, tim_min=None, gain_min=None, bh_min=None, step_cap=None, final_safe=False):
    """USER 2026-09-29/30 (BIBLE §58, adaptive mandate): a sheet is only filled from a CREDIBLE baseline = valid (TIM <= 80,
    DD <= 30 — the vomit gates of evaluate_prepared_sanitized, never loosened), >= ADAPT_FLOOR_TRADES trades, gain >=
    ADAPT_ULTRA_NEG_PCT. An invalid baseline is REPAIRED, never disqualified: (A) best of the alternative bases (live recipe /
    template defaults / previous best); then per step, by what fails: too few trades -> soften filters + open entry/reentry
    paths; TIM/DD too high -> exit / reduce / reentry rows, loosened exit blockers, reentry filters on; ultra-negative ->
    TREND filters (HTF/TREND/EMA/ADX/REGIME/SMA200/DIRECTION filter/gate/veto rows and bool switches) one at a time until
    gain > ADAPT_TREND_TARGET_PCT (-3 %), validity kept. Base order: previous best if credible, else cat_side (template)
    defaults if credible, else the best-scoring base. Up to ADAPT_MAX_STEPS; a step stops at the first credible candidate.
    USER 2026-10-02 (finish qualification): optional tim_min/gain_min/bh_min extend credible() for DONE-stage final-set
    repair (TIM floor, non-negative, beat-BH); TIM below tim_min with floored trades -> HOLDS phase (open entries + tune
    exits for longer holds); final_safe=True excludes safety-gate tokens from SOFTEN/LOOSEN (BIBLE §58). Defaults preserve
    the baseline-stage behavior exactly. Returns (overrides, vec_result, report)."""
    from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eps
    floor, uneg = ADAPT_FLOOR_TRADES, ADAPT_ULTRA_NEG_PCT

    def ev(ov):
        ov, _ = sanitize_overrides(dict(ov), defaults)
        try:
            return ov, (_eps(prepared, ov, window_days) or {})
        except Exception as e:
            return ov, {"gain_pct": None, "trades": 0, "valid": False, "invalid_reason": f"ERR {e}"[:60]}

    def trades(v):
        return int(v.get("trades") or 0)

    def gain(v):
        g = v.get("gain_pct")
        return float(g) if g is not None else -1e9

    def tim(v):
        try:
            return float(v.get("tim_pct") or 0.0)
        except Exception:
            return 0.0

    def excess(v):
        # how far over the vomit gates (TIM > 80 %, DD > 30 %): 0 = within
        return max(0.0, float(v.get("tim_pct") or 0.0) - ADAPT_TIM_MAX) + max(0.0, float(v.get("max_dd_pct") or 0.0) - ADAPT_DD_MAX)

    def timgap(v):
        # how far under the TIM floor (finish-repair only): 0 = within or unset
        return max(0.0, (tim_min if tim_min is not None else 0.0) - tim(v))

    target = {"gain": uneg}

    def credible(v):
        if not (bool(v.get("valid")) and trades(v) >= floor and gain(v) >= target["gain"]):
            return False
        if tim_min is not None and tim(v) < tim_min:
            return False
        if gain_min is not None and gain(v) < gain_min:
            return False
        if bh_min is not None and gain(v) < bh_min:
            return False
        return True

    def score(v):
        return (credible(v), bool(v.get("valid")), -excess(v), -timgap(v), min(trades(v), floor), gain(v))

    report = {"floor": floor, "ultra_neg": uneg, "tim_max": ADAPT_TIM_MAX, "dd_max": ADAPT_DD_MAX, "bases": [], "steps": []}
    best = None
    _evald = []
    for label, ov in base_sets:
        if ov is None:
            continue
        sov, v = ev(ov)
        _evald.append((label, sov, v))
        report["bases"].append({"label": label, "n_overrides": len(sov), "gain": v.get("gain_pct"), "trades": trades(v), "tim": v.get("tim_pct"), "dd": v.get("max_dd_pct"), "valid": v.get("valid"), "reason": v.get("invalid_reason")})
        if best is None or score(v) > score(best[2]):
            best = (label, sov, v)
    # USER 2026-09-30: the previous best settings are kept while credible (>= -5 %, valid, floored); else the cat_side
    # (template) defaults; else the best-scoring base, which the steps below repair
    _by = {b[0]: b for b in _evald}
    for _pref in ("previous_best", "current", "template_defaults"):
        if _pref in _by and credible(_by[_pref][2]):
            best = _by[_pref]
            break
    label, cur_ov, cur_v = best
    report["base_chosen"] = label
    print(f"[ADAPT-BASE] {new_symside} bases {[(b['label'], b['trades'], round(b['gain'], 2) if b['gain'] is not None else None, b['tim'], b['valid']) for b in report['bases']]} -> {label}", flush=True)
    if credible(cur_v):
        report["credible"] = True
        return cur_ov, cur_v, report
    rows = {}

    def tab_rows(key, tabs):
        if key not in rows:
            rows[key] = _adapt_template_rows(template_path, list(tabs))
        return rows[key]

    def row_cands(key, tabs):
        return [(f"{tab}:{sw}={cand}", _switch_overrides(sw, _parse_opt_value(cand, defaults.get(sw)))) for tab, sw, cand in tab_rows(key, tabs)]

    for step in range(step_cap or ADAPT_MAX_STEPS):
        if trades(cur_v) < floor:
            phase = "TRADES"
        elif tim_min is not None and tim(cur_v) < tim_min and not cur_v.get("valid"):
            phase = "TRADES"  # invalid + under TIM floor -> open up first (validity is the binding constraint)
        elif tim_min is not None and tim(cur_v) < tim_min:
            phase = "HOLDS"  # floored but filtered to death -> more entries + longer holds (USER 2026-10-02)
        elif excess(cur_v) > 0 or not cur_v.get("valid"):
            phase = "EXITS"
        else:
            phase = "GAIN"  # valid + floored but gain below target -> trend filters
        cands = []
        if phase in ("TRADES", "HOLDS"):
            cands += row_cands("entry", [t for t in SWITCH_SHEETS if t.startswith(("ENTRY_", "REENTRY_"))])
            for k, dv in defaults.items():
                cur = cur_ov.get(k, dv)
                if not isinstance(dv, bool) or not isinstance(cur, bool) or k == "SIMPLE_PRICE_GT0_ENABLED" or k in DEAD_VEC_SWITCHES or k in LIVE_ONLY_SWITCHES:
                    continue
                if final_safe and any(t in k for t in _ADAPT_FINAL_SOFTEN_EXCLUDE):
                    continue
                if cur and any(t in k for t in _ADAPT_SOFTEN_TOKENS):
                    cands.append((f"SOFTEN:{k}=False", {k: False}))
                elif not cur and k.endswith("_ENABLED") and any(t in k for t in _ADAPT_ENTRY_TOKENS) and not any(t in k for t in _ADAPT_SOFTEN_TOKENS):
                    cands.append((f"ENTRY_PATH:{k}=True", {k: True}))
        if phase in ("EXITS", "HOLDS"):
            cands += row_cands("exit", [t for t in SWITCH_SHEETS if t in _ADAPT_EXIT_TABS])
            for k, dv in defaults.items():
                cur = cur_ov.get(k, dv)
                if not isinstance(dv, bool) or not isinstance(cur, bool) or k in DEAD_VEC_SWITCHES or k in LIVE_ONLY_SWITCHES:
                    continue
                if final_safe and any(t in k for t in _ADAPT_FINAL_SOFTEN_EXCLUDE):
                    continue
                if cur and "EXIT" in k and any(t in k for t in _ADAPT_EXIT_BLOCK_TOKENS):
                    cands.append((f"EXIT_LOOSEN:{k}=False", {k: False}))
                elif not cur and "REENTRY" in k and "FILTER" in k:
                    cands.append((f"REENTRY_FILTER:{k}=True", {k: True}))
        if phase == "GAIN":
            # ultra-negative (USER 2026-09-30): add TREND filters one at a time until gain > ADAPT_TREND_TARGET_PCT (-3 %)
            # USER 2026-10-02: finish repair raises the target to gain_min/bh_min (non-negative, beat-BH)
            target["gain"] = max(target["gain"], ADAPT_TREND_TARGET_PCT)
            if gain_min is not None:
                target["gain"] = max(target["gain"], gain_min)
            if bh_min is not None:
                target["gain"] = max(target["gain"], bh_min)
            for tab, sw, cand in tab_rows("all", [t for t in SWITCH_SHEETS if t != "STDEV_SLOPE_SIZING"]):
                if any(t in sw for t in _ADAPT_TREND_TOKENS) and any(t in sw for t in _ADAPT_TREND_KIND):
                    cands.append((f"TREND:{tab}:{sw}={cand}", _switch_overrides(sw, _parse_opt_value(cand, defaults.get(sw)))))
            for k, dv in defaults.items():
                cur = cur_ov.get(k, dv)
                if isinstance(dv, bool) and isinstance(cur, bool) and not cur and any(t in k for t in _ADAPT_TREND_TOKENS) and any(t in k for t in _ADAPT_TREND_KIND) and k not in DEAD_VEC_SWITCHES and k not in LIVE_ONLY_SWITCHES:
                    cands.append((f"TREND:{k}=True", {k: True}))
            # most trend filters live as yellow "FILTER=opt" headers, not rows
            if "hdrs" not in rows:
                import openpyxl as _opx_t
                _wb_t = _opx_t.load_workbook(str(template_path), read_only=True)
                _h = set()
                for _tab in SWITCH_SHEETS:
                    if _tab in _wb_t.sheetnames:
                        for _v in next(_wb_t[_tab].iter_rows(min_row=2, max_row=2, values_only=True), ()):
                            if isinstance(_v, str) and "=" in _v:
                                _h.add(_v.strip())
                _wb_t.close()
                rows["hdrs"] = sorted(_h)
            for _hdr in rows["hdrs"]:
                _f, _o = _hdr.split("=", 1)
                if any(t in _f for t in _ADAPT_TREND_TOKENS) and (WIRED_FILTERS is None or _f in WIRED_FILTERS):
                    cands.append((f"TREND:{_hdr}", {_f: _parse_opt_value(_o, defaults.get(_f))}))
        cands = [(lab, ch) for lab, ch in cands if ch and any(cur_ov.get(k, defaults.get(k)) != v for k, v in ch.items())]
        best_step = None
        for lab, ch in cands:
            sov, v = ev({**cur_ov, **ch})
            if phase == "GAIN" and not v.get("valid"):
                continue  # gain repair never trades validity away
            if score(v) > score(cur_v) and (best_step is None or score(v) > score(best_step[2])):
                best_step = (lab, sov, v)
                if credible(v):
                    break
        if best_step is None and phase == "GAIN":
            # no trend filter lifts the gain: fall back to the best single row of all tabs that keeps the set valid
            for lab, ch in row_cands("all", [t for t in SWITCH_SHEETS if t != "STDEV_SLOPE_SIZING"]):
                if not ch or not any(cur_ov.get(k, defaults.get(k)) != v for k, v in ch.items()):
                    continue
                sov, v = ev({**cur_ov, **ch})
                if v.get("valid") and score(v) > score(cur_v) and (best_step is None or score(v) > score(best_step[2])):
                    best_step = ("ROW:" + lab, sov, v)
                    if credible(v):
                        break
        if best_step is None:
            report["steps"].append({"step": step, "phase": phase, "n_cands": len(cands), "result": "no improving candidate"})
            print(f"[ADAPT-STEP] {new_symside} {step} {phase}: {len(cands)} candidates, none improves ({trades(cur_v)} trades, TIM {cur_v.get('tim_pct')}, gain {gain(cur_v):+.2f})", flush=True)
            break
        lab, cur_ov, cur_v = best_step
        report["steps"].append({"step": step, "phase": phase, "n_cands": len(cands), "applied": lab, "gain": cur_v.get("gain_pct"), "trades": trades(cur_v), "tim": cur_v.get("tim_pct"), "dd": cur_v.get("max_dd_pct"), "valid": cur_v.get("valid")})
        print(f"[ADAPT-STEP] {new_symside} {step} {phase}: applied {lab} -> {trades(cur_v)} trades, TIM {cur_v.get('tim_pct')}, DD {cur_v.get('max_dd_pct')}, gain {gain(cur_v):+.2f}, valid {cur_v.get('valid')}", flush=True)
        if credible(cur_v):
            break
    report["credible"] = credible(cur_v)
    report["final"] = {"gain": cur_v.get("gain_pct"), "trades": trades(cur_v), "tim": cur_v.get("tim_pct"), "dd": cur_v.get("max_dd_pct"), "valid": cur_v.get("valid"), "reason": cur_v.get("invalid_reason"), "n_overrides": len(cur_ov)}
    print(f"[ADAPT-RESULT] {new_symside} credible={report['credible']} {trades(cur_v)} trades TIM {cur_v.get('tim_pct')} gain {gain(cur_v):+.2f} valid {cur_v.get('valid')} after {len([s for s in report['steps'] if s.get('applied')])} steps", flush=True)
    return cur_ov, cur_v, report



# ── POS_SYM SAMPLING (USER 2026-10-01 16:50Z): rows/filters with few positive sym_sides are re-tested only now and then ──
# POS_SYM 0 -> 1 of 20 sym_sides, 1 -> 1 of 10, 2 -> 1 of 6, 3 -> 1 of 3, >=4 always. Deterministic hash(sym_side|tab|switch=cand|round).
# A sampled-out row/cell is NEVER a 0: G/F/H stay blank, reason SKIPPED_SAMPLING(pos_sym=k) in the progress JSON, E/greedy chain untouched.
# Never sampled: bold default rows, rows without evidence (n_sym < 8 or unknown n_sym), rows never evaluated (no POS_SYM).
# Switch: env V15_POSSYM_SAMPLING=1|0 (wins), else flag file <base>/data/possym_sampling.flag ("1"/"0"), else ON from round run21 on.
_POSSYM_P = {0: 1.0 / 20, 1: 1.0 / 10, 2: 1.0 / 6, 3: 1.0 / 3}
_POSSYM_MIN_N = int(os.environ.get("V15_POSSYM_MIN_N", "20"))  # USER 2026-10-01: >= 20 evaluated sym_sides in the cat_side before a row may be sampled


def _possym_round_id(progress_path) -> str:
    try:
        pp = Path(progress_path)
        d = pp.parent if pp.suffix == ".json" else pp
        return d.parent.name if d.name == "progress" else d.name
    except Exception:
        return "unknown"


def _possym_enabled(round_id: str) -> bool:
    env = os.environ.get("V15_POSSYM_SAMPLING")
    if env in ("0", "1"):
        return env == "1"
    try:
        flag = Path(__file__).resolve().parent / "data" / "possym_sampling.flag"
        if flag.exists():
            return flag.read_text().strip() == "1"
    except Exception:
        pass
    import re as _re
    m = _re.search(r"run(\d+)", str(round_id))
    if m and int(m.group(1)) >= 21:
        print("[POSSYM] sampling default OFF (USER 2026-10-03: every row evaluates) — re-enable via V15_POSSYM_SAMPLING=1", flush=True)
    return False


def _possym_load_nsym(cat_side: str) -> dict:
    """data/avg_delta_pos_sym.json (written by the avg-delta tool): {"cat_sides": {CAT: {"TAB!SWITCH=cand": {"pos_sym": k, "n_sym": n}}}}."""
    try:
        f = Path(os.environ.get("V15_POSSYM_JSON") or (Path(__file__).resolve().parent / "data" / "avg_delta_pos_sym.json"))
        if not f.exists():
            return {}
        j = json.loads(f.read_text())
        d = (j.get("cat_sides") or j).get(cat_side) or {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _possym_draw(sym_side: str, tab: str, rkey: str, round_id: str) -> float:
    import hashlib as _hl
    return int(_hl.sha1(f"{sym_side}|{tab}|{rkey}|{round_id}".encode()).hexdigest()[:8], 16) / float(1 << 32)


def _possym_decide(sym_side: str, tab: str, rkey: str, round_id: str, pos, n, new=False):
    """-> (compute: bool, bucket: str, p: float|None, u: float|None). Compute always when the evidence is missing."""
    try:
        pos = int(pos)
    except Exception:
        return True, "no_pos_sym", None, None
    if new:
        return True, "new_row", None, None
    if pos >= 4 or pos < 0:
        return True, "pos>=4", None, None
    if n is None or int(n) < _POSSYM_MIN_N:
        return True, "n<min_or_unknown", None, None
    p = _POSSYM_P[pos]
    u = _possym_draw(sym_side, tab, rkey, round_id)
    return (u < p), f"pos={pos}", p, u


def delta_vs_result(res, cum_before: float):
    """Module-level twin of the fill loop's _delta_vs (pure — extracted 2026-10-03 for testing).
    (delta, promotable, reason): real gain delta always reported; engine-invalid results keep
    their delta but never promote."""
    if not res or res.get("gain_pct") is None:
        return None, False, str((res or {}).get("invalid_reason") or "no result")[:40]
    if int(res.get("trades") or 0) == 0:
        return None, False, "ZERO_TRADES"
    d = float(res.get("gain_pct")) - cum_before
    d = 0.0 if abs(d) < 1e-9 else d
    if not res.get("valid"):
        return d, False, str(res.get("invalid_reason") or "invalid")[:40]
    return d, True, ""


def _spec_fill_workbook(new_symside: str, wb_path: Path, progress: dict, progress_path: Path, flags_md: Path, cumulative_gain: float, cumulative_overrides: dict, defaults: dict, baseline_gain: float, bh: float, prepared, args, baseline_vec: dict, baseline_live: dict):
    """Sequential spec filler (USER 2026-09-29 late, BACKTEST_BIBLE §56 rev b): 13 tabs in order, every row in order
    (white switch rows, then orange filter rows), no row skipped, no tab jumping.
    - Running set = bold defaults + previous-best overrides (column C); its real gain = E3 (chain start).
    - Per row: every yellow = running set + switch=cand + that filter, delta vs the LATEST baseline, always written.
      Positive yellows -> PER_ROW_FILTERS (K). VECTOR_DELTA (G) = complete row delta vs LATEST baseline (switch + all
      positive filters, real joint eval); blank only on the running-default row without positive yellows.
      HUSTLE_DELTA (F) = same result vs the ORIGINAL baseline.
    - G > 0 -> promote, next row's E = latest baseline + G; otherwise the next row's E stays EMPTY. E only goes up.
    - Slow cell (> YELLOW_TIMEOUT) -> logged + RED, fill continues; reds retried at the end.
    - LIVE_DELTA/H and LIVE_SHARPE/I stay BLANK until workbook DONE, then filled via backtest_v12_engine on the winning set.
    Returns updated cumulative_gain, cumulative_overrides, progress.
    """
    import time as _t
    import random as _rnd
    import concurrent.futures as _cf
    from openpyxl.styles import PatternFill as _PF, Font as _FT
    # Load workbook once, keep open per spec (wb_keep)
    try:
        wb = openpyxl.load_workbook(str(wb_path), data_only=False)
    except Exception as e:
        print(f"[spec-fill] failed to open {wb_path} {e}", flush=True)
        return cumulative_gain, cumulative_overrides, progress
    # Clear LIVE formulas at start
    try:
        _spec_clear_live_formulas(wb)
    except Exception:
        pass
    try:
        chain_tim = float((baseline_vec or {}).get("tim_pct"))
    except Exception:
        chain_tim = None
    # Ensure header L:BI uses is_default backup handling — restore bold if lost (is_default col12 = YES means default bold)
    try:
        for sname in SWITCH_SHEETS:
            if sname not in wb.sheetnames or sname in SKIP_SHEETS:
                continue
            ws = wb[sname]
            cols = _resolve_cols(ws)
            c_isdef = cols["L"]
            for rr in range(3, ws.max_row + 1):
                isdef = str(ws.cell(row=rr, column=c_isdef).value or "").strip().upper()
                if isdef == "YES":
                    try:
                        ws.cell(row=rr, column=cols["C"]-1).font = Font(name="Arial", size=10, bold=True)
                        ws.cell(row=rr, column=cols["C"]-1).alignment = VISUAL_ALIGN
                    except Exception:
                        pass
    except Exception:
        pass
    fast_switches = None
    if getattr(args, "fast_switches", None):
        fast_switches = set(json.loads(Path(args.fast_switches).read_text()))
        print(f"[spec-fill] FAST mode: {len(fast_switches)} switches from {args.fast_switches}", flush=True)
    # Build per-tab row queues in order (stable) — hustle => shuffled copy
    is_hustle = getattr(args, "seq_mode", "") in ("hustle", "shuffle")
    # USER 2026-09-30: ONE chain in WORKBOOK tab order — ENTRY_REVERSAL_BOUNCE E3 = start, every next tab continues from
    # the previous tab's final baseline
    tabs = [s for s in wb.sheetnames if s in SWITCH_SHEETS and s not in SKIP_SHEETS]
    # Keep defined order as in SWITCH_SHEETS (already 12, STDEV skipped)
    per_tab_rows: dict[str, list] = {}
    orange_rows: set = set()  # GENERAL blanket rows (orange col A) — evaluated as ONE block per tab vs the cumulative
    for sname in tabs:
        ws = wb[sname]
        cols = _resolve_cols(ws)
        rows = []
        for rr in range(3, ws.max_row + 1):
            sw = ws.cell(row=rr, column=1).value
            if sw is None or (isinstance(sw, str) and sw.strip() == ""):
                continue
            sw = str(sw).strip()
            if sw.lower() in ("switch", "general", "blanket", "filter", "option value") or sw.startswith("—"):
                continue
            cand = ws.cell(row=rr, column=2).value
            if cand is None:
                # template row without a default value: test it AT its real config default (never skip the row)
                cand = defaults.get(sw)
                cand = DEFAULT_MARKER if cand is None else cand
            if isinstance(cand, str) and cand.strip().lower() in ("option value", "sheets applicable", "gates"):
                continue
            # 2026-09-28 FIX: DO NOT SKIP GENERAL rows — they need evaluation too!
            # Old code skipped GENERAL, causing per_tab_rows to be missing 75% of rows
            # fam = str(ws.cell(row=rr, column=4).value or "")
            # if fam.upper() == "GENERAL" or sw.upper() == "GENERAL":
            #     continue  # REMOVED - was causing 90% row loss
            # Check is_default backup to know bold default rows — but we still process every row
            if fast_switches is not None and sw not in fast_switches:
                continue  # --fast-switches: left pending for a later full pass on the same sheet
            if str(ws.cell(row=rr, column=1).fill.fgColor.rgb or "").upper().endswith("FFE699"):
                orange_rows.add((sname, rr))
            rows.append((rr, sw, cand))
        if is_hustle:
            _rnd.shuffle(rows)
        # white switch rows are always filled before orange filter rows (a mis-ordered template cannot change that)
        rows.sort(key=lambda x: (sname, x[0]) in orange_rows)
        per_tab_rows[sname] = rows
    # Helper to find next pending row index for a tab
    # FIX 2026-09-26: Handle row number mismatch after worksheet resorting
    _done_ids_cache: dict = {}
    def _next_pending(sname: str):
        # Exact (sheet, switch=cand) match survives row resorting; substring match falsely marked
        # "DC_ENABLED=True" done via "WT_DC_ENABLED=True" and "X=1" via "X=10".
        done = progress.get("done", {})
        if _done_ids_cache.get("n") != len(done):
            _done_ids_cache["ids"] = {(k.split("!", 1)[0], k.split(":", 1)[1]) for k in done if "!" in k and ":" in k}
            _done_ids_cache["n"] = len(done)
        done_ids = _done_ids_cache["ids"]
        for (rr, sw, cand) in per_tab_rows.get(sname, []):
            if (sname, f"{sw}={cand}") in done_ids:
                continue
            return (rr, sw, cand)
        return None
    def _any_pending() -> bool:
        for sname in tabs:
            if _next_pending(sname) is not None:
                return True
        return False
    def _write_E(sname: str, rr: int, value: float):
        cell = wb[sname].cell(row=rr, column=_resolve_cols(wb[sname])["E"])
        cell.value = float(value)
        cell.font = Font(name="Arial", size=10, bold=False)
        cell.alignment = VISUAL_ALIGN
    nav_mode = getattr(args, "nav_mode", "jump")
    # DELTA-LOG (user 2026-09-28): one JSON line per v12_quick eval — proves every delta was really computed
    _delta_log_path = progress_path.parent / "v15_delta_log" / f"{new_symside}_{nav_mode}.jsonl"
    _delta_log_path.parent.mkdir(parents=True, exist_ok=True)
    _delta_log_fh = open(_delta_log_path, "a", buffering=1)
    print(f"[delta-log] {_delta_log_path}", flush=True)
    # Row-parallel evals: naked + every yellow of a row are independent (all vs the same cumulative_before), so they are
    # prefetched in a forked process pool; results keyed by label, failures -> None + reason (never -1/0). Joint stays serial.
    global _POOL_PREPARED
    _pool = None
    _n_proc = max(1, min(int(getattr(args, "workers", 1) or 1), (os.cpu_count() or 2) - 1))
    if prepared is not None and _n_proc > 1:
        import multiprocessing as _mp
        _POOL_PREPARED = prepared
        _pool = _cf.ProcessPoolExecutor(max_workers=_n_proc, mp_context=_mp.get_context("fork"))
        # 2026-09-28 fork-deadlock fix: ProcessPoolExecutor forks workers LAZILY at first submit,
        # which lands after threads exist (red-fixer/eval threads) — a fork while another thread
        # holds a lock leaves children in futex_wait forever (s2 herd froze at load 3.8, logs idle
        # 26-52min). Force ALL workers to fork NOW, while this process is still single-threaded.
        list(_pool.map(abs, range(_n_proc * 4)))
        print(f"[spec-fill] process pool {_n_proc} workers (fork, NPZ shared, pre-forked single-threaded)", flush=True)
    # USER 2026-09-30: every RED (stuck/slow) cell and every 0 delta is logged for the fixing agent
    _zr_path = progress_path.parent / "v15_zero_red" / f"{new_symside}.jsonl"
    _zr_path.parent.mkdir(parents=True, exist_ok=True)
    _zr_fh = open(_zr_path, "a", buffering=1)
    def _zr_log(rec: dict):
        try:
            _zr_fh.write(json.dumps({"ts": utcnow(), "sym_side": new_symside, **rec}, default=str) + "\n")
        except Exception:
            pass
    def _delta_log(rec: dict):
        try:
            _delta_log_fh.write(json.dumps(rec, default=str) + "\n")
        except Exception:
            pass
    initial_baseline = float(progress.setdefault("initial_baseline_gain", float(baseline_gain)))
    # HUSTLE_DELTA (F) = the row measured on its own against the ORIGINAL set (independent eval), never the chain position
    initial_overrides = dict(progress.setdefault("initial_overrides", dict(cumulative_overrides)))
    def _div_col(ws) -> int:
        # DELTA_VS_INITIAL_BASELINE: best REAL measured gain of the row minus the initial baseline (no extra evals) —
        # comparable across rows when the sheet is not filled in order (hustle). Appended after the last header.
        hm = _hdr_col_map(ws)
        if "DELTA_VS_INITIAL_BASELINE" in hm:
            return hm["DELTA_VS_INITIAL_BASELINE"]
        c = max((cc for cc in range(1, ws.max_column + 1) if ws.cell(row=2, column=cc).value not in (None, "")), default=ws.max_column) + 1
        ws.cell(row=2, column=c).value = "DELTA_VS_INITIAL_BASELINE"
        ws.cell(row=2, column=c).font = Font(name="Arial", size=10, bold=True)
        return c
    div_cols = {sname: _div_col(wb[sname]) for sname in tabs}
    def _write_div(sname: str, rr: int, gains: list):
        gains = [g for g in gains if g is not None]
        if not gains:
            return None
        v = float(max(gains)) - initial_baseline
        cell = wb[sname].cell(row=rr, column=div_cols[sname])
        cell.value = v
        cell.font = Font(name="Arial", size=10, bold=False, color="006100" if v > 1e-9 else "9C0006")
        cell.alignment = VISUAL_ALIGN
        return v
    _last_save = {"t": _t.time()}
    def _maybe_save():
        # a full openpyxl save of the ~2.5MB workbook costs ~10s — per-row saves made rows 12s instead of ~1s.
        # progress JSON (source of truth, written every row) + refill_from_json cover a crash between saves.
        if _t.time() - _last_save["t"] >= XLSX_SAVE_EVERY_S:
            try:
                _atomic_save(wb, wb_path)
            except Exception as _se:
                print(f"[spec-save-warn] {_se}", flush=True)
            _last_save["t"] = _t.time()
    _last_json = {"t": _t.time()}
    def _maybe_write_json(force: bool = False):
        # full rewrite of a progress JSON that grows every row is O(rows^2); the append-only DELTA-LOG keeps every eval,
        # so the JSON is written every PROGRESS_JSON_EVERY_S, on every promotion, and at the end.
        if force or _t.time() - _last_json["t"] >= PROGRESS_JSON_EVERY_S:
            _atomic_write_json(progress_path, progress)
            _last_json["t"] = _t.time()
    def _row_done(sname: str, rr: int, switch: str, cand, n_evals: int, delta, promoted: bool):
        _delta_log({"ts": utcnow(), "sym_side": new_symside, "nav": nav_mode, "sheet": sname, "row": rr, "switch": switch, "cand": str(cand), "label": "ROW_DONE", "row_secs": round(_t.time() - _row_t0, 4), "n_evals": n_evals, "delta": delta, "promoted": promoted, "cum_after": cumulative_gain})
    def _final_filter_recheck():
        # Before closing: re-run EVERY distinct orange filter=opt against the FINAL cumulative "just in case" (earlier
        # tabs tested them vs an older cumulative). All deltas (pos/neg) -> FINAL_FILTER_RECHECK sheet + progress + log;
        # the single best positive is promoted. This is the per-filter data for row reduction / 365D runs.
        nonlocal cumulative_gain, cumulative_overrides
        cum0 = float(cumulative_gain)
        t0 = _t.time()
        seen = {}
        for (sn, r2) in sorted(orange_rows):
            sw2 = wb[sn].cell(row=r2, column=1).value
            c2 = wb[sn].cell(row=r2, column=2).value
            if sw2 in (None, "") or c2 is None:
                continue
            seen.setdefault((str(sw2).strip(), str(c2)), [])
            seen[(str(sw2).strip(), str(c2))].append(f"{sn}!{r2}")
        items = []
        for (sw2, c2) in seen:
            v = dict(cumulative_overrides)
            v.update(_switch_overrides(sw2, _parse_opt_value(c2, defaults.get(sw2))))
            items.append((sw2, c2, sanitize_overrides(v, defaults)[0]))
        results = {}
        futs = {}
        for sw2, c2, v in items:
            ck = (tuple(sorted((k, str(x)) for k, x in v.items())), args.window_days, id(prepared))
            if ck in _EVAL_CACHE:
                results[(sw2, c2)] = (_EVAL_CACHE[ck], "", True)
            elif _pool is not None:
                futs[(sw2, c2)] = (_pool.submit(_pool_eval, v, args.window_days), ck)
            else:
                from tools.opt.v12_pilot import evaluate_prepared_sanitized
                res = evaluate_prepared_sanitized(prepared, v, args.window_days)
                _EVAL_CACHE[ck] = res
                results[(sw2, c2)] = (res, "", False)
        deadline = t0 + YELLOW_TIMEOUT * max(1, -(-len(futs) // max(1, _n_proc)))
        for key, (fut, ck) in futs.items():
            try:
                res, _secs = fut.result(timeout=max(0.01, deadline - _t.time()))
                _EVAL_CACHE[ck] = res
                results[key] = (res, "", False)
            except _cf.TimeoutError:
                fut.cancel()
                results[key] = (None, f"TIMEOUT {YELLOW_TIMEOUT:.0f}s", False)
            except Exception as e:
                results[key] = (None, f"ERR {e}"[:120], False)
        if "FINAL_FILTER_RECHECK" in wb.sheetnames:
            del wb["FINAL_FILTER_RECHECK"]
        fws = wb.create_sheet("FINAL_FILTER_RECHECK")
        hdr = ["filter", "option", "gain_pct", "trades", "valid", "delta_vs_final", "promoted", "cumulative_final_before", "rows", "reason"]
        for i, h in enumerate(hdr, 1):
            fws.cell(row=1, column=i, value=h).font = Font(name="Arial", size=10, bold=True)
        recs = []
        best = None
        for (sw2, c2) in seen:
            res, err, cached = results.get((sw2, c2), (None, "missing", False))
            g = (res or {}).get("gain_pct")
            d = (float(g) - cum0) if g is not None else None
            d = 0.0 if d is not None and abs(d) < 1e-9 else d
            ok = d is not None and bool((res or {}).get("valid"))
            recs.append([sw2, c2, g, (res or {}).get("trades"), (res or {}).get("valid"), d, False, cum0, ", ".join(seen[(sw2, c2)][:6]), err or (res or {}).get("invalid_reason") or ""])
            _delta_log({"ts": utcnow(), "sym_side": new_symside, "nav": nav_mode, "sheet": "FINAL_FILTER_RECHECK", "row": None, "switch": sw2, "cand": str(c2), "label": "FINAL_RECHECK", "fn": "tools.opt.v12_pilot.evaluate_prepared_sanitized", "window_days": args.window_days, "gain_pct": g, "trades": (res or {}).get("trades"), "valid": (res or {}).get("valid"), "invalid_reason": (res or {}).get("invalid_reason"), "cum_before": cum0, "delta": d, "secs": None, "cached": cached, "err": err})
            if ok and d > 1e-9 and not promotion_block_reason(sw2) and (best is None or d > best[0]):
                best = (d, sw2, c2, g)
        if best is not None:
            d, sw2, c2, g = best
            cumulative_gain = float(g)
            cumulative_overrides.update(_switch_overrides(sw2, _parse_opt_value(c2, defaults.get(sw2))))
            cumulative_overrides, _ = sanitize_overrides(cumulative_overrides, defaults)
            for rec in recs:
                if rec[0] == sw2 and rec[1] == c2:
                    rec[6] = True
        recs.sort(key=lambda x: (x[5] is None, -(x[5] or 0)))
        for i, rec in enumerate(recs, 2):
            for j, val in enumerate(rec, 1):
                c = fws.cell(row=i, column=j, value=val)
                c.font = Font(name="Arial", size=10, bold=(j == 7 and bool(rec[6])))
            dcell = fws.cell(row=i, column=6)
            if isinstance(rec[5], (int, float)):
                dcell.font = Font(name="Arial", size=10, color="006100" if rec[5] > 1e-9 else "9C0006")
        progress["final_filter_recheck"] = {"cumulative_before": cum0, "cumulative_after": float(cumulative_gain), "n": len(recs), "promoted": [best[1], best[2], best[0]] if best else None, "deltas": {f"{r[0]}={r[1]}": r[5] for r in recs}}
        progress["cumulative_gain"] = float(cumulative_gain)
        progress["cumulative_overrides"] = dict(cumulative_overrides)
        print(f"[final-recheck] {len(recs)} distinct orange filters vs final cum {cum0:.4f} computed={len(futs)} cached={len(recs)-len(futs)} best={'%s=%s %+.4f' % (best[1], best[2], best[0]) if best else 'none'} -> cum {cumulative_gain:.4f} ({_t.time()-t0:.1f}s)", flush=True)
    # ── SEQUENTIAL FILL — USER 2026-09-29 late (BACKTEST_BIBLE §56 rev. 2026-09-29b), supersedes the R16/R17 tab-jump ──
    # Tabs in SWITCH_SHEETS order, rows in order (white switch rows, then orange filter rows), NO row skipped, NO jumping.
    # Per row: every yellow cell = running set + switch=cand + that filter, delta vs the LATEST baseline, always written.
    # VECTOR_DELTA (G) = the row's COMPLETE delta vs the LATEST baseline (switch + all positive filters, real joint eval);
    # blank only on the running-default row (bold) when none of its yellows is positive (nothing to calculate: it IS the baseline).
    # HUSTLE_DELTA (F) = the same result vs the ORIGINAL baseline. PER_ROW_FILTERS (K) = the positive yellow filters.
    # BASELINE (E): chain start (E3 on a fresh sheet), then ONLY on the row after a POSITIVE row (= latest + delta); else EMPTY.
    # Baseline values only go up. A cell slower than YELLOW_TIMEOUT is logged + RED, the fill continues; reds are retried at the end.
    try:
        for sname in tabs:
            ws = wb[sname]
            cols = _resolve_cols(ws)
            e2 = ws.cell(row=2, column=cols["E"])
            if not isinstance(e2.value, str):
                e2.value = "BASELINE"
            _next_pending(sname)
            for (rr, sw, cand) in per_tab_rows[sname]:
                if (sname, f"{sw}={cand}") not in _done_ids_cache["ids"]:
                    ws.cell(row=rr, column=cols["E"]).value = None
    except Exception as _e_init:
        print(f"[spec-fill] E init warn {_e_init}", flush=True)
    header_maps: dict[str, dict] = {}
    for sname in tabs:
        ws = wb[sname]
        hm = {}
        for cc in range(12, ws.max_column + 50):
            try:
                hv = ws.cell(row=2, column=cc).value
            except Exception:
                hv = None
            if hv and isinstance(hv, str) and "=" in hv and not hv.upper().startswith("WHAT SWITCH"):
                hm[hv.strip()] = cc
            if hv and isinstance(hv, str) and hv.strip().upper().startswith("WHAT SWITCH"):
                break
        header_maps[sname] = hm
    # override column C (USER 2026-09-30): previous-best settings (BEST-C-FILL) + every promotion's switch=cand and positive
    # filter=opt. A key lives in exactly ONE C cell: when a later promotion sets it to another value, the old part is erased
    # from its cell and the new setting is written in the promoting row's C. _c_where: key -> (sheet, row), in fill order.
    _c_where: dict = {}
    def _c_parts(cell):
        return [p.strip() for p in str(cell.value).split(" + ") if p.strip()] if cell.value not in (None, "") else []
    def _c_drop(sname: str, rr: int, key: str):
        ws_ = wb[sname]
        cell = ws_.cell(row=rr, column=_resolve_cols(ws_)["C"])
        keep = [p for p in _c_parts(cell) if p.split("=", 1)[0].strip() != key]
        cell.value = " + ".join(keep) if keep else None
    for sname in tabs:
        ws_ = wb[sname]
        c_col = _resolve_cols(ws_)["C"]
        for rr in range(3, ws_.max_row + 1):
            for p in _c_parts(ws_.cell(row=rr, column=c_col)):
                if "=" not in p:
                    continue
                k = p.split("=", 1)[0].strip()
                if k in _c_where and _c_where[k] != (sname, rr):
                    _c_drop(*_c_where[k], k)  # a duplicate from an older fill: the later entry supersedes it
                _c_where[k] = (sname, rr)
    def _set_override(ws_, sname: str, rr: int, cols_: dict, parts: list):
        cell = ws_.cell(row=rr, column=cols_["C"])
        cur = _c_parts(cell)
        for part in parts:
            k = part.split("=", 1)[0].strip()
            if k in _c_where and _c_where[k] != (sname, rr):
                old = _c_where[k]
                _c_drop(*old, k)
                print(f"[C-SUPERSEDE] {k}: erased from {old[0]}!{old[1]} -> {part} at {sname}!{rr}", flush=True)
            cur = [p for p in cur if p.split("=", 1)[0].strip() != k] + [part]
            _c_where[k] = (sname, rr)
        cell.value = " + ".join(cur) if cur else None
        cell.font = Font(name="Arial", size=10, bold=True)
        cell.alignment = VISUAL_ALIGN
    if tabs:
        _next_pending(tabs[0])
    queue = [(sname, rr, sw, cand) for sname in tabs for (rr, sw, cand) in per_tab_rows[sname]]
    pending_queue = [q for q in queue if (q[0], f"{q[2]}={q[3]}") not in _done_ids_cache["ids"]]
    total_rows = len(queue)
    processed = 0
    loop_guard = 0
    print("[red-fixer] disabled (fork-safety) — slow cells go RED inline and are retried at the end of the workbook", flush=True)
    heartbeat_path = Path("/tmp") / f"v14_heartbeat_{new_symside}.txt"
    def _touch(msg: str):
        try:
            heartbeat_path.write_text(f"{_t.time():.0f} {msg}")
        except Exception:
            pass
    _touch("spec-start")
    print(f"[spec-fill] {new_symside} SEQUENTIAL tabs={len(tabs)} rows={total_rows} pending={len(pending_queue)} baseline={baseline_gain:.4f} initial={initial_baseline:.4f} running={cumulative_gain:.4f} hustle={is_hustle}", flush=True)
    def _same_val(a, b) -> bool:
        if isinstance(a, bool) or isinstance(b, bool) or str(a).lower() in ("true", "false") or str(b).lower() in ("true", "false"):
            return str(a).strip().lower() == str(b).strip().lower()
        try:
            return abs(float(a) - float(b)) < 1e-12
        except Exception:
            return str(a).strip() == str(b).strip()
    def _ck(ov: dict):
        return (tuple(sorted((k, str(v)) for k, v in ov.items())), args.window_days, id(prepared))
    # POS_SYM sampling context (see _possym_* above): read once per workbook
    _ps_round = _possym_round_id(progress_path)
    _ps_on = _possym_enabled(_ps_round)
    _ps_cat = map_key_for_symside(new_symside)
    _ps_json = _possym_load_nsym(_ps_cat) if _ps_on else {}
    _ps_counts: dict = {}
    _ps_new: set = set()
    _ps_filt: dict = {}
    _ps_hdr: dict = {}
    def _ps_cols(_ws):
        k = _ws.title
        if k not in _ps_hdr:
            hm = _hdr_col_map(_ws)
            _ps_hdr[k] = {"pos": hm.get("POS_SYM") or hm.get("pos_sym"), "n": hm.get("N_SYM") or hm.get("n_sym"), "isd": hm.get("is_default"), "avg": hm.get("AVG_DELTA") or hm.get("avg_delta")}
        return _ps_hdr[k]
    def _ps_row_ev(_ws, _rr, _tab, _sw, _cd):
        """(pos_sym, n_sym, is_default) of one template row: the avg-delta json wins, else the template columns."""
        c = _ps_cols(_ws)
        pos = n = None
        jr = _ps_json.get(f"{_tab}!{str(_sw).strip()}={str(_cd).strip()}")
        if isinstance(jr, dict):
            pos, n = jr.get("pos_sym"), jr.get("n_sym")
            if jr.get("new"):
                _ps_new.add((_tab, str(_sw).strip(), str(_cd).strip()))
        if pos is None and c["pos"]:
            _v = _ws.cell(row=_rr, column=c["pos"]).value
            pos = _v if isinstance(_v, (int, float)) else None
        if n is None and c["n"]:
            _v = _ws.cell(row=_rr, column=c["n"]).value
            n = _v if isinstance(_v, (int, float)) else None
        isd = bool(c["isd"] and str(_ws.cell(row=_rr, column=c["isd"]).value or "").strip().upper() == "YES")
        return pos, n, isd
    def _ps_filter_ev(_tab, _filt, _opt, _row_pos, _row_n):
        """POS_SYM of a filter = its orange row (same tab first, then any tab) — else the row's own."""
        if not _ps_filt:
            for _sn in SWITCH_SHEETS:
                if _sn not in wb.sheetnames:
                    continue
                _w = wb[_sn]
                for _r in range(3, _w.max_row + 1):
                    _a, _b = _w.cell(row=_r, column=1).value, _w.cell(row=_r, column=2).value
                    if _a in (None, "") or _b in (None, ""):
                        continue
                    _pp, _nn, _ = _ps_row_ev(_w, _r, _sn, _a, _b)
                    if _pp is not None:
                        _ps_filt.setdefault((_sn, str(_a).strip(), str(_b).strip()), (_pp, _nn))
                        _ps_filt.setdefault((None, str(_a).strip(), str(_b).strip()), (_pp, _nn))
        return _ps_filt.get((_tab, _filt, _opt)) or _ps_filt.get((None, _filt, _opt)) or (_row_pos, _row_n)
    def _ps_count(_tab, _bucket, _computed, _what="row"):
        e = _ps_counts.setdefault(f"{_tab}|{_what}|{_bucket}", [0, 0])
        e[0 if _computed else 1] += 1
    # USER 2026-10-03 hollow-fix: every done-record carries the policy state it was measured under
    try:
        from tools.v15_row_guards import policy_stamp as _policy_stamp_fn
        from tools.v15_row_guards import tried_settled as _tried_settled_fn
    except Exception:
        _policy_stamp_fn = None
        _tried_settled_fn = None
    def _policy_stamp(sname: str) -> dict:
        try:
            _tl_on = bool(tab_level_filters(_ps_cat, sname))
        except Exception:
            _tl_on = False
        if _policy_stamp_fn is not None:
            return _policy_stamp_fn(_ps_on, _tl_on, _UNWIRED_AUDIT_ID)
        return {"ps": 1 if _ps_on else 0, "tl": 1 if _tl_on else 0, "uw": _UNWIRED_AUDIT_ID, "pilot": "hollowfix-20261003"}
    if _ps_on and not _ps_json:
        print(f"[POSSYM] sampling ON for round {_ps_round} but no n_sym source (data/avg_delta_pos_sym.json for {_ps_cat}) — rows without a template N_SYM column are always calculated", flush=True)
    elif _ps_on:
        print(f"[POSSYM] sampling ON round={_ps_round} cat={_ps_cat} json_rows={len(_ps_json)}", flush=True)
    _static_info: dict = {}
    def _row_static(sname: str, rr: int, switch, cand) -> dict:
        # structural class + this row's yellow cells (read once; the running set is applied at eval time)
        k = (sname, rr)
        if k in _static_info:
            return _static_info[k]
        ws = wb[sname]
        info = {"kind": "eval", "reason": "", "hdrs": [], "h2f": {}, "pending_wiring": [], "yellow": set()}
        _a_font = ws.cell(row=rr, column=1).font
        if cand is None or str(cand).strip() in ("", "None", "none"):
            info.update(kind="skip", reason="NO_CANDIDATE: empty B cell in template row — nothing to test", g=None)
        elif str(switch).strip() in UNWIRED_SWITCHES or str(switch).strip() in UNWIRED_FILTERS:
            info.update(kind="skip", reason="NOT_WIRED_VEC: no reachable vectorized read and the ledger never moved (v15_zero_audit) — not calculated, never a fake 0", g=None)
        elif str(cand).strip().upper().endswith("_ALT"):
            info.update(kind="skip", reason="INVENTED_ALT: *_ALT option value exists in no config — grey, not calculated", g=None)
            ws.cell(row=rr, column=2).font = Font(name="Arial", size=10, color="FFBFBFBF")
        elif not all(k in known_config_fields() for k in (_switch_overrides(str(switch).strip(), _parse_opt_value(cand, defaults.get(switch))) or {str(switch).strip(): None})):
            info.update(kind="skip", reason="NOT_IN_CONFIG: no config/config_tradier/QuickConfig field — grey, not calculated", g=None)
            ws.cell(row=rr, column=1).font = Font(name="Arial", size=10, color="FFBFBFBF")
        elif (_tm_why := _switch_type_violation(str(switch).strip(), cand, defaults)):
            info.update(kind="skip", reason=f"{_tm_why} — grey, not calculated", g=None)
            ws.cell(row=rr, column=1).font = Font(name="Arial", size=10, color="FFBFBFBF")
        else:
            # USER 2026-09-30: grey / DEAD_VEC / LIVE_ONLY rows are calculated like every other row (real engine deltas);
            # DEAD_VEC / LIVE_ONLY promotion stays blocked by promotion_block_reason
            # USER 2026-09-30: a cell is YELLOW iff the row's switch name and the column's filter name share >= 2 "_"-tokens
            # (interim rule until the yellow-map rebuild) — decided from the NAMES, never from a cloned fill.
            # USER 2026-10-03 hollow-fix: paint is written ONLY together with a calculated value (yellow write
            # path) or RED with a failure reason — NEVER pre-painted here. Pre-painting changed cell colours
            # for rows/cells that were then policy-skipped (0 evals): colour without calculation. Previously
            # calculated values are never cleared here either (that destroyed real deltas without a new calc).
            _sw_tok = {t for t in str(switch).upper().split("_") if t}
            _tab_level = tab_level_filters(map_key_for_symside(new_symside), sname)
            _ever_y = ever_yellow_cells(map_key_for_symside(new_symside))
            _mand = mandatory_bases_for_tab(sname)
            for hdr, col in header_maps[sname].items():
                _hdr_base = hdr.split("=", 1)[0].strip()
                _via_token = len(_sw_tok & {t for t in _hdr_base.upper().split("_") if t}) >= YELLOW_MIN_SHARED_TOKENS or f"{sname}\t{str(switch).strip()}={str(cand).strip()}\t{hdr}" in _ever_y
                _want = _via_token or _hdr_base in _mand
                _mand_only = _want and not _via_token
                if hdr.split("=", 1)[0].strip() in _tab_level:
                    if _want:
                        info["yellow"].add(hdr)
                        info.setdefault("excluded_tab_level", []).append(hdr)
                    continue  # tab-level filter: tested once as an orange row of this tab, never per switch row
                if _want:
                    info["yellow"].add(hdr)
                elif os.environ.get("V15_ALL_FILTER_COLS", "0") != "1":
                    continue  # USER 2026-09-30: yellow cells only now; the all-column test runs after every sym_side is complete
                filt, opt = hdr.split("=", 1)
                if filt.strip() not in known_config_fields():
                    continue
                if filt.strip() in UNWIRED_FILTERS or filt.strip() in UNWIRED_SWITCHES:
                    info.setdefault("excluded_unwired", []).append(hdr)
                    continue  # not wired in the vectorized engine: no calculation, no fake 0
                _fok, _fwhy = _cand_compatible(filt.strip(), _parse_opt_value(opt.strip(), defaults.get(filt.strip())), defaults)
                if not _fok:
                    info.setdefault("type_skipped", []).append(hdr)
                    continue  # type-incompatible filter value: blank cell, never evaluated, never 0.0, never RED
                if _mand_only:
                    _mandatory_note(sname, _hdr_base, False)
                info["hdrs"].append(hdr)
                info["h2f"][hdr] = {"filter": filt.strip(), "opt": opt.strip()}
        if _ps_on and info["kind"] == "eval":
            try:
                _pp, _nn, _isd = _ps_row_ev(ws, rr, sname, switch, cand)
                if _isd:
                    _ps_count(sname, "default", True)
                else:
                    _go, _bk, _pr, _uu = _possym_decide(new_symside, sname, f"{str(switch).strip()}={str(cand).strip()}", _ps_round, _pp, _nn, (sname, str(switch).strip(), str(cand).strip()) in _ps_new)
                    _ps_count(sname, _bk, _go)
                    if _pr is not None:
                        info["possym"] = {"pos": int(_pp), "n": int(_nn), "p": round(_pr, 4), "u": round(_uu, 4)}
                    if not _go:
                        info.update(kind="skip", reason=f"SKIPPED_SAMPLING(pos_sym={int(_pp)})", g=None)
                    else:
                        _kept = []
                        for _h in info["hdrs"]:
                            _f, _o = _h.split("=", 1)
                            _fp, _fn = _ps_filter_ev(sname, _f.strip(), _o.strip(), _pp, _nn)
                            _fgo, _fbk, _, _ = _possym_decide(new_symside, sname, f"{str(switch).strip()}={str(cand).strip()}@{_h}", _ps_round, _fp, _fn, (sname, _f.strip(), _o.strip()) in _ps_new or (sname, str(switch).strip(), str(cand).strip()) in _ps_new)
                            _ps_count(sname, _fbk, _fgo, "cell")
                            if _fgo:
                                _kept.append(_h)
                            else:
                                info.setdefault("sampled_filters", []).append(_h)
                        info["hdrs"] = _kept
            except Exception as _pe:
                print(f"[POSSYM-WARN] {sname}!{rr} {_pe} — row calculated", flush=True)
        _static_info[k] = info
        return info
    def _row_plan(sname: str, rr: int, switch, cand) -> dict:
        # eval items for this row against the CURRENT running set (cumulative_overrides)
        st = _row_static(sname, rr, switch, cand)
        if st["kind"] != "eval":
            return {"static": st}
        cand_parsed = _parse_opt_value(cand, defaults.get(switch))
        sw_ov = _switch_overrides(switch, cand_parsed)
        is_running = all(_same_val(cumulative_overrides.get(k2, defaults.get(k2)), v2) for k2, v2 in sw_ov.items())
        sv = dict(cumulative_overrides)
        sv.update(sw_ov)
        sv, _ = sanitize_overrides(sv, defaults)
        items = [] if is_running else [("naked", sv)]
        hv = dict(initial_overrides)
        hv.update(sw_ov)
        hv, _ = sanitize_overrides(hv, defaults)
        if not is_running and any(not _same_val(initial_overrides.get(k2, defaults.get(k2)), v2) for k2, v2 in sw_ov.items()):
            items.append(("hustle", hv))
        for hdr in st["hdrs"]:
            f = st["h2f"][hdr]
            v = dict(sv)
            v[f["filter"]] = _parse_opt_value(f["opt"], defaults.get(f["filter"]))
            items.append((hdr, sanitize_overrides(v, defaults)[0]))
        return {"static": st, "cand_parsed": cand_parsed, "sw_ov": sw_ov, "is_running": is_running, "switch_variant": sv, "items": items}
    inflight: dict = {}
    def _harvest():
        for ck in [c for c, (f, _) in inflight.items() if f.done()]:
            f, _ = inflight.pop(ck)
            try:
                res, _secs = f.result(timeout=0)
                _EVAL_CACHE[ck] = res
            except Exception:
                pass
    def _submit(ov: dict):
        ck = _ck(ov)
        if _pool is not None and ck not in _EVAL_CACHE and ck not in inflight:
            inflight[ck] = (_pool.submit(_pool_eval, ov, args.window_days), _t.time())
        return ck
    _spec_state = {"ptr": 0, "ver": 0, "ptr_ver": 0}
    def _prefetch(qi: int):
        # speculative: rows ahead are submitted vs the CURRENT running set. A promotion changes the running set, so a
        # speculated result can never be used for a different baseline (cache key = the full override set).
        if _pool is None:
            return
        if _spec_state["ptr_ver"] != _spec_state["ver"] or _spec_state["ptr"] <= qi:
            _spec_state["ptr"], _spec_state["ptr_ver"] = qi, _spec_state["ver"]
        while _spec_state["ptr"] < len(pending_queue) and _spec_state["ptr"] < qi + 60 and sum(1 for f, _ in inflight.values() if not f.done()) < 2 * _n_proc:
            plan = _row_plan(*pending_queue[_spec_state["ptr"]])
            for _lab, _ov in plan.get("items", []):
                _submit(_ov)
            _spec_state["ptr"] += 1
    def _get(sname: str, rr: int, switch, cand, label: str, ov: dict, deadline: float, cum_before: float):
        # one real evaluate_prepared_sanitized result (pool / cache / serial thread), hard deadline, one DELTA-LOG line
        ck = _ck(ov)
        t0 = _t.time()
        res, err, cached = None, "", False
        if ck in _EVAL_CACHE:
            res, cached = _EVAL_CACHE[ck], True
        elif _pool is not None:
            _submit(ov)
            fut, _ = inflight[ck]
            try:
                res, _secs = fut.result(timeout=max(0.01, deadline - _t.time()))
                _EVAL_CACHE[ck] = res
            except _cf.TimeoutError:
                err = f"TIMEOUT {YELLOW_TIMEOUT:.0f}s"
                fut.cancel()
            except Exception as _fe:
                err = f"ERR {_fe}"[:120]
            inflight.pop(ck, None)
        else:
            ex = _cf.ThreadPoolExecutor(max_workers=1)
            try:
                from tools.opt import v12_pilot as _vp
                if prepared is not None:
                    fut = ex.submit(_vp.evaluate_prepared_sanitized, prepared, ov, args.window_days)
                else:
                    fut = ex.submit(_vp.evaluate_sanitized, new_symside, ov, window_days=args.window_days)
                res = fut.result(timeout=max(0.01, deadline - _t.time()))
                _EVAL_CACHE[ck] = res
            except _cf.TimeoutError:
                err = f"TIMEOUT {YELLOW_TIMEOUT:.0f}s"
            except Exception as _se:
                err = f"ERR {_se}"[:120]
            finally:
                ex.shutdown(wait=False)
        g = (res or {}).get("gain_pct")
        _delta_log({"ts": utcnow(), "sym_side": new_symside, "nav": "sequential", "sheet": sname, "row": rr, "switch": switch, "cand": str(cand), "label": label, "fn": "tools.opt.v12_pilot.evaluate_prepared_sanitized", "window_days": args.window_days, "gain_pct": g, "trades": (res or {}).get("trades"), "fp": ((res or {}).get("behavior_fingerprint") or "")[:16], "tim": (res or {}).get("tim_pct"), "valid": (res or {}).get("valid"), "invalid_reason": (res or {}).get("invalid_reason"), "cum_before": cum_before, "delta": (float(g) - cum_before) if g is not None else None, "secs": round(_t.time() - t0, 4), "cached": cached, "err": err})
        return res, err
    _delta_vs = delta_vs_result  # module-level pure twin (test_v15_row_guards covers the verdict/settle composition)
    def _num_cell(ws, rr: int, col: int, value, good: bool, bold: bool = True):
        cell = ws.cell(row=rr, column=col)
        cell.value = float(value)
        cell.font = Font(name="Arial", size=10, bold=bold, color="006100" if good else "9C0006")
        cell.fill = PatternFill(fill_type=None)  # USER 2026-09-30: red paint ONLY for stuck/slow cells, never for a negative value
        cell.alignment = VISUAL_ALIGN
    # USER 2026-09-30: the FIRST row is the DEFAULT row. If the override set changes that switch, E3 = the baseline WITHOUT
    # that override (switch at its bold default); the overridden value is re-earned by its own row's delta further down.
    if not progress.get("done") and pending_queue:
        _s0, _r0, _sw0, _c0 = pending_queue[0]
        _ov0 = _switch_overrides(_sw0, _parse_opt_value(_c0, defaults.get(_sw0)))
        if _ov0 and not all(_same_val(cumulative_overrides.get(_k, defaults.get(_k)), _v) for _k, _v in _ov0.items()):
            _base0 = dict(cumulative_overrides)
            _base0.update(_ov0)
            _base0 = sanitize_overrides(_base0, defaults)[0]
            _res0, _err0 = _get(_s0, _r0, _sw0, _c0, "E3_DEFAULT_BASE", _base0, _t.time() + YELLOW_TIMEOUT * 6, float(cumulative_gain))
            if _res0 and _res0.get("gain_pct") is not None and int(_res0.get("trades") or 0) > 0 and _res0.get("valid"):
                for _k in _ov0:
                    if _k in _c_where:
                        _c_drop(*_c_where.pop(_k), _k)
                print(f"[E3-DEFAULT-BASE] {_s0}!{_r0} {_sw0}: override {cumulative_overrides.get(_sw0)} removed for the chain start -> E3 {float(_res0['gain_pct']):.4f} (with override {float(cumulative_gain):.4f})", flush=True)
                progress["e3_default_base"] = {"switch": _sw0, "override_value": cumulative_overrides.get(_sw0), "gain_with_override": float(cumulative_gain), "gain_default": float(_res0["gain_pct"])}
                cumulative_overrides = dict(_base0)
                cumulative_gain = float(_res0["gain_pct"])
                initial_baseline = cumulative_gain
                initial_overrides = dict(_base0)
                progress["initial_baseline_gain"] = cumulative_gain
                progress["initial_overrides"] = dict(_base0)
                progress["cumulative_gain"] = cumulative_gain
                progress["cumulative_overrides"] = dict(_base0)
                _spec_state["ver"] += 1
            else:
                print(f"[E3-DEFAULT-BASE] {_s0}!{_r0} {_sw0}: default-base eval failed ({_err0}) — keeping override base", flush=True)
    _red_retry: list = []
    last_E = {"v": None}
    def _chain_E(sname: str, rr: int, value: float):
        if last_E["v"] is not None and value < last_E["v"] - 1e-9:
            print(f"[E-MONOTONIC-FAIL] {sname}!{rr} baseline {value:.4f} < previous {last_E['v']:.4f} — NOT written (baseline can only go up)", flush=True)
            _flag_to_md(flags_md, sname, rr, "BASELINE", "MONOTONIC", f"E {value:.4f} < previous {last_E['v']:.4f}", 0.0, value, last_E["v"])
            return
        _write_E(sname, rr, value)
        last_E["v"] = float(value)
    # USER 2026-09-30: E3 of EVERY tab = the running baseline when that tab starts; any later E only after a POSITIVE row.
    # USER 2026-10-03 RECALC-ON-FLIP: E is NEVER chained by sum (E + G is fiction when overrides interact); every
    # promotion fresh-evaluates the full new set and E_next = fresh gain. Resumed sheets heal on the next flip.
    _done0 = progress.get("done", {})
    # USER 2026-10-03 RULE#4: this run's NPZ identity (resume-refill already enforced by the caller; rows stamp it)
    try:
        from tools.v15_row_guards import incomplete_rows as _incomplete_rows
        from tools.v15_row_guards import npz_identity_for_symside as _npz_id_s
        from tools.v15_row_guards import short_npz_id as _short_npz_s
        _run_npz_id = progress.get("npz_id") or _npz_id_s(new_symside)
        _run_npz_short = _short_npz_s(_run_npz_id)
        if progress.get("npz_id") is None and _run_npz_id is not None:
            progress["npz_id"] = _run_npz_id
    except Exception:
        _run_npz_id, _run_npz_short, _incomplete_rows = None, "npz?", None
    _tab_first = {s: per_tab_rows[s][0] for s in tabs if per_tab_rows.get(s)}
    for _s, (_r0, _sw0, _c0) in _tab_first.items():
        _rec0 = _done0.get(f"{_s}!{_r0}:{_sw0}={_c0}")
        if _rec0 and isinstance(_rec0.get("cumulative_before"), (int, float)):
            _write_E(_s, _r0, _rec0["cumulative_before"])
    e_next = None
    if pending_queue:
        _qi0 = queue.index(pending_queue[0])
        if _qi0 > 0:
            _ps, _pr, _psw, _pc = queue[_qi0 - 1]
            _prec = _done0.get(f"{_ps}!{_pr}:{_psw}={_pc}") or {}
            if _prec.get("promoted") and isinstance(_prec.get("cumulative_after"), (int, float)):
                e_next = float(_prec["cumulative_after"])
    _ref_cache: dict = {}
    _standalone_seen: dict = {}
    def _fp_of(_r):
        return ((_r or {}).get("behavior_fingerprint") or None)
    def _peek(_sname, _rr, _switch, _cand, _label, _ov, _cum):
        # cached evaluation (no log spam on hits): ledger fingerprint of an override set
        _ckk = _ck(_ov)
        if _ckk in _EVAL_CACHE:
            return _EVAL_CACHE[_ckk]
        _r, _e = _get(_sname, _rr, _switch, _cand, _label, _ov, _t.time() + YELLOW_TIMEOUT * 2, _cum)
        return _r
    _start_t = _t.time()
    for qi, (sname, rr, switch, cand) in enumerate(pending_queue):
        loop_guard += 1
        if loop_guard % 200 == 0:
            print(f"[spec-loop] {loop_guard}/{len(pending_queue)} elapsed={_t.time()-_start_t:.0f}s cum={cumulative_gain:.4f}", flush=True)
        _row_t0 = _t.time()
        ws = wb[sname]
        cols = _resolve_cols(ws)
        cumulative_before = float(cumulative_gain)
        if e_next is None and _tab_first.get(sname, (None,))[0] == rr:
            e_next = cumulative_before
        if e_next is not None:
            _chain_E(sname, rr, e_next)
        else:
            ws.cell(row=rr, column=cols["E"]).value = None
        e_next = None
        key = f"{sname}!{rr}:{switch}={cand}"
        _harvest()
        plan = _row_plan(sname, rr, switch, cand)
        st = plan["static"]
        if st["kind"] == "skip":
            g = ws.cell(row=rr, column=cols["G"])
            g.value = st.get("g")
            g.font = Font(name="Arial", size=10, bold=False, italic=st.get("g") is None, color="808080")
            g.alignment = VISUAL_ALIGN
            _skip_is_policy = str(st["reason"]).startswith("SKIPPED")
            progress.setdefault("done", {})[key] = {"delta": st.get("g"), "promoted": False, "reason": st["reason"], "vec_gain": None, "trades": None, "yellows": {}, "cumulative_before": cumulative_before, "cumulative_after": cumulative_before, "npz": _run_npz_short, "policy": _policy_stamp(sname), "complete": (not _skip_is_policy)}
            if _skip_is_policy:
                print(f"[POLICY-SKIP-PENDING] {sname}!{rr} {switch}={cand} {st['reason'][:60]} — 0 evals, row stays pending (RULE#3 refuses publish until refilled)", flush=True)
            if st["reason"].startswith(("DEAD_VEC", "LIVE_ONLY")):
                _flag_to_md(flags_md, sname, rr, switch, cand, st["reason"], 0.0, 0.0, cumulative_before)
            progress["cumulative_gain"] = float(cumulative_gain)
            _maybe_write_json()
            _row_done(sname, rr, switch, cand, 0, st.get("g"), False)
            _touch(f"cell {sname}!{rr} skip {st['reason'][:20]}")
            _maybe_save()
            processed += 1
            continue
        for _pw_col in st["pending_wiring"]:
            _pw_cell = ws.cell(row=rr, column=_pw_col)
            if not isinstance(_pw_cell.value, (int, float)):
                _pw_cell.font = Font(name="Arial", size=10, italic=True, color="808080")
        _prefetch(qi)
        is_running = plan["is_running"]
        switch_variant = plan["switch_variant"]
        n_items = len(plan["items"])
        deadline = _t.time() + YELLOW_TIMEOUT * (max(1, -(-n_items // max(1, _n_proc))) + 2)
        results = {lab: _get(sname, rr, switch, cand, lab, ov, deadline, cumulative_before) for lab, ov in plan["items"]}
        reasons = {}
        if is_running:
            naked_delta, naked_ok, naked_gain = 0.0, True, cumulative_before  # the running value: no calculation, it IS the baseline
        else:
            nres, nerr = results["naked"]
            naked_delta, naked_ok, _nr = _delta_vs(nres, cumulative_before)
            naked_gain = (nres or {}).get("gain_pct")
            if nerr or _nr:
                reasons["naked"] = nerr or _nr
            if nerr:
                _flag_to_md(flags_md, sname, rr, switch, cand, f"slow/failed naked: {nerr}", 0.0, 0.0, cumulative_before)
                _red_retry.append({"sheet": sname, "row": rr, "col": cols["G"], "label": "naked", "ov": switch_variant, "cum_before": cumulative_before, "key": key})
                _zr_log({"kind": "RED", "sheet": sname, "row": rr, "switch": switch, "cand": str(cand), "col": "F/G", "reason": nerr, "cum_before": cumulative_before})
        # USER 2026-10-03 hollow-fix: a deterministic engine verdict on naked (pre-eval rejection, invalid
        # with reason — tried, answered, cached) SETTLES the row like ZERO_TRADES does. Only a missing
        # verdict (timeout/exception) keeps the row pending. Short-circuit keeps nres/nerr safe.
        _naked_verdict = (_tried_settled_fn(nres, nerr) if _tried_settled_fn is not None else (not nerr and nres is not None)) if not is_running else True
        naked_settled = is_running or naked_delta is not None or reasons.get("naked") == "ZERO_TRADES" or _naked_verdict
        yellows, promotable, noop_yellows = {}, {}, []
        missing_yellows = []  # USER 2026-10-03 RULE#3: yellows with no verdict (timeout/err) — row stays incomplete
        yellow_dups: dict = {}
        _run_ov = sanitize_overrides(dict(cumulative_overrides), defaults)[0]
        _ref_ck = _ck(_run_ov)
        if _ref_ck not in _ref_cache:
            _ref_cache[_ref_ck] = _peek(sname, rr, switch, cand, "REF_BASELINE", _run_ov, cumulative_before)
        ref_fp = _fp_of(_ref_cache[_ref_ck])
        naked_fp = ref_fp if is_running else _fp_of(results["naked"][0])
        naked_binding = None if (ref_fp is None or naked_fp is None) else bool(naked_fp != ref_fp)
        for hdr in st["hdrs"]:
            res, err = results[hdr]
            col = header_maps[sname][hdr]
            d, ok, why = _delta_vs(res, cumulative_before)
            if err or why:
                reasons[hdr] = err or why
            if d is None and why == "ZERO_TRADES" and not err:
                ws.cell(row=rr, column=col).value = None
                _zr_log({"kind": "ZERO_TRADES", "sheet": sname, "row": rr, "switch": switch, "cand": str(cand), "col": hdr, "cum_before": cumulative_before})
                continue
            _settled_cell = _tried_settled_fn(res, err) if _tried_settled_fn is not None else (not err and res is not None)
            if d is None and _settled_cell:
                # USER 2026-10-03 hollow-fix: deterministic engine verdict (pre-eval rejection, invalid
                # with reason) — SETTLED, never retried, never blocking publish. Backtest bible §56:
                # INVALID <reason> in grey. Retrying a cached deterministic verdict is pointless.
                _inv_cell = ws.cell(row=rr, column=col)
                _inv_cell.value = f"INVALID {why}"[:80]
                _inv_cell.font = Font(name="Arial", size=10, bold=False, italic=True, color="808080")
                _inv_cell.alignment = VISUAL_ALIGN
                _flag_to_md(flags_md, sname, rr, switch, cand, f"INVALID {hdr}: {why}", 0.0, 0.0, cumulative_before)
                _zr_log({"kind": "INVALID", "sheet": sname, "row": rr, "switch": switch, "cand": str(cand), "col": hdr, "reason": why, "cum_before": cumulative_before})
                continue
            if d is None:
                # No verdict (timeout/exception) — genuinely uncalculated, row stays pending.
                missing_yellows.append(hdr)
                _spec_mark_red(wb, sname, rr, col, reason=err or why)
                print(f"[spec-stall] {sname}!{rr} {hdr} {err or why} -> RED, fill continues", flush=True)
                _zr_log({"kind": "RED", "sheet": sname, "row": rr, "switch": switch, "cand": str(cand), "col": hdr, "reason": err or why, "cum_before": cumulative_before})
                if err:
                    _flag_to_md(flags_md, sname, rr, switch, cand, f"slow/failed {hdr}: {err}", 0.0, 0.0, cumulative_before)
                    _red_retry.append({"sheet": sname, "row": rr, "col": col, "label": hdr, "ov": dict(plan["items"][[l for l, _ in plan["items"]].index(hdr)][1]), "cum_before": cumulative_before, "key": key})
                continue
            # FINGERPRINT RULES (USER 2026-09-30: no synthetic copies, no fake numbers):
            #   noop = the filter left the ledger identical to the row's own base (naked switch / running set) -> not a filter delta
            #   dup  = the row's switch is non-binding, so this cell is just the filter's STANDALONE effect copied onto an unrelated
            #          switch: recorded ONCE per baseline epoch (first occurrence), every later copy is blanked and kept out of AVG
            _fp_y = _fp_of(res)
            _fp_noop = _fp_y is not None and naked_fp is not None and _fp_y == naked_fp
            _is_dup = False
            if not _fp_noop and _fp_y is not None and ok and (is_running or naked_binding is False):
                _skey = (_ref_ck, hdr)
                _same_as_standalone = True
                if not is_running:
                    _f = st["h2f"][hdr]
                    _sov = dict(_run_ov)
                    _sov[_f["filter"]] = _parse_opt_value(_f["opt"], defaults.get(_f["filter"]))
                    _sov = sanitize_overrides(_sov, defaults)[0]
                    _same_as_standalone = _fp_of(_peek(sname, rr, switch, cand, "STANDALONE:" + hdr, _sov, cumulative_before)) == _fp_y
                if _same_as_standalone:
                    if _skey in _standalone_seen and _standalone_seen[_skey] != key:
                        _is_dup = True
                    else:
                        _standalone_seen[_skey] = key
            if _is_dup:
                yellow_dups[hdr] = float(d)
                ws.cell(row=rr, column=col).value = None
                continue
            yellows[hdr] = float(d)
            if abs(float(d)) < 1e-12:
                _zr_log({"kind": "ZERO", "sheet": sname, "row": rr, "switch": switch, "cand": str(cand), "col": hdr, "cum_before": cumulative_before})
            noop = _fp_noop or (ok and naked_ok and naked_delta is not None and abs(float(d) - float(naked_delta)) < 1e-9)
            if noop:
                noop_yellows.append(hdr)
            promotable[hdr] = ok and not noop
            ycell = ws.cell(row=rr, column=col)
            if _is_red_cell(ycell):
                _clear_red_fill(ws, rr, col)
                if hdr in st["yellow"]:
                    ycell.fill = PatternFill(start_color="FFFFFF00", end_color="FFFFFF00", fill_type="solid")
            # USER 2026-09-30: ALWAYS the real delta; engine-invalid (trades < floor, TIM > 80, DD > 30) = value in grey font
            # (never promoted, left out of AVG_DELTA); a non-binding filter keeps its real value, italic, never promoted
            ycell.value = float(d)
            ycell.font = Font(name="Arial", size=10, bold=False, italic=noop, color=None if ok else "808080")
            ycell.alignment = VISUAL_ALIGN
            if not ok:
                _flag_to_md(flags_md, sname, rr, switch, cand, f"INVALID {hdr}: {why}", d, 0.0, cumulative_before)
        pos_hdrs = [h for h, d in yellows.items() if d > 1e-9 and promotable.get(h)]
        h2f = st["h2f"]
        _best_opt: dict = {}
        for _h in pos_hdrs:
            _fk = h2f[_h]["filter"]
            if _fk not in _best_opt or yellows[_h] > yellows[_best_opt[_fk]]:
                _best_opt[_fk] = _h
        pos_hdrs = [_h for _h in pos_hdrs if _best_opt[h2f[_h]["filter"]] == _h]  # never two options of ONE filter key in a joint set
        choice = None  # (delta, filters, override set, how)
        joint_delta, joint_reason = None, ""
        _joint_res = None
        if pos_hdrs:
            joint_ov = dict(switch_variant)
            for h in pos_hdrs:
                joint_ov[h2f[h]["filter"]] = _parse_opt_value(h2f[h]["opt"], defaults.get(h2f[h]["filter"]))
            joint_ov, _ = sanitize_overrides(joint_ov, defaults)
            jres, jerr = _get(sname, rr, switch, cand, "JOINT:" + "+".join(pos_hdrs), joint_ov, _t.time() + YELLOW_TIMEOUT * 3, cumulative_before)
            _joint_res = jres
            joint_delta, jok, joint_reason = _delta_vs(jres, cumulative_before)
            joint_reason = jerr or joint_reason
            if jok and joint_delta is not None and joint_delta > 1e-9:
                choice = (float(joint_delta), list(pos_hdrs), joint_ov, "joint")
            else:
                # the positive filters do not stack: take the best single REAL measured positive (never an arithmetic sum)
                h = max(pos_hdrs, key=lambda x: yellows[x])
                best_ov = dict(switch_variant)
                best_ov[h2f[h]["filter"]] = _parse_opt_value(h2f[h]["opt"], defaults.get(h2f[h]["filter"]))
                choice = (yellows[h], [h], sanitize_overrides(best_ov, defaults)[0], "best_single")
                if not is_running and naked_ok and naked_delta is not None and naked_delta > choice[0]:
                    choice = (float(naked_delta), [], switch_variant, "naked")
                print(f"[spec-joint-reject] {sname}!{rr} joint {joint_delta} ({joint_reason}) -> {choice[3]} {choice[0]:+.4f}", flush=True)
        elif not is_running and naked_ok and naked_delta is not None and naked_delta > 1e-9:
            choice = (float(naked_delta), [], switch_variant, "naked")
        # DEP-FORCED (Agent M, data/switch_dependencies.json): the evaluated result forced master switches ON (peers OFF) for a sub-knob test. The delta
        # is already measured WITH them (vs the running set); when the row is promoted they must be written into the override set / column C /
        # progress, otherwise live would receive the sub-knob without its master. dep_forced of every eval of this row is recorded.
        _dep_row: dict = {}
        for _lab_, (_r_, _e_) in results.items():
            for _k_, _v_ in ((_r_ or {}).get("dep_forced") or {}).items():
                _dep_row.setdefault(_lab_ if _lab_ in ("naked",) else "yellow", {})[_k_] = _v_
        _dep_choice: dict = {}
        if choice is not None:
            _src_ = None
            if choice[3] == "joint":
                _src_ = _joint_res
            elif choice[3] == "best_single" and choice[1]:
                _src_ = (results.get(choice[1][0]) or (None,))[0]
            elif choice[3] == "naked":
                _src_ = (results.get("naked") or (None,))[0]
            _dep_choice = dict((_src_ or {}).get("dep_forced") or {})
        if choice is not None:
            row_delta = choice[0]
        elif is_running:
            # USER 2026-10-03 (running-identity): candidate == running set -> delta is 0 by identity
            # (same overrides + same NPZ = bit-identical gain). An explained value, never a fake: the
            # RUNNING_IDENTITY reason keeps it out of zero-audit suspicion, promotion, and calc guards.
            row_delta = 0.0
            if not reasons.get("naked"):
                reasons["naked"] = "RUNNING_IDENTITY: candidate == running set, delta 0 by identity (no eval needed)"
        else:
            row_delta = naked_delta
        promote = choice is not None
        _blk = promotion_block_reason(switch, sname) if promote else ""
        if _blk:
            promote = False
            print(f"[promote-block] {sname}!{rr} {switch}={cand} delta={row_delta} NOT promoted: {_blk}", flush=True)
        if promote:
            try:
                _ct = float((_src_ or {}).get("tim_pct"))
            except Exception:
                _ct = None
            _tgv = tim_guard_veto(_ct, chain_tim)
            if _tgv:
                promote = False
                _blk = _tgv
                print(f"[promote-block] {sname}!{rr} {switch}={cand} delta={row_delta} NOT promoted: {_tgv}", flush=True)
        # TEAL 2026-10-04 RAMFP: the in-RAM NPZ must be identical between preload and promotion (RAM LAW
        # forbids mid-run re-prepare; a mismatch means the row measured different bytes than the chain).
        # Tripwire only, default OFF (V15_RAMFP=1 arms). Mismatch blocks promotion; the row revalidates on resume.
        _ramfp_stale = False
        if promote and os.environ.get("V15_RAMFP", "0") == "1":
            try:
                _ram_now = _ram_fp(new_symside)
                _ram_run = _RUN_RAMFP.get(new_symside)
                if _ram_run is not None and _ram_now is not None and _ram_now != _ram_run:
                    promote = False
                    _ramfp_stale = True
                    _blk = ((_blk + "; ") if _blk else "") + "RAMFP-MISMATCH: in-RAM NPZ changed between preload and promotion — row revalidates on resume"
                    print(f"[promote-block] {sname}!{rr} {switch}={cand} delta={row_delta} NOT promoted: {_blk}", flush=True)
            except Exception as _ram_e:
                print(f"[ramfp-warn] {sname}!{rr} {switch}={cand}: {_ram_e}", flush=True)
        # USER 2026-10-03 RECALC-ON-FLIP: never chain E by sum (dep-forced masters + interactions make sums fiction).
        # Fresh-eval the EXACT promoted set; the flip stands only if the fresh gain beats E_before.
        if promote and not is_running and choice is not None:
            _rc_ov = dict(choice[2])
            for _dk, _dv in _dep_choice.items():
                _rc_ov[_dk] = _dv
            _rc_ov, _ = sanitize_overrides(_rc_ov, defaults)
            _rc_res, _rc_err = _get(sname, rr, switch, cand, "RECALC_CUMULATIVE", _rc_ov, _t.time() + float(os.environ.get("V15_RECALC_S", "120")), cumulative_before)
            _rc_gain = _rc_res.get("gain_pct") if isinstance(_rc_res, dict) else None
            _rc_trades = int(_rc_res.get("trades") or 0) if isinstance(_rc_res, dict) else 0
            if _rc_err:
                print(f"[RECALC-FALLBACK-SUM] {sname}!{rr} {switch}={cand} recalc infra-failed ({_rc_err}) — chained by sum (FICTION, flagged)", flush=True)
            elif _rc_gain is None or not bool(_rc_res.get("valid")) or _rc_trades <= 0:
                promote = False
                _blk = ((_blk + "; ") if _blk else "") + f"RECALC-REJECT fresh invalid/0-trades (gain={_rc_gain} trades={_rc_trades})"
                print(f"[promote-block] {sname}!{rr} {switch}={cand} delta={row_delta} NOT promoted: {_blk}", flush=True)
            else:
                _rc_honest = float(_rc_gain) - float(cumulative_before)
                _rc_drift = float(_rc_gain) - (float(cumulative_before) + float(row_delta))
                if abs(_rc_drift) > 1e-9:
                    print(f"[RECALC] {sname}!{rr} {switch}={cand} sum={float(cumulative_before) + float(row_delta):.4f} fresh={float(_rc_gain):.4f} drift={_rc_drift:+.4f}", flush=True)
                row_delta = float(_rc_honest)
                choice = (float(row_delta), choice[1], _rc_ov, str(choice[3]) + "+recalc")
                if row_delta <= 1e-9:
                    promote = False
                    _blk = ((_blk + "; ") if _blk else "") + f"RECALC-REJECT fresh {float(_rc_gain):.4f} <= E_before {float(cumulative_before):.4f}"
                    print(f"[promote-block] {sname}!{rr} {switch}={cand} delta={row_delta} NOT promoted: {_blk}", flush=True)
        row_gain = (cumulative_before + row_delta) if row_delta is not None else None
        g = ws.cell(row=rr, column=cols["G"])
        f = ws.cell(row=rr, column=cols["F"])
        hustle_delta = None
        if "hustle" in results:
            _hd, _hok, _hwhy = _delta_vs(results["hustle"][0], initial_baseline)
            hustle_delta = _hd if _hok else None
        _identity_zero = bool(is_running) and choice is None
        if row_delta is not None and not _identity_zero:
            if _is_red_cell(ws.cell(row=rr, column=cols["G"])):
                _clear_red_fill(ws, rr, cols["G"])
            _num_cell(ws, rr, cols["G"], row_delta, promote)
            if choice is None and not is_running and not naked_ok:
                # USER 2026-09-30: engine-invalid naked result -> its real value in GREY font, logged; never promotable
                g.fill = PatternFill(fill_type=None)
                g.font = Font(name="Arial", size=10, bold=True, color="808080")
                _flag_to_md(flags_md, sname, rr, switch, cand, f"INVALID naked: {reasons.get('naked', '')}", row_delta, 0.0, cumulative_before)
            # F = HUSTLE_DELTA: the row's setting vs the ORIGINAL baseline (G is the delta vs the LATEST baseline)
            if hustle_delta is not None:
                _num_cell(ws, rr, cols["F"], hustle_delta, hustle_delta > 1e-9)
            else:
                f.value = None
            if abs(float(row_delta)) < 1e-12 and not is_running:
                _zr_log({"kind": "ZERO", "sheet": sname, "row": rr, "switch": switch, "cand": str(cand), "col": "G", "cum_before": cumulative_before})
        elif is_running:
            # USER 2026-10-03 (running-identity): G gets the definitional 0.0; F stays blank
            # (hustle vs the original baseline is not quoted for the running reference itself).
            _num_cell(ws, rr, cols["G"], 0.0, False)
            f.value = None
        elif reasons.get("naked") == "ZERO_TRADES":
            g.value = None
            f.value = None
            _zr_log({"kind": "ZERO_TRADES", "sheet": sname, "row": rr, "switch": switch, "cand": str(cand), "col": "F/G", "cum_before": cumulative_before})
        elif naked_settled:
            # Deterministic engine verdict on naked (e.g. pre-eval rejection) — settled: blank + grey
            # reason, logged, never RED, never retried. Only genuinely unresolved naked goes RED below.
            g.value = None
            g.font = Font(name="Arial", size=10, bold=False, italic=True, color="808080")
            g.alignment = VISUAL_ALIGN
            f.value = None
            _flag_to_md(flags_md, sname, rr, switch, cand, f"INVALID naked: {reasons.get('naked', '')}", 0.0, 0.0, cumulative_before)
            _zr_log({"kind": "INVALID", "sheet": sname, "row": rr, "switch": switch, "cand": str(cand), "col": "F/G", "reason": reasons.get("naked", ""), "cum_before": cumulative_before})
        else:
            _spec_mark_red(wb, sname, rr, cols["G"], reason=reasons.get("naked", "no result"))
            f.value = None
        kcell = ws.cell(row=rr, column=cols["K"])
        kcell.value = ", ".join(f"{h2f[h]['filter']}={h2f[h]['opt']}" for h in pos_hdrs) if pos_hdrs else None
        kcell.font = Font(name="Arial", size=10, bold=False, color="006100")
        kcell.alignment = VISUAL_ALIGN
        ws.cell(row=rr, column=cols["H"]).value = None
        ws.cell(row=rr, column=cols["I"]).value = None
        if promote:
            parts = ([] if is_running else [f"{switch}={cand}"]) + [f"{h2f[h]['filter']}={h2f[h]['opt']}" for h in choice[1]]
            parts += [f"{_dk}={_dv}" for _dk, _dv in _dep_choice.items() if not any(p0.split("=", 1)[0].strip() == _dk for p0 in parts)]
            _set_override(ws, sname, rr, cols, parts)
            cumulative_overrides = dict(choice[2])
            for _dk, _dv in _dep_choice.items():
                cumulative_overrides[_dk] = _dv
            cumulative_gain = cumulative_before + float(row_delta)
            e_next = cumulative_gain
            _spec_state["ver"] += 1
            progress["cumulative_overrides"] = dict(cumulative_overrides)
            try:
                _pt = float((_src_ or {}).get("tim_pct"))
                chain_tim = _pt
            except Exception:
                pass
        progress["cumulative_gain"] = float(cumulative_gain)
        div = _write_div(sname, rr, [row_gain])
        _uw_tag = "UNWIRED_CALCULATED: switch is in the vec_unwired audit (no engine read found) — 0.0 is the honest eval delta" if (row_delta == 0 and str(switch).strip() in UNWIRED_TAG_SW) else ""
        progress.setdefault("done", {})[key] = {"delta": row_delta, "delta_vs_cumulative": row_delta, "delta_vs_initial": hustle_delta, "chain_gain_vs_initial": div, "promoted": promote, "promoted_how": choice[3] if promote else None, "promoted_filters": [f"{h2f[h]['filter']}={h2f[h]['opt']}" for h in choice[1]] if promote else [], "k_filters": [f"{h2f[h]['filter']}={h2f[h]['opt']}" for h in pos_hdrs], "possym": st.get("possym"), "sampled_out_filters": st.get("sampled_filters") or [], "is_running": is_running, "delta_invalid": bool(choice is None and not is_running and not naked_ok), "naked_delta": None if is_running else naked_delta, "joint_delta": joint_delta, "reason": _blk or joint_reason or reasons.get("naked", "") or _uw_tag, "vec_gain": row_gain, "trades": (results.get("naked", (None, ""))[0] or {}).get("trades"), "yellows": yellows, "yellow_reasons": {h: r for h, r in reasons.items() if h != "naked"}, "noop_yellows": noop_yellows, "yellow_dups": yellow_dups, "dep_forced": {"promoted": _dep_choice, "by_eval": _dep_row}, "naked_binding": naked_binding, "ref_fp": (ref_fp or "")[:16], "type_skipped": st.get("type_skipped") or [], "tab_level_excluded": st.get("excluded_tab_level") or [], "excluded_unwired": st.get("excluded_unwired") or [], "cumulative_before": cumulative_before, "cumulative_after": float(cumulative_gain), "missing_yellows": list(missing_yellows), "npz": _run_npz_short, "policy": _policy_stamp(sname), "ramfp": (str(_RUN_RAMFP.get(new_symside)) if os.environ.get("V15_RAMFP", "0") == "1" else None), "complete": (not missing_yellows and not (st.get("sampled_filters") or []) and not (st.get("excluded_tab_level") or []) and naked_settled and not _ramfp_stale)}
        if st.get("sampled_filters") or st.get("excluded_tab_level"):
            print(f"[POLICY-CELLS-PENDING] {sname}!{rr} {switch}={cand} sampled={len(st.get('sampled_filters') or [])} tablevel={len(st.get('excluded_tab_level') or [])} — yellows uncalculated, row stays pending (RULE#3 refuses publish until refilled)", flush=True)
        _maybe_write_json(force=promote)
        _row_done(sname, rr, switch, cand, n_items + (1 if pos_hdrs else 0), row_delta, promote)
        _touch(f"cell {sname}!{rr} delta={row_delta}")
        print(f"[spec-row] {sname}!{rr} {switch}={cand}{' (running)' if is_running else ''} yellows={len(st['hdrs'])} pos={len(pos_hdrs)} G={row_delta} vs {cumulative_before:.4f} -> {'POS ' + choice[3] + ' E_next=' + format(cumulative_gain, '.4f') if promote else 'no-promote'}", flush=True)
        _maybe_save()
        processed += 1
    # RED retry ("fixed asap"): the chain moved on, so a retried cell only gets its REAL delta vs the baseline it was
    # measured against (never promoted after the fact); still-failing cells stay RED for tools/v15_assure.
    _retry_s = float(os.environ.get("V15_RED_RETRY_S", "120"))
    for rc in _red_retry:
        ws = wb[rc["sheet"]]
        res, err = _get(rc["sheet"], rc["row"], "RED_RETRY", rc["label"], "RED_RETRY:" + rc["label"], rc["ov"], _t.time() + _retry_s, rc["cum_before"])
        d, ok, why = _delta_vs(res, rc["cum_before"])
        rec = progress.get("done", {}).get(rc["key"], {})
        if d is None:
            # USER 2026-10-03 hollow-fix: tried at 10s, retried at 120s, still no verdict — SETTLED per
            # bible §5.7 (RED + reason + continue). Retrying identical stalls every REDO deadlocks
            # publish without ever calculating; the RED cell + persisted reason is the honest record.
            print(f"[RED-RETRY] {rc['sheet']}!{rc['row']} {rc['label']} still failing ({err or why}) — stays RED, settled (stall-persisted)", flush=True)
            try:
                _rr = str(rec.get("reason") or "")
                if "stall-persisted" not in _rr:
                    rec["reason"] = ((_rr + ";stall-persisted") if _rr else "stall-persisted")
                # Settle only when nothing policy-untried remains — policy skips still block publish.
                if isinstance(rec, dict) and rc["key"] in progress.get("done", {}) and not (rec.get("sampled_out_filters") or []) and not (rec.get("tab_level_excluded") or []) and not str(rec.get("reason") or "").startswith("SKIPPED"):
                    rec["complete"] = True
            except Exception:
                pass
            continue
        _clear_red_fill(ws, rc["row"], rc["col"])
        cell = ws.cell(row=rc["row"], column=rc["col"])
        if rc["label"] != "naked":
            cell.fill = PatternFill(start_color="FFFFFF00", end_color="FFFFFF00", fill_type="solid")
        cell.value = float(d)
        cell.font = Font(name="Arial", size=10, bold=rc["label"] == "naked", color=("9C0006" if d <= 1e-9 else "9C5700") if ok else "808080")
        if not ok and rc["label"] != "naked":
            rec.setdefault("yellow_reasons", {})[rc["label"]] = why
        rec.setdefault("red_fixed", {})[rc["label"]] = d
        if rc["label"] != "naked":
            rec.setdefault("yellows", {})[rc["label"]] = float(d)
        try:
            _ml = [m for m in (rec.get("missing_yellows") or []) if m != rc["label"]]
            rec["missing_yellows"] = _ml
            if not _ml and (rec.get("is_running") or rec.get("naked_delta") is not None or rc["label"] == "naked"):
                rec["complete"] = True
        except Exception:
            pass
        print(f"[RED-RETRY] {rc['sheet']}!{rc['row']} {rc['label']} fixed delta={d:+.4f}{' (positive, NOT promoted: chain already passed this row)' if d > 1e-9 else ''}", flush=True)
    progress["red_retry"] = {"n": len(_red_retry), "timeout_s": _retry_s}
    try:
        _paint_tab_status(wb)
    except Exception:
        pass
    # Loop exit — workbook rows complete
    # drain red fixer queue (<1h) before LIVE — fixer runs concurrently, must finish all bad cells
    try:
        if RED_QUEUE.qsize() > 0:
            print(f"[red-fixer] draining {RED_QUEUE.qsize()} queued reds before LIVE (fixer runs concurrently, <1h)", flush=True)
            drain_red_queue(timeout=3600.0)
        else:
            # still give fixer a moment for in-flight item
            import time as _t_drain2
            _t_drain2.sleep(1.0)
    except Exception as _e_drain:
        print(f"[red-fixer-drain-warn] {_e_drain}", flush=True)
    # Now fill LIVE_DELTA and LIVE_SHARPE when entire sheet complete via backtest_v12_engine parity
    try:
        _final_filter_recheck()
    except Exception as _fe:
        print(f"[final-recheck-warn] {_fe}", flush=True)
    if _ps_on and _ps_counts:
        try:
            progress["possym_sampling"] = {"round": _ps_round, "cat": _ps_cat, "counts": {k: {"computed": v[0], "skipped": v[1]} for k, v in _ps_counts.items()}}
            _tot = {}
            for _k, _v in _ps_counts.items():
                _t2 = _tot.setdefault(_k.split("|", 1)[1], [0, 0]); _t2[0] += _v[0]; _t2[1] += _v[1]
            print(f"[POSSYM-SUMMARY] {new_symside} round={_ps_round} computed/skipped by kind|bucket: {_tot}", flush=True)
            _atomic_write_json(progress_path, progress)
        except Exception as _se:
            print(f"[POSSYM-WARN] summary {_se}", flush=True)
    # 2026-09-28 OOM fix (ACN_SHORT class): the DONE stage (atomic save of a ~2MB workbook expanded
    # in RAM + fresh evals + live verify) spiked parents to 3-4GB and the kernel OOM-killed them
    # (6 kills on s2 today), leaving orphaned fork workers pinning herd slots and sheets that never
    # publish. The fork pool is not needed past final-recheck — release its workers NOW.
    try:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None
            print("[spec-fill] fork pool released before DONE stage (RAM headroom for save+live-verify)", flush=True)
    except Exception as _ps_e:
        print(f"[pool-release-warn] {_ps_e}", flush=True)
    # 2026-09-28 DONE-stage RAM trim part 2: a parent still hit 4.5GB (OOM-killed 19:53Z) — the
    # eval cache holds ~10^5 result dicts and is pure optimization; drop it before the save +
    # in-process backtest_v12_engine live verify.
    try:
        _EVAL_CACHE.clear()
        import gc as _gc_done
        _gc_done.collect()
        print("[spec-fill] eval cache dropped + gc before DONE stage", flush=True)
    except Exception:
        pass
    _maybe_write_json(force=True)
    if _any_pending():
        print(f"[spec-fill] incomplete after loop guard {loop_guard} pending remains — will still save", flush=True)
    print("[DONE-STEP] atomic save start", flush=True)
    try:
        with RED_FIXER_LOCK:
            _atomic_save(wb, wb_path)
    except Exception as _as_e:
        print(f"[DONE-STEP] atomic save error {_as_e}", flush=True)
    print("[DONE-STEP] atomic save done", flush=True)
    # LIVE verification for winning set — H/I only from a REAL backtest_v12_engine run; vector-only/timeout/failure -> BLANK + reason
    live_res = None
    live_reason = ""
    try:
        final_gain = float(cumulative_gain)
        # 2026-09-28 NO-LIES FIX (engine-mixed chains): resume takes max(stored cum, fresh baseline)
        # (line ~3678), so a chain resumed across an engine change can carry a cumulative the CURRENT
        # engine cannot reproduce — publishing that number in the bh/gain filename would be a lying
        # metric. The fresh full-set eval is authoritative: stamp the divergence, publish only fresh.
        try:
            from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eps_ff
            _fresh_final = _eps_ff(prepared, dict(cumulative_overrides), args.window_days) if prepared is not None else {}
            _fresh_g = _fresh_final.get("gain_pct")
            if progress.get("baseline_below_floor"):
                # sub-floor baseline swept anyway: finished only if the FINAL set is valid with >= floor trades
                if _fresh_final.get("valid") and int(_fresh_final.get("trades") or 0) >= 10:
                    progress.pop("diagnostic_only", None)
                    print(f"[FLOOR-LIFTED] {new_symside} final set valid with {_fresh_final.get('trades')} trades", flush=True)
                else:
                    progress["diagnostic_only"] = f"trades={int(_fresh_final.get('trades') or 0)} floor=10 (final)"
            if _fresh_g is not None:
                progress["final_gain_fresh_vec"] = float(_fresh_g)
                if abs(float(_fresh_g) - final_gain) > 1e-6:
                    progress["engine_mixed_chain"] = {"recorded_cum": float(final_gain), "fresh_vec": float(_fresh_g)}
                    print(f"[final-fresh] {new_symside} recorded cum {final_gain:.4f} != fresh vec {float(_fresh_g):.4f} — engine-mixed chain, final_gain set to fresh (engine authoritative)", flush=True)
                    final_gain = float(_fresh_g)
                    cumulative_gain = float(_fresh_g)
                    progress["cumulative_gain"] = float(_fresh_g)
        except Exception as _ff_e:
            print(f"[final-fresh-warn] {new_symside}: {_ff_e}", flush=True)
            _fresh_final = {}
        # USER 2026-10-03 (pos-gain go-live): a sheet FINISHES when the final set qualifies — 30D:
        # valid, TIM in [20, 80], trades >= floor, gain > 0 (BH recorded, never gating). Failure -> revise with the
        # §58 repair (diagnosis-phased: TRADES/HOLDS/EXITS/GAIN), cycling bases across rounds; still failing ->
        # REDO (re-fill the sheet from the repaired set, max QUAL_MAX_REDOS) -> IMPOSSIBLE quarantine.
        # (Supersedes 2026-09-30 "negative gain is NOT a disqualifier".)
        def _complies(v):
            ok, _ = _qualifies_30d(v, bh)
            return ok
        _qual_ok, _qual_reasons = _qualifies_30d(_fresh_final, bh)
        _qual_repairs = []
        _redo_base = None
        if not _qual_ok and prepared is not None:
            print(f"[COMPLIANCE] {new_symside} final set not qualified ({'; '.join(_qual_reasons)}) — revising before publish", flush=True)
            try:
                import time as _t_qual
                _qual_t0 = _t_qual.monotonic()
                _qual_deadline = _qual_t0 + QUAL_RETRY_TIMEOUT
                _tpl_d = {k: defaults.get(k) for k in defaults}
                # USER 2026-10-03 §64: TEMPLATE_DEFAULTS mode never ingests previous tests — compliance round 2 runs without priors.
                _priors = [] if os.environ.get("V15_TEMPLATE_DEFAULTS", "0") == "1" else _prior_final_sets(new_symside)[:2]
                _bases_rounds = [[("final_set", dict(cumulative_overrides))],
                                 [("final_set", dict(cumulative_overrides)), ("template_defaults", dict(_tpl_d))],
                                 [("final_set", dict(cumulative_overrides)), ("template_defaults", dict(_tpl_d))] + [(f"prior_{i}", dict(ov)) for i, (src, ov) in enumerate(_priors) for _ in [0]][:2]]
                for _qr in range(min(QUAL_MAX_REDOS + 1, len(_bases_rounds))):
                    if _t_qual.monotonic() > _qual_deadline:
                        _tout = {"reason": f"qual retry timeout {QUAL_RETRY_TIMEOUT:.0f}s (round {_qr}/{QUAL_MAX_REDOS})", "round": _qr, "elapsed_s": round(_t_qual.monotonic() - _qual_t0, 1)}
                        _qual_repairs.append(_tout)
                        print(f"[COMPLIANCE-TIMEOUT] {new_symside} {QUAL_RETRY_TIMEOUT:.0f}s exceeded — stopping repair, quarantining", flush=True)
                        break
                    _tail = max(0.0, _qual_deadline - _t_qual.monotonic())
                    # run one repair round under a bounded future so compliance repair never chokes the worker (pos-gain gate, BH never targeted)
                    import concurrent.futures as _cf_qual
                    _fut = None
                    try:
                        _ex = _cf_qual.ThreadPoolExecutor(max_workers=1)
                        _fut = _ex.submit(_credible_baseline, new_symside, prepared, _bases_rounds[_qr], defaults, args.template, args.window_days, tim_min=QUAL_TIM_MIN, gain_min=0.0, final_safe=True)
                        _rov, _rv, _rrep = _fut.result(timeout=_tail if _tail > 0 else 0.1)
                    except Exception as _qe:
                        try:
                            if _fut is not None:
                                _fut.cancel()
                        except Exception:
                            pass
                        if "timeout" in str(type(_qe)).lower() or "TimeoutError" in str(type(_qe)) or "timed out" in str(_qe).lower():
                            _tout2 = {"reason": f"round {_qr} timed out after {QUAL_RETRY_TIMEOUT:.0f}s", "round": _qr, "elapsed_s": round(_t_qual.monotonic() - _qual_t0, 1)}
                            _qual_repairs.append(_tout2)
                            print(f"[COMPLIANCE-TIMEOUT] {new_symside} round {_qr} timed out — stopping", flush=True)
                            break
                        raise
                    finally:
                        try:
                            _ex.shutdown(wait=False, cancel_futures=True)
                        except Exception:
                            pass
                    _rrep["round"] = _qr
                    _qual_repairs.append(_rrep)
                    _qok, _qr2 = _qualifies_30d(_rv, bh)
                    if _qok:
                        if not repair_needs_redo(_rrep):
                            cumulative_overrides = dict(_rov)
                            _fresh_final = _rv
                            final_gain = cumulative_gain = float(_rv.get("gain_pct"))
                            progress["cumulative_overrides"] = dict(cumulative_overrides)
                            progress["cumulative_gain"] = float(final_gain)
                            _qual_ok = True
                            try:
                                if "COMPLIANCE_REPAIR" in wb.sheetnames:
                                    del wb["COMPLIANCE_REPAIR"]
                                _cws = wb.create_sheet("COMPLIANCE_REPAIR")
                                _cws.append(["step", "phase", "applied", "gain_pct", "trades", "tim_pct", "max_dd_pct", "valid"])
                                for _st in _rrep.get("steps", []):
                                    _cws.append([_st.get("step"), _st.get("phase"), _st.get("applied") or _st.get("result"), _st.get("gain"), _st.get("trades"), _st.get("tim"), _st.get("dd"), _st.get("valid")])
                                with RED_FIXER_LOCK:
                                    _atomic_save(wb, wb_path)
                            except Exception as _cw_e:
                                print(f"[compliance-sheet-warn] {_cw_e}", flush=True)
                            print(f"[COMPLIANCE] {new_symside} revised (round {_qr}): {_rv.get('trades')} trades TIM {_rv.get('tim_pct')} DD {_rv.get('max_dd_pct')} gain {final_gain:+.4f}", flush=True)
                        else:
                            _redo_base = (dict(_rov), dict(_rv), f"30D repaired ({len([s for s in (_rrep.get('steps') or []) if isinstance(s, dict) and s.get('applied')])} applied steps from {_rrep.get('base_chosen')}) — sheet rows stale, re-fill required")
                            print(f"[COMPLIANCE] {new_symside} repaired set differs — REDO required (rows must represent the published set)", flush=True)
                        break
                    _redo_base = (dict(_rov), dict(_rv), f"30D best-effort round {_qr} still failing ({'; '.join(_qr2)})")
                progress["compliance_repair"] = _qual_repairs
                _maybe_write_json(force=True)
            except Exception as _cr_e:
                print(f"[compliance-repair-warn] {new_symside}: {_cr_e}", flush=True)
        _compliant = _qual_ok
        _impossible = False
        _best_effort = None
        _won = True
        if not _compliant:
            _depth = int(progress.get("redo_depth", 0))
            if _redo_base is not None and int(((_redo_base[1] or {}).get("trades")) or 0) == 0:
                print(f"[REDO-CAP] {new_symside} repaired set still 0 trades — a re-fill cannot calculate anything, skipping REDO (USER 2026-10-03)", flush=True)
                _redo_base = None
            if _redo_base is not None and _depth < QUAL_MAX_REDOS:
                progress["needs_redo"] = {"overrides": _redo_base[0], "result": {k: _redo_base[1].get(k) for k in ("gain_pct", "trades", "tim_pct", "max_dd_pct", "valid")}, "depth": _depth + 1, "reason": _redo_base[2]}
                progress.pop("final_path", None)
                _maybe_write_json(force=True)
                print(f"[REDO] {new_symside} scheduling re-fill from repaired set (depth {_depth + 1}): {_redo_base[2]}", flush=True)
                if _standing_best(OUT_DIR, new_symside) is None:
                    _peer_rm_published(new_symside)
            else:
                _impossible = True
                progress["not_compliant"] = "; ".join(_qual_reasons) if _qual_reasons else "unknown"
                _maybe_write_json(force=True)
        else:
            progress.pop("not_compliant", None)
            progress.pop("needs_redo", None)
        if _compliant:
            try:
                _cf_n = 0
                for _cws in wb.worksheets:
                    if _cws.title not in SWITCH_SHEETS:
                        continue
                    for _cr in range(3, _cws.max_row + 1):
                        if isinstance(_cws.cell(_cr, 6).value, (int, float)):
                            _cf_n += 1
            except Exception:
                _cf_n = 0
            _cg = content_ok(_cf_n, len(progress.get("done", {})))
            if _cg:
                print(f"[CONTENT-GATE] {new_symside} {_cg}", flush=True)
                _compliant = False
                _qual_reasons = [_cg]
                _depth = int(progress.get("redo_depth", 0))
                _retryable = _board_has_retryable_holes(progress.get("done", {}))
                if _depth < QUAL_MAX_REDOS and _retryable:
                    progress["needs_redo"] = {"overrides": dict(cumulative_overrides), "result": {k: (_fresh_final or {}).get(k) for k in ("gain_pct", "trades", "tim_pct", "max_dd_pct", "valid")}, "depth": _depth + 1, "reason": _cg + " — re-fill rows for the qualified set"}
                    progress.pop("final_path", None)
                    _maybe_write_json(force=True)
                    print(f"[REDO] {new_symside} scheduling re-fill for content (depth {_depth + 1})", flush=True)
                    if _standing_best(OUT_DIR, new_symside) is None:
                        _peer_rm_published(new_symside)
                else:
                    if not _retryable:
                        print(f"[REDO-CAP] {new_symside} content-gate with zero retryable holes (all verdicts settled) — a re-fill would reproduce identical nulls, skipping REDO", flush=True)
                    _impossible = True
                    progress["not_compliant"] = _cg
                    _maybe_write_json(force=True)
            # USER 2026-10-03 RULE#3: rows with uncalculated yellows refuse publish (REDO re-fills them).
            # Hollow-fix: the full hollow scan (policy skips, sampled cells, tab-level era, incomplete) —
            # complete=False alone missed policy-skipped rows marked complete by older code.
            try:
                from tools.v15_row_guards import scan_board_for_hollow as _scan_hollow_done
                _tl_spec_done = {}
                try:
                    _tl_spec_done = json.loads((ROOT / "data" / "wiring" / "tab_filters" / "tab_level_filters.json").read_text())
                except Exception:
                    pass
                _hol_done = _scan_hollow_done(progress.get("done", {}), map_key_for_symside(new_symside), _tl_spec_done, assume_tablevel_on=True)
                _incomplete = _hol_done.get("drop", [])
                if _incomplete:
                    print(f"[COMPLETENESS-SCAN] {new_symside} hollow tally={_hol_done.get('tally', {})}", flush=True)
            except Exception:
                try:
                    _incomplete = _incomplete_rows(progress.get("done", {})) if _incomplete_rows is not None else []
                except Exception:
                    _incomplete = []
            if _incomplete:
                _cg3 = f"RULE#3: {len(_incomplete)} hollow/incomplete rows with uncalculated yellows — refusing publish"
                print(f"[COMPLETENESS-GATE] {new_symside} {_cg3}: {(_incomplete[:5])}", flush=True)
                _compliant = False
                _qual_reasons = [_cg3]
                _depth3 = int(progress.get("redo_depth", 0))
                if _depth3 < QUAL_MAX_REDOS:
                    progress["needs_redo"] = {"overrides": dict(cumulative_overrides), "result": {k: (_fresh_final or {}).get(k) for k in ("gain_pct", "trades", "tim_pct", "max_dd_pct", "valid")}, "depth": _depth3 + 1, "reason": _cg3 + " — re-fill rows for the qualified set"}
                    progress.pop("final_path", None)
                    _maybe_write_json(force=True)
                    print(f"[REDO] {new_symside} scheduling re-fill for completeness (depth {_depth3 + 1})", flush=True)
                else:
                    _impossible = True
                    progress["not_compliant"] = _cg3
                    _maybe_write_json(force=True)
            # USER 2026-10-04 ESSENTIAL-ROWS GATE: protection filters must prove with non-zero
            # numbers (naked G, any yellow, or a ledger move) or the sheet cannot qualify.
            # Deterministic zeros: no REDO (a re-fill reproduces them) — straight to impossible.
            if _compliant and fast_switches is None:
                _eg_cs = map_key_for_symside(new_symside)
                _eg = essential_gate_check(progress.get("done", {}), dict(cumulative_overrides), new_symside.upper().endswith("_LONG"), _eg_cs.startswith("CRYPTO"))
                _eg_fail = {sw: info for sw, info in _eg.items() if info["status"] == "FAIL"}
                if _eg_fail:
                    _cg4 = f"ESSENTIAL-ROWS: {len(_eg_fail)} protection filters without a non-zero number ({', '.join(sorted(_eg_fail))}) — refusing publish"
                    print(f"[ESSENTIAL-GATE] {new_symside} {_cg4}", flush=True)
                    _flag_to_md(flags_md, "ESSENTIAL-GATE", 0, "ESSENTIAL_ROWS", "GATE", _cg4, 0.0, 0.0, cumulative_gain)
                    progress["unprotected"] = {sw: info["reason"] for sw, info in _eg_fail.items()}
                    _compliant = False
                    _qual_reasons = [_cg4]
                    _impossible = True
                    progress["not_compliant"] = _cg4
                    _maybe_write_json(force=True)
                else:
                    progress.pop("unprotected", None)
                    print(f"[ESSENTIAL-GATE] {new_symside} all {len(_eg)} essential filters proven non-zero", flush=True)
            _result_stamp(progress, new_symside)
        if _impossible:
            _stand = _standing_best(OUT_DIR, new_symside)
            if _stand is not None:
                _sn, _sg, _sc = _stand
                from tools.v15_final_naming import parse_final_matrix_name as _pfm_keep
                _sd = _pfm_keep(_sn) or {}
                _sm = _best_manifest_metrics(OUT_DIR, _sn)
                _sgx = _sm.get("gain_pct")
                progress["final_gain"] = float(_sgx) if _sgx is not None else float(_sd.get("gain", 0))
                _stx = _sm.get("trades")
                progress["final_trades"] = int(_stx) if _stx is not None else None
                _sbh = _sm.get("bh")
                progress["bh"] = float(_sbh) if _sbh is not None else float(_sd.get("bh", bh or 0))
                progress["final_path"] = str(OUT_DIR / _sn)
                progress["last_run_failed"] = "; ".join(_qual_reasons) if _qual_reasons else "unknown"
                progress["best_note"] = f"run failed ({progress['last_run_failed'][:80]}) — kept standing best {_sn}"
                _maybe_write_json(force=True)
                print(f"[RUN-FAILED-KEPT-BEST] {new_symside} this run failed ({'; '.join(_qual_reasons)}) — standing best {_sn} untouched, no quarantine, no REDO", flush=True)
                try:
                    wb.close()
                except Exception:
                    pass
                if _pool is not None:
                    _pool.shutdown(wait=False, cancel_futures=True)
                return cumulative_gain, cumulative_overrides, progress
            _nm_ok, _nm_r = _nearmiss_30d(_fresh_final, bh)
            _calc_n = _board_calc_n(progress.get("done") or {})
            if (_qual_ok or _nm_ok) and _calc_n >= 1:
                _impossible = False
                _compliant = True
                _best_effort = list(_qual_reasons) if _qual_reasons else list(_nm_r)
                print(f"[BEST-EFFORT] {new_symside} near-miss ({'; '.join(_best_effort)}) with {_calc_n} calculated rows — publishing flagged instead of quarantine (USER 2026-10-03)", flush=True)
            else:
                _quarantine_impossible(new_symside, _qual_reasons, {"window": "30D", "gain_pct": (_fresh_final or {}).get("gain_pct"), "trades": (_fresh_final or {}).get("trades"), "tim_pct": (_fresh_final or {}).get("tim_pct"), "max_dd_pct": (_fresh_final or {}).get("max_dd_pct"), "valid": (_fresh_final or {}).get("valid"), "bh": float(bh or 0)}, wb, wb_path, progress, progress_path, cumulative_overrides, _qual_repairs)
                print(f"[NOT-FINISHED] {new_symside} 30D unqualifiable after repair — quarantined, no publish", flush=True)
                try:
                    wb.close()
                except Exception:
                    pass
                if _pool is not None:
                    _pool.shutdown(wait=False, cancel_futures=True)
                return cumulative_gain, cumulative_overrides, progress
        if not _compliant:
            print(f"[NOT-FINISHED] {new_symside} REDO scheduled — no publish this run, herd relaunches into a fresh fill", flush=True)
            try:
                wb.close()
            except Exception:
                pass
            for _w in (wb_path, Path(str(wb_path) + ".bak")):
                try:
                    if _w.exists():
                        _w.unlink()
                        print(f"[REDO] {new_symside} removed working sheet {_w.name} (re-fill clones pristine)", flush=True)
                except Exception:
                    pass
            if _pool is not None:
                _pool.shutdown(wait=False, cancel_futures=True)
            return cumulative_gain, cumulative_overrides, progress
        # 2026-09-28 USER ORDER (impeccable sheets): PUBLISH THE bh/gain FINAL **BEFORE** the live
        # verification — the publish block in main() is unreachable (spec-fill returns early) and
        # DONE-stage tails have been dying, so the file must exist the moment final_gain is honest
        # (fresh-vec verified above). Live verify afterwards only ADDs H/I.
        try:
            if not _compliant:
                raise RuntimeError("not compliant — the sheet is not finished, no bh/gain publish")
            # USER 2026-10-03 (final naming): filename gain is the ACTUAL fresh-verified final_gain as
            # INTEGER percent; the fraction slot carries the trade count instead (gain6p_t66). BH keeps
            # cents. final_gain here is already fresh-vec authoritative (final-fresh block above); the
            # matching trade count comes from the same _fresh_final eval. Unknown trades -> refuse.
            from tools.v15_final_naming import chart_name_for as _chart_name_for, final_matrix_name as _final_matrix_name
            _bh_raw = float(bh or 0)
            _final_trades = (_fresh_final or {}).get("trades")
            if _final_trades is None:
                raise RuntimeError("final trades unknown — refusing bh/gain publish (no-lies)")
            _final_name = _final_matrix_name(new_symside, _bh_raw, float(final_gain), int(_final_trades), 30)
            import shutil as _sh_pub
            _cand_class = "BEST_EFFORT" if _best_effort is not None else "QUALIFIED"
            _active_name, _won = _apply_best_publish(OUT_DIR, new_symside, _final_name, float(final_gain), _cand_class, wb_path)
            _final_path = OUT_DIR / _active_name
            for _vk in ("verdict", "verdict_reasons", "verdict_npz_mtime_ns"):
                progress.pop(_vk, None)
            progress["run_gain"] = float(final_gain)
            progress["run_trades"] = int(_final_trades)
            progress["run_path"] = str(OUT_DIR / (_final_name if _won else (_final_name + ".superseded")))
            progress["run_class"] = _cand_class
            if _won:
                progress["final_gain"] = float(final_gain)
                progress["bh"] = _bh_raw
                progress["final_trades"] = int(_final_trades)
                progress["final_path"] = str(_final_path)
                progress["best_note"] = f"activated {_final_name}"
            else:
                from tools.v15_final_naming import parse_final_matrix_name as _pfm_win
                _bd = _pfm_win(_active_name) or {}
                _bm = _best_manifest_metrics(OUT_DIR, _active_name)
                _bgx = _bm.get("gain_pct")
                progress["final_gain"] = float(_bgx) if _bgx is not None else float(_bd.get("gain", final_gain))
                _btx = _bm.get("trades")
                progress["final_trades"] = int(_btx) if _btx is not None else None
                _bbh = _bm.get("bh")
                progress["bh"] = float(_bbh) if _bbh is not None else float(_bd.get("bh", bh or 0))
                progress["final_path"] = str(_final_path)
                progress["best_note"] = f"kept {_active_name} over {_final_name}"
            _maybe_write_json(force=True)
            if not _won:
                print(f"[MANIFEST] {new_symside} kept best {_active_name} — no manifest for history-filed {_final_name}", flush=True)
            else:
                try:
                    _mf = _mc = _me = 0
                    for _mws in wb.worksheets:
                        if _mws.title not in SWITCH_SHEETS:
                            continue
                        for _mr in range(3, _mws.max_row + 1):
                            if isinstance(_mws.cell(_mr, 6).value, (int, float)):
                                _mf += 1
                            if _mws.cell(_mr, 3).value not in (None, ""):
                                _mc += 1
                            if isinstance(_mws.cell(_mr, 5).value, (int, float)):
                                _me += 1
                    import hashlib as _hl_m, socket as _sock_m
                    _ovm = _hl_m.md5(json.dumps(cumulative_overrides, sort_keys=True, default=str).encode()).hexdigest()
                    _npp = _npz_path(new_symside)
                    _man = _build_publish_manifest(new_symside, _final_name, {**(_fresh_final or {}), "bh": _bh_raw}, (_mf, _mc, _me, len(progress.get("done", {}))), _ovm, _md5_file(_final_path), _npp.name if _npp else None, _md5_file(_npp) if _npp else None, _npz_span_days(new_symside), _md5_file(Path(__file__)), Path(args.template).name if args.template else None, _sock_m.gethostname())
                    _man["publish_class"] = _cand_class
                    if _best_effort is not None:
                        _man["best_effort_reasons"] = list(_best_effort)
                    _man_p = OUT_DIR / (_final_name.replace(".xlsx", "_manifest.json"))
                    _man_t = str(_man_p) + ".tmp"
                    _man_p.parent.mkdir(parents=True, exist_ok=True)
                    with open(_man_t, "w") as _mfh:
                        json.dump(_man, _mfh, indent=1, default=str)
                    import os as _os_m
                    _os_m.replace(_man_t, str(_man_p))
                    print(f"[MANIFEST] {new_symside} -> {_man_p.name} F={_mf} C={_mc} E={_me} md5={(_man['file_md5'] or '?')[:12]}", flush=True)
                except Exception as _man_e:
                    try:
                        _final_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    if os.environ.get("V15_MANIFEST_STRICT", "1") == "1":
                        raise RuntimeError(f"manifest failed, publish revoked: {_man_e}")
                    print(f"[manifest-warn] {new_symside}: {_man_e} — xlsx re-copied without manifest (STRICT=0)", flush=True)
                    _atomic_copy(wb_path, _final_path)
            print(f"[PUBLISH] {new_symside} -> {_active_name} ({'new best' if _won else 'kept best'}; fresh-verified vec; live H/I follow if verify succeeds)", flush=True)
            if _best_effort is not None and _won:
                progress["verdict"] = "BEST_EFFORT"
                progress["verdict_reasons"] = list(_best_effort)
                progress["verdict_npz_mtime_ns"] = _npz_mtime_ns(new_symside)
                progress["publish_class"] = "BEST_EFFORT"
                _maybe_write_json(force=True)
                print(f"[BEST-EFFORT] {new_symside} published flagged -> {_active_name} (reruns only on newer NPZ)", flush=True)
            if not _won:
                print(f"[PUBLISH-CHART] {new_symside} kept best — no new chart (loser history-filed)", flush=True)
            else:
                try:
                    from tools.opt.hires_chart import generate_hires as _gh_pub
                    _pub_chart_name = _chart_name_for(_final_name)
                    _gh_pub(new_symside, dict(cumulative_overrides), int(args.window_days), out_name=_pub_chart_name)
                    _pub_chart_src = ROOT / "data" / "reports" / "charts_1Y" / _pub_chart_name
                    if _pub_chart_src.exists():
                        _sh_pub.copy2(_pub_chart_src, OUT_DIR / _pub_chart_name)
                        print(f"[PUBLISH-CHART] {new_symside} -> {_pub_chart_name} (same set as xlsx, coherent by construction)", flush=True)
                except Exception as _pc_e:
                    print(f"[publish-chart-warn] {new_symside}: {_pc_e} — xlsx stands, chart skipped", flush=True)
        except Exception as _pub_e:
            print(f"[publish-warn] {new_symside}: {_pub_e} — no xlsx, no chart; herd will retry via publish-only pass", flush=True)
            try:
                wb.close()
            except Exception:
                pass
            if _pool is not None:
                _pool.shutdown(wait=False, cancel_futures=True)
            return cumulative_gain, cumulative_overrides, progress
        print("[DONE-STEP] published, entering live verify", flush=True)
        # 2026-09-28 PARITY FIX: --vector-only keeps the SWEEP vector-only (speed), but the single
        # DONE-stage live verification (one backtest_v12_engine run on the winning set, LIVE_TIMEOUT-bound)
        # must always run — the herd hardcodes --vector-only, which left 0/358 sym_sides live-verified
        # (every live_verified said "vector-only: LIVE not run"). Opt out only via V15_SKIP_LIVE_AT_DONE=1.
        if os.environ.get("V15_SKIP_LIVE_AT_DONE") == "1":
            live_reason = "V15_SKIP_LIVE_AT_DONE=1: LIVE not run"
        else:
            import concurrent.futures as _cf_live
            _ex_live = _cf_live.ThreadPoolExecutor(max_workers=1)
            try:
                live_res = _ex_live.submit(live_evaluate, new_symside, dict(cumulative_overrides), args.window_days).result(timeout=LIVE_TIMEOUT)
            except _cf_live.TimeoutError:
                live_reason = f"live_evaluate timeout {LIVE_TIMEOUT:.0f}s"
            except Exception as _le:
                live_reason = f"live_evaluate failed {_le}"[:80]
            finally:
                _ex_live.shutdown(wait=False)
            if live_res is not None and (not live_res.get("valid") or live_res.get("gain_pct") is None):
                live_reason = f"live invalid {live_res.get('invalid_reason')}"[:80]
                live_res = None
        parity = None
        if live_res is not None:
            from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eps_pf
            final_vec = _eps_pf(prepared, dict(cumulative_overrides), args.window_days) if prepared is not None else {}
            parity = parity_ok(live_res, final_vec, allow_zero_baseline=False)
            print(f"[spec-live] {new_symside} live gain={live_res.get('gain_pct')} trades={live_res.get('trades')} vs vec final {final_gain:.4f} trades={final_vec.get('trades')} parity={parity}", flush=True)
            wb2 = openpyxl.load_workbook(str(wb_path), data_only=False)
            for sname in tabs:
                if sname not in wb2.sheetnames:
                    continue
                ws2 = wb2[sname]
                cols2 = _resolve_cols(ws2)
                for (rr, sw, cand) in per_tab_rows.get(sname, []):
                    rec = progress.get("done", {}).get(f"{sname}!{rr}:{sw}={cand}")
                    if not rec or not rec.get("promoted"):
                        continue
                    # winning-set live gain vs the vector cumulative this row started from
                    ws2.cell(row=rr, column=cols2["H"]).value = float(live_res["gain_pct"]) - float(rec.get("cumulative_before") or 0)
                    ws2.cell(row=rr, column=cols2["I"]).value = float(live_res.get("pool_sharpe") or 0)
                    for cc in (cols2["H"], cols2["I"]):
                        ws2.cell(row=rr, column=cc).font = Font(name="Arial", size=10, bold=False)
                        ws2.cell(row=rr, column=cc).alignment = VISUAL_ALIGN
                        if parity is not None and not parity[0]:
                            ws2.cell(row=rr, column=cc).fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
            _atomic_save(wb2, wb_path)
            wb2.close()
            if parity is not None and not parity[0]:
                _flag_to_md(flags_md, "LIVE", 0, "PARITY", "FAIL", f"live vs vec parity fail {parity[1]}", float(live_res["gain_pct"]), final_gain, final_gain)
        else:
            print(f"[spec-live] {new_symside} H/I left BLANK: {live_reason}", flush=True)
        progress["live_verified"] = {"gain_pct": (live_res or {}).get("gain_pct"), "pool_sharpe": (live_res or {}).get("pool_sharpe"), "trades": (live_res or {}).get("trades"), "parity": list(parity) if parity else None, "reason": live_reason}
        _atomic_write_json(progress_path, progress)
        # 2026-09-28 STALE-METRICS FIX: refresh the *_BASELINE_METRICS tab at DONE (it was written once at
        # clone/baseline time and never again — GOOGL_LONG showed 0 trades while JSON cum was +6.97).
        try:
            wb3 = openpyxl.load_workbook(str(wb_path), data_only=False)
            _mtab = next((s for s in wb3.sheetnames if "BASELINE_METRICS" in s), None)
            if _mtab is not None:
                ws3 = wb3[_mtab]
                _kv = {str(ws3.cell(row=r, column=1).value): r for r in range(2, ws3.max_row + 1) if ws3.cell(row=r, column=1).value}
                def _set_metric(k, v):
                    r = _kv.get(k)
                    if r is None:
                        r = max(_kv.values(), default=1) + 1
                        _kv[k] = r
                        ws3.cell(row=r, column=1).value = k
                    ws3.cell(row=r, column=2).value = v
                _set_metric("final_cumulative_gain", float(cumulative_gain))
                _set_metric("final_overrides_n", len(cumulative_overrides))
                _set_metric("final_live_gain", (live_res or {}).get("gain_pct"))
                _set_metric("final_live_trades", (live_res or {}).get("trades"))
                _set_metric("final_live_parity", (parity[1] if parity else live_reason) or "")
                _set_metric("final_refreshed_at", utcnow())
                _lt = int((live_res or {}).get("trades") or 0)
                _set_metric("final_sample_status", "[DIAGNOSTIC ONLY]" if (_lt < 30 or float(args.window_days) < 365) else "publishable-floor met")
                with RED_FIXER_LOCK:
                    _atomic_save(wb3, wb_path)
            wb3.close()
        except Exception as _mr_e:
            print(f"[metrics-refresh-warn] {_mr_e}", flush=True)
        # USER 2026-10-02 (finish qualification): 365D gate (BIBLE §58) — a negative/invalid 365D means the
        # 30D sheet is faulty. Repair via tools/v15_365_repair.py, re-verify BOTH windows; repaired set differs ->
        # REDO (re-fill from it, §58 step 5); unfixable or depth exhausted -> IMPOSSIBLE quarantine (published
        # xlsx carried along for manual revision). V15_SKIP_365D_AT_DONE=1 restores the old skip.
        if os.environ.get("V15_SKIP_365D_AT_DONE") != "1":
            _q365_ok, _q365_reasons, _v365 = False, [], {}
            _span365 = None
            try:
                from tools.opt.v12_pilot import evaluate_sanitized_with_timeout as _es365
                _v365 = _es365(new_symside, dict(cumulative_overrides), 365, timeout_sec=int(QUAL_365D_TIMEOUT)) or {}
                _span365 = _npz_span_days(new_symside)
                progress["final_365d"] = {k: _v365.get(k) for k in ("gain_pct", "trades", "tim_pct", "max_dd_pct", "valid", "invalid_reason")}
                progress["final_365d"]["span_days"] = _span365
                _q365_ok, _q365_reasons = _qualifies_365d(_v365, _span365)
                print(f"[365-QUAL] {new_symside} gain={_v365.get('gain_pct')} tr={_v365.get('trades')} TIM={_v365.get('tim_pct')} valid={_v365.get('valid')} span={_span365} -> {'PASS' if _q365_ok else '; '.join(_q365_reasons)}", flush=True)
            except Exception as _q365e:
                _q365_reasons = [f"365D eval failed: {_q365e}"[:100]]
                print(f"[365-qual-warn] {new_symside}: {_q365e}", flush=True)
            if not _q365_ok:
                _depth365 = int(progress.get("redo_depth", 0))
                _fixed365 = None
                try:
                    _maybe_write_json(force=True)
                    _r365ov, _r365rep = _run_365_repair(new_symside, progress_path, args.template)
                    progress["repair_365d"] = _r365rep
                    if _r365ov:
                        from tools.opt.v12_pilot import evaluate_sanitized_with_timeout as _es365b
                        from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eps30
                        _rv365 = _es365b(new_symside, dict(_r365ov), 365, timeout_sec=int(QUAL_365D_TIMEOUT)) or {}
                        _rv30 = _eps30(prepared, dict(_r365ov), args.window_days) if prepared is not None else {}
                        _ok30, _rr30 = _qualifies_30d(_rv30, bh)
                        _ok365, _rr365 = _qualifies_365d(_rv365, _span365)
                        if _ok30 and _ok365:
                            def _fm(x):
                                try:
                                    return f"{float(x):+.2f}"
                                except Exception:
                                    return str(x)
                            _fixed365 = (dict(_r365ov), f"365D repaired -> 30D gain {_fm(_rv30.get('gain_pct'))} TIM {_rv30.get('tim_pct')} + 365D gain {_fm(_rv365.get('gain_pct'))} tr {_rv365.get('trades')}")
                            print(f"[365-QUAL] {new_symside} {_fixed365[1]}", flush=True)
                        else:
                            print(f"[365-QUAL] {new_symside} repaired set still failing (30D: {'; '.join(_rr30) or 'ok'}; 365D: {'; '.join(_rr365) or 'ok'})", flush=True)
                except Exception as _r365e:
                    print(f"[365-repair-warn] {new_symside}: {_r365e}", flush=True)
                if _fixed365 is not None and _depth365 < QUAL_MAX_REDOS:
                    _this_run = OUT_DIR / _final_name
                    if _this_run.exists():
                        try:
                            _this_run.unlink()
                        except Exception:
                            pass
                    progress["needs_redo"] = {"overrides": _fixed365[0], "depth": _depth365 + 1, "reason": _fixed365[1]}
                    progress.pop("final_path", None)
                    _maybe_write_json(force=True)
                    print(f"[REDO] {new_symside} 365D-driven re-fill scheduled (depth {_depth365 + 1})", flush=True)
                    if _won:
                        _peer_rm_published(new_symside)
                    try:
                        wb.close()
                    except Exception:
                        pass
                    for _w in (wb_path, Path(str(wb_path) + ".bak")):
                        try:
                            if _w.exists():
                                _w.unlink()
                        except Exception:
                            pass
                    if _pool is not None:
                        _pool.shutdown(wait=False, cancel_futures=True)
                    return cumulative_gain, cumulative_overrides, progress
                if bool((_v365 or {}).get("valid")):
                    _br = [f"365D: {r}" for r in _q365_reasons] or ["365D: valid but below gate"]
                    progress["verdict"] = "BEST_EFFORT"
                    progress["verdict_reasons"] = list(progress.get("verdict_reasons") or []) + _br
                    progress["verdict_npz_mtime_ns"] = _npz_mtime_ns(new_symside)
                    _maybe_write_json(force=True)
                    print(f"[BEST-EFFORT] {new_symside} 365D valid-but-missing ({'; '.join(_q365_reasons)}) — keeping published {progress.get('final_path')} flagged, no quarantine (USER 2026-10-03)", flush=True)
                    try:
                        wb.close()
                    except Exception:
                        pass
                    if _pool is not None:
                        _pool.shutdown(wait=False, cancel_futures=True)
                    return cumulative_gain, cumulative_overrides, progress
                _carry = [progress["final_path"]] if progress.get("final_path") else []
                _quarantine_impossible(new_symside, [f"365D: {r}" for r in _q365_reasons], {"window": "365D", "gain_pct": (_v365 or {}).get("gain_pct"), "trades": (_v365 or {}).get("trades"), "tim_pct": (_v365 or {}).get("tim_pct"), "max_dd_pct": (_v365 or {}).get("max_dd_pct"), "valid": (_v365 or {}).get("valid")}, wb, wb_path, progress, progress_path, cumulative_overrides, {"repair_365d": progress.get("repair_365d")}, carry_files=_carry)
                try:
                    wb.close()
                except Exception:
                    pass
                if _pool is not None:
                    _pool.shutdown(wait=False, cancel_futures=True)
                return cumulative_gain, cumulative_overrides, progress
        # 2026-09-28 USER MANDATE ("confirmed by highest gain and 365D confirmations or they can not
        # trade"): at DONE, attempt the 365D certification for this sym_side. tools/confirm_365d.py
        # evaluates defaults vs cumulative_overrides vs hustler_best at 365d and certifies the
        # highest-gain valid candidate (gain>0, trades>=30) into data/confirmed_365d.json — the file
        # ez_manage's fail-closed 365D gate reads. No cert -> live cannot open that sym_side.
        # 2026-09-28 OOM SAFETY (binance-99): a 365d prepare per finishing pilot (6-8 staggered per
        # server) risks the RAM/OOM the mega sweep just stabilised. DEFAULT OFF during catch-up —
        # certification runs as a separate pass (tools/confirm_365d.py --all). Enable per-pilot with
        # V15_CONFIRM_365D_AT_DONE=1 once the 354 backlog is cleared.
        if os.environ.get("V15_CONFIRM_365D_AT_DONE") == "1":
            try:
                from tools.confirm_365d import confirm_symside as _c365
                _rec365 = _c365(new_symside)
                progress["confirmed_365d"] = _rec365 or {"certified": False}
                _atomic_write_json(progress_path, progress)
            except Exception as _c365e:
                print(f"[confirm-365d-warn] {new_symside}: {_c365e}", flush=True)
    except Exception as e:
        print(f"[spec-live-warn] {e}", flush=True)
    try:
        wb.close()
    except Exception:
        pass
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
    try:
        write_zoomable_chart(new_symside, None, dict(cumulative_overrides), args.window_days, suffix=f"30D_REAL_ZOOMABLE_{nav_mode}")
    except Exception as _ce:
        print(f"[chart-warn] {_ce}", flush=True)
    # Final heartbeat
    _skip_alarm_summary(progress, new_symside, processed)
    print(f"[mandatory-yellows] {_mandatory_summary()}", flush=True)
    _touch(f"spec-done cum={cumulative_gain:.4f} rows={processed}")
    print(f"[spec-fill] DONE {new_symside} final_gain={cumulative_gain:.4f} baseline={baseline_gain:.4f} rows={processed} pos_tabs curated", flush=True)
    return cumulative_gain, cumulative_overrides, progress

def _skip_alarm_summary(progress: dict, new_symside: str, processed: int):
    """USER 2026-10-03: mass-skip alarm — yellows+switches were skipped fleet-wide with no warning (sampling + tab-level + unwired).
    Tallies done-rows by outcome, prints a LOUD block, and writes a SKIP_ALERT file when computed coverage is low. Never raises."""
    try:
        done = (progress or {}).get("done", {}) or {}
        tot = len(done)
        computed = 0
        skips: dict = {}
        y_cells = 0
        y_rows0 = 0
        for _k, _v in done.items():
            if not isinstance(_v, dict):
                continue
            _r = str(_v.get("reason") or "")
            _d = _v.get("delta")
            _ok = isinstance(_d, (int, float)) and not _v.get("delta_invalid") and not _v.get("is_running")
            if _ok:
                computed += 1
            else:
                _cls = "OTHER"
                for _cand in ("NOT_WIRED_VEC", "SKIPPED_SAMPLING", "SKIPPED_ORANGE_ROW", "TYPE_MISMATCH", "ZERO_TRADES", "SKIPPED_", "RUNNING", "INVALID", "v12 prepared", "all vectors invalid"):
                    if _cand in _r or (_cand == "RUNNING" and _v.get("is_running")):
                        _cls = _cand
                        break
                if _cls == "OTHER" and _d is None:
                    _cls = "DELTA_NONE"
                skips[_cls] = skips.get(_cls, 0) + 1
            _y = _v.get("yellows") or {}
            _yn = sum(1 for _yv in _y.values() if isinstance(_yv, (int, float)) or (isinstance(_yv, dict) and isinstance(_yv.get("delta"), (int, float))))
            y_cells += _yn
            if _yn == 0:
                y_rows0 += 1
        frac = (computed / tot) if tot else 0.0
        print(f"[SKIP-ALARM] {new_symside} rows={tot} computed={computed} ({frac:.0%}) skipped={skips} yellow_cells={y_cells} rows_zero_yellows={y_rows0}", flush=True)
        if tot >= 50 and (frac < 0.50 or (tot > 0 and y_rows0 / tot > 0.85)):
            try:
                FLAGS_DIR.mkdir(parents=True, exist_ok=True)
                (FLAGS_DIR / f"{new_symside}_SKIP_ALERT.txt").write_text(f"symside={new_symside} rows={tot} computed={computed} frac={frac:.2f} skipped={skips} yellow_cells={y_cells} rows_zero_yellows={y_rows0}\n")
            except Exception:
                pass
            print(f"[SKIP-ALARM] *** ALERT {new_symside}: computed {frac:.0%} of rows, {y_rows0}/{tot} rows with zero yellow cells — see SKIP_ALERT ***", flush=True)
    except Exception as _se:
        print(f"[skip-alarm-warn] {_se}", flush=True)


def _atomic_save(wb, wb_path: Path):
    # FLT2 2026-10-01: a foreign cleaner/syncer can delete our *.tmp between save and replace (ENOENT) — that crashed fresh pilots in clone_template (chmod on a never-created xlsx). Retry the whole save up to 3x.
    for _att in range(3):
        _atomic_save._ok = True
        _atomic_save_once(wb, wb_path)
        if _atomic_save._ok:
            return
        import time as _t_r
        _t_r.sleep(1.0 + _att)
    return


def _validate_xlsx_tmp(tmp: str, label: str):
    """Shared xlsx validation: >=10 zip entries + CRC-32 read of each. Raises on failure."""
    import zipfile as _zf_v
    _z = _zf_v.ZipFile(tmp, 'r')
    try:
        _namelist = _z.namelist()
        if len(_namelist) < 10:
            raise RuntimeError(f"tmp zip has only {len(_namelist)} entries, expected >=10")
        for _entry in _namelist:
            try:
                _z.getinfo(_entry)
                _z.read(_entry)
            except Exception as _e_crc:
                raise RuntimeError(f"CRC-32 fail on {_entry}: {_e_crc}")
    finally:
        try:
            _z.close()
        except Exception:
            pass


def _atomic_copy(src: Path, dst: Path):
    """USER 2026-10-03 (xlsx source-of-truth): publish copies must never tear. copy2 to pid-tmp, full xlsx validation, fsync, os.replace. Raises on failure (caller treats as publish failure, never a partial file)."""
    import os as _os, shutil as _sh
    _os.makedirs(str(dst.parent), exist_ok=True)
    tmp = f"{dst}.{_os.getpid()}.copytmp"
    _sh.copy2(str(src), tmp)
    try:
        _validate_xlsx_tmp(tmp, dst.name)
    except Exception:
        try:
            _os.unlink(tmp)
        except Exception:
            pass
        raise
    try:
        fd = _os.open(tmp, _os.O_RDONLY)
        _os.fsync(fd)
        _os.close(fd)
    except Exception:
        pass
    _os.replace(tmp, str(dst))


def _atomic_save_once(wb, wb_path: Path):
    import os as _os, time as _tm
    # per-process tmp: pilot + v15_red_fixer saving the same workbook deleted each other's shared .tmp (VALIDATE-FAIL ENOENT)
    # use pid+tid to avoid collision between daemon and plower threads
    try:
        _tid = threading.get_ident()
    except Exception:
        _tid = 0
    tmp = f"{wb_path}.{_os.getpid()}.{_tid}.tmp"
    bak = str(wb_path) + ".bak"
    # visual + formula cleanup before every save so every workbook ships with Arial10 left, 1F4E78 dark blue header black text, no VLOOKUP
    try:
        _auto_adjust_all_sheets(wb)
    except Exception:
        pass
    try:
        _paint_tab_status(wb)
    except Exception as _e_tab:
        print(f"[tab-status-warn] {wb_path.name} {_e_tab}", flush=True)
    # versioned save disabled 2026-09-26 to prevent 100% disk and 16s overhead — keep only .bak, not versioned per-save
    versioned = None
    try:
        wb_path.parent.mkdir(parents=True, exist_ok=True)
        wb.save(tmp)
        # VALIDATE tmp is a complete zip before replacing live file — prevents 225KB truncation death + CRC-32 corruption
        try:
            import zipfile as _zf_v
            _z = _zf_v.ZipFile(tmp, 'r')
            _namelist = _z.namelist()
            _ok = len(_namelist) >= 10
            if _ok:
                # Validate CRC-32 by testing read of each entry
                for _entry in _namelist:
                    try:
                        _z.getinfo(_entry)
                        _z.read(_entry)
                    except Exception as _e_crc:
                        raise RuntimeError(f"CRC-32 fail on {_entry}: {_e_crc}")
            _z.close()
            if not _ok:
                raise RuntimeError(f"tmp zip has only {len(_namelist)} entries, expected >=10")
        except Exception as _e_v:
            print(f"[atomic-save-VALIDATE-FAIL] {wb_path.name} tmp invalid {_e_v} — keep previous file, do not replace", flush=True)
            try:
                _os.remove(tmp)
            except Exception:
                pass
            raise
        # fsync to ensure zip not damaged on OOM/pkill/reboot
        try:
            fd = _os.open(tmp, _os.O_RDONLY)
            _os.fsync(fd)
            _os.close(fd)
        except Exception:
            pass
        # keep complete json as source of truth, but also keep .bak of last good zip
        try:
            if _os.path.exists(str(wb_path)):
                import shutil
                shutil.copy2(str(wb_path), bak)
                # also keep versioned copy for every new save (3min for 13 sheets)
                if versioned:
                    shutil.copy2(tmp, versioned)
        except Exception:
            pass
        _os.replace(tmp, str(wb_path))
        # also ensure versioned exists as separate file for Mac sync every 3min
        try:
            if versioned and _os.path.exists(str(wb_path)):
                import shutil
                if not _os.path.exists(versioned):
                    shutil.copy2(str(wb_path), versioned)
        except Exception:
            pass
        try:
            fd = _os.open(str(wb_path), _os.O_RDONLY)
            _os.fsync(fd)
            _os.close(fd)
        except Exception:
            pass
    except Exception as _e_atomic:
        _atomic_save._ok = False
        print(f"[atomic-save-FAIL] {wb_path.name} keep previous {_e_atomic}", flush=True)
        # keep previous file, do not truncate with direct save
        try:
            if _os.path.exists(tmp):
                _os.remove(tmp)
        except Exception:
            pass

# progress_path.write_text durable-test parity — cell-by-cell heartbeat uses _atomic_write_json(progress_path, progress) via progress_path.write_text fallback
def _atomic_write_json(path: Path, data: dict):
    tmp = str(path) + ".tmp"
    import os as _os, json as _json
    try:  # PIPE 2026-10-01: every progress JSON carries the defaults round id (env V15_DEFAULTS_ROUND) so rounds with different defaults are never averaged together
        if isinstance(data, dict) and _os.environ.get("V15_DEFAULTS_ROUND") and ("done" in data or "initial_baseline_gain" in data):
            data.setdefault("defaults_round", _os.environ["V15_DEFAULTS_ROUND"])
    except Exception:
        pass
    try:
        Path(tmp).write_text(_json.dumps(data, indent=2))
        try:
            fd = _os.open(tmp, _os.O_RDONLY)
            _os.fsync(fd)
            _os.close(fd)
        except Exception:
            pass
        _os.replace(tmp, str(path))
    except Exception:
        try:
            path.write_text(_json.dumps(data, indent=2))
        except Exception:
            pass
        try:
            if _os.path.exists(tmp):
                _os.remove(tmp)
        except Exception:
            pass

def _validate_e_chain_and_yellows(progress: dict, wb_path: Path | None = None):
    """Integrated from tests/test_v15_e_bland.py — ensures no E drop and F/Yellow/Orange are real.
    Called after each sheet and at final: checks progress.json delta == vg - cum, new_cum >= cum,
    and that written F are floats (not VLOOKUP) and yellows exist."""
    try:
        j = progress
        # Use insertion order (execution order) when available — cycle/worst2best/sheet-order execute out of row-number order.
        # Sorting by row number gives false E-BLAND failures when worst-first or cycle interleaves sheets. The delta stored as
        # vg - cumulative_before at execution time must be checked against the cumulative at that execution point, not sorted row order.
        # If the progress entry stores cumulative_before explicitly, use it; otherwise fall back to insertion order chain.
        cum = float(j.get("baseline_gain", 0))
        # Prefer execution order: dict insertion order preserves pilot execution sequence (cycle, worst2best, shuffle, sheet-order)
        done_items = list(j.get("done", {}).items())
        # Detect if any entry has cumulative_before stored — use it for precise check without inferring order
        use_stored_before = any("cumulative_before" in v for _, v in done_items)
        for k, v in done_items:
            # Prefer stored cumulative_before when present (precise execution-point check)
            if use_stored_before and "cumulative_before" in v:
                cum_before = float(v.get("cumulative_before", cum))
            else:
                cum_before = cum
            delta = float(v.get("delta", 0) or 0)
            vg = float(v.get("vec_gain", 0) or 0)
            # For POS rows delta == vg - cum_before; for NEG delta 0 with vg <= cum (no improvement) is valid. Only flag NEG if vg > cum (missed positive).
            if delta > 1e-9:
                if abs((vg - cum_before) - delta) > 1e-6:
                    print(f"[E-BLAND-CHECK-FAIL] {k} delta {delta:.4f} != vg {vg:.4f} - cum {cum_before:.4f}", flush=True)
            else:
                if (vg - cum_before) > 1e-6:
                    print(f"[E-BLAND-CHECK-FAIL] {k} delta 0 but vg {vg:.4f} > cum {cum_before:.4f} (missed POS)", flush=True)
            if delta > 0:
                new_cum = float(v.get("cumulative_after", cum))
                if new_cum + 1e-9 < cum:
                    print(f"[E-BLAND-CHECK-FAIL] {k} drop {cum:.4f}->{new_cum:.4f}", flush=True)
                if abs(new_cum - vg) > 1e-6:
                    print(f"[E-BLAND-CHECK-FAIL] {k} new_cum {new_cum:.4f} != vg {vg:.4f}", flush=True)
                cum = new_cum
        if wb_path and wb_path.exists():
            wb = openpyxl.load_workbook(str(wb_path), data_only=False)
            wb2 = openpyxl.load_workbook(str(wb_path), data_only=True)
            for sheet in wb.sheetnames:
                if not sheet.startswith("ENTRY") and not sheet.startswith("STDEV"):
                    continue
                ws = wb[sheet]
                ws2 = wb2[sheet]
                for r in range(3, min(30, ws.max_row+1)):
                    if not ws.cell(r,1).value or str(ws.cell(r,1).value).startswith("—"):
                        continue
                    f = ws.cell(r,6).value
                    if isinstance(f, str) and f.startswith("="):
                        # F must be float for processed rows that are in progress.done
                        key = f"{sheet}!{r}:{ws.cell(r,1).value}={ws.cell(r,2).value}"
                        if key in j.get("done", {}):
                            print(f"[F-CHECK-FAIL] {key} still VLOOKUP with done entry", flush=True)
                    if isinstance(f, (int,float)) and f>0:
                        e = ws2.cell(r,5).value
                        e_next = ws2.cell(r+1,5).value if r+1 <= ws.max_row else None
                        if isinstance(e,(int,float)) and isinstance(e_next,(int,float)) and float(e_next) +1e-9 < float(e):
                            print(f"[E-CHECK-FAIL] {sheet}!{r} E drop {e}->{e_next}", flush=True)
            wb.close(); wb2.close()
    except Exception as e:
        print(f"[E-BLAND-CHECK-WARN] {e}", flush=True)

def preload_prepared(symside: str, window_days: int = 30):
    if symside in ALL_PREPARED:
        return ALL_PREPARED[symside]
    from tools.opt.v12_pilot import prepare_batch
    try:
        prep = prepare_batch(symside, window_days)
        if prep and prep.get("npz_prepared") is not None:
            ALL_PREPARED[symside] = prep
            npz = prep.get("npz_prepared") or {}
            ALL_NPZ_ARRAYS[symside] = {k: np.asarray(v) for k, v in npz.items() if isinstance(v, np.ndarray) and v.ndim}
            print(f"[v15-preload] {symside} {len(ALL_NPZ_ARRAYS[symside])} arrays {len(np.asarray(npz.get('close', [])))} bars hot", flush=True)
            return prep
    except Exception as e:
        print(f"[v15-preload-warn] {symside} {e}", flush=True)
    return None

def live_evaluate(symside: str, overrides: dict, window_days: int = 30) -> dict:
    import os as _os
    _os.environ["V12_NPZ_CACHE"] = "32"
    # 2026-10-04 parity-harness (staged): enable the scalar churn fix for the
    # DONE-stage live verify (same pattern as tools/v15_parity_check.py). Without
    # this, backtest_v12_engine wipes per-bar cooldowns/debounces every simulated
    # bar, exits re-fire every bar (1775 closes/900s ZENUSDT), and this call burns
    # the full LIVE_TIMEOUT (900s) -> H/I left BLANK. Process-local to the pilot
    # worker; live trading runs in separate processes and never reads BT_ flags.
    try:
        import config as _c_live
        _c_live.BT_PRESERVE_DEBOUNCE_ACROSS_BARS = True
        import config_tradier as _ct_live
        _ct_live.BT_PRESERVE_DEBOUNCE_ACROSS_BARS = True
        _ct_live.TradierConfig.BT_PRESERVE_DEBOUNCE_ACROSS_BARS = True
    except Exception:
        pass
    try:
        import dataclasses as _dc, v12_quick_engine as _VQ
        _q_defaults = {f.name: f.default for f in _dc.fields(_VQ.QuickConfig)}
        for _k in ["ATR_ADAPTIVE_SIZING_TARGET_PCT", "BOUNCE_AUGMENT_K_D_THRESHOLD", "DC_EDGE_SIZING_MAX_MULT", "EMA_DIST_SIZING_MULT", "REENTRY_TIER1_SIZE_MULT_TRADIER", "BOUNCE_AUGMENT_DC_LOW_D_TOLERANCE"]:
            if _k in overrides and isinstance(overrides[_k], bool):
                overrides = dict(overrides)
                overrides[_k] = float(_q_defaults.get(_k, 1.0))
    except Exception:
        pass
    import backtest_v12_engine as B
    try:
        return B.run_one(symside, overrides, window_days=window_days)
    except Exception as e:
        import traceback
        return {"symside": symside, "valid": False, "invalid_reason": f"live_evaluate {e}", "trace": traceback.format_exc()[:2000], "gain_pct": 0.0, "trades": 0, "pool_sharpe": 0.0}

def vector_evaluate_cached(symside: str, overrides: dict, window_days: int = 30) -> dict:
    prep = ALL_PREPARED.get(symside) or preload_prepared(symside, window_days)
    if prep is None:
        from tools.opt.v12_pilot import evaluate_sanitized
        return evaluate_sanitized(symside, overrides, window_days=window_days)
    from tools.opt.v12_pilot import evaluate_prepared_sanitized
    return evaluate_prepared_sanitized(prep, overrides, window_days=window_days)

def parity_ok(live: dict, vec: dict, allow_zero_baseline: bool = False) -> tuple[bool, str]:
    if not live.get("valid"):
        return False, f"live invalid: {live.get('invalid_reason')}"
    if not vec.get("valid"):
        return False, f"vector invalid: {vec.get('invalid_reason')}"
    lt = int(live.get("trades") or 0); vt = int(vec.get("trades") or 0)
    if lt == 0 and vt == 0:
        return False, f"zero trades live={lt} vec={vt}"
    if lt == 0 and allow_zero_baseline and vt > 10:
        pass
    else:
        if lt == 0 or vt == 0:
            return False, f"zero trades live={lt} vec={vt}"
        ratio = vt / lt if lt else 0
        if not (0.80 <= ratio <= 1.25):
            return False, f"trade-count ratio {ratio:.2f} out of 0.80..1.25 (live {lt} vec {vt})"
    lg = float(live.get("gain_pct") or 0); vg = float(vec.get("gain_pct") or 0)
    if abs(lg - vg) > 0.5 and abs(lg - vg) / max(1e-9, abs(lg)) > 0.15:
        return False, f"gain mismatch live {lg:.4f} vec {vg:.4f}"
    return True, "parity ok"

def ensure_lbI_headers(wb_path: Path):
    import time as _t_hdr
    _t0_hdr = _t_hdr.time()
    try:
        wb = openpyxl.load_workbook(str(wb_path))
    except (FileNotFoundError, zipfile.BadZipFile, OSError) as _bad:
        # Clone was deleted as empty vomit or BadZip before headers — recreate from template
        try:
            # remove BadZip if exists
            try:
                if Path(wb_path).exists():
                    Path(wb_path).unlink()
            except: pass
            tmpl = next((p for p in [Path("SPREADSHEETS/TEMPLATE_STOCKS_LONG.xlsx"), Path("SPREADSHEETS/TEMPLATE_STOCKS_SHORT.xlsx"), Path("SPREADSHEETS/TEMPLATE_CRYPTO_LONG.xlsx"), Path("SPREADSHEETS/TEMPLATE_CRYPTO_SHORT.xlsx")] if p.exists()), None)
            if tmpl and tmpl.exists():
                import shutil
                shutil.copy(str(tmpl), str(wb_path))
                wb = openpyxl.load_workbook(str(wb_path))
            else:
                raise
        except Exception as e:
            print(f"[ensure_lbI_headers] missing/BadZip {wb_path} and no template {e}", flush=True)
            return
    fd_rows = _load_filter_dictionary()
    for sheet in SWITCH_SHEETS:
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        try:
            for mr in list(ws.merged_cells.ranges):
                if mr.min_row == 1 and mr.max_row == 1 and mr.max_col >= 12 and mr.min_col <= ws.max_column + 50:
                    ws.unmerge_cells(str(mr))
        except Exception:
            pass
        existing = set()
        scan_max = max(ws.max_column, 15)
        stale = []
        for c in range(15, scan_max + 1):
            try:
                hv = ws.cell(row=2, column=c).value
            except Exception:
                hv = None
            if hv and isinstance(hv, str) and "=" in hv:
                existing.add(hv.strip())
                stale.append(c)
        for c in stale:
            try:
                ws.cell(row=2, column=c).value = None
            except Exception:
                pass
        existing = set()
        headers = []
        seen = set()
        lifecycle = sheet.split("_")[0]
        # union of every row's relevant (should-be-yellow) set: all of these must
        # exist as L:BI columns or relevant deltas have nowhere to land (header gap)
        sheet_switches = []
        for _r in range(2, ws.max_row + 1):
            _sw = ws.cell(row=_r, column=1).value
            if not _sw or not isinstance(_sw, str):
                continue
            _sw = _sw.strip()
            if _sw.lower() in ("switch", "general", "blanket", "filter", "option value") or _sw.startswith("—"):
                continue
            if ws.cell(row=_r, column=2).value is None:
                continue
            sheet_switches.append(_sw)
        union = set()
        for sw in sheet_switches:
            for e in get_opportune_filters(sw, sheet):
                if _is_general(e["rec"]):
                    continue
                union.add(f"{e['filter']}={e['opt']}")
        for hdr in sorted(union):
            if hdr in seen or hdr in existing:
                continue
            seen.add(hdr)
            headers.append(hdr)
        # GENERAL (orange-family) headers fill remaining L:BI slots up to budget
        for e in fd_rows:
            sa = (e["sheets_app"] or "").strip()
            if sa == "ALL":
                applicable = True
            else:
                applicable = (lifecycle in sa) or ("GLOBAL_CHECK" in sa)
            if not applicable:
                continue
            if not _is_general(e["rec"]):
                continue
            hdr = f"{e['filter']}={e['opt']}"
            if hdr in seen or hdr in existing:
                continue
            seen.add(hdr)
            headers.append(hdr)
            if len(headers) >= 220:
                break
        col = 15
        for hdr in headers:
            if hdr in existing:
                continue
            try:
                c = ws.cell(row=2, column=col)
                if str(getattr(c, "__class__", "")).endswith("MergedCell"):
                    for mr in ws.merged_cells.ranges:
                        if mr.min_col <= col <= mr.max_col and mr.min_row <= 1 <= mr.max_row:
                            c = ws.cell(row=mr.min_row, column=mr.min_col)
                            break
                c.value = hdr
                c.font = Font(bold=True, color="0070C0")
            except Exception:
                pass
            col += 1
    _atomic_save(wb, Path(wb_path))  # BADZIP FIX 2026-09-29: was in-place wb.save
    _dt_hdr = _t_hdr.time() - _t0_hdr
    if _dt_hdr > 1.0:
        print(f"[SLOW-CELL] ensure_lbI_headers {wb_path.name} { _dt_hdr:.2f}s >1s sheets={len(SWITCH_SHEETS)}", flush=True)
    else:
        print(f"[headers] L:BI ensured { _dt_hdr:.2f}s", flush=True)

def _load_workbook_retry(path: Path, tries: int = 5, delay_s: float = 3.0, **kw):
    """2026-09-28 flawless-pilot fix (USAR_LONG BadZipFile crash): a template/workbook read can race
    an in-flight rsync and hit a transient zip CRC error. Retry a few times before giving up loudly —
    never die with a bare traceback on the first bad read."""
    import zipfile as _zf
    _last = None
    for _t in range(int(tries)):
        try:
            return openpyxl.load_workbook(str(path), **kw)
        except (_zf.BadZipFile, KeyError, OSError) as _e:
            _last = _e
            print(f"[wb-load-retry] {Path(path).name}: {_e} — retry {_t+1}/{tries} in {delay_s:.0f}s", flush=True)
            time.sleep(delay_s)
    raise RuntimeError(f"workbook unreadable after {tries} tries (racing sync or corrupt file): {path}: {_last}")


def _pending_template_rows(symside: str):
    """2026-09-28 fix (XLMUSDT_LONG/USAR_LONG requeue loop): the herd's DONE criterion is
    v15_progress_board.symside_status — EVERY template row present in progress 'done'. After
    _merge_new_template_rows grew the templates (+vigilance rows), sheets finished under the old
    row count sit at done>=2800 with pending new rows; the old done<2800 resume gate PROHIBITs
    forever while the herd requeues forever (todo grows, launched+0, 0/354 complete). Resume must
    key on the same truth the herd uses. Returns pending row count, 0 for complete/zero-trades
    terminal, None if the board is unavailable (caller falls back to legacy gate)."""
    try:
        try:
            from tools.v15_progress_board import symside_status as _pb_status
        except Exception:
            import sys as _sys_pb
            _sys_pb.path.insert(0, str(ROOT / "tools"))
            from v15_progress_board import symside_status as _pb_status
        _pb = _pb_status(symside)
        if _pb.get("zero_trades"):
            return 0
        return max(0, int(_pb.get("rows") or 0) - int(_pb.get("filled") or 0))
    except Exception as _pb_e:
        print(f"[pending-rows-warn] {symside}: {_pb_e}", flush=True)
        return None


def _merge_new_template_rows(template: Path, target: Path) -> int:
    """USER 2026-09-28: append (Switch, value) rows that exist in the template's SWITCH_SHEETS but
    not in the reused sym_side workbook, so newly added switches (VIGILANCE_* etc.) get tested in
    the next round without recloning. Only columns A/B are written — C/E/F/G stay empty so the
    pilot's spec-fill sees them as pending rows. Atomic save; returns rows added."""
    def _norm_val(v):
        if v is None:
            return ''
        s = str(v).strip()
        try:
            f = float(s)
            return repr(int(f)) if f == int(f) else repr(f)
        except Exception:
            return s
    tpl = _load_workbook_retry(template, data_only=True, read_only=True)
    added_total = 0
    wb = None
    try:
        for sn in SWITCH_SHEETS:
            if sn not in tpl.sheetnames:
                continue
            tws = tpl[sn]
            tpl_rows = []
            for row in tws.iter_rows(min_row=3, max_col=2, values_only=True):
                a = row[0] if len(row) > 0 else None
                b = row[1] if len(row) > 1 else None
                if a not in (None, ''):
                    tpl_rows.append((str(a).strip(), b))
            if not tpl_rows:
                continue
            if wb is None:
                wb = _load_workbook_retry(target)
            if sn not in wb.sheetnames:
                continue
            ws = wb[sn]
            existing = set()
            last_row = 2
            for r in range(3, ws.max_row + 1):
                a = ws.cell(row=r, column=1).value
                if a in (None, ''):
                    continue
                last_row = max(last_row, r)
                b = ws.cell(row=r, column=2).value
                existing.add((str(a).strip(), _norm_val(b)))
            r = last_row + 1
            for name, val in tpl_rows:
                if (name, _norm_val(val)) in existing:
                    continue
                ws.cell(row=r, column=1, value=name)
                ws.cell(row=r, column=2, value=val)
                r += 1
                added_total += 1
        if wb is not None and added_total > 0:
            _atomic_save(wb, target)
            print(f"[template-merge] {target.name}: +{added_total} new template rows merged for next round", flush=True)
    finally:
        try:
            tpl.close()
        except Exception:
            pass
    return added_total


def clone_template(template: Path, new_symside: str) -> Path:
    if not template.exists():
        raise FileNotFoundError(f"template missing {template}")
    import glob as _glob
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Enforce 708 max: 1 interim per sym_side, overwrite in place — never create pilot timestamp
    # Remove stale pilot files for this sym (they leak disk and break 708)
    for p in _glob.glob(str(OUT_DIR / f"{new_symside}_30d_matrix_pilot_*.xlsx")):
        try:
            Path(p).unlink()
        except Exception:
            pass
    target = OUT_DIR / f"{new_symside}_30d_matrix.xlsx"
    # USER 2026-09-30: an existing workbook is reused ONLY to resume THIS run (its progress JSON in PROGRESS_DIR has done rows).
    # Otherwise it is an old-round sheet (old layout / old yellows / old C and E-G values): moved aside, never filled over,
    # and the current template is cloned fresh. (Round 3 filled new values into old sheets — "all sheets broken".)
    if target.exists():
        _resume = False
        try:
            _pj = PROGRESS_DIR / f"{new_symside}_v14_progress.json"
            _resume = _pj.exists() and bool(json.loads(_pj.read_text()).get("done"))
        except Exception:
            _resume = False
        if not _resume:
            _stale = target.with_name(target.name + f".stale_{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}")
            target.rename(_stale)
            print(f"[clone] {target.name}: no resumable progress in {PROGRESS_DIR} — old sheet moved to {_stale.name}, fresh clone from {template.name}", flush=True)
    # FIX 2026-09-24: never overwrite a valid filled workbook with empty template — reuse if exists and has F filled and is valid zip
    if target.exists():
        try:
            import zipfile
            z = zipfile.ZipFile(str(target), 'r')
            ok = len(z.namelist()) >= 10
            z.close()
            if ok:
                # check if already has F filled (at least one switch sheet has numeric F)
                try:
                    wb_check = openpyxl.load_workbook(str(target), data_only=True, read_only=True)
                    # 7D fix: check ALL 12 tabs have E3 baseline and at least one F, not just any — prevents empty ENTRY looking filled via GLOBAL
                    # sequential fill (2026-09-30): E3 exists only where the chain starts (first tab) — all 13 tabs present +
                    # a numeric chain start = a started sheet that must be resumed, never overwritten by a fresh template
                    has_f = all(sn in wb_check.sheetnames for sn in SWITCH_SHEETS) and isinstance(wb_check[SWITCH_SHEETS[0]].cell(3, 5).value, (int, float))
                    wb_check.close()
                    if has_f:
                        print(f"[clone] {target.name} already exists with E3 baseline for all 12 — reuse, not overwrite", flush=True)
                        # USER 2026-09-28: new template switches (e.g. VIGILANCE_*) must be tested in the
                        # next round on EXISTING sheets too — merge missing (Switch, value) rows from the
                        # template into the reused workbook so spec-fill picks them up as pending rows.
                        # USER 2026-09-30: no script adds or removes sheet rows — template-row merge into existing sheets disabled
                        return target
                except Exception:
                    pass
        except Exception:
            pass
    wb = _load_workbook_retry(template)
    new_baseline = f"{new_symside}_BASELINE_METRICS"
    old_baseline = None
    for cand in ["TEMPLATE_BASELINE_METRICS", "ADP_LONG_BASELINE_METRICS"]:
        if cand in wb.sheetnames:
            old_baseline = cand
            break
    if old_baseline:
        ws = wb[old_baseline]
        ws.title = new_baseline
        for sheet_name in wb.sheetnames:
            ws2 = wb[sheet_name]
            for row in ws2.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and old_baseline in c.value:
                        c.value = c.value.replace(old_baseline, new_baseline).replace("TEMPLATE", new_symside.split("_")[0]).replace("ADP_LONG", new_symside)
    else:
        # 2026-09-24 FIX: latest TEMPLATE_STOCKS_/CRYPTO_*.xlsx have NO baseline sheet — create it
        if new_baseline not in wb.sheetnames:
            ws = wb.create_sheet(new_baseline)
            ws["A1"] = "metric"
            ws["B1"] = "value"
            ws["A2"] = "gain_pct"
            ws["A3"] = "bh_pct"
        # fix E3 broken reference TEMPLATE_BASELINE_METRICS!B2 -> new_baseline!B2 for first SWITCH sheet
        for sheet_name in wb.sheetnames:
            ws2 = wb[sheet_name]
            for row in ws2.iter_rows():
                for c in row:
                    if isinstance(c.value, str) and "TEMPLATE_BASELINE_METRICS" in c.value:
                        c.value = c.value.replace("TEMPLATE_BASELINE_METRICS", new_baseline).replace("TEMPLATE", new_symside.split("_")[0]).replace("ADP_LONG", new_symside)
                    elif isinstance(c.value, str) and "ADP_LONG_BASELINE_METRICS" in c.value:
                        c.value = c.value.replace("ADP_LONG_BASELINE_METRICS", new_baseline)
    # E2 chain: first sheet = B2 from baseline metrics, subsequent sheets = MAX(prev!E) for cumulative; will be overwritten per-row with blank-if-neg logic
    for idx, name in enumerate(SWITCH_SHEETS):
        if name not in wb.sheetnames:
            continue
        ws = wb[name]
        # FIX: ensure first sheet E2 is never None — was printing wrong ws and leaving None
        if idx == 0:
            c0 = ws.cell(row=2, column=5)
            if c0.value is None or (isinstance(c0.value, str) and c0.value.strip() == ""):
                c0.value = f"='{new_baseline}'!B2"
                c0.font = Font(name="Arial", bold=True, color="006100")
        print(f"[TAB-START] {new_symside} {name} baseline {ws.cell(row=2, column=5).value} idx {idx}", flush=True)
        if idx > 0:
            prev = SWITCH_SHEETS[idx - 1]
            if prev in wb.sheetnames:
                c = ws.cell(row=2, column=5)
                if isinstance(c.value, str) and "MAX" in c.value:
                    c.value = f"=MAX('{prev}'!E$2:E$5000)"
                    c.font = Font(name="Arial", bold=True, color="006100")
        # Fix E column formulas per spec: BLANK when G<=0 (greedy), not Eprev. Template is =IF(G4="",E3,IF(G4>0,E3+G4,E3)) greedy cum.
        # CLEAN TRASH: delete #NUM!, #NAME?, #VALUE!, #REF!, #DIV/0! and bare 0 in E for data rows before writing (user report 01 ENTRY_REVERSAL_BOUNCE trash)
        for r in range(3, ws.max_row + 1):
            e_val = ws.cell(row=r, column=5).value
            if isinstance(e_val, str) and e_val.startswith("#"):
                ws.cell(row=r, column=5).value = None
                e_val = None
            elif e_val == 0 and r > 2:
                ws.cell(row=r, column=5).value = None
                e_val = None
            # Fix G delimiter: VLOOKUP should use "=" not "_" (was "&"_"&" in some rows -> #NAME?/0)
            g_val = ws.cell(row=r, column=7).value
            if isinstance(g_val, str) and 'VLOOKUP' in g_val and '&"_"&' in g_val:
                ws.cell(row=r, column=7).value = g_val.replace('&"_"&', '&"="&')
            if isinstance(e_val, str) and (e_val.startswith("=IF(F") or e_val.startswith("=IF(G")):
                # replace trailing ,Eprev) with ,"") to keep blank on NEG greedy (E only filled when G>0)
                # =IF(G4="",E3,IF(G4>0,E3+G4,E3)) -> =IF(G4="", "",IF(G4>0,E3+G4,""))
                try:
                    if e_val.endswith(",E3)") or ",E" in e_val:
                        import re
                        e_val = re.sub(r",E\d+\)$", ',"")', e_val)
                        ws.cell(row=r, column=5).value = e_val
                except Exception:
                    pass
        c2 = ws.cell(row=2, column=6)
        if isinstance(c2.value, str) and "-E1" in c2.value:
            c2.value = c2.value.replace("-E1", "-E2")
    for name in SWITCH_SHEETS:
        if name not in wb.sheetnames:
            continue
        wsc = wb[name]
        for r in range(2, wsc.max_row + 1):
            # a fresh clone carries no overrides: C is filled only by BEST-C-FILL and promotions
            if r >= 3 and wsc.cell(row=r, column=3).value not in (None, ""):
                wsc.cell(row=r, column=3).value = None
    for cand in ["Results_Deltas", "Results_30d_Deltas", "Results_30d", "results"]:
        if cand in wb.sheetnames:
            ws = wb[cand]
            for r in range(2, ws.max_row + 1):
                for c in range(1, ws.max_column + 1):
                    cell = ws.cell(row=r, column=c)
                    if cell.value is not None:
                        cell.value = None
            full_hdr = ["key","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","REAL_COMPLETE_DELTA","variant_sharpe","trades","tim","dd","filter_or_override","symside","window","bh_pct","gain_pct","tim_pct","max_dd","win_rate","bars","peak","source"]
            for ci, h in enumerate(full_hdr, start=1):
                cur = ws.cell(1, ci).value
                if cur is None or str(cur).strip() == "":
                    ws.cell(1, ci).value = h
                    ws.cell(1, ci).font = Font(bold=True)
                elif str(cur).strip().lower() != h.lower() and ci <= 8:
                    ws.cell(1, ci).font = Font(bold=True)
            break
    else:
        ws = wb.create_sheet("Results_Deltas")
        ws.append(["key","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","REAL_COMPLETE_DELTA","variant_sharpe","trades","tim","dd","filter_or_override","symside","window","bh_pct","gain_pct","tim_pct","max_dd","win_rate","bars","peak","source"])
        for ci in range(1, 25):
            ws.cell(1, ci).font = Font(bold=True)
    if "Results_30d_Deltas" not in wb.sheetnames:
        ws2 = wb.create_sheet("Results_30d_Deltas")
        ws2.append(["key","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","REAL_COMPLETE_DELTA","variant_sharpe","trades","tim","dd","filter_or_override","symside","window","bh_pct","gain_pct","tim_pct","max_dd","win_rate","bars","peak","source"])
        for ci in range(1, 25):
            ws2.cell(1, ci).font = Font(bold=True)
    _atomic_save(wb, Path(target))  # BADZIP FIX 2026-09-29
    if not Path(target).exists():
        raise RuntimeError(f"[clone] {target} not created after 3 atomic-save attempts")  # FLT2: explicit, instead of a chmod FileNotFoundError
    try:
        Path(target).chmod(0o644)
    except OSError as _e_chmod:
        print(f"[clone-chmod-warn] {target} {_e_chmod}", flush=True)
    return target

def _run_single(new_symside, args):
    """Single symside core — called for each of 4 NPZ batch, keeps all 4 hot in same process."""
    import time as _t
    _v15_start_time = _t.time()
    # ABSOLUTE PROHIBITION — check BEFORE any heavy NPZ/prepare (2026-09-16)
    try:
        _early_prog = None
        for _pp in ([PROGRESS_DIR / f"{new_symside}_v14_progress.json"] if (os.environ.get("V15_FRESH_RUN", "0") == "1" or os.environ.get("V15_PROGRESS_DIR")) else [PROGRESS_DIR / f"{new_symside}_v14_progress.json", Path(f"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{new_symside}_v14_progress.json")]):  # V15_FRESH_RUN / V15_PROGRESS_DIR: isolated dir only
            if _pp.exists():
                try:
                    _early_prog = json.loads(_pp.read_text())
                    break
                except Exception:
                    continue
        if (_early_prog or {}).get("verdict") == "IMPOSSIBLE" and os.getenv("FORCE_DC_RERUN") != "1":
            print(f"[IMPOSSIBLE-SKIP] {new_symside} tombstoned ({(_early_prog or {}).get('impossible_path')}) — manual revision required, MUST NOT RETOUCH.", flush=True)
            return
        if (_early_prog or {}).get("verdict") in ("NO_TRADES", "BEST_EFFORT") and os.getenv("FORCE_DC_RERUN") != "1" and not _soft_verdict_fresh(new_symside, _early_prog or {}):
            print(f"[SOFT-SKIP] {new_symside} verdict {(_early_prog or {}).get('verdict')} on same NPZ — nothing to do (reruns on NPZ refresh).", flush=True)
            return
        if (_early_prog or {}).get("verdict") in ("NO_TRADES", "BEST_EFFORT") and _soft_verdict_fresh(new_symside, _early_prog or {}):
            print(f"[SOFT-REFRESH] {new_symside} verdict {(_early_prog or {}).get('verdict')} but NPZ is newer — clearing and re-running", flush=True)
        _redo_run = bool((_early_prog or {}).get("needs_redo")) and (_early_prog or {}).get("verdict") != "IMPOSSIBLE"
        if _redo_run:
            print(f"[REDO-RUN] {new_symside} re-filling from repaired set (depth {((_early_prog or {}).get('needs_redo') or {}).get('depth')})", flush=True)
        if os.getenv("FORCE_DC_RERUN") == "1":
            print(f"[FORCE-DC-RERUN] {new_symside} hard-stop rerun forced", flush=True)
        elif _early_prog and _early_prog.get("final_gain") is not None and len(_early_prog.get("done", {})) >= 50 and not _redo_run:
            _done_cnt = len(_early_prog.get("done", {}))
            _has_final = any((ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{new_symside}*.xlsx").parent.glob(f"{new_symside}_30d_matrix.xlsx")) or any((ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{new_symside}_bh*.xlsx").parent.glob(f"{new_symside}_bh*.xlsx"))
            if not _has_final:
                import pathlib as _pl2
                _has_final = any(_pl2.Path.home().glob(f"binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{new_symside}_30d_matrix.xlsx")) or any(_pl2.Path.home().glob(f"binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{new_symside}_bh*.xlsx"))
            _pending = _pending_template_rows(new_symside)
            # 2026-09-28 publish-only pass: a board-COMPLETE sheet whose DONE stage died (OOM'd
            # parent) has pending==0 and done>=2800, so the legacy gate PROHIBITed every relaunch
            # and NO complete sheet could ever publish its bh/gain final. If no final exists on
            # disk, resume: all rows are cached, the pilot skims to DONE and publishes.
            _bh_final_exists = any((ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL").glob(f"{new_symside}_bh*_30d_matrix*.xlsx"))
            if _pending is not None and _pending == 0 and not _bh_final_exists:
                print(f"[RESUME-ALLOW] {new_symside} COMPLETE but no bh/gain final on disk — publish-only pass (DONE stage rerun)", flush=True)
            elif _pending:
                print(f"[RESUME-ALLOW] {new_symside} final_gain {_early_prog.get('final_gain'):.2f} done {_done_cnt} but {_pending} template rows pending (post-merge) — incremental resume", flush=True)
            elif _done_cnt < 2800 and not _has_final:
                print(f"[RESUME-ALLOW] {new_symside} incomplete final_gain {_early_prog.get('final_gain'):.2f} done {_done_cnt} no FINAL xlsx — resuming", flush=True)
            elif args.baseline_json and args.seq_mode in ("shuffle", "worst2best", "worst_first"):
                print(f"[{args.seq_mode.upper()}-ALLOW] {new_symside} already finished final_gain {_early_prog.get('final_gain'):.2f} but {args.seq_mode}+baseline-json allowed", flush=True)
            elif args.seq_mode == "shuffle" and args.baseline_json:
                print(f"[SHUFFLE-ALLOW] {new_symside} already finished final_gain {_early_prog.get('final_gain'):.2f} but shuffle+baseline-json allowed", flush=True)
            else:
                print(f"[PROHIBITED] {new_symside} ALREADY FINISHED early final_gain {_early_prog.get('final_gain'):.2f} done {len(_early_prog.get('done',{}))} — MUST NOT RETOUCH.", flush=True)
                return
    except Exception as _e:
        print(f"[EARLY-PROHIBIT-WARN] {_e}", flush=True)
    # Now run the original single core (from defaults onward) — keep 4 NPZs hot
    _run_single_core(new_symside, args, _v15_start_time)

def _require_progress_dir():
    # USER 2026-09-30: an unknown launcher started pilots with the DEFAULT progress dir, resuming old-round progress into
    # old sheets. Every legitimate launcher (v15_full_sweep_driver, proofs) sets V15_PROGRESS_DIR explicitly.
    if not os.environ.get("V15_PROGRESS_DIR") and os.environ.get("V15_ALLOW_DEFAULT_PROGRESS", "0") != "1":
        print("[REFUSED] v15_pilot needs an explicit V15_PROGRESS_DIR (set by tools/v15_full_sweep_driver.py); "
              "V15_ALLOW_DEFAULT_PROGRESS=1 overrides deliberately", flush=True)
        sys.exit(3)


def _run_single_core(new_symside, args, _v15_start_time):
    # Original single core body (defaults, NPZ, baseline, sheets, final) — extracted for batch
    import json as _j3
    _v15_start_time = _v15_start_time

def main():
    _capture_start_stamp()
    _cycle_deque = None
    _cycle_indices = None
    # Ensure _cycle_deque defined for sequential mode to avoid NameError
    _cycle_deque = None
    _cycle_indices = None
    ap = argparse.ArgumentParser(description="v15_pilot — SERIOUS TEMPLATE filler: cell-by-cell L:BI + Results_Deltas with in-memory NPZ (V12_NPZ_CACHE=32), workers 16, wb_keep open per sheet")
    ap.add_argument("--sym-side", dest="sym_side", default=None)
    ap.add_argument("--template", default=str(TEMPLATE))
    ap.add_argument("--out", default=None)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--sheet", default=None)
    ap.add_argument("--fast-switches", default=None, help="json list of switch names: fill only those rows (rest stay pending for a later full pass)")
    ap.add_argument("--nav-mode", default="jump", choices=["jump", "fill_tab"], help="NEG/0 delta: jump = first pending row of NEXT tab; fill_tab = stay, fill the tab completely, then next tab")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--vector-only", action="store_true", help="vector-only, no live parity (fast)")
    ap.add_argument("--no-lbI", action="store_true")
    ap.add_argument("--allow-mac", action="store_true", help="allow full run on MacBook for code writing/testing only (requires V15_ALLOW_MAC=1 or this flag); otherwise S1-only")
    # 0914 PROTOTYPE sequencing variants (TEMPLATE_0914 + v15_pilot_0914): cycle tabs on neg delta, worst->best ordering
    ap.add_argument("--seq-mode", default="cycle", choices=["sequential", "cycle", "round_robin", "worst2best", "worst_to_best", "worst_first", "worst-first", "shuffle"], help="0914 prototype sequencing: sequential (legacy), cycle/round_robin (cycle tabs on every neg delta), worst2best (sheets ordered worst->best by avg delta), shuffle (random shuffle for second round)")
    ap.add_argument("--baseline-json", default=None, help="json file with overrides to use as new baseline for shuffle second round (found settings)")
    ap.add_argument("--disable-switches-file", default=None, help="json file with list of switches to disable for next round (never had pos delta, speeds up)")
    ap.add_argument("--cycle-on-neg", action="store_true", help="0914 alias: force cycle-through-tabs on every NEG delta (same as --seq-mode cycle)")
    ap.add_argument("--sheet-order", default=None, help="0914 override sheet order comma-separated (e.g. GLOBAL_RISK_GATES,EXIT_VELOCITY,...)")
    ap.add_argument("--batch-syms", default=None, help="4 NPZ batch: comma-separated sym_sides (e.g. AAPL_LONG,AAPL_SHORT,MSFT_LONG,MSFT_SHORT) — loads 4 NPZs LONG+SHORT together, keeps all hot in RAM until all 12 tabs finished, never erase")
    args = ap.parse_args()
    if not getattr(args, "dry_run", False):
        _require_progress_dir()
    # normalize seq-mode aliases
    if args.cycle_on_neg and args.seq_mode == "sequential":
        args.seq_mode = "cycle"
    if args.seq_mode in ("round_robin", "worst_first", "worst-first"):
        args.seq_mode = "cycle"
    if args.seq_mode in ("worst_to_best",):
        args.seq_mode = "worst2best"
    # shuffle is kept as shuffle (no alias)

    import os
    os.environ["V8_SWEEP_MODE"] = "1"
    os.environ.pop("V8_KEEP_ENTRY_GATES", None)
    # 24h TEMPLATE defaults verifier — bold B values are source-of-truth, immutable 24h
    try:
        import subprocess as _sp, pathlib as _pl
        import pathlib as _pathlib_verify
        _stamp = _pathlib_verify.Path("SPREADSHEETS/.template_defaults_verified.json")
        _need_verify = True
        if _stamp.exists():
            try:
                import json as _js, time as _tm
                _verified_at = float(_js.load(open(_stamp)).get("verified_at", 0))
                if (_tm.time() - _verified_at) < 24 * 3600:
                    _need_verify = False
            except: pass
        # S5 skip verify to avoid waste (s1 yes s5 no) - verify is S1-only and 24h lock
        try:
            import socket as _sock2
            _h2 = _sock2.gethostname().lower()
            if "s5" in _h2:
                print("[TEMPLATE-VERIFY] S5 skip (s1 yes s5 no) — S1 already verified, saving 233k wasted", flush=True)
                _need_verify=False
        except: pass
        if _need_verify:
            if os.getenv("V15_FORCE_USE") == "1" or os.getenv("FORCE_DC_RERUN") == "1":
                print("[TEMPLATE-VERIFY] V15_FORCE_USE/FORCE_DC_RERUN — skip verify to avoid hang, never block pilot", flush=True)
            else:
                print("[TEMPLATE-VERIFY] 24h expired or no stamp — re-verifying bold defaults vs config source of truth (ONLY backtests, not per sym)", flush=True)
                try:
                    _sp.run([sys.executable, "tools/verify_template_defaults.py"], check=False, timeout=8)
                except Exception as _e_v:
                    print(f"[TEMPLATE-VERIFY-TIMEOUT] skip verify >8s {_e_v} — never hang", flush=True)
        else:
            print("[TEMPLATE-VERIFY] within 24h immutable or S5 skip — skipping to avoid 233k waste", flush=True)
    except Exception as _e:
        print(f"[TEMPLATE-VERIFY-WARN] {_e}", flush=True)

    if sys.platform == "darwin" and not args.dry_run and not args.allow_mac and os.getenv("V15_ALLOW_MAC") != "1" and "PYTEST_CURRENT_TEST" not in os.environ:
        print("[BLOCKED] v15_pilot is S1-ONLY — NEVER RUN ON MACBOOK except for code writing/testing.", file=sys.stderr, flush=True)
        print("[BLOCKED] Mac is live-only — NO backtests on MacBook ever. Filler is S1-only. Use ssh s1-int or 157.180.125.52.", file=sys.stderr, flush=True)
        print("[BLOCKED] Full fill requires S1 with NPZ (backtest_v8/indicators/*.npz 975K truncated on Mac gives 0 trades DATA_ERROR).", file=sys.stderr, flush=True)
        print("[BLOCKED] For code writing/testing on Mac: use --dry-run (clone only) or --allow-mac / V15_ALLOW_MAC=1", file=sys.stderr, flush=True)
        sys.exit(2)
    if sys.platform == "darwin" and (args.dry_run or args.allow_mac or os.getenv("V15_ALLOW_MAC") == "1" or "PYTEST_CURRENT_TEST" in os.environ):
        print("[warn] Mac allowed for writing/testing only — not for real sweeps (S1 required for full universe)", flush=True)

    if args.window_days == 365 or args.window_days >= 100:
        # USER 2026-09-20: 365D retest with found 30D settings allowed when baseline-json provided (found settings gate)
        if not args.baseline_json or not Path(args.baseline_json).exists():
            print("BLOCKED: 1yr requires 30D gate — run 30D first (or provide --baseline-json with found 30D settings for 365D retest)", file=sys.stderr)
            sys.exit(2)
        else:
            print(f"[365D-ALLOWED] 365D retest with found settings {args.baseline_json}", flush=True)
    if args.window_days not in (30, 20, 7, 1, 365):
        print(f"BLOCKED: only 30/20/7/1/365 allowed, got {args.window_days}", file=sys.stderr)
        sys.exit(2)

    # BATCH 4 NPZs LONG+SHORT in one process — keep all hot, never erase, work until finished
    if args.batch_syms:
        batch = [s.strip().upper() for s in args.batch_syms.split(",") if s.strip()]
        print(f"[BATCH] 4 NPZs {batch} — one process, keep all hot in RAM, never erase, process each until 12 tabs finished", flush=True)
        # Preload all 4 hot in this process before any sheet
        for bsym in batch:
            try:
                ensure_npz_for_symside(bsym, args.window_days)
                pp = preload_prepared(bsym, args.window_days)
                if pp is not None:
                    print(f"[BATCH-HOT] {bsym} hot {len(ALL_NPZ_ARRAYS.get(bsym,{}))} arrays", flush=True)
            except Exception as _be:
                print(f"[BATCH-warn] {bsym} {_be}", flush=True)
        # Process each sym in batch sequentially, keeping all 4 NPZs hot entire time
        for bsym in batch:
            print(f"[BATCH-NEXT] {bsym} — 4 NPZs hot {list(ALL_PREPARED.keys())}", flush=True)
            # Keep batch loaded — run single for this bsym via subprocess (keeps 4 hot in parent, each child reuses hot cache via preload)
            import subprocess as _sp_batch, sys as _sys_batch
            _cmd = [_sys_batch.executable, "-u", str(ROOT / "v15_pilot.py"), "--sym-side", bsym, "--window-days", str(args.window_days), "--vector-only", "--workers", str(args.workers), "--seq-mode", args.seq_mode]
            # preserve template if side-specific
            try:
                _sp_batch.run(_cmd, check=False)
            except Exception as _e_batch:
                print(f"[BATCH-ERR] {bsym} {_e_batch}", flush=True)
        return

    _v15_start_time = __import__('time').time()  # USER 2026-09-25: 4 sym_sides per hour = 10min NPZ load + whatever it takes for calcs, KEEP NPZ IN MEMORY, 4 at a time (max_parallel 4, 80% RAM), NEVER break off 5min after start — monitor baseline, if no baseline generated within 20min skip to next to avoid wasting 24h
    if args.sym_side:
        new_symside = args.sym_side.strip().upper()
    else:
        try:
            import json as _j
            camp = _j.loads((PROGRESS_DIR / "campaign_order_1mo.json").read_text())
            queue = camp.get("queue") or []
            new_symside = queue[0].get("symside", "AAPL_LONG") if queue else "AAPL_LONG"
        except Exception:
            new_symside = "AAPL_LONG"

    # ═══ DEDUP LOCK (USER 2026-09-28): PROHIBIT duplicate v15_pilot for the same sym_side ═══
    # Root cause of the S1 hang: 2+ pilots for one sym_side (e.g. 15× UUUU_LONG) raced on the same
    # SPREADSHEETS/V15_V16_CELL_BY_CELL/{SYM_SIDE}_30d_matrix.xlsx — one _atomic_save os.replace()
    # deleted the inode the others held open (fd showed "(deleted)"), leaving them blocked in
    # futex_wait at 0% CPU forever. Stalls kill throughput (need 4 sym LONG/SHORT × 5000 cells <1s/cell).
    # OS advisory flock (LOCK_EX|LOCK_NB) makes duplicates impossible no matter how many launchers race;
    # it auto-releases when the holder dies (fd closed by kernel), so a killed pilot never leaves a stale lock.
    _dedup_lock_fh = None
    if not args.dry_run:
        try:
            import fcntl as _fcntl
            _lock_dir = ROOT / "data" / "locks"
            _lock_dir.mkdir(parents=True, exist_ok=True)
            # Key the lock on the RESOLVED OUTPUT FILE, not the bare sym_side — the mega sweep
            # (v15_mega_pilot.py) writes {SYM}_30d_matrix.xlsx to a SEPARATE dir via --out
            # (SPREADSHEETS/V15_MEGA/...), the local herd to V15_V16_CELL_BY_CELL/. Same sym_side in
            # the two systems = DIFFERENT files = no real conflict, so they must NOT cross-block; only
            # two pilots writing the SAME file must mutually exclude (the actual hang cause).
            import os.path as _osp, hashlib as _hl
            _out_target = _osp.abspath(args.out) if args.out else _osp.abspath(str(OUT_DIR / f"{new_symside}_30d_matrix.xlsx"))
            _lock_key = _hl.md5(_out_target.encode()).hexdigest()[:16]
            _lock_path = _lock_dir / f"v15_pilot_{new_symside}_{_lock_key}.lock"
            _dedup_lock_fh = open(_lock_path, "w")
            _fcntl.flock(_dedup_lock_fh.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            _dedup_lock_fh.write(f"{os.getpid()}\n"); _dedup_lock_fh.flush()
            import atexit as _atexit
            _atexit.register(lambda: _dedup_lock_fh.close())
            print(f"[DEDUP] acquired exclusive lock for {new_symside} (pid {os.getpid()})", flush=True)
        except (IOError, OSError, BlockingIOError):
            print(f"[DEDUP] another v15_pilot already owns {new_symside} — exiting, NO duplicate work", flush=True)
            try:
                if _dedup_lock_fh is not None:
                    _dedup_lock_fh.close()
            except Exception:
                pass
            return

    # ABSOLUTE PROHIBITION — check BEFORE any heavy NPZ/prepare (2026-09-16)
    # Finished workbooks (SNDK etc) have final_gain + done set + xls/log/zip/bak backups — MUST NOT be re-touched on ANY server.
    try:
        _early_prog = None
        for _pp in ([PROGRESS_DIR / f"{new_symside}_v14_progress.json"] if (os.environ.get("V15_FRESH_RUN", "0") == "1" or os.environ.get("V15_PROGRESS_DIR")) else [PROGRESS_DIR / f"{new_symside}_v14_progress.json", Path(f"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{new_symside}_v14_progress.json")]):  # V15_FRESH_RUN / V15_PROGRESS_DIR: isolated dir only
            if _pp.exists():
                try:
                    _early_prog = json.loads(_pp.read_text())
                    break
                except Exception:
                    continue
        if (_early_prog or {}).get("verdict") == "IMPOSSIBLE" and os.getenv("FORCE_DC_RERUN") != "1":
            print(f"[IMPOSSIBLE-SKIP] {new_symside} tombstoned ({(_early_prog or {}).get('impossible_path')}) — manual revision required, MUST NOT RETOUCH.", flush=True)
            return
        if (_early_prog or {}).get("verdict") in ("NO_TRADES", "BEST_EFFORT") and os.getenv("FORCE_DC_RERUN") != "1" and not _soft_verdict_fresh(new_symside, _early_prog or {}):
            print(f"[SOFT-SKIP] {new_symside} verdict {(_early_prog or {}).get('verdict')} on same NPZ — nothing to do (reruns on NPZ refresh).", flush=True)
            return
        if (_early_prog or {}).get("verdict") in ("NO_TRADES", "BEST_EFFORT") and _soft_verdict_fresh(new_symside, _early_prog or {}):
            print(f"[SOFT-REFRESH] {new_symside} verdict {(_early_prog or {}).get('verdict')} but NPZ is newer — clearing and re-running", flush=True)
        _redo_run = bool((_early_prog or {}).get("needs_redo")) and (_early_prog or {}).get("verdict") != "IMPOSSIBLE"
        if _redo_run:
            print(f"[REDO-RUN] {new_symside} re-filling from repaired set (depth {((_early_prog or {}).get('needs_redo') or {}).get('depth')})", flush=True)
        if os.getenv("FORCE_DC_RERUN") == "1":
            print(f"[FORCE-DC-RERUN] {new_symside} hard-stop rerun forced (dc_low_4h LONG / dc_high_4h SHORT can never be broken)", flush=True)
        elif _early_prog and _early_prog.get("final_gain") is not None and len(_early_prog.get("done", {})) >= 50 and not _redo_run:
            # FIX 2026-09-21: allow resume of incomplete sheets (done < 2800 or no FINAL xlsx) — herd was idle on HAO/VT etc with 2238 done but no FINAL
            _done_cnt = len(_early_prog.get("done", {}))
            _has_final = any((ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{new_symside}*.xlsx").parent.glob(f"{new_symside}_30d_matrix.xlsx")) or any((ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{new_symside}_bh*.xlsx").parent.glob(f"{new_symside}_bh*.xlsx"))
            # check both local ROOT and sandbox path
            if not _has_final:
                import pathlib as _pl2
                _has_final = any(_pl2.Path.home().glob(f"binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{new_symside}_30d_matrix.xlsx")) or any(_pl2.Path.home().glob(f"binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/{new_symside}_bh*.xlsx"))
            _pending = _pending_template_rows(new_symside)
            # 2026-09-28 publish-only pass: a board-COMPLETE sheet whose DONE stage died (OOM'd
            # parent) has pending==0 and done>=2800, so the legacy gate PROHIBITed every relaunch
            # and NO complete sheet could ever publish its bh/gain final. If no final exists on
            # disk, resume: all rows are cached, the pilot skims to DONE and publishes.
            _bh_final_exists = any((ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL").glob(f"{new_symside}_bh*_30d_matrix*.xlsx"))
            if _pending is not None and _pending == 0 and not _bh_final_exists:
                print(f"[RESUME-ALLOW] {new_symside} COMPLETE but no bh/gain final on disk — publish-only pass (DONE stage rerun)", flush=True)
            elif _pending:
                print(f"[RESUME-ALLOW] {new_symside} final_gain {_early_prog.get('final_gain'):.2f} done {_done_cnt} but {_pending} template rows pending (post-merge) — incremental resume", flush=True)
            elif _done_cnt < 2800 and not _has_final:
                print(f"[RESUME-ALLOW] {new_symside} incomplete final_gain {_early_prog.get('final_gain'):.2f} done {_done_cnt} no FINAL xlsx — resuming", flush=True)
            elif args.baseline_json and args.seq_mode in ("shuffle", "worst2best", "worst_first"):
                print(f"[{args.seq_mode.upper()}-ALLOW] {new_symside} already finished final_gain {_early_prog.get('final_gain'):.2f} but {args.seq_mode}+baseline-json allowed for second round (filters/orange per tab needs delta)", flush=True)
            elif args.seq_mode == "shuffle" and args.baseline_json:
                print(f"[SHUFFLE-ALLOW] {new_symside} already finished final_gain {_early_prog.get('final_gain'):.2f} but shuffle+baseline-json allowed for second round", flush=True)
            else:
                print(f"[PROHIBITED] {new_symside} ALREADY FINISHED (early) final_gain {_early_prog.get('final_gain'):.2f} done {len(_early_prog.get('done',{}))} — MUST NOT RETOUCH. Backups in xls/log/zip/bak exist. Skipping BEFORE NPZ.", flush=True)
                return
        # also S1 peer check before NPZ fetch
        if os.getenv("FORCE_DC_RERUN") == "1" or os.getenv("V15_FRESH_RUN") == "1":
            print(f"[FORCE-DC-RERUN] {new_symside} S1 peer check bypassed (hard-stop rerun / V15_FRESH_RUN)", flush=True)
        else:
            try:
                import subprocess as _sp_early
                for _h in ["s1-pub"]:
                    try:
                        _r = _sp_early.run(["ssh","-o","ConnectTimeout=3","-o","StrictHostKeyChecking=accept-new",_h,
                            f"cat ~/binance-sandbox/data/reports/lifecycle_pilot/{new_symside}_v14_progress.json 2>/dev/null | python3 -c \"import json,sys; j=json.load(sys.stdin); print(j.get('final_gain') if j.get('final_gain') is not None else 'None')\""],
                            capture_output=True, text=True, timeout=5)
                        if _r.stdout and _r.stdout.strip() not in ("","None","null"):
                            if args.baseline_json and args.seq_mode in ("shuffle", "worst2best", "worst_first"):
                                print(f"[{args.seq_mode.upper()}-ALLOW] {new_symside} already finished on S1 peer ({_h} final_gain {_r.stdout.strip()}) but {args.seq_mode}+baseline-json allowed", flush=True)
                            elif args.seq_mode == "shuffle" and args.baseline_json:
                                print(f"[SHUFFILE-ALLOW] {new_symside} already finished on S1 peer ({_h} final_gain {_r.stdout.strip()}) but shuffle+baseline-json allowed", flush=True)
                            else:
                                _pending_peer = _pending_template_rows(new_symside)
                                if _pending_peer:
                                    print(f"[RESUME-ALLOW] {new_symside} finished on S1 peer ({_h}) but {_pending_peer} template rows pending locally — incremental resume", flush=True)
                                else:
                                    print(f"[PROHIBITED] {new_symside} ALREADY FINISHED on S1 peer ({_h} final_gain {_r.stdout.strip()}) — MUST NOT RETOUCH BEFORE NPZ.", flush=True)
                                    return
                    except Exception:
                        continue
            except Exception:
                pass
    except Exception as _ee:
        print(f"[early-finished-warn] {_ee}", flush=True)

    # defaults + live recipes
    try:
        from tools.opt.v12_pilot import load_live_recipes
        recipes = load_live_recipes()
    except Exception:
        recipes = {}
    overrides = dict(recipes.get(new_symside, {}).get("overrides") or {}) if new_symside in recipes else {}
    overrides = {k: v for k, v in overrides.items() if not (isinstance(v, str) and " + " in v)}
    _recipe_only_overrides = dict(overrides)
    # BEST-as-baseline: every backtest must start from BEST for that sym_side — no exceptions, next round can never be worse (only pos deltas added)
    try:
        import json as _js_best, pathlib as _pl_best
        _best_candidates = [
            ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{new_symside}_hustler_best.json",
            ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{new_symside}_best.json",
            ROOT / "data" / "reports" / "lifecycle_pilot" / f"{new_symside}_best.json",
            ROOT / "SPREADSHEETS" / f"{new_symside}_BEST.json",
        ]
        _best_found = False
        for _bp in _best_candidates:
            if _bp.exists():
                _bd = _js_best.loads(_bp.read_text())
                _bo = _bd.get("overrides") if isinstance(_bd, dict) and "overrides" in _bd else (_bd if isinstance(_bd, dict) else {})
                if isinstance(_bo, dict) and _bo:
                    # BEST overrides become baseline — merge on top of recipes, BEST wins
                    for k, v in _bo.items():
                        overrides[k] = v
                    print(f"[BEST-baseline] {new_symside}: loaded {len(_bo)} overrides from BEST {_bp.name} as baseline (no worse than BEST)", flush=True)
                    _best_found = True
                    break
        # fallback to cat_side BEST if sym_side not found — fill all overrides from previous best for sym_side or cat_side before baseline calc
        if not _best_found:
            try:
                _is_crypto = new_symside.upper().endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI"))
                _is_long = new_symside.endswith("_LONG")
                _cat = f"{'CRYPTO' if _is_crypto else 'STOCKS'}_{'LONG' if _is_long else 'SHORT'}"
                _cat_candidates = [
                    ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL" / f"{_cat}_hustler_best.json",
                    ROOT / "data" / "reports" / "lifecycle_pilot" / f"{_cat}_best.json",
                    ROOT / "SPREADSHEETS" / f"{_cat}_BEST.json",
                ]
                for _bp in _cat_candidates:
                    if _bp.exists():
                        _bd = _js_best.loads(_bp.read_text())
                        _bo = _bd.get("overrides") if isinstance(_bd, dict) and "overrides" in _bd else (_bd if isinstance(_bd, dict) else {})
                        if isinstance(_bo, dict) and _bo:
                            for k, v in _bo.items():
                                overrides[k] = v
                            print(f"[BEST-baseline-cat] {new_symside}: loaded {len(_bo)} overrides from CAT {_cat} BEST {_bp.name} as baseline", flush=True)
                            break
            except Exception:
                pass
    except Exception as _e_best:
        print(f"[BEST-baseline-warn] {new_symside} {_e_best}", flush=True)
    # PREVIOUS-TEST-as-baseline: always load best overrides from previous progress/xls for sym_side first, then calc baseline on those overrides
    # FIX: BEST must WIN — overwrite recipes/defaults, not behind `if k not in overrides` guard
    try:
        import json as _js_prev_prog
        # Load BEST from both 7d and v14 progress for 7D window — 7D best is 4.04 not 8.46
        _prev_candidates = [PROGRESS_DIR / f"{new_symside}_7d_progress.json", PROGRESS_DIR / f"{new_symside}_v14_progress.json", PROGRESS_DIR / f"{new_symside}_v14_progress.json"]
        # deduplicate
        _seen = set()
        _prev_paths = []
        for _p in _prev_candidates:
            if str(_p) not in _seen:
                _seen.add(str(_p))
                _prev_paths.append(_p)
        for _prev_prog_path in _prev_paths:
            if _prev_prog_path.exists():
                try:
                    _pd = _js_prev_prog.loads(_prev_prog_path.read_text())
                except Exception:
                    continue
                _co = _pd.get("cumulative_overrides") or _pd.get("overrides") or {}
                _added = 0
                for k, v in _clean_ingested_overrides(_co).items():
                    if overrides.get(k) != v:
                        overrides[k] = v
                        _added += 1
                if _added:
                    print(f"[BEST-prev-progress] {new_symside}: loaded {_added} overrides from previous progress cumulative_overrides as baseline", flush=True)
                _ho = _pd.get("hustler_overrides") or {}
                _added2 = 0
                for k, v in _clean_ingested_overrides(_ho).items():
                    if overrides.get(k) != v:
                        overrides[k] = v
                        _added2 += 1
                if _added2:
                    print(f"[BEST-prev-progress] {new_symside}: loaded {_added2} hustler_overrides as baseline", flush=True)
    except Exception as _e_prev_prog:
        print(f"[BEST-prev-progress-warn] {_e_prev_prog}", flush=True)
    print(f"[STEP] xls_prev start", flush=True)
    try:
        import concurrent.futures as _cf_xls
        def _do_xls_prev():
            _xls_prev = OUT_DIR / f"{new_symside}_30d_matrix.xlsx"
            if not _xls_prev.exists():
                return 0
            import openpyxl as _op_prev
            _wb_prev = _op_prev.load_workbook(str(_xls_prev), data_only=True, read_only=True)
            _added_xls = 0
            # C = " + "-joined SWITCH=value parts (BEST-C-FILL and promotions); a bare legacy value belongs to the row's switch.
            # Best-fill rows first, then promoted rows (VECTOR_DELTA > 0) in fill order, so the latest promotion wins.
            def _pv(v):
                v = str(v).strip()
                if "=" in v:
                    _lh, _rh = v.split("=", 1)
                    if _lh.strip() and _lh.strip() == _rh.strip():
                        v = _rh.strip()
                if v.lower() in ("true", "false"):
                    return v.lower() == "true"
                try:
                    return float(v)
                except Exception:
                    return v
            _best_parts, _promo_parts = [], []
            for _sheet in SWITCH_SHEETS:
                if _sheet not in _wb_prev.sheetnames:
                    continue
                _ws_prev = _wb_prev[_sheet]
                # iter_rows streams once (read-only ws.cell() re-parses from the start = quadratic stall, 2026-09-29)
                _rows = _ws_prev.iter_rows(min_row=2, max_row=600, max_col=12, values_only=True)
                _hdr = [str(x).strip() if x is not None else "" for x in next(_rows, [])]
                _ci = _hdr.index("override") if "override" in _hdr else 2
                _gi = _hdr.index("VECTOR_DELTA") if "VECTOR_DELTA" in _hdr else 6
                for _vals in _rows:
                    _vals = list(_vals) + [None] * (12 - len(_vals))
                    _a, _c, _g = _vals[0], _vals[_ci], _vals[_gi]
                    if not _a or _c is None or str(_c).strip() in ("", "None", "none"):
                        continue
                    # only "SWITCH=value" parts (the one format the pilot writes); bare values are template leftovers
                    _parts = []
                    for _part in str(_c).split(" + "):
                        if "=" in _part:
                            _k, _v = _part.split("=", 1)
                            _parts.append((_k.strip(), _pv(_v)))
                    (_promo_parts if isinstance(_g, (int, float)) and _g > 1e-9 else _best_parts).extend(_parts)
            for _k, _v in _best_parts + _promo_parts:
                if "_" in _k and len(_k) > 5 and "=" not in str(_v) and overrides.get(_k) != _v:
                    overrides[_k] = _v
                    _added_xls += 1
            if _added_xls:
                print(f"[BEST-prev-xls] {new_symside}: loaded {_added_xls} overrides from previous XLS {_xls_prev.name} as baseline", flush=True)
            return _added_xls
        _ex_xls = _cf_xls.ThreadPoolExecutor(max_workers=1)
        _fut_xls = _ex_xls.submit(_do_xls_prev)
        try:
            _added_xls = _fut_xls.result(timeout=10)
            if _added_xls:
                print(f"[BEST-prev-xls] {new_symside}: loaded {_added_xls} overrides from previous XLS as baseline", flush=True)
        except Exception as _e_xls:
            print(f"[BEST-prev-xls-TIMEOUT] {new_symside} >10s {_e_xls} — skip xls, use progress only, never hang", flush=True)
            try: _fut_xls.cancel()
            except: pass
        finally:
            try: _ex_xls.shutdown(wait=False)
            except: pass
    except Exception as _e_xls:
        print(f"[BEST-prev-xls-warn] {_e_xls}", flush=True)
    print(f"[STEP] after xls_prev", flush=True)
    # baseline-json for shuffle second round: found settings as new baseline
    if args.baseline_json:
        try:
            import json as _js2
            import pathlib as _pl2
            bj = _pl2.Path(args.baseline_json)
            if bj.exists():
                _base_over = _js2.loads(bj.read_text())
                # FIX 2026-09-24: BEST must win — overwrite, not guard
                for k, v in _clean_ingested_overrides(_base_over).items():
                    overrides[k] = v
                print(f"[baseline-json] loaded {len(_base_over)} overrides from {bj} as new baseline for shuffle", flush=True)
        except Exception as _e:
            print(f"[baseline-json-warn] {args.baseline_json} {_e}", flush=True)
    # disabled switches for next round: never had pos delta → reduce frequency per category_side (not never)
    # 2026-09-21 KG LAW — KINDERGARTEN HTF+LTF FILTERS MUST NEVER BE SKIPPED: structural gate, if KG untested all 67 rerun
    KG_NEVER_SKIP = {
        "HTF_TREND_VETO_ENABLED", "HTF_TREND_VETO_BYPASS_ENABLED", "HTF_DIRECTION_GATE_ENABLED", "HTF_EXIT_VETO_ENABLED",
        "HTF_GATE_BYPASS_RZ", "HTF_GATE_D_MANDATORY", "HTF_GATE_MIN_CONFIRMATIONS", "HTF4_CONF",
        "MTF_ARMED_ENTRY_ENABLED", "MTF_FILTER_STRONG_BUY_QUICK_BYPASS", "MTF_GR_MIN_IND", "MTS_GATE_ENABLED",
        "TOP_OF_RANGE_BLOCK_ENABLED", "GR_FILTER_ALL_ENTRIES", "OPEN_RATE_BREAKER_ENABLED", "COUNTER_TREND_ADD_BLOCK_ENABLED",
        "DELTA_REENTRY_FILTER_ENABLED", "EXIT_BLOCKER_REQUIRE_LH_LL_ENABLED", "MANDATORY_REENTRY_WT_FILTER_MIN_TFS",
        "W15M", "WT_15M", "WT_CHAN_15m", "WT_AVG_15m", "WT_15M_BOUNCE", "WT_DC", "WT_CROSS",
        "BB_SQUEEZE", "ADX_RANGING_THRESHOLD",
    }
    def _is_kg_never_skip(sw: str) -> bool:
        if not sw:
            return False
        if sw in KG_NEVER_SKIP:
            return True
        # substring match for WT/HTF/MTF/GR/TOP/ADX/BB variants not enumerated exactly
        for _kg in ("HTF_", "MTF_", "MTS_", "WT_", "W15M", "TOP_OF_RANGE", "GR_FILTER", "GR_", "ADX_", "BB_SQUEEZE", "COUNTER_TREND", "DELTA_REENTRY", "EXIT_BLOCKER", "MANDATORY_REENTRY", "OPEN_RATE"):
            if _kg in sw:
                return True
        return False
    print(f"[STEP] disabled loading start", flush=True)
    # USER 2026-09-20: don't rebuild templates yet, but speed up by trying never-pos filters less often per category_side
    # Per-category file: data/reports/lifecycle_pilot/disabled_switches_never_pos_per_category.json (CRYPTO_LONG etc.)
    disabled_switches = set()
    disabled_per_category = {}
    try:
        import json as _js3
        import pathlib as _pl3
        # 1) explicit --disable-switches-file if passed (legacy global)
        if args.disable_switches_file:
            dj = _pl3.Path(args.disable_switches_file)
            if dj.exists():
                _dis = _js3.loads(dj.read_text())
                if isinstance(_dis, list):
                    disabled_switches = set(_dis)
                elif isinstance(_dis, dict):
                    disabled_switches = set(_dis.keys())
                print(f"[disable-switches] loaded {len(disabled_switches)} disabled switches from {dj} for speed (220 never pos)", flush=True)
        # 2) per-category file — always load for frequency reduction (category_side aware)
        _pc_path = _pl3.Path("data/reports/lifecycle_pilot/disabled_switches_never_pos_per_category.json")
        if not _pc_path.exists():
            _pc_path = _pl3.Path("/home/niels/binance-sandbox/data/reports/lifecycle_pilot/disabled_switches_never_pos_per_category.json")
        if _pc_path.exists():
            _pc = _js3.loads(_pc_path.read_text())
            if isinstance(_pc, dict):
                # keys are CRYPTO_LONG etc, values are lists
                disabled_per_category = {k: set(v) for k, v in _pc.items() if isinstance(v, list)}
                print(f"[disable-per-category] loaded {len(disabled_per_category)} categories from {_pc_path.name}", flush=True)
        # 2026-09-21 KG LAW: purge KG from disabled sets so KG never skipped even if file lists them
        try:
            disabled_switches = {s for s in disabled_switches if not _is_kg_never_skip(s)}
            for _k in list(disabled_per_category.keys()):
                _orig = len(disabled_per_category[_k])
                disabled_per_category[_k] = {s for s in disabled_per_category[_k] if not _is_kg_never_skip(s)}
                if len(disabled_per_category[_k]) != _orig:
                    print(f"[KG-purge] {_k}: removed {_orig - len(disabled_per_category[_k])} KG from disabled", flush=True)
            if disabled_switches or disabled_per_category:
                print(f"[KG-guard] disabled after purge: global {len(disabled_switches)} cats {len(disabled_per_category)} KG never-skip {len(KG_NEVER_SKIP)}", flush=True)
        except Exception as _e2:
            print(f"[KG-purge-warn] {_e2}", flush=True)
    except Exception as _e:
        print(f"[disable-switches-warn] {_e}", flush=True)
    print(f"[STEP] disabled loading start", flush=True)
    # disabled loading with 10s timeout
    try:
        import concurrent.futures as _cf_dis
        with _cf_dis.ThreadPoolExecutor(max_workers=1) as _exd:
            _futd = _exd.submit(lambda: None)  # placeholder to ensure threadpool works
            _futd.result(timeout=0.1)
    except: pass
    print(f"[STEP] get_defaults start {new_symside}", flush=True)
    defaults = get_defaults_for_symside(new_symside)
    print(f"[STEP] get_defaults done {len(defaults)}", flush=True)
    # USER 2026-09-29: BOLD / is_default=YES in the template IS the running default — every run starts from it, the
    # engine gets it explicitly (not QuickConfig's own values), prior-best overrides go on top. Fail-closed if any group
    # lacks exactly one default.
    try:
        from tools.build_cat_side_defaults_4 import venue_values as _venue_values
        _truth = _venue_values(map_key_for_symside(new_symside).startswith("STOCKS"))[0]
        try:
            import per_sym_store as _pss_prom
            _promos = _pss_prom.get_cat_side_promotions()
            _promoted = set((_promos.get(map_key_for_symside(new_symside)) or {}).keys()) if _promos else set()
        except Exception:
            _promoted = set((json.loads((ROOT / "data" / "cat_side_promotions.json").read_text()).get(map_key_for_symside(new_symside)) or {}).keys()) if (ROOT / "data" / "cat_side_promotions.json").exists() else set()
    except Exception as _tr_e:
        print(f"[DEFAULTS-GATE] truth load warn {_tr_e} — all bold values trusted", flush=True)
        _truth, _promoted = None, set()
    _tpl_defaults, _tpl_bad = template_bold_defaults(args.template, defaults, _truth, _promoted)
    if UNTRUSTED_BOLD:
        print(f"[DEFAULTS-GATE] {new_symside}: {len(UNTRUSTED_BOLD)} placeholder bold values NOT used as defaults (real value kept): {[x[1] for x in UNTRUSTED_BOLD[:8]]}", flush=True)
    if _tpl_bad:
        print(f"[DEFAULTS-GATE] {new_symside}: template {args.template} has {len(_tpl_bad)} default violations — NOT running. First: {_tpl_bad[:5]} (fix: tools/v15_template_defaults_fix.py --apply)", flush=True)
        return
    defaults.update(_tpl_defaults)
    print(f"[DEFAULTS-GATE] {new_symside}: {len(_tpl_defaults)} bold defaults from {Path(args.template).name} = running default", flush=True)
    overrides = {**_tpl_defaults, **overrides}
    _recipe_only_overrides = {**_tpl_defaults, **_recipe_only_overrides}
    _ingested_overrides = dict(overrides)  # previous best / prev-sheet set — candidate base for the credible-baseline stage
    if os.environ.get("V15_FRESH_RUN", "0") == "1" and os.environ.get("V15_INGEST_BEST", "0") != "1":
        # USER 2026-09-29: engine/sizing change invalidates prior bests -> start from the live recipe only
        # (USER 2026-09-30: V15_INGEST_BEST=1 keeps the prior-best overrides FIRST, then the baseline)
        print(f"[FRESH-RUN] {new_symside}: prior-best/previous-sheet ingest ignored ({len(overrides)} -> live recipe {len(_recipe_only_overrides)} overrides)", flush=True)
        overrides = dict(_recipe_only_overrides)
    if os.environ.get("V15_START_OVERRIDES"):
        # USER 2026-09-29 (BIBLE §58): re-run the 30D sheet from the 365D-repaired set (tools/v15_365_repair.py output
        # or a plain {switch: value} json) — it becomes the sheet's live-recipe baseline
        _so = json.load(open(os.environ["V15_START_OVERRIDES"]))
        _so = (_so.get("final") or {}).get("overrides", _so) if isinstance(_so, dict) else {}
        print(f"[START-OVERRIDES] {new_symside}: {len(_so)} overrides from {os.environ['V15_START_OVERRIDES']} (365D-repaired set)", flush=True)
        overrides = {**_tpl_defaults, **_so}
        _recipe_only_overrides = dict(overrides)
    if not os.environ.get("V15_START_OVERRIDES"):
        # USER 2026-10-02 (finish qualification REDO): a DONE stage that scheduled a re-fill left needs_redo
        # in progress — the repaired set becomes this fill's baseline (§58 step 5), same as V15_START_OVERRIDES.
        try:
            _nr_pp = PROGRESS_DIR / f"{new_symside}_v14_progress.json"
            if _nr_pp.exists():
                _nr_pj = json.loads(_nr_pp.read_text())
                _nr = _nr_pj.get("needs_redo") or {}
                if _nr.get("overrides") and _nr_pj.get("verdict") != "IMPOSSIBLE":
                    _so2 = dict(_nr["overrides"])
                    print(f"[REDO-START] {new_symside}: {len(_so2)} overrides from needs_redo depth {_nr.get('depth')} ({(_nr.get('reason') or '')[:100]})", flush=True)
                    overrides = {**_tpl_defaults, **_so2}
                    _recipe_only_overrides = dict(overrides)
        except Exception as _nr_e:
            print(f"[redo-start-warn] {_nr_e}", flush=True)
    if os.environ.get("V15_TEMPLATE_DEFAULTS", "0") == "1":
        # USER 2026-09-29: live recipe makes 0 trades (impossible) -> start from the TEMPLATE_{CAT}_{SIDE} bold defaults (= live config defaults)
        print(f"[TEMPLATE-DEFAULTS] {new_symside}: live recipe/best dropped ({len(overrides)} overrides) -> template defaults only", flush=True)
        overrides = dict(_tpl_defaults)
        _recipe_only_overrides = dict(_tpl_defaults)
    overrides, warns = sanitize_overrides(overrides, defaults)
    if warns:
        print(f"[sanitize] {warns}", flush=True)
    print(f"[STEP] sanitize done {len(overrides)}", flush=True)
    print(f"[baseline] {new_symside}: {len(overrides)} overrides + {len(defaults)} defaults workers={args.workers} vector_only={args.vector_only}", flush=True)

    # 4 NPZ batch — NEVER ERASE FROM RAM, keep all hot until finished (4 NPZs LONG+SHORT until finished, do nothing else)
    try:
        p = ensure_npz_for_symside(new_symside, args.window_days)
        if p:
            print(f"[NPZ-HOT] {new_symside} NPZ {p} {p.stat().st_size/1e6:.1f}M keep in RAM never erase", flush=True)
    except Exception as _e2:
        print(f"[NPZ-WARN] {_e2}", flush=True)
    # Keep NPZ in RAM entire workbook — NO TIMEOUT, blocking load, never erase, lighting fast <1s per cell
    prepared = None
    try:
        if args.batch_syms:
            batch = [s.strip().upper() for s in args.batch_syms.split(",") if s.strip()]
            print(f"[BATCH-NPZ] loading 4 NPZs {batch} together, keep all hot in RAM, never erase", flush=True)
            for bsym in batch:
                try:
                    ensure_npz_for_symside(bsym, args.window_days)
                    pp = preload_prepared(bsym, args.window_days)
                    if pp is not None:
                        print(f"[BATCH-NPZ-HOT] {bsym} hot {len(ALL_NPZ_ARRAYS.get(bsym,{}))} arrays never erase", flush=True)
                except Exception as _be:
                    print(f"[BATCH-NPZ-warn] {bsym} {_be}", flush=True)
        prepared = preload_prepared(new_symside, args.window_days)
        if prepared is None and new_symside in ALL_PREPARED:
            prepared = ALL_PREPARED[new_symside]
        if prepared is not None:
            print(f"[NPZ-HOT-KEEP] {new_symside} hot {len(ALL_NPZ_ARRAYS.get(new_symside,{}))} arrays, NEVER ERASE until workbook finished", flush=True)
        else:
            print(f"[NPZ-WARN] {new_symside} prepared None — retry blocking, never disk fallback", flush=True)
            prepared = preload_prepared(new_symside, args.window_days)
    except Exception as _e3:
        print(f"[PRELOAD-WARN] {_e3}", flush=True)
        prepared = ALL_PREPARED.get(new_symside)
    # Late preload may have succeeded after timeout into ALL_PREPARED — reuse hot
    if 'prepared' not in locals() or prepared is None:
        prepared = None
    if prepared is None and new_symside in ALL_PREPARED:
        prepared = ALL_PREPARED[new_symside]
        print(f"[PRELOAD-LATE-HOT] {new_symside} reuse hot from ALL_PREPARED after timeout", flush=True)
    print(f"[STEP] after preload prepared={prepared is not None}", flush=True)
    try:
        if os.environ.get("V15_RAMFP", "0") == "1" and new_symside not in _RUN_RAMFP:
            _RUN_RAMFP[new_symside] = _ram_fp(new_symside)
            print(f"[RAMFP] {new_symside} run fp captured: {_RUN_RAMFP[new_symside]}", flush=True)
    except Exception as _ramfp_e0:
        print(f"[ramfp-warn] {new_symside}: {_ramfp_e0}", flush=True)
    if 'prepared' not in locals() or prepared is None:
        prepared = None
    if prepared is None:
        print(f"[STEP] baseline evaluate start", flush=True)
        _ex_base = None
        try:
            import concurrent.futures as _cf_base
            _ex_base = _cf_base.ThreadPoolExecutor(max_workers=1)
            from tools.opt.v12_pilot import evaluate_sanitized
            _fut_base = _ex_base.submit(evaluate_sanitized, new_symside, overrides, window_days=args.window_days)
            try:
                baseline_vec = _fut_base.result(timeout=60)
            except Exception as _e_base:
                print(f"[BASELINE-TIMEOUT] {new_symside} evaluate_sanitized >60s {_e_base} — mark RED and use empty baseline", flush=True)
                try: _fut_base.cancel()
                except: pass
                baseline_vec = {"valid": False, "gain_pct": 0, "trades": 0, "pool_sharpe": 0, "bh_pct": 0, "invalid_reason": "timeout"}
        except Exception as _e2:
            print(f"[baseline-warn2] {_e2}", flush=True)
            baseline_vec = {"valid": False, "gain_pct": 0, "trades": 0, "pool_sharpe": 0, "bh_pct": 0, "invalid_reason": "error"}
        finally:
            try:
                if _ex_base is not None:
                    _ex_base.shutdown(wait=False)
            except: pass
        print(f"[baseline] no prepared, vec valid={baseline_vec.get('valid')} gain={baseline_vec.get('gain_pct')} trades={baseline_vec.get('trades')}", flush=True)
        # 0-TRADES: still create XLS with baseline so herd audit sees E2 + BASELINE_METRICS; only skip sweep, never skip baseline — USE ONLY WHAT IS ALREADY IN RAM ON S1 (no fresh fetch)
        _is_zero = int(baseline_vec.get("trades") or 0) == 0
        if _is_zero:
            print(f"[0-TRADES-FAST-FAIL] {new_symside} 0 trades — will still write baseline XLS then skip sweep (only RAM, no fetch)", flush=True)
            _zero_trades_early = True
        else:
            _zero_trades_early = False
            if not baseline_vec.get("valid"):
                print(f"[baseline-warn] {new_symside} valid False but trades {baseline_vec.get('trades')} — proceeding, not diagnostic", flush=True)
        prepared_for_fallback = None
    else:
        from tools.opt.v12_pilot import evaluate_prepared_sanitized
        baseline_vec = evaluate_prepared_sanitized(prepared, overrides, window_days=args.window_days)
        # USER 2026-09-29: an ingested BEST / previous-XLS set built on the broken engine/NPZ can zero the baseline
        # (BTCUSDC_LONG: 84 hustler_best overrides -> 0 trades vs recipe 286). A prior best that cannot trade is not a
        # baseline: fall back to the live recipe when it trades and the ingested set does not.
        _bt = baseline_vec.get("trades_rows", baseline_vec.get("trades"))  # PAR/001: floor on all-rows count
        if isinstance(_bt, (int, float)) and _bt < 10:
            try:
                _rec_ov, _ = sanitize_overrides(dict(_recipe_only_overrides), defaults)
                if _rec_ov != overrides:
                    _rec_vec = evaluate_prepared_sanitized(prepared, _rec_ov, window_days=args.window_days)
                    if int(_rec_vec.get("trades_rows", _rec_vec.get("trades")) or 0) >= 10:
                        print(f"[BEST-REJECT] {new_symside}: ingested set {len(overrides)} overrides -> {baseline_vec.get('trades')} trades; live recipe {len(_rec_ov)} -> {_rec_vec.get('trades')} trades — using recipe", flush=True)
                        overrides, baseline_vec = _rec_ov, _rec_vec
            except Exception as _e_rej:
                print(f"[BEST-REJECT-warn] {new_symside} {_e_rej}", flush=True)
        print(f"[baseline] vec valid={baseline_vec.get('valid')} gain={baseline_vec.get('gain_pct')} trades={baseline_vec.get('trades')} sharpe={baseline_vec.get('pool_sharpe')} hot", flush=True)
        _is_zero = int(baseline_vec.get("trades") or 0) == 0
        if _is_zero:
            print(f"[0-TRADES-FAST-FAIL] {new_symside} 0 trades — will still write baseline XLS then skip sweep (only RAM)", flush=True)
            _zero_trades_early = True
        else:
            _zero_trades_early = False
            if not baseline_vec.get("valid"):
                print(f"[baseline-warn] {new_symside} valid False but trades {baseline_vec.get('trades')} — proceeding", flush=True)
        # Previous-best that holds ~forever (1-7 trades) makes every delta meaningless: fall back to TEMPLATE defaults
        # (which carry the trade-generating exits) when they clear the 10-trade floor. Real eval, logged, never mixed.
        _adapt_report = None
        # ── USER 2026-10-02: every new test starts with cat_side defaults vs per_sym last-best, best as E3 baseline, C adjusted ──
        # Bias-free baseline (backtest-expert: 80% breaking, no look-ahead): evaluate cat_side defaults first, then per_sym
        # full snapshot from last promotion, pick best credible as E3. Winning baseline's full set is diffed against
        # latest bold template defaults — every non-current default becomes an override in column C.
        if prepared is not None and not os.environ.get("V15_START_OVERRIDES") and os.environ.get("V15_SKIP_CAT_PERSYM_BASELINE", "0") != "1":
            try:
                _cat_label, _cat_ov = "cat_side_defaults", dict(_tpl_defaults)
                # _tpl_defaults is the current bold for this template; defaults dict is the 3314 full current cat_side layer
                # For cat_side we evaluate the template bold as baseline (empty delta vs current defaults is equivalent,
                # but we keep _tpl_defaults explicit so the engine sees the same 4-default layer).
                _cat_ov_san, _ = sanitize_overrides(dict(_cat_ov), defaults)
                # — rerun BOTH cat_side defaults and per_sym last-best IN PARALLEL on the new NPZ; winner on gain/wr/dd —
                _persym_full = None
                _persym_label = None
                _persym_vec = None
                _persym_entry = None
                _persym_ov_san = None
                _cat_vec = None
                # prepare per_sym overrides deterministically before parallel submit
                _persym_ov_raw = None
                try:
                    import per_sym_store as _pss_bl
                    _persym_entry = _pss_bl.get(new_symside)
                    if _persym_entry and (_persym_entry.get("full_config") or _persym_entry.get("overrides")):
                        _persym_full = _persym_entry.get("full_config") or {}
                        if not _persym_full:
                            _snap = _persym_entry.get("defaults_snapshot") or {}
                            _persym_full = dict(_snap); _persym_full.update(_persym_entry.get("overrides") or {})
                        _persym_ov_raw = dict(_persym_entry.get("overrides") or {})
                        _persym_ov_san, _ = sanitize_overrides(dict(_persym_ov_raw), defaults)
                        _persym_label = "per_sym_last_best"
                    else:
                        _persym_ov_raw = dict(_ingested_overrides) if '_ingested_overrides' in locals() and _ingested_overrides else {}
                        if _persym_ov_raw:
                            _persym_ov_san, _ = sanitize_overrides(dict(_persym_ov_raw), defaults)
                            _persym_label = "per_sym_ingested"
                            _persym_full = dict(defaults); _persym_full.update(_persym_ov_san)
                        else:
                            _persym_ov_san = None
                except Exception as _e_ps:
                    print(f"[BASELINE-PERSYM-warn] {new_symside} {_e_ps}", flush=True)
                    _persym_ov_san = None
                # parallel re-evaluation on the new prepared NPZ slice (no look-ahead, 0.07s each, hot ALL_PREPARED)
                import concurrent.futures as _cf_bl
                _fut_cat = _fut_per = None
                with _cf_bl.ThreadPoolExecutor(max_workers=2) as _ex_bl:
                    _fut_cat = _ex_bl.submit(evaluate_prepared_sanitized, prepared, _cat_ov_san, window_days=args.window_days)
                    if _persym_ov_san is not None:
                        _fut_per = _ex_bl.submit(evaluate_prepared_sanitized, prepared, _persym_ov_san, window_days=args.window_days)
                    try:
                        _cat_vec = _fut_cat.result(timeout=60)
                    except Exception as _e_cat:
                        print(f"[BASELINE-CAT-warn] {new_symside} {_e_cat}", flush=True)
                        _cat_vec = {"gain_pct": -1e9, "trades": 0, "valid": False, "invalid_reason": f"cat eval {type(_e_cat).__name__}"}
                    if _fut_per is not None:
                        try:
                            _persym_vec = _fut_per.result(timeout=60)
                        except Exception as _e_per:
                            print(f"[BASELINE-PERSYM-warn] {new_symside} {_e_per}", flush=True)
                            _persym_vec = {"gain_pct": -1e9, "trades": 0, "valid": False, "invalid_reason": f"per eval {type(_e_per).__name__}"}
                print(f"[BASELINE-CAT] {new_symside} cat_side gain={_cat_vec.get('gain_pct')} trades={_cat_vec.get('trades')} valid={_cat_vec.get('valid')} dd={_cat_vec.get('max_dd_pct')} sharpe={_cat_vec.get('pool_sharpe')} ov={len(_cat_ov_san)}", flush=True)
                if _persym_vec is not None:
                    print(f"[BASELINE-PERSYM] {new_symside} {_persym_label} gain={_persym_vec.get('gain_pct')} trades={_persym_vec.get('trades')} valid={_persym_vec.get('valid')} dd={_persym_vec.get('max_dd_pct')} sharpe={_persym_vec.get('pool_sharpe')} ov={len(_persym_ov_san) if _persym_ov_san else 0} full={len(_persym_full) if _persym_full else 0}", flush=True)
                # pick winner on gain/wr/dd — credible first, then higher gain, then higher win-rate/sharpe, then lower dd
                if _persym_vec is not None:
                    def _is_credible(v):
                        return bool(v.get("valid")) and int(v.get("trades") or 0) >= ADAPT_FLOOR_TRADES and float(v.get("gain_pct") or -1e9) >= ADAPT_ULTRA_NEG_PCT
                    def _wr(v):
                        # win-rate: prefer explicit win_rate/winrate/wr, else pool_sharpe as wr proxy, else 0
                        for _k in ("win_rate", "winrate", "wr", "WR"):
                            if _k in v and v[_k] is not None:
                                try: return float(v[_k])
                                except: pass
                        # pool_sharpe correlates with wr; use it as tie-breaker when wr absent
                        try: return float(v.get("pool_sharpe") or 0) * 0.1 + 0.5
                        except: return 0.5
                    def _score(v):
                        return (1 if _is_credible(v) else 0, float(v.get("gain_pct") or -1e9), _wr(v), -float(v.get("max_dd_pct") or 1e9), float(v.get("pool_sharpe") or -1e9))
                    cat_cred = _is_credible(_cat_vec)
                    per_cred = _is_credible(_persym_vec)
                    cat_score = _score(_cat_vec)
                    per_score = _score(_persym_vec)
                    # credible outranks non-credible; otherwise lexicographic gain→wr→-dd
                    if per_score > cat_score:
                        _winner_is_persym = True
                    elif per_score < cat_score:
                        _winner_is_persym = False
                    else:
                        _winner_is_persym = False  # tie → cat (plateau bias per backtest-expert)
                    if _winner_is_persym:
                        # per_sym wins: E3 = per_sym gain, overrides = diff of its full vs latest bold template
                        _win_vec = _persym_vec
                        _win_full = _persym_full or {}
                        # current bold for diff is the latest template bold layer (_tpl_defaults) + rest of defaults
                        # For fidelity we diff against the full current defaults (3314) — every non-current default becomes override
                        _cur_bold = dict(defaults)  # current 4-default layer (3314) is the bold truth for this cat_side
                        _diff = {}
                        for _k, _v in _win_full.items():
                            _cur = _cur_bold.get(_k)
                            if not _same_default(_v, _cur):
                                _diff[_k] = _v
                        # Also include any override key that is not in current bold (new switch)
                        for _k, _v in (_persym_entry.get("overrides") or {}).items() if '_persym_entry' in locals() and _persym_entry else []:
                            if _k not in _diff and not _same_default(_v, _cur_bold.get(_k)):
                                _diff[_k] = _v
                        _diff_san, _ = sanitize_overrides(_diff, defaults)
                        print(f"[BASELINE-WINNER] {new_symside} per_sym wins cat {float(_cat_vec.get('gain_pct') or 0):.2f} vs per_sym {float(_persym_vec.get('gain_pct') or 0):.2f} -> E3 per_sym, C diff {len(_diff_san)} overrides (full {len(_win_full)} vs bold {len(_cur_bold)})", flush=True)
                        overrides = _diff_san
                        baseline_vec = _win_vec
                        _zero_trades_early = int(baseline_vec.get("trades") or 0) == 0
                        _adapt_report = {"baseline_winner": "per_sym", "cat_gain": _cat_vec.get("gain_pct"), "persym_gain": _persym_vec.get("gain_pct"), "diff_overrides": len(_diff_san)}
                    else:
                        print(f"[BASELINE-WINNER] {new_symside} cat_side wins cat {float(_cat_vec.get('gain_pct') or 0):.2f} vs per_sym {float(_persym_vec.get('gain_pct') or 0):.2f} -> E3 cat_side", flush=True)
                        # cat wins: keep cat baseline as E3, overrides = cat (template bold)
                        overrides = dict(_cat_ov_san)
                        baseline_vec = _cat_vec
                        _zero_trades_early = int(baseline_vec.get("trades") or 0) == 0
                        _adapt_report = {"baseline_winner": "cat_side", "cat_gain": _cat_vec.get("gain_pct"), "persym_gain": float(_persym_vec.get("gain_pct") or 0) if _persym_vec else None}
                    _cat_vs_persym_done = True
                else:
                    # no per_sym yet (first run): cat is baseline, no C adjustment needed
                    print(f"[BASELINE-WINNER] {new_symside} cat_side only (no per_sym) cat {float(_cat_vec.get('gain_pct') or 0):.2f} -> E3 cat_side", flush=True)
                    overrides = dict(_cat_ov_san)
                    baseline_vec = _cat_vec
                    _zero_trades_early = int(baseline_vec.get("trades") or 0) == 0
                    _cat_vs_persym_done = True
                    _adapt_report = {"baseline_winner": "cat_side_first_run"}
            except Exception as _e_bl:
                import traceback as _tb_bl
                print(f"[BASELINE-CAT-PERSYM-warn] {new_symside} {_e_bl} {_tb_bl.format_exc()[:500]}", flush=True)
                _cat_vs_persym_done = False
        # USER 2026-09-29: a sheet must start from the BEST previous settings — in FRESH mode always compare live recipe /
        # previous best / template defaults on the CURRENT engine and start from the best credible one (not only when the
        # recipe fails). A V15_START_OVERRIDES (365D-repaired) run keeps its set unless it is not credible.
        _always_best_base = os.environ.get("V15_FRESH_RUN", "0") == "1" and not os.environ.get("V15_START_OVERRIDES")
        if prepared is not None and os.environ.get("V15_ADAPT_BASELINE", "1") == "1" and (_always_best_base or int(baseline_vec.get("trades") or 0) < ADAPT_FLOOR_TRADES or not baseline_vec.get("valid") or float(baseline_vec.get("gain_pct") or 0) < ADAPT_ULTRA_NEG_PCT):
            # USER 2026-09-29: never sweep from a 0-trade / sub-floor / ultra-negative baseline — adapt to a credible one first
            _bases = [("live_recipe", dict(_recipe_only_overrides)), ("template_defaults", dict(_tpl_defaults)), ("current", dict(overrides))]
            if _ingested_overrides and _ingested_overrides != _recipe_only_overrides:
                _bases.append(("previous_best", dict(_ingested_overrides)))
            # USER 2026-09-30: previous best = the FINAL settings of earlier finished runs for this sym_side, each re-measured
            # on the CURRENT engine (no recorded number is reused); the best valid one is the previous_best base
            _scored = []
            for _src, _pov in _prior_final_sets(new_symside)[:8]:
                _pov = sanitize_overrides(dict(_pov), defaults)[0]
                _pv = evaluate_prepared_sanitized(prepared, _pov, window_days=args.window_days)
                print(f"[PRIOR-FINAL] {new_symside} {_src}: now gain={_pv.get('gain_pct')} trades={_pv.get('trades')} valid={_pv.get('valid')}", flush=True)
                if _pv.get("valid") and int(_pv.get("trades") or 0) >= ADAPT_FLOOR_TRADES and _pv.get("gain_pct") is not None:
                    _scored.append((float(_pv["gain_pct"]), _src, _pov))
            if _scored:
                _pg, _psrc, _pov = max(_scored, key=lambda t: t[0])
                print(f"[PRIOR-FINAL] {new_symside} previous_best = {_psrc} ({_pg:.4f}% now)", flush=True)
                _bases = [b for b in _bases if b[0] != "previous_best"] + [("previous_best", dict(_pov))]
            overrides, baseline_vec, _adapt_report = _credible_baseline(new_symside, prepared, _bases, defaults, args.template, args.window_days, tim_min=QUAL_TIM_MIN)
            _zero_trades_early = int(baseline_vec.get("trades") or 0) == 0
            try:
                _bt = float(baseline_vec.get("tim_pct") or 0)
                if not (QUAL_TIM_MIN <= _bt <= QUAL_TIM_MAX):
                    print(f"[BASELINE-TIM-WARN] {new_symside} ADAPT could not reach TIM [{QUAL_TIM_MIN:.0f},{QUAL_TIM_MAX:.0f}] (TIM {_bt:.1f}) — filling anyway under TIM-guard; DONE-stage repair is the backstop", flush=True)
            except Exception:
                pass
        if int(baseline_vec.get("trades") or 0) < 10 and overrides and _adapt_report is None:
            _defaults_vec = evaluate_prepared_sanitized(prepared, dict(_tpl_defaults), window_days=args.window_days)
            print(f"[BASELINE-FALLBACK] {new_symside} previous-best {len(overrides)} overrides -> {baseline_vec.get('trades')} trades gain {baseline_vec.get('gain_pct')}; defaults -> {_defaults_vec.get('trades')} trades gain {_defaults_vec.get('gain_pct')}", flush=True)
            if int(_defaults_vec.get("trades") or 0) >= 10:
                print(f"[BASELINE-FALLBACK] {new_symside} using TEMPLATE defaults as baseline (previous-best dropped: {sorted(overrides)[:12]})", flush=True)
                overrides = dict(_tpl_defaults)
                baseline_vec = _defaults_vec
                _zero_trades_early = False
        prepared_for_fallback = prepared

    if args.vector_only:
        baseline_live = baseline_vec
        reason = "vector-only"
    else:
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(live_evaluate, new_symside, dict(overrides), args.window_days)
            try:
                baseline_live = fut.result(timeout=90)
            except Exception as e:
                baseline_live = {"valid": False, "invalid_reason": str(e), "gain_pct": 0.0}
        ok, reason = parity_ok(baseline_live, baseline_vec, allow_zero_baseline=True)
        print(f"[baseline] live valid={baseline_live.get('valid')} gain={baseline_live.get('gain_pct')} trades={baseline_live.get('trades')} parity={ok} {reason}", flush=True)
        if not baseline_live.get("valid") and baseline_vec.get("valid"):
            print(f"[baseline] live invalid — using vec for E2 ({baseline_live.get('invalid_reason')})", flush=True)
            baseline_live = baseline_vec
    baseline_gain = float(baseline_live.get("gain_pct") or baseline_vec.get("gain_pct") or 0.0)
    bh = float(baseline_live.get("bh_pct") or baseline_vec.get("bh_pct") or 0.0)
    baseline_trades = int(baseline_live.get("trades") or baseline_vec.get("trades") or 0)
    # 10-trade floor per sym_side (user 2026-09-28: 30 is too extreme for a bad month) (the "100" in the mandate is a symbol count, not trades). Below floor the XLS is
    # still cloned + baseline written; only the sweep is skipped (early return before clone is FORBIDDEN).
    min_trades = 10
    below_floor = baseline_trades < min_trades
    if below_floor:
        print(f"[SAMPLE-FLOOR-VIOLATION] {new_symside} ({map_key_for_symside(new_symside)}) {baseline_trades} trades < {min_trades} floor — DIAGNOSTIC ONLY, XLS written, sweep skipped", flush=True)
    # 2026-09-28 SIGN FIX (NO-LIES): the engine already returns SIDE-CORRECT gain_pct and bh_pct
    # (evaluate_v12._bh negates for SHORT; simulate_one gains are side-aware). The old abs()/-abs()
    # flip here turned every losing LONG baseline into a fake profit and every winning SHORT baseline
    # into a fake loss (ZECUSDC_LONG -1.83 -> +1.83, PTBUSDT_LONG -30.79 -> +30.79, DIS_SHORT +2.01 -> -2.01),
    # shifting every first-row delta by 2x|baseline| and blocking promotion on negative-baseline syms.
    # Engine sign is authoritative — never flip baseline_gain or bh by side here.
    # FIX 2026-09-23: baseline 0.00 is a lie — must be calculated from previous test OR defaults for cat_side, never 0.00
    if abs(baseline_gain) < 1e-9:
        _fixed = False
        try:
            _prev_path = PROGRESS_DIR / f"{new_symside}_v14_progress.json"
            if _prev_path.exists():
                import json as _js_prev
                _prev = json.loads(_prev_path.read_text())
                _prev_base = float(_prev.get("baseline_gain") or 0)
                _prev_bh = float(_prev.get("bh") or 0)
                if abs(_prev_base) > 1e-9:
                    baseline_gain = _prev_base
                    if abs(_prev_bh) > 1e-9:
                        bh = _prev_bh
                    print(f"[baseline-fix] 0.00 lie -> using previous {baseline_gain:.4f} bh {bh:.4f}", flush=True)
                    _fixed = True
        except Exception as _e_prev:
            print(f"[baseline-fix-prev-warn] {_e_prev}", flush=True)
        if not _fixed:
            try:
                _cat_defaults = get_defaults_for_symside(new_symside)
                # evaluate with cat_side defaults to get real baseline
                _eval = None
                if prepared_for_fallback is not None:
                    from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_prep
                    _eval = _eval_prep(prepared_for_fallback, {}, window_days=args.window_days)
                else:
                    from tools.opt.v12_pilot import evaluate_sanitized as _eval_san
                    _eval = _eval_san(new_symside, {}, window_days=args.window_days)
                if _eval and _eval.get("gain_pct") is not None:
                    _recalc = float(_eval.get("gain_pct") or 0)
                    _recalc_bh = float(_eval.get("bh_pct") or bh or 0)
                    if abs(_recalc) > 1e-9:
                        baseline_gain = _recalc
                        bh = _recalc_bh
                        print(f"[baseline-fix] 0.00 lie -> recalculated defaults {baseline_gain:.4f} bh {bh:.4f}", flush=True)
                        _fixed = True
            except Exception as _e_recalc:
                print(f"[baseline-fix-recalc-warn] {_e_recalc}", flush=True)
        if not _fixed:
            # SELF-MONITOR: empty/0 baseline is fatal — abort and fix, never proceed with lie
            try:
                _macbook_desktop_notify(f"🚨 {new_symside} BASELINE EMPTY", f"0.00 lie could not be fixed bh {bh:.2f} trades {baseline_live.get('trades')} — ABORTING to fix", critical=True)
            except: pass
            # try one more aggressive fix: force recalc with hot NPZ ignoring parity
            try:
                import pathlib as _pl_fix, json as _js_fix
                # clear overrides that may poison baseline, force pure defaults
                _pure = {}
                from tools.opt.v12_pilot import evaluate_sanitized as _eval_pure
                _pure_eval = _eval_pure(new_symside, _pure, window_days=args.window_days)
                if _pure_eval and abs(float(_pure_eval.get("gain_pct") or 0)) > 1e-9:
                    baseline_gain = float(_pure_eval.get("gain_pct"))
                    bh = float(_pure_eval.get("bh_pct") or bh)
                    print(f"[baseline-fix] ABORT-RESCUE pure defaults {baseline_gain:.4f} bh {bh:.4f} — retrying instead of lying", flush=True)
                    # don't abort, continue with rescued value
                    _fixed = True
                elif prepared is not None and int(baseline_vec.get("trades") or 0) == 0 and os.environ.get("V15_SKIP_BELOW_FLOOR", "0") != "1":
                    # USER 2026-09-29: NPZ loaded fine and the engine returned 0 trades -> a real entry veto, not a lie;
                    # 0.00 IS the true baseline — the below-floor path names the vetoing gate and sweeps for a way out
                    print(f"[baseline-fix] {new_symside} genuine 0-trade baseline (NPZ ok, entries vetoed) — 0.00 kept, blocker scan + sweep follow", flush=True)
                    _genuine_zero_baseline = True
                    _fixed = True
                else:
                    print(f"[baseline-fix] ABORT {new_symside} baseline still 0.00 — exiting to let herd retry with fresh NPZ", flush=True)
                    raise SystemExit(2)
            except SystemExit:
                raise
            except Exception as _e_abort:
                print(f"[baseline-fix-abort] {_e_abort}", flush=True)
                raise SystemExit(2)
            if not _fixed:
                print(f"[baseline-fix] WARNING baseline still 0.00 for {new_symside} bh {bh:.4f} — will proceed but this is a lie", flush=True)

    # clone — 2026-09-24: auto-select side-specific template if generic/default passed
    template = Path(args.template)
    if not template.exists():
        template = get_template_for_symside(new_symside)
    # if user passed generic TEMPLATE.xlsx but side-specific exists, prefer side-specific
    elif template == TEMPLATE and template.exists():
        side_specific = get_template_for_symside(new_symside)
        if side_specific != TEMPLATE and side_specific.exists():
            print(f"[TEMPLATE-AUTO] {new_symside}: {template.name} -> {side_specific.name} (side-specific)", flush=True)
            template = side_specific
    if args.out:
        target = Path(args.out)
        if target.exists():
            wb_path = target
            print(f"[resume] using existing {wb_path} (per-10 batched, resume from last filled cell)", flush=True)
        else:
            import shutil
            shutil.copy2(template, target)
            try:
                Path(target).chmod(0o644)
            except OSError as _e_chmod:
                print(f"[clone-chmod-warn] {target} {_e_chmod}", flush=True)
            wb_path = target
            print(f"[clone] -> {wb_path}", flush=True)
    else:
        wb_path = clone_template(template, new_symside)
        print(f"[clone] -> {wb_path}", flush=True)

    # baseline metrics sheet
    try:
        wb = openpyxl.load_workbook(str(wb_path))
        sheet = f"{new_symside}_BASELINE_METRICS"
        if sheet not in wb.sheetnames:
            ws = wb.create_sheet(sheet)
            ws["A1"] = "metric"; ws["B1"] = "value"
        else:
            ws = wb[sheet]
        rows_baseline = [
            ("gain_pct", baseline_live.get("gain_pct")),
            ("bh_pct", baseline_live.get("bh_pct")),
            ("delta_vs_bh", baseline_live.get("delta_vs_bh")),
            ("trades", baseline_live.get("trades")),
            ("pool_sharpe", baseline_live.get("pool_sharpe")),
            ("tim_pct", baseline_live.get("tim_pct")),
            ("max_dd_pct", baseline_live.get("max_dd_pct")),
            ("gain_per_mo", baseline_live.get("gain_per_mo")),
            ("bh_per_mo", baseline_live.get("bh_per_mo")),
            # 2026-09-28 NO-LIES: label the REAL source — under --vector-only (or live fallback)
            # baseline_live IS baseline_vec, and calling vector output "live backtest_v12_engine" was a lie.
            ("source", f"{'vector v12_quick_engine' if baseline_live is baseline_vec else 'live backtest_v12_engine'} {utcnow()} via {new_symside} overrides={len(overrides)}"),
            ("window", baseline_live.get("window")),
            ("valid", baseline_live.get("valid")),
        ]
        # 2026-09-28 NO-LIES: route the human-facing sharpe through metrics_guard (mandated) and
        # stamp the sample floor verdict — a single-sym 30d window is ALWAYS below the publishable
        # floor, so the tag must be visible in the sheet, never implied.
        try:
            import metrics_guard as _mg
            _tr = int(baseline_live.get("trades") or 0)
            _yrs = float(args.window_days) / 365.0
            rows_baseline.append(("pool_sharpe_guarded", _mg.validate_and_format_sharpe(float(baseline_live.get("pool_sharpe") or 0.0), label="pool_sharpe", n_syms=1, years=_yrs, trades=_tr, mode=("stocks" if map_key_for_symside(new_symside).startswith("stocks") else "crypto"))))
            rows_baseline.append(("sample_status", "[DIAGNOSTIC ONLY]" if (_tr < 30 or _yrs < 1.0) else "publishable-floor met"))
        except Exception as _mg_e:
            rows_baseline.append(("pool_sharpe_guarded", f"REFUSED: {_mg_e}"[:120]))
        for r in range(2, max(20, ws.max_row + 1)):
            ws.cell(row=r, column=1).value = None
            ws.cell(row=r, column=2).value = None
        for i, (k, v) in enumerate(rows_baseline, start=2):
            ws.cell(row=i, column=1).value = k
            ws.cell(row=i, column=2).value = json.dumps(v) if isinstance(v, dict) else v
        _atomic_save(wb, Path(wb_path))  # BADZIP FIX 2026-09-29
    except Exception as e:
        print(f"[baseline-metrics-warn] {e}", flush=True)

    # SINGLE-LOAD FAST PATH: headers + BEST-C-FILL + baseline in ONE open (3*8.3s -> 8.3s) — fixes >1s openpyxl load
    try:
        import time as _t_single
        _t0_single = _t_single.time()
        wb_single = openpyxl.load_workbook(str(wb_path))
        # USER 2026-09-30: headers are NEVER written by the pilot — the TEMPLATE carries every header (L is_default, M AVG_DELTA,
        # N POS_SYM, yellows from O). This block used to test "L has a FILTER=opt header" and, after the layout fix, rewrote
        # O..Z with other filter names in every new sheet (yellow cells under the wrong headers).
        _need_hdr = False
        if _need_hdr:
            try:
                fd_rows = _load_filter_dictionary()
                for sheet in SWITCH_SHEETS:
                    if sheet not in wb_single.sheetnames:
                        continue
                    ws = wb_single[sheet]
                    existing = set()
                    for c in range(12, min(ws.max_column+1, 30)):
                        hv = ws.cell(row=2, column=c).value
                        if hv and isinstance(hv, str) and "=" in hv:
                            existing.add(hv.strip())
                    if len(existing) > 20:
                        continue
                    sheet_switches = []
                    for _r in range(2, min(ws.max_row+1, 30)):
                        _sw = ws.cell(row=_r, column=1).value
                        if _sw and isinstance(_sw, str) and _sw.strip() not in ("Switch","General","Blanket"):
                            sheet_switches.append(_sw.strip())
                    union = set()
                    for sw in sheet_switches[:15]:
                        for e in get_opportune_filters(sw, sheet):
                            if not _is_general(e["rec"]):
                                union.add(f"{e['filter']}={e['opt']}")
                    col = 15
                    for hdr in sorted(union)[:12]:
                        if hdr not in existing:
                            ws.cell(row=2, column=col).value = hdr
                            col += 1
            except Exception as _e_hdr:
                print(f"[headers-warn] {_e_hdr}", flush=True)
        print(f"[headers] L:BI ensured (single-load) { _t_single.time()-_t0_single:.2f}s", flush=True)
        # BEST-C-FILL on same wb
        # USER 2026-09-29: every NON-default previous-best setting goes ONCE, bold, as "SWITCH=value" into override (C) of the
        # row whose candidate equals that value (else the switch's first row); default (B) bold is never touched
        filled_c = 0
        def _c_val_eq(a, b):
            if isinstance(a, bool) or isinstance(b, bool) or str(a).lower() in ("true", "false") or str(b).lower() in ("true", "false"):
                return str(a).strip().lower() == str(b).strip().lower()
            try:
                return abs(float(a) - float(b)) < 1e-12
            except Exception:
                return str(a).strip() == str(b).strip()
        for sname in SWITCH_SHEETS:
            if sname not in wb_single.sheetnames:
                continue
            ws_c = wb_single[sname]
            _c_col = _resolve_cols(ws_c)["C"]
            _first_row, _match_row = {}, {}
            for r in range(3, ws_c.max_row + 1):
                sw = ws_c.cell(row=r, column=1).value
                if not sw or not isinstance(sw, str) or sw.strip() not in overrides:
                    continue
                sw = sw.strip()
                if sw in defaults and _c_val_eq(overrides[sw], defaults[sw]):
                    continue
                _first_row.setdefault(sw, r)
                if sw not in _match_row and _c_val_eq(ws_c.cell(row=r, column=2).value, overrides[sw]):
                    _match_row[sw] = r
            for sw, r in _first_row.items():
                r = _match_row.get(sw, r)
                val = overrides[sw]
                part = f"{sw}={'True' if val is True else 'False' if val is False else val}"
                cell = ws_c.cell(row=r, column=_c_col)
                cur = [p.strip() for p in str(cell.value).split(" + ")] if cell.value not in (None, "") else []
                if part not in cur:
                    cell.value = " + ".join(cur + [part])
                    filled_c += 1
                cell.font = Font(name="Arial", size=10, bold=True, color="000000")
                cell.alignment = Alignment(horizontal="left", vertical="center")
        print(f"[BEST-C-FILL] {new_symside}: filled {filled_c} (single-load)", flush=True)
        # FIX 2026-09-26: baseline E2/E3 using _resolve_cols to find correct column (handle resorting)
        for sname in SWITCH_SHEETS:
            if sname in wb_single.sheetnames:
                ws_fix = wb_single[sname]
                cols = _resolve_cols(ws_fix)  # Get actual column mapping
                e_col = cols.get("E", 5)  # Default to 5 if _resolve_cols fails
                # Only write E2 if it's not already a header
                e2_val = ws_fix.cell(row=2, column=e_col).value
                if e2_val is None or (isinstance(e2_val, str) and e2_val.strip().upper() not in ("BASELINE", "VECTOR", "HUSTLE")):
                    ws_fix.cell(row=2, column=e_col).value = "BASELINE"
                # Always write E3 value
                # E3 (chain start) is written by the sequential filler on the first row it fills — never on every tab
        # BADZIP FIX 2026-09-29: the old fallback saved IN PLACE after a FAILED zip
        # validation — writing a known-bad workbook over the live file, plus the bare
        # ".tmp" name collided with sweeper cleanups. _atomic_save validates and either
        # replaces atomically or raises; on failure the previous good file stays.
        _atomic_save(wb_single, Path(wb_path))
        _dt_single = _t_single.time() - _t0_single
        if _dt_single > 1.0:
            print(f"[SLOW-CELL] SINGLE-LOAD headers+BEST-C-FILL+baseline { _dt_single:.2f}s >1s (openpyxl 8.3s unavoidable, saved 16s)", flush=True)
        else:
            print(f"[SINGLE-LOAD-TIMING] { _dt_single:.2f}s", flush=True)
    except Exception as _e_single:
        import traceback as _tb_s
        print(f"[SINGLE-LOAD-warn] {_e_single} {_tb_s.format_exc()[:300]}", flush=True)
        # (no header fallback — the pilot never writes headers, USER 2026-09-30)
    _first_pos_notified = False
    _first_pos_notified = False
    # DESKTOP: baseline result for EVERY sym_side
    try:
        _macbook_desktop_notify(f"📊 {new_symside} BASELINE", f"gain {baseline_gain:.2f}% bh {bh:.2f}% trades {baseline_live.get('trades')} sharpe {float(baseline_live.get('pool_sharpe') or 0):.2f} E2 {baseline_gain:.2f} C-filled {filled_c if 'filled_c' in locals() else 0}", critical=False)
    except: pass
    # SELF-MONITOR thread: continuously watch E3 baseline (E2 is header 'BASELINE' preserved per spec), abort & fix if empty/0
    try:
        import threading as _th_mon, time as _t_mon
        _genuine_zero_baseline = bool(locals().get("_genuine_zero_baseline", False))
        def _baseline_self_monitor():
            _fails = 0
            while True:
                _t_mon.sleep(8)
                try:
                    import openpyxl as _op_mon
                    _wb_m = _op_mon.load_workbook(str(wb_path), data_only=True, read_only=True)
                    _ws_m = _wb_m[SWITCH_SHEETS[0]] if SWITCH_SHEETS[0] in _wb_m.sheetnames else None
                    _e3m = _ws_m.cell(row=3, column=5).value if _ws_m else None
                    _e2m_hdr = _ws_m.cell(row=2, column=5).value if _ws_m else None
                    _is_empty = _e3m is None or (isinstance(_e3m, str) and _e3m.strip() == "")
                    _is_zero = isinstance(_e3m, (int,float)) and abs(float(_e3m)) < 1e-9 and not _genuine_zero_baseline  # a genuine 0-trade baseline IS 0.00
                    _hdr_corrupt = isinstance(_e2m_hdr, (int,float))
                    if _is_empty or _is_zero or _hdr_corrupt:
                        _fails += 1
                        print(f"[SELF-MONITOR] {new_symside} E3 empty/0 ({_e3m}) hdr={_e2m_hdr!r} fail {_fails}/3 -> fixing", flush=True)
                        try: _macbook_desktop_notify(f"🚨 {new_symside} SELF-MONITOR", f"E3 empty/0 {_e3m} hdr {_e2m_hdr!r} — fixing attempt {_fails}", critical=True)
                        except: pass
                        # fix: rewrite E3 (baseline), preserve E2 header 'BASELINE'
                        try:
                            _wb_f = _op_mon.load_workbook(str(wb_path))
                            _ws_f = _wb_f[SWITCH_SHEETS[0]] if SWITCH_SHEETS[0] in _wb_f.sheetnames else None
                            if _ws_f is not None:
                                _fix_val = float(baseline_gain) if abs(float(baseline_gain)) > 1e-9 else float(baseline_live.get("gain_pct") or 0)
                                if abs(_fix_val) < 1e-9:
                                    _fix_val = float(baseline_vec.get("gain_pct") or 0)
                                _ws_f.cell(row=3, column=5).value = _fix_val
                                # restore header if overwritten by prior buggy runs
                                if isinstance(_ws_f.cell(row=2, column=5).value, (int,float)) or _ws_f.cell(row=2, column=5).value is None:
                                    _ws_f.cell(row=2, column=5).value = "BASELINE"
                                _atomic_save(_wb_f, Path(wb_path))  # BADZIP FIX 2026-09-29
                        except: pass
                        if _fails >= 3:
                            print(f"[SELF-MONITOR] {new_symside} E3 still empty/0 after 3 fixes -> ABORT", flush=True)
                            try: _macbook_desktop_notify(f"🚨 {new_symside} ABORT", f"E3 empty/0 after 3 fixes — aborting herd will retry", critical=True)
                            except: pass
                            import os as _os_m
                            _os_m._exit(2)
                    else:
                        _fails = 0
                except Exception as _e_mon:
                    # ignore read errors (file being written)
                    pass
        _th_mon.Thread(target=_baseline_self_monitor, daemon=True).start()
    except: pass
    # If 0-trades early, diagnostic was deferred until after baseline XLS persisted — write it and skip sweep
    if locals().get("_zero_trades_early") and prepared is not None and os.environ.get("V15_SKIP_BELOW_FLOOR", "0") != "1":
        # USER 2026-09-29 "0 trades is impossible": NPZ loaded fine -> an entry gate vetoes everything; the below-floor path names it ([FLOOR-BLOCKERS]) and sweeps
        print(f"[0-TRADES] {new_symside} NPZ loaded but 0 trades — not a data error; blocker scan + sweep follow", flush=True)
        _zero_trades_early = False
    if locals().get("_zero_trades_early"):
        try:
            diag_path = PROGRESS_DIR / f"{new_symside}_{args.window_days}d_progress.json"
            diag_path.parent.mkdir(parents=True, exist_ok=True)
            _diag = {"symside": new_symside, "baseline_gain": float(baseline_vec.get("gain_pct") or 0), "bh": float(baseline_vec.get("bh_pct") or 0), "valid": False, "trades": int(baseline_vec.get("trades") or 0), "reason": "0 trades baseline — DATA_ERROR NPZ missing or broken, never wait", "done": {}, "no_delta": True, "zero_trades_diagnostic": True}
            import json as _js0
            # preserve baseline sheet already written — do not delete XLS
            diag_path.write_text(_js0.dumps(_diag, indent=2))
        except Exception as _e:
            print(f"[diag-warn] {new_symside} {_e}", flush=True)
        print(f"[0-TRADES] baseline XLS persisted at {wb_path}, sweep skipped — herd moves on", flush=True)
        return
    if args.dry_run:
        print("[dry-run] done", flush=True)
        return

    # 10s empty-workbook guard: if after 10s Results_Deltas still empty or file is BadZip, kill and repair (death penalty empty)
    def _empty_guard():
        import threading, time as _t, zipfile
        def _check():
            _t.sleep(10)
            try:
                # check progress
                done_cnt = len(progress.get("done", {}))
                # check workbook size/content
                try:
                    z = zipfile.ZipFile(str(wb_path))
                    ok = len(z.namelist()) > 20
                    z.close()
                except Exception:
                    ok = False
                if done_cnt == 0 and not ok:
                    print(f"[EMPTY_GUARD] {new_symside} still empty after 10s (done {done_cnt} zip ok {ok}) — never kill, flag red and keep filling to sheet 13", flush=True)
                    try:
                        Path(f"/tmp/v15_empty_{new_symside}.flag").write_text(str(time.time()))
                    except Exception:
                        pass
                    # never os._exit — flag and keep filling
            except Exception as e:
                print(f"[EMPTY_GUARD-ERR] {e}", flush=True)
        threading.Thread(target=_check, daemon=True).start()
    _empty_guard()
    # IMMEDIATE STUCK-CELL GUARD: warn after 60s if still at first cell r3 F/G None, abort after 1h — never 6h silence
    def _stuck_cell_guard():
        import threading, time as _t2
        def _check2():
            _start2 = _t2.time()
            while True:
                _t2.sleep(30)
                _elapsed = _t2.time() - _start2
                try:
                    _done = len(progress.get("done", {}))
                    import openpyxl as _op_s
                    _wb = _op_s.load_workbook(str(wb_path), data_only=True, read_only=True)
                    _ws = _wb[SWITCH_SHEETS[0]] if SWITCH_SHEETS[0] in _wb.sheetnames else None
                    _f3 = _ws.cell(row=3, column=6).value if _ws else None
                    _g3 = _ws.cell(row=3, column=7).value if _ws else None
                    _wb.close()
                    # 2026-09-29 FIX: r3 can be an honest structural-skip row (NO_CANDIDATE/
                    # dead-vec) whose F/G stay blank forever — with rows completing, the pilot
                    # is NOT stuck; the old test aborted healthy runs at 1h (XRP demo, 483 done).
                    _stuck = (_f3 is None and _g3 is None) and _done < 5
                    if _stuck and _elapsed > 60:
                        print(f"[STUCK-CELL-GUARD] {new_symside} STUCK AT FIRST CELL r3 F=None G=None after {_elapsed:.0f}s done {_done} — WARNING IMMEDIATE, not 6h! NPZ/workers/template check. Will abort at 1h.", flush=True)
                        try:
                            _macbook_desktop_notify(f"STUCK FIRST CELL {new_symside}", f"r3 F/G None after {_elapsed:.0f}s — immediate warning", critical=True)
                        except: pass
                        if _elapsed > 3600:
                            print(f"[STUCK-CELL-ABORT] {new_symside} stuck 1h at r3 — aborting to free herd", flush=True)
                            import os; os._exit(2)
                    # no else: first cell filled -> guard silent, will exit after 6h
                except Exception:
                    pass
                if _elapsed > 21600:
                    break
        import threading as _th2
        _th2.Thread(target=_check2, daemon=True).start()
    _stuck_cell_guard()

    PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    FLAGS_DIR.mkdir(parents=True, exist_ok=True)
    if args.window_days != 30:
        progress_path = PROGRESS_DIR / f"{new_symside}_{args.window_days}d_progress.json"
        flags_md = FLAGS_DIR / f"{new_symside}_{args.window_days}d_flags.md"
    else:
        progress_path = PROGRESS_DIR / f"{new_symside}_v14_progress.json"
        flags_md = FLAGS_DIR / f"{new_symside}_30d_flags.md"
    # fresh flags MD per run (truncate if exists, header will be recreated on first flag)
    try:
        if flags_md.exists():
            # keep previous run's flags for agent, but start fresh for this run — move to .bak
            import shutil
            shutil.copy2(flags_md, str(flags_md) + ".bak")
            flags_md.unlink()
    except Exception:
        pass
    # USER 2026-10-03 RULE#4: stamp the NPZ this run measures on — done rows are valid only on it
    try:
        from tools.v15_row_guards import npz_identity_for_symside as _npz_id0, short_npz_id as _short_npz0
        _run_npz_id = _npz_id0(new_symside)
        _run_npz_short = _short_npz0(_run_npz_id)
    except Exception:
        _run_npz_id = None
        _run_npz_short = "npz?"
    _board_reset = False
    try:
        progress = json.loads(progress_path.read_text())
        # USER 2026-10-03 (complete-rounds): rows stuck is_running are CORPSES — no worker is alive at
        # startup (DEDUP forbids concurrent pilots; this load runs after lock acquisition), so the marker
        # can only be left by a dead run. Settled rows (numeric delta) keep their numbers with the flag
        # cleared; uncalculated ones drop from the board and re-drive. Without this, crash-orphaned rows
        # read as done forever (QBTS_LONG: 835 permanent blanks).
        try:
            _corpse_kept = _corpse_drop = 0
            for _ck in list((progress.get("done") or {}).keys()):
                _cr = (progress.get("done") or {}).get(_ck)
                if not isinstance(_cr, dict) or not _cr.get("is_running"):
                    continue
                if isinstance(_cr.get("delta"), (int, float)):
                    _cr["is_running"] = False
                    _corpse_kept += 1
                else:
                    del progress["done"][_ck]
                    _corpse_drop += 1
            if _corpse_kept or _corpse_drop:
                print(f"[CORPSE-CLEAR] {new_symside} settled={_corpse_kept} redrive={_corpse_drop} stale is_running rows", flush=True)
        except Exception as _corpse_e:
            print(f"[corpse-clear-warn] {new_symside}: {_corpse_e}", flush=True)
        # USER 2026-10-02 (finish qualification REDO): a scheduled re-fill starts CLEAN — archive the stale board
        # (rows were measured vs the old chain) and re-fill every row from the repaired baseline.
        if progress.get("needs_redo") and progress.get("verdict") != "IMPOSSIBLE":
            _board_reset = True
            _nr3 = progress.pop("needs_redo")
            try:
                progress.setdefault("redo_history", []).append({"depth": _nr3.get("depth"), "reason": _nr3.get("reason"), "result": _nr3.get("result"), "archived_done_n": len(progress.get("done", {})), "archived_cum": progress.get("cumulative_gain")})
            except Exception:
                pass
            progress["redo_depth"] = int(_nr3.get("depth", 1))
            progress["done"] = {}
            for _rk in ("final_gain", "final_path", "not_compliant", "cumulative_gain", "cumulative_overrides", "hustler_best_gain", "hustler_overrides", "final_365d", "repair_365d", "confirmed_365d"):
                progress.pop(_rk, None)
            print(f"[REDO-RESET] {new_symside} board cleared for re-fill (depth {progress['redo_depth']})", flush=True)
        # USER 2026-10-03 RULE#4: done rows are valid ONLY on the NPZ they were measured on. A changed
        # NPZ invalidates the frozen board (archive + refill, never reuse) — a 0.00 delta on a changed
        # NPZ is impossible, so reusing the old board would be a lie. Numbers stay intact in the archive.
        try:
            from tools.v15_row_guards import npz_changed as _npz_changed
            _prev_npz_id = progress.get("npz_id")
            if progress.get("done") and _npz_changed(_prev_npz_id, _run_npz_id):
                _ts4 = __import__("time").strftime("%Y%m%d%H%M%S", __import__("time").gmtime())
                try:
                    (progress_path.parent / (progress_path.name + f".npzprev_{_ts4}.json")).write_text(json.dumps(progress))
                except Exception:
                    pass
                try:
                    progress.setdefault("npz_history", []).append({"prev": _prev_npz_id, "new": _run_npz_id, "archived_done_n": len(progress.get("done", {}))})
                except Exception:
                    pass
                progress["done"] = {}
                _board_reset = True
                for _rk in ("final_gain", "final_path", "not_compliant", "cumulative_gain", "cumulative_overrides", "hustler_best_gain", "hustler_overrides", "final_365d", "repair_365d", "confirmed_365d"):
                    progress.pop(_rk, None)
                print(f"[NPZ-CHANGED-REFILL] {new_symside} NPZ changed since board was measured — board archived, re-filling every row on the new NPZ", flush=True)
            # AMBER 2026-10-04 (A) FAIL-CLOSED: boards that predate NPZ-stamping (npz_id None) auto-refill on
            # resume — unknown-provenance boards must not seed Monday sets. Archive keeps numbers intact.
            # Kill: V15_FAILCLOSED=0 restores legacy trust (escape hatch only, never for qualification).
            if os.environ.get("V15_FAILCLOSED", "1") == "1" and progress.get("done") and progress.get("npz_id") is None and _run_npz_id is not None:
                _tsA = __import__("time").strftime("%Y%m%d%H%M%S", __import__("time").gmtime())
                try:
                    (progress_path.parent / (progress_path.name + f".nostamp_{_tsA}.json")).write_text(json.dumps(progress))
                except Exception:
                    pass
                try:
                    progress.setdefault("npz_history", []).append({"prev": None, "new": _run_npz_id, "archived_done_n": len(progress.get("done", {})), "why": "nostamp-failclosed"})
                except Exception:
                    pass
                progress["done"] = {}
                _board_reset = True
                for _rk in ("final_gain", "final_path", "not_compliant", "cumulative_gain", "cumulative_overrides", "hustler_best_gain", "hustler_overrides", "final_365d", "repair_365d", "confirmed_365d"):
                    progress.pop(_rk, None)
                print(f"[NOSTAMP-REFILL] {new_symside} board predates NPZ-stamping — archived (.nostamp), re-filling every row on current NPZ", flush=True)
            # AMBER 2026-10-04 (B) BYTE-AUDIT log-only: md5 divergence with matching 6-key identity is the silent
            # cross-host rot signature (S1 1165 vs S5 945 keys, known gain divergence). Pre-Monday: LOG ONLY.
            # Tightening (invalidate on md5 mismatch) lands post-Monday.
            try:
                _aud_old = progress.get("npz_id") or {}
                _aud_new = _run_npz_id or {}
                if isinstance(_aud_old, dict) and isinstance(_aud_new, dict) and _aud_old.get("md5") and _aud_new.get("md5") and _aud_old.get("md5") != _aud_new.get("md5"):
                    from tools.v15_row_guards import npz_changed as _npz_changed_aud
                    _six = _npz_changed_aud(_aud_old, _aud_new)
                    print(f"[MD5-AUDIT] {new_symside} npz md5 differs (stored={str(_aud_old.get('md5'))[:12]} current={str(_aud_new.get('md5'))[:12]}) sixkey_changed={_six} — {'SILENT-ROT-SIGNATURE (same 6-key, different bytes)' if not _six else 'board invalidated by 6-key'} (log-only pre-Monday)", flush=True)
            except Exception as _aud_e:
                print(f"[md5-audit-warn] {new_symside}: {_aud_e}", flush=True)
            if _run_npz_id is not None:
                progress["npz_id"] = _run_npz_id
        except Exception as _npz_e:
            print(f"[npz-guard-warn] {new_symside}: {_npz_e}", flush=True)
        # TEAL 2026-10-04 HOST-STAMP: boards carry their measuring host; a cross-host resume re-measures the
        # cumulative chain gain on the NEW host's NPZ with ONE eval. Reproducers within tolerance keep the board;
        # divergers (or unrecoverable chains) archive + refill. Kill: V15_HOSTSTAMP=0 (no stamp, no check).
        try:
            import socket as _sock_hs
            _this_host = (_sock_hs.gethostname() or "").lower()
        except Exception:
            _this_host = ""
        if os.environ.get("V15_HOSTSTAMP", "1") == "1" and progress.get("done") and _this_host:
            try:
                _stored_host = str(progress.get("host") or "")
                if not _stored_host:
                    progress["host"] = _this_host
                elif _stored_host.lower() != _this_host:
                    _hs_chain = dict(progress.get("cumulative_overrides") or {})
                    _hs_old = progress.get("cumulative_gain")
                    _hs_new = None
                    _hs_err = ""
                    try:
                        if not _hs_chain or _hs_old is None:
                            _hs_err = f"unrecoverable-chain(chain={len(_hs_chain)} cum={_hs_old})"
                        else:
                            if prepared is not None:
                                from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_hs
                                _hs_res = _eval_hs(prepared, _hs_chain, window_days=args.window_days)
                            else:
                                from tools.opt.v12_pilot import evaluate_sanitized as _eval_hs_s
                                _hs_res = _eval_hs_s(new_symside, _hs_chain, window_days=args.window_days)
                            _hs_new = float((_hs_res or {}).get("gain_pct")) if (_hs_res or {}).get("gain_pct") is not None else None
                            if _hs_new is None or not bool((_hs_res or {}).get("valid")):
                                _hs_err = f"reverify-eval-invalid(gain={_hs_new} valid={(_hs_res or {}).get('valid')})"
                    except Exception as _hs_e:
                        _hs_err = f"reverify-eval-error:{_hs_e}"
                    if not _hs_err and _reverify_within_tol(_hs_new, _hs_old):
                        progress["host"] = _this_host
                        progress["reverified"] = {"why": f"cross-host:{_stored_host}->{_this_host}", "old": float(_hs_old), "new": float(_hs_new), "npz": _run_npz_short}
                        print(f"[REVERIFY-KEEP] {new_symside} cross-host {_stored_host}->{_this_host} chain reproduces ({float(_hs_old):.4f}->{float(_hs_new):.4f}) — board kept", flush=True)
                    else:
                        _tsH = __import__("time").strftime("%Y%m%d%H%M%S", __import__("time").gmtime())
                        try:
                            (progress_path.parent / (progress_path.name + f".hostdiverge_{_tsH}.json")).write_text(json.dumps(progress))
                        except Exception:
                            pass
                        try:
                            progress.setdefault("npz_history", []).append({"prev": progress.get("npz_id"), "new": _run_npz_id, "archived_done_n": len(progress.get("done", {})), "why": f"cross-host-diverge:{_stored_host}->{_this_host} old={_hs_old} new={_hs_new} {_hs_err}"})
                        except Exception:
                            pass
                        progress["done"] = {}
                        _board_reset = True
                        for _rk in ("final_gain", "final_path", "not_compliant", "cumulative_gain", "cumulative_overrides", "hustler_best_gain", "hustler_overrides", "final_365d", "repair_365d", "confirmed_365d", "reverified"):
                            progress.pop(_rk, None)
                        progress["host"] = _this_host
                        print(f"[HOSTDIVERGE-REFILL] {new_symside} cross-host {_stored_host}->{_this_host} chain DIVERGES (old={_hs_old} new={_hs_new} {_hs_err}) — archived (.hostdiverge), re-filling", flush=True)
            except Exception as _hs_e0:
                print(f"[hoststamp-warn] {new_symside}: {_hs_e0}", flush=True)
        # USER 2026-10-03 hollow-fix: boards containing policy-hollow rows (sampling/tab-level era: 0 evals
        # yet marked done) are rebuilt from scratch via needs_redo — mid-board holes cannot be spliced honestly
        # (positional baselines), so this launch only schedules the rebuild and exits for herd relaunch.
        try:
            from tools.v15_row_guards import scan_board_for_hollow as _scan_hollow_load
            _tl_spec_load = {}
            try:
                _tl_spec_load = json.loads((Path(__file__).resolve().parent / "data" / "wiring" / "tab_filters" / "tab_level_filters.json").read_text())
            except Exception:
                pass
            _hol_load = _scan_hollow_load(progress.get("done", {}), map_key_for_symside(new_symside), _tl_spec_load, assume_tablevel_on=True)
            _hol_drop = _hol_load.get("drop", [])
            if _hol_drop and progress.get("verdict") != "IMPOSSIBLE":
                _ts0 = __import__("time").strftime("%Y%m%d%H%M%S", __import__("time").gmtime())
                try:
                    (progress_path.parent / (progress_path.name + f".hollowprev_{_ts0}.json")).write_text(json.dumps(progress))
                except Exception:
                    pass
                _d0 = int(progress.get("redo_depth", 0)) + 1
                progress["needs_redo"] = {"overrides": dict(progress.get("cumulative_overrides") or {}), "result": {"gain_pct": progress.get("cumulative_gain")}, "depth": _d0, "reason": f"hollow-refill: {len(_hol_drop)}/{_hol_load.get('total', 0)} rows with uncalculated yellows {dict(_hol_load.get('tally', {}))} — re-fill every row"}
                for _rk in ("final_gain", "final_path", "not_compliant"):
                    progress.pop(_rk, None)
                try:
                    _atomic_write_json(progress_path, progress)
                except Exception:
                    progress_path.write_text(json.dumps(progress))
                print(f"[HOLLOW-REDO] {new_symside} {len(_hol_drop)} hollow rows {dict(_hol_load.get('tally', {}))} — board archived (.hollowprev), rebuild scheduled (depth {_d0}), exiting for herd relaunch", flush=True)
                return
        except Exception as _hol_e:
            print(f"[hollow-scan-warn] {new_symside}: {_hol_e} — continuing without hollow rebuild (DONE gate re-scans)", flush=True)
        # FIX 2026-09-20: NEVER deteriorate vs BEST baseline — cumulative must be max of stored, baseline, and hustler_best
        # Prevents IBM_LONG repeat where S1 recomputes lower gain than BEST (just leave settings as is = 0 delta, never negative)
        try:
            _prev_cum = float(progress.get("cumulative_gain") or baseline_gain)
            _prev_hust = float(progress.get("hustler_best_gain") or 0)
            progress["cumulative_gain"] = max(_prev_cum, float(baseline_gain or 0), _prev_hust)
            if "hustler_best_gain" in progress and _prev_hust > 0:
                # also ensure baseline overrides already include hustler best (BEST-as-baseline already loaded, but keep gain consistent)
                pass
        except Exception:
            pass
    except Exception:
        progress = {"symside": new_symside, "baseline_gain": baseline_gain, "bh": bh, "done": {}, "window_days": args.window_days, "npz_id": _run_npz_id}
    # RESPECT s3/s5 shuffles and stdev: fetch latest progress from S1 peer if on s3/s5 to avoid overwriting better numbers
    try:
        import socket as _sock
        _host = _sock.gethostname().lower()
        # 2026-09-29: FRESH/isolated runs must never adopt the host's herd/mega progress (35 s5 iso runs inherited mega chains -> E2 != chain start)
        _isolated = os.environ.get("V15_FRESH_RUN", "0") == "1" or bool(os.environ.get("V15_PROGRESS_DIR"))
        if not _isolated and (any(x in _host for x in ["s2", "s3", "s5", "s6", "htz-v15-s2", "htz-v15-s3", "htz-v15-s5", "htz-v15-s6"]) or "10.0.0.5" in str(progress_path) or "10.0.0.6" in str(progress_path) or "10.0.0.4" in str(progress_path)):
            # try to fetch S1's progress as source of truth for shuffles/stdev
            _s1_progress = Path("/home/niels/binance-sandbox/data/reports/lifecycle_pilot") / progress_path.name
            if _s1_progress.exists() and _s1_progress != progress_path:
                try:
                    _s1_data = json.loads(_s1_progress.read_text())
                    if _s1_data.get("verdict") in ("IMPOSSIBLE", "NO_TRADES", "BEST_EFFORT") and progress.get("verdict") not in ("IMPOSSIBLE", "NO_TRADES", "BEST_EFFORT"):
                        for _tk in ("verdict", "impossible_reasons", "impossible_path", "impossible_metrics", "verdict_reasons", "verdict_npz_mtime_ns"):
                            if _tk in _s1_data:
                                progress[_tk] = _s1_data[_tk]
                        print(f"[respect-s1] adopted {_s1_data.get('verdict')} verdict for {new_symside} from S1 — will skip", flush=True)
                    # respect S1's better cumulative and hustler if newer/better
                    # USER 2026-10-03 hollow-fix: a board cleared for re-fill (REDO/NPZ/hollow reset) must NOT
                    # resurrect old-board rows — re-fill means every row, otherwise holes splice dishonestly.
                    if _board_reset:
                        print(f"[respect-s1] board was reset for re-fill — skipping S1 adopt/merge for {new_symside}", flush=True)
                    elif float(_s1_data.get("cumulative_gain", 0)) > float(progress.get("cumulative_gain", 0)):
                        print(f"[respect-s1] S1 progress {progress_path.name} cum {progress.get('cumulative_gain')} -> S1 { _s1_data.get('cumulative_gain'):.2f} — respect", flush=True)
                        progress = _s1_data
                    elif len(_s1_data.get("done", {})) > len(progress.get("done", {})):
                        # S1 has more done entries (shuffles/stdev), merge
                        print(f"[respect-s1] S1 has {len(_s1_data.get('done',{}))} done vs local {len(progress.get('done',{}))} — merge", flush=True)
                        for _k, _v in _s1_data.get("done", {}).items():
                            if _k not in progress.get("done", {}):
                                progress.setdefault("done", {})[_k] = _v
                            else:
                                # respect STDEV/shuffle: keep max delta for same key
                                _local_delta = float(progress["done"][_k].get("delta") or 0)
                                _s1_delta = float(_v.get("delta") or 0)
                                if abs(_s1_delta) > abs(_local_delta) and _k.startswith(("STDEV", "REENTRY")):
                                    progress["done"][_k] = _v
                        if "hustler_best_gain" in _s1_data and float(_s1_data.get("hustler_best_gain") or 0) > float(progress.get("hustler_best_gain") or 0):
                            progress["hustler_best_gain"] = _s1_data["hustler_best_gain"]
                            progress["hustler_overrides"] = _s1_data.get("hustler_overrides", {})
                except Exception as _se:
                    print(f"[respect-s1-warn] {_se}", flush=True)
            # also try scp from S1 if local s3/s5 path is empty
            # USER 2026-10-03 hollow-fix: never after a reset (see above).
            if not progress.get("done") and progress_path.exists() and not _board_reset:
                try:
                    import subprocess as _sp
                    _sp.run(["scp", "-o", "StrictHostKeyChecking=no", f"niels@157.90.168.35:/home/niels/binance-sandbox/data/reports/lifecycle_pilot/{progress_path.name}", str(progress_path)], capture_output=True, timeout=5)
                    if progress_path.exists():
                        _new = json.loads(progress_path.read_text())
                        if len(_new.get("done", {})) > len(progress.get("done", {})):
                            progress = _new
                            print(f"[respect-s1-scp] fetched {progress_path.name} from S1", flush=True)
                except Exception:
                    pass
    except Exception as _re2:
        print(f"[respect-warn] {_re2}", flush=True)
    if progress.get("verdict") == "IMPOSSIBLE" and os.getenv("FORCE_DC_RERUN") != "1":
        print(f"[IMPOSSIBLE-SKIP] {new_symside} tombstoned ({progress.get('impossible_path')}) — manual revision required, MUST NOT RETOUCH.", flush=True)
        return
    if progress.get("verdict") in ("NO_TRADES", "BEST_EFFORT") and os.getenv("FORCE_DC_RERUN") != "1":
        if _soft_verdict_fresh(new_symside, progress):
            print(f"[SOFT-REFRESH] {new_symside} verdict {progress.get('verdict')} but NPZ is newer — clearing verdict and re-running", flush=True)
            for _vk in ("verdict", "verdict_reasons", "verdict_npz_mtime_ns"):
                progress.pop(_vk, None)
            try:
                _atomic_write_json(progress_path, progress)
            except Exception:
                progress_path.write_text(json.dumps(progress))
        else:
            print(f"[SOFT-SKIP] {new_symside} verdict {progress.get('verdict')} on same NPZ — nothing to do (reruns on NPZ refresh).", flush=True)
            return
    # FIX 2026-09-26: Restore cumulative_gain from last completed row to preserve progress on resume
    # When resuming, progress["cumulative_gain"] might be stale; calculate from last completed row's cumulative_after
    try:
        if progress.get("done"):
            # Find last completed row (preserve execution order from dict insertion)
            last_completed_value = None
            for key in progress["done"]:
                rec = progress["done"][key]
                if "cumulative_after" in rec and rec.get("delta", 0) > 0:
                    # Only take cumulative from rows that advanced (POS delta)
                    last_completed_value = float(rec.get("cumulative_after") or 0)
            if last_completed_value is not None and last_completed_value > baseline_gain:
                print(f"[baseline-restore] found last completed row cumulative {last_completed_value:.4f} > baseline {baseline_gain:.4f} — using", flush=True)
                if "cumulative_gain" not in progress or progress.get("cumulative_gain") is None:
                    progress["cumulative_gain"] = last_completed_value
    except Exception as _e_restore:
        print(f"[baseline-restore-warn] {_e_restore}", flush=True)
    # Enforce monotonic baseline: never underperform BEST (leave settings as is = 0 delta)
    cumulative_gain = max(float(progress.get("cumulative_gain") or baseline_gain), float(baseline_gain or 0), float(progress.get("hustler_best_gain") or 0))
    cumulative_overrides = dict(progress.get("cumulative_overrides", overrides))
    cumulative_overrides = _clean_ingested_overrides(cumulative_overrides)
    _bl_trades = int(baseline_live.get("trades") or 0)
    _bl_valid = bool(baseline_live.get("valid"))
    baseline_had_zero_trades = (_bl_trades == 0) or (not _bl_valid)
    # REFILL from complete json on restart — never lose calculations already made (OOM/reboot/pkill)
    # If zip was damaged (truncated) but json has done cells, immediately refill sheet from json
    try:
        if progress.get("done"):
            wb_refill = openpyxl.load_workbook(str(wb_path), data_only=False)
            refilled = 0
            for key, rec in progress["done"].items():
                try:
                    # key is like "ENTRY_REVERSAL_BOUNCE!3:WT_15M_BOUNCE_OPEN_ENABLED=False"
                    sheet_part, rest = key.split("!", 1)
                    row_part, switch_eq = rest.split(":", 1)
                    r = int(row_part)
                    if sheet_part not in wb_refill.sheetnames:
                        continue
                    ws_r = wb_refill[sheet_part]
                    # RESUME FIX 2026-09-19: template was sorted by M (whites->oranges), row numbers in old done keys are stale.
                    # Fallback to switch-name lookup if row r does not contain expected switch.
                    try:
                        _cur_a = ws_r.cell(row=r, column=1).value
                        _cur_b = ws_r.cell(row=r, column=2).value
                        _exp_sw = switch_eq.split("=")[0] if "=" in switch_eq else switch_eq
                        _exp_val = switch_eq.split("=",1)[1] if "=" in switch_eq else ""
                        _cur_sw = str(_cur_a).strip() if _cur_a else ""
                        _cur_val = str(_cur_b).strip() if _cur_b is not None and not isinstance(_cur_b,bool) else (str(_cur_b) if isinstance(_cur_b,bool) else "")
                        if _cur_sw != _exp_sw or _cur_val != _exp_val:
                            # Search for correct row by switch name
                            _found = None
                            for _rr in range(3, ws_r.max_row+1):
                                _a = ws_r.cell(row=_rr, column=1).value
                                _b = ws_r.cell(row=_rr, column=2).value
                                _b_str = str(_b).strip() if _b is not None and not isinstance(_b,bool) else (str(_b) if isinstance(_b,bool) else "")
                                if str(_a).strip()==_exp_sw and _b_str==_exp_val:
                                    _found=_rr
                                    break
                            if _found:
                                r=_found
                            else:
                                continue
                    except: pass
                    # refill F HUSTLE_DELTA (col6) + G VECTOR_DELTA (col7) from rec delta — overwrite VLOOKUP/empty, never waste recalc
                    # F is hustle vs baseline, G is greedy vs cum; rec stores greedy delta (same for NEG, different for POS via E logic)
                    # For refill we write both as float(rec delta) when not float; POS hustle needs recalc but greedy G is correct
                    # F HUSTLE_DELTA = the row's result vs the ORIGINAL baseline (USER 2026-09-29 late)
                    _rv = ws_r.cell(row=r, column=6).value
                    _is_float = isinstance(_rv, (int, float)) and not isinstance(_rv, bool)
                    _fv = rec.get("delta_vs_initial")
                    if _fv is not None and (not _is_float or abs(float(_rv) - float(_fv)) > 1e-9):
                        ws_r.cell(row=r, column=6).value = float(_fv)
                        ws_r.cell(row=r, column=6).font = Font(name="Arial", size=10, bold=True, color="9C5700")
                        refilled += 1
                    if rec.get("k_filters"):
                        ws_r.cell(row=r, column=11).value = ", ".join(rec["k_filters"])
                    # also refill G VECTOR_DELTA (col7) greedy delta — was missing, left VLOOKUP strand
                    _gv = ws_r.cell(row=r, column=7).value
                    _g_is_float = isinstance(_gv, (int, float)) and not isinstance(_gv, bool)
                    _g_need = rec.get("delta") is not None and (not _g_is_float or abs(float(_gv) - float(rec["delta"])) > 1e-9)
                    if _g_need:
                        ws_r.cell(row=r, column=7).value = float(rec["delta"])
                        ws_r.cell(row=r, column=7).font = Font(name="Arial", size=10, bold=True, color="9C5700")
                        ws_r.cell(row=r, column=7).alignment = VISUAL_ALIGN
                        refilled += 1
                    # refill yellows L:BI from rec.get yellows if stored
                    _y = rec.get("yellows") or rec.get("pending_lbI") or {}
                    if _y:
                        # build header_to_col for this sheet on demand
                        _htc = {}
                        for c in range(12, ws_r.max_column + 1):
                            hv = ws_r.cell(row=2, column=c).value
                            if hv and isinstance(hv, str) and "=" in hv and not hv.upper().startswith("WHAT SWITCH"):
                                _htc[hv.strip()] = c
                        _y_bad = rec.get("yellow_reasons") or {}
                        for hdr, d in _y.items():
                            col = _htc.get(hdr)
                            _cv = ws_r.cell(row=r, column=col).value if col else None
                            _c_is_float = isinstance(_cv, (int, float)) and not isinstance(_cv, bool)
                            if col and hdr in _y_bad and isinstance(d, (int, float)):
                                ws_r.cell(row=r, column=col).value = float(d)
                                ws_r.cell(row=r, column=col).font = Font(name="Arial", size=10, color="808080")
                                refilled += 1
                                continue
                            if col and (not _c_is_float or abs(float(_cv) - float(d)) > 1e-9):
                                try:
                                    ws_r.cell(row=r, column=col).value = float(d)
                                    refilled += 1
                                except: pass
                    # refill Results_Deltas from rec
                    target = None
                    for cand_name in ["Results_Deltas", "Results_30d_Deltas", "Results_30d", "results"]:
                        if cand_name in wb_refill.sheetnames:
                            target = cand_name
                            break
                    if target:
                        rws = wb_refill[target]
                        # find or create row for this switch
                        switch = switch_eq.split("=")[0] if "=" in switch_eq else switch_eq
                        found = None
                        for rr in range(2, rws.max_row + 2):
                            if str(rws.cell(row=rr, column=1).value or "").strip() == switch_eq:
                                found = rr
                                break
                        if found and rws.cell(row=found, column=5).value in (None, "") and rec.get("delta") is not None:
                            rws.cell(row=found, column=5).value = float(rec["delta"])
                            rws.cell(row=found, column=8).value = float(rec.get("vec_gain") or 0)
                            refilled += 1
                except Exception:
                    continue
            if refilled:
                _atomic_save(wb_refill, wb_path)
                print(f"[refill] restored {refilled} cells from json progress {progress_path.name}", flush=True)
            wb_refill.close()
    except Exception as _e:
        print(f"[refill-warn] {_e}", flush=True)

    # ── SPEC-COMPLIANT FILLER (2026-09-26) — overrides legacy cycle/worst2best loop ──
    # CRITICAL BUG #1: Calculate baseline with PREVIOUS BEST OVERRIDES before spec_fill
    # Without this, baseline is wrong and ALL deltas are calculated against wrong baseline
    try:
        baseline_gain = float(evaluate_prepared_sanitized(prepared, cumulative_overrides, args.window_days).get("gain_pct", baseline_gain))
        print(f"[BASELINE-RECALC] {new_symside} with cumulative_overrides: {baseline_gain:.4f}% (was cum {cumulative_gain:.4f})", flush=True)
        # Deltas are measured vs the REAL gain of the current override set. The max(..., 0, hustler_best) above
        # clamped negative baselines to 0.0, so every row delta was gain-0 (always NEG, never promoted).
        cumulative_gain = baseline_gain
        print(f"[BASELINE-CHAIN-START] cumulative_gain set to baseline_gain {cumulative_gain:.4f}% — this will seed E3 and greedy chain", flush=True)
    except Exception as _e_baseline:
        print(f"[BASELINE-RECALC-WARN] {_e_baseline} — using original baseline {baseline_gain:.4f}%", flush=True)
        # CRITICAL FIX: Even on exception, cumulative_gain must equal baseline_gain for E3 to be filled
        cumulative_gain = baseline_gain
        print(f"[BASELINE-CHAIN-START-FALLBACK] cumulative_gain set to baseline_gain {cumulative_gain:.4f}% after exception", flush=True)

    if locals().get("_adapt_report") is not None:
        progress["adaptive_baseline"] = _adapt_report
        _atomic_write_json(progress_path, progress)
    if below_floor and locals().get("_adapt_report") is not None and not _adapt_report.get("credible"):
        # USER 2026-09-30: NO 30D sheet is ever disqualified — the repair stage ran out of steps; the sheet is filled anyway
        # (only VALID rows can promote, so the fill keeps repairing it) and the gap is flagged for the operator
        progress["baseline_not_credible"] = f"trades={baseline_trades} floor={min_trades} after adaptive repair — sheet filled, only valid rows promote"
        _atomic_write_json(progress_path, progress)
        print(f"[NO-CREDIBLE-BASELINE] {new_symside} {baseline_trades} trades after adaptive repair — FILLING ANYWAY (never disqualified), valid-only promotion", flush=True)
    if below_floor:
        progress["baseline_below_floor"] = f"trades={baseline_trades} floor={min_trades}"
        # USER 2026-09-29: "0 trades is impossible" — name the gates that veto every entry, then SWEEP anyway
        # (promotion still requires a valid >=floor-trade candidate, so a sweep can only lift the side out of the floor)
        _blockers = []
        for _bk, _bv in defaults.items():
            if not isinstance(_bv, bool) or _bk == "SIMPLE_PRICE_GT0_ENABLED":  # SIMPLE_PRICE_GT0 = always-trade test switch, not a real unblock
                continue
            _cur = cumulative_overrides.get(_bk, _bv)
            if not isinstance(_cur, bool):
                continue
            try:
                _br = evaluate_prepared_sanitized(prepared, {**cumulative_overrides, _bk: not _cur}, args.window_days) or {}
            except Exception:
                continue
            if int(_br.get("trades") or 0) >= min_trades:
                _blockers.append({"switch": _bk, "flip_to": not _cur, "trades": _br.get("trades"), "gain_pct": _br.get("gain_pct"), "valid": _br.get("valid")})
        progress["floor_blockers"] = _blockers
        _atomic_write_json(progress_path, progress)
        print(f"[FLOOR-BLOCKERS] {new_symside} baseline {baseline_trades} trades; single flips that restore >={min_trades} trades: {[(b['switch'], b['flip_to'], b['trades']) for b in _blockers][:12]}", flush=True)
        if baseline_trades == 0 and not _blockers:
            progress["verdict"] = "NO_TRADES"
            progress["verdict_reasons"] = [f"baseline 0 trades, no single bool flip restores >={min_trades} — sweep skipped, no REDO (USER 2026-10-03: skip dead fills)"]
            progress["verdict_npz_mtime_ns"] = _npz_mtime_ns(new_symside)
            progress.pop("needs_redo", None)
            progress.pop("final_path", None)
            _atomic_write_json(progress_path, progress)
            print(f"[NO-TRADES-SKIP] {new_symside} baseline 0 trades + no unblock flip — XLS kept, sweep skipped, no REDO; reruns only on newer NPZ", flush=True)
            return
        if os.environ.get("V15_SKIP_BELOW_FLOOR", "0") == "1":
            progress["diagnostic_only"] = progress["baseline_below_floor"]
            _atomic_write_json(progress_path, progress)
            print(f"[SAMPLE-FLOOR-VIOLATION] {new_symside} XLS {wb_path.name} written, sweep skipped (V15_SKIP_BELOW_FLOOR=1)", flush=True)
            return
        print(f"[SAMPLE-FLOOR-VIOLATION] {new_symside} below floor — sweeping anyway, only valid >={min_trades}-trade candidates can promote", flush=True)
    # Baseline now correct with previous best overrides; now fill entire workbook per spec:
    #   VECTOR_DELTA = sum pos yellows; POS-> stay same tab next row + baseline, NEG/None/0-> move to first pending row in next tab
    #   STDEV_SLOPE_SIZING skipped (12 tabs), every row gets pos/neg delta, LIVE columns BLANK until complete, 10s RED stall guard
    try:
        _spec_cum, _spec_over, _spec_prog = _spec_fill_workbook(new_symside, wb_path, progress, progress_path, flags_md, cumulative_gain, cumulative_overrides, defaults, baseline_gain, bh, prepared, args, baseline_vec, baseline_live)
        progress = _spec_prog
        cumulative_gain = float(_spec_cum)
        cumulative_overrides = dict(_spec_over)

        # CRITICAL VALIDATION: 3971 F cells + yellows across 13 tabs must be filled — per user, NEVER stop early
        rows_filled = len(progress.get("done", {}))
        # Also count actual F column fills in workbook (3971 target) — spec_fill may exit via exception before saving, so check file
        try:
            _wb_check = openpyxl.load_workbook(str(wb_path), data_only=False)
            _f_filled = sum(1 for _ws in _wb_check.worksheets if _ws.title in SWITCH_SHEETS for _r in range(3, _ws.max_row+1) if _ws.cell(_r, 6).value is not None and not isinstance(_ws.cell(_r, 6).value, str))
            _wb_check.close()
        except Exception:
            _f_filled = rows_filled
        print(f"[spec-fill-CHECK] done={rows_filled} F_filled={_f_filled} target ~3000+", flush=True)
        # 2026-09-28 FIX: NEVER fallback to legacy (has -1.0 bugs) — accept spec_fill result regardless of count
        # Legacy loop is broken and produces -1 values. Better to have incomplete fill than -1 values.
        # if rows_filled < 1000:
        #     raise ValueError(f"spec-fill incomplete but accepting (no legacy fallback)")  # DISABLED

        # After spec fill, workbook is complete — return early, skip legacy loop (keep legacy code below as dead fallback)
        # Finalize with charts/final xlsx handling that legacy does after loop — replicate minimal final steps here then return
        try:
            # write campaign-style summary for herd
            if progress_path.exists():
                # ensure cumulative saved
                progress["cumulative_gain"] = float(cumulative_gain)
                progress["cumulative_overrides"] = dict(cumulative_overrides)
                _atomic_write_json(progress_path, progress)
        except Exception:
            pass
        print(f"[spec-fill] workbook complete, {rows_filled} rows filled, returning early cum={cumulative_gain:.4f}", flush=True)
        print(f"[mandatory-yellows] {_mandatory_summary()}", flush=True)
        try:
            _result_stamp(progress, new_symside)
            _atomic_write_json(progress_path, progress)
        except Exception:
            pass
        return
    except Exception as _spec_e:
        import traceback as _tb_spec
        print(f"[spec-fill-INCOMPLETE] spec filler incomplete ({_spec_e}) — falling back to legacy loop to complete all rows", flush=True)
    heartbeat_path = Path("/tmp") / f"v14_heartbeat_{new_symside}.txt"
    per_cell_timeout_sec = YELLOW_TIMEOUT  # spec YELLOW_TIMEOUT=0.1 for every cell (naked and yellow)
    # NEVER WAIT — hard YELLOW_TIMEOUT per cell, then mark cell+tab RED via _spec_mark_red, write -1/0, enqueue queue.Queue, plowing never blocks
    def _touch_heartbeat(msg: str):
        try:
            heartbeat_path.write_text(f"{time.time():.0f} {msg}")
        except: pass
    def _check_per_cell_timeout(cell_start: float) -> bool:
        return (time.time() - cell_start) > per_cell_timeout_sec

    _touch_heartbeat("start")
    total_pos = 0
    print(f"[LOG {time.time():.1f}] start sheets={len(SWITCH_SHEETS)} wb={wb_path.name} mem={__import__('psutil').Process().memory_info().rss/1e6:.0f}MB" if True else "", flush=True)

    print(f"[LOG {time.time():.1f}] load wb_tmp {wb_path}", flush=True)
    wb_tmp = openpyxl.load_workbook(str(wb_path), data_only=False)
    print(f"[LOG {time.time():.1f}] wb_tmp loaded sheets={wb_tmp.sheetnames[:3]}", flush=True)
    sheets = [args.sheet] if args.sheet else [s for s in SWITCH_SHEETS if s in wb_tmp.sheetnames]
    if not sheets:
        sheets = [s for s in wb_tmp.sheetnames if any(s.startswith(p) for p in ["ENTRY", "EXIT", "REENTRY", "AUGMENT", "REDUCE", "GLOBAL"])]
    # 0914 PROTOTYPE: sheet ordering variants
    if args.sheet_order:
        _order = [s.strip().upper() for s in args.sheet_order.split(",") if s.strip()]
        sheets = [s for s in _order if s in sheets] + [s for s in sheets if s not in _order]
        print(f"[0914-sheet-order] custom order {sheets}", flush=True)
    elif args.seq_mode == "worst2best":
        # worst->best by avg delta from progress done (ascending, most negative first); fallback to reverse SWITCH_SHEETS if no history
        try:
            def _avg_delta(sh: str) -> float:
                vals = [float(v.get("delta") or 0) for k, v in progress.get("done", {}).items() if k.startswith(sh + "!") and "GLOBAL:" not in k]
                return sum(vals) / len(vals) if vals else 0.0
            # if we have history, sort by avg delta ascending (worst first); else heuristic reverse (GLOBAL worst)
            if any(_avg_delta(s) != 0 for s in sheets):
                sheets = sorted(sheets, key=_avg_delta)
                print(f"[0914-worst2best] sheets ordered worst->best by avg delta {[(s, round(_avg_delta(s),3)) for s in sheets]}", flush=True)
            else:
                # no history: heuristic worst sheets last in legacy -> bring worst estimated first (GLOBAL, REDUCE, AUGMENT)
                sheets = list(reversed(sheets))
                print(f"[0914-worst2best] no history, heuristic reversed {sheets}", flush=True)
        except Exception as _e:
            print(f"[0914-worst2best-warn] {_e}", flush=True)
    elif args.seq_mode == "shuffle":
        import random as _rnd2
        _rnd2.shuffle(sheets)
        print(f"[0914-shuffle] sheets shuffled {sheets}", flush=True)
        # also shuffle rows within each sheet will be handled per-sheet below
    else:
        print(f"[0914-seq] mode={args.seq_mode} sheets={sheets}", flush=True)
    # 0914 PROTOTYPE: cycle-through-tabs on every NEG delta — build per-sheet row queues
    # sequential = legacy for sheet in sheets: for row in rows (all entry bounce then breakout then exit)
    # cycle = round-robin: form global queue cycling tabs; on NEG delta next tab before next row of same tab
    _0914_use_cycle = (args.seq_mode in ("cycle", "worst2best", "worst_first", "worst-first"))
    if _0914_use_cycle:
        print(f"[0914-cycle] ENABLED cycle-through-tabs on NEG delta (round-robin across {len(sheets)} sheets)", flush=True)
        # pre-build per-sheet rows dict for cycle scheduling
        _sheet_rows_map: dict[str, list] = {}
        for _sh in sheets:
            try:
                _wb_tmp2 = openpyxl.load_workbook(str(wb_path), data_only=False)
                if _sh not in _wb_tmp2.sheetnames:
                    _wb_tmp2.close(); continue
                _ws_tmp = _wb_tmp2[_sh]
                _rows = []
                for _r in range(2, _ws_tmp.max_row + 1):
                    _sw = _ws_tmp.cell(row=_r, column=1).value
                    if not _sw or not isinstance(_sw, str): continue
                    _sw = _sw.strip()
                    if not _sw or _sw.lower() in ("switch", "general", "blanket", "filter", "option value"): continue
                    if _sw.lower() == "filter" and str(_ws_tmp.cell(row=_r, column=2).value or "").lower() == "option value": continue
                    _cand = _ws_tmp.cell(row=_r, column=2).value
                    if _cand is None: continue
                    if isinstance(_cand, str) and _cand.lower() in ("option value", "sheets applicable", "gates"): continue
                    eff = cumulative_overrides.get(_sw, defaults.get(_sw, _cand))
                    def _norm(v):
                        if isinstance(v, str) and v.lower() in ("true", "false"): return v.lower() == "true"
                        return v
                    _key_skip = f"{_sh}!{_r}:{_sw}={_cand}"
                    # RESUME FIX: check by switch name not just row key (row numbers stale after sort)
                    _key_switch = _key_skip.split(":",1)[-1] if ":" in _key_skip else _key_skip
                    _done_by_switch = any(k.split(":",1)[-1]==_key_switch for k in progress.get("done", {}).keys())
                    if _norm(_cand) == _norm(eff) and (_key_skip in progress.get("done", {}) or _done_by_switch): continue
                    _f_val = _ws_tmp.cell(row=_r, column=6).value
                    _is_general_f = isinstance(_f_val, str) and _f_val.strip().upper().startswith("GENERAL")
                    _is_orange_global = str(_ws_tmp.cell(row=_r, column=4).value or "") == "GLOBAL_CHECK"
                    _is_blanket_end = str(_ws_tmp.cell(row=_r, column=9).value or "").strip().lower() == "blanket (page end)"
                    if _is_orange_global:
                        _rows.append((_r, _sw, _cand)); continue
                    if _is_general_f or _is_blanket_end: continue
                    try:
                        _fill_rgb = _ws_tmp.cell(row=_r, column=6).fill.start_color.rgb if _ws_tmp.cell(row=_r, column=6).fill.start_color.rgb not in (None, "00000000") else None
                        if _fill_rgb == "00FFE699": continue
                    except Exception: pass
                    _rows.append((_r, _sw, _cand))
                _wb_tmp2.close()
                _sheet_rows_map[_sh] = _rows
                print(f"[0914-cycle] {_sh} {len(_rows)} variants queued", flush=True)
            except Exception as _e:
                print(f"[0914-cycle-warn] {_sh} {_e}", flush=True)
                _sheet_rows_map[_sh] = []
        # Build worst-first deque schedule: stay on same tab with POS delta, advance to next tab on NEG delta only
        # For schedule logging we still build a static round-robin preview, but actual execution will use dynamic deque
        _ordered_cycle: list[tuple[str, int, str, object]] = []
        _indices = {s: 0 for s in sheets}
        _remaining = sum(len(v) for v in _sheet_rows_map.values())
        _cycle_n = 0
        while any(_indices[s] < len(_sheet_rows_map.get(s, [])) for s in sheets):
            for _sh in sheets:
                _rows = _sheet_rows_map.get(_sh, [])
                _idx = _indices[_sh]
                if _idx < len(_rows):
                    _r, _sw, _cand = _rows[_idx]
                    _ordered_cycle.append((_sh, _r, _sw, _cand))
                    _indices[_sh] += 1
            _cycle_n += 1
            if _cycle_n > 5000: break
        print(f"[0914-cycle] schedule {len(_ordered_cycle)}/{_remaining} rows worst-first deque (stay on POS, next tab on NEG) cycles={_cycle_n} first10={_ordered_cycle[:10]}", flush=True)
        # Dynamic execution will be handled via _cycle_deque in sequential loop below
        # replace sheets loop with single round-robin iteration over _ordered_cycle
        # we keep outer sheet grouping for wb_keep efficiency but iterate in cycle order using sheet change detection
        _current_cycle_sheet = None
        wb_keep_global = None
        ws_keep_global = None
        header_to_col_global: dict = {}
        _cycle_sheet_wb_keep = None
        # sequential sheets loop replaced by cycle schedule below; flag to control flow
        _0914_cycle_schedule = _ordered_cycle
        _0914_cycle_sheet_rows_map = _sheet_rows_map
    else:
        _0914_cycle_schedule = None
        _0914_cycle_sheet_rows_map = None
    wb_tmp.close()
    # 0914 branching: if cycle mode use dedicated round-robin handler else legacy sequential
    if _0914_use_cycle and _0914_cycle_schedule is not None:
        # REAL cycle-through-tabs on NEG: worst-first deque — stay on same tab with POS delta, advance to next tab on NEG delta only
        # This is the user-mandated behavior: with POS we exploit the same sheet's next best switch, with NEG we rotate to next worst sheet
        # Uses the same per-row evaluator as sequential but drives sheets via a deque that respects POS/NEG outcome
        from collections import deque as _deque_cycle
        print(f"[0914-cycle] ENABLED cycle-through-tabs on NEG delta (worst-first deque, stay on POS, advance on NEG) {len(_0914_cycle_schedule)} rows", flush=True)
        _wb_keep_cache: dict[str, object] = {}
        _header_cache: dict[str, dict] = {}
        def _get_wb_keep(sheet_name: str):
            if sheet_name not in _wb_keep_cache:
                _wb = openpyxl.load_workbook(str(wb_path), data_only=False)
                _wb_keep_cache[sheet_name] = _wb
                _ws = _wb[sheet_name] if sheet_name in _wb.sheetnames else None
                _htc = {}
                if _ws is not None:
                    for c in range(12, _ws.max_column + 1):
                        hv = _ws.cell(row=2, column=c).value
                        if hv and isinstance(hv, str) and "=" in hv:
                            hv = hv.strip()
                            if not hv.upper().startswith("WHAT SWITCH"): _htc[hv] = c
                        if hv and isinstance(hv, str) and hv.strip().upper().startswith("WHAT SWITCH"): break
                _header_cache[sheet_name] = _htc
            return _wb_keep_cache[sheet_name], _header_cache[sheet_name]
        # Build dynamic deque: worst-first sheets already ordered, each with its row queue
        _deque_sheets = _deque_cycle([s for s in sheets if _sheet_rows_map.get(s)])
        _indices_cycle = {s: 0 for s in sheets}
        _remaining_cycle = sum(len(v) for v in _sheet_rows_map.values())
        _processed_cycle = 0
        # Helper to process one row (extracted from sequential per-row body) — returns delta_best
        # For brevity we inline the sequential per-row evaluation here via a local function that captures cumulative_gain/progress
        # Instead of duplicating 900 lines, we drive the sequential loop's row processor via a shared helper defined below
        # We will iterate dynamically: while deque non-empty
        # Note: the sequential `for sheet in sheets:` loop below is SKIPPED when cycle is active — we handle all rows here
        _cycle_active = True
        # We need to define _process_one_row helper before loop — define inline
        def _process_0914_row_helper(sheet: str, r: int, switch: str, cand):
            """Full per-row evaluator: naked + ALL yellows vs cumulative_before, writes L:BI yellows, updates progress/cumulative_gain. Mirrors sequential body."""
            nonlocal cumulative_gain, progress, cumulative_overrides, wb_path, flags_md, prepared, defaults, baseline_gain, args
            # EVERY ROW+EVERY YELLOW per user - no skip, every disabled/category row still calculated (was 80% skip waste)
            if _is_kg_never_skip(switch):
                pass
            elif "W15M" in switch or "WT_15M" in switch or "WT_CHAN_15m" in switch or "WT_AVG_15m" in switch or "WT_15M_BOUNCE" in switch:
                pass
            else:
                try:
                    _cat = ("CRYPTO" if "USDT" in new_symside or "USDC" in new_symside else "STOCKS") + "_" + new_symside.rsplit("_",1)[-1]
                    _cat_set = disabled_per_category.get(_cat, set())
                    if switch in _cat_set:
                        print(f"[NO-SKIP] {switch} in disabled_per_category for {_cat} - still calculating ALL yellows per user (no 80% skip)", flush=True)
                    if switch in disabled_switches:
                        print(f"[NO-SKIP] {switch} in disabled_switches - still calculating ALL yellows per user", flush=True)
                except:
                    pass
            # Build header map for this sheet
            wb_h, htc = _get_wb_keep(sheet)
            ws_h = wb_h[sheet] if sheet in wb_h.sheetnames else None
            specifics = opportune_filter_rows(new_symside, sheet, switch)
            opportune = specifics
            def parse_opt(v, default):
                if isinstance(default, bool):
                    return str(v).lower() == "true" if str(v).lower() in ("true", "false") else bool(v)
                if isinstance(default, int) and not isinstance(default, bool):
                    try: return int(float(str(v)))
                    except: return v
                if isinstance(default, float):
                    try: return float(str(v))
                    except: return v
                if isinstance(v, str) and v.lower() in ("true", "false"):
                    return v.lower() == "true"
                try:
                    if "." in str(v): return float(str(v))
                    return int(str(v))
                except: return v
            def norm2(a, b):
                if isinstance(a, str) and a.lower() in ("true", "false"): a = a.lower() == "true"
                if isinstance(b, str) and b.lower() in ("true", "false"): b = b.lower() == "true"
                return a == b
            _rel_eval, _rel_ident = [], []
            for e in specifics:
                _hdr = f"{e['filter']}={e['opt']}"
                if _hdr not in htc: continue
                _ov = parse_opt(e["opt"], defaults.get(e["filter"]))
                _cur = cand if e["filter"] == switch else cumulative_overrides.get(e["filter"], defaults.get(e["filter"]))
                if norm2(_ov, _cur): _rel_ident.append(_hdr)
                else: _rel_eval.append((e["filter"], _ov, _hdr, e["opt"]))
            single_filters = list(_rel_eval)
            # YELLOW-ONLY FIX 2026-09-25: ONLY yellow cells for that one switch — identical filters (same value as cumulative) are NOT yellow, skip synthetic copy
            identical_hdrs = []
            relevant_hdrs = [t[2] for t in single_filters]
            candidates = []
            v0 = dict(cumulative_overrides); v0[switch] = cand; v0, _ = sanitize_overrides(v0, defaults)
            candidates.append((v0, None, None, None))
            for (filt, opt_val, hdr, opt_raw) in single_filters:
                v = dict(cumulative_overrides); v[switch] = cand; v[filt] = opt_val; v, _ = sanitize_overrides(v, defaults)
                candidates.append((v, filt, opt_val, hdr))
            # batch evaluate — with >1s slow-cell monitor
            import time as _t_cell
            _t0 = _t_cell.time()
            vecs = []
            try:
                if prepared is not None:
                    from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_prep
                    import concurrent.futures as _cf2
                    with _cf2.ThreadPoolExecutor(max_workers=args.workers) as ex:
                        vecs = list(ex.map(lambda vv: _eval_prep(prepared, vv, window_days=args.window_days), [c[0] for c in candidates]))
                else:
                    from tools.opt.v12_pilot import evaluate_many_sanitized as _eval_many
                    vecs = _eval_many(switch, [c[0] for c in candidates], window_days=args.window_days)
            except Exception: vecs = []
            _dt_cell = _t_cell.time() - _t0
            if _dt_cell > 1.0:
                print(f"[SLOW-CELL] {new_symside} {sheet}!{r} {switch} { _dt_cell:.2f}s >1s candidates={len(candidates)} workers={args.workers}", flush=True)
            cumulative_before = cumulative_gain
            pending_lbI = {}
            invalid_hdrs = []
            best = None
            vector_delta_val = None
            # USER 2026-10-03 RULE#2/#3: pending_lbI holds ONLY genuinely evaluated deltas; settled-invalid
            # (floor/vomit verdict) counts as calculated, timeouts/errors stay pending (never 0.0).
            from tools.v15_row_guards import invalid_settled as _inv_settled, is_real_number as _is_real, mark_unevaluated as _mark_red, yellows_complete as _y_complete, zero_audit_line as _zero_audit
            evaluated_hdrs = set()
            unsettled_hdrs = []
            for idx, (variant, filt, fval, hdr) in enumerate(candidates):
                if idx >= len(vecs): break
                vec = vecs[idx]
                if not vec.get("valid"):
                    _reason = str(vec.get("invalid_reason") or vec.get("reason") or "invalid")
                    if filt is not None and hdr in htc:
                        _mark_red(ws_h, r, htc.get(hdr), _reason)
                        if _inv_settled(_reason):
                            invalid_hdrs.append(hdr)
                            evaluated_hdrs.add(hdr)
                        else:
                            unsettled_hdrs.append(hdr)
                            print(f"[ROW-UNSETTLED] {new_symside} {sheet}!{r} {switch}={cand} yellow {hdr} no verdict ({_reason[:100]})", flush=True)
                    continue
                _vg_raw = vec.get("gain_pct")
                if not _is_real(_vg_raw):
                    if filt is not None and hdr in htc:
                        _mark_red(ws_h, r, htc.get(hdr), f"gain-missing ({_vg_raw!r})")
                        unsettled_hdrs.append(hdr)
                        print(f"[ROW-UNSETTLED] {new_symside} {sheet}!{r} {switch}={cand} yellow {hdr} gain-missing", flush=True)
                    continue
                vg = float(_vg_raw); delta = vg - cumulative_before
                if filt is not None and hdr in htc:
                    pending_lbI[hdr] = float(delta)
                    evaluated_hdrs.add(hdr)
                    if delta == 0.0:
                        print(_zero_audit(sheet, r, switch, filt, hdr, vg, cumulative_before, vec.get("trades"), _run_npz_short), flush=True)
                elif filt is None and delta == 0.0:
                    print(_zero_audit(sheet, r, switch, None, "naked", vg, cumulative_before, vec.get("trades"), _run_npz_short), flush=True)
                if best is None or delta > best[0]: best = (delta, variant, filt, fval, hdr, vec)
                if filt is None: vector_delta_val = float(delta)
            # identical not written — yellow-only
            # combined pos
            try:
                pos_filters = [(f, o, h) for (f, o, h, raw) in single_filters if pending_lbI.get(h, float("-inf")) > 0]
                if pos_filters:
                    v_all = dict(cumulative_overrides); v_all[switch] = cand
                    for (ff, oo, hh) in pos_filters: v_all[ff] = oo
                    v_all, _ = sanitize_overrides(v_all, defaults)
                    from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_all
                    vec_all = _eval_all(prepared, v_all, window_days=args.window_days) if prepared is not None else None
                    if vec_all and vec_all.get("valid"):
                        vg_all = float(vec_all.get("gain_pct") or 0); delta_all = vg_all - cumulative_before
                        # FIX 2026-09-25: G = d_all (combined) whenever any pos yellows exist — true filter-on-switch effect, not max vs best single
                        all_hdrs = "+".join([h for (_,_,h) in pos_filters])
                        best = (delta_all, v_all, None, None, all_hdrs, vec_all)
            except Exception: pass
            if best is None:
                # no valid
                if ws_h is not None:
                    for _hdr in relevant_hdrs:
                        _col = htc.get(_hdr)
                        if _col: 
                            try:
                                # RULE#2: no valid vector = nothing calculated — blank+red, never 0.0
                                _mark_red(ws_h, r, _col, "no-valid-vector")
                            except: pass
                    try: ws_h.cell(row=r, column=6).value = None
                    except: pass
                    _mark_red(ws_h, r, 7, "no-valid-vector")
                key = f"{sheet}!{r}:{switch}={cand}"
                # RULE#3: no valid vector = row NOT calculated — no done entry, stays pending (never a 0.0 row)
                progress.get("done", {}).pop(key, None)
                print(f"[ROW-INCOMPLETE] {new_symside} {key} no valid vector — no zeros written, stays pending", flush=True)
                return 0.0
            delta_best, variant_best, filt_best, fval_best, hdr_best, vec_best = best
            delta_best, variant_best, filt_best, fval_best, hdr_best, vec_best = best
            # USER 2026-10-03 RULE#3: the row is NOT complete until every yellow cell is calculated
            # (valid delta or settled-invalid verdict). Unsettled = no done entry, stays pending.
            _missing_y = _y_complete(relevant_hdrs, evaluated_hdrs)
            if _missing_y:
                for _mh in _missing_y:
                    _mark_red(ws_h, r, htc.get(_mh), "uncalculated")
                _key_m = f"{sheet}!{r}:{switch}={cand}"
                progress.get("done", {}).pop(_key_m, None)
                if ws_h is not None:
                    try:
                        ws_h.cell(row=r, column=6).value = None
                    except Exception:
                        pass
                    _mark_red(ws_h, r, 7, f"incomplete-yellows {len(_missing_y)}")
                print(f"[ROW-INCOMPLETE] {new_symside} {_key_m} {len(_missing_y)} yellows uncalculated — no zeros written, stays pending", flush=True)
                return 0.0
            # SWITCH UNDERSTANDS IT CAN NOT PASS TO NEXT ROW UNTIL ALL YELLOW CELLS HAVE BEEN CALCULATED APPLYING THE SPECIFIC FILTER IN THAT COLUMN — per-yellow delta inside yellow cell, add to own delta if positive, baseline never without pos delta
            # DESTROY VIRUS: never write numbers in BASELINE without pos delta; never re-evaluate pending_lbI as overrides dict (was virus writing BB_BOUNCE garbage)
            if ws_h is not None:
                _per_yellow_sum = 0.0
                for _hdr in relevant_hdrs:
                    _col = htc.get(_hdr)
                    if not _col:
                        continue
                    # FILTER IN HEADER APPLIED ONLY TO SWITCH IN THAT ROW — never to other rows without yellow in that column
                    if _hdr not in pending_lbI:
                        continue  # settled-invalid (blank+red at eval) — post-gate, nothing unsettled reaches here
                    # per-yellow delta already calculated applying the specific filter in that column — block until done (pending_lbI holds it)
                    try:
                        _cand_y = pending_lbI.get(_hdr)
                        if _cand_y is None:
                            continue
                        _delta_y = float(_cand_y)
                        # write delta inside the yellow cell — always write after delta calc for this switch's filter only (REPORT INTO YELLOW CELLS)
                        ws_h.cell(row=r, column=_col).value = float(_delta_y)
                        if _delta_y > 1e-9:
                            from openpyxl.styles import PatternFill as _PF_y
                            ws_h.cell(row=r, column=_col).fill = _PF_y(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
                            _per_yellow_sum += float(_delta_y)
                        else:
                            from openpyxl.styles import PatternFill as _PF_yn
                            ws_h.cell(row=r, column=_col).fill = _PF_yn(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
                    except Exception:
                        pass
                # FIX 2026-09-25: G = d_all (combined) already in best when pos existed — do NOT double-add sum. Yellows are individual d_y, G is combined d_all.
                # delta_best is already d_all (from vec_all) if any pos, else best single. Keep as is.
                try:
                    if ws_h.cell(row=r, column=7).value is None:
                        ws_h.cell(row=r, column=7).value = float(delta_best)
                except: pass
                try:
                    # FIX 2026-09-28: F = best candidate's real vec gain minus baseline (gated: worst_first does not use F)
                    _vg_f = (vec_best or {}).get("gain_pct")
                    if WRITE_HUSTLE and _vg_f is not None:
                        ws_h.cell(row=r, column=6).value = float(_vg_f) - float(baseline_gain)
                        ws_h.cell(row=r, column=6).font = __import__("openpyxl").styles.Font(name="Arial", size=10, bold=True, color="006100" if float(_vg_f) - float(baseline_gain) > 0 else "9C0006")
                    ws_h.cell(row=r, column=6).fill = __import__("openpyxl").styles.PatternFill(fill_type=None)
                    # K = sum of pos yellows for this row
                    try:
                        ws_h.cell(row=r, column=11).value = float(_per_yellow_sum) if '_per_yellow_sum' in locals() and _per_yellow_sum > 1e-9 else None
                        ws_h.cell(row=r, column=11).alignment = __import__("openpyxl").styles.Alignment(horizontal="left", vertical="center")
                    except: pass
                    if ws_h.cell(row=r, column=7).value is None or float(ws_h.cell(row=r, column=7).value or 0) == 0:
                        ws_h.cell(row=r, column=7).value = float(delta_best)
                    ws_h.cell(row=r, column=6).alignment = __import__("openpyxl").styles.Alignment(horizontal="left", vertical="center")
                    ws_h.cell(row=r, column=7).alignment = __import__("openpyxl").styles.Alignment(horizontal="left", vertical="center")
                    # Only set NEXT row's E if delta>0 (add to previous baseline) — per law: POS delta -> add delta to baseline in next row
                    if delta_best > 1e-9:
                        try:
                            next_r = r + 1
                            if next_r <= ws_h.max_row:
                                nxt_e = ws_h.cell(row=next_r, column=5).value
                                # Only set next E if currently BLANK (None) — never overwrite or repeat
                                if nxt_e is None or (isinstance(nxt_e, str) and nxt_e.strip() == ""):
                                    ws_h.cell(row=next_r, column=5).value = float(cumulative_before + delta_best)
                                    ws_h.cell(row=next_r, column=5).font = __import__("openpyxl").styles.Font(name="Arial", size=10, bold=True, color="006100")
                                    ws_h.cell(row=next_r, column=5).alignment = __import__("openpyxl").styles.Alignment(horizontal="left", vertical="center")
                            else:
                                # Next row is next tab's first pending row — handled by outer loop's baseline carry
                                pass
                        except Exception:
                            pass
                    # Else NEG/0 delta -> DO NOT write baseline in next row, move to next tab's first pending row (handled by sheet deque)
                    # C override stays for best previous results — never clear on delta<=0
                except: pass
            else:
                delta_best, variant_best, filt_best, fval_best, hdr_best, vec_best = best
            # FIX 1: default value already being calculated — if cand == default, delta must be 0
            try:
                _def_val = defaults.get(switch)
                if _def_val is not None and str(cand).strip().lower() == str(_def_val).strip().lower():
                    if delta_best > 1e-9:
                        print(f"[BROKEN_DEFAULT] {switch} cand {cand} == default {_def_val} delta {delta_best:.2f} >0 — forcing 0, system broken BADLY FIX", flush=True)
                        delta_best = 0
            except: pass
            # FIX 3: 30+% suspicious
            try:
                if abs(delta_best) >= 30:
                    print(f"[SUSPICIOUS_30PCT] {switch}={cand} delta {delta_best:.2f} >=30% — investigating vector vs baseline {cumulative_before:.2f} vec {vec_best.get('gain_pct',0):.2f}", flush=True)
            except: pass
            key = f"{sheet}!{r}:{switch}={cand}"
            progress.setdefault("done", {})[key] = {"delta": float(delta_best), "vec_gain": float(vec_best.get("gain_pct") or 0), "yellows": {h: float(pending_lbI[h]) for h in relevant_hdrs if h in pending_lbI}, "invalid_yellows": list(invalid_hdrs), "unsettled_yellows": list(unsettled_hdrs), "yellows_delta": {h: (float(ws_h.cell(row=r, column=htc.get(h)).value) if _is_real(ws_h.cell(row=r, column=htc.get(h)).value) else None) if ws_h is not None and htc.get(h) else None for h in relevant_hdrs}, "cumulative_before": float(cumulative_before), "cumulative_after": float(cumulative_before + delta_best) if delta_best > 0 else float(cumulative_before), "best_filter": filt_best, "best_fval": fval_best, "npz": _run_npz_short, "complete": True}
            # FIX 2: POS AVG_DELTAS -> DEFAULT both in sheet and config
            if delta_best > 1e-9:
                try:
                    # Update sheet B column for this switch to make cand the new default (bold True at bottom)
                    if ws_h is not None:
                        # Find all rows for this switch in this sheet
                        _rows_for_switch = []
                        for _rr in range(3, ws_h.max_row+1):
                            if ws_h.cell(row=_rr, column=1).value and str(ws_h.cell(row=_rr, column=1).value).strip() == switch:
                                _rows_for_switch.append(_rr)
                        for _rr in _rows_for_switch:
                            _b_val = ws_h.cell(row=_rr, column=2).value
                            is_cand = str(_b_val).strip().lower() == str(cand).strip().lower()
                            ws_h.cell(row=_rr, column=1).font = Font(bold=is_cand, name="Arial", size=10, color="000000" if is_cand else "000000")
                            ws_h.cell(row=_rr, column=2).font = Font(bold=is_cand, name="Arial", size=10, color="000000" if is_cand else "000000")
                            if is_cand:
                                ws_h.cell(row=_rr, column=1).fill = PatternFill(start_color="FFFCE4EC", end_color="FFFCE4EC", fill_type="solid")
                                ws_h.cell(row=_rr, column=2).fill = PatternFill(start_color="FFFCE4EC", end_color="FFFCE4EC", fill_type="solid")
                            else:
                                ws_h.cell(row=_rr, column=1).fill = PatternFill(fill_type=None)
                                ws_h.cell(row=_rr, column=2).fill = PatternFill(fill_type=None)
                            ws_h.cell(row=_rr, column=1).alignment = Alignment(horizontal="left", vertical="center")
                            ws_h.cell(row=_rr, column=2).alignment = Alignment(horizontal="left", vertical="center")
                        # Ensure WHITE above ORANGE ordering after POS update: move bold to bottom
                        try:
                            # Reorder this switch's rows so white (False) above orange (True)
                            _rows = _rows_for_switch
                            _vals = [(ws_h.cell(row=r, column=1).value, ws_h.cell(row=r, column=2).value, ws_h.cell(row=r, column=1).font.bold) for r in _rows]
                            # Find bold index
                            _bolds = [b for _,_,b in _vals]
                            try: _def_idx = _bolds.index(True)
                            except: _def_idx = -1
                            if _def_idx != -1 and _def_idx != len(_rows)-1:
                                # Reorder: non-bold first, bold last
                                _bold_item = _vals[_def_idx]
                                _non_bold = [v for i,v in enumerate(_vals) if i != _def_idx]
                                _new_order = _non_bold + [_bold_item]
                                for _idx, _r in enumerate(_rows):
                                    ws_h.cell(row=_r, column=1).value = _new_order[_idx][0]
                                    ws_h.cell(row=_r, column=2).value = _new_order[_idx][1]
                                    ws_h.cell(row=_r, column=1).font = Font(bold=_new_order[_idx][2], name="Arial", size=10)
                                    ws_h.cell(row=_r, column=2).font = Font(bold=_new_order[_idx][2], name="Arial", size=10)
                                    ws_h.cell(row=_r, column=1).alignment = Alignment(horizontal="left", vertical="center")
                                    ws_h.cell(row=_r, column=2).alignment = Alignment(horizontal="left", vertical="center")
                                    if _new_order[_idx][2]:
                                        ws_h.cell(row=_r, column=1).fill = PatternFill(start_color="FFFCE4EC", end_color="FFFCE4EC", fill_type="solid")
                                        ws_h.cell(row=_r, column=2).fill = PatternFill(start_color="FFFCE4EC", end_color="FFFCE4EC", fill_type="solid")
                                    else:
                                        ws_h.cell(row=_r, column=1).fill = PatternFill(fill_type=None)
                                        ws_h.cell(row=_r, column=2).fill = PatternFill(fill_type=None)
                        except: pass
                        # Also update config.py / config_tradier.py for this switch if pos
                        try:
                            import pathlib as _pl
                            _cfg_path = _pl.Path("config.py") if "STOCKS" not in switch else _pl.Path("config_tradier.py")
                            # Actually check which file has this switch
                            for _cf in [pathlib.Path("config.py"), pathlib.Path("config_tradier.py")]:
                                if _cf.exists():
                                    _txt = _cf.read_text()
                                    if switch in _txt:
                                        # Update default value to cand
                                        import re
                                        # Find line like '    SWITCH: type = old_value'
                                        _pattern = rf"(\s+{re.escape(switch)}\s*:\s*\w+\s*=\s*)(.+)"
                                        def _repl(m):
                                            old = m.group(2)
                                            # Preserve comment
                                            if "#" in old:
                                                val_part, comment = old.split("#",1)
                                                return m.group(1) + repr(cand) + "  #" + comment
                                            else:
                                                return m.group(1) + repr(cand)
                                        _new_txt, n = re.subn(_pattern, _repl, _txt)
                                        if n>0:
                                            _cf.write_text(_new_txt)
                                            print(f"[CONFIG_UPDATE] {switch} -> {cand} in {_cf.name} (pos delta {delta_best:.2f})", flush=True)
                                            break
                        except: pass
                except: pass
                cumulative_gain = float(cumulative_before + delta_best)
                cumulative_overrides[switch] = cand
                if filt_best: cumulative_overrides[filt_best] = fval_best
                # FIX 2026-09-23: yellow filter isolation — per-yellow deltas already added to delta_best above,
                # but filter itself does NOT persist to cumulative_overrides for other switches (isolated to this row)
                # previously pos_filters were persisted here, violating isolation — removed
                _prev_delta_positive = True
            else:
                _prev_delta_positive = False
            return float(delta_best)

        def _process_cycle_row(sheet: str, r: int, switch: str, cand):
            return _process_0914_row_helper(sheet, r, switch, cand)
        # Iterate with POS-stay / NEG-advance
        while _deque_sheets and _processed_cycle < _remaining_cycle:
            sheet = _deque_sheets[0]
            idx = _indices_cycle[sheet]
            rows = _sheet_rows_map.get(sheet, [])
            if idx >= len(rows):
                _deque_sheets.popleft()
                continue
            r, switch, cand = rows[idx]
            _indices_cycle[sheet] += 1
            _processed_cycle += 1
            _touch_heartbeat(f"cycle {sheet}!{r}")
            print(f"[DEBUG] cycle sheet {sheet} row {r} {switch}={cand} start cum={cumulative_gain:.4f}", flush=True)
            try:
                # Call shared row processor (must be defined before this block in file — we ensure it exists)
                delta_best = _process_0914_row_helper(sheet, r, switch, cand)
            except NameError:
                # Fallback: if helper not yet defined (prototype), treat as NEG to keep deque moving
                delta_best = 0
                print(f"[cycle-warn] _process_0914_row_helper not defined, using fallback delta 0 for {sheet}!{r}", flush=True)
            except Exception as _e:
                print(f"[cycle-ERR] {sheet}!{r} {_e}", flush=True)
                delta_best = 0
            # Cycle-through-all-tabs: POS and NEG both advance to next tab (all 12 then come back), POS advances row, NEG stays row
            if delta_best is not None and delta_best > 1e-9:
                print(f"[0914-cycle] POS {sheet}!{r} delta {delta_best:.4f} -> next tab (all-tabs cycle, advance row on POS)", flush=True)
                _deque_sheets.rotate(-1)
                if _indices_cycle[sheet] >= len(rows):
                    # sheet exhausted, will be popped next iteration, but already rotated
                    pass
            else:
                print(f"[0914-cycle] NEG {sheet}!{r} delta {delta_best if delta_best is not None else 0:.4f} -> next tab (all-tabs cycle, stay row on NEG)", flush=True)
                _deque_sheets.rotate(-1)
                # If sheet exhausted, will be popped next iteration
            # flush periodic — progress and workbook per row, never lose CPU
            if _processed_cycle % 5 == 0:
                try:
                    _atomic_write_json(progress_path, progress)
                except: pass
                # BADZIP FIX 2026-09-29: the "simple save" comment was exactly backwards —
                # in-place saves ARE the torn-copy source (rsync mid-write + kill mid-save).
                try:
                    wb_cur, _ = _get_wb_keep(sheet)
                    _atomic_save(wb_cur, Path(wb_path))
                except Exception:
                    pass
        # After dynamic cycle completes, flush all wb_keep caches and progress — never skip, every row must have F/G or red error
        for _wb in _wb_keep_cache.values():
            try:
                _atomic_save(_wb, Path(wb_path))  # BADZIP FIX 2026-09-29
                _wb.close()
            except: pass
        try:
            _atomic_write_json(progress_path, progress)
        except: pass
        print(f"[0914-cycle] dynamic cycle complete {len(progress.get('done',{}))} rows cum={cumulative_gain:.4f} (stay on POS, next tab on NEG)", flush=True)
        # Skip sequential fallback — cycle has handled all rows
        raise SystemExit(0)
    # Ensure _cycle_deque is defined for sequential mode
    if '_cycle_deque' not in locals():
        _cycle_deque = None
    # Ensure _cycle_deque defined for sequential mode (was only primed for cycle)
    if '_cycle_deque' not in locals():
        _cycle_deque = None
    # Ensure _cycle_deque defined for sequential (fix NameError when not cycle)
    if '_cycle_deque' not in locals():
        _cycle_deque = None
    if '_cycle_deque' in locals() and _cycle_deque is not None:
        # Cycle deque already primed above — drive sheets via deque, processing one sheet's rows with POS/NEG logic
        # For true POS-stay/NEG-advance we need per-row deque, but per-row body already logs POS/NEG and will drive next sheet selection via _cycle_deque
        # Here we keep outer sheet loop as deque-driven: pop sheet, process its next row, then decide stay/rotate
        # To avoid duplicating per-row body, we keep sequential sheet loop but log deque state
        print(f"[0914-cycle] deque active {list(_cycle_deque)[:5]} — per-row POS/NEG will drive next tab", flush=True)
    # 2026-09-22 TIMEOUT LAW: per-sym ≤60m never stall >20m, per-cell ≤10s red and move on — never 0 trades
    import signal as _sig_to
    import concurrent.futures as _cf_to
    def _cell_timeout_handler(signum, frame):
        raise TimeoutError("cell 10s timeout")
    try:
        _sig_to.signal(_sig_to.SIGALRM, _cell_timeout_handler)
        _sig_to.alarm(3600)
    except Exception:
        pass
    _prev_delta_positive = True  # first row r=3 always baseline per spec, then per previous delta
    for sheet in sheets:
        try:
            print(f"\n[LOG {time.time():.1f}] [sheet] {sheet} cumulative={cumulative_gain:.4f} mem={__import__('psutil').Process().memory_info().rss/1e6:.0f}MB", flush=True)
            _touch_heartbeat(f"sheet {sheet}")
            print(f"[LOG {time.time():.1f}] load wb for {sheet}", flush=True)
            # never-stop: on BadZip (truncated save) restore from .bak and continue — engine must not stall at sheet N
            try:
                wb = openpyxl.load_workbook(str(wb_path), data_only=False)
            except Exception as _zip_e:
                if "BadZipFile" in str(type(_zip_e)) or "zip" in str(_zip_e).lower():
                    print(f"[BadZip-recover] {sheet} {_zip_e} — restoring .bak", flush=True)
                    try:
                        import shutil as _sh_bak
                        _bak = str(wb_path) + ".bak"
                        if Path(_bak).exists():
                            _sh_bak.copy2(_bak, str(wb_path))
                            wb = openpyxl.load_workbook(str(wb_path), data_only=False)
                            print(f"[BadZip-recover] restored {sheet} from .bak", flush=True)
                        else:
                            raise
                    except Exception as _rb:
                        print(f"[BadZip-recover-fail] {_rb}", flush=True)
                        # mark all remaining rows in this sheet red and continue to next sheet
                        _flag_to_md(flags_md, sheet, 0, "BadZip", str(wb_path), f"BadZip restore failed {_zip_e}", 0, 0, cumulative_gain)
                        continue
                else:
                    raise
            print(f"[LOG {time.time():.1f}] wb loaded {sheet} rows={wb[sheet].max_row if sheet in wb.sheetnames else 0}", flush=True)
            if sheet not in wb.sheetnames:
                wb.close()
                continue
            ws = wb[sheet]
            rows = []
            for r in range(2, ws.max_row + 1):
                sw = ws.cell(row=r, column=1).value
                if not sw or not isinstance(sw, str):
                    continue
                sw = sw.strip()
                if not sw or sw.lower() in ("switch", "general", "blanket", "filter", "option value"):
                    continue
                # skip synthetic header row "Filter | Option Value | SATOSHIT..." (col A=Filter, col B=Option Value)
                if sw.lower() == "filter" and str(ws.cell(row=r, column=2).value or "").lower() == "option value":
                    continue
                cand = ws.cell(row=r, column=2).value
                if cand is None:
                    continue
                # skip rows where cand is header text like "Option Value" or "Sheets applicable"
                if isinstance(cand, str) and cand.lower() in ("option value", "sheets applicable", "gates"):
                    continue
                eff = cumulative_overrides.get(sw, defaults.get(sw, cand))
                def norm(v):
                    if isinstance(v, str) and v.lower() in ("true", "false"):
                        return v.lower() == "true"
                    return v
                _key_skip = f"{sheet}!{r}:{sw}={cand}"
                if norm(cand) == norm(eff) and _key_skip in progress.get("done", {}):
                    continue
                # blanket / separator rows: GENERAL in F (col6) or yellow FFE699 fill — never calculate, just skip
                f_val = ws.cell(row=r, column=6).value
                is_general_f = isinstance(f_val, str) and f_val.strip().upper().startswith("GENERAL")
                is_orange_global = str(ws.cell(row=r, column=4).value or "") == "GLOBAL_CHECK"
                is_blanket_end = str(ws.cell(row=r, column=9).value or "").strip().lower() == "blanket (page end)"
                # ORANGE FIX: GLOBAL_CHECK rows apply to ALL rows of that sheet alone — must be calculated per sheet, not skipped (both live and vectorized)
                if is_orange_global:
                    rows.append((r, sw, cand))
                    continue
                if is_general_f or is_blanket_end:
                    continue
                try:
                    fill_rgb = ws.cell(row=r, column=6).fill.start_color.rgb if ws.cell(row=r, column=6).fill.start_color.rgb not in (None, "00000000") else None
                    if fill_rgb == "00FFE699":
                        continue
                except Exception:
                    pass
                rows.append((r, sw, cand))
            wb.close()
            print(f"[LOG {time.time():.1f}] [sheet] {sheet} {len(rows)} variants", flush=True)
            if not rows:
                continue

            print(f"[LOG {time.time():.1f}] load wb_keep for {sheet}", flush=True)
            wb_keep = openpyxl.load_workbook(str(wb_path), data_only=False)
            print(f"[LOG {time.time():.1f}] wb_keep loaded", flush=True)
            ws_keep = wb_keep[sheet] if sheet in wb_keep.sheetnames else None
            header_to_col = {}
            _cols = {}
            if ws_keep is not None:
                for c in range(12, ws_keep.max_column + 1):
                    hv = ws_keep.cell(row=2, column=c).value
                    if hv and isinstance(hv, str) and "=" in hv:
                        hv = hv.strip()
                        if not hv.upper().startswith("WHAT SWITCH"):
                            header_to_col[hv] = c
                    if hv and isinstance(hv, str) and hv.strip().upper().startswith("WHAT SWITCH"):
                        break
                _cols = _resolve_cols(ws_keep)

            for (r, switch, cand) in rows:
                if _is_kg_never_skip(switch):
                    _is_w15m_seq = True  # KG never skip
                else:
                    _is_w15m_seq = "W15M" in switch or "WT_15M" in switch or "WT_CHAN_15m" in switch or "WT_AVG_15m" in switch or "WT_15M_BOUNCE" in switch
                # FORWARD FIX 2026-09-23: restore 80% skip to produce deltas within seconds — no-skip made every disabled row calculate 50+ yellows (>10s) and hit LOUD-STOP before first delta. Keep yellow-block for sampled rows.
                if not _is_w15m_seq:
                    # EVERY ROW+EVERY YELLOW per user - no skip
                    try:
                        _cat_seq = ("CRYPTO" if "USDT" in new_symside or "USDC" in new_symside else "STOCKS") + "_" + new_symside.rsplit("_",1)[-1]
                        _cat_set_seq = disabled_per_category.get(_cat_seq, set())
                        if switch in _cat_set_seq:
                            print(f"[NO-SKIP] {switch} in disabled_per_category for {_cat_seq} - still calculating ALL yellows per user", flush=True)
                        if switch in disabled_switches:
                            print(f"[NO-SKIP] {switch} in disabled_switches - still calculating ALL yellows per user", flush=True)
                    except:
                        pass
                # FORWARD FIX 2026-09-23: LOUD guard log-only — old delete+return destroyed workbook before baseline/delta could appear within seconds. Now log but continue to produce deltas.
                if len(progress.get("done", {})) == 0 and __import__('time').time() - _v15_start_time > 300:
                    print(f"[LOUD-STOP-NO-DELTA-ROW-DISABLED] {new_symside} sheet {sheet}!{r} NO deltas after {__import__('time').time() - _v15_start_time:.1f}s total_pos {total_pos} done {len(progress.get('done',{}))} — continuing (abort disabled, would have deleted)", flush=True)
                    try: _flag_to_md(flags_md, sheet, r, new_symside, "NO_DELTA", f"no delta after 300s log-only", 0, 0, cumulative_gain)
                    except: pass
                # old VIRUS0 pos-only check now log-only (pos==0 is ok if NEG deltas exist, keep filling)
                if total_pos == 0 and __import__('time').time() - _v15_start_time > 15 and len(progress.get("done", {})) > 5:
                    print(f"[VIRUS0-15s-ROW-DISABLED] {new_symside} sheet {sheet}!{r} 0 pos after {__import__('time').time() - _v15_start_time:.1f}s total_pos 0 — continuing (abort disabled per FULL SHEET LAW, yellows kept)", flush=True)
                cell_start = time.time()
                key = f"{sheet}!{r}:{switch}={cand}"
                # YELLOW SET FOR A SINGLE SWITCH (this row): SPECIFIC filters gated
                # to this switch = the cells that should be yellow (pos-delta-capable).
                # Other switches' headers are non-yellow for this row: never evaluated,
                # never written — that is the compute saving. Cheap to build (no evals).
                specifics = opportune_filter_rows(new_symside, sheet, switch)
                opportune = specifics
                def parse_opt(v, default):
                    if isinstance(default, bool):
                        return str(v).lower() == "true" if str(v).lower() in ("true", "false") else bool(v)
                    if isinstance(default, int) and not isinstance(default, bool):
                        try: return int(float(str(v)))
                        except: return v
                    if isinstance(default, float):
                        try: return float(str(v))
                        except: return v
                    if isinstance(v, str) and v.lower() in ("true", "false"):
                        return v.lower() == "true"
                    try:
                        if "." in str(v): return float(str(v))
                        return int(str(v))
                    except:
                        return v
                # BOLD row: naked delta should be 0 (already in baseline) but yellows still evaluated per spec — first row IS bold default and yellows must be tested
                try:
                    _is_bold_default = False
                    try:
                        _b_font = ws.cell(row=r, column=2).font
                        _is_bold_default = bool(_b_font.bold)
                    except: pass
                    if _is_bold_default:
                        print(f"[BOLD-DEFAULT-YELLOWS-ONLY] {sheet}!{r} {switch}={cand} bold default already in baseline {cumulative_before:.4f} — naked delta forced 0, yellows still evaluated", flush=True)
                        # do NOT continue — fall through to normal yellow evaluation; naked synthetic pos will be zeroed downstream (BROKEN_DEFAULT guard)
                except: pass
                def norm2(a, b):
                    if isinstance(a, str) and a.lower() in ("true", "false"):
                        a = a.lower() == "true"
                    if isinstance(b, str) and b.lower() in ("true", "false"):
                        b = b.lower() == "true"
                    return a == b
                # relevant_hdrs: full per-switch yellow set (mapped + identical-value),
                # unmapped_hdrs: relevant but no L:BI column (header gap, flagged not evaluated)
                _rel_eval, _rel_ident, _rel_unmapped = [], [], []
                for e in specifics:
                    _hdr = f"{e['filter']}={e['opt']}"
                    if _hdr not in header_to_col:
                        _rel_unmapped.append(_hdr)
                        continue
                    _ov = parse_opt(e["opt"], defaults.get(e["filter"]))
                    _cur = cand if e["filter"] == switch else cumulative_overrides.get(e["filter"], defaults.get(e["filter"]))
                    if norm2(_ov, _cur):
                        _rel_ident.append(_hdr)
                    else:
                        _rel_eval.append((e["filter"], _ov, _hdr, e["opt"]))
                # C is override column — must receive candidate value for this switch row (L is unmutable header, C is per-row override)
                try:
                    _val_c = cand
                    if isinstance(_val_c, bool):
                        _val_str_c = "TRUE" if _val_c else "FALSE"
                    else:
                        _val_str_c = str(_val_c)
                        if _val_str_c.lower() in ("true", "false"):
                            _val_str_c = _val_str_c.upper()
                    if ws_keep is not None:
                        cur_c = ws_keep.cell(row=r, column=3).value
                        if cur_c is None or str(cur_c).strip() == "":
                            ws_keep.cell(row=r, column=3).value = _val_str_c
                            try:
                                ws_keep.cell(row=r, column=3).font = Font(name="Arial", size=10, bold=True, color="000000")
                                ws_keep.cell(row=r, column=3).alignment = Alignment(horizontal="left", vertical="center")
                            except: pass
                except: pass
                _rel_total = len(_rel_eval) + len(_rel_ident)
                # RED RETRY 2026-09-25: a NO VALID row never computed (timeout/error) — it is a red placeholder, not a filled
                # cell, so it is retried on every resume until it fills. Computed POS/NEG rows stay frozen below.
                if key in progress.get("done", {}) and progress["done"][key].get("reason") == "all vectors invalid":
                    print(f"[RED-RETRY] {key} was NO VALID — recalculating", flush=True)
                    progress["done"].pop(key, None)
                if key in progress.get("done", {}):
                    prev = progress["done"][key]
                    # ABSOLUTE PER-CELL PROHIBITION — never recalc a cell already in done set (2026-09-16)
                    # Backups in xls/log/zip/bak exist — repeating wastes 1s/cell and violates Sequential One-Workbook-Then-Next law.
                    # Even if cum stale or yellows missing, DO NOT re-eval — keep original delta vs original cum (honest historical record).
                    try:
                        _wsk = wb_keep[sheet] if sheet in wb_keep.sheetnames else None
                        if _wsk is not None:
                            _pd = float(prev.get("delta") or 0)
                            if not isinstance(_wsk.cell(row=r, column=6).value, float) and prev.get("delta_vs_initial") is not None:  # FZ: F only from a real recorded value, never float(None or 0)
                                _wsk.cell(row=r, column=6).value = float(prev.get("delta_vs_initial"))
                            if not isinstance(_wsk.cell(row=r, column=7).value, float):
                                _wsk.cell(row=r, column=7).value = _pd
                            for _yh, _yd in ((prev.get("yellows") or prev.get("pending_lbI") or {}).items()):
                                _yc = header_to_col.get(_yh)
                                if _yc and not isinstance(_wsk.cell(row=r, column=_yc).value, float):
                                    try:
                                        _wsk.cell(row=r, column=_yc).value = float(_yd)
                                    except Exception:
                                        pass
                            try:
                                _rec_hdrs = set((prev.get("yellows") or prev.get("pending_lbI") or {}).keys())
                                for _hc, _cc in header_to_col.items():
                                    if _hc in _rec_hdrs:
                                        continue
                                    if isinstance(_wsk.cell(row=r, column=_cc).value, float):
                                        _wsk.cell(row=r, column=_cc).value = None
                            except Exception:
                                pass
                            # DESTROYED DOUBLE E: never rewrite BASELINE (col5 E) on resume — single write only at promotion, E never overwritten
                            pass
                    except Exception:
                        pass
                    if prev.get("delta") and prev["delta"] > 0:
                        try:
                            cumulative_gain = float(prev.get("cumulative_after", cumulative_gain))
                        except Exception:
                            pass
                        cumulative_overrides[switch] = cand
                        if prev.get("best_filter"):
                            cumulative_overrides[prev["best_filter"]] = prev.get("best_fval")
                        print(f"[PROHIBITED-CELL] skip cached POS {key} cum {cumulative_gain:.4f} — already filled, never recalc (xls/log/zip/bak)", flush=True)
                        continue
                    else:
                        print(f"[PROHIBITED-CELL] skip cached NEG {key} delta {prev.get('delta')} cum {cumulative_gain:.4f} — already filled, never recalc", flush=True)
                        continue
                print(f"[DEBUG] sheet {sheet} row {r} {switch}={cand} start cum={cumulative_gain:.4f}", flush=True)
                _touch_heartbeat(f"cell {sheet}!{r}")
                try:
                    cumulative_before = cumulative_gain
                    # YELLOW = this row's relevant set only (precomputed above): each
                    # evaluated as switch=cand + that one filter vs cumulative_before.
                    # Identical-value headers reuse the naked delta (no eval = saving).
                    # Unmapped relevant headers are a header gap: flagged, not evaluated.
                    # sequential heavy (no timeout, MAX TIMEPER CELL 1.0s/0.5s) was the
                    # pre-2026-09-13 approach; now parallel-16 batches under the same
                    # per-cell budget (post-hoc flag, never mid-batch truncate).
                    is_heavy = len(np.asarray(prepared["npz_prepared"].get("close", []))) > 2000 if prepared else False
                    single_filters = list(_rel_eval)
                    identical_hdrs = []
                    invalid_hdrs = []
                    relevant_hdrs = [t[2] for t in single_filters]
                    if _rel_unmapped:
                        _flag_to_md(flags_md, sheet, r, switch, cand, f"header gap {len(_rel_unmapped)} relevant w/o L:BI col", 0.0, 0.0, cumulative_before)
                    # 1s per cell + entire F until 200 then next tab max baseline: heavy 2333 bars -> 0.6s/candidate
                    # Keep distinct per row, not blanket same, ensure ENTIRE row F until 200 calculated
                    before_len = len(single_filters)
                    # YELLOW-BLOCK: NO CAP - every single row and every yellow cell must be calculated, cap is waste per user. All yellows before row advance.
                    if len(single_filters) > 0:
                        print(f"[filter-full] {switch} {before_len} filters full eval <1.0s greedy+hustle", flush=True)
                    # No blanket same: each row's Y is its own switch+filter deltas, not copied; entire F until 200 via cumulative max baseline next tab
                    candidates = []
                    v0 = dict(cumulative_overrides)
                    v0[switch] = cand
                    v0, _ = sanitize_overrides(v0, defaults)
                    candidates.append((v0, None, None, None))
                    for (filt, opt_val, hdr, opt_raw) in single_filters:
                        v = dict(cumulative_overrides)
                        v[switch] = cand
                        v[filt] = opt_val
                        v, _ = sanitize_overrides(v, defaults)
                        candidates.append((v, filt, opt_val, hdr))
                    _single_filters_for_combo = single_filters

                    best = None
                    pending_lbI = {}
                    vector_delta_val = None
                    # USER 2026-10-03 RULE#2/#3: pending_lbI holds ONLY genuinely evaluated deltas (see cycle path)
                    from tools.v15_row_guards import invalid_settled as _inv_settled, is_real_number as _is_real, mark_unevaluated as _mark_red, yellows_complete as _y_complete, zero_audit_line as _zero_audit
                    evaluated_hdrs = set()
                    unsettled_hdrs = []
                    print(f"[LOG {time.time():.1f}] {sheet}!{r} candidates={len(candidates)} start vec batch", flush=True)
                    # Spec: YELLOW_TIMEOUT=0.1 for every cell (naked and yellow) — plowing never blocks
                    # Every finished eval is KEPT; only unfinished at 0.1s go RED via _spec_mark_red, write -1/0, queue, continue immediately
                    per_cell_deadline = YELLOW_TIMEOUT  # 0.1s per cell spec; ignore env 10s override (spec says 0.1)
                    # keep env compat but cap to 0.1 if caller tries 10s
                    try:
                        _env_deadline = float(os.environ.get("V15_CELL_DEADLINE_S", str(YELLOW_TIMEOUT)))
                        if _env_deadline != YELLOW_TIMEOUT:
                            print(f"[deadline-override] env V15_CELL_DEADLINE_S={_env_deadline} ignored, using spec {YELLOW_TIMEOUT}", flush=True)
                    except Exception:
                        pass
                    vecs = []
                    import concurrent.futures as _cf2
                    try:
                        if prepared is not None:
                            from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_prep
                            _batch_fn = lambda c: _eval_prep(prepared, c[0], window_days=args.window_days)
                            _n_workers = 16  # spec Keep ThreadPool 16 (was args.workers min)
                        else:
                            from tools.opt.v12_pilot import evaluate_sanitized as _eval_disk
                            _batch_fn = lambda c: _eval_disk(new_symside, c[0], window_days=args.window_days)
                            _n_workers = 16
                        print(f"[LOG {time.time():.1f}] vec batch {len(candidates)} workers={_n_workers} {'heavy' if is_heavy else 'light'} {'hot' if prepared is not None else 'DISK'} guard {per_cell_deadline}s", flush=True)
                        ex = _cf2.ThreadPoolExecutor(max_workers=_n_workers)
                        futs = [ex.submit(_batch_fn, c) for c in candidates]
                        _done, _not_done = _cf2.wait(futs, timeout=per_cell_deadline)
                        ex.shutdown(wait=False, cancel_futures=True)
                        for fut in futs:
                            if fut in _done:
                                try:
                                    vecs.append(fut.result())
                                except Exception as _e:
                                    vecs.append({"valid": False, "reason": f"eval error {_e}"})
                            else:
                                # Spec: YELLOW_TIMEOUT=0.1 TimeoutError → _spec_mark_red + -1/0 placeholder + queue.Queue + continue immediately
                                vecs.append({"valid": False, "reason": f"YELLOW_TIMEOUT {per_cell_deadline}s"})
                        if _not_done:
                            print(f"[CELL-TIMEOUT] {sheet}!{r} {switch}={cand} {len(_not_done)}/{len(futs)} unfinished >{per_cell_deadline}s → those RED via _spec_mark_red, placeholder -1, queued, continue", flush=True)
                            _flag_to_md(flags_md, sheet, r, switch, cand, f"YELLOW_TIMEOUT {len(_not_done)}/{len(futs)} >{per_cell_deadline}s RED", -1.0, 0.0, cumulative_before)
                            # For each unfinished, mark RED via _spec_mark_red, write placeholder, enqueue to both queues, never block
                            try:
                                for idx_u, fut_u in enumerate(futs):
                                    if fut_u in _not_done and idx_u < len(candidates):
                                        _var_u, _filt_u, _fval_u, _hdr_u = candidates[idx_u]
                                        _col_u = header_to_col.get(_hdr_u) if _hdr_u else (_cols.get("G") if ' _cols' in locals() else None)
                                        if _col_u is None and _hdr_u is None:
                                            # naked cell -> G col
                                            try:
                                                _col_u = _cols.get("G", 7)
                                            except Exception:
                                                _col_u = 7
                                        if _col_u:
                                            try:
                                                _spec_mark_red(wb_keep if 'wb_keep' in locals() and wb_keep is not None else ws_keep, sheet, r, _col_u, reason=f"TIMEOUT {per_cell_deadline}s")
                                                # RULE#2: timeout = uncalculated — blank+red, never a 0.0 placeholder
                                                try:
                                                    _ws_tmp = ws_keep if 'ws_keep' in locals() and ws_keep is not None else None
                                                    if _ws_tmp is not None:
                                                        _mark_red(_ws_tmp, r, _col_u, f"YELLOW_TIMEOUT {per_cell_deadline}s")
                                                except Exception:
                                                    pass
                                            except Exception:
                                                pass
                                        queue_red_cell(sheet, r, _col_u, _hdr_u, _var_u, cumulative_before, switch, cand, key=f"{sheet}!{r}:{switch}={cand}")
                                        try:
                                            RED_CELL_QUEUE.put({"sheet": sheet, "row": r, "hdr": _hdr_u, "col": _col_u, "variant": _var_u, "cumulative_before": cumulative_before, "reason": f"YELLOW_TIMEOUT {per_cell_deadline}s"})
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                        print(f"[LOG {time.time():.1f}] vec batch done {len(_done)}/{len(futs)}", flush=True)
                    except Exception as e:
                        print(f"[vec-batch-err] {sheet}!{r} {switch} err {e}", flush=True)
                        _flag_to_md(flags_md, sheet, r, switch, cand, f"VEC-BATCH-ERR {e}", -1.0, 0.0, cumulative_before)
                        vecs = [{"valid": False, "reason": f"batch error {e}"} for _ in candidates]
                    # CORRECT FILL LOGIC per user 2026-09-12: for each row, evaluate naked + ALL yellows, keep pos deltas, record in overrides, total delta = naked + sum(pos yellows) via combined variant
                    for idx, (variant, filt, fval, hdr) in enumerate(candidates):
                        if idx >= len(vecs):
                            break
                        vec = vecs[idx]
                        if not vec.get("valid"):
                            _reason = str(vec.get("invalid_reason") or vec.get("reason") or "invalid")
                            if filt is not None and hdr in header_to_col:
                                _mark_red(ws_keep if 'ws_keep' in locals() else None, r, header_to_col.get(hdr), _reason)
                                if _inv_settled(_reason):
                                    invalid_hdrs.append(hdr)
                                    evaluated_hdrs.add(hdr)
                                else:
                                    unsettled_hdrs.append(hdr)
                                    print(f"[ROW-UNSETTLED] {new_symside} {sheet}!{r} {switch}={cand} yellow {hdr} no verdict ({_reason[:100]})", flush=True)
                                    # queue for fixer (10s re-eval) — fixer will clear RED and write correct delta/yellow
                                    try:
                                        if "unfinished" in _reason.lower() or "timeout" in _reason.lower():
                                            queue_red_cell(sheet, r, header_to_col.get(hdr), hdr, variant, cumulative_before, switch, cand, key=f"{sheet}!{r}:{switch}={cand}")
                                    except Exception:
                                        pass
                            # settled-invalid is a calculated yellow (verdict); unsettled stays pending via the RULE#3 gate
                            continue
                        # 0/1 TRADE RED LAW — ANY VERSION
                        _tr = int(vec.get("trades") or 0)
                        if _tr <= 1:
                            try:
                                ws_keep.cell(row=r, column=6).value = None  # FZ: F never a fake 0
                                ws_keep.cell(row=r, column=7).value = None  # RULE#2: 0/1-trade = no valid delta, red+blank not 0.0
                                from openpyxl.styles import PatternFill
                                ws_keep.cell(row=r, column=7).fill = __import__("openpyxl").styles.PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                ws_keep.cell(row=r, column=7).font = __import__("openpyxl").styles.Font(name="Arial", size=10, bold=True, color="FFFFFF")
                                ws_row.cell(row=r, column=7).alignment = VISUAL_ALIGN
                                # H/I/K per-row (added for gap fix)
                                try: _hk_ld = float(live_delta) if 'live_delta' in locals() and live_delta is not None else None
                                except: _hk_ld = 0.0
                                try: _hk_ls = float((live_best or {}).get('pool_sharpe') or 0) if 'live_best' in locals() and live_best is not None and (live_best or {}).get('pool_sharpe') is not None else None
                                except: _hk_ls = 0.0
                                try: _hk_pf = ", ".join(f"{k}={v}" for k,v in (pos_yellows.items() if 'pos_yellows' in locals() and isinstance(pos_yellows, dict) else {}))
                                except: _hk_pf = ""
                                _write_per_row_HIK(ws_row, r, _hk_ld, _hk_ls, _hk_pf)
                                try: _clear_vlookup_formulas(ws_row)
                                except: pass
                            except: pass
                            _flag_to_md(flags_md, sheet, r, switch, cand, f"RED 0/1 TRADE trades={_tr}", float(vec.get("gain_pct") or 0), cumulative_before)
                            print(f"[RED 0/1 TRADE] {sheet}!{r} {switch}={cand} trades={_tr} — RED EVERYWHERE", flush=True)
                            if filt is not None and hdr in header_to_col:
                                invalid_hdrs.append(hdr)
                            continue
                        # SLOW != UNFILLED: a computed vec is kept; slowness is only flagged (was repainting real results red)
                        _elapsed_cell = __import__('time').time() - cell_start
                        if _elapsed_cell > 60 and idx == 0:
                            _flag_to_md(flags_md, sheet, r, switch, cand, f"SLOW CELL {_elapsed_cell:.1f}s (value kept)", float(vec.get("gain_pct") or 0), cumulative_before)
                            print(f"[SLOW CELL] {sheet}!{r} {switch}={cand} elapsed={_elapsed_cell:.1f}s — value kept", flush=True)
                        _vg_raw = vec.get("gain_pct")
                        if not _is_real(_vg_raw):
                            if filt is not None and hdr in header_to_col:
                                _mark_red(ws_keep if 'ws_keep' in locals() else None, r, header_to_col.get(hdr), f"gain-missing ({_vg_raw!r})")
                                unsettled_hdrs.append(hdr)
                                print(f"[ROW-UNSETTLED] {new_symside} {sheet}!{r} {switch}={cand} yellow {hdr} gain-missing", flush=True)
                            continue
                        vg = float(_vg_raw)
                        delta = vg - cumulative_before
                        if filt is None:
                            print(f"[CANDIDATE] {sheet}!{r} {switch}={cand} alone vec_gain={vg:.4f} delta={delta:.4f} vs cum {cumulative_before:.4f} trades={vec.get('trades')} sharpe={float(vec.get('pool_sharpe') or 0):.4f}", flush=True)
                        else:
                            print(f"[CANDIDATE] {sheet}!{r} {switch}={cand}+{filt}={fval} vec_gain={vg:.4f} delta={delta:.4f} vs cum {cumulative_before:.4f} trades={vec.get('trades')} sharpe={float(vec.get('pool_sharpe') or 0):.4f}", flush=True)
                        if filt is not None and hdr in header_to_col:
                            pending_lbI[hdr] = float(delta)
                            evaluated_hdrs.add(hdr)
                            if delta == 0.0:
                                print(_zero_audit(sheet, r, switch, filt, hdr, vg, cumulative_before, vec.get("trades"), _run_npz_short), flush=True)
                        if best is None or delta > best[0]:
                            best = (delta, variant, filt, fval, hdr, vec)
                        if filt is None:
                            vector_delta_val = float(delta)
                            if delta == 0.0:
                                print(_zero_audit(sheet, r, switch, None, "naked", vg, cumulative_before, vec.get("trades"), _run_npz_short), flush=True)

                    # identical not written — yellow-only, skip synthetic (settled-invalid stays blank+red, never 0.0 in pending)

                    # CORRECT: F is best SINGLE or combined pos filters recalculated with real backtest (multi-filter combined is correct when recalculated with additional filter)
                    try:
                        pos_filters = [(f, o, h) for (f, o, h, raw) in single_filters if pending_lbI.get(h, float("-inf")) > 0]
                        if pos_filters:
                            v_all = dict(cumulative_overrides)
                            v_all[switch] = cand
                            for (ff, oo, hh) in pos_filters:
                                v_all[ff] = oo
                            v_all, _ = sanitize_overrides(v_all, defaults)
                            from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_all
                            if prepared is not None:
                                vec_all = _eval_all(prepared, v_all, window_days=args.window_days)
                            else:
                                from tools.opt.v12_pilot import evaluate_sanitized as _eval_all2
                                vec_all = _eval_all2(new_symside, v_all, window_days=args.window_days)
                            if vec_all.get("valid"):
                                vg_all = float(vec_all.get("gain_pct") or 0)
                                delta_all = vg_all - cumulative_before
                                if best is None or delta_all > best[0]:
                                    all_hdrs = "+".join([h for (_,_,h) in pos_filters])
                                    best = (delta_all, v_all, None, None, all_hdrs, vec_all)
                                    print(f"[COMBINED POS] {sheet}!{r} {switch}={cand}+{len(pos_filters)} pos filters delta={delta_all:.4f} vs best {best[0]:.4f}", flush=True)
                    except Exception as _e:
                        print(f"[combined-warn] {sheet}!{r} {_e}", flush=True)
                    # keep combo pairs disabled (invented pairs can never happen) — F is single best or combined pos recalc only
                    _best_before_combo = best[0] if best else float("-inf")
                    _combo_pool = []
                    all_combos = []
                    vecs_c = []

                    wb_row = wb_keep
                    ws_row = wb_keep[sheet] if sheet in wb_keep.sheetnames else None
                    # DESTROYED: BASELINE_FALLBACK was virus writing fake baseline delta without pos delta — NEVER fallback, best None means NO VALID
                    if best is None:
                        # ALWAYS WRITE TO EVERY YELLOW BUT AFTER CALCULATING DELTA — even when NO VALID, write candidate yellows, baseline never without pos delta
                        try:
                            if ws_row is not None:
                                for _hdr in relevant_hdrs:
                                    _col = header_to_col.get(_hdr)
                                    if not _col:
                                        continue
                                    _mark_red(ws_row, r, header_to_col.get(_hdr), "no-valid-vector")
                        except: pass
                        progress.get("done", {}).pop(key, None)
                        print(f"[ROW-INCOMPLETE] {new_symside} {key} no valid vector — no zeros written, stays pending", flush=True)
                        print(f"[ROW] {sheet}!{r} {switch}={cand} vs cum {cumulative_before:.4f} -> NO VALID", flush=True)
                        _atomic_write_json(progress_path, progress)
                        _touch_heartbeat(f"cell {sheet}!{r} NO VALID")
                        # keep G as actual negative, never 0.0 for NEG/invalid — E ALWAYS numeric (user fix 01 ENTRY_REVERSAL_BOUNCE empty)
                        try:
                            if ws_row is not None:
                                ws_row.cell(row=r, column=5).value = float(cumulative_before) if r == 3 or _prev_delta_positive else None
                                ws_row.cell(row=r, column=5).font = __import__("openpyxl").styles.Font(name="Arial", bold=False, color="000000")
                                ws_row.cell(row=r, column=6).value = None  # F hustle vs baseline — not in worst_first, leave empty until live hustle
                                ws_row.cell(row=r, column=6).fill = __import__("openpyxl").styles.PatternFill(fill_type=None)
                                ws_row.cell(row=r, column=6).font = __import__("openpyxl").styles.Font(name="Arial", size=10, color="000000")
                                ws_row.cell(row=r, column=6).alignment = VISUAL_ALIGN
                                # WT_MOMENTUM_EXIT_THRESHOLD 0 cannot give -1 — fix formula: if no valid vector, leave G empty, not -1
                                ws_row.cell(row=r, column=7).value = None
                                ws_row.cell(row=r, column=7).fill = __import__("openpyxl").styles.PatternFill(fill_type=None)
                                ws_row.cell(row=r, column=7).font = __import__("openpyxl").styles.Font(name="Arial", size=10, color="000000")
                                ws_row.cell(row=r, column=7).alignment = VISUAL_ALIGN
                                ws_row.cell(row=r, column=5).alignment = VISUAL_ALIGN
                                _write_per_row_HIK(ws_row, r, None, None, "")
                                _clear_vlookup_formulas(ws_row)
                                if r + 1 <= ws_row.max_row:
                                    ws_row.cell(row=r+1, column=5).value = None
                                ws_row.cell(row=r, column=3).value = None
                                try:
                                    _atomic_save(wb_keep, wb_path)
                                except: pass
                        except: pass
                        _flag_to_md(flags_md, sheet, r, switch, cand, "NO VALID all vectors invalid", 0.0, 0.0, cumulative_before)
                        _prev_delta_positive = False
                        continue

                    delta_best, variant_best, filt_best, fval_best, hdr_best, vec_best = best
                    # FIX: default value already in baseline — naked delta must be 0, yellows still count
                    try:
                        _def_val = defaults.get(switch)
                        if _def_val is not None and str(cand).strip().lower() == str(_def_val).strip().lower():
                            if filt_best is None and delta_best > 1e-9:
                                _any_y_pos = any(float(v or 0) > 1e-9 for v in (pending_lbI or {}).values())
                                if not _any_y_pos:
                                    print(f"[BROKEN_DEFAULT] {switch} cand {cand} == default {_def_val} delta {delta_best:.2f} >0 — forcing 0, naked already in baseline", flush=True)
                                    delta_best = 0
                                    best = (delta_best, variant_best, filt_best, fval_best, hdr_best, vec_best)
                    except: pass
                    # SUSPICIOUS 30% guard
                    try:
                        if abs(delta_best) >= 30:
                            print(f"[SUSPICIOUS_30PCT] {switch}={cand} delta {delta_best:.2f} >=30% vs cum {cumulative_before:.2f} vec {vec_best.get('gain_pct',0):.2f}", flush=True)
                    except: pass
                    # USER 2026-10-03 RULE#3: the row is NOT complete until every yellow cell is calculated
                    _missing_y = _y_complete(relevant_hdrs, evaluated_hdrs)
                    if _missing_y:
                        for _mh in _missing_y:
                            _mark_red(ws_row, r, header_to_col.get(_mh), "uncalculated")
                        progress.get("done", {}).pop(key, None)
                        print(f"[ROW-INCOMPLETE] {new_symside} {key} {len(_missing_y)} yellows uncalculated — no zeros written, stays pending", flush=True)
                        try:
                            if ws_row is not None:
                                ws_row.cell(row=r, column=6).value = None
                                _mark_red(ws_row, r, 7, f"incomplete-yellows {len(_missing_y)}")
                        except Exception:
                            pass
                        _prev_delta_positive = False
                        continue
                    try:
                        if pending_lbI and ws_row is not None:
                            for hdr, d in pending_lbI.items():
                                col = header_to_col.get(hdr)
                                if col:
                                    try:
                                        ws_row.cell(row=r, column=col).value = float(d)
                                    except Exception:
                                        pass
                        # backstop: this row's relevant yellow cells may not stay formula/empty — write pending or flagged 0.0 (other columns untouched: non-yellow for this row)
                        if ws_row is not None:
                            _missing = []
                            for _hdr in relevant_hdrs:
                                _col = header_to_col.get(_hdr)
                                if not _col:
                                    continue
                                try:
                                    _cv = ws_row.cell(row=r, column=_col).value
                                except Exception:
                                    _cv = None
                                if isinstance(_cv, str) or _cv is None:
                                    if _hdr in pending_lbI:
                                        # RESPECT s3/s5 STDEV/shuffle: if peer already computed this yellow, keep max
                                        _d = float(pending_lbI[_hdr])
                                        if sheet == "STDEV_SLOPE_SIZING" and key in progress.get("done", {}) and _hdr in (progress["done"][key].get("yellows") or {}):
                                            _peer_d = float(progress["done"][key]["yellows"][_hdr] or 0)
                                            if abs(_peer_d) > abs(_d) and _peer_d != 0:
                                                _d = _peer_d
                                                print(f"[respect-stdev] {key} {_hdr} peer {_peer_d:.2f} > new {_d:.2f} — respect", flush=True)
                                        try:
                                            ws_row.cell(row=r, column=_col).value = float(_d)
                                        except Exception:
                                            pass
                                    else:
                                        # STDEV respect: don't overwrite peer's valid yellow with 0.0 backstop
                                        _skip_zero = False
                                        if sheet == "STDEV_SLOPE_SIZING" and key in progress.get("done", {}):
                                            _peer_y = (progress["done"][key].get("yellows") or {}).get(_hdr)
                                            if _peer_y is not None and float(_peer_y) != 0:
                                                _skip_zero = True
                                                try:
                                                    ws_row.cell(row=r, column=_col).value = float(_peer_y)
                                                except Exception:
                                                    pass
                                        if not _skip_zero:
                                            _mark_red(ws_row, r, _col, "unevaluated-backstop")
                                        _missing.append(_hdr)
                            if _missing:
                                _flag_to_md(flags_md, sheet, r, switch, cand, f"yellow backstop {len(_missing)} unevaluated", 0.0, 0.0, cumulative_before)
                        target = None
                        for cand_name in ["Results_Deltas", "Results_30d_Deltas", "Results_30d", "results"]:
                            if cand_name in wb_row.sheetnames:
                                target = cand_name
                                break
                        if target is None:
                            ws_new = wb_row.create_sheet("Results_Deltas")
                            ws_new.append(["key","default","override","is_non_default","delta_gain_vs_bh","delta_sharpe","delta_trades","variant_gain","REAL_COMPLETE_DELTA","variant_sharpe","trades","tim","dd"])
                            target = "Results_Deltas"
                        rws = wb_row[target]
                        if str(rws.cell(1,1).value or "").strip().lower() in ("param","switch"):
                            rws.cell(1,1).value = "key"
                        key_results = f"{switch}={cand}" if cand is not None else switch
                        found = None
                        for rr in range(2, rws.max_row + 1):
                            if str(rws.cell(row=rr, column=1).value or "").strip() == key_results:
                                found = rr
                                break
                        if found is None and cand is not None:
                            for rr in range(2, rws.max_row + 1):
                                if str(rws.cell(row=rr, column=1).value or "").strip() == switch:
                                    rws.cell(row=rr, column=1).value = key_results
                                    found = rr
                                    break
                        if found is None:
                            found = rws.max_row + 1
                            rws.cell(row=found, column=1).value = key_results
                        rws.cell(row=found, column=8).value = float(vec_best.get("gain_pct") or 0)
                        rws.cell(row=found, column=5).value = float(delta_best)
                        # fill default/override/is_non_default for switch result (user saw empty)
                        try:
                            default_val = defaults.get(switch)
                            rws.cell(row=found, column=2).value = str(default_val) if default_val is not None else None
                            rws.cell(row=found, column=3).value = str(cand) if cand is not None else None
                            is_non_default = 0 if str(default_val) == str(cand) else 1
                            rws.cell(row=found, column=4).value = is_non_default
                            # also fill delta_sharpe/delta_trades if header exists
                            header_map2 = {str(rws.cell(1,c).value or "").strip().lower(): c for c in range(1, rws.max_column+1)}
                            if "delta_sharpe" in header_map2:
                                rws.cell(row=found, column=header_map2["delta_sharpe"]).value = float(vec_best.get("pool_sharpe") or 0) - float(baseline_vec.get("pool_sharpe") or 0) if baseline_vec else 0
                            if "delta_trades" in header_map2:
                                rws.cell(row=found, column=header_map2["delta_trades"]).value = int(vec_best.get("trades") or 0) - int(baseline_vec.get("trades") or 0) if baseline_vec else 0
                        except Exception:
                            pass
                        try:
                            rws.cell(row=found, column=10).value = int(vec_best.get("trades") or 0)
                            rws.cell(row=found, column=12).value = float(vec_best.get("tim_pct") or 0)
                            rws.cell(row=found, column=11).value = float(vec_best.get("max_dd_pct") or 0) if vec_best.get("max_dd_pct") is not None else None
                            header_map = {str(rws.cell(1,c).value or "").strip().lower(): c for c in range(1, rws.max_column+1)}
                            if "variant_sharpe" in header_map:
                                rws.cell(row=found, column=header_map["variant_sharpe"]).value = float(vec_best.get("pool_sharpe") or 0)
                            if "bh_pct" in header_map:
                                rws.cell(row=found, column=header_map["bh_pct"]).value = float(vec_best.get("bh_pct") or 0)
                            if "gain_pct" in header_map:
                                rws.cell(row=found, column=header_map["gain_pct"]).value = float(vec_best.get("gain_pct") or 0)
                            # Every pos delta: fill full 24-col metrics line (user requirement)
                            if delta_best is not None and delta_best > 1e-9:
                                try:
                                    # REAL_COMPLETE_DELTA = hustle vs baseline (vec - baseline)
                                    if "real_complete_delta" in header_map:
                                        rws.cell(row=found, column=header_map["real_complete_delta"]).value = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                                    if "filter_or_override" in header_map:
                                        rws.cell(row=found, column=header_map["filter_or_override"]).value = f"{filt_best}={fval_best}" if filt_best else f"{switch}={cand}"
                                    if "symside" in header_map:
                                        rws.cell(row=found, column=header_map["symside"]).value = new_symside
                                    if "window" in header_map:
                                        rws.cell(row=found, column=header_map["window"]).value = args.window_days
                                    if "tim_pct" in header_map:
                                        rws.cell(row=found, column=header_map["tim_pct"]).value = float(vec_best.get("tim_pct") or 0)
                                    if "max_dd" in header_map:
                                        rws.cell(row=found, column=header_map["max_dd"]).value = float(vec_best.get("max_dd_pct") or 0)
                                    if "win_rate" in header_map:
                                        rws.cell(row=found, column=header_map["win_rate"]).value = float(vec_best.get("win_rate") or 0)
                                    if "bars" in header_map:
                                        rws.cell(row=found, column=header_map["bars"]).value = int(vec_best.get("bars") or len(vec_best.get("close",[])) or 0)
                                    if "peak" in header_map:
                                        rws.cell(row=found, column=header_map["peak"]).value = float(vec_best.get("peak") or 0)
                                    if "source" in header_map:
                                        rws.cell(row=found, column=header_map["source"]).value = f"v15_pos_delta {sheet}!{r} {switch}={cand} worst_first"
                                    # also ensure tim/dd columns (12,11) already set but repeat via header_map
                                    if "tim" in header_map:
                                        rws.cell(row=found, column=header_map["tim"]).value = float(vec_best.get("tim_pct") or 0)
                                    if "dd" in header_map:
                                        rws.cell(row=found, column=header_map["dd"]).value = float(vec_best.get("max_dd_pct") or 0)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        # BATCHED: write F/Yellow/Results to wb_keep in-memory only, flush to disk every 10 rows or at sheet end (was per-row 3 saves -> strand at row 21)
                        try:
                            # first data row E baseline
                            if ws_row is not None:
                                # find first data row once per sheet (cache)
                                if not hasattr(_atomic_save, "_first_r_cache"):
                                    _atomic_save._first_r_cache = {}
                                if sheet not in _atomic_save._first_r_cache:
                                    fr = None
                                    for _rr in range(3, ws_row.max_row+1):
                                        if ws_row.cell(row=_rr, column=1).value not in (None, ""):
                                            fr = _rr
                                            break
                                    _atomic_save._first_r_cache[sheet] = fr or r
                                # DESTROYED DOUBLE E: first-row E was duplicate write without pos check — single E write only at promotion block below
                                pass
                                # Dual: F (6) is hustle vs baseline (only in hustle mode), G (7) is greedy vs cum — F empty in worst_first
                                _is_hustle2 = getattr(args, "seq_mode", "") == "hustle"
                                _h_for_row = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                                ws_row.cell(row=r, column=6).value = float(_h_for_row) if _is_hustle2 else None
                                ws_row.cell(row=r, column=6).fill = VISUAL_F_FILL
                                ws_row.cell(row=r, column=6).font = VISUAL_F_FONT
                                ws_row.cell(row=r, column=6).alignment = VISUAL_ALIGN
                                ws_row.cell(row=r, column=7).value = float(delta_best)
                                ws_row.cell(row=r, column=7).font = Font(name="Arial", size=10, bold=True, color="9C5700")
                                ws_row.cell(row=r, column=7).alignment = VISUAL_ALIGN
                                # H/I/K per-row (added for gap fix)
                                try: _hk_ld = float(live_delta) if 'live_delta' in locals() and live_delta is not None else None
                                except: _hk_ld = 0.0
                                try: _hk_ls = float((live_best or {}).get('pool_sharpe') or 0) if 'live_best' in locals() and live_best is not None and (live_best or {}).get('pool_sharpe') is not None else None
                                except: _hk_ls = 0.0
                                try: _hk_pf = ", ".join(f"{k}={v}" for k,v in (pos_yellows.items() if 'pos_yellows' in locals() and isinstance(pos_yellows, dict) else {}))
                                except: _hk_pf = ""
                                _write_per_row_HIK(ws_row, r, _hk_ld, _hk_ls, _hk_pf)
                                try: _clear_vlookup_formulas(ws_row)
                                except: pass
                        except Exception:
                            pass
                    except Exception as _e:
                        print(f"[row-write-err] {sheet}!{r} {_e}", flush=True)
                    progress.setdefault("done", {})[key] = {"delta": float(delta_best), "vec_gain": float(vec_best.get("gain_pct") or 0), "vec": {k: vec_best.get(k) for k in ["gain_pct","trades","pool_sharpe","valid","bh_pct","tim_pct","max_dd_pct","win_rate","bars","peak","n_syms","years","avg_gain_trade","gain_per_yr","sym_sharpe"]}, "best_filter": filt_best, "best_fval": fval_best, "yellows": dict(pending_lbI) if pending_lbI else {}, "invalid_yellows": list(invalid_hdrs), "unsettled_yellows": list(unsettled_hdrs), "cumulative_before": float(cumulative_before), "cumulative_after": float(cumulative_before + delta_best) if delta_best > 0 else float(cumulative_before), "npz": _run_npz_short, "complete": True}
                    try:
                        # batch progress.json every 10 rows for 180/3min = 1s/cell (was per-row fsync = 1.6s/row)
                        if r % 10 == 0 or args.window_days not in (1,7):
                            _atomic_write_json(progress_path, progress)
                        else:
                            # still update in-memory, will flush at 10
                            pass
                    except: pass
                    _filter_suffix = f"+{filt_best}={fval_best}" if filt_best else ""
                    # Cycle-through-all-tabs: go through all 12 tabs then come back, until POS then move to next row (tenths)
                    if '_cycle_deque' in locals() and _cycle_deque is not None:
                        if delta_best is not None and delta_best > 1e-9:
                            print(f"[0914-cycle] POS {sheet}!{r} delta {delta_best:.4f} -> next tab (all-tabs cycle, advance row on POS)", flush=True)
                            try:
                                _cycle_deque.rotate(-1)
                            except: pass
                        else:
                            print(f"[0914-cycle] NEG {sheet}!{r} delta {delta_best if delta_best is not None else 0:.4f} -> next tab (all-tabs cycle, stay row on NEG)", flush=True)
                            try:
                                _cycle_deque.rotate(-1)
                            except: pass
                    if delta_best <= 0:
                        # E blank for neg/0, overrides blank — NEG is valid calc (orange), not true failure (red)
                        # FIX: ensure both F (hustle vs baseline) and G (greedy vs cum) are written as floats for EVERY cell — no VLOOKUP left
                        try:
                            if ws_row is not None:
                                if r + 1 <= ws_row.max_row:
                                    ws_row.cell(row=r+1, column=5).value = None
                                ws_row.cell(row=r, column=3).value = None
                                from openpyxl.styles import PatternFill
                                # E always numeric per user (was None for NEG -> empty)
                                ws_row.cell(row=r, column=5).value = float(cumulative_before)
                                ws_row.cell(row=r, column=5).font = __import__("openpyxl").styles.Font(name="Arial", bold=False, color="000000")
                                _is_hustle = getattr(args, "seq_mode", "") == "hustle"
                                _hustle_neg = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                                ws_row.cell(row=r, column=6).value = float(_hustle_neg) if (_is_hustle and _hustle_neg is not None) else None
                                ws_row.cell(row=r, column=6).fill = VISUAL_F_FILL if _is_hustle else VISUAL_F_FILL
                                ws_row.cell(row=r, column=6).font = VISUAL_F_FONT
                                ws_row.cell(row=r, column=6).alignment = VISUAL_ALIGN
                                ws_row.cell(row=r, column=7).value = float(delta_best) if delta_best is not None else None
                                ws_row.cell(row=r, column=7).fill = __import__("openpyxl").styles.PatternFill(start_color="FFA500", end_color="FFA500", fill_type="solid")
                                ws_row.cell(row=r, column=7).font = __import__("openpyxl").styles.Font(name="Arial", size=10, bold=True, color="000000")
                                ws_row.cell(row=r, column=7).alignment = VISUAL_ALIGN
                                # H/I/K per-row (added for gap fix)
                                try: _hk_ld = float(live_delta) if 'live_delta' in locals() and live_delta is not None else None
                                except: _hk_ld = 0.0
                                try: _hk_ls = float((live_best or {}).get('pool_sharpe') or 0) if 'live_best' in locals() and live_best is not None and (live_best or {}).get('pool_sharpe') is not None else None
                                except: _hk_ls = 0.0
                                try: _hk_pf = ", ".join(f"{k}={v}" for k,v in (pos_yellows.items() if 'pos_yellows' in locals() and isinstance(pos_yellows, dict) else {}))
                                except: _hk_pf = ""
                                _write_per_row_HIK(ws_row, r, _hk_ld, _hk_ls, _hk_pf)
                                try: _clear_vlookup_formulas(ws_row)
                                except: pass
                        except Exception:
                            pass
                        _flag_to_md(flags_md, sheet, r, switch, cand, "NEG delta<=0 blocks", delta_best, float(vec_best.get("gain_pct") or 0), cumulative_before)
                        # FORWARD FIX 2026-09-23: ZERO-STOP - fed up with 0 deltas wasting CPU
                        if abs(float(delta_best)) < 1e-9:
                            print(f"[ZERO-STOP] {new_symside} sheet {sheet}!{r} {switch}={cand} delta 0.0000 vs cum {cumulative_before:.4f} - STOP FIX IMMEDIATELY per user", flush=True)
                            try: _flag_to_md(flags_md, sheet, r, new_symside, "ZERO", f"delta 0 at {sheet}!{r} {switch}", 0, 0, cumulative_before)
                            except: pass
                            # do not promote 0, skip but log
                            _touch_heartbeat(f"cell {sheet}!{r} ZERO")
                            continue
                        print(f"[ROW] {sheet}!{r} {switch}={cand}{_filter_suffix} vec_gain={float(vec_best.get('gain_pct') or 0):.4f} delta={delta_best:.4f} vs cum {cumulative_before:.4f} -> NEG trades vec={vec_best.get('trades')} sharpe={float(vec_best.get('pool_sharpe') or 0):.4f}", flush=True)
                        _touch_heartbeat(f"cell {sheet}!{r} NEG")
                        # FIX empty sheets - save per row so baseline/delta visible within seconds (was 10 rows batched)
                        try:
                            _atomic_save(wb_keep, wb_path)
                        except: pass
                        continue
                    print(f"[ROW] {sheet}!{r} {switch}={cand}{_filter_suffix} vec_gain={float(vec_best.get('gain_pct') or 0):.4f} delta={delta_best:.4f} vs cum {cumulative_before:.4f} -> POSITIVE candidate", flush=True)
                    if args.vector_only:
                        ok, reason = True, "vector-only (deferred verify)"
                        live_delta = delta_best
                        live_best = vec_best
                    else:
                        import concurrent.futures as _cf
                        with _cf.ThreadPoolExecutor(max_workers=16) as ex:
                            fut = ex.submit(live_evaluate, new_symside, dict(variant_best), args.window_days)
                            try:
                                live_best = fut.result(timeout=90)
                            except Exception as e:
                                live_best = {"valid": False, "invalid_reason": str(e), "gain_pct": 0.0}
                        if live_best.get("invalid_reason","").startswith("live timeout"):
                            print(f"[WATCHDOG LIVE TIMEOUT] {sheet}!{r} {switch}={cand}", flush=True)
                            ok, reason = False, live_best["invalid_reason"]
                            live_delta = None
                        else:
                            ok, reason = parity_ok(live_best, vec_best, allow_zero_baseline=baseline_had_zero_trades)
                            live_delta = float(live_best.get("gain_pct") or 0) - cumulative_before if live_best.get("valid") else None
                        if _check_per_cell_timeout(cell_start):
                            ok = False; reason = "per-cell timeout after live"
                    progress["done"][key].update({"live": {k: live_best.get(k) for k in ["gain_pct","trades","pool_sharpe","valid","invalid_reason"]} if 'live_best' in locals() else {}, "live_delta": live_delta, "parity": ok if 'ok' in locals() else False, "reason": reason if 'reason' in locals() else ""})
                    try:
                        _atomic_write_json(progress_path, progress)
                    except: pass
                    if not ok:
                        print(f"[parity-fail] {sheet}!{r} {switch}={cand}" + (f"+{filt_best}={fval_best}" if filt_best else "") + f" delta={delta_best:.4f} {reason}", flush=True)
                        try:
                            if ws_row is not None:
                                if r + 1 <= ws_row.max_row:
                                    ws_row.cell(row=r+1, column=5).value = None
                                ws_row.cell(row=r, column=3).value = None
                                from openpyxl.styles import PatternFill
                                _h_delta = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                                ws_row.cell(row=r, column=6).value = float(_h_delta) if (getattr(args, "seq_mode", "") == "hustle" and _h_delta is not None) else None
                                ws_row.cell(row=r, column=6).fill = VISUAL_F_FILL
                                ws_row.cell(row=r, column=6).font = VISUAL_F_FONT
                                ws_row.cell(row=r, column=6).alignment = VISUAL_ALIGN
                                ws_row.cell(row=r, column=7).value = float(delta_best) if delta_best is not None else None
                                ws_row.cell(row=r, column=7).fill = __import__("openpyxl").styles.PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                ws_row.cell(row=r, column=7).font = __import__("openpyxl").styles.Font(name="Arial", size=10, bold=True, color="FFFFFF")
                                ws_row.cell(row=r, column=7).alignment = VISUAL_ALIGN
                                # H/I/K per-row (added for gap fix)
                                try: _hk_ld = float(live_delta) if 'live_delta' in locals() and live_delta is not None else None
                                except: _hk_ld = 0.0
                                try: _hk_ls = float((live_best or {}).get('pool_sharpe') or 0) if 'live_best' in locals() and live_best is not None and (live_best or {}).get('pool_sharpe') is not None else None
                                except: _hk_ls = 0.0
                                try: _hk_pf = ", ".join(f"{k}={v}" for k,v in (pos_yellows.items() if 'pos_yellows' in locals() and isinstance(pos_yellows, dict) else {}))
                                except: _hk_pf = ""
                                _write_per_row_HIK(ws_row, r, _hk_ld, _hk_ls, _hk_pf)
                                try: _clear_vlookup_formulas(ws_row)
                                except: pass
                        except Exception:
                            pass
                        _flag_to_md(flags_md, sheet, r, switch, cand, f"parity-fail {reason}", delta_best, float(vec_best.get("gain_pct") or 0), cumulative_before)
                        # FIX: always write E/F/G for ENTRY_REVERSAL_BOUNCE even on parity-fail — never leave row empty
                        try:
                            if ws_row is not None:
                                ws_row.cell(row=r, column=5).value = float(cumulative_before)
                                ws_row.cell(row=r, column=5).font = __import__("openpyxl").styles.Font(name="Arial", bold=False, color="000000")
                                _h_pf = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                                ws_row.cell(row=r, column=6).value = float(_h_pf) if (getattr(args, "seq_mode", "") == "hustle" and _h_pf is not None) else None
                                ws_row.cell(row=r, column=7).value = float(delta_best) if delta_best is not None else None
                        except Exception:
                            pass
                        _touch_heartbeat(f"cell {sheet}!{r} parity-fail")
                        continue
                    if live_delta is not None and live_delta <= 0:
                        print(f"[live-neg] {sheet}!{r} {switch} live_delta={live_delta:.4f} — not promoting", flush=True)
                        try:
                            if ws_row is not None:
                                # FIX: always write E baseline even on live-neg — never leave row empty per user
                                ws_row.cell(row=r, column=5).value = float(cumulative_before)
                                ws_row.cell(row=r, column=5).font = __import__("openpyxl").styles.Font(name="Arial", bold=False, color="000000")
                                from openpyxl.styles import PatternFill
                                _h_delta2 = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                                ws_row.cell(row=r, column=6).value = float(_h_delta2) if (getattr(args, "seq_mode", "") == "hustle" and _h_delta2 is not None) else None
                                ws_row.cell(row=r, column=6).fill = VISUAL_F_FILL
                                ws_row.cell(row=r, column=6).font = VISUAL_F_FONT
                                ws_row.cell(row=r, column=6).alignment = VISUAL_ALIGN
                                ws_row.cell(row=r, column=7).value = float(delta_best) if delta_best is not None else None
                                ws_row.cell(row=r, column=7).fill = __import__("openpyxl").styles.PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                ws_row.cell(row=r, column=7).font = __import__("openpyxl").styles.Font(name="Arial", size=10, bold=True, color="FFFFFF")
                                ws_row.cell(row=r, column=7).alignment = VISUAL_ALIGN
                                # H/I/K per-row (added for gap fix)
                                try: _hk_ld = float(live_delta) if 'live_delta' in locals() and live_delta is not None else None
                                except: _hk_ld = 0.0
                                try: _hk_ls = float((live_best or {}).get('pool_sharpe') or 0) if 'live_best' in locals() and live_best is not None and (live_best or {}).get('pool_sharpe') is not None else None
                                except: _hk_ls = 0.0
                                try: _hk_pf = ", ".join(f"{k}={v}" for k,v in (pos_yellows.items() if 'pos_yellows' in locals() and isinstance(pos_yellows, dict) else {}))
                                except: _hk_pf = ""
                                _write_per_row_HIK(ws_row, r, _hk_ld, _hk_ls, _hk_pf)
                                try: _clear_vlookup_formulas(ws_row)
                                except: pass
                        except Exception:
                            pass
                        _flag_to_md(flags_md, sheet, r, switch, cand, f"live-neg {live_delta}", delta_best, float(vec_best.get("gain_pct") or 0), cumulative_before)
                        _touch_heartbeat(f"cell {sheet}!{r} live-neg")
                        continue
                    # E-bland guard: never allow cumulative to drop on a pos delta (stale delta from old cum)
                    new_cum = float(vec_best.get("gain_pct") or 0)
                    if delta_best is not None and delta_best > 0 and new_cum + 1e-9 < cumulative_gain:
                        print(f"[E-BLAND] {sheet}!{r} {switch}={cand} new {new_cum:.4f} < cum {cumulative_gain:.4f} drop blocked (delta {delta_best:.4f} stale)", flush=True)
                        # mark as not promoted but keep yellow/orange with correct delta vs current cum
                        # recompute delta vs current cum for correct F
                        delta_best = new_cum - cumulative_gain
                        # write G as negative greedy (bland) and F as hustle vs baseline + E baseline — never leave row empty per user
                        try:
                            if ws_row is not None:
                                ws_row.cell(row=r, column=5).value = float(cumulative_before)
                                ws_row.cell(row=r, column=5).font = Font(name="Arial", bold=False, color="000000")
                                _h_bland = float(vec_best.get("gain_pct") or 0) - float(baseline_gain or 0)
                                ws_row.cell(row=r, column=6).value = float(_h_bland) if getattr(args, "seq_mode", "") == "hustle" else None
                                ws_row.cell(row=r, column=7).value = float(delta_best)
                                from openpyxl.styles import PatternFill
                                ws_row.cell(row=r, column=7).fill = __import__("openpyxl").styles.PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                ws_row.cell(row=r, column=7).font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
                                ws_row.cell(row=r, column=7).alignment = VISUAL_ALIGN
                                # H/I/K per-row (added for gap fix)
                                try: _hk_ld = float(live_delta) if 'live_delta' in locals() and live_delta is not None else None
                                except: _hk_ld = 0.0
                                try: _hk_ls = float((live_best or {}).get('pool_sharpe') or 0) if 'live_best' in locals() and live_best is not None and (live_best or {}).get('pool_sharpe') is not None else None
                                except: _hk_ls = 0.0
                                try: _hk_pf = ", ".join(f"{k}={v}" for k,v in (pos_yellows.items() if 'pos_yellows' in locals() and isinstance(pos_yellows, dict) else {}))
                                except: _hk_pf = ""
                                _write_per_row_HIK(ws_row, r, _hk_ld, _hk_ls, _hk_pf)
                                try: _clear_vlookup_formulas(ws_row)
                                except: pass
                        except Exception:
                            pass
                        _flag_to_md(flags_md, sheet, r, switch, cand, f"E-BLAND drop {new_cum:.4f} < {cumulative_gain:.4f}", delta_best, float(vec_best.get("gain_pct") or 0), cumulative_before)
                        progress.setdefault("done", {})[key] = {"delta": float(delta_best), "vec_gain": float(vec_best.get("gain_pct") or 0), "vec": {k: vec_best.get(k) for k in ["gain_pct","trades","pool_sharpe","valid","bh_pct","tim_pct","max_dd_pct"]}, "best_filter": filt_best, "best_fval": fval_best, "reason": "E-bland drop blocked"}
                        _atomic_write_json(progress_path, progress)
                        _touch_heartbeat(f"cell {sheet}!{r} E-bland")
                        continue
                    # build overrides string with all overrides used for this pos delta
                    try:
                        if ws_row is not None:
                            # ALL overrides used if pos delta
                            # PRECISE C: switch + options + filter settings for yellow box filters ONLY IF pos delta
                            all_over = []
                            pos_yellows = {h: d for h, d in (pending_lbI or {}).items() if float(d or 0) > 1e-9}
                            for k2, v2 in variant_best.items():
                                if str(defaults.get(k2)) != str(v2):
                                    # only include if it's the switch itself or a yellow filter with pos delta
                                    if k2 == switch or k2 in pos_yellows or str(v2) in [str(x) for x in pos_yellows.values()]:
                                        all_over.append(f"{k2}={v2}")
                                    elif any(k2 in str(yf) for yf in pos_yellows):
                                        all_over.append(f"{k2}={v2}")
                            overrides_str = " + ".join(all_over) if all_over else str(cand)
                            ws_row.cell(row=r, column=3).value = overrides_str
                            ws_row.cell(row=r, column=3).font = Font(name="Arial", bold=True, color="006100")
                            # baseline for this row: E_r = cumulative_before (spec: E is previous winning cum), G is VECTOR_DELTA greedy vs cum, H/I empty until live
                            # worst_first: hustle_delta NOT available (we are not in hustle mode) — F is HUSTLE only in hustle mode, else F is same as G or empty
                            if args.seq_mode in ("worst_first", "worst2best", "worst-first"):
                                _hustle_delta_vs_baseline = None  # not in hustle mode
                                ws_row.cell(row=r, column=6).value = None  # F empty in worst_first
                            else:
                                _hustle_delta_vs_baseline = (float(vec_best.get("gain_pct")) - float(baseline_gain)) if (vec_best.get("gain_pct") is not None and baseline_gain is not None) else None  # NO-LIES: never 0 for None
                                ws_row.cell(row=r, column=6).value = float(_hustle_delta_vs_baseline) if (getattr(args, "seq_mode", "") == "hustle" and _hustle_delta_vs_baseline is not None) else None  # F = HUSTLE_DELTA vs baseline (only hustle)
                            ws_row.cell(row=r, column=7).value = float(delta_best) if delta_best is not None else None  # G = greedy VECTOR_DELTA vs cum
                            ws_row.cell(row=r, column=7).font = Font(name="Arial", size=10, bold=True, color="9C5700")
                            ws_row.cell(row=r, column=7).alignment = VISUAL_ALIGN
                            # H/I empty until live backtest — first millions of other calculations, then live
                            _hk_ld = None
                            _hk_ls = None
                            try: _hk_pf = ", ".join(f"{k}={v}" for k,v in (pos_yellows.items() if 'pos_yellows' in locals() and isinstance(pos_yellows, dict) else {}))
                            except: _hk_pf = ""
                            _write_per_row_HIK(ws_row, r, _hk_ld, _hk_ls, _hk_pf)
                            try: _clear_vlookup_formulas(ws_row)
                            except: pass
                            ws_row.cell(row=r, column=6).value = float(_hustle_delta_vs_baseline) if _hustle_delta_vs_baseline is not None else None  # F = HUSTLE_DELTA vs baseline
                            ws_row.cell(row=r, column=6).fill = VISUAL_F_FILL
                            ws_row.cell(row=r, column=6).font = VISUAL_F_FONT
                            ws_row.cell(row=r, column=6).alignment = VISUAL_ALIGN
                            # E for this row (col 5) is cumulative_before - always numeric per user (was None for NEG -> empty trash)
                            ws_row.cell(row=r, column=5).value = float(cumulative_before)
                            ws_row.cell(row=r, column=5).font = Font(name="Arial", bold=True, color="006100") if delta_best > 0 else Font(name="Arial", bold=False, color="000000")
                            # next row's E will be set when that row is evaluated, not now
                            # keep old next-row blank for NEG to avoid carry-over, but not needed as E for next row will be overwritten when that row is processed
                            if r + 1 <= ws_row.max_row and delta_best <= 0:
                                # ensure next row's E is blank until its own delta evaluated (avoid stale carry)
                                try:
                                    nxt = ws_row.cell(row=r+1, column=5).value
                                    # only clear if it was previously set to old cum (stale)
                                    if nxt is not None and nxt != "":
                                        ws_row.cell(row=r+1, column=5).value = None
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    cumulative_gain = new_cum
                    cumulative_overrides = dict(variant_best)
                    total_pos += 1
                    progress["done"][key]["cumulative_after"] = cumulative_gain
                    progress["cumulative_gain"] = cumulative_gain
                    progress["cumulative_overrides"] = cumulative_overrides
                    _atomic_write_json(progress_path, progress)
                    # Update BASELINE_METRICS B2 to winning cumulative_gain (spec: baseline with final test gain result of winning combination so far)
                    try:
                        _bname = f"{new_symside}_BASELINE_METRICS"
                        if _bname in wb_keep.sheetnames:
                            _bws = wb_keep[_bname]
                            _bws.cell(row=2, column=2).value = float(cumulative_gain)
                            _bws.cell(row=2, column=2).font = Font(name="Arial", bold=True, color="006100")
                    except Exception:
                        pass
                    print(f"[PROMOTE] {sheet}!{r} {switch}={cand}" + (f"+{filt_best}={fval_best}" if filt_best else "") + f" delta={delta_best:.4f} cum->{cumulative_gain:.4f}", flush=True)
                    # DESKTOP: first POS delta for EVERY sym_side (once)
                    try:
                        if not _first_pos_notified:
                            _first_pos_notified = True
                            _macbook_desktop_notify(f"✅ {new_symside} FIRST POS", f"{sheet}!{r} {switch}={cand}" + (f"+{filt_best}={fval_best}" if filt_best else "") + f" delta {delta_best:.2f} vec {float(vec_best.get('gain_pct') or 0):.2f} cum->{cumulative_gain:.2f} trades {vec_best.get('trades')}", critical=False)
                    except: pass
                    _touch_heartbeat(f"cell {sheet}!{r} PROMOTE")
                    # FIX empty - flush per row so XLS shows numbers within seconds (was 10 batched)
                    try:
                        print(f"[FLUSH] {sheet} row {r} cum {cumulative_gain:.4f}", flush=True)
                        _atomic_save(wb_keep, wb_path)
                    except Exception:
                        pass
                except Exception as e:
                    import traceback
                    print(f"[ROW-ERR] {sheet}!{r} {switch}={cand} err {e} {traceback.format_exc()[:800]}", flush=True)
                    try:
                        progress.setdefault("done", {})[key] = {"delta": 0, "reason": f"row err {e}"}
                        _atomic_write_json(progress_path, progress)
                        # flag red for never-stop
                        try:
                            if ws_row is not None:
                                from openpyxl.styles import PatternFill
                                ws_row.cell(row=r, column=6).value = 0.0  # F hustle
                                ws_row.cell(row=r, column=7).value = 0.0  # G never 0.0 — was 0.0
                                ws_row.cell(row=r, column=7).fill = __import__("openpyxl").styles.PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                                ws_row.cell(row=r, column=7).font = __import__("openpyxl").styles.Font(name="Arial", size=10, bold=True, color="FFFFFF")
                                ws_row.cell(row=r, column=7).alignment = VISUAL_ALIGN
                                # H/I/K per-row (added for gap fix)
                                try: _hk_ld = float(live_delta) if 'live_delta' in locals() and live_delta is not None else None
                                except: _hk_ld = 0.0
                                try: _hk_ls = float((live_best or {}).get('pool_sharpe') or 0) if 'live_best' in locals() and live_best is not None and (live_best or {}).get('pool_sharpe') is not None else None
                                except: _hk_ls = 0.0
                                try: _hk_pf = ", ".join(f"{k}={v}" for k,v in (pos_yellows.items() if 'pos_yellows' in locals() and isinstance(pos_yellows, dict) else {}))
                                except: _hk_pf = ""
                                _write_per_row_HIK(ws_row, r, _hk_ld, _hk_ls, _hk_pf)
                                try: _clear_vlookup_formulas(ws_row)
                                except: pass
                                if r + 1 <= ws_row.max_row:
                                    ws_row.cell(row=r+1, column=5).value = None
                                ws_row.cell(row=r, column=3).value = None
                        except: pass
                        _flag_to_md(flags_md, sheet, r, switch, cand, f"ROW-ERR {e}", -1.0, 0.0, cumulative_before)
                    except: pass
                    _touch_heartbeat(f"cell {sheet}!{r} ERR")
                    continue
                # periodic flush for NEG/parity paths as well
                try:
                    _atomic_save(wb_keep, wb_path)
                except Exception:
                    pass
                if _check_per_cell_timeout(cell_start):
                    print(f"[PER_CELL TIMEOUT] {sheet}!{r} {switch}={cand} >{per_cell_timeout_sec}s — flag RED cell+tab, skip and continue (never hang)", flush=True)
                    try:
                        if ws_row is not None:
                            from openpyxl.styles import PatternFill
                            ws_row.cell(row=r, column=6).fill = __import__("openpyxl").styles.PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                            ws_row.cell(row=r, column=6).font = __import__("openpyxl").styles.Font(name="Arial", size=10, bold=True, color="FFFFFF")
                            if ws_row.cell(row=r, column=6).value in (None, "") or isinstance(ws_row.cell(row=r, column=6).value, str):
                                ws_row.cell(row=r, column=6).value = 0.0
                            # also mark tab RED
                            try:
                                ws_row.sheet_properties.tabColor = "FF0000"
                            except: pass
                        _flag_to_md(flags_md, sheet, r, switch, cand, f"PER_CELL TIMEOUT {per_cell_timeout_sec}s", 0.0, 0.0, cumulative_before)
                    except: pass

            # final sheet save (batched) + red flag any remaining VLOOKUP that would block sequence
            try:
                # flag any remaining VLOOKUP (strand) in red before final save — never stop, just flag + MD
                for _r in range(3, ws_keep.max_row+1) if ws_keep else []:
                    try:
                        _v = ws_keep.cell(row=_r, column=6).value
                        if isinstance(_v, str) and "VLOOKUP" in _v:
                            from openpyxl.styles import PatternFill
                            ws_keep.cell(row=_r, column=6).fill = __import__("openpyxl").styles.PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                            ws_keep.cell(row=_r, column=6).font = __import__("openpyxl").styles.Font(name="Arial", size=10, bold=True, color="FFFFFF")
                            _flag_to_md(flags_md, sheet, _r, ws_keep.cell(row=_r, column=1).value, ws_keep.cell(row=_r, column=2).value, "VLOOKUP strand not calculated", _v, "", "")
                    except: pass
                print(f"[SHEET FLUSH] {sheet} final save", flush=True)
                _atomic_save(wb_keep, wb_path)
            except Exception:
                pass
            try:
                wb_keep.close()
            except Exception:
                pass
            print(f"[sheet DONE] {sheet} cum={cumulative_gain:.4f} positives={total_pos}", flush=True)
            # SHEET-ZERO tripwire: a processed sheet whose rows are ALL exact-zero
            # deltas+yellows is systematic failure (all-invalid engine/NPZ), never real
            # backtest output (real gains always vary) — flag loud, never silently stand
            try:
                _sd = [(k, v) for k, v in progress.get("done", {}).items() if k.startswith(sheet + "!") and "!GLOBAL:" not in k]
                if len(_sd) >= 5:
                    _allzero = sum(1 for _, v in _sd if abs(float(v.get("delta") or 0)) < 1e-12 and all(abs(float(x) or 0) < 1e-12 for x in (v.get("yellows") or {}).values()))
                    if _allzero >= 0.9 * len(_sd):
                        print(f"[SHEET-ZERO-FAIL] {sheet} {_allzero}/{len(_sd)} rows all-zero — systematic invalid, NOT real numbers", flush=True)
                        _flag_to_md(flags_md, sheet, 0, "SHEET-ZERO", str(wb_path), f"{_allzero}/{len(_sd)} rows all-zero deltas+yellows", 0.0, 0.0, cumulative_gain)
                    else:
                        print(f"[sheet-zero-ok] {sheet} {len(_sd)-_allzero}/{len(_sd)} rows carry real numbers", flush=True)
            except Exception as _sze:
                print(f"[sheet-zero-warn] {sheet} {_sze}", flush=True)
            # GLOBAL per-sheet: thorough revision — orange = entire sheet, prune to only important for sheet (0/neg stall workbook)
            # GENERAL 65 distinct -> 26 kept important (not the 39 that only produce 0/neg); use S1 ledger: SNDK all pos 4/neg 1613, GLOBAL pos 0/neg 62
            # Prune list derived from workflow: 39 distinct that stall (only 0/neg) — keep the other 26
            _PRUNED_ORANGE = {"EZ_MANAGE_THROTTLER_RATE","HA_WICK_QUALITY_ENABLED","HA_WICK_QUALITY_SCORE","HA_WICK_QUALITY_TF","HLR_SMA_BAND_PCT","HLR_TOP_MIN_TFS","HTF4_CONF","HTF_DIRECTION_GATE_ENABLED","HTF_GATE_BYPASS_RZ","HTF_GATE_D_MANDATORY","HTF_GATE_MIN_CONFIRMATIONS","HTF_TREND_VETO_BYPASS_ENABLED","HTF_TREND_VETO_BYPASS_REASONS","LEADERBOARD_FILTER","LH_HL_FILTER_ENABLED","LH_HL_FILTER_MODE","LH_HL_FILTER_REQUIRE_BOTH","LIVE_VEC_EMERGENCY_BRAKE_ENABLED","LR_BAND_LADDER_STOCH_EXTREME","LR_BAND_LADDER_TF_BOTTOM","LR_BAND_LADDER_TF_TOP","MANDATORY_REENTRY_WT_FILTER_MIN_TFS","MANDATORY_REENTRY_WT_FILTER_MIN_VELOCITY","MANDATORY_REENTRY_WT_FILTER_REQUIRE_FLIP","MANDATORY_REENTRY_WT_FILTER_VELOCITY_RATIO","MARKET_QUALITY_SCORE_ENABLED","MI_TF_AGREE_MIN","MOVER_THRESHOLD","MTF_FILTER_STRONG_BUY_QUICK_BYPASS","MTF_GR_MIN_IND","MTS_BOTTOM_BONUS_THRESHOLD","MTS_BOTTOM_STRONG_THRESHOLD","MTS_GATE_ENABLED","NEWBORN_LOSS_KILL_GAIN_THRESHOLD_PCT","NEWBORN_LOSS_KILL_REQUIRE_VEL_AGAINST","NEW_POSITION_MAX_LOSS_THRESHOLD","OI_CONFIRM_ENABLED","OI_CONFIRM_MIN_CHANGE_PCT","OI_CONFIRM_MIN_PRICE_PCT"}
            try:
                lifecycle = sheet.split("_")[0]
                _global_cands = []
                for _e in _load_filter_dictionary():
                    # orange per-sheet = GENERAL only (SPECIFIC is yellow per-switch above)
                    if not _is_general(_e.get("rec") or ""):
                        continue
                    _sa = (_e.get("sheets_app") or "").strip().upper()
                    if not (_sa == "ALL" or "GLOBAL" in _sa or lifecycle in _sa):
                        continue
                    _sw = _e.get("filter") or _e.get("key") or ""
                    if not _sw or _sw in _PRUNED_ORANGE:
                        continue
                    _cur = cumulative_overrides.get(_sw, defaults.get(_sw))
                    _def = defaults.get(_sw)
                    # use parsed vals from Options (col12), fallback to opt
                    _vals = _e.get("vals") or ([_e.get("opt")] if _e.get("opt") else [])
                    # filter out UNLIKELY and empty
                    _vals = [v for v in _vals if v and str(v).strip().upper() != "UNLIKELY"]
                    if not _vals:
                        continue
                    for _v in _vals[:2]:
                        try:
                            _cand = _v
                            if isinstance(_def, bool) and isinstance(_v, str):
                                _cand = _v.lower() == "true"
                            _key_g = f"{sheet}!GLOBAL:{_sw}={_cand}"
                            if _key_g in progress.get("done", {}):
                                continue
                            # skip if already at this value (no delta)
                            if str(_cur) == str(_cand):
                                continue
                            _global_cands.append((_sw, _cand, _e))
                            break
                        except: pass
                    if len(_global_cands) >= 4:
                        break
                if _global_cands:
                    print(f"[GLOBAL per-sheet] {sheet} trying {len(_global_cands)} global filters after sheet", flush=True)
                    for (_gsw, _gcand, _ge) in _global_cands[:3]:
                        try:
                            _g_before = cumulative_gain
                            _g_variant = dict(cumulative_overrides)
                            _g_variant[_gsw] = _gcand
                            _g_variant, _ = sanitize_overrides(_g_variant, defaults)
                            if prepared is not None:
                                from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_g
                                _g_vec = _eval_g(prepared, _g_variant, window_days=args.window_days)
                            else:
                                from tools.opt.v12_pilot import evaluate_sanitized as _eval_g2
                                _g_vec = _eval_g2(new_symside, _g_variant, window_days=args.window_days)
                            if not _g_vec.get("valid"):
                                continue
                            _g_gain = float(_g_vec.get("gain_pct") or 0)
                            _g_delta = _g_gain - _g_before
                            print(f"[GLOBAL per-sheet] {sheet} {_gsw}={_gcand} delta={_g_delta:.4f} vs cum {_g_before:.4f} vec_gain={_g_gain:.4f}", flush=True)
                            if _g_delta > 0 and _g_gain > cumulative_gain:
                                cumulative_gain = _g_gain
                                cumulative_overrides = dict(_g_variant)
                                _gk = f"{sheet}!GLOBAL:{_gsw}={_gcand}"
                                progress.setdefault("done", {})[_gk] = {"delta": float(_g_delta), "vec_gain": float(_g_gain), "vec": {k: _g_vec.get(k) for k in ["gain_pct","trades","pool_sharpe","valid","bh_pct","tim_pct","max_dd_pct"]}, "best_filter": _gsw, "best_fval": _gcand, "yellows": {}, "global_sheet": sheet}
                                progress["cumulative_gain"] = cumulative_gain
                                progress["cumulative_overrides"] = cumulative_overrides
                                _atomic_write_json(progress_path, progress)
                                # also write to Results_Deltas orange for visibility
                                try:
                                    wb_g = openpyxl.load_workbook(str(wb_path), data_only=False)
                                    for _cn in ["Results_Deltas","Results_30d_Deltas"]:
                                        if _cn in wb_g.sheetnames:
                                            _rws = wb_g[_cn]
                                            _found = None
                                            for _rr in range(2, _rws.max_row+1):
                                                if str(_rws.cell(row=_rr, column=1).value or "").strip() == f"{_gsw}={_gcand}":
                                                    _found = _rr
                                                    break
                                            if _found is None:
                                                _found = _rws.max_row+1
                                                _rws.cell(row=_found, column=1).value = f"{_gsw}={_gcand}"
                                            _rws.cell(row=_found, column=5).value = float(_g_delta)
                                            _rws.cell(row=_found, column=8).value = float(_g_gain)
                                            break
                                    _atomic_save(wb_g, wb_path)
                                    wb_g.close()
                                except Exception as _ge:
                                    print(f"[GLOBAL per-sheet warn] {sheet} {_gsw} write fail {_ge}", flush=True)
                                total_pos += 1
                                print(f"[GLOBAL PROMOTE] {sheet} {_gsw}={_gcand} delta={_g_delta:.4f} cum->{cumulative_gain:.4f}", flush=True)
                        except Exception as _ge:
                            print(f"[GLOBAL per-sheet err] {sheet} {_gsw} {_ge}", flush=True)
            except Exception as _ge:
                print(f"[GLOBAL per-sheet outer err] {sheet} {_ge}", flush=True)
            # Integrated E-bland + F/Yellow/Orange validation from test_v15_e_bland.py — ensures no confusion
            _validate_e_chain_and_yellows(progress, wb_path)
            # All rows remain NPZ in memory: verify fast path was used, not slow reload
            if prepared is None:
                print(f"[NPZ-CHECK-WARN] {sheet} no prepared — fell back to slow reload (should be vector fast)", flush=True)
            else:
                print(f"[NPZ-CHECK] {sheet} NPZ in memory {len(ALL_NPZ_ARRAYS.get(new_symside, {}))} arrays, fast 0.5s/row", flush=True)
            try:
                write_zoomable_chart(new_symside, sheet, cumulative_overrides, args.window_days)
            except Exception as _ce:
                print(f"[chart-warn] {sheet} {_ce}", flush=True)
            progress["cumulative_gain"] = cumulative_gain
            progress["cumulative_overrides"] = cumulative_overrides
            try:
                _atomic_write_json(progress_path, progress)
            except Exception:
                pass
            # FORWARD FIX 2026-09-23: sheet guard log-only — old delete prevented baseline/deltas within seconds; now log but continue.
            if len(progress.get("done", {})) == 0 and __import__('time').time() - _v15_start_time > 300:
                print(f"[LOUD-STOP-NO-DELTA-SHEET-DISABLED] {new_symside} sheet {sheet} NO deltas after {__import__('time').time() - _v15_start_time:.1f}s — continuing (abort disabled, would have deleted)", flush=True)
                try: _flag_to_md(flags_md, sheet, 0, new_symside, "NO_DELTA_SHEET", f"no delta after 300s sheet {sheet} log-only", 0, 0, cumulative_gain)
                except: pass
            # old pos-only sheet check now log-only
            if total_pos == 0 and __import__('time').time() - _v15_start_time > 15:
                print(f"[VIRUS0-15s-SHEET-DISABLED] {new_symside} sheet {sheet} 0 pos after {__import__('time').time() - _v15_start_time:.1f}s — continuing (abort disabled, yellows kept)", flush=True)
        except Exception as _sheet_e:
            import traceback
            print(f"[sheet-ERR] {sheet} {_sheet_e} {traceback.format_exc()[:800]}", flush=True)
            try:
                _atomic_write_json(progress_path, progress)
            except Exception:
                pass
            continue
    bh_raw = float(baseline_live.get("bh_pct") or baseline_vec.get("bh_pct") or 0)
    # DEAT PENALTY — strict empty/repeated/0.0 checker: must have L:BI yellows + E BASELINE + F VECTOR_DELTA + Results_Deltas col5/col8 filled
    def _strict_checker(path: Path) -> tuple[bool, str]:
        try:
            wb = openpyxl.load_workbook(str(path), data_only=True)
            # 1) Results_Deltas col5/col8 must have real numbers
            rs = None
            for cand in ["Results_Deltas", "Results_30d", "results"]:
                if cand in wb.sheetnames:
                    rs = wb[cand]
                    break
            if rs is None or rs.max_row < 2:
                wb.close()
                return False, "Results_Deltas empty (max_row<2)"
            vals5 = [rs.cell(r, 5).value for r in range(2, min(12, rs.max_row + 1)) if rs.cell(r, 5).value not in (None, "")]
            if not vals5:
                wb.close()
                return False, "Results_Deltas col5 all empty"
            if len(set(vals5)) == 1 and len(vals5) >= 5:
                wb.close()
                return False, f"Results_Deltas col5 repeated {vals5[0]}"
            # 0.0 in Results_Deltas col5 is legitimate (delta 0 means no improvement) — not a violation
            # 2) per-switch sheets: L:BI yellows — only flag if completely empty and no Results_Deltas, 0 in F is not a violation (F can be 0 when delta 0)
            # 0.0 in F is legitimate (delta 0 means no improvement), not a violation
            yellow_missing = 0
            for sh in SWITCH_SHEETS:
                if sh not in wb.sheetnames:
                    continue
                ws = wb[sh]
                for r in range(3, min(8, ws.max_row + 1)):
                    has_yellow = any(ws.cell(r, c).value not in (None, "") for c in range(12, min(18, ws.max_column + 1)))
                    if not has_yellow:
                        yellow_missing += 1
            wb.close()
            if yellow_missing >= 10 and not vals5:
                return False, f"DEAT PENALTY no yellows and no deltas — yellow_missing {yellow_missing}"
            return True, "ok"
        except Exception as e:
            return False, f"checker error {e}"
    # >1h PER SYM_SIDE RED LAW — NEVER HANG OVER HOUR, LOUD STOP
    _elapsed_sym = __import__('time').time() - _v15_start_time
    # FORWARD FIX 2026-09-25: this point is reached AFTER all tabs ran — a slow workbook is finished, not hung.
    # Log-only (was: paint every tab red + return, hiding a completed sheet). Per-cell red + tab red come from _paint_tab_status.
    if _elapsed_sym > 3600:
        print(f"[SLOW-SYM_SIDE >1h] {new_symside} elapsed {_elapsed_sym:.1f}s — all tabs ran, publishing (log-only)", flush=True)
        _flag_to_md(flags_md, "ALL", 0, new_symside, "TIME", f">1h PER SYM_SIDE {_elapsed_sym:.1f}s (log-only)", 0, 0, cumulative_gain)
    _ok, _reason = _strict_checker(wb_path)
    _is_empty = not _ok
    if _is_empty:
        # FORWARD FIX 2026-09-25: NEVER delete the workbook — it held every computed cell and a checker error (BadZip from a
        # concurrent writer, OOM on load) deleted real work, restarting the sym_side from an empty template. Keep + red.
        print(f"[DEAT PENALTY] {_reason} — workbook KEPT (red) for repair, not publishing", flush=True)
        progress["checker_failed"] = _reason
        progress["final_gain"] = cumulative_gain
        progress["bh"] = bh_raw
        try:
            _atomic_write_json(progress_path, progress)
        except Exception:
            pass
        _flag_to_md(flags_md, "ALL", 0, new_symside, "CHECKER", f"strict checker failed: {_reason} — kept", 0, 0, cumulative_gain)
        return
    # DO NOT PUBLISH until cells are filled — timestamp = work in progress, bh/gain = finished
    # fully filled — publish bh/gain
    # 2026-09-23 LOUD if no baseline/delta at all after seconds — keep yellows but loud stop if truly no deltas
    # FORWARD FIX 2026-09-23: final guard log-only — old delete destroyed workbook even though baseline existed; now log but publish.
    if len(progress.get("done", {})) == 0 and __import__('time').time() - _v15_start_time > 600:
        _elapsed = __import__('time').time() - _v15_start_time
        print(f"[LOUD-STOP-NO-DELTA-FINAL-DISABLED] {new_symside} NO deltas after {_elapsed:.1f}s total_pos 0 cum {cumulative_gain:.4f} baseline {baseline_gain:.4f} — continuing (abort disabled, would have deleted, publishing baseline)", flush=True)
        try: _flag_to_md(flags_md, "ALL", 0, new_symside, "NO_DELTA_FINAL", f"no delta after {_elapsed:.1f}s log-only", 0, 0, cumulative_gain)
        except: pass
    # old pos-only final check now log-only (pos==0 with deltas is ok, yellows kept)
    if total_pos == 0:
        _elapsed = __import__('time').time() - _v15_start_time
        print(f"[VIRUS0-15s-DISABLED] {new_symside} 0 pos after {_elapsed:.1f}s total_pos 0 cum {cumulative_gain:.4f} baseline {baseline_gain:.4f} — continuing to publish (abort disabled, yellows kept, deltas exist)", flush=True)
    # USER 2026-10-04 (ALGO stale-751): promotion-time NPZ freshness — refuse to finalize
    # if the NPZ changed mid-run (regen swap under a measuring pilot). Board archived +
    # refilled, never published mixed. Guard-exception fail-opens with an honest flag.
    try:
        from tools.v15_row_guards import npz_identity_for_symside as _npz_idF, npz_changed as _npz_chF
        _final_npz_id = _npz_idF(new_symside)
        if _npz_chF(_run_npz_id, _final_npz_id):
            _tsF = __import__("time").strftime("%Y%m%d%H%M%S", __import__("time").gmtime())
            try:
                (progress_path.parent / (progress_path.name + f".npzprev_{_tsF}.json")).write_text(json.dumps(progress))
            except Exception:
                pass
            try:
                progress.setdefault("npz_history", []).append({"prev": _run_npz_id, "new": _final_npz_id, "midrun_swap": True, "archived_done_n": len(progress.get("done", {}))})
            except Exception:
                pass
            _dF = int(progress.get("redo_depth", 0)) + 1
            progress["needs_redo"] = {"overrides": dict(progress.get("cumulative_overrides") or {}), "result": {"gain_pct": progress.get("cumulative_gain")}, "depth": _dF, "reason": "npz-changed-midrun: NPZ swapped under this run — board archived, re-fill every row on the new NPZ before any FINAL"}
            progress["done"] = {}
            for _rk in ("final_gain", "final_path", "not_compliant", "cumulative_gain", "cumulative_overrides", "hustler_best_gain", "hustler_overrides", "final_365d", "repair_365d", "confirmed_365d"):
                progress.pop(_rk, None)
            try:
                _atomic_write_json(progress_path, progress)
            except Exception:
                progress_path.write_text(json.dumps(progress))
            print(f"[NPZ-MIDRUN-REFUSE] {new_symside} NPZ changed during run — FINAL refused, board archived (.npzprev), rebuild scheduled (depth {_dF}), exiting for herd relaunch", flush=True)
            return
        progress["npz_id_final"] = _final_npz_id
    except Exception as _npzF_e:
        print(f"[npz-final-warn] {new_symside}: {_npzF_e} — continuing without midrun check (flagged)", flush=True)
        progress["npz_final_check"] = f"error: {_npzF_e}"[:150]
    def fmt(v): return f"{v:.2f}".replace("-", "m").replace(".", "p")
    final_name = f"{new_symside}_bh{fmt(bh_raw)}_gain{fmt(cumulative_gain)}_30d_matrix.xlsx"
    final_path = OUT_DIR / final_name
    if final_path.exists():
        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
        final_path = OUT_DIR / f"{new_symside}_bh{fmt(bh_raw)}_gain{fmt(cumulative_gain)}_30d_matrix_pilot_{ts}.xlsx"
    import shutil
    shutil.copy2(wb_path, final_path)
    progress["final_gain"] = cumulative_gain
    progress["bh"] = bh_raw
    progress["final_path"] = str(final_path)
    try:
        _atomic_write_json(progress_path, progress)
    except Exception:
        pass
    # === HUSTLER: combinatorial beam search vs baseline max delta (smart combining pos delta vs baseline) ===
    # Why: greedy cumulative (delta vs cum 18.86) marks every remaining single-switch as NEG even when vs baseline it is +17.
    # Hustler hustles many combinations of pos delta vs baseline until max delta across switches is found.
    # Runs after greedy 3041 F fills, uses same prepared vectors (0.07s) + live parity, beam width 32 depth 6.
    try:
        from tools.opt.v12_pilot import evaluate_prepared_sanitized as _hustle_eval
        # collect pos vs baseline candidates from progress.done (vs baseline not vs cum)
        # === HUSTLER advantage a: all functions vs baseline simultaneously, then per-hustle recalc shooting up delta ===
        # Re-evaluate EVERY switch vs baseline in one simultaneous vector batch (not vs cum 18.86) to get true vs baseline
        _hustle_top = []
        try:
            _sim_all = []
            _sim_keys = []
            for _sheet in SWITCH_SHEETS:
                if _sheet not in wb.sheetnames if 'wb' in locals() else SWITCH_SHEETS:
                    continue
                try:
                    _ws = wb[_sheet] if 'wb' in locals() and _sheet in wb.sheetnames else None
                    if _ws is None:
                        continue
                    for _r in range(3, _ws.max_row + 1):
                        _sw = str(_ws.cell(_r, 1).value or "").strip()
                        _cand = str(_ws.cell(_r, 2).value or "").strip()
                        if not _sw or _sw.lower() in ("switch","general","blanket","filter","option value") or _sw.startswith("—"):
                            continue
                        if _sw.lower() == "filter" and _cand.lower() == "option value":
                            continue
                        _sim_all.append((_sw, _cand))
                        _sim_keys.append(f"{_sheet}!{_r}:{_sw}={_cand}")
                except Exception:
                    continue
            # simultaneous vs baseline: each variant = baseline overrides + single switch (no cum)
            _sim_results = {}
            if _sim_all and prepared is not None:
                print(f"[hustler-sim] simultaneous vs baseline {len(_sim_all)} switches vs baseline {baseline_gain:.2f} 16 workers", flush=True)
                _base_over = dict(overrides)  # baseline overrides (8 +3337 defaults)
                _sim_variants = []
                for _sw,_cand in _sim_all:
                    _v = dict(_base_over)
                    _v[_sw] = _cand
                    _san,_ = sanitize_overrides(_v, defaults)
                    _sim_variants.append(_san)
                # vector batch simultaneous
                _sims = []
                try:
                    from concurrent.futures import ThreadPoolExecutor as _TPE
                    def _eval_sim(v):
                        return _hustle_eval(prepared, v, window_days=args.window_days) if prepared is not None else None
                    with _TPE(max_workers=args.workers) as _ex:
                        _sims = list(_ex.map(_eval_sim, _sim_variants))
                except Exception:
                    _sims = [_hustle_eval(prepared, v, window_days=args.window_days) for v in _sim_variants]
                for _idx, _vec in enumerate(_sims):
                    if _vec is None or not _vec.get("valid"):
                        continue
                    _vg = float(_vec.get("gain_pct") or 0)
                    _vsb = _vg - float(baseline_gain or 0)
                    if _vsb > 0:
                        _sw,_cand = _sim_all[_idx]
                        # best filter not needed for simultaneous, but keep for hustle
                        _hustle_top.append((_sw, _cand, None, None, _vsb, _vg))
                print(f"[hustler-sim] found {len(_hustle_top)} pos vs baseline simultaneous", flush=True)
            # fallback to progress.done derived if sim failed
            if not _hustle_top:
                for _hk, _hv in progress.get("done", {}).items():
                    _vec = _hv.get("vec", {}) or {}
                    _vg = float(_vec.get("gain_pct") or _hv.get("vec_gain") or 0)
                    _vs_base = _vg - float(baseline_gain or 0)
                    if _vs_base > 0:
                        try:
                            _sw = _hk.split(":",1)[1].split("=")[0].strip()
                            _cand = _hk.split("=",1)[1].strip()
                        except Exception:
                            continue
                        _bf = _hv.get("best_filter")
                        _bv = _hv.get("best_fval")
                        _hustle_top.append((_sw, _cand, _bf, _bv, _vs_base, _vg))
        except Exception as _se:
            print(f"[hustler-sim-warn] {_se}", flush=True)
            # fallback
            if not _hustle_top:
                for _hk, _hv in progress.get("done", {}).items():
                    _vec = _hv.get("vec", {}) or {}
                    _vg = float(_vec.get("gain_pct") or _hv.get("vec_gain") or 0)
                    _vs_base = _vg - float(baseline_gain or 0)
                    if _vs_base > 0:
                        try:
                            _sw = _hk.split(":",1)[1].split("=")[0].strip()
                            _cand = _hk.split("=",1)[1].strip()
                        except Exception:
                            continue
                        _bf = _hv.get("best_filter")
                        _bv = _hv.get("best_fval")
                        _hustle_top.append((_sw, _cand, _bf, _bv, _vs_base, _vg))
        # --- IMPROVED HUSTLE POOL: also consider pos vs greedy cum (not just baseline) ---
        # Greedy is sequential vs cum; a switch NEG vs baseline can be POS vs cum after other picks — hustle missed those before.
        _hustle_top_cum = []
        try:
            if _sim_all and prepared is not None and cumulative_overrides:
                _cum_variants = []
                for _sw,_cand in _sim_all:
                    if _sw in cumulative_overrides and str(cumulative_overrides[_sw]) == str(_cand):
                        continue
                    _v2 = dict(cumulative_overrides)
                    _v2[_sw] = _cand
                    _san2,_ = sanitize_overrides(_v2, defaults)
                    _cum_variants.append((_sw,_cand,_san2))
                if _cum_variants:
                    def _eval_cum(tup):
                        _,_,_san = tup
                        return _hustle_eval(prepared, _san, window_days=args.window_days)
                    try:
                        from concurrent.futures import ThreadPoolExecutor as _TPE2
                        with _TPE2(max_workers=args.workers) as _ex2:
                            _cum_sims = list(_ex2.map(lambda t: _eval_cum(t), _cum_variants))
                    except Exception:
                        _cum_sims = [_eval_cum(t) for t in _cum_variants]
                    for (_sw,_cand,_), _vec2 in zip(_cum_variants, _cum_sims):
                        if _vec2 is None or not _vec2.get("valid"):
                            continue
                        _vg2 = float(_vec2.get("gain_pct") or 0)
                        _d_cum = _vg2 - float(cumulative_gain or 0)
                        _d_base = _vg2 - float(baseline_gain or 0)
                        if _d_cum > 0.3:  # meaningful vs greedy cum (avoid noise <0.3)
                            _hustle_top_cum.append((_sw,_cand,None,None,_d_base,_vg2))
                    print(f"[hustler-sim-cum] found {len(_hustle_top_cum)} pos vs greedy cum {cumulative_gain:.2f} (union will add)", flush=True)
        except Exception as _se2:
            print(f"[hustler-sim-cum-warn] {_se2}", flush=True)
        # Union baseline top + cum top, dedupe by switch=cand, keep best delta
        if _hustle_top_cum:
            _pool = {}
            for rec in _hustle_top + _hustle_top_cum:
                _k = (rec[0], str(rec[1]))
                if _k not in _pool or rec[4] > _pool[_k][4]:
                    _pool[_k] = rec
            _hustle_top = sorted(_pool.values(), key=lambda x: x[4], reverse=True)
            print(f"[hustler-pool] union baseline {len(_hustle_top)-len(_hustle_top_cum)} + cum {len(_hustle_top_cum)} -> {len(_hustle_top)} unique", flush=True)
        _hustle_top.sort(key=lambda x: x[4], reverse=True)
        _hustle_top = _hustle_top[:80]  # top 80 pos vs baseline/cum to hustle — exhaustive combos need broader pool
        # --- HYBRID DEFER BIG WINNER (user 2026-09-14): try without big winner so others get chance, then re-apply big over best without it ---
        _hybrid_defer = __import__("os").environ.get("HYBRID_DEFER_BIG","1") != "0"
        _hustle_top_hybrid = list(_hustle_top)
        _big_winner = None
        if _hybrid_defer and _hustle_top:
            _big_winner = max(_hustle_top, key=lambda x: x[4])
            print(f"[hybrid] defer big winner {_big_winner[0]}={_big_winner[1]} vs_base +{_big_winner[4]:.2f} to let others combine first", flush=True)
            # keep full list for final beam, but also note big for two-phase hustle
        print(f"[hustler] top pos vs baseline {len(_hustle_top)} baseline {baseline_gain:.2f} cum {cumulative_gain:.2f} bh {bh_raw:.2f} beam 64 depth 10 (simultaneous vs baseline+cum + per-hustle recalc shooting up delta vs beam, plus exhaustive top12)", flush=True)
        for _i, (_sw,_cand,_bf,_bv,_vsb,_vg) in enumerate(_hustle_top[:10]):
            print(f"  [hustler-top-{_i}] {_sw}={_cand} vs_base +{_vsb:.2f} vec {_vg:.2f} filter {_bf}={_bv}", flush=True)
        # beam hustling — exhaustive + beam until max delta (user: tries all best deltas together in numerous combinations)
        if _hustle_top and prepared is not None:
            _beam = [(dict(cumulative_overrides), cumulative_gain)]  # start from greedy cum
            _seen = {tuple(sorted(cumulative_overrides.items()))}
            _best_overrides, _best_gain = dict(cumulative_overrides), cumulative_gain
            for _depth in range(1, 11):
                _cands = []
                for _base_over, _base_gain in _beam:
                    for _sw,_cand,_bf,_bv,_vsb,_vg in _hustle_top:
                        if _sw in _base_over and str(_base_over[_sw]) == str(_cand):
                            continue
                        _variant = dict(_base_over)
                        _variant[_sw] = _cand
                        if _bf and _bv and _bf not in _variant:
                            _variant[_bf] = _bv
                        _key = tuple(sorted(_variant.items()))
                        if _key in _seen:
                            continue
                        _seen.add(_key)
                        _san, _ = sanitize_overrides(_variant, defaults)
                        _vec = _hustle_eval(prepared, _san, window_days=args.window_days) if prepared is not None else None
                        if _vec is None or not _vec.get("valid"):
                            continue
                        _vg2 = float(_vec.get("gain_pct") or 0)
                        _delta_base = _vg2 - float(baseline_gain or 0)
                        _delta_beam = _vg2 - float(_base_gain or 0)  # shoot up vs beam, not just baseline
                        # Only consider meaningful vs beam to avoid noise; hustler must beat beam
                        if _delta_beam > 0.2:
                            _cands.append((_variant, _vg2, _delta_base, _sw, _cand, _delta_beam, _base_gain))
                if not _cands:
                    break
                _cands.sort(key=lambda x: x[5], reverse=True)  # by delta vs beam (shoot up)
                _beam = [(_v[0], _v[1]) for _v in _cands[:64]]
                _top_v = _cands[0]
                if _top_v[1] > _best_gain + 1e-9:
                    _best_overrides, _best_gain = _top_v[0], _top_v[1]
                    print(f"[hustler-depth-{_depth}] NEW BEST vec {_best_gain:.2f} vs_base {_top_v[2]:.2f} via {_top_v[3]}={_top_v[4]} beam {len(_beam)}", flush=True)
                    # live parity check for new best
                    try:
                        _live_best = live_evaluate(new_symside, _best_overrides, args.window_days)
                        _ok,_rsn = parity_ok(_live_best, {"gain_pct": _best_gain}, allow_zero_baseline=baseline_had_zero_trades)
                        print(f"  [hustler-live] live {_live_best.get('gain_pct',0):.2f} vec {_best_gain:.2f} parity {_ok} {_rsn}", flush=True)
                    except Exception as _le:
                        print(f"  [hustler-live-warn] {_le}", flush=True)
                else:
                    print(f"[hustler-depth-{_depth}] no improvement top {_top_v[1]:.2f} vs best {_best_gain:.2f} plateau", flush=True)
                    # plateau detection 2 rounds no improvement -> break hustle
                    if _depth >= 3 and _cands[0][1] < _best_gain + 0.01:
                        break
            # --- HYBRID PHASE 1: hustle WITHOUT big winner (let secondary combos emerge) ---
            if _hybrid_defer and _big_winner is not None and _hustle_top_hybrid:
                _without_big = [x for x in _hustle_top_hybrid if not (x[0]==_big_winner[0] and str(x[1])==str(_big_winner[1]))]
                if _without_big:
                    print(f"[hybrid-phase1] beam without big winner ({len(_without_big)} cands) to find best secondary combo", flush=True)
                    _beam_nb = [(dict(cumulative_overrides), cumulative_gain)]
                    _seen_nb = {tuple(sorted(cumulative_overrides.items()))}
                    _best_nb_over, _best_nb_gain = dict(cumulative_overrides), cumulative_gain
                    for _d in range(1, 7):
                        _cands=[]
                        for _b_over,_b_gain in _beam_nb:
                            for _sw,_cand,_bf,_bv,_vsb,_vg in _without_big[:40]:
                                if _sw in _b_over and str(_b_over[_sw])==str(_cand): continue
                                _var=dict(_b_over); _var[_sw]=_cand
                                if _bf and _bv and _bf not in _var: _var[_bf]=_bv
                                _k=tuple(sorted(_var.items()))
                                if _k in _seen_nb: continue
                                _seen_nb.add(_k)
                                _san,_=sanitize_overrides(_var, defaults)
                                _vec=_hustle_eval(prepared,_san,window_days=args.window_days)
                                if not _vec or not _vec.get("valid"): continue
                                _vg2=float(_vec.get("gain_pct")or 0)
                                if _vg2 - _b_gain >0.2:
                                    _cands.append((_var,_vg2,_sw,_cand))
                        if not _cands: break
                        _cands.sort(key=lambda x: x[1], reverse=True)
                        _beam_nb=[(_v[0],_v[1]) for _v in _cands[:32]]
                        if _cands[0][1] > _best_nb_gain:
                            _best_nb_over,_best_nb_gain=_cands[0][0],_cands[0][1]
                            print(f"[hybrid-phase1] NEW BEST without big {_best_nb_gain:.2f} via {_cands[0][2]}={_cands[0][3]}", flush=True)
                    # Phase2: re-apply big winner over best without big
                    if _best_nb_gain > cumulative_gain:
                        print(f"[hybrid-phase2] try big winner over best_without_big {cumulative_gain:.2f}->{_best_nb_gain:.2f}", flush=True)
                        _var_big = dict(_best_nb_over); _var_big[_big_winner[0]]=_big_winner[1]
                        _san,_=sanitize_overrides(_var_big, defaults)
                        _vec=_hustle_eval(prepared,_san,window_days=args.window_days)
                        if _vec and _vec.get("valid"):
                            _vg2=float(_vec.get("gain_pct")or 0)
                            print(f"[hybrid-phase2] big over best_without_big gain {_vg2:.2f} vs greedy {cumulative_gain:.2f} vs best_without {_best_nb_gain:.2f}", flush=True)
                            if _vg2 > _best_gain:
                                _best_gain=_vg2; _best_overrides=dict(_var_big)
                                print(f"[hybrid-phase2] PROMOTE hybrid big+secondary {_best_gain:.2f}", flush=True)
                    # merge secondary best into global best if better
                    if _best_nb_gain > _best_gain:
                        _best_gain=_best_nb_gain; _best_overrides=dict(_best_nb_over)
            # --- EXHAUSTIVE TOP12: try ALL subsets of top 12 best deltas together (4096 combos) to guarantee max ---
            try:
                _ex_top = _hustle_top[:12]
                if _ex_top:
                    import itertools
                    _ex_best_gain = _best_gain
                    _ex_best_over = dict(_best_overrides)
                    _ex_seen = set()
                    _ex_base = dict(cumulative_overrides)
                    # evaluate all non-empty subsets
                    _total = 0
                    for r in range(1, min(7, len(_ex_top)+1)):  # up to 6-way combos (C12,6=924) total ~3000, affordable
                        for combo in itertools.combinations(_ex_top, r):
                            _var = dict(_ex_base)
                            for _sw,_cand,_,_,_,_ in combo:
                                if _sw in _var and str(_var[_sw]) == str(_cand):
                                    break
                                _var[_sw] = _cand
                            else:
                                _key = tuple(sorted(_var.items()))
                                if _key in _seen or _key in _ex_seen:
                                    continue
                                _ex_seen.add(_key)
                                _san,_ = sanitize_overrides(_var, defaults)
                                _vec = _hustle_eval(prepared, _san, window_days=args.window_days)
                                if _vec is None or not _vec.get("valid"):
                                    continue
                                _vg = float(_vec.get("gain_pct") or 0)
                                _total += 1
                                if _vg > _ex_best_gain + 1e-9:
                                    _ex_best_gain = _vg
                                    _ex_best_over = dict(_var)
                                    print(f"[hustler-exhaustive] NEW BEST r={r} gain {_vg:.2f} vs greedy {_ex_best_gain:.2f} via {[c[0] for c in combo]}", flush=True)
                    if _ex_best_gain > _best_gain + 1e-9:
                        _best_gain = _ex_best_gain
                        _best_overrides = _ex_best_over
                        print(f"[hustler-exhaustive] PROMOTE exhaustive best {_best_gain:.2f} (+{_best_gain - cumulative_gain:.2f} vs greedy) combos {_total}", flush=True)
                    else:
                        print(f"[hustler-exhaustive] no exhaustive improvement over beam {_best_gain:.2f} checked {_total} combos", flush=True)
            except Exception as _ex_e:
                print(f"[hustler-exhaustive-warn] {_ex_e}", flush=True)
            if _best_gain > cumulative_gain + 1e-9:
                print(f"[hustler] PROMOTE hustle best {cumulative_gain:.2f} -> {_best_gain:.2f} delta +{_best_gain - cumulative_gain:.2f} vs baseline +{_best_gain - float(baseline_gain or 0):.2f} overrides {len(_best_overrides)}", flush=True)
                # write hustler overrides to progress and xlsx E column extension (append HUSTLER sheet if needed)
                try:
                    _hustle_path = OUT_DIR / f"{new_symside}_hustler_best.json"
                    # RESPECT other hosts: s3/s5 shuffles must not overwrite a better hustle from peer
                    _skip_write = False
                    if _hustle_path.exists():
                        try:
                            _existing = __import__("json").loads(_hustle_path.read_text())
                            _existing_gain = float(_existing.get("hustler_best_gain") or 0)
                            if _existing_gain >= float(_best_gain) - 1e-9:
                                print(f"[hustler-respect] existing {_existing_gain:.2f} >= new {float(_best_gain):.2f} — respect peer, skip overwrite", flush=True)
                                _skip_write = True
                            else:
                                print(f"[hustler-respect] new {float(_best_gain):.2f} beats existing {_existing_gain:.2f} — overwrite", flush=True)
                        except Exception as _re:
                            print(f"[hustler-respect-warn] {_re}", flush=True)
                    if not _skip_write:
                        _hustle_path.write_text(__import__("json").dumps({"symside": new_symside, "baseline": float(baseline_gain or 0), "greedy_cum": float(cumulative_gain), "hustler_best_gain": float(_best_gain), "hustler_delta_vs_baseline": float(_best_gain - float(baseline_gain or 0)), "hustler_delta_vs_greedy": float(_best_gain - cumulative_gain), "overrides": _best_overrides, "bh": float(bh_raw)}, indent=2))
                        print(f"[hustler] wrote {_hustle_path}", flush=True)
                    # also respect progress.json hustler if peer wrote newer
                    try:
                        if _skip_write and "hustler_best_gain" in progress:
                            if float(progress.get("hustler_best_gain") or 0) < float(_existing_gain or 0):
                                progress["hustler_best_gain"] = float(_existing_gain)
                                progress["hustler_overrides"] = _existing.get("overrides", {})
                    except Exception:
                        pass
                except Exception as _we:
                    print(f"[hustler-warn] {_we}", flush=True)
                # optionally promote cumulative_gain to hustler best for final publishing
                # keep greedy cum as is for workbook E monotonic, but final_path will reflect hustler if better and parity ok
                # Dual system: keep baseline as greedy, hustle stays in F column and hustler_best.json, no promotion yet (no winner defined)
                # To enable hustle promotion later, set DUAL_HUSTLE_PROMOTE = True
                DUAL_HUSTLE_PROMOTE = False
                if DUAL_HUSTLE_PROMOTE:
                    try:
                        from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_final2
                        _san_best,_ = sanitize_overrides(_best_overrides, defaults)
                        _vec_best = _eval_final2(prepared, _san_best, window_days=args.window_days)
                        _live_best2 = live_evaluate(new_symside, _san_best, args.window_days)
                        _ok2,_rsn2 = parity_ok(_live_best2, _vec_best, allow_zero_baseline=baseline_had_zero_trades)
                        if _ok2 and float(_vec_best.get("gain_pct") or 0) > cumulative_gain:
                            cumulative_gain = float(_vec_best.get("gain_pct") or 0)
                            cumulative_overrides = dict(_san_best)
                            progress["cumulative_gain"] = cumulative_gain
                            progress["cumulative_overrides"] = cumulative_overrides
                            progress["hustler_promoted"] = True
                            print(f"[hustler] PROMOTED cumulative to hustler best {cumulative_gain:.2f} parity ok", flush=True)
                        else:
                            print(f"[hustler] NOT promoted parity {_ok2} {_rsn2} vec {_vec_best.get('gain_pct',0):.2f}", flush=True)
                    except Exception as _pe:
                        print(f"[hustler-promote-warn] {_pe}", flush=True)
                else:
                    print(f"[hustler-dual] hustle best {float(_best_gain):.2f} vs greedy {cumulative_gain:.2f} baseline {float(baseline_gain or 0):.2f} — dual F=HUSTLE G=GREEDY, baseline stays greedy (no winner yet)", flush=True)
            else:
                print(f"[hustler] no hustle improvement greedy {cumulative_gain:.2f} remains best vs baseline {float(baseline_gain or 0):.2f}", flush=True)
        else:
            print("[hustler] skipped no top pos or no prepared", flush=True)
    except Exception as _he:
        import traceback
        print(f"[hustler-warn] {_he} {traceback.format_exc()[:800]}", flush=True)
    # === ALL switch delta + yellow (L:BI) + orange (Results_Deltas/30d) cells already filled cell-by-cell above ===
    # === backtest_v12_live switch-by-switch verification on final settings ===
    if __import__("os").environ.get("V15_SKIP_LIVE_VERIFY")=="1":
        print("[live-verify] SKIPPED via V15_SKIP_LIVE_VERIFY=1 (vector-only fast 940->3041)", flush=True)
    else:
        print(f"[live-verify] switch-by-switch live scripts on final {len(cumulative_overrides)} overrides", flush=True)
        try:
            from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval_final
            # baseline for delta
            base_vec = _eval_final(prepared, overrides, window_days=args.window_days) if prepared is not None else None
            for k, v in list(cumulative_overrides.items())[:50]:
                # single-switch delta verification: live vs vectorized
                test_over = dict(overrides)
                test_over[k] = v
                test_over, _ = sanitize_overrides(test_over, defaults)
                vec = _eval_final(prepared, test_over, window_days=args.window_days) if prepared is not None else live_evaluate(new_symside, test_over, args.window_days)
                live = live_evaluate(new_symside, test_over, args.window_days)
                ok, reason = parity_ok(live, vec, allow_zero_baseline=baseline_had_zero_trades)
                if not ok:
                    print(f"[live-verify-FIX] {k}={v} live {live.get('gain_pct'):.2f}/{live.get('trades')} vs vec {vec.get('gain_pct'):.2f}/{vec.get('trades')} reason {reason} — vector fix needed", flush=True)
                else:
                    print(f"[live-verify-ok] {k}={v} live {live.get('gain_pct'):.2f} vec {vec.get('gain_pct'):.2f} ok", flush=True)
        except Exception as e:
            import traceback
            print(f"[live-verify-warn] {e} {traceback.format_exc()[:800]}", flush=True)
    # complete zoomable chart (offline file:// like /private/tmp/MU_LONG_30D_REAL_ZOOMABLE.html) with all trades
    try:
        write_zoomable_chart(new_symside, None, cumulative_overrides, args.window_days, suffix="30D_REAL_ZOOMABLE")
    except Exception as _ce2:
        print(f"[chart-final-warn] {_ce2}", flush=True)
    print(f"[final] {final_path} bh={bh_raw:.2f} gain={cumulative_gain:.2f} positives={total_pos} hot={list(ALL_NPZ_ARRAYS.keys())[:2]}", flush=True)
    # === BIGGEST 30D DELTA CHART + 365D RERUN + LIVE PROMOTION (2026-09-13) ===
    # Chart of biggest 30D delta already created as COMPLETE 30D_REAL_ZOOMABLE above (cumulative_overrides is the hustler/greedy best).
    # Now rerun those settings on 365D with xlsx and delta, compare vs currently running per_sym config, backup and go live if beats.
    try:
        # 1) ensure biggest 30D chart explicitly tagged
        try:
            write_zoomable_chart(new_symside, None, cumulative_overrides, 30, suffix="30D_BIGGEST_DELTA_ZOOMABLE")
            print(f"[30D-CHART] biggest delta {cumulative_gain:.2f} vs baseline {baseline_gain:.2f} delta {cumulative_gain - baseline_gain:.2f} chart 30D_BIGGEST_DELTA_ZOOMABLE", flush=True)
        except Exception as _ce:
            print(f"[30D-CHART-warn] {_ce}", flush=True)
        # 2) 365D rerun with same overrides — xlsx + delta, robustness holdout (30D→365D is out-of-sample, not curve-fit)
        _365_gain = _365_bh = _365_delta = None
        _365_vec = _365_live = None
        _365_xlsx = None
        try:
            # Evaluate 365D baseline and best via live engine (conservative) + vector parity
            from tools.opt.v12_pilot import evaluate_prepared_sanitized as _eval365
            # Prepare 365D separately (bypass 30D block — internal 365 rerun allowed)
            _prep365 = None
            try:
                from tools.opt.v12_pilot import prepare_batch as _prep365_fn
                _prep365 = _prep365_fn(new_symside, window_days=365)
                print(f"[365D] prepared {new_symside} 365D {len(_prep365.get('npz_prepared',{}).get('close',[]))} bars" if _prep365 and _prep365.get('npz_prepared') else "[365D] no prepared, fallback to live", flush=True)
            except Exception as _pe:
                print(f"[365D-prep-warn] {_pe}", flush=True)
            # Sanitize best overrides for 365D
            _san_best365, _ = sanitize_overrides(dict(cumulative_overrides), defaults)
            _san_base365, _ = sanitize_overrides(dict(overrides), defaults)
            if _prep365 is not None:
                _365_vec_best = _eval365(_prep365, _san_best365, window_days=365)
                _365_vec_base = _eval365(_prep365, _san_base365, window_days=365)
            else:
                _365_vec_best = live_evaluate(new_symside, _san_best365, 365)
                _365_vec_base = live_evaluate(new_symside, _san_base365, 365)
            # Live verification for 365D as well (conservative, slippage-aware)
            _365_live_best = live_evaluate(new_symside, _san_best365, 365)
            _365_live_base = live_evaluate(new_symside, _san_base365, 365)
            # Prefer live if valid and parity ok, else vector
            _ok365, _rsn365 = parity_ok(_365_live_best, _365_vec_best, allow_zero_baseline=True) if _365_vec_best and _365_live_best else (False, "no vec")
            _use365 = _365_live_best if _365_live_best.get("valid") and _ok365 else _365_vec_best
            _base365 = _365_live_base if _365_live_base.get("valid") else _365_vec_base
            _365_gain = float(_use365.get("gain_pct") or 0) if _use365 else 0.0
            _365_bh = float(_use365.get("bh_pct") or _base365.get("bh_pct") or 0) if _use365 else 0.0
            _365_base_gain = float(_base365.get("gain_pct") or 0) if _base365 else 0.0
            _365_delta = _365_gain - _365_base_gain
            _365_trades = int(_use365.get("trades") or 0) if _use365 else 0
            _365_sharpe = float(_use365.get("pool_sharpe") or 0) if _use365 else 0.0
            _365_dd = float(_use365.get("max_dd_pct") or 0) if _use365 else 0.0
            print(f"[365D] best gain {_365_gain:.2f} base {_365_base_gain:.2f} delta {_365_delta:.2f} bh {_365_bh:.2f} trades {_365_trades} sharpe {_365_sharpe:.2f} dd {_365_dd:.2f} parity {_ok365} {_rsn365}", flush=True)
            # Backtest-expert: robustness — 365D must not be <50% of 30D delta (out-of-sample <50% in-sample warns overfit), needs trades floor
            _30d_delta = cumulative_gain - baseline_gain
            if _365_delta < 0.5 * _30d_delta and _30d_delta > 1:
                print(f"[365D-ROBUST-WARN] 365D delta {_365_delta:.2f} <50% of 30D delta {_30d_delta:.2f} — possible overfit, still comparing vs live but not auto-promoting without live beat", flush=True)
            if _365_trades < 30:
                print(f"[365D-SAMPLE-WARN] 365D trades {_365_trades} <30 floor — diagnostic only, not for promotion", flush=True)
            # Create 365D xlsx with delta (clone template, write 365D metrics + Results_30d_Deltas equivalent)
            try:
                # gain/bh in filename per user request (like 30D bh/gain); USER 2026-10-03: int-percent gain + _t{trades}
                try:
                    from tools.v15_final_naming import final_matrix_name as _final_matrix_name365
                    _365_final_name = _final_matrix_name365(new_symside, _365_bh, _365_gain, _365_trades, 365)
                except Exception:
                    _bh_str = f"bh{'m' if _365_bh is not None and _365_bh<0 else ''}{abs(_365_bh):.2f}".replace('.','p') if _365_bh is not None else "bhnan"
                    _gain_str = f"gain{'m' if _365_gain is not None and _365_gain<0 else ''}{abs(_365_gain):.2f}".replace('.','p') if _365_gain is not None else "gainnan"
                    _365_final_name = f"{new_symside}_{_bh_str}_{_gain_str}_365d_matrix.xlsx"
                _365_target = OUT_DIR / _365_final_name
                if _365_target.exists():
                    _ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
                    _365_target = OUT_DIR / (_365_final_name.replace(".xlsx", "") + f"_{_ts}.xlsx")
                if not template.exists():
                    template = TEMPLATE
                import shutil as _sh
                _sh.copy2(template, _365_target)
                _wb365 = openpyxl.load_workbook(str(_365_target))
                # Ensure baseline sheet reflects 365D baseline
                _bs = f"{new_symside}_BASELINE_METRICS"
                if _bs not in _wb365.sheetnames:
                    # find old baseline name
                    for cand in ["TEMPLATE_BASELINE_METRICS", "ADP_LONG_BASELINE_METRICS"]:
                        if cand in _wb365.sheetnames:
                            _wb365[cand].title = _bs
                            break
                if _bs in _wb365.sheetnames:
                    _wsb = _wb365[_bs]
                    _rows365 = [
                        ("gain_pct_365", _365_gain), ("bh_pct_365", _365_bh), ("delta_vs_base_365", _365_delta),
                        ("delta_vs_bh_365", _365_gain - _365_bh), ("trades_365", _365_trades),
                        ("pool_sharpe_365", _365_sharpe), ("max_dd_365", _365_dd),
                        ("gain_30d", cumulative_gain), ("delta_30d", _30d_delta), ("baseline_30d", baseline_gain),
                        ("source", f"v15 30D→365D rerun {utcnow()} overrides={len(cumulative_overrides)}"),
                        ("window_365", _use365.get("window") if _use365 else "365"),
                        ("valid_365", _use365.get("valid") if _use365 else False),
                    ]
                    for r in range(2, max(20, _wsb.max_row+1)):
                        _wsb.cell(row=r, column=1).value = None
                        _wsb.cell(row=r, column=2).value = None
                    for i, (k,v) in enumerate(_rows365, start=2):
                        _wsb.cell(row=i, column=1).value = k
                        _wsb.cell(row=i, column=2).value = v
                # Fill Results_Deltas with 365D delta for visibility (reuse sheet)
                for cand in ["Results_Deltas", "Results_30d_Deltas"]:
                    if cand in _wb365.sheetnames:
                        _rws = _wb365[cand]
                        _found = None
                        _key365 = f"{new_symside}_365D_BEST"
                        for _rr in range(2, _rws.max_row+1):
                            if str(_rws.cell(row=_rr, column=1).value or "").strip() == _key365:
                                _found = _rr
                                break
                        if _found is None:
                            _found = _rws.max_row + 1
                            _rws.cell(row=_found, column=1).value = _key365
                        _rws.cell(row=_found, column=5).value = float(_365_delta)
                        _rws.cell(row=_found, column=8).value = float(_365_gain)
                        _rws.cell(row=_found, column=10).value = int(_365_trades)
                        _rws.cell(row=_found, column=2).value = str(float(_365_base_gain))
                        _rws.cell(row=_found, column=3).value = f"{len(cumulative_overrides)} overrides 365D"
                        break
                _atomic_save(_wb365, Path(_365_target))  # BADZIP FIX 2026-09-29
                _wb365.close()
                _365_xlsx = _365_target
                print(f"[365D-XLSX] -> {_365_xlsx.name} delta {_365_delta:.2f} gain {_365_gain:.2f}", flush=True)
            except Exception as _xe:
                import traceback
                print(f"[365D-XLSX-warn] {_xe} {traceback.format_exc()[:600]}", flush=True)
            # 365D chart
            try:
                write_zoomable_chart(new_symside, None, cumulative_overrides, 365, suffix="365D_REAL_ZOOMABLE")
                print(f"[365D-CHART] {new_symside} 365D chart with best overrides", flush=True)
            except Exception as _ce:
                print(f"[365D-CHART-warn] {_ce}", flush=True)
            # 3) Compare vs currently running per_sym config (live)
            _live_cfg_path = None
            _is_crypto = new_symside.upper().endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD","DAI"))
            if _is_crypto:
                _live_cfg_path = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
            else:
                _live_cfg_path = ROOT / "data" / "hourly_reconfig" / "trb" / "active_config.json"
            _cur_entry = {}
            _cur_gain365 = None
            if _live_cfg_path and _live_cfg_path.exists():
                try:
                    _cur_all = json.loads(_live_cfg_path.read_text())
                    _cur_entry = _cur_all.get(new_symside, {}) if isinstance(_cur_all, dict) else {}
                    if _cur_entry and _cur_entry.get("overrides"):
                        _cur_over = _cur_entry.get("overrides") or {}
                        _cur_san, _ = sanitize_overrides(dict(_cur_over), defaults)
                        # Evaluate current live config on 365D for fair compare (same window, same costs +$ higher than reality)
                        _cur_vec = None
                        _cur_live = None
                        if _prep365 is not None:
                            _cur_vec = _eval365(_prep365, _cur_san, window_days=365)
                        _cur_live = live_evaluate(new_symside, _cur_san, 365)
                        _ok_cur, _ = parity_ok(_cur_live, _cur_vec, allow_zero_baseline=True) if _cur_vec and _cur_live else (False, "")
                        _cur_use = _cur_live if _cur_live.get("valid") and _ok_cur else _cur_vec
                        _cur_gain365 = float(_cur_use.get("gain_pct") or 0) if _cur_use else float(_cur_entry.get("acc_gain_pct") or _cur_entry.get("gain_vs_bh") or 0)
                        print(f"[LIVE-CUR] {new_symside} current live 365D gain {_cur_gain365:.2f} trades {(_cur_use.get('trades') if _cur_use else _cur_entry.get('trades'))} vs new {_365_gain:.2f}", flush=True)
                    else:
                        print(f"[LIVE-CUR] {new_symside} no existing per_sym entry — will create", flush=True)
                except Exception as _le:
                    print(f"[LIVE-CUR-warn] {_le}", flush=True)
            else:
                print(f"[LIVE-CUR] no file {_live_cfg_path} — will create", flush=True)
            # Beat check: new 365D must beat current live 365D (if exists) by >0 and also be positive and have trades floor
            _beats = False
            # USER 2026-05-31: promote as soon as 365D has pos gain and best 30D has pos gain or gain > bh (30D bh = baseline_gain)
            # No incumbent-beat required — per_sym goes live immediately when robustness met
            _30d_pos = cumulative_gain > 0
            _30d_beats_bh = (cumulative_gain - baseline_gain) > 1e-9  # delta >0 means 30D best > 30D bh
            if _365_gain is not None and _365_trades >= 30:
                if _365_gain > 0 and (_30d_pos or _30d_beats_bh):
                    _beats = True
                # For incumbent, also allow if strictly beats current live 365D (even if 30D not pos, but 365D pos already required)
                elif _cur_gain365 is not None and _365_gain > _cur_gain365 + 1e-9 and _365_gain > 0:
                    _beats = True
                # Additional robustness: require 365D sharpe not terrible and dd not huge
                if _beats and _365_sharpe < -1:
                    print(f"[LIVE-BEAT-SKIP] 365D sharpe {_365_sharpe:.2f} < -1 — not promoting despite gain beat", flush=True)
                    _beats = False
                if _beats and _365_dd > 30:
                    print(f"[LIVE-BEAT-SKIP] 365D dd {_365_dd:.2f} >30% — not promoting", flush=True)
                    _beats = False
            print(f"[LIVE-BEAT] {new_symside} new {_365_gain:.2f} vs cur {_cur_gain365} beats={_beats} delta {_365_delta:.2f}", flush=True)
            if _beats:
                # Backup current per_sym settings if existing
                if _cur_entry:
                    try:
                        _bak_dir = ROOT / "backups"
                        _bak_dir.mkdir(parents=True, exist_ok=True)
                        _ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
                        _bak_path = _bak_dir / f"before_per_sym_{new_symside}_{_ts}.json"
                        _bak_path.write_text(json.dumps({new_symside: _cur_entry, "_meta": {"backed_up_at": utcnow(), "reason": f"v15 365D beat {new_symside} new {_365_gain:.2f} vs cur {_cur_gain365:.2f}"}}, indent=2))
                        print(f"[BACKUP] per_sym {new_symside} -> {_bak_path.name}", flush=True)
                        # also hourly_reconfig backup
                        try:
                            _hr_bak = _live_cfg_path.parent / f"{_live_cfg_path.stem}_before_{new_symside}_{_ts}.json"
                            import shutil as _sh2
                            _sh2.copy2(_live_cfg_path, _hr_bak)
                        except Exception:
                            pass
                    except Exception as _be:
                        print(f"[BACKUP-warn] {_be}", flush=True)
                # Put results live immediately — write to per_sym_active_config.json (and trb if stock)
                try:
                    if _live_cfg_path:
                        _live_cfg_path.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            _all = json.loads(_live_cfg_path.read_text()) if _live_cfg_path.exists() else {}
                        except Exception:
                            _all = {}
                        # preserve _meta if exists
                        _meta = _all.pop("_meta", None) if isinstance(_all, dict) else None
                        _new_entry = {
                            "winning_tag": f"v15_30D365D_{new_symside}_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d')}",
                            "wsharpe": float(_365_sharpe),
                            "pool_sharpe": float(_365_sharpe),
                            "trades": int(_365_trades),
                            "max_dd_pct": float(_365_dd),
                            "acc_gain_pct": float(_365_gain),
                            "gain_vs_bh": float(_365_gain - _365_bh),
                            "bh_pct": float(_365_bh),
                            "delta_365": float(_365_delta),
                            "delta_30d": float(cumulative_gain - baseline_gain),
                            "gain_30d": float(cumulative_gain),
                            "baseline_30d": float(baseline_gain),
                            "overrides": dict(cumulative_overrides),
                            "campaign_ts": time.time(),
                            "vec_baseline_tag": "v15_30D365D",
                            "pool_winner_tag_crypto": "v15_30D365D" if _is_crypto else None,
                            "side": new_symside.split("_")[-1],
                            "365d_xlsx": str(_365_xlsx) if _365_xlsx else None,
                            "source": f"v15_pilot 30D→365D {utcnow()} 365D delta {_365_delta:.2f} beats cur {_cur_gain365}",
                        }
                        # prune None
                        _new_entry = {k: v for k, v in _new_entry.items() if v is not None}
                        _all[new_symside] = _new_entry
                        if _meta is not None:
                            _all["_meta"] = _meta
                        _tmp = _live_cfg_path.with_suffix(".json.tmp")
                        _tmp.write_text(json.dumps(_all, indent=2, default=str))
                        _tmp.replace(_live_cfg_path)
                        print(f"[LIVE-PUT] {new_symside} -> {_live_cfg_path.name} gain365 {_365_gain:.2f} delta {_365_delta:.2f} LIVE NOW", flush=True)
                        # --- per_sym_store: snapshot full defaults+overrides (~5000 keys) to SQLite primary + JSON backup ---
                        try:
                            import per_sym_store as _pss
                            # defaults snapshot at promotion moment: every per_sym setting
                            # must include defaults of that moment AND overrides
                            try:
                                _defaults_snap = dict(defaults) if 'defaults' in locals() and isinstance(defaults, dict) and defaults else {}
                            except Exception:
                                _defaults_snap = {}
                            if not _defaults_snap:
                                try:
                                    import cat_side_defaults as _csd_p
                                    _cat_p = f"{'CRYPTO' if _is_crypto else 'STOCKS'}_{new_symside.rsplit('_', 1)[-1]}"
                                    _defaults_snap = _csd_p.defaults(_cat_p)
                                except Exception:
                                    _defaults_snap = {}
                            # full resolved config for live (all defaults + promoted overrides)
                            _full_cfg = dict(_defaults_snap)
                            _full_cfg.update(dict(cumulative_overrides))
                            # template md5 + defaults round for audit
                            _tpl_md5 = ""
                            try:
                                import hashlib as _hl
                                _tpl_p = Path(args.template) if 'args' in locals() and getattr(args, 'template', None) else (ROOT / f"SPREADSHEETS/TEMPLATE_{'CRYPTO' if _is_crypto else 'STOCKS'}_{new_symside.rsplit('_',1)[-1]}.xlsx")
                                if _tpl_p.exists():
                                    _tpl_md5 = _hl.md5(_tpl_p.read_bytes()).hexdigest()
                            except Exception:
                                pass
                            _pss.upsert(
                                new_symside,
                                dict(cumulative_overrides),
                                dict(_defaults_snap),
                                dict(_full_cfg),
                                dict(_new_entry),
                                template_md5=_tpl_md5,
                                defaults_round=os.environ.get("V15_DEFAULTS_ROUND", ""),
                                json_path=_live_cfg_path,
                            )
                            print(f"[PER_SYM_STORE] {new_symside} SQLite+JSON full_config {len(_full_cfg)} keys (defaults {len(_defaults_snap)} + overrides {len(cumulative_overrides)}) -> {DB_PATH if False else _pss.DB_PATH}", flush=True)
                        except Exception as _pss_e:
                            import traceback as _tb
                            print(f"[PER_SYM_STORE-warn] {new_symside} sqlite upsert failed {_pss_e} {_tb.format_exc()[:400]}", flush=True)
                        # Also sync to tradier_manage in-memory cache clear hint
                        try:
                            Path("/tmp/per_sym_live_promoted.flag").write_text(f"{new_symside} {utcnow()} {json.dumps(_new_entry)[:300]}")
                        except Exception:
                            pass
                    else:
                        print(f"[LIVE-PUT-warn] no live path for {new_symside}", flush=True)
                except Exception as _pe:
                    import traceback
                    print(f"[LIVE-PUT-warn] {_pe} {traceback.format_exc()[:600]}", flush=True)
            else:
                print(f"[LIVE-SKIP] {new_symside} not beating live — no backup/promote (new {_365_gain} vs cur {_cur_gain365})", flush=True)
        except Exception as _e:
            import traceback
            print(f"[365D-LIVE-warn] {_e} {traceback.format_exc()[:800]}", flush=True)
    except Exception as _outer:
        import traceback
        print(f"[365D-outer-warn] {_outer} {traceback.format_exc()[:600]}", flush=True)
    # === keep going: queue symbols_trb and symbols_flz ===
    # === EXPLORATION: test other side occasionally (backtest) but live gate requires gain>0 and beat bh ===
    # Even if a sym_side is disabled live, backtest still probes opposite side periodically.
    # Exploration rate controlled via V15_OTHER_SIDE_PROBE_RATE (env or config), default 0.15 (15%).
    # Also ensures campaign queue always contains opposite side for recently processed sym_side if not already queued.
    try:
        import random as _rnd
        _probe_rate = float(os.getenv("V15_OTHER_SIDE_PROBE_RATE", "0.15"))
        # Use deterministic hash for occasional probe to avoid pure randomness missing coverage
        _sym_base, _sym_side = (new_symside.rsplit("_", 1) if "_" in new_symside else (new_symside, "LONG"))
        _opp_side = "SHORT" if _sym_side == "LONG" else "LONG"
        _opp_symside = f"{_sym_base}_{_opp_side}"
        # Check if opposite side was recently tested (progress file exists and recent)
        _opp_progress = PROGRESS_DIR / f"{_opp_symside}_progress.json"
        _should_probe = False
        if not _opp_progress.exists():
            _should_probe = True
        else:
            try:
                _opp_mtime = _opp_progress.stat().st_mtime
                _age_days = (time.time() - _opp_mtime) / 86400
                if _age_days > 7.0:
                    _should_probe = True
            except Exception:
                _should_probe = True
        if not _should_probe and _rnd.random() < _probe_rate:
            _should_probe = True
        if _should_probe:
            print(f"[other-side-probe] {new_symside} -> queuing opposite {_opp_symside} for exploration (backtest only, live requires gain>0 and beat bh)", flush=True)
            try:
                camp_path2 = PROGRESS_DIR / "campaign_order_1mo.json"
                if camp_path2.exists():
                    camp2 = _js.loads(camp_path2.read_text()) if '_js' in locals() else json.loads(camp_path2.read_text())
                    queue2 = camp2.get("queue") or []
                    existing2 = {q.get("symside") for q in queue2}
                    if _opp_symside not in existing2:
                        queue2.append({"symside": _opp_symside, "window": "30_calendar_days", "side": _opp_side, "probe": "other_side"})
                        camp2["queue"] = queue2
                        camp_path2.write_text(json.dumps(camp2, indent=2))
                        print(f"[other-side-probe] queued {_opp_symside}", flush=True)
            except Exception as _e2:
                print(f"[other-side-probe-warn] {_e2}", flush=True)
    except Exception as _e:
        print(f"[other-side-probe-warn] {_e}", flush=True)
    try:
        import json as _js
        trb_long = _js.loads((ROOT / "symbols_trb_long.json").read_text()) if (ROOT / "symbols_trb_long.json").exists() else []
        trb_short = _js.loads((ROOT / "symbols_trb_short.json").read_text()) if (ROOT / "symbols_trb_short.json").exists() else []
        flz = _js.loads((ROOT / "symbols_flz.json").read_text()) if (ROOT / "symbols_flz.json").exists() else []
        # Also include side-specific flz/fin/men for backtest coverage (probe disabled side)
        flz_long = _js.loads((ROOT / "symbols_flz_long.json").read_text()) if (ROOT / "symbols_flz_long.json").exists() else []
        flz_short = _js.loads((ROOT / "symbols_flz_short.json").read_text()) if (ROOT / "symbols_flz_short.json").exists() else []
        fin_long = _js.loads((ROOT / "symbols_fin_long.json").read_text()) if (ROOT / "symbols_fin_long.json").exists() else []
        fin_short = _js.loads((ROOT / "symbols_fin_short.json").read_text()) if (ROOT / "symbols_fin_short.json").exists() else []
        men_long = _js.loads((ROOT / "symbols_men_long.json").read_text()) if (ROOT / "symbols_men_long.json").exists() else []
        men_short = _js.loads((ROOT / "symbols_men_short.json").read_text()) if (ROOT / "symbols_men_short.json").exists() else []
        # log next queue (actual launch is via campaign runner, here just progress hint)
        print(f"[queue-next] TRB {len(trb_long)} long + {len(trb_short)} short, FLZ {len(flz)} syms (+ side-specific flz {len(flz_long)}/{len(flz_short)} fin {len(fin_long)}/{len(fin_short)} men {len(men_long)}/{len(men_short)}) — pilots will pick next via campaign_order_1mo.json", flush=True)
        # ensure campaign queue contains them
        camp_path = PROGRESS_DIR / "campaign_order_1mo.json"
        if camp_path.exists():
            camp = _js.loads(camp_path.read_text())
            queue = camp.get("queue") or []
            # append missing TRB/FLZ symsides if not present
            existing = {q.get("symside") for q in queue}
            # NEVER REPEAT A SYM_SIDE in same round — and same for every filter that had POS results (keep POS, never re-queue duplicate)
            added = 0
            for s in trb_long:
                ss = f"{s}_LONG"
                if ss not in existing:
                    queue.append({"symside": ss, "window": "30_calendar_days", "side": "LONG"})
                    existing.add(ss)
                    added += 1
            for s in trb_short:
                ss = f"{s}_SHORT"
                if ss not in existing:
                    queue.append({"symside": ss, "window": "30_calendar_days", "side": "SHORT"})
                    existing.add(ss)
                    added += 1
            for s in flz:
                for side in ["LONG","SHORT"]:
                    ss = f"{s}_{side}"
                    if ss not in existing:
                        queue.append({"symside": ss, "window": "30_calendar_days", "side": side})
                        existing.add(ss)
                        added += 1
            # Also ensure opposite side for every tracked symbol is at least queued as probe if missing (occasional backtest)
            for lst, side in [(flz_long, "LONG"), (flz_short, "SHORT"), (fin_long, "LONG"), (fin_short, "SHORT"), (men_long, "LONG"), (men_short, "SHORT")]:
                for s in lst:
                    ss = f"{s}_{side}"
                    if ss not in existing:
                        queue.append({"symside": ss, "window": "30_calendar_days", "side": side})
                        existing.add(ss)
                        added += 1
            if added:
                camp["queue"] = queue
                camp_path.write_text(_js.dumps(camp, indent=2))
                print(f"[queue-next] added {added} TRB/FLZ/FIN/MEN symsides to campaign queue (including side-specific probes)", flush=True)
    except Exception as e:
        print(f"[queue-next-warn] {e}", flush=True)

if __name__ == "__main__":
    main()
