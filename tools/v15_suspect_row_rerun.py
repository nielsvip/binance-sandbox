#!/usr/bin/env python3
"""SURGICAL suspect-row rerun — USER 2026-10-15 (EMERGENCY, pre-13:30Z).

NOT a board. Starts from each sym_side's EXISTING measured best set (per_sym_store full_config), re-measures the
baseline on the CURRENT NPZ, then tests ONLY the rows and yellow cells the 2026-10-15 coverage collapse
compromised. Minutes per sym_side, not the 20-60 min a full v15_pilot board costs.

WHY (established 2026-10-15, not re-derived here):
  WT15 yellows/row fell 26.20 (run27 Oct 6) -> 0.65 (run32 Oct 12); EXIT-tab zero-yellow share 44.9% -> 93.5%.
  The compromised switches moved 100% one-directionally, which a per-sym search cannot produce:
    EXIT_R1_R2_FILTER_TF    15m->D    109/222 stock, 98/325 crypto  (crypto family is ('15m',) so D is a total SWAP)
    FROZEN_STOP_FILTER_TF   15m->OFF  109 stock
    MTF_ATR_TRAIL_FILTER_TF 15m->OFF  90 stock / 186 crypto
    GAP_MOC_EXIT_ENABLED    True->False  84 stock
    DC_HARD_STOP_15M_ENABLED True->False 31
  Loss attribution (data/reports/v15_loser_replay_guard.json): MTF_ATR_TRAIL OFF -$231, EXIT_R1_R2=D -$226,
  FROZEN_STOP OFF -$210, GAP_MOC False -$192.

ENGINE: the pilot's own path only — tools.opt.v12_pilot.prepare_batch + evaluate_prepared_sanitized. Nothing is
reimplemented. Yellow attachment is v15_pilot._header_disc_precompute/_header_disc_combine + mandatory_bases_for_tab
(switch-aware WT forcing, 2026-10-15). Promotion gate is v15_pilot._qualifies_30d. Delta semantics are
v15_pilot.delta_vs_result. v15_pilot.py is IMPORTED, never modified (it is version-gate candidate 20261015T220419Z).

PARENT GATES (BIBLE §14.3 case 2 — an upstream gate suppressing the trades the switch would touch): three of the
suspect TF switches are inert in the vector unless their parent is armed, so each rung is also tested WITH the
parent armed and the parent-only arm is tested on its own. Measured call sites:
  EXIT_R1_R2_FILTER_TF    <- WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED  v12_quick_engine.py:17110
  FROZEN_STOP_FILTER_TF   <- BB_FROZEN_STOP_ENABLED                v12_quick_engine.py:17089 (crypto only)
  MTF_ATR_TRAIL_FILTER_TF <- MTF_ATR_TRAIL_ENABLED[_TRADIER]       v12_quick_engine.py:17573-17576
A rung whose every variant reads 0.0 with its parent off is reported INERT_IN_VECTOR, never promoted, never fixed
here — vec wiring is not this script's job.

NO-LIES: every promoted value carries sym_side + switch + old -> new + gain before/after + trades + the eval that
produced it, written to data/reports/v15_suspect_row_rerun_*.json. Nothing is extrapolated across sym_sides.

GREEDY (BIBLE §14.2): delta is always vs the RUNNING cumulative; only delta > 0 that also passes _qualifies_30d
promotes; negative/zero keeps the incumbent. A negative winner is never reported.

WRITE PATH: per_sym_store.upsert() with the COMPLETE set (USER 2026-10-15: per_sym is a full set, "overrides" is a
TEMPLATE-only concept). per_sym_active_config*.json is never hand-edited. --apply is OFF by default.

PAUSE: data/fleet_drain_hosts.json, the flag tools/v15_fleet_scheduler.py::_load_drain already honours — a drained
host gets NO new launches and every in-flight board finishes untouched. Reversible in one step; --resume restores
the pre-pause file from backups/.

USAGE
  # 1. dry run (writes nothing)
  python3 tools/v15_suspect_row_rerun.py --sym-sides BATUSDT_LONG,RLCUSDT_SHORT,XTZUSDT_SHORT
  # 2. pause the fleet dispatcher (on S1), then run, then resume
  python3 tools/v15_suspect_row_rerun.py --pause s1,s2
  python3 tools/v15_suspect_row_rerun.py --venue crypto --limit 60 --apply
  python3 tools/v15_suspect_row_rerun.py --resume
"""
import argparse
import json
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("V12_NPZ_CACHE", "32")

# GATE DISCIPLINE (USER 2026-10-15): the fleet's v15_pilot.py stays the champion and is NEVER pushed or modified.
# The yellow-discovery helpers (_header_disc_precompute/_header_disc_combine) and the switch-aware
# mandatory_bases_for_tab live only in candidate 20261015T220419Z, so that file is shipped BESIDE the champion as a
# read-only sidecar (tools/_v15_pilot_for_rerun.py) and imported here. Nothing in the dispatch path changes: the
# scheduler, the herd and every board still load v15_pilot.py. V15_RERUN_PILOT_MODULE overrides the choice.
import importlib

# The sidecar MUST sit at the repo root: v15_pilot derives ROOT from its own __file__, and from tools/ it would look
# for tools/SPREADSHEETS/, tools/data/ and refuse on a missing template / fail open on the unwired lists.
_PILOT_NAME = os.environ.get("V15_RERUN_PILOT_MODULE") or ("_v15_pilot_for_rerun" if (ROOT / "_v15_pilot_for_rerun.py").exists() else "v15_pilot")
PILOT = importlib.import_module(_PILOT_NAME)
import per_sym_store as PSS
from tools.opt import v12_pilot as VP
from vec_decisions.twin_yellow_filters import r2_eff_tfs

TRIAGE = ROOT / "data" / "reports" / "v15_recalc_triage.json"
PILOT_DIR = ROOT / "data" / "reports" / "lifecycle_pilot"
DRAIN_FILE = ROOT / "data" / "fleet_drain_hosts.json"
BACKUP_DIR = ROOT / "backups"
OUT_DIR = ROOT / "data" / "reports"
# r2_eff_tfs family_list defaults are hardcoded at the LIVE call sites, not in any config set
# (stocks tradier_manage.py:16471, crypto ez_manage.py:52406) — same source as tools/v15_loser_replay_guard.py.
R2_FAMILY_CALLSITE = {"stocks": ("1h", "4h", "D"), "crypto": ("15m",)}
TF_LADDER = ["OFF", "15m", "1h", "4h", "D"]
R2_LADDER = ["D", "4h", "1h", "15m"]  # user's order: D -> 4h -> 1h -> 15m hunt for best delta
EXIT_TABS = ["EXIT_STRUCTURAL", "EXIT_VELOCITY", "GLOBAL_RISK_GATES"]

# The suspect set, in the user's priority order (exits first — crypto is bleeding peaks right now).
# "parents": armed together with each rung because the vector gate above makes the TF inert otherwise.
SUSPECTS = [
    {
        "switch": "EXIT_R1_R2_FILTER_TF",
        "ladder": R2_LADDER,
        "parents": {"WT_15M_VEL_SLOW_AT_ZERO_GAIN_ENABLED": True},
        "parent_ref": "v12_quick_engine.py:17110",
        "venue": "both",
        "tabs": EXIT_TABS,
    },
    {
        "switch": "MTF_ATR_TRAIL_FILTER_TF",
        "ladder": [t for t in TF_LADDER if t != "OFF"],
        "parents": {"MTF_ATR_TRAIL_ENABLED": True},
        "parents_stocks": {"MTF_ATR_TRAIL_ENABLED_TRADIER": True},
        "parent_ref": "v12_quick_engine.py:17573-17576",
        "venue": "both",
        "tabs": EXIT_TABS,
    },
    {
        "switch": "FROZEN_STOP_FILTER_TF",
        "ladder": [t for t in TF_LADDER if t != "OFF"],
        "parents": {"BB_FROZEN_STOP_ENABLED": True},
        "parent_ref": "v12_quick_engine.py:17089 (crypto-only branch)",
        "venue": "crypto",
        "tabs": EXIT_TABS,
    },
    {"switch": "WT_LOWER_CROSS_EXIT_TF", "ladder": ["OFF", "15m", "1h", "4h"], "parents": {}, "venue": "both", "tabs": ["EXIT_VELOCITY"]},
]


def log(msg):
    print(msg, flush=True)


def _pilot_md5():
    try:
        import hashlib
        return hashlib.md5(Path(PILOT.__file__).read_bytes()).hexdigest()
    except Exception:
        return ""


def utcnow():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def venue_of(sym_side):
    base = str(sym_side).upper().rsplit("_", 1)[0]
    return "crypto" if base.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")) else "stocks"


