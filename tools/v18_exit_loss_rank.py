#!/usr/bin/env python3
"""v18_exit_loss_rank — B1 of doc 17 §3.2: rank the most frequently losing EXIT FAMILIES per cat_side, on the board trade
ledgers (tools/v18_ledger_replay.py) and on the hermetic live truth (/history), and build the exit REPAIR MENU.

THREE COLUMNS, NEVER SUMMED
  board           trade rows of the replayed board ledgers (one real include_ledger eval of each latest published final set)
  live_hermetic   /history lifecycles of the cat_side's accounts whose CLOSE (the exit decision) is at/after the hermetic cut
                  (crypto 2026-10-14T13:00Z, stocks 2026-10-17T13:00Z; `opened_pre_hermetic` flags entries made before it) on a vector family (crypto reasons carry |VEC_EXACT; stock reasons carry
                  no tag, all stock exits are vector since TRADIER_HERMETIC_ENFORCE)
  stop_execution  the same lifecycles closed by FIXED_STOP* / VEC_STALE_FLATTEN / VEC_EXIT_OWED / VEC_CONVERGE_* (reported, never
                  a repair target of the board)
  (live_other: crypto closes with no VEC_EXACT tag that are not stop/execution — reported in the json, not ranked)

NORMALISATION (gain definition: net $ / peak cost basis). loss_sum_pct, giveback_sum, premature_sum and drift_into_loss_sum
are PCT-POINTS OF PEAK COST BASIS: board rows -> pnl_dollars / that board's max_invested_dollars x 100 (sum over a board = its
gain_pct); live rows -> lifecycle pnl $ / the (account, sym_side) window peak cost basis x 100. loss_sum_pct is signed
(sum of the losers' pnl, <= 0). giveback = (MFE% - realised%) x deployed / peak over winners; premature = post-exit 32-bar
continuation x deployed / peak over trades whose continuation >= max(1%, realised) (tools/v15_trade_autopsy rule);
drift_into_loss = pnl of trades with MFE >= 1.0% that closed <= 0. Live MFE/post-exit come from the Mac's 15m klines
(klines_cache; None when the bars are missing). Live pnl = event prices recorded in /history (broker-echo fills dropped).

REPAIR MENU (data/learn/exit_repair_menu_<CAT_SIDE>.json): family -> paths {switch, cand, companions, source, support_n,
support_syms, forward_factor}. Sources: (a) `curated` doc 17 §3.2 table (every switch AND value verified to exist in the
cat_side TEMPLATE; dropped names/values listed), (b) `token` family<->switch-name token overlap on the exit tabs, (c) `learned`
paired real-engine evals (`measure` on S2: final set vs final set + k=v, attributed by entry bar) where k=v reduced the family's
loss on >= 3 sym_sides; a candidate that was not (or could not be) measured is `pending_evidence` with support null.
support_n / support_syms: (a)/(b) = sym_sides with a main-pass naked eval of switch=cand / of those, positive; (c) = sym_sides
measured / of those, family loss reduced. companions = the filter cells positive WITH that switch (pair delta > 0 and above
the naked) on the most sym_sides of this cat_side's evidence.

usage
  python3 tools/v18_exit_loss_rank.py build [--cat-side CS] [--apply]        (Mac; dry-run prints the top tables)
  python3 tools/v18_exit_loss_rank.py measure --root ~/binance-sandbox --ledgers ~/v18_learn/trade_ledgers \
      --sets ~/v18_learn/replay_sets.jsonl.gz --menu-dir ~/v18_learn/menu_draft --out ~/v18_learn/menu_measure --workers 2 --apply  (S2)
"""
from __future__ import annotations

import argparse
import bisect
import glob
import gzip
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import v18_learn_common as C  # noqa: E402