def tradeable_universe():
    """Side-exact tradeable set: crypto from tradeable_keys.json (acct:SYM_SIDE), stocks from symbols_trb_*.json
    plus TradierConfig.TRADIER_MANDATORY_*_TRB. Untradeable = zero CPU here (no NPZ build, no backtest)."""
    out = set()
    try:
        keys = json.loads((ROOT / "tradeable_keys.json").read_text())
        keys = keys.get("keys") if isinstance(keys, dict) else keys
        for k in keys or []:
            out.add(str(k).split(":", 1)[-1].strip().upper())
    except Exception as e:
        log(f"[universe-warn] tradeable_keys.json {e}")
    for fname, side in (("symbols_trb_long.json", "LONG"), ("symbols_trb_short.json", "SHORT")):
        try:
            d = json.loads((ROOT / fname).read_text())
            syms = d.get("symbols") if isinstance(d, dict) else d
            for s in syms or []:
                out.add(f"{str(s).strip().upper()}_{side}")
        except Exception as e:
            log(f"[universe-warn] {fname} {e}")
    try:
        from config_tradier import TradierConfig as TC
        for attr, side in (("TRADIER_MANDATORY_LONG_TRB", "LONG"), ("TRADIER_MANDATORY_SHORT_TRB", "SHORT")):
            for s in getattr(TC, attr, None) or []:
                out.add(f"{str(s).strip().upper()}_{side}")
    except Exception as e:
        log(f"[universe-warn] TradierConfig {e}")
    return out


def select_sym_sides(args):
    """CRYPTO FIRST (bleeding now, trades 24/7), then stocks; within a venue the triage tier order."""
    uni = tradeable_universe()
    if args.sym_sides:
        want = [s.strip().upper() for s in args.sym_sides.split(",") if s.strip()]
        skipped = [s for s in want if s not in uni]
        if skipped and not args.allow_untradeable:
            log(f"[universe] REFUSED (not tradeable, zero CPU spent): {','.join(skipped)}")
        return [s for s in want if s in uni or args.allow_untradeable]
    rows = json.loads(TRIAGE.read_text())
    tiers = {int(t) for t in str(args.tiers).split(",") if str(t).strip().isdigit()} if args.tiers else None
    rows = [r for r in rows if str(r.get("sym_side", "")).upper() in uni]
    if tiers:
        rows = [r for r in rows if int(r.get("tier", 9)) in tiers]
    if args.venue != "all":
        rows = [r for r in rows if r.get("venue") == args.venue]
    rows.sort(key=lambda r: (0 if r.get("venue") == "crypto" else 1, int(r.get("tier", 9)), float(r.get("week_usd") or 0.0)))
    out = [str(r["sym_side"]).upper() for r in rows]
    return out[: args.limit] if args.limit else out


_EXPORT_SETS = {}


def load_start_sets(path):
    """MEASURE-HERE / APPLY-THERE (2026-10-15): the NPZ lives on S1 but the LIVE per_sym truth lives on the Mac
    (S1's sandbox store held 444 rows vs the Mac's 740). An export of the Mac's per_sym_active full sets is read
    FIRST so a measurement run on a worker starts from the set live actually trades. Never a template default."""
    global _EXPORT_SETS
    d = json.loads(Path(path).read_text())
    sets = d.get("sets") if isinstance(d, dict) and "sets" in d else d
    if not isinstance(sets, dict) or not sets:
        raise SystemExit(f"--start-sets {path}: no sets found")
    _EXPORT_SETS = {str(k).upper(): v for k, v in sets.items() if isinstance(v, dict) and v}
    log(f"[start-sets] {path}: {len(_EXPORT_SETS)} sym_sides, exported_at={(d.get('exported_at') if isinstance(d, dict) else None) or 'n/a'} from {(d.get('host') if isinstance(d, dict) else None) or 'n/a'}")


def set_md5(ov):
    import hashlib

    return hashlib.md5(json.dumps({k: _j(v) for k, v in sorted((ov or {}).items())}, sort_keys=True).encode()).hexdigest()


def start_set(sym_side):
    """The EXISTING measured best set (never template defaults). An explicit --start-sets export first, then
    per_sym_store full_config (PER_SYM_APPLY_FULL_SET), else the newest lifecycle_pilot progress
    cumulative_overrides."""
    if _EXPORT_SETS:
        ov = _EXPORT_SETS.get(sym_side.upper())
        if ov:
            return dict(ov), "start_sets_export.full_config"
    ov = PSS.get_overrides(sym_side)
    if ov:
        return dict(ov), "per_sym_store.full_config"
    best, best_mt = None, -1.0
    for p in sorted(PILOT_DIR.glob(f"{sym_side}*_v14_progress.json")):
        try:
            mt = p.stat().st_mtime
            d = json.loads(p.read_text())
            co = d.get("cumulative_overrides") or d.get("hustler_overrides")
            if isinstance(co, dict) and co and mt > best_mt:
                best, best_mt = dict(co), mt
        except Exception:
            continue
    return (best, f"lifecycle_pilot progress ({time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(best_mt))})") if best else (None, "NONE")


_TPL_CACHE = {}
_WT_FORCE_FALLBACK = set()  # tabs where the host's v15_pilot has the pre-gate tab-only mandatory_bases_for_tab


def template_view(sym_side, defaults):
    """Per cat_side: {tab: {"rows": [(row, switch, cand, is_default)], "hmap": {"FILTER=opt": col}}}.
    Read-only openpyxl — no .xlsx is ever written (TEMPLATE ONE-SCRIPT RULE)."""
    tpl = PILOT.get_template_for_symside(sym_side)
    if tpl in _TPL_CACHE:
        return _TPL_CACHE[tpl]
    import openpyxl
    wb = openpyxl.load_workbook(tpl, read_only=False, data_only=True)
    view = {}
    try:
        for sname in [s for s in wb.sheetnames if s in PILOT.SWITCH_SHEETS and s not in PILOT.SKIP_SHEETS]:
            ws = wb[sname]
            cols = PILOT._resolve_cols(ws)
            rows = []
            for rr in range(3, ws.max_row + 1):
                sw = ws.cell(row=rr, column=1).value
                if not isinstance(sw, str) or not sw.strip() or sw.startswith("—"):
                    continue
                sw = sw.strip()
                if sw.lower() in ("switch", "general", "blanket", "filter", "option value"):
                    continue
                cand = ws.cell(row=rr, column=2).value
                if cand is None:
                    cand = defaults.get(sw)
                    cand = PILOT.DEFAULT_MARKER if cand is None else cand
                if isinstance(cand, str) and cand.strip().lower() in ("option value", "sheets applicable", "gates"):
                    continue
                isdef = str(ws.cell(row=rr, column=cols["L"]).value or "").strip().upper() == "YES"
                rows.append((rr, sw, cand, isdef))
            hmap = {}
            for cc in range(12, ws.max_column + 50):
                hv = ws.cell(row=2, column=cc).value
                if isinstance(hv, str) and hv.strip().upper().startswith("WHAT SWITCH"):
                    break
                if isinstance(hv, str) and "=" in hv:
                    hmap[hv.strip()] = cc
            view[sname] = {"rows": rows, "hmap": hmap}
    finally:
        try:
            wb.close()
        except Exception:
            pass
    _TPL_CACHE[tpl] = view
    return view


def yellow_headers(view, switch, cand, tabs, defaults):
    """The applicable yellow cells for one (switch=cand), exactly as the pilot attaches them: the >=2 shared
    '_'-token rule + mandatory_bases_for_tab(sheet, switch) (switch-aware WT core forcing), through
    _header_disc_precompute/_header_disc_combine. Not reimplemented here."""
    out = {}
    for sname in tabs:
        tv = view.get(sname)
        if not tv:
            continue
        tab_level = set()
        # GATE DISCIPLINE: the switch-aware signature is v15_pilot candidate 20261015T220419Z, not yet deployed on the
        # fleet. We never push the gated pilot, so fall back to the champion's tab-only call and say so in the record.
        try:
            mand = PILOT.mandatory_bases_for_tab(sname, switch)
        except TypeError:
            mand = PILOT.mandatory_bases_for_tab(sname)
            _WT_FORCE_FALLBACK.add(sname)
            if switch in getattr(PILOT, "WT_CORE_MANDATORY_BASES", ()) and os.environ.get("V15_WT_CORE_MANDATORY", "1") != "0":
                try:
                    mand = set(mand) | (set(PILOT.WT_CORE_MANDATORY_BASES) & PILOT.known_config_fields())
                except Exception:
                    mand = set(mand) | set(PILOT.WT_CORE_MANDATORY_BASES)
                mand.discard(switch)
        pre = PILOT._header_disc_precompute(sname, switch, tv["hmap"], tab_level, mand, defaults)
        # 🔴 USER 2026-10-16: "these have to be the 'yellow box' filters that get applied to ONE SWITCH ONLY —
        # supposedly we have about 80 for each wt15 entry/exit/reentry/augment/reduce switch, THIS is what needs to
        # be tested." The pilot decides that with a 6th argument to _header_disc_combine (v15_pilot.py:5920-5923):
        # ALL filter columns on the tab for a WT-core row, no token rule, nothing skipped — measured 26 -> 127 on
        # WT_LOWER_CROSS_EXIT_TF. This tool passed a hardcoded False, so every WT row here was still getting the
        # >=2-shared-token subset (26) instead of the full filter set. Same env levers as the pilot.
        _all_cols = os.environ.get("V15_ALL_FILTER_COLS", "0") == "1"
        if not _all_cols and switch in getattr(PILOT, "WT_CORE_MANDATORY_BASES", ()) and os.environ.get("V15_WT_CORE_ALL_COLS", "1") != "0":
            _all_cols = True
        # USER 2026-10-16: "MAKE 15 NEW ROWS IN EVERY TEMPLATE FOR REENTRY WITH ALL FILTERS IN YELLOW BOXES AND HAVE
        # THEM TESTED IN THE RERUN - NO SUICIDE REENTRIES BUT SENSIBLE REENTRIES OR OVER EXIT PRICE REENTRIES".
        # A reentry row therefore gets the SAME full filter box as a WT-core row: the filters are what make the
        # reentry sensible (over the exit price, higher low, DC break, WT required) instead of a blind re-open.
        if not _all_cols and switch.startswith(("REENTRY", "OBLIGATORY_REENTRY", "HARDCODED_RALLY", "GUARANTEED_REENTRY", "EZ_REENTRY")) and os.environ.get("V15_REENTRY_ALL_COLS", "1") != "0":
            _all_cols = True
        comb = PILOT._header_disc_combine(pre, sname, switch, cand, set(), _all_cols)
        for hdr in comb.get("hdrs", []):
            if hdr in out:
                continue
            filt, _, opt = hdr.partition("=")
            filt, opt = filt.strip(), opt.strip()
            if filt == switch:
                continue
            try:
                val = PILOT._parse_opt_value(opt, defaults.get(filt))
            except Exception:
                continue
            out[hdr] = {"tab": sname, "filter": filt, "opt": opt, "ov": {filt: val}}
    return out


def r2_legs(ov, venue, raw):
    fam = ov.get("R2_TF_LIST")
    src = "set:R2_TF_LIST"
    if not fam:
        fam, src = R2_FAMILY_CALLSITE[venue], f"live_callsite({venue})"
    try:
        return tuple(r2_eff_tfs(raw, tuple(fam))), src, tuple(fam)
    except Exception:
        return (), src, tuple(fam)


class Evaluator:
    """Every number in this script comes from here — the pilot's own vectorised path, nothing else."""

    def __init__(self, prepared, window_days, workers, timeout):
        self.prepared, self.window, self.timeout = prepared, window_days, timeout
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.n = 0
        self.secs = 0.0

    def one(self, ov):
        t0 = time.time()
        try:
            res = self.pool.submit(VP.evaluate_prepared_sanitized, self.prepared, dict(ov), self.window).result(timeout=self.timeout)
        except Exception as e:
            res = {"valid": False, "invalid_reason": f"eval {type(e).__name__}: {str(e)[:60]}", "gain_pct": None, "trades": 0}
        self.n += 1
        self.secs += time.time() - t0
        return res

    def many(self, ovs):
        t0 = time.time()
        futs = [self.pool.submit(VP.evaluate_prepared_sanitized, self.prepared, dict(o), self.window) for o in ovs]
        out = []
        for f in futs:
            try:
                out.append(f.result(timeout=self.timeout))
            except Exception as e:
                out.append({"valid": False, "invalid_reason": f"eval {type(e).__name__}: {str(e)[:60]}", "gain_pct": None, "trades": 0})
        self.n += len(ovs)
        self.secs += time.time() - t0
        return out

    def close(self):
        self.pool.shutdown(wait=False)


def variants_for(sus, cur_ov, venue):
    """[(label, move_dict)] for one suspect: each non-incumbent rung naked, each rung with its parent armed, and
    the parent-only arm. The incumbent rung is skipped naked (BIBLE §14.1: restating the running value is delta 0)."""
    sw = sus["switch"]
    parents = dict(sus.get("parents_stocks") if venue == "stocks" and sus.get("parents_stocks") else sus.get("parents") or {})
    parents_off = {k: v for k, v in parents.items() if cur_ov.get(k) != v}
    incumbent = cur_ov.get(sw, "<ABSENT>")
    out = []
    for rung in sus["ladder"]:
        if rung != incumbent:
            out.append((f"{sw}={rung}", {sw: rung}))
        if parents_off:
            lab = "+".join([f"{sw}={rung}"] + [f"{k}={v}" for k, v in parents_off.items()])
            out.append((lab, dict({sw: rung}, **parents_off)))
    if parents_off:
        out.append(("+".join(f"{k}={v}" for k, v in parents_off.items()) + " (parent-arm only)", dict(parents_off)))
    return out, incumbent, parents_off


# USER 2026-10-16: "DC_HARD_STOP_15M_ENABLED=False +1.608 is TOO BAD as it is now not testable anymore and a hard
# fact" — switching a hard stop OFF buys gain in a backtest by removing the only thing that bounds the next loss, and
# once it is off the protection it would have given is unobservable. These switches may therefore never be promoted to
# their unprotected value, no matter how large the measured delta. The ladder also drops the OFF rung so the eval is
# never even spent on it.
# USER 2026-10-16: "DC_HARD_STOP_15M_ENABLED can NEVER be switched as it is the bottom stop market order that goes
# with the entry to the exchange." It is not a tunable at all: the order rests AT the exchange, so a backtest that
# flips it is simulating a position that cannot exist. These names are removed from every candidate source — the
# standing suspects, the WT family, and the new-row discovery — so no eval is ever spent on them and no delta for
# them can ever be reported, let alone promoted.
# CORRECTED 2026-10-16 by the user: the non-negotiable stop is the one RESTING AT THE EXCHANGE —
# dc_low_15m/dc_high_15m -/+ 0.25% (0.3% on the market-order stop). That one is never a switch.
# The dc_low4/high4 family is NOT in here: "the low4 are switches that need to be tried on every sym_side like
# everything else", so ULTIMATE_DC_4H_STOP_ENABLED and the VIGILANCE_DC4_* knobs stay testable.
NEVER_TEST = {"DC_HARD_STOP_15M_ENABLED", "FIXED_STOP_BROKER_BACKSTOP_ENABLED",
              "TRADIER_FIXED_STOP_BROKER_BACKSTOP_ENABLED"}


# USER 2026-10-16: switches that are HARD-SET for now are neither tested nor promoted — every eval spent on them is
# an eval not spent on something that can still move. GAP_MOC_EXIT_ENABLED is pinned True ("just hard true for now,
# not something we are interested in"); DC_HARD_STOP_15M_ENABLED keeps whatever the board has and its False rung is
# gone from the ladders ("not testable anymore and a hard fact").
# USER 2026-10-16 ("seems to be trading against the trend indicating the KG filters are still not being applied
# correctly (and GR as well)"). The gates ARE wired and read by the vector — the boards had simply tuned them below
# their own live default, which makes an enabled gate a no-op. Census of the 741 live per_sym sets:
#   GOLDEN_RULE_HTF_MIN_TFS  < config default 1  on 181/414 STOCK sym_sides (and 1/327 crypto)
#   EMA_9_21_FILTER_ENABLED  False where stocks default True on 57/414
#   GOLDEN_RULE_MIN_IND <default on 7, KINDERGARTEN_EMA_GATE_ENABLED off on 4
# CRWV_SHORT (the board the user opened) carried GOLDEN_RULE_HTF_MIN_TFS=0 against a default of 1.
# A start set may therefore not enter the rerun WEAKER than the live default on a trend/alignment gate. This repairs
# to the default, never beyond it, and the cost of the repair is measured and reported per sym_side.
TREND_GATE_FLOORS = ("GOLDEN_RULE_ENABLED", "GOLDEN_RULE_HTF_MIN_TFS", "GOLDEN_RULE_MIN_IND", "GR_HTF_GATE_ENABLED",
                     "KINDERGARTEN_EMA_GATE_ENABLED", "KINDERGARTEN_CUMULATIVE_MIN_TFS", "EMA_9_21_FILTER_ENABLED")


def coerce_set_types(ov, venue):
    """Repair values whose TYPE drifted away from the live config field, using config/config_tradier as the one
    authority (USER 2026-10-16: "QuickConfig can never digress from config/config_tradier").
    Found 2026-10-16: ALB_SHORT's published set carried FILTER_EXEMPTIONS as the STRING "{'REENTRY': [...]}" where
    the live field is a dict, and the evaluator rejects the whole set before running — the sym_side silently drops
    out of the round with BASELINE_UNMEASURABLE instead of being measured. Only literal containers are parsed, with
    ast.literal_eval (never eval), and anything that does not parse is left exactly as it was.
    -> (set, {switch: [was, now]})"""
    import ast

    try:
        if venue == "crypto":
            from config import Config as _C
            live = _C()
        else:
            from config_tradier import TradierConfig as _T
            live = _T()
    except Exception as e:
        log(f"[type-coerce] config unavailable ({e}) — skipped (fail-open)")
        return dict(ov or {}), {}
    out, fixed = dict(ov or {}), {}
    for k, v in list(out.items()):
        lv = getattr(live, k, None)
        if lv is None or callable(lv) or not isinstance(lv, (dict, list, tuple)) or not isinstance(v, str):
            continue
        try:
            parsed = ast.literal_eval(v)
        except (ValueError, SyntaxError):
            continue
        if isinstance(parsed, type(lv)) or (isinstance(lv, tuple) and isinstance(parsed, list)):
            parsed = type(lv)(parsed) if isinstance(lv, tuple) else parsed
            fixed[k] = [v[:60], parsed]
            out[k] = parsed
    return out, fixed