CRYPTO_ACCOUNTS = ("ang", "fin", "flz", "inf", "men")
STOCKS_ACCOUNTS = ("tra", "trb", "trc")
EXIT_TABS = ("EXIT_STRUCTURAL", "EXIT_VELOCITY", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER")
COLUMNS = ("board", "live_hermetic", "stop_execution")
DRIFT_MFE = float(os.environ.get("V18_DRIFT_MFE_PCT", "1.0"))
PREMATURE_MIN = 1.0
POST_BARS = 32
BAR_S = 900
LEARNED_MIN_SYMS = 3
TOP_FAMILIES_MENU = int(os.environ.get("V18_MENU_TOP_FAMILIES", "12"))
TOKEN_STOP = frozenset({"EXIT", "ENTRY", "ENABLED", "TF", "MIN", "MAX", "PCT", "LONG", "SHORT", "THE", "AND", "OR", "OF", "MODE",
                        "15M", "1H", "4H", "D", "W", "M", "3M", "1M", "5M", "FILTER", "REQUIRE", "TRADIER", "CRYPTO", "S", "BARS",
                        "X", "NEW", "ON", "OFF", "USE", "IF", "TO", "AT", "VEC", "EXACT", "LIVE", "CLOSE", "REDUCE"})
# doc 17 §3.2 curated table: symptom -> (family matcher, [(switch or regex, [values] or None = every non-default template value)])
CURATED = [
    ("EXIT_VELOCITY_WT 1-bar losers", lambda fam, st: fam.startswith("EXIT_VELOCITY_WT"),
     [("EXIT_VELOCITY_WT_MIN_TFS", ["2", "3"]), ("EXIT_VELOCITY_WT_MIN_HOLD_ENABLED", None), ("WT_4H_VEL_EXIT_REQUIRE_PROFIT", None),
      ("WT_CROSS_EXIT_REQUIRE_15M_CONFIRM", None), (re.compile(r"^REGIME_.*_WT_EXIT_VEL$"), None)]),
    ("DC_HOPELESS on the entry bar", lambda fam, st: fam.startswith("DC_HOPELESS"),
     [("DC_HOPELESS_EXIT_MIN_AGE_S", ["900", "3600"])]),
    ("winners drifting into a loss", lambda fam, st: st.get("drift_n", 0) > 0,
     [("HARD_BREAKEVEN_FLOOR", None), ("BREAKEVEN_GAIN_EROSION_MIN_GAIN", ["0.1", "0.25"]), ("WIN_TRAIL_EROSION_PCT", None),
      ("PEAK_GIVEBACK_HARD_ZERO_ENABLED", None)]),
    ("giveback (MFE >> realised)", lambda fam, st: st.get("giveback_sum", 0.0) > 0.0,
     [(re.compile(r"^PEAK_GIVEBACK_PROTECTION_"), None), ("PEAK_GIVEBACK_SCOPE", None), (re.compile(r"^MTF_ATR_TRAIL"), None),
      ("DC_DAYTRADE_TARGET_PCT", None)]),
    ("stop reference too wide / tight", lambda fam, st: "STOP" in fam and not C.is_stop_execution(fam),
     [("BB_FROZEN_STOP_ENABLED", None), ("BB_FROZEN_STOP_FIELD", None), ("BB_FROZEN_STOP_TF", None), ("DC_LOW_FROZEN_STOP_TF", None),
      ("DC_HARD_STOP_TF", None), ("TECHNICAL_DC_STOP_TF", None), ("ATR_ADAPTIVE_STOP_TF", None)]),
    ("STATEFUL_PORTED_EXIT / REDUCE_TO_FLAT churn", lambda fam, st: fam.startswith(("STATEFUL_PORTED_EXIT", "REDUCE_TO_FLAT")),
     [("STRUCTURAL_EXIT_GATE_ENABLED", None), ("VEC_GENERIC_EXIT_MIN_GAIN_PCT", None)]),
]
NOT_WIRED = {"MIN_HOLD_BARS_BEFORE_EXIT": "dead on the churn paths (measured 2026-10-16); listed only once wired (doc 17 §9 open item 2)"}


# ------------------------------------------------------------------------------------------------------------ inputs


def load_ledgers(cat_side, ledger_dir):
    """-> (rows, metas). Each row gets sym_side + board peak + pnl_pp (already in the file)."""
    rows, metas = [], []
    for p in sorted(glob.glob(str(Path(ledger_dir) / cat_side / "*.jsonl"))):
        meta = None
        with open(p) as fh:
            for line in fh:
                d = json.loads(line)
                if d.get("kind") == "meta":
                    meta = d
                    metas.append(d)
                elif d.get("kind") == "trade" and meta is not None:
                    d["sym_side"] = meta["sym_side"]
                    d["exit_family"] = C.exit_family(d.get("exit_reason"))
                    d["entry_family"] = C.entry_family(d.get("entry_reason"))
                    d["peak"] = meta.get("max_invested_dollars")
                    rows.append(d)
    return rows, metas


class Klines:
    """15m bars from the Mac's klines_cache (crypto <SYM>_15m.json, stocks tradier/<SYM>_15m.json); lazily loaded."""

    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.cache = {}

    def bars(self, symbol, venue):
        key = (symbol, venue)
        if key not in self.cache:
            p = self.root / "klines_cache" / ("tradier" if venue == "STOCKS" else "") / f"{symbol}_15m.json"
            out = None
            try:
                raw = json.loads(p.read_text(errors="replace"))
                raw = raw if isinstance(raw, list) else (raw.get("klines") or raw.get("data") or [])
                ts, hi, lo, cl = [], [], [], []
                for b in raw:
                    t = C.iso_epoch(b.get("timestamp") or b.get("time") or b.get("t"))
                    if t is None:
                        continue
                    ts.append(t)
                    hi.append(float(b["high"]))
                    lo.append(float(b["low"]))
                    cl.append(float(b["close"]))
                out = (ts, hi, lo, cl) if ts else None
            except Exception:
                out = None
            self.cache[key] = out
        return self.cache[key]

    def excursions(self, symbol, venue, is_long, t_open, t_close, p_in, p_out):
        b = self.bars(symbol, venue)
        if not b or not p_in:
            return None, None, None
        ts, hi, lo, cl = b
        i0 = bisect.bisect_right(ts, t_open)
        i1 = bisect.bisect_right(ts, t_close)
        if i1 <= i0 and not (ts and ts[0] <= t_open <= ts[-1] + BAR_S):
            return None, None, None
        sgn = 1.0 if is_long else -1.0
        fav = hi if is_long else lo
        adv = lo if is_long else hi
        mfe = max([sgn * (fav[j] - p_in) / p_in * 100.0 for j in range(i0, i1)] + [0.0])
        mae = min([sgn * (adv[j] - p_in) / p_in * 100.0 for j in range(i0, i1)] + [0.0])
        post = None
        if p_out and i1 < len(ts):
            post = max([sgn * (cl[j] - p_out) / p_out * 100.0 for j in range(i1, min(len(ts), i1 + POST_BARS))] + [0.0])
        return round(mfe, 4), round(mae, 4), (round(post, 4) if post is not None else None)


def _echo(reason):
    s = str(reason or "")
    return C.is_broker_echo(s) or s.startswith("BROKER_SYNC") or s.startswith("BROKER_RECONCILED")


def live_lifecycles(cat_side, root=ROOT, klines=None):
    """Hermetic /history lifecycles of the cat_side -> trade rows comparable to the board ledger rows."""
    venue = C.venue_of(cat_side)
    side = cat_side.split("_")[1]
    is_long = side == "LONG"
    cut = C.HERMETIC_CUT[venue]
    if venue == "CRYPTO":
        files = [(a, p) for a in CRYPTO_ACCOUNTS for p in sorted((Path(root) / "data" / "history" / a).glob(f"*_{side}.jsonl"))]
    else:
        files = [(a, p) for a in STOCKS_ACCOUNTS for p in sorted((Path(root) / "data" / "tradier" / "history" / a).glob(f"*_{side}.jsonl"))]
    out, counters = [], Counter()
    for acct, p in files:
        ss = p.name[: -len(".jsonl")]
        if C.cat_side_of(ss) != cat_side:
            counters["file_wrong_cat_side"] += 1
            continue
        if C.is_tokenized(ss):
            counters["tokenized_skipped"] += 1
            continue
        evs, seen_ev = [], set()
        with open(p, errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    counters["bad_json"] += 1
                    continue
                ep = C.iso_epoch(d.get("ts"))
                if ep is None:
                    continue
                if _echo(d.get("reason")):
                    if ep >= cut:
                        counters["broker_echo_dropped"] += 1
                    continue
                ek = (d.get("ts"), d.get("type"), d.get("qty"), d.get("price"), d.get("reason"))
                if ek in seen_ev:
                    counters["duplicate_event_dropped"] += 1
                    continue
                seen_ev.add(ek)
                evs.append((ep, d))
        evs.sort(key=lambda x: x[0])
        qty = cost = peak_cost = realised = 0.0
        life = None
        rows_here = []
        for ep, d in evs:
            typ = str(d.get("type") or "").upper()
            q = abs(float(d.get("qty") or 0.0))
            px = float(d.get("price") or 0.0)
            if q <= 0 or px <= 0:
                continue
            if typ in ("OPEN", "AUGMENT", "REENTRY"):
                if typ == "OPEN" and qty > 1e-12:
                    counters["lifecycle_abandoned_unclosed"] += 1  # its close was a broker-sync echo (dropped); a fresh OPEN starts anew
                    qty = 0.0
                if qty <= 1e-12:
                    life = {"open_ts": ep, "entry_reason": d.get("reason"), "first_px": px, "prov_open": d.get("prov"), "events": 0}
                    qty = cost = peak_cost = realised = 0.0
                qty += q
                cost += q * px
                peak_cost = max(peak_cost, cost)
                life["events"] += 1
            elif typ in ("REDUCE", "CLOSE"):
                if life is None or qty <= 1e-12:
                    counters["decrease_without_known_open"] += 1
                    continue
                qd = min(q, qty)
                avg = cost / qty if qty else 0.0
                realised += qd * (px - avg) * (1.0 if is_long else -1.0)
                cost -= qd * avg
                qty -= qd
                life["events"] += 1
                if qty <= max(1e-9, 1e-6 * q):
                    reason = d.get("reason")
                    fam = C.exit_family(reason)
                    if ep < cut:
                        counters["close_pre_hermetic"] += 1
                    else:
                        if life["open_ts"] < cut:
                            counters["hermetic_close_of_pre_hermetic_open"] += 1
                        tag = "VEC_EXACT" if "VEC_EXACT" in str(reason) else "untagged"
                        if C.is_stop_execution(fam):
                            col = "stop_execution"
                        elif venue == "STOCKS" or tag == "VEC_EXACT":
                            col = "live_hermetic"
                        else:
                            col = "live_other"
                        avg_in = life["first_px"]
                        rows_here.append({"sym_side": ss, "account": acct, "column": col, "open_ts": life["open_ts"], "close_ts": ep,
                                          "bars_held": int(round((ep - life["open_ts"]) / BAR_S)), "entry_reason": life["entry_reason"],
                                          "exit_reason": reason, "entry_family": C.entry_family(life["entry_reason"]), "exit_family": fam,
                                          "pnl_dollars": realised, "deployed": peak_cost, "entry_price": avg_in, "exit_price": px,
                                          "pnl_pct": realised / peak_cost * 100.0 if peak_cost else 0.0, "prov": d.get("prov"), "prov_tag": tag,
                                          "opened_pre_hermetic": life["open_ts"] < cut})
                    qty = cost = 0.0
                    life = None
        if rows_here:
            win_peak = max(r["deployed"] for r in rows_here) or 1.0
            sym = ss.rsplit("_", 1)[0]
            for r in rows_here:
                r["peak"] = win_peak
                r["pnl_pp"] = r["pnl_dollars"] / win_peak * 100.0
                if klines is not None:
                    r["mfe_pct"], r["mae_pct"], r["post_exit_32b_pct"] = klines.excursions(sym, venue, is_long, r["open_ts"], r["close_ts"],
                                                                                       r["entry_price"], r["exit_price"])
            out.extend(rows_here)
            counters["lifecycles"] += len(rows_here)
    return out, dict(counters)


def relabel_unlabelled_exits(lrows, brows, counters):
    """A /history close whose reason is an ENTRY label (e.g. `REDUCE ... B_KZONE |VEC_EXACT`) names no exit: it is booked as
    family UNLABELLED_EXIT (raw label kept), never ranked as if B_KZONE were an exit. Vocabulary = this cat_side's board ledgers."""
    exits = {r["exit_family"] for r in brows}
    entries = {r.get("entry_family") for r in brows}
    for r in lrows:
        fam = C.map_to_vocab(r["exit_family"], exits)
        if fam != r["exit_family"]:
            r["exit_family_raw"] = r["exit_family"]
            r["exit_family"] = fam
            counters["live_family_mapped_to_board_vocab"] = counters.get("live_family_mapped_to_board_vocab", 0) + 1
        if fam == r.get("entry_family") or (fam in entries and fam not in exits):
            r.setdefault("exit_family_raw", fam)
            r["exit_family"] = "UNLABELLED_EXIT"
            counters["unlabelled_exit_relabelled"] = counters.get("unlabelled_exit_relabelled", 0) + 1


# ------------------------------------------------------------------------------------------------------------ ranking


def _scale(r):
    peak = r.get("peak") or 0.0
    return (float(r.get("deployed") or 0.0) / peak) if peak else 0.0


def family_stats(rows):
    """Per exit family: the doc 17 §3.2 measures. rows need pnl_pp, pnl_pct, bars_held, deployed, peak, mfe/post (may be None)."""
    agg = {}
    for r in rows:
        fam = r["exit_family"]
        a = agg.setdefault(fam, {"family": fam, "n": 0, "losers": 0, "loss_sum_pct": 0.0, "pnl_sum_pct": 0.0, "one_bar": 0, "bars": 0,
                                 "giveback_sum": 0.0, "premature_sum": 0.0, "premature_n": 0, "drift_into_loss_sum": 0.0, "drift_n": 0,
                                 "mfe_known": 0, "syms": set(), "unrealised_marks": 0})
        pp = float(r.get("pnl_pp") or 0.0)
        pct = float(r.get("pnl_pct") or 0.0)
        a["n"] += 1
        a["syms"].add(r["sym_side"])
        a["pnl_sum_pct"] += pp
        a["bars"] += int(r.get("bars_held") or 0)
        if int(r.get("bars_held") or 0) <= 1:
            a["one_bar"] += 1
        if r.get("unrealised_mark"):
            a["unrealised_marks"] += 1
        if pp < 0:
            a["losers"] += 1
            a["loss_sum_pct"] += pp
        mfe, post = r.get("mfe_pct"), r.get("post_exit_32b_pct")
        sc = _scale(r)
        if mfe is not None:
            a["mfe_known"] += 1
            if pp > 0 and mfe > pct:
                a["giveback_sum"] += (mfe - pct) * sc
            if mfe >= DRIFT_MFE and pp <= 0:
                a["drift_n"] += 1
                a["drift_into_loss_sum"] += pp
        if post is not None and post >= max(PREMATURE_MIN, pct):
            a["premature_n"] += 1
            a["premature_sum"] += post * sc
    out = []
    for a in agg.values():
        n = a["n"]
        out.append({"family": a["family"], "n": n, "losers": a["losers"], "loss_sum_pct": round(a["loss_sum_pct"], 4),
                    "pnl_sum_pct": round(a["pnl_sum_pct"], 4), "one_bar_share": round(a["one_bar"] / n, 4) if n else 0.0,
                    "mean_bars_held": round(a["bars"] / n, 3) if n else 0.0, "giveback_sum": round(a["giveback_sum"], 4),
                    "premature_sum": round(a["premature_sum"], 4), "premature_n": a["premature_n"],
                    "drift_into_loss_sum": round(a["drift_into_loss_sum"], 4), "drift_n": a["drift_n"], "mfe_known": a["mfe_known"],
                    "n_syms": len(a["syms"]), "unrealised_marks": a["unrealised_marks"]})
    out.sort(key=lambda x: (x["loss_sum_pct"], x["drift_into_loss_sum"]))
    return out


def crosstab(rows, top=40):
    agg = {}
    for r in rows:
        k = (r.get("entry_family") or "UNKNOWN", r["exit_family"])
        a = agg.setdefault(k, [0, 0, 0.0, 0.0])
        pp = float(r.get("pnl_pp") or 0.0)
        a[0] += 1
        a[2] += pp
        if pp < 0:
            a[1] += 1
            a[3] += pp
    out = [{"entry_family": k[0], "exit_family": k[1], "n": v[0], "losers": v[1], "pnl_sum_pct": round(v[2], 4), "loss_sum_pct": round(v[3], 4)}
           for k, v in agg.items()]
    out.sort(key=lambda x: x["loss_sum_pct"])
    return out[:top] if top else out


# ------------------------------------------------------------------------------------------------------------ menu


def _tokens(name):
    return [t for t in re.split(r"[_\W]+", str(name).upper()) if t and t not in TOKEN_STOP and not t.isdigit()]


def exit_switches(tidx):
    out = {}
    for tab in EXIT_TABS:
        for _r, sw, cand, isdef in tidx["tabs"].get(tab, {}).get("rows", []):
            out.setdefault(sw, {"tab": tab, "cands": [], "default": None})
            if cand not in out[sw]["cands"]:
                out[sw]["cands"].append(cand)
            if isdef:
                out[sw]["default"] = cand
    return out


def _cands_for(tidx, sw, want):
    have = tidx["switches"].get(sw) or []
    dflt = tidx["defaults"].get(sw)
    if want is None:
        return [c for c in have if c != dflt][:6], []
    keep = [c for c in want if c in have]
    return keep, [c for c in want if c not in have]


def evidence_for(ev, sw, cand):
    """(support_n, support_syms, companions) from the miner's evidence (naked key + positive pair cells)."""
    nk = (ev or {}).get("naked", {}).get(f"{sw}={cand}")
    support_n = nk[1] if nk else 0
    support_pos = nk[2] if nk else 0
    comps = []
    pref = f"{sw}={cand}|"
    for k, v in (ev or {}).get("_pairs_by_switch", {}).get(f"{sw}={cand}", []):
        comps.append((v[4], v[5], k))
    comps.sort(key=lambda x: (-x[0], -x[1]))
    companions = []
    for cnt, _m, k in comps[:3]:
        if cnt <= 0:
            continue
        f, _, opt = k[len(pref):].partition("=")
        companions.append({"filter": f, "opt": opt, "support_syms": cnt})
    return support_n, support_pos, companions


def index_pairs(ev):
    """Group the evidence pairs by switch=cand once (companion lookups)."""
    if not ev or "_pairs_by_switch" in ev:
        return ev
    by = defaultdict(list)
    for k, v in ev.get("pairs", {}).items():
        swc, _, _f = k.partition("|")
        if v[4] > 0:
            by[swc].append((k, v))
    ev["_pairs_by_switch"] = by
    return ev


def build_menu(cat_side, ranked_board, ranked_live, tidx, ev, measured, fwd):
    """family -> [paths]. Families = union of the top board + live_hermetic families by loss."""
    ev = index_pairs(ev)
    exsw = exit_switches(tidx)
    fams = []
    for lst in (ranked_board, ranked_live):
        for st in lst:
            if st["family"] in ("FINAL_MTM",) or C.is_stop_execution(st["family"]):
                continue
            if st["losers"] <= 0 and st["drift_n"] <= 0 and st["giveback_sum"] <= 0:
                continue
            if st["family"] not in [f for f, _ in fams]:
                fams.append((st["family"], st))
    fams = fams[:TOP_FAMILIES_MENU]
    menu, dropped = {}, []
    for fam, st in fams:
        paths, seen = [], set()

        def add(sw, cand, source, extra=None):
            if (sw, cand) in seen:
                return
            why = C.excluded_key_reason(sw, cand) or C.type_refused(sw, cand, cat_side)
            if why:
                dropped.append({"family": fam, "switch": sw, "cand": cand, "why": why})
                return
            seen.add((sw, cand))
            sn, sp, comps = evidence_for(ev, sw, cand)
            f = fwd.get(f"{sw}={cand}", (0, 0))
            p = {"switch": sw, "cand": cand, "companions": [{"filter": c["filter"], "opt": c["opt"]} for c in comps], "source": source,
                 "support_n": sn, "support_syms": sp, "forward_factor": C.forward_factor(*f)}
            if extra:
                p.update(extra)
            paths.append(p)

        for symptom, match, rows in CURATED:
            if not match(fam, st):
                continue
            for spec, want in rows:
                names = [s for s in tidx["switches"] if spec.search(s)] if isinstance(spec, re.Pattern) else [spec]
                if isinstance(spec, re.Pattern) and not names:
                    dropped.append({"family": fam, "switch": spec.pattern, "why": "no template switch matches the curated pattern"})
                for sw in names:
                    if sw in NOT_WIRED:
                        dropped.append({"family": fam, "switch": sw, "why": NOT_WIRED[sw]})
                        continue
                    if sw not in tidx["switches"]:
                        dropped.append({"family": fam, "switch": sw, "why": f"not a row of TEMPLATE_{cat_side}"})
                        continue
                    keep, miss = _cands_for(tidx, sw, want)
                    for v in miss:
                        dropped.append({"family": fam, "switch": sw, "cand": v, "why": f"value not a template candidate of {sw} in {cat_side}"})
                    for c in keep:
                        add(sw, c, "curated", {"symptom": symptom})
        ftok = set(_tokens(fam))
        scored = []
        for sw, meta in exsw.items():
            stok = set(_tokens(sw))
            ov = len(ftok & stok)
            if (ftok and sw.startswith(fam.upper())) or ov >= 2 or (ov >= 1 and len(ftok) == 1):
                scored.append((ov + (2 if sw.startswith(fam.upper()) else 0), sw))
        scored.sort(key=lambda x: (-x[0], x[1]))
        for _s, sw in scored[:10]:
            keep, _miss = _cands_for(tidx, sw, None)
            for c in keep[:3]:
                add(sw, c, "token")
        for m in (measured or {}).get(fam, []):
            if m.get("n_syms_measured", 0) >= LEARNED_MIN_SYMS and m.get("n_syms_loss_reduced", 0) >= LEARNED_MIN_SYMS:
                key = (m["switch"], m["cand"])
                for p in paths:
                    if (p["switch"], p["cand"]) == key:
                        p["learned"] = {k: m[k] for k in ("n_syms_measured", "n_syms_loss_reduced", "sum_family_loss_delta_pp", "sum_d_gain", "n_d_gain_pos")}
                if key not in seen:
                    add(m["switch"], m["cand"], "learned")
                for p in paths:
                    if (p["switch"], p["cand"]) == key and p["source"] == "learned":
                        p["support_n"], p["support_syms"] = m["n_syms_measured"], m["n_syms_loss_reduced"]
        measured_keys = {(m["switch"], m["cand"]) for m in (measured or {}).get(fam, [])}
        for p in paths:
            if p["source"] != "learned" and (p["switch"], p["cand"]) not in measured_keys:
                p["learned_status"] = "pending_evidence"
            elif p["source"] != "learned":
                m = next(x for x in measured[fam] if (x["switch"], x["cand"]) == (p["switch"], p["cand"]))
                p["learned_status"] = "measured_supported" if m["n_syms_loss_reduced"] >= LEARNED_MIN_SYMS else "measured_not_supported"
                p["learned"] = {k: m[k] for k in ("n_syms_measured", "n_syms_loss_reduced", "sum_family_loss_delta_pp", "sum_d_gain", "n_d_gain_pos")}
        rank = {"learned": 0, "curated": 1, "token": 2}
        paths.sort(key=lambda p: (rank[p["source"]], -(p.get("learned", {}).get("n_syms_loss_reduced") or 0), -(p["support_syms"] or 0)))
        menu[fam] = paths
    return menu, dropped


# ------------------------------------------------------------------------------------------------------------ build


def build(a):
    ledger_dir = Path(a.ledgers)
    ev_dir = Path(a.evidence)
    klines = Klines()
    cats = [a.cat_side] if a.cat_side else list(C.CAT_SIDES)
    summary = {}
    for cs in cats:
        brows, metas = load_ledgers(cs, ledger_dir)
        lrows, lcount = live_lifecycles(cs, ROOT, klines)
        relabel_unlabelled_exits(lrows, brows, lcount)
        cols = {"board": brows, "live_hermetic": [r for r in lrows if r["column"] == "live_hermetic"],
                "stop_execution": [r for r in lrows if r["column"] == "stop_execution"]}
        other = [r for r in lrows if r["column"] == "live_other"]
        ranked = {c: family_stats(rs) for c, rs in cols.items()}
        tidx = C.template_index(cs)
        ev = C.read_json(ev_dir / f"evidence_{cs}.json.gz", {}) or {}
        fwd = {}
        for k, (n, ok) in (ev.get("forward") or {}).items():
            kind, _, key = k.partition("|")
            if kind == "FORWARD":
                fwd[key] = (fwd.get(key, (0, 0))[0] + n, fwd.get(key, (0, 0))[1] + ok)
        measured = C.read_json(Path(a.measured) / f"menu_measure_{cs}.json", {}) if a.measured else {}
        menu, dropped = build_menu(cs, ranked["board"], ranked["live_hermetic"], tidx, ev, (measured or {}).get("families", {}), fwd)
        meta = {"cat_side": cs, "built_at": C.utc(), "era_cut": C.ERA_CUT_ISO[C.venue_of(cs)], "tool": "tools/v18_exit_loss_rank.py",
                "board_ledgers": len(metas), "board_trades": len(brows), "board_sym_sides": len({m["sym_side"] for m in metas}),
                "board_npz_windows": sorted({(m.get("window_start"), m.get("window_end")) for m in metas})[-3:],
                "board_sum_gain_pct_replayed": round(sum(m.get("gain_pct") or 0.0 for m in metas), 4),
                "live_counters": lcount, "live_hermetic_lifecycles": len(cols["live_hermetic"]), "stop_execution_lifecycles": len(cols["stop_execution"]),
                "live_other_lifecycles": len(other), "live_mfe_known": sum(1 for r in lrows if r.get("mfe_pct") is not None),
                "normalisation": "pct-points of peak cost basis (board: pnl$/board max_invested x100; live: lifecycle pnl$/(account,sym_side) window peak cost x100); loss_sum_pct signed <= 0",
                "drift_mfe_pct": DRIFT_MFE, "evidence_file": str(ev_dir / f"evidence_{cs}.json.gz") if ev else None,
                "measured_file": (measured or {}).get("meta", {}).get("out") if measured else None,
                "template": tidx["path"], "template_md5": tidx["md5"]}
        doc = {"meta": meta, "columns": ranked, "crosstab": {c: crosstab(cols[c]) for c in ("board", "live_hermetic")},
               "live_other": family_stats(other)}
        mdoc = {"meta": dict(meta, families=len(menu), dropped_curated=len([d for d in dropped if "cand" not in d or d.get("why", "").startswith("value")])),
                "menu": menu, "dropped": dropped}
        summary[cs] = {"board_trades": len(brows), "board_families": len(ranked["board"]), "live_hermetic": len(cols["live_hermetic"]),
                       "stop_execution": len(cols["stop_execution"]), "menu_families": len(menu), "menu_paths": sum(len(v) for v in menu.values())}
        md = render_md(doc, mdoc)
        if a.apply:
            C.write_json(C.LEARN_DIR / f"exit_loss_rank_{cs}.json", doc)
            (C.LEARN_DIR / f"exit_loss_rank_{cs}.md").write_text(md)
            C.write_json(C.LEARN_DIR / f"exit_repair_menu_{cs}.json", mdoc)
        else:
            print(md[:4000])
    print(json.dumps({"apply": bool(a.apply), "summary": summary}, indent=1))
    return 0


def render_md(doc, mdoc):
    m = doc["meta"]
    L = [f"# Exit-loss rank — {m['cat_side']} — {m['built_at']}", "",
         f"Board: {m['board_ledgers']} replayed ledgers / {m['board_trades']} trades (one real `include_ledger=True` eval of each latest "
         f"published final set, today's 30-day NPZ window). Live: {m['live_hermetic_lifecycles']} hermetic lifecycles + "
         f"{m['stop_execution_lifecycles']} stop/execution (opened on/after {m['era_cut']}); live_other {m['live_other_lifecycles']}.",
         f"Units: {m['normalisation']}. Columns are never summed.", ""]
    hdr = ["family", "n", "losers", "loss_sum_pct", "one_bar_share", "mean_bars", "giveback", "premature", "drift_into_loss", "drift_n", "syms"]
    for c in COLUMNS:
        rows = doc["columns"][c][:10]
        L += [f"## {c} — top 10 by loss sum", "", C.md_table(hdr, [[r["family"], r["n"], r["losers"], r["loss_sum_pct"], r["one_bar_share"],
                                                                     r["mean_bars_held"], r["giveback_sum"], r["premature_sum"],
                                                                     r["drift_into_loss_sum"], r["drift_n"], r["n_syms"]] for r in rows]), ""]
    L += ["## entry x exit (board) — 15 worst cells", "", C.md_table(["entry_family", "exit_family", "n", "losers", "loss_sum_pct"],
                                                                      [[x["entry_family"], x["exit_family"], x["n"], x["losers"], x["loss_sum_pct"]] for x in doc["crosstab"]["board"][:15]]), ""]
    L += ["## repair menu (first 6 paths per family)", ""]
    for fam, paths in mdoc["menu"].items():
        L.append(f"- **{fam}**: " + "; ".join(f"`{p['switch']}={p['cand']}` ({p['source']}, n={p['support_n']}, pos={p['support_syms']}, "
                                               f"{p.get('learned_status', 'learned')})" for p in paths[:6]))
    if mdoc["dropped"]:
        L += ["", f"Dropped curated/token entries: {len(mdoc['dropped'])} (see json `dropped`)."]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------------------------------------------------ measure (S2)


def _attr_family_loss(base_rows, cand_rows, family):
    """Loss (pp) of `family` among base trades vs what the same entries became in the candidate (keyed by entry bar)."""
    cb = {r["bar_entry"]: r for r in cand_rows}
    before = after = 0.0
    n = 0
    for r in base_rows:
        if r["exit_family"] != family or (r.get("pnl_pp") or 0.0) >= 0:
            continue
        n += 1
        before += r["pnl_pp"] or 0.0
        c = cb.get(r["bar_entry"])
        after += (c.get("pnl_pp") or 0.0) if c is not None else 0.0
    return n, before, after


def measure_one(job):
    """One sym_side: base ledger (final set) vs final set + k=v for every menu candidate of the families it loses on."""
    rec, cands, root = job
    from tools.v18_ledger_replay import ledger_rows, wait_for_load
    from tools.opt import v12_pilot as VP
    out = []
    wait_for_load()
    prep = VP.prepare_batch(rec["sym_side"], 30)
    if prep is None or (isinstance(prep, dict) and prep.get("_stale")):
        return rec["sym_side"], [{"skip": "NPZ_STALE_OR_MISSING"}]
    npz = prep.get("npz_prepared") or {}
    is_long = bool(prep.get("is_long"))
    base = VP.evaluate_prepared_sanitized(prep, dict(rec["set"]), 30, include_ledger=True)
    brows, _ = ledger_rows(base, npz, is_long, "base")
    for fam, sw, cand in cands:
        ov = dict(rec["set"])
        ok, cv, _why = __import__("tools.switch_types", fromlist=["coerce"]).coerce(sw, cand, "tradier" if rec["cat_side"].startswith("STOCKS") else "crypto")
        if not ok:
            continue
        if ov.get(sw) == cv:
            out.append({"family": fam, "switch": sw, "cand": cand, "inert_already_in_set": True})
            continue
        ov[sw] = cv
        res = VP.evaluate_prepared_sanitized(prep, ov, 30, include_ledger=True)
        crows, _ = ledger_rows(res, npz, is_long, "cand")
        n, before, after = _attr_family_loss(brows, crows, fam)
        fam_after_total = sum((r.get("pnl_pp") or 0.0) for r in crows if r["exit_family"] == fam and (r.get("pnl_pp") or 0.0) < 0)
        out.append({"family": fam, "switch": sw, "cand": cand, "n_base_losers": n, "loss_before_pp": round(before, 6),
                    "same_entries_after_pp": round(after, 6), "family_loss_after_total_pp": round(fam_after_total, 6),
                    "d_gain": round((res.get("gain_pct") or 0.0) - (base.get("gain_pct") or 0.0), 6), "valid": bool(res.get("valid")),
                    "trades": res.get("trades"), "base_trades": base.get("trades")})
    return rec["sym_side"], out


def measure(a):
    root = Path(os.path.expanduser(a.root)).resolve()
    os.chdir(root)
    with gzip.open(os.path.expanduser(a.sets), "rt") as fh:
        sets = {json.loads(l)["sym_side"]: json.loads(l) for l in fh if l.strip()}
    out_dir = Path(os.path.expanduser(a.out))
    out_dir.mkdir(parents=True, exist_ok=True)
    for cs in ([a.cat_side] if a.cat_side else C.CAT_SIDES):
        menu_p = Path(os.path.expanduser(a.menu_dir)) / f"exit_repair_menu_{cs}.json"
        if not menu_p.exists():
            print(f"[measure] {cs}: no menu draft {menu_p}", flush=True)
            continue
        menu = json.loads(menu_p.read_text())["menu"]
        rows, _ = load_ledgers(cs, os.path.expanduser(a.ledgers))
        by_sym = defaultdict(lambda: defaultdict(float))
        for r in rows:
            if (r.get("pnl_pp") or 0.0) < 0:
                by_sym[r["exit_family"]][r["sym_side"]] += r["pnl_pp"]
        jobs = defaultdict(list)
        for fam, paths in menu.items():
            worst = sorted(by_sym.get(fam, {}).items(), key=lambda kv: kv[1])[: a.syms_per_family]
            for ss, _l in worst:
                if ss not in sets:
                    continue
                for p in paths[: a.paths_per_family]:
                    jobs[ss].append((fam, p["switch"], p["cand"]))
        todo = [(sets[ss], c, str(root)) for ss, c in jobs.items()]
        n_evals = sum(len(c) for _s, c, _r in todo)
        print(f"[measure] {cs}: {len(todo)} sym_sides, {n_evals} candidate evals (+1 base each) {C.utc()}", flush=True)
        if not a.apply:
            continue
        results = {}
        if a.workers > 1:
            import multiprocessing as mp
            with mp.get_context("fork").Pool(min(a.workers, 4), maxtasksperchild=10) as pool:
                for ss, res in pool.imap_unordered(measure_one, todo):
                    results[ss] = res
                    print(f"[measure] {cs} {ss} {len(res)} rows", flush=True)
        else:
            for t in todo:
                ss, res = measure_one(t)
                results[ss] = res
        fams = defaultdict(dict)
        for ss, res in results.items():
            for r in res:
                if r.get("skip") or r.get("inert_already_in_set") or "n_base_losers" not in r:
                    continue
                k = (r["switch"], r["cand"])
                e = fams[r["family"]].setdefault(k, {"switch": r["switch"], "cand": r["cand"], "n_syms_measured": 0, "n_syms_loss_reduced": 0,
                                                     "sum_family_loss_delta_pp": 0.0, "sum_d_gain": 0.0, "n_d_gain_pos": 0, "syms": []})
                e["n_syms_measured"] += 1
                delta = r["family_loss_after_total_pp"] - r["loss_before_pp"]
                e["sum_family_loss_delta_pp"] += delta
                e["sum_d_gain"] += r["d_gain"]
                if delta > C.EPS and r["valid"]:
                    e["n_syms_loss_reduced"] += 1
                if r["d_gain"] > C.EPS and r["valid"]:
                    e["n_d_gain_pos"] += 1
                e["syms"].append(ss)
        doc = {"meta": {"cat_side": cs, "at": C.utc(), "host": os.uname().nodename, "out": str(out_dir / f"menu_measure_{cs}.json"),
                        "rule": "family loss reduced = (sum of the family's losing pnl_pp in the candidate ledger) - (same in the base ledger) > 0; "
                                "base = latest published final set on today's NPZ; candidate = base + switch=cand; both real include_ledger evals",
                        "syms_per_family": a.syms_per_family, "paths_per_family": a.paths_per_family},
               "families": {f: sorted(v.values(), key=lambda e: -e["n_syms_loss_reduced"]) for f, v in fams.items()}, "raw": results}
        C.write_json(out_dir / f"menu_measure_{cs}.json", doc)
        print(f"[measure] {cs} written {out_dir / f'menu_measure_{cs}.json'}", flush=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("build")
    p.add_argument("--cat-side", default="")
    p.add_argument("--ledgers", default=str(C.LEARN_DIR / "trade_ledgers"))
    p.add_argument("--evidence", default=str(C.LEARN_DIR / "evidence"))
    p.add_argument("--measured", default=str(C.LEARN_DIR / "menu_measure"))
    p.add_argument("--apply", action="store_true")
    p = sub.add_parser("measure")
    p.add_argument("--cat-side", default="")
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--ledgers", required=True)
    p.add_argument("--sets", required=True)
    p.add_argument("--menu-dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--syms-per-family", type=int, default=6)
    p.add_argument("--paths-per-family", type=int, default=24)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    return build(a) if a.cmd == "build" else measure(a)


if __name__ == "__main__":
    sys.exit(main())