# 🔴 REENTRY GUARANTEE (USER 2026-10-16, after BATUSDT_LONG exited into a rally and never re-entered):
# "make sure we can NEVER skip reentry (testing all applicable filters in yellow boxes so we can never enter
# against the trend) ... the entire reason we have reentries at all is so you can not lose 90% of a rally".
# Measured on that board's published set: REENTRY_RALLY_K15M_MAX = 0.15 against a LIVE DEFAULT of 30.0 (crypto)
# / 75.0 (stocks) — a 200x tightening of the rally gate, so the reentry could never fire — plus
# REENTRY_POST_CONSOL_ENABLED=False against a live default of True. The board had optimised its own reentries away.
# A reentry ENABLER may never be False when live defaults it True, and a reentry GATE may never be stricter than
# its live default. Direction is explicit per knob because a "_MAX" gate is loosened by raising it and a
# "_MIN"/"_GAP" gate by lowering it. Repaired to the live default, never beyond it, and the cost is measured.
REENTRY_ENABLERS = ("REENTRY_MANDATORY", "OBLIGATORY_REENTRY_ENABLED", "OBLIGATORY_REENTRY_LONG_ENABLED",
                    "OBLIGATORY_REENTRY_SHORT_ENABLED", "REENTRY_POST_CONSOL_ENABLED",
                    "REENTRY_ON_DC4_15M_BREAK_ENABLED", "REENTRY_ON_EXIT_PRICE_BREAK_ENABLED",
                    "REENTRY_ON_HIGHER_LOW_ENABLED", "EZ_REENTRY_PRICE_CROSS_GUARANTEE_ENABLED")
# USER 2026-10-16: "WE DO NOT HAVE A FUCKING stoch_k filter anywhere for over a year, only wt — GET IT OUT".
# Confirmed dead on both sides, so these are NOT repaired and NOT tested here: REENTRY_RALLY_K15M_MAX,
# REENTRY_STOCH_K_MAX_LONG/_MIN_SHORT, GUARANTEED_REENTRY_K_{HIGH,LOW}_BLOCK, OBLIGATORY_REENTRY_K15_HIGH_BLOCK.
# In v12_quick_engine their only occurrences are list-literal registry entries (:2586, :4047 — never a read),
# and in ez_manage the sole non-registry site is a stub farm (`if bool(getattr(config, X)): _ = getattr(...)`
# at :64651-64652). Repairing a dead knob would have been a fabricated fix on a fabricated cause.
REENTRY_GATES_AT_LEAST = ("REENTRY_MAX_PRICE_DIVERGENCE_PCT",)
REENTRY_GATES_AT_MOST = ("REENTRY_MIN_GAP_MINUTES", "REENTRY_PULLBACK_GR_SCORE_MIN", "REENTRY_TIER2_MIN_MINUTES")


def apply_reentry_guarantee(ov, venue):
    """-> (set whose reentry paths are at least as permissive as live, {switch: [was, now]})."""
    try:
        if venue == "crypto":
            from config import Config as _C
            live = _C()
        else:
            from config_tradier import TradierConfig as _T
            live = _T()
    except Exception as e:
        log(f"[reentry-guarantee] config unavailable ({e}) — skipped (fail-open)")
        return dict(ov or {}), {}
    out, fixed = dict(ov or {}), {}
    for name in REENTRY_ENABLERS:
        d = getattr(live, name, None)
        if d is True and name in out and out[name] is not True:
            fixed[name] = [_j(out[name]), True]
            out[name] = True
    for name in REENTRY_GATES_AT_LEAST:
        d = getattr(live, name, None)
        if d is None or name not in out:
            continue
        try:
            if float(out[name]) < float(d):
                fixed[name] = [_j(out[name]), _j(d)]
                out[name] = d
        except (TypeError, ValueError):
            continue
    for name in REENTRY_GATES_AT_MOST:
        d = getattr(live, name, None)
        if d is None or name not in out:
            continue
        try:
            if float(out[name]) > float(d):
                fixed[name] = [_j(out[name]), _j(d)]
                out[name] = d
        except (TypeError, ValueError):
            continue
    return out, fixed


def apply_trend_floors(ov, venue):
    """-> (set with no trend gate below its live config default, {switch: [was, now]})."""
    try:
        if venue == "crypto":
            from config import Config as _C
            cfg = _C()
        else:
            from config_tradier import TradierConfig as _T
            cfg = _T()
    except Exception as e:
        log(f"[trend-floor] config unavailable ({e}) — floors skipped (fail-open)")
        return dict(ov or {}), {}
    out, fixed = dict(ov or {}), {}
    for name in TREND_GATE_FLOORS:
        d = getattr(cfg, name, None)
        if d is None or name not in out:
            continue
        v = out[name]
        try:
            if isinstance(d, bool):
                if d is True and v is not True:
                    fixed[name] = [_j(v), True]
                    out[name] = True
            elif float(v) < float(d):
                fixed[name] = [_j(v), _j(d)]
                out[name] = d
        except (TypeError, ValueError):
            continue
    return out, fixed


# DC_HARD_STOP_15M_ENABLED is pinned True in every set this round publishes, not just left alone: the baseline
# boards still carry False on 39 sym_sides, and a republished set that kept False would be pushed straight
# back to live by the go-live pump. It is in NEVER_TEST as well, so it is pinned without ever being measured.
HARD_SET = {"GAP_MOC_EXIT_ENABLED": True, "DC_HARD_STOP_15M_ENABLED": True}


def apply_hard_set(ov):
    """-> (set with every HARD_SET value pinned, {switch: old_value} for the ones that actually changed)."""
    out, changed = dict(ov or {}), {}
    for k, v in HARD_SET.items():
        if out.get(k) != v:
            changed[k] = out.get(k, "<absent>")
            out[k] = v
    return out, changed


# GAP_MOC_EXIT_ENABLED is deliberately NOT here: it is a gap/market-on-close exit, not a stop, its shipped bold
# default is False for STOCKS_LONG, and False measured +7.162 on ZCSH_SHORT. It stays a measurable choice.
PROTECTED_OFF = {**{k: (False,) for k in REENTRY_ENABLERS}, "DC_HARD_STOP_15M_ENABLED": (False, 0), "MTF_ATR_TRAIL_ENABLED": (False,), "BB_FROZEN_STOP_ENABLED": (False,), "FROZEN_STOP_FILTER_TF": ("OFF",), "MTF_ATR_TRAIL_FILTER_TF": ("OFF",)}
# NOT protected: EXIT_R1_R2_FILTER_TF="OFF" is the MOST protective value, not a switch-off — r2_eff_tfs() returns the
# WHOLE family for "OFF" and for the default "15m" (twin_yellow_filters.py:1069). Narrowing to one leg (D) is the
# unprotective direction there, and that is a measurable choice, not a hard block.


QUAL_GAIN_FLOOR = float(os.environ.get("V15_QUAL_GAIN_MIN", "-inf"))


def qualifies(res, bh=None):
    """`v15_pilot._qualifies_30d` with ONE reason relaxed, per USER 2026-10-16: "EVERY result trades while in
    tradeable_keys or symbols(stocks) just with minimum amounts but IT TRADES". A negative-gain board is live
    anyway, so refusing a loss-reducing improvement (RLCUSDT_SHORT -30.65 -> -27.54, +3.11) froze exactly the
    bleeding sym_sides on the settings that were bleeding. valid / TIM / trades / DD gates are untouched, and an
    UNKNOWN gain still fails closed. V15_QUAL_GAIN_MIN=0 restores the old behaviour in one step (the Mac's
    v15_pilot already carries the same floor; the workers' read-only sidecar predates it, so it is applied here)."""
    ok, reasons = PILOT._qualifies_30d(res, bh)
    if ok:
        return True, reasons
    others = [r for r in reasons if not str(r).startswith("gain ")]
    if others:
        return False, reasons
    g = (res or {}).get("gain_pct")
    if g is None or float(g) <= QUAL_GAIN_FLOOR:
        return False, reasons
    return True, [f"gain {float(g):+.4f} accepted below zero (USER 2026-10-16: the sym_side trades at minimum size regardless)"]


# USER 2026-10-16: "make sure we exit more conservatively unless numbers prove that absolutely wrong — all defaults
# should be on the capital protect side not the let it run until we die side."
# A move that LOOSENS protection (switching an exit/stop/trail/guard off, or raising the gain a position must reach
# before an exit may fire) must therefore clear the board-noise floor, not merely be positive. A move that TIGHTENS
# protection still promotes on any positive delta. The floor is the gate's own per-side noise figure (0.48 measured
# on identical tree+NPZ reruns, BIBLE board-nondeterminism); V15_LOOSEN_FLOOR overrides it.
LOOSEN_FLOOR = float(os.environ.get("V15_LOOSEN_FLOOR", "0.48"))
_PROTECTIVE_TOKENS = ("EXIT", "STOP", "TRAIL", "GUARD", "VETO", "BLOCK", "HARD", "KILL", "PROTECT", "REDUCE", "LOCK")
_LOOSER_WHEN_RAISED = ("MIN_GAIN", "MIN_PROFIT", "PEAK_MIN", "MIN_AGE", "COOLDOWN", "MIN_BARS", "MIN_HOLD")


def loosening_reason(move):
    """-> a text reason when `move` makes the system hold risk LONGER, else None."""
    for k, v in (move or {}).items():
        ku = str(k).upper()
        protective = any(t in ku for t in _PROTECTIVE_TOKENS)
        if protective and v is False:
            return f"{k}=False switches a protective path off"
        if protective and isinstance(v, str) and v.strip().upper() == "OFF" and "FILTER_TF" in ku:
            return f"{k}=OFF disables a protective filter"
        if protective and any(t in ku for t in _LOOSER_WHEN_RAISED):
            return f"{k}={v} raises the bar an exit must clear"
    return None


def conservative_block(move, delta):
    """Capital-protect default: a loosening move needs delta > LOOSEN_FLOOR, a tightening move needs delta > 0."""
    why = loosening_reason(move)
    if why is None:
        return None
    try:
        if float(delta) > LOOSEN_FLOOR:
            return None
    except (TypeError, ValueError):
        pass
    return f"{why}; delta {delta} <= capital-protect floor {LOOSEN_FLOOR} (USER 2026-10-16: default to protection unless the numbers prove otherwise)"


def protected_block(move):
    """-> the reason this move may never promote, or None."""
    for k, v in (move or {}).items():
        if k in NEVER_TEST:
            return f"{k} is an EXCHANGE-RESTING stop (USER 2026-10-16): never switched, never tested, never promoted"
        bad = PROTECTED_OFF.get(k)
        if bad is None:
            continue
        if v in bad or (isinstance(v, str) and v.upper() in {str(b).upper() for b in bad}):
            return f"{k}={v} is a PROTECTED-OFF value (USER 2026-10-16): a hard stop/trail may never be promoted off"
    return None


def hunt_switch(sus, cur_ov, cum, cum_wr, ev, view, defaults, venue, yellow_mode, probe_unwired=False, wr_tol=5.0):
    """One suspect switch, greedy and honest. Returns (record, promoted_move|None, new_cum).

    Every delta is measured gain minus the RUNNING cumulative — never a sum of deltas from separate evals. The
    joint "row + all its positive yellows" set is evaluated ONCE and its own gain is what promotes (BIBLE §56:
    G = complete row delta vs the latest baseline, a real joint eval)."""
    sw = sus["switch"]
    unwired = sw in PILOT.UNWIRED_SWITCHES or sw in PILOT.UNWIRED_FILTERS
    if unwired and not probe_unwired:
        return {"switch": sw, "status": "UNWIRED in data/vec_unwired.json — the sweep refuses it, so no delta here can be real; never promoted. Re-run with --probe-unwired to MEASURE it (research only).", "ladder": []}, None, cum, cum_wr
    if sw in NEVER_TEST:
        return {"switch": sw, "status": "NEVER_TESTED — exchange-resting stop (USER 2026-10-16)", "ladder": [], "yellows": []}, None, cum, cum_wr
    if sus.get("skip_parent_off"):
        return {"switch": sw, "status": f"SKIPPED_PARENT_OFF — {sus['skip_parent_off']}=False on this board, so this row and its yellow box cannot move a bar (the parent switch itself IS tested)", "ladder": [], "yellows": []}, None, cum, cum_wr
    if sus["venue"] != "both" and sus["venue"] != venue:
        return {"switch": sw, "status": f"venue {sus['venue']} only — skipped on {venue}", "ladder": []}, None, cum, cum_wr
    variants, incumbent, parents_off = variants_for(sus, cur_ov, venue)
    rec = {"switch": sw, "unwired": unwired, "probe_only": bool(unwired and probe_unwired), "incumbent": _j(incumbent), "parents_armed_by_test": {k: _j(v) for k, v in parents_off.items()}, "parent_ref": sus.get("parent_ref", ""), "cum_before": round(cum, 4), "wr_before_pct": _r(cum_wr), "wr_guard_tol_pp": wr_tol, "ladder": [], "yellows": []}
    if sw == "EXIT_R1_R2_FILTER_TF":
        rec["r2_family"] = list(r2_legs(cur_ov, venue, incumbent)[2])
        rec["r2_family_source"] = r2_legs(cur_ov, venue, incumbent)[1]
    if not variants:
        rec["status"] = "no non-incumbent rung"
        return rec, None, cum, cum_wr
    results = ev.many([dict(cur_ov, **mv) for _, mv in variants])
    best = None
    for (lab, mv), res in zip(variants, results):
        d, promotable, why = PILOT.delta_vs_result(res, cum)
        ok, reasons = qualifies(res, None)
        wrg, wrwhy = _wr_ok(res, cum_wr, wr_tol)
        row = {"label": lab, "move": {k: _j(v) for k, v in mv.items()}, "gain": _r(res.get("gain_pct")), "trades": int(res.get("trades") or 0), "delta": _r(d), "wr_pct": _r(_wr(res)), "wr_delta_pp": _r((_wr(res) - cum_wr) if (_wr(res) is not None and cum_wr is not None) else None), "wr_guard": "pass" if wrg else wrwhy, "valid": bool(res.get("valid")), "qualifies": ok, "why": why or ("; ".join(reasons) if not ok else "")}
        if sw == "EXIT_R1_R2_FILTER_TF" and sw in mv:
            legs, _, _ = r2_legs(cur_ov, venue, mv[sw])
            row["r2_effective_legs"] = list(legs)
        rec["ladder"].append(row)
        pblock = protected_block(mv) or conservative_block(mv, d)
        if pblock:
            row["protected_block"] = pblock
        if d is not None and d > 0 and promotable and ok and wrg and not pblock and (best is None or d > best[0]):
            best = (d, lab, mv, res)
    nz = [r for r in rec["ladder"] if r["delta"] not in (None, 0.0)]
    if not nz:
        rec["status"] = "INERT_IN_VECTOR — every variant read 0.0 (BIBLE §14.3: an upstream gate suppresses the trades this switch touches; vec wiring is not fixed here)"
    off_rungs = [r for r in rec["ladder"] if len(r["move"]) == 1 and sw in r["move"]]
    if parents_off and off_rungs and all(r["delta"] in (None, 0.0) for r in off_rungs):
        rec["tf_inert_with_parent_off"] = f"every naked rung read 0.0 while {list(parents_off)} is off ({sus.get('parent_ref', '')}) — the TF value cannot move this sym_side's vector until the parent is armed"
    if yellow_mode != "off":
        # Yellows ride the winning rung (the narrowing that makes this minutes); they are measured vs that rung's
        # own gain, and the promoting number is one JOINT eval of rung + every positive yellow — not a sum.
        base_lab, base_mv = (best[1], best[2]) if best else ("incumbent", {})
        base_cum = (cum + best[0]) if best else cum
        ycands = yellow_headers(view, sw, base_mv.get(sw, incumbent), sus["tabs"], defaults)
        rec["yellow_count"] = len(ycands)
        if ycands:
            ylabels = list(ycands)
            yres = ev.many([dict(cur_ov, **dict(base_mv, **ycands[h]["ov"])) for h in ylabels])
            pos = []
            for hdr, res in zip(ylabels, yres):
                d, promotable, why = PILOT.delta_vs_result(res, base_cum)
                ok, reasons = qualifies(res, None)
                wrg, wrwhy = _wr_ok(res, cum_wr, wr_tol)
                rec["yellows"].append({"on": base_lab, "header": hdr, "tab": ycands[hdr]["tab"], "gain": _r(res.get("gain_pct")), "trades": int(res.get("trades") or 0), "delta_vs_row": _r(d), "wr_pct": _r(_wr(res)), "wr_guard": "pass" if wrg else wrwhy, "qualifies": ok, "why": why or ("; ".join(reasons) if not ok else "")})
                ypb = protected_block(ycands[hdr]["ov"]) or conservative_block(ycands[hdr]["ov"], d)
                if ypb:
                    rec["yellows"][-1]["protected_block"] = ypb
                if d is not None and d > 0 and promotable and ok and wrg and not ypb:
                    pos.append((hdr, d))
            rec["positive_yellows"] = [{"header": h, "delta_vs_row": _r(d)} for h, d in sorted(pos, key=lambda x: -x[1])]
            if pos:
                joint_mv = dict(base_mv)
                for hdr, _d in pos:
                    joint_mv.update(ycands[hdr]["ov"])
                jres = ev.one(dict(cur_ov, **joint_mv))
                jd, jpromotable, jwhy = PILOT.delta_vs_result(jres, cum)
                jok, jreasons = qualifies(jres, None)
                jwrg, jwrwhy = _wr_ok(jres, cum_wr, wr_tol)
                rec["joint"] = {"label": f"{base_lab} + {len(pos)} positive yellow(s)", "headers": [h for h, _ in pos], "gain": _r(jres.get("gain_pct")), "trades": int(jres.get("trades") or 0), "delta_vs_cum": _r(jd), "wr_pct": _r(_wr(jres)), "wr_guard": "pass" if jwrg else jwrwhy, "qualifies": jok, "why": jwhy or ("; ".join(jreasons) if not jok else "")}
                if jd is not None and jd > 0 and jpromotable and jok and jwrg and (best is None or jd > best[0]):
                    best = (jd, rec["joint"]["label"], joint_mv, jres)
                # a single yellow can still beat the joint set (yellows interact) — take the best MEASURED one
                for hdr, d in pos:
                    if best is None or d + (base_cum - cum) > best[0]:
                        sres = yres[ylabels.index(hdr)]
                        sd, sprom, _ = PILOT.delta_vs_result(sres, cum)
                        if sd is not None and sd > 0 and sprom and _wr_ok(sres, cum_wr, wr_tol)[0] and (best is None or sd > best[0]):
                            best = (sd, f"{base_lab} + {hdr}", dict(base_mv, **ycands[hdr]["ov"]), sres)
    if best is None:
        rec.setdefault("status", "no positive qualifying move — incumbent kept")
        rec["promoted"] = None
        return rec, None, cum, cum_wr
    _d, lab, mv, res = best
    # NO-LIES: the promoted delta is recomputed from the winning result itself, never carried from a chain.
    d = float(res["gain_pct"]) - cum
    rec["promoted"] = {"label": lab, "move": {k: _j(v) for k, v in mv.items()}, "old": {k: _j(cur_ov.get(k, "<ABSENT>")) for k in mv}, "delta": _r(d), "gain_before": round(cum, 4), "gain_after": _r(res.get("gain_pct")), "trades": int(res.get("trades") or 0), "max_dd_pct": _r(res.get("max_dd_pct")), "tim_pct": _r(res.get("tim_pct")), "pool_sharpe": _r(res.get("pool_sharpe")), "wr_before_pct": _r(cum_wr), "wr_after_pct": _r(_wr(res)), "wr_delta_pp": _r((_wr(res) - cum_wr) if (_wr(res) is not None and cum_wr is not None) else None), "wr_source": "evaluate_v12 realised ledger (pnl_dollars>0 per trade)"}
    if rec.get("probe_only"):
        rec["status"] = "PROBE ONLY (unwired) — measured, deliberately NOT promoted"
        rec["probe_best"] = rec.pop("promoted")
        return rec, None, cum, cum_wr
    rec["status"] = "PROMOTED"
    return rec, mv, cum + d, _wr(res)


def wt_family_rows(view, defaults, cur_ov, venue):
    """The WT15 family as real template rows (the candidate space with its is_default truth), one suspect per
    (tab, switch): WT_15M_BOUNCE_*, WT_CROSS_EXIT_*, WT_15M_CROSS_ENTRY_ENABLED. Every recoverable losing entry on
    2026-10-15 was WT_15M_BOUNCE_OPEN with RSI_MAX/RVOL_MIN/REQUIRE_HTF unset."""
    # USER 2026-10-16: "these need to be on the wt15 entries and exits not just suffocate tim as generic
    # filters" — so the HTF/trend agreement tested here is the one SCOPED to the WT path
    # (WT_COMPOSITE_HTF_GATE, WT_DC_HTF_GATE/WT_DC_DIRECT_HTF_GATE), not a blanket entry screen.
    want = lambda s: s.startswith(("WT_15M_BOUNCE", "WT_CROSS_EXIT", "WT_15M_CROSS_ENTRY", "WT_COMPOSITE_HTF", "WT_DC_HTF", "WT_DC_DIRECT_HTF")) or s.startswith(("REENTRY_", "OBLIGATORY_REENTRY_", "HARDCODED_RALLY_"))
    by_switch = {}
    for sname, tv in view.items():
        for rr, sw, cand, isdef in tv["rows"]:
            if sw in NEVER_TEST or not want(sw) or sw in PILOT.UNWIRED_SWITCHES or sw in PILOT.UNWIRED_FILTERS:
                continue
            if sw not in PILOT.known_config_fields():
                continue
            try:
                val = PILOT._parse_opt_value(cand, defaults.get(sw))
                if not PILOT._cand_compatible(sw, val, defaults)[0]:
                    continue
            except Exception:
                continue
            e = by_switch.setdefault(sw, {"switch": sw, "ladder": [], "parents": {}, "venue": "both", "tabs": []})
            if val not in e["ladder"]:
                e["ladder"].append(val)
            if sname not in e["tabs"]:
                e["tabs"].append(sname)
    # A sub-knob of a DISABLED WT path cannot move a single bar — measured on CRWV_SHORT and CLS_LONG, where
    # WT_15M_BOUNCE_OPEN_ENABLED is False and all 13 WT_15M_BOUNCE_* rows read exactly 0.0 across every rung AND
    # every yellow. At ~127 filter columns per row (USER 2026-10-16: the full yellow box per switch) that is ~1650
    # wasted evals per sym_side. The rows are not dropped from the report — they are recorded as
    # SKIPPED_PARENT_OFF with the parent that disabled them, so coverage stays auditable. The parent switch ITSELF
    # is always tested, so a board that wants the path back on still finds it.
    gate = {}
    for sw in list(by_switch):
        par = ("WT_15M_BOUNCE_OPEN_ENABLED" if sw.startswith("WT_15M_BOUNCE") and sw != "WT_15M_BOUNCE_OPEN_ENABLED"
               else "WT_CROSS_EXIT_ENABLED" if sw.startswith("WT_CROSS_EXIT") and sw != "WT_CROSS_EXIT_ENABLED"
               else "WT_15M_CROSS_ENTRY_ENABLED" if sw.startswith("WT_15M_CROSS_ENTRY_") else None)
        if par and par in cur_ov and cur_ov.get(par) is False:
            gate.setdefault(par, []).append(sw)
            by_switch[sw]["skip_parent_off"] = par
    if gate:
        log(f"[wt-family] skipping {sum(len(v) for v in gate.values())} sub-rows whose WT path is off: " + ", ".join(f"{k}=False -> {len(v)} rows" for k, v in gate.items()))
    order = sorted(by_switch, key=lambda s: (0 if "OPEN_ENABLED" in s else 1 if "BOUNCE" in s else 2, s))
    return [by_switch[s] for s in order]


def _wr(res):
    """Verified win rate for one eval. evaluate_v12 computes wr_pct from the REALISED per-trade ledger
    (pnl_dollars > 0 per trade, tools/opt/evaluate_v12.py:1176) and returns it without include_ledger, so WR is
    derivable on EVERY eval here — it is never [UNVERIFIED] in this tool. A 0-trade result has no WR."""
    try:
        if int((res or {}).get("trades") or 0) <= 0:
            return None
        v = (res or {}).get("wr_pct")
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _wr_ok(res, cum_wr, tol):
    """USER 2026-10-16: promotion is on GAIN delta, but refuse a gain win that drops verified WR by more than
    `tol` percentage points vs the running cumulative. WR is NOT the objective — this is only a guard. When either
    side's WR is not derivable the guard cannot speak, so it abstains (pass) and the row is labelled."""
    w, c = _wr(res), cum_wr
    if w is None or c is None:
        return True, "unverified-abstain"
    return ((w - c) >= -float(tol), f"WR {w:.1f}% vs {c:.1f}% = {w - c:+.1f}pp beyond -{tol:.1f}pp")


def _r(x):
    try:
        return None if x is None else round(float(x), 4)
    except (TypeError, ValueError):
        return None


def _j(v):
    return v if isinstance(v, (int, float, str, bool, type(None))) else str(v)


def run_one(sym_side, args):
    t_start = time.time()
    venue = venue_of(sym_side)
    out = {"sym_side": sym_side, "venue": venue, "ts": utcnow(), "host": os.uname().nodename, "window_days": args.window_days, "source": "suspect_row_rerun", "pilot_module": _PILOT_NAME, "pilot_md5": _pilot_md5(), "switches": []}
    cur_ov, src = start_set(sym_side)
    out["start_set_source"] = src
    if not cur_ov:
        out["status"] = "NO_START_SET — no per_sym entry and no progress JSON; refusing (never start from template defaults)"
        log(f"  {sym_side}: {out['status']}")
        return out
    out["start_set_keys"] = len(cur_ov)
    out["start_set_md5"] = set_md5(cur_ov)
    if os.environ.get("V12_ALLOW_STALE_NPZ") == "1":
        out["NUMBERS_ARE"] = "FROZEN-WINDOW STRUCTURAL FIGURES — V12_ALLOW_STALE_NPZ=1, the NPZ does not reach the latest closed bar. NOT production gains. Never apply from this run."
        out["apply_blocked_by_stale_npz"] = True
    prepared = VP.prepare_batch(sym_side, args.window_days)
    if isinstance(prepared, dict) and prepared.get("_stale"):
        out["status"] = f"NPZ_STALE: {prepared['_stale']}"
        log(f"  {sym_side}: {out['status']} (set V12_ALLOW_STALE_NPZ=1 only to read a frozen-window structural figure)")
        return out
    defaults = PILOT.get_defaults_for_symside(sym_side)
    view = template_view(sym_side, defaults)
    ev = Evaluator(prepared, args.window_days, args.workers, args.cell_timeout)
    base = ev.one(cur_ov)
    bh = _r(base.get("bh_pct"))
    if base.get("gain_pct") is None:
        out["status"] = f"BASELINE_UNMEASURABLE: {base.get('invalid_reason')}"
        ev.close()
        log(f"  {sym_side}: {out['status']}")
        return out
    cum = float(base["gain_pct"])
    cum_wr = _wr(base)
    bok, breasons = qualifies(base, bh)
    out["baseline"] = {"gain_pct": _r(cum), "bh_pct": bh, "trades": int(base.get("trades") or 0), "max_dd_pct": _r(base.get("max_dd_pct")), "tim_pct": _r(base.get("tim_pct")), "pool_sharpe": _r(base.get("pool_sharpe")), "wr_pct": _r(cum_wr), "wr_source": "evaluate_v12 realised ledger (pnl_dollars>0 per trade)" if cum_wr is not None else "[UNVERIFIED] 0 trades — no per-trade returns", "valid": bool(base.get("valid")), "qualifies": bok, "why": "" if bok else "; ".join(breasons), "remeasured_on": "current NPZ"}
    log(f"  {sym_side} [{venue}] baseline RE-MEASURED gain {cum:+.2f}% bh {bh} trades {base.get('trades')} dd {out['baseline']['max_dd_pct']} ({src}, {len(cur_ov)} keys)")
    suspects = list(SUSPECTS) + (wt_family_rows(view, defaults, cur_ov, venue) if args.wt else [])
    promoted = {}
    for sus in suspects:
        rec, mv, cum, cum_wr = hunt_switch(sus, cur_ov, cum, cum_wr, ev, view, defaults, venue, args.yellow_mode, args.probe_unwired, args.wr_tol)
        out["switches"].append(rec)
        if mv:
            cur_ov = dict(cur_ov, **mv)
            promoted.update(mv)
            log(f"    + {rec['promoted']['label']}  delta {rec['promoted']['delta']:+.3f}  -> {rec['promoted']['gain_after']:+.2f}% / {rec['promoted']['trades']}tr")
        elif rec.get("status", "").startswith("INERT"):
            log(f"    . {sus['switch']}: INERT_IN_VECTOR (parent {rec.get('parents_armed_by_test') or 'n/a'}) — not promoted")
    final = ev.one(cur_ov) if promoted else base
    out["final"] = {"gain_pct": _r(final.get("gain_pct")), "trades": int(final.get("trades") or 0), "max_dd_pct": _r(final.get("max_dd_pct")), "tim_pct": _r(final.get("tim_pct")), "pool_sharpe": _r(final.get("pool_sharpe")), "wr_pct": _r(_wr(final)), "valid": bool(final.get("valid"))}
    fok, freasons = qualifies(final, bh)
    out["final"]["qualifies"] = fok
    out["final"]["why"] = "" if fok else "; ".join(freasons)
    # BIBLE §14.2 invariant: final_gain == baseline + sum(positive promoted deltas). A mismatch is an accumulation bug.
    expect = float(out["baseline"]["gain_pct"]) + sum(float(s["promoted"]["delta"]) for s in out["switches"] if s.get("promoted"))
    got = out["final"]["gain_pct"]
    out["accumulation_check"] = {"expected": _r(expect), "measured": got, "drift": _r((got - expect) if got is not None else None), "ok": got is not None and abs(got - expect) <= args.accum_tol}
    out["promoted_moves"] = {k: _j(v) for k, v in promoted.items()}
    _bw, _fw = _wr(base), _wr(final)
    out["wr"] = {"baseline_pct": _r(_bw), "final_pct": _r(_fw), "delta_pp": _r((_fw - _bw) if (_bw is not None and _fw is not None) else None), "guard_tol_pp": args.wr_tol, "verified": _bw is not None and _fw is not None, "note": "WR is NOT the objective (promotion is on gain delta); the guard only refuses a gain win that costs more than the tolerance in verified WR", "class": ("guard-applied" if (_bw is not None and _fw is not None) else "UNVERIFIED")}
    out["wr_guard_blocked"] = [{"switch": s0["switch"], "label": r0["label"], "delta": r0["delta"], "wr_guard": r0["wr_guard"]} for s0 in out["switches"] for r0 in (s0.get("ladder") or []) if r0.get("wr_guard") not in (None, "pass", "unverified-abstain") and (r0.get("delta") or 0) > 0]
    out["evals"] = ev.n
    if _WT_FORCE_FALLBACK:
        out["wt_mandatory_note"] = f"host v15_pilot has the pre-gate tab-only mandatory_bases_for_tab; WT core forcing applied locally for {sorted(_WT_FORCE_FALLBACK)}"
    out["secs"] = round(time.time() - t_start, 1)
    out["status"] = "OK"
    ev.close()
    log(f"  {sym_side} DONE {out['secs']}s  {ev.n} evals  baseline {out['baseline']['gain_pct']:+.2f}% -> final {got:+.2f}%  {len(promoted)} moves  accum_ok={out['accumulation_check']['ok']}")
    if not out["accumulation_check"]["ok"]:
        log(f"  {sym_side} ACCUMULATION DRIFT {out['accumulation_check']['drift']} (expected {out['accumulation_check']['expected']}) — board nondeterminism tolerance is --accum-tol; NOT applied")
    return out


def apply_one(out, args):
    """Write the COMPLETE per_sym set (USER 2026-10-15: per_sym is a full set, not a diff) through
    per_sym_store.upsert. Refuses on anything unproven."""
    ss = out["sym_side"]
    if out.get("status") != "OK" or not out.get("promoted_moves"):
        return {"applied": False, "reason": out.get("status") if out.get("status") != "OK" else "no promoted move"}
    if not out["final"].get("qualifies"):
        return {"applied": False, "reason": f"final set fails _qualifies_30d: {out['final'].get('why')}"}
    if not out["accumulation_check"].get("ok"):
        return {"applied": False, "reason": f"accumulation drift {out['accumulation_check'].get('drift')}"}
    if float(out["final"]["gain_pct"] or 0) < float(out["baseline"]["gain_pct"] or 0):
        return {"applied": False, "reason": "final below re-measured baseline — best-start law"}
    e = PSS.get(ss) or {}
    # MEASURE-HERE / APPLY-THERE: a run measured on a worker from an exported start set may only be applied while
    # the live set is still the one that was measured. If go-live moved it meanwhile, the deltas are stale — refuse.
    if out.get("start_set_md5"):
        live_ov = PSS.get_overrides(ss) or {}
        if live_ov and set_md5(live_ov) != out["start_set_md5"]:
            return {"applied": False, "reason": f"live set moved since measurement (measured {out['start_set_md5'][:8]}, live {set_md5(live_ov)[:8]}) — re-measure"}
    base_ov = dict(e.get("overrides") or {})
    snap = dict(e.get("defaults_snapshot") or {})
    if not snap:
        try:
            import cat_side_defaults as CSD
            snap = CSD.defaults(f"{'CRYPTO' if out['venue'] == 'crypto' else 'STOCKS'}_{ss.rsplit('_', 1)[-1]}")
        except Exception:
            snap = {}
    new_ov = dict(base_ov)
    new_ov.update(out["promoted_moves"])
    # The COMPLETE set starts from the set this run actually MEASURED (the board's full_config), not from a fresh
    # defaults merge — re-merging would let a morning default move drift live off its own board (2026-10-15 rule).
    full = dict(e.get("full_config") or {}) or dict(snap)
    full.update(new_ov)
    full.update(out["promoted_moves"])
    meta = {"source": "suspect_row_rerun", "promoted_at": out["ts"], "host": out["host"], "window_days": out["window_days"], "gain_pct": out["final"]["gain_pct"], "baseline_gain_pct": out["baseline"]["gain_pct"], "trades": out["final"]["trades"], "max_dd_pct": out["final"]["max_dd_pct"], "pool_sharpe": out["final"]["pool_sharpe"], "switches_moved": sorted(out["promoted_moves"]), "moves": out["promoted_moves"], "start_set_source": out["start_set_source"], "evidence": f"data/reports/v15_suspect_row_rerun_{args.tag}.json"}
    jp = ROOT / "data" / "hourly_reconfig" / ("per_sym_active_config.json" if out["venue"] == "crypto" else "trb/active_config.json")
    try:
        PSS.upsert(ss, new_ov, snap, full, meta, json_path=jp if jp.exists() else None)
    except Exception as ex:
        return {"applied": False, "reason": f"upsert failed: {ex}"}
    back = PSS.get_overrides(ss) or {}
    bad = {k: [_j(v), _j(back.get(k, "<ABSENT>"))] for k, v in out["promoted_moves"].items() if back.get(k) != v}
    return {"applied": not bad, "keys": len(full), "verify_mismatch": bad or None}


def apply_from(path, args):
    """Apply a report measured on another host. Same gates as --apply (status OK, promoted moves, _qualifies_30d,
    accumulation check, best-start law, live-set-unchanged md5) — nothing is relaxed because the measurement is remote."""
    d = json.loads(Path(path).read_text())
    results = d.get("results") if isinstance(d, dict) else d
    if not isinstance(results, list):
        raise SystemExit(f"--apply-from {path}: no results list")
    log(f"[apply-from] {path}: {len(results)} measured sym_sides, host={os.uname().nodename}")
    done, skipped = [], []
    for out in results:
        if out.get("apply_blocked_by_stale_npz"):
            skipped.append((out.get("sym_side"), "measured on stale NPZ"))
            continue
        r = apply_one(out, args)
        log(f"  {out.get('sym_side')} APPLY {r}")
        (done if r.get("applied") else skipped).append((out.get("sym_side"), r.get("reason") or r))
    log(f"[apply-from] applied {len(done)} / skipped {len(skipped)}")
    for ss, why in skipped:
        log(f"  skip {ss}: {why}")
    op = OUT_DIR / f"v15_suspect_row_apply_{args.tag}.json"
    op.write_text(json.dumps({"from": str(path), "ts": utcnow(), "host": os.uname().nodename, "applied": [s for s, _ in done], "skipped": [{"sym_side": s, "why": _j(w)} for s, w in skipped]}, indent=1))
    log(f"[apply-from] {op}")
    return 0 if done else 1


def pause_fleet(hosts):
    BACKUP_DIR.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M", time.gmtime())
    bp = BACKUP_DIR / f"before_suspect_rerun_drain_{stamp}.json"
    cur = {"hosts": []}
    if DRAIN_FILE.exists():
        shutil.copy2(DRAIN_FILE, bp)
        cur = json.loads(DRAIN_FILE.read_text())
    else:
        bp.write_text(json.dumps(cur))
    new = {"hosts": sorted(set(cur.get("hosts") or []) | set(hosts)), "reason": f"suspect_row_rerun pause {utcnow()} (RESTORE: cp {bp} {DRAIN_FILE})", "prev": cur.get("hosts") or [], "backup": str(bp)}
    DRAIN_FILE.write_text(json.dumps(new, indent=1))
    log(f"[PAUSE] {DRAIN_FILE} hosts={new['hosts']} (was {new['prev']})")
    log(f"[PAUSE] in-flight boards are UNTOUCHED — _load_drain only withholds NEW launches (tools/v15_fleet_scheduler.py:1666)")
    log(f"[RESTORE — one step] cp {bp} {DRAIN_FILE}")
    log(f"[RESTORE — or     ] python3 tools/v15_suspect_row_rerun.py --resume")
    back = json.loads(DRAIN_FILE.read_text()).get("hosts") or []
    ok = set(hosts) <= set(back)
    log(f"[PAUSE] VERIFIED in effect: {ok} (file now lists {back})")
    return 0 if ok else 1


def resume_fleet():
    bks = sorted(BACKUP_DIR.glob("before_suspect_rerun_drain_*.json"))
    if not bks:
        log("[RESUME] no backup found — refusing to guess; edit data/fleet_drain_hosts.json by hand")
        return 1
    shutil.copy2(bks[-1], DRAIN_FILE)
    log(f"[RESUME] restored {bks[-1]} -> {DRAIN_FILE}: {json.loads(DRAIN_FILE.read_text()).get('hosts')}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="surgical suspect-row + yellow-cell delta rerun on top of each sym_side's existing best set")
    ap.add_argument("--sym-sides", default="", help="explicit comma list (overrides --venue/--tiers)")
    ap.add_argument("--venue", default="crypto", choices=["crypto", "stocks", "all"], help="crypto first — it is bleeding and trades 24/7")
    ap.add_argument("--tiers", default="", help="triage tier filter, e.g. 1,2")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--cell-timeout", type=float, default=120.0)
    ap.add_argument("--yellow-mode", default="best", choices=["best", "off"], help="best = yellows on the winning rung (the narrowing that makes this minutes instead of an hour); off = naked ladder only")
    ap.add_argument("--wt", action="store_true", help="also hunt the WT15 family template rows")
    ap.add_argument("--probe-unwired", action="store_true", help="MEASURE switches listed in data/vec_unwired.json (EXIT_R1_R2_FILTER_TF is one) — research only, never promoted, never applied")
    ap.add_argument("--wr-tol", type=float, default=5.0, help="WR guard: refuse a positive-gain promotion that drops verified win rate by more than this many percentage points vs the running cumulative (default 5.0pp). WR comes from evaluate_v12's realised per-trade ledger, so it is verified on every eval; a 0-trade result is [UNVERIFIED] and the guard abstains.")
    ap.add_argument("--accum-tol", type=float, default=2.5, help="BIBLE: board nondeterminism tolerance (gate TOL_SUM 2.5)")
    ap.add_argument("--apply", action="store_true", help="write the complete per_sym set for promoted moves (OFF by default)")
    ap.add_argument("--allow-untradeable", action="store_true")
    ap.add_argument("--tag", default=time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()))
    ap.add_argument("--pause", default="", help="drain these hosts (comma list) and exit")
    ap.add_argument("--resume", action="store_true", help="restore the pre-pause drain file and exit")
    ap.add_argument("--start-sets", default="", help="JSON export of the LIVE per_sym full sets (tools/v15_export_start_sets.py) — read before the local store, for measuring on a worker whose store is not the live truth")
    ap.add_argument("--apply-from", default="", help="apply the promoted moves of an EXISTING report JSON on this host (the Mac is the single per_sym writer) and exit")
    args = ap.parse_args()
    if args.start_sets:
        load_start_sets(args.start_sets)
    if args.apply_from:
        return apply_from(args.apply_from, args)
    if args.pause:
        return pause_fleet([h.strip() for h in args.pause.split(",") if h.strip()])
    if args.resume:
        return resume_fleet()
    targets = select_sym_sides(args)
    if not targets:
        log("[select] nothing to do")
        return 1
    log(f"[run] tag={args.tag} host={os.uname().nodename} {len(targets)} sym_sides window={args.window_days}d workers={args.workers} yellow={args.yellow_mode} wt={args.wt} apply={args.apply}")
    log(f"[run] {', '.join(targets[:12])}{' ...' if len(targets) > 12 else ''}")
    outs = []
    op = OUT_DIR / f"v15_suspect_row_rerun_{args.tag}.json"
    for i, ss in enumerate(targets, 1):
        log(f"[{i}/{len(targets)}] {ss}")
        try:
            o = run_one(ss, args)
        except Exception as ex:
            import traceback
            o = {"sym_side": ss, "status": f"ERROR {type(ex).__name__}: {ex}", "traceback": traceback.format_exc()[-800:]}
            log(f"  {ss} ERROR {ex}")
        if args.apply:
            o["apply"] = apply_one(o, args)
            log(f"  {ss} APPLY {o['apply']}")
        outs.append(o)
        op.write_text(json.dumps({"tag": args.tag, "ts": utcnow(), "args": vars(args), "results": outs}, indent=1, default=str))
    ok = [o for o in outs if o.get("status") == "OK"]
    moved = [o for o in ok if o.get("promoted_moves")]
    log("")
    log(f"[summary] {len(ok)}/{len(outs)} measured, {len(moved)} with >=1 promoted move")
    if ok:
        secs = [o["secs"] for o in ok]
        log(f"[summary] wall-clock per sym_side: min {min(secs)}s median {sorted(secs)[len(secs) // 2]}s max {max(secs)}s | evals {sum(o['evals'] for o in ok)}")
    flat = [o for o in ok if not o.get("promoted_moves")]
    log(f"[summary] FLAT (found nothing, reported as flat — never dressed up): {len(flat)}")
    _cls = {}
    for o in ok:
        _cls[(o.get("wr") or {}).get("class", "?")] = _cls.get((o.get("wr") or {}).get("class", "?"), 0) + 1
    log(f"[summary] WR classes: {_cls} (tol {args.wr_tol}pp) | WR-guard-blocked positive moves: {sum(len(o.get('wr_guard_blocked') or []) for o in ok)}")
    for o in moved:
        _w = o.get("wr") or {}
        _wtxt = f"WR {_w.get('baseline_pct')}% -> {_w.get('final_pct')}% ({_w.get('delta_pp'):+.1f}pp)" if _w.get("verified") and _w.get("delta_pp") is not None else "WR [UNVERIFIED]"
        log(f"[summary] {o['sym_side']} {o['baseline']['gain_pct']:+.2f}% -> {o['final']['gain_pct']:+.2f}%  {_wtxt}  {o['promoted_moves']}")
    log(f"[summary] evidence {op}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
