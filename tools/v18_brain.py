#!/usr/bin/env python3
"""v18_brain — the V18 BRAIN: one learned network per cat_side over the ENTIRE evaluation corpus (E8, USER 2026-10-20).

WHY. tools/v15_brain.py (bible §108) learns from `~/v15_run*` delta logs inside a 14-day window, because its globs were
hardcoded. V18 learns from everything that was ever measured, keeps the four cat_sides strictly apart, connects switches,
filters AND their settings to gain / trades / TIM / DD / Sharpe / WR and to forward / live outcomes, and drives the pilot's
per-1000 allocator with a hard floor so every (switch, filter) pair keeps its chance (doc 17 principles 10-13).

USER LAWS KEPT (v18_user_laws_20261020)
  1 entire corpus: pluggable sources (delta logs of every host and archive, rebuilt_v1.db, /history, board ledgers, the
    experience store's native rows), watermarked + deduped; no time window.
  2 every combination keeps its chance: p_eval = clip(P(pos)/p_ref, floor/1000, 1); the floor is clamped to >= 1/1000 and
    is a guarantee, never zero; inert/damaging are verdicts with n that are re-tested at the floor rate.
  3 four cat_sides = four models: graph, heads, EMAs, exports are per cat_side; there is no GLOBAL scope (v15 had one);
    shrinkage is sym_side -> cat_side only. Cross-pollination is never computed here.
  4 the graph connects SWITCH=cand, FILTER=opt, switch name, setting bucket (TF / bool / numeric tercile), tab family,
    exit/entry family (live + ledgers), regime (board bh bucket) and sym_side, with real outcome sums per metric.
  5 NO-LIES: every count is a logged engine answer. Identity lines (`cached: true`) are collapsed to ONE settlement per
    row visit and never counted as evidence; brain propensities (`brain_p`) are booked and used as inverse-propensity
    weights so skipped cells keep the estimates unbiased.

PIPELINE (three stages, each restartable)
  extract  one source -> a spill file of chronological events (`<ts>\t<json>`), counters, watermarks. Runs where the data
           is (S2 for its logs / rebuilt db / ledgers, the Mac for the ext-drive archives and /history).
  train    LC_ALL=C sort of every spill, then ONE prequential pass (predict-then-learn, chronological), dedupe by row key
           across hosts and copies, -> state pickle + data/brain/v18_brain_<CAT_SIDE>.json + a report JSON.
  export / suggest / explain / compare / report   read the state or the exported JSON.

EVIDENCE UNITS
  row   main-pass row = naked eval of SWITCH=cand + its yellow cells (FILTER=opt), closed by ROW_DONE (logs) or by the next
        row (rebuilt db, promotion inferred from the cum_before step and labelled `inferred`). Cell marginal = cell delta -
        naked delta (same row, same chain), exactly v15_brain.iter_rows.
  synergy  in-board quartet: chain (cum_before), chain+a (naked of a), chain+b (cell b on an IDENTITY row at the same chain
        state: its naked is cached with delta 0, i.e. the switch materialises to the chain), chain+a+b (cell b on row a):
        syn = d_ab - d_a - d_b (v17_priors_mine's "differ in exactly one key within a context", in delta-log form).
  rescue / kill  rescue = naked <= 0 and cell > 0 (the filter at its chain setting suffocates the switch, the option frees
        it); kill = naked > 0 and cell marginal < 0 (the option suffocates a working switch).
  ablation  ABL_STRIP / ABL_DISCOVER / ABL_RESTORE1 / ABL_ATTR records of a board: eff = ATTR - RESTORE1, killer when the
        discovered switch gain d (> 0) falls below 0.5 d with the filter restored (v17_killers rule).
  phase  single-key evals of the other phases (SACRED_REPASS, FOCUS_GRID:K=V, ADD, SOFTEN, ...) vs the then chain.
  forward  next-board persistence: a key promoted on board k of a sym_side is labelled on that sym_side's next board k+1
        (newer NPZ): y = 1 when no other cand of the same switch is positive there and its own fresh naked (if any) is >= 0;
        y = 0 when another cand wins or it turns negative. Predicted AT PROMOTION (features of that moment), learned when
        resolved. FORWARD / HOLDOUT delta-log lines are consumed the same way when present.
  live  hermetic /history lifecycles (tools/v18_exit_loss_rank.live_lifecycles: era cut, broker echoes dropped): per exit
        and entry family; per key as CO-OCCURRENCE (`live_cooc`) with the promotions of the sym_side's last finished board
        before the close — not causal, labelled so.
  ledger  replayed board ledgers (~/v18_learn/trade_ledgers): exit-family trade outcomes per cat_side and per key of the
        replayed set (co-occurrence).

MODEL (per cat_side): online logistic heads R (naked row), F (cell given its naked), F0 (cell before the naked = what the
pilot can use), metric heads dd/tim/trades/sharpe/wr (P(metric improves)) for R and F0, and FWD (P(promotion persists on
the next board)). Features = shrunk log-odds of the key at cat_side and sym_side scope, switch/filter name, setting bucket,
key-in-regime, forward and live evidence, pair, row, tab, board/tab EMA, tier, bh, naked result (F only). Predict-then-learn
on every real eval in time order. mean delta is never a ranking input (anti-predictive, §108.1); ranking = P(pos) x
E[d | pos].

CLI
  extract --source logs|rebuilt|history|ledgers|native --from GLOB --host H --spill-dir D
  extract --source progress|xlsx --from GLOB --host H --spill-dir D [--covered-by GLOB] [--covered-by-ctx J] [--progress-index P]
           (board-level corpora: *_v14_progress.json and published workbooks; docs/v15_pending/22_BRAIN_BOARDS_SOURCE_20261022.md)
  train   --spill-dir D [--spill-dir D2] --cut ISO --out-dir data/brain --state S.pkl --report R.json
  export  --state S.pkl --out-dir data/brain
  suggest --cat-side CS --metric gain|dd|tim|sharpe|wr|forward [--sym-side SS] [--model F|--state S]
  explain --cat-side CS (--switch K | --family F) [--state S]
  compare --champ A.json --chal B.json --spill-dir D [--days 3]
  report  --state S.pkl
Nothing here writes sheets, progress JSONs, templates, configs or gate flags. Production changes only through the gate.
"""
from __future__ import annotations

import argparse
import array
import collections
import glob
import gzip
import hashlib
import json
import math
import os
import pickle
import re
import sqlite3
import subprocess
import sys
import time
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("V18_ROOT") or Path(__file__).resolve().parents[1])
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    import numpy as np
except Exception:  # pragma: no cover - numpy is present in every training env (S2 .venv, Mac conda)
    np = None

SCHEMA = "v18_brain/1"
EPS = 1e-9
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
CRYPTO_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD")
SWITCH_SHEETS = ("STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL",
                 "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK",
                 "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES")
SHEET_SET = frozenset(SWITCH_SHEETS)
EXIT_SHEETS = frozenset({"EXIT_STRUCTURAL", "EXIT_VELOCITY", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER"})
DENY = frozenset({"MODE", "VENUE", "IS_TRADIER", "SIMPLE_PRICE_GT0_ENABLED"})
TFS = frozenset({"OFF", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "D", "1d", "W", "M"})
YELLOW_RE = re.compile(r"^[A-Za-z0-9_]+=[^=:+]*$")
M_SHRINK = float(os.environ.get("V18_BRAIN_M", os.environ.get("V15_BRAIN_M", "8")))
HARD_MIN_FLOOR = 1.0
DEFAULT_FLOOR = float(os.environ.get("V18_FLOOR_PER_1000", "5"))
DEFAULT_PREF_R = float(os.environ.get("V18_BRAIN_PREF_R", "0.001"))
DEFAULT_PREF_F = float(os.environ.get("V18_BRAIN_PREF_F", "0.002"))
W_FIT = float(os.environ.get("V18_BRAIN_W_FIT", "0.25"))
W_FWD = float(os.environ.get("V18_BRAIN_W_FWD", "1.0"))
MAX_EXPORT_BYTES = int(os.environ.get("V18_BRAIN_MAX_BYTES", str(10 * 1000 * 1000)))
METRIC_HEADS = ("dd", "tim", "trades", "sharpe", "wr")
METRIC_IDX = {"tim": 0, "trades": 1, "dd": 2, "sharpe": 3, "wr": 4}
METRIC_SIGN = {"tim": 1.0, "trades": 1.0, "dd": -1.0, "sharpe": 1.0, "wr": 1.0}
TIER_CODE = {"W": 1.0, "WINNER": 1.0, "M": 0.0, "MEDIOCRE": 0.0, "L": -1.0, "LOSER": -1.0, "LOST_CAUSE": -1.0}
NF = 20
P_REFS_REPORT = (0.001, 0.002, 0.005, 0.01, 0.02, 0.05)


# ------------------------------------------------------------------------------------------------ small helpers

def cat_side_of(sym_side):
    """'BTCUSDC_LONG' -> 'CRYPTO_LONG'; anything that is not <SYM>_LONG/_SHORT -> None (refused, never guessed)."""
    s = str(sym_side or "").strip()
    if "_" not in s:
        return None
    symbol, side = s.rsplit("_", 1)
    side = side.upper()
    if side not in ("LONG", "SHORT") or not symbol:
        return None
    return ("CRYPTO" if symbol.upper().endswith(CRYPTO_QUOTES) else "STOCKS") + "_" + side


def sym_side_of_path(path):
    name = os.path.basename(str(path)).replace(".jsonl", "")
    s = name.split("_jump")[0].split("_sequential")[0]
    if not s.endswith(("_LONG", "_SHORT")):
        s = "_".join(name.split("_")[:2])
    return s


def _f(x):
    try:
        if x is None:
            return None
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _r(x, nd=5):
    return None if x is None else round(x, nd)


def iso_of(x):
    """epoch / ISO (any offset, fractional) -> 'YYYY-MM-DDTHH:MM:SSZ' (sortable as text); None when unreadable."""
    if x is None or x == "":
        return None
    try:
        if isinstance(x, (int, float)):
            return datetime.fromtimestamp(float(x), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        s = str(x).strip()
        if len(s) == 20 and s.endswith("Z") and s[10] == "T":
            return s
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def epoch_of(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def utc_now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def h64(text):
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8", "replace"), digest_size=8).digest(), "big") >> 1


def lo(pos, n, a=0.5):
    """smoothed log-odds of a (weighted) hit rate"""
    return math.log((pos + a) / (max(0.0, n - pos) + a))


def sig(z):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def logit(p):
    p = min(0.98, max(0.002, p))
    return math.log(p / (1.0 - p))


def tier_code(t):
    return TIER_CODE.get(str(t or "").strip().upper(), 0.0)


def bh_code(bh):
    v = _f(bh)
    return 0.0 if v is None else max(-1.0, min(1.0, v / 25.0))


def regime_of(bh):
    """Board regime from its buy-and-hold over the window (the only regime context every board carries). The NPZ-feature
    classifier tools/v17_combo_engine.classify_regime needs bars the corpus does not hold, so it is not used here."""
    v = _f(bh)
    if v is None:
        return "UNK"
    if abs(v) < 2.0:
        return "FLAT"
    if v >= 10.0:
        return "STRONG_UP"
    if v > 0:
        return "UP"
    return "STRONG_DOWN" if v <= -10.0 else "DOWN"


def floor_frac(floor_per_1000):
    """The floor guarantee: never below HARD_MIN_FLOOR per 1000, whatever is configured."""
    try:
        v = float(floor_per_1000)
    except Exception:
        v = DEFAULT_FLOOR
    return max(HARD_MIN_FLOOR, v) / 1000.0


def p_eval(p_pos, p_ref, floor_per_1000, n_ev, min_n=3):
    """Pure. Unevidenced (n < min_n) -> 1.0 (always computed). Else clip(p/p_ref, floor/1000, 1), floor >= 1/1000."""
    if n_ev < min_n:
        return 1.0
    return max(floor_frac(floor_per_1000), min(1.0, p_pos / max(1e-9, p_ref)))


def draw(sym_side, tab, key, round_id):
    return int(hashlib.sha1(f"{sym_side}|{tab}|{key}|{round_id}".encode()).hexdigest()[:8], 16) / float(1 << 32)


def is_numeric(s):
    try:
        float(str(s))
        return str(s).strip().lower() not in ("nan", "inf", "-inf", "true", "false")
    except Exception:
        return False


class Buckets:
    """SETTING bucket of a value: TF (cross-switch within a tab family), bool, or numeric tercile among the switch's
    candidates (the cat_side TEMPLATE's when its index is loaded, else the values this cat_side has seen)."""

    def __init__(self, cands=None):
        self.cands = {}
        for sw, lst in (cands or {}).items():
            nums = sorted({float(c) for c in lst if c is not None and is_numeric(c)})
            if nums:
                self.cands[sw] = nums

    def of(self, tab, sw, val):
        v = str(val if val is not None else "").strip()
        fam = (tab or "ANY").split("_")[0]
        if v in TFS:
            return f"{fam}:TF={v}"
        if v in ("True", "False"):
            return f"{fam}:BOOL={v}"
        if is_numeric(v):
            nums = self.cands.get(sw)
            x = float(v)
            if not nums or len(nums) < 3:
                return f"{sw}:num"
            i = bisect_right(nums, x) - 1
            q = (max(0, i)) / max(1, len(nums) - 1)
            return f"{sw}:" + ("lo" if q < 1 / 3 else ("mid" if q < 2 / 3 else "hi"))
        return f"{sw}:other"


# ------------------------------------------------------------------------------------------------ extraction

class Spill:
    """Append-only event spill `<ts>\t<json>` (one per extract run). Events without a ts are refused and counted."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = gzip.open(str(self.path) + ".part", "wt", compresslevel=3) if str(self.path).endswith(".gz") else open(str(self.path) + ".part", "w")
        self.n = collections.Counter()

    def __call__(self, ev):
        ts = ev.get("t")
        if not ts:
            self.n["refused_no_ts"] += 1
            return
        self.fh.write(ts + "\t" + json.dumps(ev, separators=(",", ":")) + "\n")
        self.n[ev.get("e", "?")] += 1

    def close(self):
        self.fh.close()
        os.replace(str(self.path) + ".part", self.path)


class Assembler:
    """One delta-log stream (a file, or one rebuilt src) in file order -> brain events. Pure apart from `emit`.
    Identity lines (`cached`) are values (chain reference, quartet b-alone) but never evidence."""

    def __init__(self, sym_side, src, emit, counters, ctx_lookup=None):
        self.sym = sym_side
        self.cs = cat_side_of(sym_side)
        self.src = src
        self.emit = emit
        self.c = counters
        self.ctx_lookup = ctx_lookup
        self.board = None
        self.board_rows = 0
        self.ctx = {"bh": None, "tier": None, "round": None}
        self.ref = [None] * 5
        self.ref_fp = None
        self.buf = None
        self.inert = {}
        self.abl = {"strip": {}, "restore1": {}, "disc": {}}

    # -- board / context
    def _new_board(self, ts, ctx, line):
        self.close_row(None)
        self.board = f"{self.sym}|{ts}"
        self.board_rows = 0
        self.inert = {}
        self.abl = {"strip": {}, "restore1": {}, "disc": {}}
        if ctx:
            self.ctx = {"bh": _f(ctx.get("bh")), "tier": ctx.get("tier"), "round": ctx.get("round")}
        elif self.ctx_lookup is not None:
            got = self.ctx_lookup(self.src, line)
            if got:
                self.ctx = got
        self.c["boards"] += 1
        self.emit({"e": "board", "t": ts, "s": self.sym, "b": self.board, "bh": _r(self.ctx.get("bh"), 3), "ti": self.ctx.get("tier"),
                   "rd": self.ctx.get("round"), "rk": h64(f"B|{self.board}")})

    def _ensure_board(self, ts, line):
        if self.board is None:
            self._new_board(ts, None, line)

    def _rec(self, r):
        d = _f(r.get("delta"))
        valid = bool(r.get("valid"))
        m = (_f(r.get("tim")), _f(r.get("trades")), _f(r.get("max_dd_pct")), _f(r.get("pool_sharpe")), _f(r.get("wr_pct")))
        diffs = [(m[i] - self.ref[i]) if (valid and m[i] is not None and self.ref[i] is not None) else None for i in range(5)]
        bp = _f(r.get("brain_p"))
        w = 1.0 / max(bp, 1e-3) if (bp is not None and bp > 0) else 1.0
        return [d, 1 if valid else 0, _f(r.get("secs")) or 0.0] + diffs + [w]

    def _ref_update(self, r):
        if bool(r.get("valid")) and (_f(r.get("delta")) is not None) and abs(_f(r.get("delta"))) <= EPS and _f(r.get("tim")) is not None:
            self.ref = [_f(r.get("tim")), _f(r.get("trades")), _f(r.get("max_dd_pct")), _f(r.get("pool_sharpe")), _f(r.get("wr_pct"))]
            self.ref_fp = r.get("fp")

    # -- rows
    def close_row(self, promoted, how=None, next_cb=None):
        b = self.buf
        self.buf = None
        if b is None:
            return
        if promoted is None and next_cb is not None and b["cb"] is not None:
            promoted = (next_cb - b["cb"]) > 1e-7
            how = "inferred"
        naked = b.get("naked")
        nd = naked[0] if (naked is not None and naked[1] and naked[0] is not None) else None
        cbk = None if b["cb"] is None else round(b["cb"], 6)
        if naked is not None and b.get("naked_cached") and naked[0] is not None and abs(naked[0]) <= EPS and cbk is not None:
            box = self.inert.setdefault(cbk, {})
            for hdr, val in b["yv"].items():
                box.setdefault(hdr, val)
        ys = []
        syn = []
        inert_here = self.inert.get(cbk) if cbk is not None else None
        for hdr, rec in b["y"].items():
            if naked is None or naked[0] is None or rec[0] is None:
                self.c["cells_without_naked"] += 1
                continue
            md = rec[0] - naked[0]
            mm = [(rec[3 + i] - naked[3 + i]) if (rec[3 + i] is not None and naked[3 + i] is not None) else None for i in range(5)]
            ys.append([hdr, _r(md), rec[1], _r(rec[2], 4)] + [_r(x) for x in mm] + [_r(rec[8], 3), _r(rec[0])])
            if nd is not None and abs(nd) > EPS and rec[1] and inert_here is not None and hdr in inert_here and inert_here[hdr] is not None:
                syn.append([hdr, _r(rec[0] - nd - inert_here[hdr])])
        sw, cand = b["key"][2], b["key"][3]
        nrec = None
        if naked is not None:
            nrec = [_r(naked[0]), naked[1], _r(naked[2], 4)] + [_r(x) for x in naked[3:8]] + [_r(naked[8], 3), 0 if b.get("naked_was_cached") else 1]
        self.board_rows += 1
        self.c["rows"] += 1
        self.c["fresh_cells"] += len(ys)
        if nrec is not None and nrec[-1]:
            self.c["fresh_naked"] += 1
        rk = h64(f"R|{self.sym}|{b['key'][0]}|{b['key'][1]}|{sw}|{cand}|{b['t0']}|{cbk}")
        if not ys and not syn and not promoted and (nrec is None or not nrec[-1]):
            self.c["identity_only_rows"] += 1
            self.emit({"e": "id", "t": b["t0"], "s": self.sym, "k": f"{sw}={cand}", "ni": b["ni"], "rk": rk})
            return
        self.emit({"e": "row", "t": b["t0"], "s": self.sym, "b": self.board, "sh": b["key"][0], "k": f"{sw}={cand}", "cb": _r(b["cb"]), "n": nrec,
                   "y": ys, "ni": b["ni"], "sy": syn, "p": promoted, "ps": how, "g": regime_of(self.ctx.get("bh")), "ti": self.ctx.get("tier"),
                   "bh": _r(self.ctx.get("bh"), 3), "rk": rk})

    def feed(self, r, line):
        label = r.get("label")
        ts = iso_of(r.get("ts"))
        if ts is None:
            self.c["no_ts"] += 1
            return
        if label == "BOARD_CTX":
            self._new_board(ts, {"bh": r.get("bh"), "tier": r.get("tier"), "round": r.get("round")}, line)
            return
        if label == "ROW_DONE":
            b = self.buf
            if b is not None and b["key"] == (r.get("sheet"), r.get("row"), r.get("switch"), r.get("cand")):
                self.close_row(bool(r.get("promoted")), "rowdone")
            elif r.get("promoted"):
                self.c["row_done_unmatched_promoted"] += 1
            return
        if label in ("FORWARD", "HOLDOUT"):
            self._ensure_board(ts, line)
            ev = {k: r.get(k) for k in ("key", "d_gain", "d_trades", "d_dd", "d_tim", "d_sharpe", "d_wr", "final_minus_start", "unseen_bars", "days")}
            ev.update({"e": label.lower(), "t": ts, "s": r.get("sym_side") or self.sym, "b": self.board, "rk": h64(f"{label}|{self.sym}|{ts}|{r.get('key')}|{line}|{self.src}")})
            self.c[label.lower()] += 1
            self.emit(ev)
            return
        if label == "SETTLE":
            self._ensure_board(ts, line)
            self.c["settle_" + str(r.get("code"))] += 1
            self.emit({"e": "settle", "t": ts, "s": self.sym, "code": r.get("code"), "k": r.get("key"), "f": r.get("filter"), "p": _f(r.get("p")),
                       "rk": h64(f"S|{self.sym}|{ts}|{r.get('code')}|{r.get('key')}|{r.get('filter')}|{line}")})
            return
        sw, cand = r.get("switch"), r.get("cand")
        if label == "BEST_START_SET" or sw == "BEST_START":
            if self.board is None or self.board_rows > 0:
                self._new_board(ts, None, line)
            self._ref_update(r)
            return
        if r.get("gain_pct") is None and r.get("delta") is None:
            self.c["other_lines"] += 1
            return
        self.c["eval_lines"] += 1
        cached = bool(r.get("cached"))
        if cached:
            self.c["identity_lines"] += 1
        self._ensure_board(ts, line)
        self._ref_update(r)
        lab = str(label or "")
        if lab.startswith("RED_RETRY:"):
            lab = lab[len("RED_RETRY:"):]
        sheet = r.get("sheet")
        if sw in DENY:
            self.c["deny_lines"] += 1
            return
        if sheet in SHEET_SET and sw and (lab == "naked" or YELLOW_RE.match(lab)):
            key = (sheet, r.get("row"), sw, cand if cand is not None else "")
            cb = _f(r.get("cum_before"))
            if self.buf is None or self.buf["key"] != key:
                self.close_row(None, next_cb=cb)
                self.buf = {"key": key, "t0": ts, "cb": cb, "y": {}, "yv": {}, "ni": 0, "naked": None}
            rec = self._rec(r)
            if lab == "naked":
                self.buf["naked"] = rec
                fp = r.get("fp")
                self.buf["naked_cached"] = cached and (fp is None or self.ref_fp is None or fp == self.ref_fp)
                self.buf["naked_was_cached"] = cached
                if cached:
                    self.buf["ni"] += 1
            else:
                self.buf["yv"][lab] = rec[0]
                if cached:
                    self.buf["ni"] += 1
                else:
                    self.buf["y"][lab] = rec
            return
        if lab == "hustle":
            self.c["hustle_lines"] += 1
            return
        if lab.startswith("ABL_"):
            self._abl(lab, r, ts, line, cached)
            return
        if cached:
            return
        if sw and cand is not None and "+" not in lab and (":" not in lab or lab.rsplit(":", 1)[1] == f"{sw}={cand}"):
            rec = self._rec(r)
            self.c["phase_evals"] += 1
            self.emit({"e": "phase", "t": ts, "s": self.sym, "ph": lab.split(":", 1)[0], "k": f"{sw}={cand}", "sh": sheet, "d": _r(rec[0]), "v": rec[1],
                       "sec": _r(rec[2], 4), "m": [_r(x) for x in rec[3:8]], "w": _r(rec[8], 3), "rk": h64(f"P|{self.sym}|{ts}|{lab}|{_r(rec[0])}|{line}")})
            return
        self.c["other_evals"] += 1

    def _abl(self, lab, r, ts, line, cached):
        pre, _, rest = lab.partition(":")
        group, _, tail = rest.partition(":")
        g = _f(r.get("gain_pct"))
        A = self.abl
        if pre == "ABL_STRIP":
            A["strip"][group] = g
        elif pre == "ABL_DISCOVER":
            s0 = A["strip"].get(group)
            if g is not None and s0 is not None and bool(r.get("valid")):
                A["disc"][(group, tail)] = g - s0
        elif pre == "ABL_RESTORE1":
            A["restore1"][(group, tail)] = g
        elif pre == "ABL_ATTR":
            eq = tail.find("=")
            plus = tail.find("+", eq if eq >= 0 else 0)
            if plus < 0:
                return
            swk, filt = tail[:plus], tail[plus + 1:]
            dd = A["disc"].get((group, swk))
            r1 = A["restore1"].get((group, filt))
            if dd is None or r1 is None or g is None:
                self.c["abl_attr_unpaired"] += 1
                return
            eff = g - r1
            self.c["abl_attr"] += 1
            self.emit({"e": "abl", "t": ts, "s": self.sym, "grp": group, "k": swk, "f": filt, "dd": _r(dd), "eff": _r(eff),
                       "kill": bool(dd > EPS and eff < 0.5 * dd), "rk": h64(f"A|{self.sym}|{ts}|{lab}|{_r(g)}|{line}")})
        elif pre in ("ABL_RESTORE", "ABL_EXEMPT", "ABL_PROXY") and not cached:
            d = _f(r.get("delta"))
            self.c["abl_remedy"] += 1
            self.emit({"e": "remedy", "t": ts, "s": self.sym, "cls": pre, "lab": tail, "d": _r(d), "v": 1 if r.get("valid") else 0,
                       "rk": h64(f"M|{self.sym}|{ts}|{lab}|{_r(d)}|{line}")})

    def finish(self):
        self.close_row(None)


def _line_count(path):
    n = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            n += chunk.count(b"\n")
    return n


def _load_wm(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return {}


def _save_wm(path, wm):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = str(path) + ".tmp"
    Path(tmp).write_text(json.dumps(wm, indent=1, sort_keys=True))
    os.replace(tmp, path)


class CtxMap:
    """src path -> [(line, ctx)] from the rebuilt db (bh/tier/round per board), for delta logs that predate BOARD_CTX."""

    def __init__(self, path=None):
        self.m = {}
        if path and Path(path).exists():
            raw = json.loads(Path(path).read_text())
            for src, lst in raw.items():
                self.m[src] = sorted((int(a), b) for a, b in lst)

    def __call__(self, src, line):
        lst = self.m.get(src)
        if not lst:
            return None
        i = bisect_right([a for a, _ in lst], int(line or 0)) - 1
        return dict(lst[max(0, i)][1])


def extract_logs(a, emit, counters):
    files = []
    for g in a.source_from:
        files.extend(glob.glob(os.path.expanduser(g)))
    files = sorted(set(files))
    wm_path = Path(a.spill_dir) / f"watermarks_{a.host}.json"
    wm = _load_wm(wm_path)
    ctx = CtxMap(a.ctx_map)
    for f in files:
        try:
            st = os.stat(f)
        except OSError:
            continue
        key = f"{a.host}:{os.path.abspath(f)}"
        prev = wm.get(key) or {}
        if prev.get("size") == st.st_size and not a.full:
            counters["files_unchanged"] += 1
            continue
        min_line = 0 if a.full else int(prev.get("lines") or 0)
        sym = sym_side_of_path(f)
        if cat_side_of(sym) is None:
            counters["files_refused_cat_side"] += 1
            continue
        counters["files"] += 1
        state = {"min_line": min_line}

        def gated(ev, _st=state):
            if ev.get("_line", 10 ** 12) > _st["min_line"]:
                ev.pop("_line", None)
                emit(ev)
            else:
                counters["events_below_watermark"] += 1

        asm = Assembler(sym, os.path.abspath(f), lambda ev: gated(dict(ev, _line=asm_line[0])), counters, ctx_lookup=ctx)
        asm_line = [0]
        n = 0
        last_ts = ""
        with open(f, "rb") as fh:
            for raw in fh:
                n += 1
                if not raw.endswith(b"\n"):
                    counters["partial_last_line"] += 1
                    n -= 1
                    break
                asm_line[0] = n
                counters["lines"] += 1
                try:
                    r = json.loads(raw)
                except Exception:
                    counters["bad_json"] += 1
                    continue
                if not isinstance(r, dict):
                    continue
                if r.get("sym_side") and r.get("sym_side") != sym and cat_side_of(r.get("sym_side")):
                    sym2 = r["sym_side"]
                    if sym2 != asm.sym:
                        counters["sym_side_switch_in_file"] += 1
                        asm.finish()
                        asm = Assembler(sym2, os.path.abspath(f), lambda ev: gated(dict(ev, _line=asm_line[0])), counters, ctx_lookup=ctx)
                asm.feed(r, n)
                last_ts = r.get("ts") or last_ts
            asm.finish()
        wm[key] = {"size": st.st_size, "lines": n, "last_ts": last_ts, "at": utc_now()}
    _save_wm(wm_path, wm)


def extract_rebuilt(a, emit, counters):
    """rebuilt_v1.db evals_compact, rowid order (one src after another). Rows of a src whose delta log still exists on this
    host with at least that many lines are COVERED by the log (the log is a superset: ROW_DONE, BOARD_CTX) and skipped; the
    rest is assembled exactly like a log. Always writes the src -> ctx map (bh / tier / round per board)."""
    p = os.path.expanduser(a.source_from[0])
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    nlines = {}
    asms = {}
    ctxmap = collections.defaultdict(list)
    last_ctx = {}
    last = 0
    batch = 200000
    t0 = time.time()
    while True:
        rows = con.execute("SELECT rowid, symside, switch, cand, ctx, metrics, prov FROM evals_compact WHERE rowid > ? ORDER BY rowid LIMIT ?", (last, batch)).fetchall()
        if not rows:
            break
        for rowid, symside, sw, cand, ctx, metrics, prov in rows:
            last = rowid
            counters["rebuilt_rows"] += 1
            try:
                pv = json.loads(prov)
            except Exception:
                counters["rebuilt_bad_prov"] += 1
                continue
            src = pv.get("src") or ""
            line = int(pv.get("line") or 0)
            if last_ctx.get(src) != ctx:
                last_ctx[src] = ctx
                try:
                    cj = json.loads(ctx) if ctx else {}
                except Exception:
                    cj = {}
                ctxmap[src].append((line, {"bh": cj.get("bh_pct"), "tier": cj.get("tier"), "round": cj.get("round")}))
            if src not in nlines:
                nlines[src] = _line_count(src) if (src and os.path.exists(src)) else 0
            if line <= nlines[src]:
                counters["rebuilt_rows_covered_by_log"] += 1
                if pv.get("cached_when_logged"):
                    counters["rebuilt_rows_covered_identity"] += 1
                continue
            counters["rebuilt_rows_used"] += 1
            try:
                m = json.loads(metrics)
            except Exception:
                counters["rebuilt_bad_metrics"] += 1
                continue
            r = {"ts": pv.get("ts"), "label": pv.get("label"), "sheet": pv.get("sheet"), "row": pv.get("row"), "switch": sw, "cand": cand,
                 "delta": pv.get("delta"), "cum_before": pv.get("cum_before"), "secs": pv.get("secs"), "cached": pv.get("cached_when_logged"),
                 "gain_pct": m.get("gain_pct"), "trades": m.get("trades"), "tim": m.get("tim_pct"), "max_dd_pct": m.get("max_dd_pct"),
                 "pool_sharpe": m.get("pool_sharpe"), "wr_pct": m.get("wr_pct"), "valid": m.get("valid"),
                 "fp": m.get("behavior_fingerprint")}
            if sw == "BEST_START":
                r["label"] = "BEST_START_SET"
            asm = asms.get(src)
            if asm is None:
                asm = asms[src] = Assembler(symside, src, emit, counters)
                try:
                    cj = json.loads(ctx) if ctx else {}
                    asm.ctx = {"bh": _f(cj.get("bh_pct")), "tier": cj.get("tier"), "round": cj.get("round")}
                except Exception:
                    pass
            asm.feed(r, line)
        if counters["rebuilt_rows"] % 2000000 < batch:
            print(f"[rebuilt] {counters['rebuilt_rows']:,} rows {time.time() - t0:.0f}s used={counters['rebuilt_rows_used']:,}", flush=True)
    for asm in asms.values():
        asm.finish()
    con.close()
    out = Path(a.spill_dir) / f"ctxmap_{a.host}.json"
    out.write_text(json.dumps({k: v for k, v in ctxmap.items()}))
    counters["ctxmap_srcs"] = len(ctxmap)


def extract_history(a, emit, counters):
    """Hermetic /history lifecycles per cat_side via tools/v18_exit_loss_rank.live_lifecycles (the store's era and echo rules)."""
    from tools import v18_exit_loss_rank as X
    root = Path(a.source_from[0]) if a.source_from else ROOT
    for cs in CAT_SIDES:
        rows, cnt = X.live_lifecycles(cs, root=root)
        for k, v in cnt.items():
            counters[f"{cs}:{k}"] += v
        for r in rows:
            ts = iso_of(r.get("close_ts"))
            if ts is None:
                continue
            counters["live_lifecycles"] += 1
            emit({"e": "live", "t": ts, "s": r["sym_side"], "acct": r.get("account"), "col": r.get("column"), "fam": r.get("exit_family"),
                  "efam": r.get("entry_family"), "pnl": _r(_f(r.get("pnl_pp")), 4), "pnl_pct": _r(_f(r.get("pnl_pct")), 4), "bars": r.get("bars_held"),
                  "rk": h64(f"L|{r.get('account')}|{r['sym_side']}|{r.get('open_ts')}|{r.get('close_ts')}")})


def extract_ledgers(a, emit, counters):
    """Replayed board ledgers (tools/v18_ledger_replay.py): one trade row per line, `kind: meta` header with the set sha.
    The replayed set's keys come from replay_sets.jsonl.gz (sym_side -> set) when present."""
    root = Path(os.path.expanduser(a.source_from[0]))
    sets = {}
    sp = Path(os.path.expanduser(a.sets)) if a.sets else None
    if sp and sp.exists():
        with gzip.open(sp, "rt") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                ss = d.get("sym_side")
                st = d.get("set") or d.get("overrides") or {}
                if ss and isinstance(st, dict):
                    sets[ss] = sorted(f"{k}={v}" for k, v in st.items() if k not in DENY)
    for f in sorted(root.glob("*/*.jsonl")):
        meta = None
        with open(f, errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    counters["bad_json"] += 1
                    continue
                if d.get("kind") == "meta":
                    meta = d
                    ss = meta.get("sym_side")
                    if sets.get(ss):
                        emit({"e": "ledgerset", "t": "0000-00-00T00:00:00Z", "s": ss, "set": sets[ss], "rk": h64(f"TS|{ss}|{meta.get('set_sha256')}")})
                    continue
                if d.get("kind") != "trade" or meta is None:
                    continue
                ss = meta.get("sym_side")
                ts = iso_of(d.get("ts_exit")) or iso_of(meta.get("replayed_at"))
                counters["ledger_trades"] += 1
                emit({"e": "ledger", "t": ts, "s": ss, "fam": d.get("exit_family"), "efam": d.get("entry_family"), "pnl": _r(_f(d.get("pnl_pp")), 4),
                      "rk": h64(f"T|{ss}|{d.get('eval_id')}|{d.get('trade_no')}")})


def extract_native(a, emit, counters):
    """Native eval-cache rows from the experience store (provenance='native', explicit delta vs a shared base) -> in-context
    quartets: B, B+x, B+y, B+x+y measured in one (sym_side, base, window, offset, npz) context -> syn = Z - X - Y + B.
    Unary native effects are NOT emitted (the same evals are already in the delta logs that wrote them). Read-only; grouped
    in memory per shard (no ORDER BY temp sort on the drive)."""
    root = Path(os.path.expanduser(a.source_from[0]))
    max_d = int(a.max_delta_keys)
    for shard in sorted(root.glob("experience_*_*.db")):
        if not any(f"experience_{c}_" in shard.name for c in CAT_SIDES):
            continue
        con = sqlite3.connect(f"file:{shard}?mode=ro", uri=True)
        groups = collections.defaultdict(dict)
        try:
            cur = con.execute("SELECT sym_side, base_set_id, window_days, offset_days, npz_md5, delta, gain, valid, ts FROM evals "
                              "WHERE provenance='native' AND delta_rule='explicit'")
            for ss, bid, wd, od, npz, delta, gain, valid, ts in cur:
                counters["native_rows"] += 1
                if not valid or gain is None:
                    continue
                try:
                    dj = json.loads(delta) if delta else {}
                except Exception:
                    continue
                st = dj.get("set") or {}
                dl = dj.get("del") or []
                if len(st) + len(dl) > max_d:
                    counters["native_rows_delta_too_wide"] += 1
                    continue
                fs = frozenset([f"{k}={v}" for k, v in st.items()] + [f"{k}=<absent>" for k in dl])
                groups[(ss, bid, wd, od, npz)][fs] = (float(gain), ts)
        except Exception as e:
            counters["shard_error"] += 1
            print(f"[native] {shard.name}: {e}", file=sys.stderr)
        con.close()
        for gk, g in groups.items():
            for dset, (gz, tsz) in g.items():
                if len(dset) < 2:
                    continue
                parents = [x for x in dset if (dset - {x}) in g]  # Z - {x} measured: one-key edges into Z
                if len(parents) < 2:
                    continue
                parents.sort()
                for i in range(len(parents)):
                    for j in range(i + 1, len(parents)):
                        x, y = parents[i], parents[j]
                        base = dset - {x, y}
                        bx, by, b0 = g.get(base | {x}), g.get(base | {y}), g.get(base)
                        if bx is None or by is None or b0 is None:
                            continue
                        counters["native_quartets"] += 1
                        emit({"e": "quartet", "t": iso_of(tsz) or "1970-01-01T00:00:00Z", "s": gk[0], "x": x, "y": y, "syn": _r(gz - bx[0] - by[0] + b0[0]),
                              "rk": h64(f"Q|{gk}|{sorted(dset)}|{x}|{y}")})


# ------------------------------------------------------------------------------------------------ boards: progress JSONs + published workbooks (2026-10-22)

BOARD_TOL_S = 7200.0
PROGRESS_SETTLED = ("noop_yellows", "yellow_dups", "zero_skipped_filters", "sampled_out_filters", "type_skipped", "tab_level_excluded", "excluded_unwired",
                    "missing_yellows", "inert_filters", "prior_skipped_filters", "coarse_deferred_filters")
NPZ_TAG_RE = re.compile(r"^ts(?P<ts>[0-9.]+)_n(?P<n>[0-9]+)$")
XLSX_SS_RE = re.compile(r"^(?P<ss>[A-Z0-9]+_(?:LONG|SHORT))(?:_(?P<stamp>[0-9]{8}))?_")
ISO_IN_TEXT_RE = re.compile(r"(20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})?)")


def npz_ts_of(x):
    """npz identity -> last-bar epoch (int): 'ts1791332100.0_n39157', a number, or an npz_id / manifest dict; None when unknown."""
    if isinstance(x, dict):
        x = x.get("ts_last") or x.get("npz_last_bar_ts")
    if isinstance(x, str):
        m = NPZ_TAG_RE.match(x.strip())
        x = m.group("ts") if m else x
    v = _f(x)
    return int(v) if (v is not None and v > 1e9) else None


class Coverage:
    """Boards another source already holds: exact (sym_side, npz last-bar ts) pairs, and board times of sym_sides whose ctx
    carries no npz. A board is covered by npz equality; by time only against ctx points without an npz (or, when the board
    itself has no npz, against every ctx point of its sym_side), window [t0 - 2 h, t1 + 2 h]."""

    def __init__(self):
        self.npz = set()
        self.t_only = collections.defaultdict(list)
        self.t_all = collections.defaultdict(list)
        self.c = collections.Counter()

    def add(self, sym, npz=None, t=None, src="ctx", t1=None):
        if cat_side_of(sym) is None:
            self.c[src + "_refused_cat_side"] += 1
            return
        n = npz_ts_of(npz)
        ep = epoch_of(iso_of(t)) if t is not None else None
        ep1 = epoch_of(iso_of(t1)) if t1 is not None else None
        if n is not None:
            self.npz.add((sym, n))
        if ep is not None:
            iv = (ep, max(ep, ep1 if ep1 is not None else ep))
            if n is None:
                self.t_only[sym].append(iv)
            self.t_all[sym].append(iv)
        self.c[src + ("_npz" if n is not None else ("_time" if ep is not None else "_empty"))] += 1

    def covers(self, sym, npz, t0, t1=None):
        n = npz_ts_of(npz)
        if n is not None and (sym, n) in self.npz:
            return "npz"
        if t0 is None:
            return None
        pool = self.t_only.get(sym, ()) if n is not None else self.t_all.get(sym, ())
        lo_, hi_ = t0 - BOARD_TOL_S, max(t0, t1 if t1 is not None else t0) + BOARD_TOL_S
        return "time" if any(a <= hi_ and b >= lo_ for a, b in pool) else None


def load_coverage(globs, ctx_json=None):
    """--covered-by: delta logs (*.jsonl, BOARD_CTX lines -> sym_side + npz_last_bar_ts + ts) and brain spills (spill_*.tsv[.gz],
    board events -> sym_side + board ts, no npz). --covered-by-ctx: JSON list of [sym_side, npz_ts_or_ISO] or
    {sym_side|s, npz|npz_last_bar_ts, ts|t}."""
    cov = Coverage()
    files = sorted({f for g in (globs or []) for f in glob.glob(os.path.expanduser(g))})
    for f in files:
        cov.c["covered_by_files"] += 1
        if f.endswith((".tsv", ".tsv.gz")):
            op = gzip.open if f.endswith(".gz") else open
            with op(f, "rt", errors="replace") as fh:
                for line in fh:
                    if '"e":"board"' not in line:
                        continue
                    try:
                        ev = json.loads(line.partition("\t")[2])
                    except Exception:
                        continue
                    cov.add(ev.get("s"), None, ev.get("t"), "spill")
            continue
        sym0 = sym_side_of_path(f)
        with open(f, "rb") as fh:
            for raw in fh:
                if b"BOARD_CTX" not in raw:
                    continue
                try:
                    r = json.loads(raw)
                except Exception:
                    continue
                if isinstance(r, dict) and r.get("label") == "BOARD_CTX":
                    cov.add(r.get("sym_side") or sym0, r.get("npz_last_bar_ts"), r.get("ts"), "log")
    if ctx_json:
        for it in json.loads(Path(os.path.expanduser(ctx_json)).read_text()):
            if isinstance(it, dict):
                cov.add(it.get("sym_side") or it.get("s"), it.get("npz") or it.get("npz_last_bar_ts"), it.get("ts") or it.get("t"), "ctxjson")
            elif isinstance(it, (list, tuple)) and len(it) >= 2:
                x = it[1]
                cov.add(it[0], x if not isinstance(x, str) or NPZ_TAG_RE.match(x) else None, x if isinstance(x, str) and not NPZ_TAG_RE.match(x) else None, "ctxjson")
    return cov


def _cand_str(v):
    if isinstance(v, bool):
        return "True" if v else "False"
    return "" if v is None else str(v).strip()


def _num(v):
    return None if isinstance(v, bool) else _f(v) if isinstance(v, (int, float)) else None


class BoardEmitter:
    """One board of a board-level source (progress JSON or workbook) -> brain events through the log Assembler (same row /
    cell / identity / synergy encoding as extract_logs). Every board event carries its provenance; row keys are re-hashed
    with src=<source> so they never collide with log-derived keys; workbook row keys are content-addressed (copies of the
    same board under several names dedupe in train)."""

    def __init__(self, src_kind, sym, t0, emit, counters, extra, src_id, content_rk=False):
        self.kind = src_kind
        self.sym = sym
        self.cs = cat_side_of(sym)
        self.t0 = t0
        self.c = counters
        self.extra = extra
        self.src_id = src_id
        self.content_rk = content_rk
        self.out = emit
        self.asm = Assembler(sym, src_id, self._emit, counters)

    def _emit(self, ev):
        e = ev.get("e")
        if e == "board":
            ev.update(self.extra)
            ev["rk"] = h64(f"src={self.kind}|B|{self.src_id}|{ev['b']}")
        elif self.content_rk and e == "row":
            ev["rk"] = h64(f"src={self.kind}|R|{self.sym}|{ev['sh']}|{ev['k']}|{ev['cb']}|{json.dumps(ev['n'])}|{json.dumps(ev['y'])}|{json.dumps(ev.get('ya'))}")
        else:
            ev["rk"] = h64(f"src={self.kind}|{e}|{self.src_id}|{ev.get('rk')}")
        self.out(ev)

    def start(self, bh, tier, rnd):
        self.asm.feed({"label": "BOARD_CTX", "ts": self.t0, "bh": bh, "tier": tier, "round": rnd}, 0)

    def row(self, sheet, row, sw, cand, cb, naked, cells, promoted, identity, valid=True, trades=None):
        """naked: delta vs cb or None (unknown); cells: [(hdr, delta, valid)] already stripped of settled cells."""
        base = {"ts": self.t0, "sheet": sheet, "row": row, "switch": sw, "cand": cand, "cum_before": cb}
        if naked is None and not identity:
            self.asm.close_row(None)
            ev = {"e": "row", "t": self.t0, "s": self.sym, "b": self.asm.board, "sh": sheet, "k": f"{sw}={cand}", "cb": _r(cb), "n": None, "y": [],
                  "ya": [[h, _r(d), 1 if v else 0] for h, d, v in cells], "nu": 1, "ni": 0, "sy": [], "p": bool(promoted), "ps": self.kind,
                  "g": regime_of(self.asm.ctx.get("bh")), "ti": self.asm.ctx.get("tier"), "bh": _r(self.asm.ctx.get("bh"), 3),
                  "rk": h64(f"U|{self.sym}|{sheet}|{row}|{sw}|{cand}|{self.t0}|{cb}")}
            self._emit(ev)
            return
        nd = 0.0 if identity else naked
        self.asm.feed(dict(base, label="naked", delta=nd, valid=bool(valid) if not identity else True, cached=bool(identity), trades=trades), row)
        for hdr, d, v in cells:
            self.asm.feed(dict(base, label=hdr, delta=d, valid=bool(v), cached=False), row)
        self.asm.feed(dict(base, label="ROW_DONE", promoted=bool(promoted)), row)

    def finish(self):
        self.asm.finish()


def count_board_ev(counters, cs, ev):
    e = ev.get("e")
    if e == "row":
        n = ev.get("n")
        counters[f"{cs}:naked_events"] += int(bool(n and n[-1]))
        counters[f"{cs}:naked_settled_identity_with_cells"] += int(bool(n is not None and not n[-1]))
        counters[f"{cs}:cell_events"] += len(ev.get("y") or [])
        counters[f"{cs}:naked_unknown_rows"] += int(bool(ev.get("nu")))
        counters[f"{cs}:naked_unknown_cells_raw"] += len(ev.get("ya") or [])
        counters[f"{cs}:promotions"] += int(bool(ev.get("p")))
    elif e == "id":
        counters[f"{cs}:identity_rows_settled"] += 1


def _board_day_count(counters, cs, t0):
    counters[f"{cs}:board_day:{t0[:10]}"] += 1
    fk, lk = f"{cs}:first_board", f"{cs}:last_board"
    if not counters.get(fk) or t0 < counters[fk]:
        counters[fk] = t0
    if not counters.get(lk) or t0 > counters[lk]:
        counters[lk] = t0


def _progress_names(d):
    out = set()
    for k in ("run_path", "impossible_path"):
        if d.get(k):
            out.add(os.path.basename(str(d[k])))
    note = str(d.get("best_note") or "")
    if note.startswith("activated "):
        out.add(note.split(" ", 1)[1].strip())
    return sorted(out)


def extract_progress(a, emit, counters):
    """*_v14_progress.json -> one board per npz the done rows carry (done order kept). Board time = the npz last bar (cells
    are computed on that NPZ, minutes after it: ts_source progress_npz_last_bar), else result_start_utc, else the file mtime.
    Row: naked = naked_delta, else delta when joint_delta is None, else unknown (cells kept raw, never learned); RUNNING_IDENTITY
    / is_running rows = identity settlement (naked never evidence; their fresh cells are cells vs a naked of 0, exactly as an
    identity row of a delta log); settled cells (noop / dup / zero-skipped / sampled-out / inert / prior / coarse / type /
    excluded / missing) are never evidence. Writes progress_index_<host>.json (every board, used or covered) for the xlsx pass."""
    cov = load_coverage(a.covered_by, a.covered_by_ctx)
    for k, v in cov.c.items():
        counters["coverage_" + k] = v
    files = sorted({f for g in a.source_from for f in glob.glob(os.path.expanduser(g))})
    index = []
    for f in files:
        counters["files"] += 1
        try:
            st = os.stat(f)
            with open(f, "rb") as fh:
                d = json.load(fh)
        except Exception:
            counters["files_unreadable"] += 1
            continue
        if not isinstance(d, dict):
            counters["files_not_dict"] += 1
            continue
        sym = d.get("symside") or os.path.basename(f).replace("_v14_progress.json", "")
        cs = cat_side_of(sym)
        done = d.get("done") or {}
        if cs is None:
            counters["files_refused_cat_side"] += 1
            continue
        if not isinstance(done, dict) or not done:
            counters[f"{cs}:files_without_done"] += 1
            continue
        top_npz = npz_ts_of(d.get("npz_id"))
        groups = collections.OrderedDict()
        for key, rec in done.items():
            if not isinstance(rec, dict):
                counters["done_not_dict"] += 1
                continue
            groups.setdefault(npz_ts_of(rec.get("npz")) or top_npz, []).append((key, rec))
        rs, rd = iso_of(d.get("result_start_utc")), iso_of(d.get("result_done_utc"))
        names = _progress_names(d)
        mt = iso_of(st.st_mtime)
        for npz, rows in groups.items():
            if npz is not None:
                t0, src_ts = iso_of(npz), "progress_npz_last_bar"
            elif rs:
                t0, src_ts = rs, "progress_result_start_utc"
            else:
                t0, src_ts = mt, "progress_mtime"
            t1 = rd if (rd and npz == top_npz and rd > t0) else t0
            ent = {"s": sym, "npz": npz, "t0": t0, "t1": t1, "mtime": mt, "names": names, "file": os.path.abspath(f), "rows": len(rows)}
            how = cov.covers(sym, npz, epoch_of(t0), epoch_of(t1))
            if how:
                counters[f"{cs}:boards_covered_{how}"] += 1
                counters[f"{cs}:rows_in_covered_boards"] += len(rows)
                index.append(dict(ent, status="covered_" + how))
                continue
            counters[f"{cs}:boards_used"] += 1
            counters[f"ts_source_{src_ts}"] += 1
            _board_day_count(counters, cs, t0)
            index.append(dict(ent, status="used"))
            extra = {"src": "progress", "ts_source": src_ts, "npz": npz, "cs": cs, "wd": d.get("window_days"), "file": os.path.basename(f),
                     "set0": d.get("initial_overrides") or None, "sbs": sorted((d.get("sets_by_sha") or {}).keys()) or None,
                     "bl": _r(_f(d.get("baseline_gain"))), "fin": _r(_f(d.get("final_gain") if d.get("final_gain") is not None else d.get("cumulative_gain")))}
            def out(ev, _cs=cs):
                count_board_ev(counters, _cs, ev)
                emit(ev)

            be = BoardEmitter("progress", sym, t0, out, counters, extra, f"{os.path.basename(f)}#{npz}")
            be.start(_f(d.get("bh")), d.get("tier"), d.get("defaults_round"))
            for key, rec in rows:
                _progress_row(be, key, rec, counters, cs)
            be.finish()
    out = Path(a.spill_dir) / f"progress_index_{a.host}.json"
    out.write_text(json.dumps(index, separators=(",", ":")))
    counters["progress_index_boards"] = len(index)


def _progress_row(be, key, rec, counters, cs):
    tab, _, rest = str(key).partition("!")
    rown, _, kc = rest.partition(":")
    sw, eq, cand = kc.partition("=")
    if tab not in SHEET_SET or not sw or not eq:
        counters[f"{cs}:rows_not_main_pass"] += 1
        return
    if sw in DENY:
        counters[f"{cs}:rows_deny"] += 1
        return
    identity = bool(rec.get("is_running")) or str(rec.get("reason") or "").startswith("RUNNING_IDENTITY")
    valid = not bool(rec.get("delta_invalid"))
    nd = _f(rec.get("naked_delta"))
    if nd is None and rec.get("joint_delta") is None:
        nd = _f(rec.get("delta"))
    settled = set()
    for k in PROGRESS_SETTLED:
        v = rec.get(k)
        if isinstance(v, dict):
            settled.update(v.keys())
        elif isinstance(v, (list, tuple, set)):
            settled.update(str(x) for x in v)
    yr = rec.get("yellow_reasons") or {}
    cells = []
    for hdr, v in (rec.get("yellows") or {}).items():
        dv = _num(v)
        if dv is None:
            counters[f"{cs}:cells_reason_not_numeric"] += 1
        elif hdr in settled:
            counters[f"{cs}:cells_settled"] += 1
        elif not YELLOW_RE.match(str(hdr)):
            counters[f"{cs}:cells_bad_header"] += 1
        else:
            cells.append((str(hdr), dv, hdr not in yr))
    counters[f"{cs}:rows"] += 1
    counters[f"{cs}:identity_rows" if identity else (f"{cs}:naked_unknown" if nd is None else f"{cs}:naked_known")] += 1
    row_no = int(rown) if rown.isdigit() else rown
    be.row(tab, row_no, sw, cand, _f(rec.get("cumulative_before")), nd, cells, bool(rec.get("promoted")), identity, valid, rec.get("trades"))


def _xlsx_meta(path):
    """manifest next to the workbook (npz_last_bar_ts, cells_vintage, published_utc, metrics.bh) when present."""
    mp = str(path)[:-5] + "_manifest.json"
    try:
        return json.loads(Path(mp).read_text())
    except Exception:
        return {}


def _xlsx_read(path, counters):
    """openpyxl read_only values -> (baseline metrics dict, [(tab, [row tuples], header map, yellow cols)])"""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        base = {}
        for sn in wb.sheetnames:
            if sn.endswith("_BASELINE_METRICS"):
                for r in wb[sn].iter_rows(max_row=60, values_only=True):
                    if r and isinstance(r[0], str) and len(r) > 1:
                        base[r[0].strip()] = r[1]
                break
        tabs = []
        for sn in SWITCH_SHEETS:
            if sn not in wb.sheetnames:
                counters["xlsx_tab_missing"] += 1
                continue
            it = wb[sn].iter_rows(values_only=True)
            next(it, None)
            hdr = list(next(it, None) or [])
            h = {}
            ycols = []
            for j, v in enumerate(hdr):
                if not isinstance(v, str):
                    continue
                v = v.strip()
                if v == "WHAT SWITCH":
                    break
                h.setdefault(v, j)
                if YELLOW_RE.match(v):
                    ycols.append((j, v))
            if not all(k in h for k in ("Switch", "default", "override", "VECTOR_DELTA")):
                counters["xlsx_tab_without_headers"] += 1
                continue
            tabs.append((sn, list(it), h, ycols))
        return base, tabs
    finally:
        wb.close()


def _xlsx_t0(path, man, base, stamp):
    v = iso_of(man.get("cells_vintage"))
    if v:
        return v, "xlsx_cells_vintage"
    m = ISO_IN_TEXT_RE.search(str(base.get("source") or ""))
    if m and iso_of(m.group(1)):
        return iso_of(m.group(1)), "xlsx_baseline_source_ts"
    if stamp:
        try:
            return datetime.strptime(stamp, "%y%m%d%H").replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "xlsx_name_stamp"
        except ValueError:
            pass
    return iso_of(os.path.getmtime(path)), "xlsx_mtime"


def extract_xlsx(a, emit, counters):
    """Published board workbooks (13 SWITCH_SHEETS, row-2 headers). Per row with a numeric G: identity = G == 0 with HUSTLE (F)
    blank (RUNNING_IDENTITY rows get no F) -> settlement; else naked = G when no yellow cell is > 0 (BIBLE §56.0: G is the
    joint / best positive / naked, so with no positive yellow G IS the naked), else naked UNKNOWN (cells kept raw in `ya`,
    never learned). Cells equal to the row's naked (|cell - naked| <= 1e-9) are noop / zero-skip fills = settled, never
    evidence. promoted = C non-empty AND G > 0. Rows with G blank (skipped / invalid) or only F are not used. Coverage: the
    workbook name is a progress JSON's run_path / activated / impossible name, or (sym_side, manifest npz) matches a progress
    board or a delta-log ctx, or the board time is within 2 h of a progress board window or progress file mtime or a ctx
    point of the sym_side -> covered, counted, skipped. Several copies of one board: first by time is used, later ones whose
    manifest npz repeats are skipped, the rest dedupe by content row keys (in-extract per sym_side and in train)."""
    cov = load_coverage(a.covered_by, a.covered_by_ctx)
    for k, v in cov.c.items():
        counters["coverage_" + k] = v
    pidx = []
    for p in (a.progress_index or []):
        try:
            pidx.extend(json.loads(Path(os.path.expanduser(p)).read_text()))
        except Exception:
            counters["progress_index_unreadable"] += 1
    pnames = {n for e in pidx for n in (e.get("names") or [])}
    pcov = Coverage()
    for e in pidx:
        pcov.add(e.get("s"), e.get("npz"), e.get("t0"), "progress", e.get("t1"))
    files = sorted({f for g in a.source_from for f in glob.glob(os.path.expanduser(g)) if f.endswith(".xlsx") and not os.path.basename(f).startswith("~$")})
    by_sym = collections.defaultdict(list)
    for f in files:
        m = XLSX_SS_RE.match(os.path.basename(f))
        if not m or cat_side_of(m.group("ss")) is None:
            counters["files_refused_name"] += 1
            continue
        by_sym[m.group("ss")].append((f, m.group("stamp")))
    t_start = time.time()
    nfile = 0
    for sym in sorted(by_sym):
        cs = cat_side_of(sym)
        seen_npz = set()
        seen_rk = set()
        cand_boards = []
        for f, stamp in by_sym[sym]:
            man = _xlsx_meta(f)
            cand_boards.append((iso_of(man.get("cells_vintage")) or iso_of(os.path.getmtime(f)) or "", f, stamp, man))
        cand_boards.sort()
        for _k, f, stamp, man in cand_boards:
            nfile += 1
            if nfile % 200 == 0:
                print(f"[xlsx] {nfile:,}/{len(files):,} files {time.time() - t_start:.0f}s", flush=True)
            counters["files"] += 1
            bn = os.path.basename(f)
            if bn in pnames:
                counters[f"{cs}:boards_covered_progress_name"] += 1
                continue
            try:
                base, tabs = _xlsx_read(f, counters)
            except Exception as e:
                counters["files_unreadable"] += 1
                print(f"[xlsx] unreadable {bn}: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            t0, src_ts = _xlsx_t0(f, man, base, stamp)
            npz = npz_ts_of(man.get("npz_last_bar_ts")) or npz_ts_of((man.get("provenance") or {}).get("npz_window"))
            ep = epoch_of(t0)
            mt_ep = os.path.getmtime(f)
            how = pcov.covers(sym, npz, ep)
            how = ("progress_" + how) if how else None
            if not how:
                h2 = cov.covers(sym, npz, ep)
                how = ("log_" + h2) if h2 else None
            if how:
                counters[f"{cs}:boards_covered_{how}"] += 1
                continue
            if npz is not None and npz in seen_npz:
                counters[f"{cs}:boards_dup_copy_npz"] += 1
                continue
            if npz is not None:
                seen_npz.add(npz)
            bh = _f(base.get("bh_pct"))
            if bh is None:
                bh = _f((man.get("metrics") or {}).get("bh"))
            extra = {"src": "xlsx", "ts_source": src_ts, "npz": npz, "cs": cs, "wd": 30 if "_30d_" in bn else None, "file": bn,
                     "bl": _r(_f(base.get("gain_pct"))), "fin": _r(_f((man.get("metrics") or {}).get("gain_pct"))), "mtime": iso_of(mt_ep)}
            buf = []
            bc = collections.Counter()
            be = BoardEmitter("xlsx", sym, t0, buf.append, bc, extra, bn, content_rk=True)
            be.start(bh, None, None)
            for sn, rows, h, ycols in tabs:
                _xlsx_tab(be, sn, rows, h, ycols, bc, cs)
            be.finish()
            fresh = [ev for ev in buf if ev.get("e") == "row" and ev["rk"] not in seen_rk]
            if not fresh:
                counters[f"{cs}:boards_all_rows_dup_or_empty"] += 1
                counters[f"{cs}:rows_in_dup_or_empty_boards"] += sum(1 for ev in buf if ev.get("e") in ("row", "id"))
                continue
            if len(fresh) < sum(1 for ev in buf if ev.get("e") == "row"):
                counters[f"{cs}:boards_partly_dup"] += 1
            counters.update(bc)
            for ev in buf:
                if ev.get("e") == "row":
                    if ev["rk"] in seen_rk:
                        counters[f"{cs}:rows_dup_copy"] += 1
                        continue
                    seen_rk.add(ev["rk"])
                count_board_ev(counters, cs, ev)
                emit(ev)
            counters[f"{cs}:boards_used"] += 1
            counters[f"ts_source_{src_ts}"] += 1
            _board_day_count(counters, cs, t0)


def _xlsx_tab(be, sn, rows, h, ycols, counters, cs):
    jA, jB, jC, jG = h["Switch"], h["default"], h["override"], h["VECTOR_DELTA"]
    jE, jF, jD = h.get("BASELINE"), h.get("HUSTLE_DELTA"), h.get("Family")
    for i, r in enumerate(rows, start=3):
        if not r or jA >= len(r) or not isinstance(r[jA], str) or not r[jA].strip():
            continue
        sw = r[jA].strip()
        if jD is not None and jD < len(r) and str(r[jD] or "").strip().upper() == "GENERAL":
            counters[f"{cs}:xlsx_rows_general"] += 1
            continue
        if sw in DENY:
            counters[f"{cs}:rows_deny"] += 1
            continue
        g = _num(r[jG]) if jG < len(r) else None
        fv = _num(r[jF]) if (jF is not None and jF < len(r)) else None
        if g is None:
            counters[f"{cs}:xlsx_rows_f_only" if fv is not None else f"{cs}:xlsx_rows_unevaluated"] += 1
            continue
        cand = _cand_str(r[jB] if jB < len(r) else None)
        cb = _num(r[jE]) if (jE is not None and jE < len(r)) else None
        promoted = bool(str(r[jC] if jC < len(r) and r[jC] is not None else "").strip()) and g > EPS
        ys = [(hdr, _num(r[j])) for j, hdr in ycols if j < len(r)]
        ys = [(hdr, v) for hdr, v in ys if v is not None]
        identity = abs(g) <= EPS and fv is None
        pos = any(v > EPS for _h, v in ys)
        counters[f"{cs}:rows"] += 1
        if identity:
            nd = 0.0
            counters[f"{cs}:identity_rows"] += 1
        elif not pos:
            nd = g
            counters[f"{cs}:naked_known"] += 1
        else:
            nd = None
            counters[f"{cs}:naked_unknown"] += 1
        cells = []
        for hdr, v in ys:
            if nd is not None and abs(v - nd) <= EPS:
                counters[f"{cs}:cells_settled"] += 1
            else:
                cells.append((hdr, v, True))
        be.row(sn, i, sw, cand, cb, nd, cells, promoted, identity)


def cmd_extract(a):
    a.source_from = a.source_from or []
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"_{os.getpid()}_{int(time.time() * 1e6) % 1000000:06d}"
    sp = Path(a.spill_dir) / f"spill_{a.source}_{a.host}_{stamp}.tsv.gz"
    spill = Spill(sp)
    counters = collections.Counter()
    t0 = time.time()
    fn = {"logs": extract_logs, "rebuilt": extract_rebuilt, "history": extract_history, "ledgers": extract_ledgers, "native": extract_native,
          "progress": extract_progress, "xlsx": extract_xlsx}[a.source]
    fn(a, spill, counters)
    spill.close()
    rep = {"source": a.source, "from": a.source_from, "host": a.host, "spill": str(sp), "at": utc_now(), "secs": round(time.time() - t0, 1),
           "counters": dict(counters), "events": dict(spill.n)}
    (Path(a.spill_dir) / f"extract_{a.source}_{a.host}_{stamp}.json").write_text(json.dumps(rep, indent=1, sort_keys=True))
    print(json.dumps(rep, indent=1, sort_keys=True))
    return 0


# ------------------------------------------------------------------------------------------------ graph

COLS = ("n", "inv", "pos", "neg", "wn", "wpos", "sum_pos", "sum_neg", "secs", "s_tim", "c_tim", "s_tr", "c_tr", "s_dd", "c_dd", "s_sh", "c_sh",
        "s_wr", "c_wr", "n_ident", "fwd_n", "fwd_pos", "live_n", "live_pos", "live_pnl", "n_rescue", "n_kill", "n_syn", "syn_sum", "syn_pos",
        "syn_neg", "last", "abl_n", "abl_kill", "abl_dd", "ph_n", "ph_pos", "n_skip", "n_sym", "n_sym_pos")
C = {c: i for i, c in enumerate(COLS)}
NC = len(COLS)


class Graph:
    """Edges of ONE cat_side. key = (scope_id, a_id, b_id); scope 0 = the cat_side, else a sym_side id. numpy columns COLS."""

    def __init__(self, cat_side):
        self.cs = cat_side
        self.nodes, self.nid = [], {}
        self.scopes, self.sid = ["CS"], {"CS": 0}
        self.key = {}
        self.cap = 4096
        self.E = np.zeros((self.cap, NC), dtype=np.float64)
        self.m = 0
        self.sym_seen = set()
        self.sym_pos = set()

    def node(self, s):
        i = self.nid.get(s)
        if i is None:
            i = self.nid[s] = len(self.nodes)
            self.nodes.append(s)
        return i

    def scope(self, s):
        i = self.sid.get(s)
        if i is None:
            i = self.sid[s] = len(self.scopes)
            self.scopes.append(s)
        return i

    def idx(self, scope_id, a, b=""):
        k = (scope_id, self.node(a), self.node(b) if b else -1)
        i = self.key.get(k)
        if i is None:
            if self.m >= self.cap:
                self.cap *= 2
                E2 = np.zeros((self.cap, NC), dtype=np.float64)
                E2[: self.m] = self.E[: self.m]
                self.E = E2
            i = self.key[k] = self.m
            self.m += 1
        return i

    def get(self, scope_id, a, b=""):
        na = self.nid.get(a)
        if na is None:
            return None
        nb = -1
        if b:
            nb = self.nid.get(b)
            if nb is None:
                return None
        return self.key.get((scope_id, na, nb))

    def pw(self, scope_id, a, b=""):
        """(weighted positives, weighted valid mass) of an edge; (0, 0) when absent."""
        i = self.get(scope_id, a, b)
        if i is None:
            return 0.0, 0.0
        row = self.E[i]
        return float(row[5]), float(row[4])

    def col2(self, scope_id, a, c_pos, c_n, b=""):
        i = self.get(scope_id, a, b)
        if i is None:
            return 0.0, 0.0
        return float(self.E[i, C[c_pos]]), float(self.E[i, C[c_n]])

    def add_eval(self, i, d, valid, secs, mets, w, ts_ep, sym_id=None):
        E = self.E[i]
        E[0] += 1
        E[8] += secs or 0.0
        if ts_ep and ts_ep > E[31]:
            E[31] = ts_ep
        if not valid:
            E[1] += 1
            E[4] += w
            return
        if d is None:
            return
        E[4] += w
        if d > EPS:
            E[2] += 1
            E[5] += w
            E[6] += d
            if sym_id is not None:
                k = i * 1048576 + sym_id
                if k not in self.sym_pos:
                    self.sym_pos.add(k)
                    E[C["n_sym_pos"]] += 1
        elif d < -EPS:
            E[3] += 1
            E[7] += d
        if sym_id is not None:
            k = i * 1048576 + sym_id
            if k not in self.sym_seen:
                self.sym_seen.add(k)
                E[C["n_sym"]] += 1
        for j, v in enumerate(mets):
            if v is not None:
                E[9 + 2 * j] += v
                E[10 + 2 * j] += 1



# ------------------------------------------------------------------------------------------------ features + heads

class Tables:
    """The lookups features need, over a live Graph (training) or exported JSON tables (runtime). Every method returns
    (positive mass, total mass) with (0, 0) when there is no evidence."""

    def __init__(self, graph=None, blob=None, sym_side=None):
        self.g = graph
        if blob is not None:
            nodes = blob["nodes"]
            self.cs_t = {nodes[r[0]]: (r[1], r[2]) for r in blob.get("cs", [])}
            self.fwd_t = {nodes[r[0]]: (r[15], r[14]) for r in blob.get("cs", []) if len(r) > 15}
            self.live_t = {nodes[r[0]]: (r[17], r[16]) for r in blob.get("cs", []) if len(r) > 17}
            self.reg_t = {(nodes[r[0]], r[1]): (r[2], r[3]) for r in blob.get("regime", [])}
            self.pair_t = {(nodes[r[0]], nodes[r[1]]): (r[2], r[3]) for r in blob.get("pair", [])}
            self.sym_t = {}
            if sym_side is not None:
                self.sym_t = {nodes[r[0]]: [r[1], r[2]] for r in (blob.get("sym") or {}).get(sym_side, [])}

    def cs(self, a):
        if self.g is not None:
            return self.g.pw(0, a)
        return self.cs_t.get(a, (0.0, 0.0))

    def sym(self, sym_side, a):
        if self.g is not None:
            sid = self.g.sid.get(sym_side)
            return self.g.pw(sid, a) if sid is not None else (0.0, 0.0)
        return tuple(self.sym_t.get(a, (0.0, 0.0)))

    def reg(self, a, g):
        if self.g is not None:
            return self.g.pw(0, a, "G:" + g)
        return self.reg_t.get((a, g), (0.0, 0.0))

    def pair(self, f, r):
        if self.g is not None:
            return self.g.pw(0, f, r)
        return self.pair_t.get((f, r), (0.0, 0.0))

    def fwd(self, a):
        if self.g is not None:
            return self.g.col2(0, a, "fwd_pos", "fwd_n")
        return self.fwd_t.get(a, (0.0, 0.0))

    def live(self, a):
        if self.g is not None:
            return self.g.col2(0, a, "live_pos", "live_n")
        return self.live_t.get(a, (0.0, 0.0))


def _shr(pw, k):
    p, n = pw
    return lo(p, n) * n / (n + k), n


def node_names(kind, sheet, row_key, hdr, buckets):
    sw, _, cand = row_key.partition("=")
    if kind == "R":
        return "R:" + row_key, "N:" + sw, "V:" + buckets.of(sheet, sw, cand)
    fk, _, fv = str(hdr).strip().partition("=")
    return "F:" + str(hdr).strip(), "NF:" + fk, "VF:" + buckets.of(None, fk, fv)


def featurize(T, buckets, kind, sym_side, sheet, row_key, hdr, naked_d, regime, tier, bh, ema_b, ema_t):
    """One feature vector (len NF). kind R = naked row, F = cell given its naked, F0 = cell before its naked."""
    a, nm, st = node_names("R" if kind == "R" else "F", sheet, row_key, hdr, buckets)
    x = [0.0] * NF
    x[0] = 1.0
    x[1], cn = _shr(T.cs(a), 3.0)
    sp, sn = T.sym(sym_side, a)
    x[2] = (lo(sp, sn) - x[1]) * sn / (sn + 2.0)
    x[3], _ = _shr(T.cs(nm), 3.0)
    x[4], _ = _shr(T.cs(st), 3.0)
    rp, rn = T.reg(a, regime)
    x[5] = (lo(rp, rn) - x[1]) * rn / (rn + 3.0)
    x[6] = math.log1p(cn)
    x[7] = 1.0 if cn < 3 else 0.0
    x[8] = logit(ema_b)
    x[9] = logit(ema_t)
    x[10], _ = _shr(T.fwd(a), 2.0)
    x[11], _ = _shr(T.live(a), 2.0)
    x[12] = tier
    x[13] = bh
    if kind != "R":
        x[14], _ = _shr(T.pair(a, "R:" + row_key), 2.0)
        x[15], _ = _shr(T.cs("R:" + row_key), 3.0)
        if kind == "F" and naked_d is not None:
            x[17] = 1.0 if naked_d > EPS else (-1.0 if naked_d < -EPS else 0.0)
            x[18] = max(-3.0, min(3.0, naked_d))
    x[16], _ = _shr(T.cs("T:" + str(sheet)), 3.0)
    x[19] = tier * x[1]
    return x, cn


class Heads:
    """Online logistic heads of ONE cat_side (SGD, predict-then-learn)."""

    NAMES = ("R", "F", "F0", "FWD") + tuple(f"{m}:{k}" for m in METRIC_HEADS for k in ("R", "F0"))

    def __init__(self, lr=0.03, l2=1e-5):
        self.w = {h: [(-4.0 if i == 0 else 0.0) for i in range(NF)] for h in self.NAMES}
        self.w["FWD"][0] = 0.0
        self.lr, self.l2 = lr, l2
        self.n = collections.Counter()

    def p(self, h, x):
        w = self.w[h]
        return sig(sum(wi * xi for wi, xi in zip(w, x)))

    def learn(self, h, x, y, p=None, sw=1.0):
        if p is None:
            p = self.p(h, x)
        g = (y - p) * sw
        w = self.w[h]
        lr, l2 = self.lr, self.l2
        for i in range(NF):
            xi = x[i]
            if xi != 0.0 or w[i] != 0.0:
                w[i] += lr * (g * xi - l2 * w[i])
        self.n[h] += 1
        return p


# ------------------------------------------------------------------------------------------------ training

class CatBrain:
    """Graph + heads + prequential capture of ONE cat_side."""

    def __init__(self, cs, buckets, cut):
        self.cs = cs
        self.g = Graph(cs)
        self.T = Tables(graph=self.g)
        self.h = Heads()
        self.bk = buckets
        self.cut = cut
        self.ema = collections.defaultdict(lambda: 0.02)
        self.c = collections.Counter()
        self.cap = {k: array.array("d") for k in ("R", "F", "F0R")}
        self.cap_cur = {k: array.array("b") for k in ("R", "F", "F0R")}
        self.cap_ev = {k: array.array("b") for k in ("R", "F", "F0R")}
        self.fwd_eval = []
        self.board_of = {}
        self.cur_promos = collections.defaultdict(dict)
        self.pending = {}
        self.inforce = {}
        self.ledger = collections.defaultdict(lambda: [0, 0, 0.0, 0.0])
        self.ledger_sets = {}
        self.ledger_key = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0.0]))
        self.live_fam = collections.defaultdict(lambda: [0, 0, 0.0, 0.0])
        self.remedy = collections.defaultdict(lambda: [0, 0, 0.0])
        self.quart = collections.defaultdict(lambda: [0, 0, 0, 0.0])
        self.sheet_of = {}
        self.first_ts = self.last_ts = ""

    # -- board / forward
    def _board(self, ev):
        sym = ev["s"]
        old = self.board_of.get(sym)
        if old == ev["b"] or (old is not None and str(ev["b"]).rsplit("|", 1)[-1] < str(old).rsplit("|", 1)[-1]):
            return
        self._resolve(sym)
        self.pending[sym] = {sw: dict(v, alt=[], own=[]) for sw, v in self.cur_promos.pop(sym, {}).items()}
        if self.pending[sym]:
            self.inforce[sym] = {sw: v["cand"] for sw, v in self.pending[sym].items()}
        self.board_of[sym] = ev["b"]
        self.c["boards"] += 1

    def _resolve(self, sym):
        pend = self.pending.pop(sym, None)
        if not pend:
            return
        sid = self.g.scope(sym)
        for sw, v in pend.items():
            if not v["alt"] and not v["own"]:
                self.c["fwd_unobserved"] += 1
                continue
            y = 1.0 if (max(v["alt"], default=-1e9) <= EPS and min(v["own"], default=0.0) >= -EPS) else 0.0
            a = "R:" + f"{sw}={v['cand']}"
            for s in (0, sid):
                i = self.g.idx(s, a)
                self.g.E[i, C["fwd_n"]] += 1
                self.g.E[i, C["fwd_pos"]] += y
            p_now = self.h.p("FWD", v["x"])
            self.fwd_eval.append((v["ts"], v["p_fwd"], v["p_fit"], y, a, v["ts"] >= self.cut))
            self.h.learn("FWD", v["x"], y, p_now, W_FWD)
            self.c["fwd_labels"] += 1
            self.c["fwd_pos"] += int(y)

    def finish(self):
        for sym in list(self.pending):
            self._resolve(sym)
        for sym, promos in list(self.cur_promos.items()):
            if promos:
                self.c["fwd_promos_without_next_board"] += len(promos)

    # -- events
    def handle(self, ev, ts):
        e = ev.get("e")
        if not self.first_ts and not ts.startswith("0000"):
            self.first_ts = ts
        self.last_ts = ts
        if e == "row":
            self._row(ev, ts)
        elif e == "id":
            if ev.get("ni"):
                edge_index = self.g.idx(0, "R:" + ev["k"])
                self.g.E[edge_index, C["n_ident"]] += 1
                self.c["identity_row_visits"] += 1
                self.c["identity_lines"] += ev["ni"]
            self.c["rows"] += 1
        elif e == "board":
            self._board(ev)
        elif e == "phase":
            self._phase(ev)
        elif e == "abl":
            self._abl(ev)
        elif e == "remedy":
            r = self.remedy[ev.get("cls")]
            r[0] += 1
            r[1] += int((ev.get("d") or 0) > EPS and ev.get("v"))
            r[2] += ev.get("d") or 0.0
        elif e == "live":
            self._live(ev)
        elif e == "ledgerset":
            self._ledgerset(ev)
        elif e == "ledger":
            self._ledger(ev)
        elif e == "quartet":
            q = self.quart[tuple(sorted((ev["x"], ev["y"])))]
            q[0] += 1
            s = ev.get("syn") or 0.0
            q[1] += int(s > EPS)
            q[2] += int(s < -EPS)
            q[3] += s
            self.c["quartets"] += 1
        elif e in ("forward", "holdout"):
            self._forward_line(ev)
        elif e == "settle":
            if ev.get("k"):
                i = self.g.idx(0, "R:" + str(ev["k"]) if not ev.get("f") else "F:" + str(ev["f"]))
                self.g.E[i, C["n_skip" if ev.get("code") == "BRAIN_SKIP" else "n_ident"]] += 1
            self.c["settle_" + str(ev.get("code"))] += 1
        self.c["ev_" + str(e)] += 1

    def _forward_line(self, ev):
        if ev["e"] == "holdout":
            self.c["holdout_lines"] += 1
            return
        key = ev.get("key")
        d = _f(ev.get("d_gain"))
        if not key or d is None:
            self.c["forward_unusable"] += 1
            return
        k = str(key)
        a = k if k.startswith(("R:", "F:")) else ("R:" + k if "=" in k else "N:" + k)
        sid = self.g.scope(ev["s"])
        y = 1.0 if d > EPS else 0.0
        for s in (0, sid):
            i = self.g.idx(s, a)
            self.g.E[i, C["fwd_n"]] += 1
            self.g.E[i, C["fwd_pos"]] += y
        self.c["forward_lines"] += 1

    def _phase(self, ev):
        a = "R:" + ev["k"]
        i = self.g.idx(0, a)
        self.g.E[i, C["ph_n"]] += 1
        if ev.get("v") and (ev.get("d") or 0) > EPS:
            self.g.E[i, C["ph_pos"]] += 1
        j = self.g.idx(0, "PH:" + str(ev.get("ph")))
        self.g.add_eval(j, ev.get("d"), ev.get("v"), ev.get("sec"), ev.get("m") or [], ev.get("w") or 1.0, epoch_of(ev["t"]))
        self.c["phase_evals"] += 1

    def _abl(self, ev):
        i = self.g.idx(0, "NF:" + str(ev["f"]), "R:" + str(ev["k"]))
        self.g.E[i, C["abl_n"]] += 1
        self.g.E[i, C["abl_kill"]] += int(bool(ev.get("kill")))
        self.g.E[i, C["abl_dd"]] += ev.get("dd") or 0.0
        sw = str(ev["k"]).split("=", 1)[0]
        j = self.g.idx(0, "N:" + sw)
        self.g.E[j, C["abl_n"]] += 1
        self.g.E[j, C["abl_kill"]] += int(bool(ev.get("kill")))
        self.c["abl_attr"] += 1

    def _live(self, ev):
        fam = ev.get("fam") or "UNKNOWN"
        pnl = ev.get("pnl") or 0.0
        col = ev.get("col") or "?"
        for key in (f"X:{fam}", f"XC:{col}:{fam}"):
            lf = self.live_fam[key]
            lf[0] += 1
            lf[1] += int(pnl > 0)
            lf[2] += pnl
            lf[3] += min(0.0, pnl)
        for a in ("X:" + fam, "E:" + str(ev.get("efam") or "UNKNOWN")):
            i = self.g.idx(0, a)
            self.g.E[i, C["live_n"]] += 1
            self.g.E[i, C["live_pos"]] += int(pnl > 0)
            self.g.E[i, C["live_pnl"]] += pnl
        sym = ev["s"]
        keys = self.inforce.get(sym) or {}
        sid = self.g.scope(sym)
        for sw, cand in keys.items():
            a = f"R:{sw}={cand}"
            for s in (0, sid):
                i = self.g.idx(s, a)
                self.g.E[i, C["live_n"]] += 1
                self.g.E[i, C["live_pos"]] += int(pnl > 0)
                self.g.E[i, C["live_pnl"]] += pnl
        self.c["live_lifecycles"] += 1
        self.c["live_attributed_keys"] += len(keys)

    def _ledgerset(self, ev):
        self.ledger_sets[ev["s"]] = ev.get("set") or []

    def _ledger(self, ev):
        fam = ev.get("fam") or "UNKNOWN"
        pnl = ev.get("pnl") or 0.0
        lf = self.ledger[fam]
        lf[0] += 1
        lf[1] += int(pnl > 0)
        lf[2] += pnl
        lf[3] += min(0.0, pnl)
        for k in self.ledger_sets.get(ev["s"]) or []:
            t = self.ledger_key[k][fam]
            t[0] += 1
            t[1] += pnl
        self.c["ledger_trades"] += 1

    def _row(self, ev, ts):
        sym = ev["s"]
        cur_b = self.board_of.get(sym)
        foreign = False
        if cur_b != ev.get("b"):
            if cur_b is None or str(ev.get("b")).rsplit("|", 1)[-1] > str(cur_b).rsplit("|", 1)[-1]:
                self._board({"s": sym, "b": ev.get("b")})
            else:
                foreign = True
                self.c["rows_of_superseded_board"] += 1
        sheet, row_key = ev["sh"], ev["k"]
        sw, _, cand = row_key.partition("=")
        self.sheet_of.setdefault(sw, sheet)
        regime = ev.get("g") or "UNK"
        tc, bc = tier_code(ev.get("ti")), bh_code(ev.get("bh"))
        sid = self.g.scope(sym)
        test = ts >= self.cut
        ep = epoch_of(ts)
        naked = ev.get("n")
        nd = naked[0] if (naked and naked[1] and naked[0] is not None) else None
        eb, et = self.ema[sym], self.ema[(sym, sheet)]
        T, H, g = self.T, self.h, self.g
        a = "R:" + row_key
        x_r = None
        p_fit = None
        if naked is not None and naked[-1]:
            x_r, cn = featurize(T, self.bk, "R", sym, sheet, row_key, None, None, regime, tc, bc, eb, et)
            p = H.p("R", x_r)
            p_fit = p
            d = naked[0] if naked[1] else None
            y = 1.0 if (d is not None and d > EPS) else 0.0
            if test:
                self._record("R", p, naked[2], y, d if y else 0.0, cn, a, None)
            H.learn("R", x_r, y, p, W_FIT * naked[8])
            for m in METRIC_HEADS:
                v = naked[3 + METRIC_IDX[m]]
                if v is not None and naked[1]:
                    H.learn(f"{m}:R", x_r, 1.0 if METRIC_SIGN[m] * v > EPS else 0.0, None, W_FIT * naked[8])
            self.c["evidence_naked"] += 1
        cells = ev.get("y") or []
        for c in cells:
            hdr, md, valid, secs = c[0], c[1], c[2], c[3]
            mm, w, dabs = c[4:9], c[9] or 1.0, c[10]
            x_f, cn = featurize(T, self.bk, "F", sym, sheet, row_key, hdr, nd, regime, tc, bc, eb, et)
            x0 = x_f[:17] + [0.0, 0.0] + x_f[19:]
            pf = H.p("F", x_f)
            p0 = H.p("F0", x0)
            y = 1.0 if (valid and md is not None and md > EPS) else 0.0
            if test:
                f_node = "F:" + str(hdr).strip()
                self._record("F", pf, secs, y, md if y else 0.0, cn, f_node, a)
                self._record("F0R", 1.0 if (nd is not None and nd > EPS) else p0, secs, y, md if y else 0.0, cn, f_node, a)
            H.learn("F", x_f, y, pf, W_FIT * w)
            H.learn("F0", x0, y, p0, W_FIT * w)
            if valid:
                for m in METRIC_HEADS:
                    v = mm[METRIC_IDX[m]]
                    if v is not None:
                        H.learn(f"{m}:F0", x0, 1.0 if METRIC_SIGN[m] * v > EPS else 0.0, None, W_FIT * w)
            self.c["evidence_cells"] += 1
        # graph after the predictions (prequential)
        if naked is not None:
            nmet = naked[3:8]
            if naked[-1]:
                r_nm, r_st = "N:" + sw, "V:" + self.bk.of(sheet, sw, cand)
                for s in (0, sid):
                    g.add_eval(g.idx(s, a), naked[0], naked[1], naked[2], nmet, naked[8], ep, sid if s == 0 else None)
                for nn in (r_nm, r_st, "T:" + sheet):
                    g.add_eval(g.idx(0, nn), naked[0], naked[1], naked[2], nmet, naked[8], ep)
                g.add_eval(g.idx(0, a, "G:" + regime), naked[0], naked[1], naked[2], nmet, naked[8], ep)
            if ev.get("ni"):
                edge_index = g.idx(0, a)
                g.E[edge_index, C["n_ident"]] += 1
                self.c["identity_row_visits"] += 1
                self.c["identity_lines"] += ev["ni"]
        for c in cells:
            hdr, md, valid, secs = c[0], c[1], c[2], c[3]
            mm, w, dabs = c[4:9], c[9] or 1.0, c[10]
            f = "F:" + str(hdr).strip()
            fk, _, fv = str(hdr).strip().partition("=")
            for s in (0, sid):
                g.add_eval(g.idx(s, f), md, valid, secs, mm, w, ep, sid if s == 0 else None)
            g.add_eval(g.idx(0, "NF:" + fk), md, valid, secs, mm, w, ep)
            g.add_eval(g.idx(0, "VF:" + self.bk.of(None, fk, fv)), md, valid, secs, mm, w, ep)
            ip = g.idx(0, f, a)
            g.add_eval(ip, md, valid, secs, mm, w, ep, sid)
            if valid and nd is not None:
                if nd <= EPS and dabs is not None and dabs > EPS:
                    g.E[ip, C["n_rescue"]] += 1
                elif nd > EPS and md is not None and md < -EPS:
                    g.E[ip, C["n_kill"]] += 1
        for hdr, syn in ev.get("sy") or []:
            if syn is None:
                continue
            ip = g.idx(0, "F:" + str(hdr).strip(), a)
            g.E[ip, C["n_syn"]] += 1
            g.E[ip, C["syn_sum"]] += syn
            g.E[ip, C["syn_pos"]] += int(syn > EPS)
            g.E[ip, C["syn_neg"]] += int(syn < -EPS)
            self.c["synergy_quartets"] += 1
        if naked is not None and naked[-1]:
            yb = 1.0 if (nd is not None and nd > EPS) else 0.0
            for k in (sym, (sym, sheet)):
                self.ema[k] = min(0.98, max(0.002, 0.98 * self.ema[k] + 0.02 * yb))
        # forward bookkeeping
        pend = None if foreign else (self.pending.get(sym) or {}).get(sw)
        if pend is not None and naked is not None and naked[-1] and naked[1] and naked[0] is not None:
            (pend["own"] if cand == pend["cand"] else pend["alt"]).append(naked[0])
        if ev.get("p") and not foreign:
            if x_r is None:
                x_r, _cn = featurize(T, self.bk, "R", sym, sheet, row_key, None, None, regime, tc, bc, eb, et)
                p_fit = H.p("R", x_r)
            self.cur_promos[sym][sw] = {"cand": cand, "x": x_r, "p_fwd": H.p("FWD", x_r), "p_fit": p_fit, "ts": ts}
            self.c["promotions_" + str(ev.get("ps"))] += 1
        self.c["rows"] += 1

    def _record(self, kind, p, secs, y, dpos, n_ev, node, row_node):
        self.cap[kind].extend((p, secs or 0.0, y, dpos or 0.0))
        self.cap_ev[kind].append(1 if n_ev >= 3 else 0)
        cur = 0
        i = self.g.get(0, node)
        if i is not None:
            n_sym, pos_sym = self.g.E[i, C["n_sym"]], self.g.E[i, C["n_sym_pos"]]
            cur = 1 if (n_sym >= 10 and pos_sym <= max(1, int(0.05 * n_sym))) else 0
        self.cap_cur[kind].append(cur)

    # -- prequential summaries
    def capture(self, floor=DEFAULT_FLOOR, prefs=P_REFS_REPORT):
        out = {}
        fl = floor_frac(floor)
        for kind in ("R", "F", "F0R"):
            A = self.cap[kind]
            n = len(A) // 4
            if not n:
                continue
            P = np.frombuffer(A, dtype=np.float64).reshape(n, 4) if np is not None else None
            ev = np.frombuffer(self.cap_ev[kind], dtype=np.int8).astype(bool)
            cur = np.frombuffer(self.cap_cur[kind], dtype=np.int8).astype(bool)
            p, sec, y, dp = P[:, 0], P[:, 1], P[:, 2], P[:, 3]
            S, Y, D = float(sec.sum()) or 1.0, float(y.sum()) or 1.0, float(dp.sum()) or 1.0
            curve = []
            for pr in prefs:
                w = np.where(p >= 1.0, 1.0, np.clip(p / pr, fl, 1.0))
                w = np.where(ev, w, 1.0)
                curve.append({"p_ref": pr, "secs_kept": round(float((w * sec).sum()) / S, 4), "evals_kept": round(float(w.sum()) / n, 4),
                              "pos_kept": round(float((w * y).sum()) / Y, 4), "pos_delta_kept": round(float((w * dp).sum()) / D, 4)})
            keep = ~cur
            out[kind] = {"n": int(n), "pos": int(y.sum()), "eval_secs": round(S, 1), "evidenced_share": round(float(ev.mean()), 4), "brain_curve": curve,
                         "current_rule_proxy": {"secs_kept": round(float(sec[keep].sum()) / S, 4), "pos_kept": round(float(y[keep].sum()) / Y, 4),
                                                "pos_delta_kept": round(float(dp[keep].sum()) / D, 4)}}
        return out

    def forward_report(self):
        H = [r for r in self.fwd_eval if r[5]]
        out = {"labels_total": len(self.fwd_eval), "labels_holdout": len(H), "pos_total": int(sum(r[3] for r in self.fwd_eval)),
               "pos_holdout": int(sum(r[3] for r in H))}
        if len(H) >= 20 and 0 < sum(r[3] for r in H) < len(H):
            out["auc_fwd_head"] = round(auc([r[1] for r in H], [r[3] for r in H]), 4)
            out["auc_fit_head_R"] = round(auc([r[2] if r[2] is not None else 0.0 for r in H], [r[3] for r in H]), 4)
            byk = collections.defaultdict(list)
            for r in H:
                byk[r[4]].append(r)
            ks = [(sum(r[1] for r in v) / len(v), sum(r[3] for r in v) / len(v), sum((r[2] or 0) for r in v) / len(v)) for v in byk.values() if len(v) >= 3]
            out["keys_with_ge3_holdout_labels"] = len(ks)
            if len(ks) >= 5:
                out["spearman_fwd_head_vs_persist_rate"] = round(spearman([k[0] for k in ks], [k[1] for k in ks]), 4)
                out["spearman_fit_head_vs_persist_rate"] = round(spearman([k[2] for k in ks], [k[1] for k in ks]), 4)
        else:
            out["note"] = "too few holdout forward labels (need >= 20 with both classes) for AUC"
        return out


def auc(scores, labels):
    pairs = sorted(zip(scores, labels))
    n_pos = sum(1 for _, y in pairs if y > 0.5)
    n_neg = len(pairs) - n_pos
    if not n_pos or not n_neg:
        return float("nan")
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        rank_sum += avg * sum(1 for k in range(i, j + 1) if pairs[k][1] > 0.5)
        i = j + 1
    return (rank_sum - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def _ranks(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2.0
        i = j + 1
    return r


def spearman(a, b):
    ra, rb = _ranks(a), _ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((y - mb) ** 2 for y in rb))
    return cov / (va * vb) if va and vb else float("nan")


def load_buckets(template_cache_dir):
    """cat_side -> Buckets from the cached TEMPLATE indexes (tools/v18_learn_common.template_index). Missing -> empty."""
    out = {}
    tidx = {}
    for cs in CAT_SIDES:
        cands = {}
        if template_cache_dir:
            fs = sorted(glob.glob(os.path.join(os.path.expanduser(template_cache_dir), f"template_index_{cs}_*.json")), key=os.path.getmtime)
            if fs:
                try:
                    t = json.loads(Path(fs[-1]).read_text())
                    tidx[cs] = t
                    cands = t.get("switches") or {}
                except Exception:
                    pass
        out[cs] = Buckets(cands)
    return out, tidx


def sorted_events(spill_dirs, tmp_dir, out_path):
    files = []
    for d in spill_dirs:
        dd = os.path.expanduser(d)
        files.extend(sorted(glob.glob(os.path.join(dd, "spill_*.tsv")) + glob.glob(os.path.join(dd, "spill_*.tsv.gz"))))
    if not files:
        raise SystemExit("no spill_*.tsv[.gz] in " + ", ".join(spill_dirs))
    Path(tmp_dir).mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, LC_ALL="C")
    plain, made = [], []
    for f in files:
        if f.endswith(".gz"):
            u = Path(tmp_dir) / (Path(f).name[:-3] + ".u")
            with gzip.open(f, "rb") as src, open(u, "wb") as dst:
                for chunk in iter(lambda: src.read(1 << 22), b""):
                    dst.write(chunk)
            plain.append(str(u))
            made.append(u)
        else:
            plain.append(f)
    cmd = ["sort", "-s", "-t", "\t", "-k1,1", "-S", os.environ.get("V18_SORT_MEM", "1G"), "-T", str(tmp_dir), "-o", str(out_path)] + plain
    subprocess.run(cmd, check=True, env=env)
    for u in made:
        os.remove(u)
    return files


def cmd_train(a):
    t0 = time.time()
    tmp = Path(a.tmp_dir or (Path(a.spill_dir[0]) / "tmp"))
    srt = tmp / "events_sorted.tsv"
    files = sorted_events(a.spill_dir, tmp, srt)
    t_sort = time.time() - t0
    buckets, tidx = load_buckets(a.template_cache)
    brains = {cs: CatBrain(cs, buckets[cs], a.cut) for cs in CAT_SIDES}
    seen = set()
    cnt = collections.Counter()
    n = 0
    with open(srt) as fh:
        for line in fh:
            n += 1
            ts, _, js = line.partition("\t")
            try:
                ev = json.loads(js)
            except Exception:
                cnt["bad_event"] += 1
                continue
            rk = ev.get("rk")
            if rk is not None:
                if rk in seen:
                    cnt["dup_" + str(ev.get("e"))] += 1
                    continue
                seen.add(rk)
            cs = cat_side_of(ev.get("s"))
            if cs is None:
                cnt["refused_cat_side"] += 1
                continue
            brains[cs].handle(ev, ts)
            cnt["events_" + cs] += 1
            if n % 1000000 == 0:
                print(f"[train] {n:,} events {time.time() - t0:.0f}s", flush=True)
    for b in brains.values():
        b.finish()
    del seen
    meta = {"schema": SCHEMA, "at": utc_now(), "cut": a.cut, "spills": files, "events_read": n, "dedupe": dict(cnt), "sort_secs": round(t_sort, 1),
            "train_secs": round(time.time() - t0 - t_sort, 1), "m_shrink": M_SHRINK, "w_fit": W_FIT, "w_fwd": W_FWD, "nf": NF,
            "regime_source": "board bh bucket (FLAT/UP/DOWN/STRONG_*); classify_regime needs NPZ bars the corpus does not hold",
            "extract_reports": [json.loads(Path(p).read_text()) for d in a.spill_dir for p in sorted(glob.glob(os.path.join(os.path.expanduser(d), "extract_*.json")))]}
    st = _state_dump({"meta": meta, "brains": brains, "tidx": tidx})
    del brains
    if a.state:
        Path(a.state).parent.mkdir(parents=True, exist_ok=True)
        with open(str(a.state) + ".tmp", "wb") as fh:
            pickle.dump(st, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(str(a.state) + ".tmp", a.state)
    rep = build_report(st, floor=a.floor)
    if a.out_dir:
        rep["exports"] = export_all(st, a.out_dir, a.floor, a.p_ref_r, a.p_ref_f)
    if a.report:
        Path(a.report).parent.mkdir(parents=True, exist_ok=True)
        Path(a.report).write_text(json.dumps(rep, indent=1, sort_keys=True, default=str))
    print(json.dumps({k: rep[k] for k in ("meta_short", "per_cat_side") if k in rep}, indent=1, default=str)[:20000])
    if not a.keep_sorted:
        try:
            os.remove(srt)
        except OSError:
            pass
    return 0


def _state_dump(state):
    out = {"meta": state["meta"], "tidx": state["tidx"], "brains": {}}
    for cs, b in state["brains"].items():
        out["brains"][cs] = {"cs": cs, "nodes": b.g.nodes, "scopes": b.g.scopes, "keys": list(b.g.key.items()), "E": b.g.E[: b.g.m].copy(),
                             "w": b.h.w, "hn": dict(b.h.n), "ema": {k: v for k, v in b.ema.items() if isinstance(k, str)},
                             "c": dict(b.c), "capture": b.capture(), "forward": b.forward_report(), "ledger": {k: list(v) for k, v in b.ledger.items()},
                             "ledger_key": {k: {f: list(t) for f, t in v.items()} for k, v in b.ledger_key.items()},
                             "live_fam": {k: list(v) for k, v in b.live_fam.items()}, "remedy": {k: list(v) for k, v in b.remedy.items()},
                             "quart": {"|".join(k): list(v) for k, v in b.quart.items()}, "sheet_of": b.sheet_of, "first_ts": b.first_ts, "last_ts": b.last_ts,
                             "buckets": b.bk.cands}
    return out


def load_state(path):
    with open(path, "rb") as fh:
        return pickle.load(fh)


# ------------------------------------------------------------------------------------------------ frozen view (state or export)

class View:
    """Read-only evidence view of one cat_side from a dumped state (full tables)."""

    def __init__(self, sb):
        self.sb = sb
        self.nodes = sb["nodes"]
        self.scopes = sb["scopes"]
        self.E = sb["E"]
        self.nid = {s: i for i, s in enumerate(self.nodes)}
        self.sid = {s: i for i, s in enumerate(self.scopes)}
        self.k = dict((tuple(k), v) for k, v in sb["keys"])

    def row(self, scope_id, a, b=""):
        na = self.nid.get(a)
        if na is None:
            return None, None
        nb = self.nid.get(b, None) if b else -1
        if nb is None:
            return None, None
        i = self.k.get((scope_id, na, nb))
        return (i, self.E[i]) if i is not None else (None, None)

    def edges(self, pred):
        for (s, na, nb), i in self.k.items():
            if pred(s, na, nb):
                yield (s, na, nb), i, self.E[i]

    def p0(self, prefix):
        """cat_side base positive rate of a node kind (R: / F: / pair) — the Beta prior mean."""
        if not hasattr(self, "_p0"):
            self._p0 = {}
        if prefix in self._p0:
            return self._p0[prefix]
        pos = n = 0.0
        for (s, na, nb), i in self.k.items():
            if s == 0 and nb == -1 and self.nodes[na].startswith(prefix):
                pos += self.E[i, C["wpos"]]
                n += self.E[i, C["wn"]]
        self._p0[prefix] = (pos + 0.5) / (n + 1.0)
        return self._p0[prefix]


def get_view(sb):
    v = sb.get("_view")
    if v is None:
        v = sb["_view"] = View(sb)
    return v


def edge_stats(E, p0=0.05, m=M_SHRINK):
    def mean(sc, cc):
        return round(float(E[C[sc]] / E[C[cc]]), 4) if E[C[cc]] else None
    wn, wpos = float(E[C["wn"]]), float(E[C["wpos"]])
    p = (wpos + m * p0) / (wn + m)
    eg = float(E[C["sum_pos"]] / E[C["pos"]]) if E[C["pos"]] else 0.0
    out = {"n": int(E[C["n"]]), "inv": int(E[C["inv"]]), "pos": int(E[C["pos"]]), "neg": int(E[C["neg"]]), "p_pos": round(p, 4), "e_gain_pos": round(eg, 4),
           "value": round(p * eg, 5), "d_tim": mean("s_tim", "c_tim"), "d_trades": mean("s_tr", "c_tr"), "d_dd": mean("s_dd", "c_dd"),
           "d_sharpe": mean("s_sh", "c_sh"), "d_wr": mean("s_wr", "c_wr"), "mean_secs": round(float(E[C["secs"]] / E[C["n"]]), 4) if E[C["n"]] else None,
           "n_ident": int(E[C["n_ident"]]), "fwd_n": int(E[C["fwd_n"]]), "fwd_pos": int(E[C["fwd_pos"]]), "live_n": int(E[C["live_n"]]),
           "live_pos": int(E[C["live_pos"]]), "live_pnl_pp": round(float(E[C["live_pnl"]]), 3), "ph_n": int(E[C["ph_n"]]), "ph_pos": int(E[C["ph_pos"]]),
           "n_sym": int(E[C["n_sym"]]), "n_sym_pos": int(E[C["n_sym_pos"]]), "abl_n": int(E[C["abl_n"]]), "abl_kill": int(E[C["abl_kill"]])}
    return out


def pair_stats(E):
    return {"n": int(E[C["n"]]), "pos": int(E[C["pos"]]), "neg": int(E[C["neg"]]), "n_rescue": int(E[C["n_rescue"]]), "n_kill": int(E[C["n_kill"]]),
            "n_syn": int(E[C["n_syn"]]), "syn_mean": round(float(E[C["syn_sum"]] / E[C["n_syn"]]), 4) if E[C["n_syn"]] else None,
            "syn_pos": int(E[C["syn_pos"]]), "syn_neg": int(E[C["syn_neg"]]), "abl_n": int(E[C["abl_n"]]), "abl_kill": int(E[C["abl_kill"]]),
            "mean_marg": round(float((E[C["sum_pos"]] + E[C["sum_neg"]]) / max(1.0, E[C["n"]] - E[C["inv"]])), 4), "n_sym": int(E[C["n_sym"]]),
            "n_sym_pos": int(E[C["n_sym_pos"]])}


# ------------------------------------------------------------------------------------------------ export

def export_one(sb, tidx, floor, p_ref_r, p_ref_f, max_bytes=MAX_EXPORT_BYTES, min_keep=1000):
    V = get_view(sb)
    nodes = V.nodes
    cs_rows, reg_rows, pair_rows, sym = [], [], [], collections.defaultdict(list)
    keep_nodes = {}

    def nidx(s):
        j = keep_nodes.get(s)
        if j is None:
            j = keep_nodes[s] = len(keep_nodes)
        return j

    pairs = []
    for (s, na, nb), i, E in V.edges(lambda s, na, nb: True):
        a = nodes[na]
        if s == 0 and nb == -1:
            if E[C["n"]] == 0 and E[C["fwd_n"]] == 0 and E[C["live_n"]] == 0 and E[C["ph_n"]] == 0 and E[C["n_ident"]] == 0:
                continue
            st = edge_stats(E)
            cs_rows.append([nidx(a), round(float(E[C["wpos"]]), 3), round(float(E[C["wn"]]), 3), st["n"], st["pos"], st["neg"], st["inv"], round(float(E[C["sum_pos"]]), 3),
                            st["d_tim"], st["d_trades"], st["d_dd"], st["d_sharpe"], st["d_wr"], st.get("n_sym", 0), st["fwd_n"], st["fwd_pos"], st["live_n"],
                            st["live_pos"], st["n_ident"], st["mean_secs"], st["live_pnl_pp"]])
        elif s == 0 and nb >= 0:
            b = nodes[nb]
            if b.startswith("G:"):
                if E[C["wn"]] > 0:
                    reg_rows.append([nidx(a), b[2:], round(float(E[C["wpos"]]), 3), round(float(E[C["wn"]]), 3)])
            elif E[C["wn"]] > 0 or E[C["abl_n"]] > 0:
                pairs.append((E[C["pos"]] > 0 or E[C["n_rescue"]] > 0 or E[C["abl_kill"]] > 0, float(E[C["wn"]]), a, b, i, E))
        elif s > 0 and nb == -1 and E[C["wn"]] > 0:
            sym[V.scopes[s]].append((float(E[C["pos"]]), float(E[C["wn"]]), a, E))
    pairs.sort(key=lambda r: (not r[0], -r[1]))
    for imp, _wn, a, b, i, E in pairs:
        pair_rows.append([nidx(a), nidx(b), round(float(E[C["wpos"]]), 3), round(float(E[C["wn"]]), 3), int(E[C["n"]]), int(E[C["pos"]]), int(E[C["n_rescue"]]),
                          int(E[C["n_kill"]]), int(E[C["n_syn"]]), round(float(E[C["syn_sum"]]), 3), int(E[C["n_sym"]]), int(E[C["abl_n"]]), int(E[C["abl_kill"]])])
    sym_rows = {}
    for ss, lst in sym.items():
        lst.sort(key=lambda r: (-(r[0] > 0), -r[1]))
        sym_rows[ss] = [[nidx(a), round(float(E[C["wpos"]]), 3), round(float(E[C["wn"]]), 3)] for _p, _n, a, E in lst]
    coverage = coverage_of(sb, tidx, floor, p_ref_r, p_ref_f)
    blob = {"_meta": {"schema": SCHEMA, "cat_side": sb["cs"], "at": utc_now(), "first_ts": sb["first_ts"], "last_ts": sb["last_ts"], "counters": sb["c"],
                      "heads_trained": sb["hn"], "nf": NF, "floor_per_1000": max(HARD_MIN_FLOOR, floor), "p_ref": {"R": p_ref_r, "F": p_ref_f},
                      "m_shrink": M_SHRINK, "cs_cols": ["node", "wpos", "wn", "n", "pos", "neg", "inv", "sum_pos", "d_tim", "d_trades", "d_dd", "d_sharpe", "d_wr",
                                                        "n_sym", "fwd_n", "fwd_pos", "live_n", "live_pos", "n_ident", "mean_secs", "live_pnl_pp"],
                      "pair_cols": ["filter", "row", "wpos", "wn", "n", "pos", "n_rescue", "n_kill", "n_syn", "syn_sum", "n_sym", "abl_n", "abl_kill"],
                      "regime_cols": ["node", "regime", "wpos", "wn"], "sym_cols": ["node", "wpos", "wn"], "trimmed": {}},
            "heads": sb["w"], "buckets": sb.get("buckets") or {}, "ema": {k: round(v, 4) for k, v in sb["ema"].items()},
            "cs": cs_rows, "regime": reg_rows, "pair": pair_rows, "sym": sym_rows, "coverage": coverage,
            "exits": {"live": sb["live_fam"], "ledger": sb["ledger"]}, "prequential": sb["capture"], "forward_eval": sb["forward"]}
    nodes_list = [None] * len(keep_nodes)
    for s, j in keep_nodes.items():
        nodes_list[j] = s
    blob["nodes"] = nodes_list
    txt = json.dumps(blob, separators=(",", ":"))
    # size law: trim the lowest-evidence pair rows, then the lowest-evidence sym rows, until <= max_bytes
    while len(txt) > max_bytes:
        over = len(txt) - max_bytes
        if len(blob["pair"]) > min_keep:
            k = max(1, int(len(blob["pair"]) * min(0.5, over / len(txt) + 0.05)))
            cut = max(min_keep, len(blob["pair"]) - k)
            blob["_meta"]["trimmed"]["pair"] = blob["_meta"]["trimmed"].get("pair", 0) + len(blob["pair"]) - cut
            blob["pair"] = blob["pair"][:cut]
        else:
            tot = sum(len(v) for v in blob["sym"].values())
            if tot <= min_keep:
                break
            frac = min(0.5, over / len(txt) + 0.05)
            dropped = 0
            for ss in list(blob["sym"]):
                L = blob["sym"][ss]
                keep = max(min(10, min_keep), int(len(L) * (1 - frac)))
                dropped += len(L) - keep
                blob["sym"][ss] = L[:keep]
            blob["_meta"]["trimmed"]["sym"] = blob["_meta"]["trimmed"].get("sym", 0) + dropped
        txt = json.dumps(blob, separators=(",", ":"))
    return blob, txt


def coverage_of(sb, tidx, floor, p_ref_r, p_ref_f):
    """Template universe vs evidence: every (switch=cand) row and (switch=cand, filter=opt) cell of the cat_side TEMPLATE."""
    t = (tidx or {}).get(sb["cs"])
    if not t:
        return {"note": "no template index available on this host — coverage not computed"}
    V = get_view(sb)
    T = _ViewTables(V)
    bk = Buckets(sb.get("buckets") or {})
    w = sb["w"]
    fl = floor_frac(floor)
    rows_total = rows_lt3 = rows_floor = 0
    pairs_total = pairs_seen = pairs_lt3 = pairs_floor = 0
    by_tab = {}
    for tab, blk in t["tabs"].items():
        rt = pt = plt3 = pfl = 0
        for _r, sw, cand, _d in blk["rows"]:
            if sw in DENY:
                continue
            rk = f"{sw}={cand}"
            rows_total += 1
            x, cn = featurize(T, bk, "R", "", tab, rk, None, None, "UNK", 0.0, 0.0, 0.02, 0.02)
            if cn < 3:
                rows_lt3 += 1
            elif p_eval(sig(sum(a * b for a, b in zip(w["R"], x))), p_ref_r, floor, cn) <= fl + 1e-12:
                rows_floor += 1
            rt += 1
            for y in blk["yellows"]:
                pairs_total += 1
                pt += 1
                _i, E = V.row(0, "F:" + y, "R:" + rk)
                n_pair = float(E[C["n"]]) if E is not None else 0.0
                if n_pair > 0:
                    pairs_seen += 1
                if n_pair < 3:
                    pairs_lt3 += 1
                    plt3 += 1
                x, cn = featurize(T, bk, "F0", "", tab, rk, y, None, "UNK", 0.0, 0.0, 0.02, 0.02)
                if cn >= 3 and p_eval(sig(sum(a * b for a, b in zip(w["F0"], x))), p_ref_f, floor, cn) <= fl + 1e-12:
                    pairs_floor += 1
                    pfl += 1
        by_tab[tab] = {"rows": rt, "pairs": pt, "pairs_n_lt_3": plt3, "pairs_at_floor": pfl}
    return {"template": t.get("path"), "template_md5": t.get("md5"), "rows_total": rows_total, "rows_n_lt_3": rows_lt3, "rows_at_floor": rows_floor,
            "pairs_total": pairs_total, "pairs_seen": pairs_seen, "pairs_n_lt_3": pairs_lt3, "pairs_at_floor": pairs_floor, "by_tab": by_tab,
            "note": "at_floor = evidenced (n>=3) candidates whose allocator p_eval equals the floor with a neutral board (EMA 0.02, no regime); "
                    "n_lt_3 = never fairly tried (always computed, fail-open)"}


class _ViewTables(Tables):
    def __init__(self, V):
        self.V = V
        self.g = None

    def _pw(self, s, a, b=""):
        _i, E = self.V.row(s, a, b)
        return (float(E[C["wpos"]]), float(E[C["wn"]])) if E is not None else (0.0, 0.0)

    def cs(self, a):
        return self._pw(0, a)

    def sym(self, sym_side, a):
        sid = self.V.sid.get(sym_side)
        return self._pw(sid, a) if sid is not None else (0.0, 0.0)

    def reg(self, a, g):
        return self._pw(0, a, "G:" + g)

    def pair(self, f, r):
        return self._pw(0, f, r)

    def fwd(self, a):
        _i, E = self.V.row(0, a)
        return (float(E[C["fwd_pos"]]), float(E[C["fwd_n"]])) if E is not None else (0.0, 0.0)

    def live(self, a):
        _i, E = self.V.row(0, a)
        return (float(E[C["live_pos"]]), float(E[C["live_n"]])) if E is not None else (0.0, 0.0)


def export_all(st, out_dir, floor, p_ref_r, p_ref_f):
    """st = a dumped state (_state_dump / load_state)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    res = {}
    for cs, sb in st["brains"].items():
        blob, txt = export_one(sb, st.get("tidx"), floor, p_ref_r, p_ref_f)
        p = out_dir / f"v18_brain_{cs}.json"
        with open(str(p) + ".tmp", "w") as fh:
            fh.write(txt)
        os.replace(str(p) + ".tmp", p)
        res[cs] = {"path": str(p), "bytes": p.stat().st_size, "nodes": len(blob["nodes"]), "cs_rows": len(blob["cs"]), "pair_rows": len(blob["pair"]),
                   "sym_sides": len(blob["sym"]), "regime_rows": len(blob["regime"]), "trimmed": blob["_meta"]["trimmed"],
                   "coverage": {k: v for k, v in blob["coverage"].items() if k != "by_tab"}}
    return res


def cmd_export(a):
    st = load_state(a.state)
    print(json.dumps(export_all(st, a.out_dir, a.floor, a.p_ref_r, a.p_ref_f), indent=1))
    return 0


# ------------------------------------------------------------------------------------------------ report / suggest / explain

def build_report(st, floor=DEFAULT_FLOOR):
    """st = a dumped state (_state_dump / load_state)."""
    meta = st["meta"]
    per = {}
    for cs, sb in st["brains"].items():
        V = get_view(sb)
        kinds = collections.Counter(n.split(":", 1)[0] for n in V.nodes)
        ekinds = collections.Counter()
        for (s, na, nb), i in V.k.items():
            ekinds["cat_side_unary" if (s == 0 and nb == -1) else ("cat_side_pair" if s == 0 else "sym_side")] += 1
        per[cs] = {"counters": sb["c"], "nodes": len(V.nodes), "node_kinds": dict(kinds), "edges": len(V.k), "edge_kinds": dict(ekinds),
                   "first_ts": sb["first_ts"], "last_ts": sb["last_ts"], "prequential": sb["capture"], "forward": sb["forward"]}
        try:
            board, live = losing_exit_families(sb, 3)
            per[cs]["exit_families_board"] = board
            per[cs]["exit_families_live"] = live
            fams = [f["family"] for f in board] + [f["family"] for f in live if f["family"] not in {g["family"] for g in board}]
            per[cs]["explain_exit_families"] = {f: explain_family(sb, f, top=20) for f in fams}
            sm = most_suffocated(sb, 5)
            per[cs]["most_suffocated"] = sm
            per[cs]["explain_suffocated"] = {r["switch"]: explain_switch(sb, r["switch"], top=20) for r in sm}
        except Exception as e:  # report must never die on one cat_side
            per[cs]["explain_error"] = repr(e)
    return {"meta_short": {k: meta[k] for k in ("schema", "at", "cut", "events_read", "dedupe", "sort_secs", "train_secs")}, "meta": meta, "per_cat_side": per}


def losing_exit_families(sb, k=3, min_live=3):
    """Exit families by loss, two rankings never summed: board = replayed ledger trades (sum of losers, pp of peak), live =
    hermetic /history lifecycles with >= min_live closes. Each row carries both columns."""
    live = {f[2:]: v for f, v in sb["live_fam"].items() if f.startswith("X:")}
    led = sb["ledger"]
    rows = []
    for fam in set(live) | set(led):
        lv = live.get(fam, [0, 0, 0.0, 0.0])
        ld = led.get(fam, [0, 0, 0.0, 0.0])
        rows.append({"family": fam, "live_n": lv[0], "live_pos": lv[1], "live_pnl_pp": round(lv[2], 3), "live_loss_pp": round(lv[3], 3),
                     "ledger_trades": ld[0], "ledger_pos": ld[1], "ledger_pnl_pp": round(ld[2], 3), "ledger_loss_pp": round(ld[3], 3)})
    board = sorted((r for r in rows if r["ledger_trades"]), key=lambda r: r["ledger_loss_pp"])[:k]
    live = sorted((r for r in rows if r["live_n"] >= min_live), key=lambda r: r["live_loss_pp"])[:k]
    return board, live


def _family_switches(sb, fam):
    """Switches whose name carries the family name (by name, on the exit/reduce tabs): EXIT_VELOCITY_WT_MIN_TFS -> EXIT_VELOCITY_WT."""
    fam = str(fam)
    out = []
    curated = []
    try:
        from tools import v18_exit_loss_rank as X
        for _sym, match, paths in X.CURATED:
            try:
                if match(fam, {}):
                    curated.extend(p for p, _vals in paths)
            except Exception:
                continue
    except Exception:
        pass
    stop = {"EXIT", "ENTRY", "ENABLED", "TF", "MIN", "MAX", "PCT", "15M", "1H", "4H", "D", "W", "M", "FILTER", "ON", "OFF", "THE", "UNCOND"}
    ftok = {t for t in fam.upper().split("_") if t and t not in stop and not t[0].isdigit()}
    for sw, sheet in sb["sheet_of"].items():
        core = re.sub(r"_(ENABLED|TF|FILTER_TF|ON)$", "", sw)
        by_name = sw.startswith(fam) or (len(core) >= 8 and fam.startswith(core))
        by_cur = any((c == sw) if isinstance(c, str) else bool(c.search(sw)) for c in curated)
        by_tok = sheet in EXIT_SHEETS and len(ftok) >= 2 and ftok <= set(sw.upper().split("_"))
        if by_name or by_cur or by_tok:
            out.append((sw, sheet))
    return out


def explain_family(sb, fam, top=20):
    V = get_view(sb)
    p0 = V.p0("R:")
    rows = []
    for sw, sheet in _family_switches(sb, fam):
        for (s, na, nb), i, E in V.edges(lambda s, na, nb, _sw=sw: s == 0 and nb == -1 and V.nodes[na].startswith("R:" + _sw + "=")):
            st = edge_stats(E, p0)
            rows.append(dict(key=V.nodes[na][2:], sheet=sheet, **st))
    rows.sort(key=lambda r: -r["value"])
    out = {"family": fam, "mapping": "switch name prefix / doc-17 curated table (v18_exit_loss_rank.CURATED) / all family tokens in an exit-tab switch name",
           "switches_by_name": sorted({r["key"].split("=", 1)[0] for r in rows}), "rows": rows[:top]}
    xi, XE = V.row(0, "X:" + fam)
    if XE is not None:
        out["live_family"] = {"n": int(XE[C["live_n"]]), "pos": int(XE[C["live_pos"]]), "pnl_pp": round(float(XE[C["live_pnl"]]), 3)}
    if not rows:
        out["note"] = "no switch name matches this family on the exit tabs (by-name mapping); the family is an execution/stop path or carries a parameter tail"
    return out


def most_suffocated(sb, k=5):
    """Switch names ranked by sym_sides on which some filter option RESCUED a non-positive naked (the chain's filter setting
    suffocated it), plus ablation killer attributions."""
    V = get_view(sb)
    agg = collections.defaultdict(lambda: [0, 0, 0, set()])
    for (s, na, nb), i, E in V.edges(lambda s, na, nb: s == 0 and nb >= 0):
        b = V.nodes[nb]
        if not b.startswith("R:"):
            continue
        sw = b[2:].split("=", 1)[0]
        a = agg[sw]
        a[0] += int(E[C["n_rescue"]])
        a[1] += int(E[C["abl_kill"]])
        a[2] += int(E[C["n_kill"]])
        if E[C["n_rescue"]] > 0 or E[C["abl_kill"]] > 0:
            a[3].add(V.nodes[na])
    rows = [{"switch": sw, "rescues": v[0], "abl_killer_attr": v[1], "kills": v[2], "n_filters": len(v[3])} for sw, v in agg.items() if v[0] or v[1]]
    rows.sort(key=lambda r: -(r["rescues"] + r["abl_killer_attr"]))
    return rows[:k]


def explain_switch(sb, switch, top=20):
    V = get_view(sb)
    p0r = V.p0("R:")
    cands = []
    for (s, na, nb), i, E in V.edges(lambda s, na, nb: s == 0 and nb == -1 and V.nodes[na].startswith("R:" + switch + "=")):
        cands.append(dict(key=V.nodes[na][2:], **edge_stats(E, p0r)))
    cands.sort(key=lambda r: -r["value"])
    regimes = collections.defaultdict(list)
    for (s, na, nb), i, E in V.edges(lambda s, na, nb: s == 0 and nb >= 0 and V.nodes[na].startswith("R:" + switch + "=") and V.nodes[nb].startswith("G:")):
        st = edge_stats(E, p0r)
        if st["n"] - st["inv"] >= 3:
            regimes[V.nodes[nb][2:]].append({"key": V.nodes[na][2:], "n": st["n"], "pos": st["pos"], "p_pos": st["p_pos"], "value": st["value"]})
    reg_best = {g: sorted(v, key=lambda r: -r["value"])[:3] for g, v in regimes.items()}
    suff, comp = [], []
    for (s, na, nb), i, E in V.edges(lambda s, na, nb: s == 0 and nb >= 0 and V.nodes[nb].startswith("R:" + switch + "=")):
        ps = pair_stats(E)
        ps.update({"filter": V.nodes[na].split(":", 1)[1], "row": V.nodes[nb][2:]})
        if ps["n_rescue"] or ps["n_kill"] or ps["abl_kill"] or ps["syn_neg"]:
            suff.append(ps)
        if ps["pos"] and ps["mean_marg"] > 0:
            comp.append(ps)
    suff.sort(key=lambda r: -(r["n_rescue"] + r["abl_kill"] + r["n_kill"] + r["syn_neg"]))
    comp.sort(key=lambda r: -r["pos"])
    exits = collections.defaultdict(lambda: [0, 0.0])
    for key, fams in sb["ledger_key"].items():
        if key.startswith(switch + "="):
            for fam, t in fams.items():
                exits[fam][0] += t[0]
                exits[fam][1] += t[1]
    ex_rows = sorted(({"exit_family": f, "trades": v[0], "pnl_pp": round(v[1], 3)} for f, v in exits.items()), key=lambda r: r["pnl_pp"])
    name_i, NE = V.row(0, "N:" + switch)
    return {"switch": switch, "sheet": sb["sheet_of"].get(switch), "name_level": edge_stats(NE, p0r) if NE is not None else None,
            "settings_ranked": cands[:top], "best_by_regime": reg_best, "suffocating_filters": suff[:top], "companions": comp[:top],
            "exits_it_ends_in_ledger_cooc": ex_rows[:top],
            "definitions": {"n_rescue": "naked <= 0 and the cell with filter=opt > 0: the chain's filter setting suffocated the switch",
                            "n_kill": "naked > 0 and the cell marginal < 0: filter=opt suffocates the working switch",
                            "abl_kill": "ablation attribution: discovered on the strip base, restoring this filter cut it below 0.5x",
                            "syn_neg": "in-board quartet chain/+a/+b/+a+b with negative interaction",
                            "exits_it_ends_in_ledger_cooc": "replayed board ledgers whose set contains switch=cand: trades by exit family (co-occurrence)"}}


def _state_sb(a):
    st = load_state(a.state)
    return st["brains"][a.cat_side]


def cmd_report(a):
    st = load_state(a.state)
    print(json.dumps(build_report(st, a.floor), indent=1, default=str))
    return 0


def cmd_explain(a):
    sb = _state_sb(a)
    if a.family:
        out = explain_family(sb, a.family, a.top)
    else:
        out = explain_switch(sb, a.switch, a.top)
    print(json.dumps(out, indent=1, default=str))
    return 0


def cmd_suggest(a):
    """Rank keys for a metric from the exported model (or the state). gain = P(pos) x E[d|pos]; dd = most negative mean d_dd;
    tim/trades/sharpe/wr = mean metric difference; forward = Beta posterior of next-board persistence x P(pos). Keys with no
    evidence for the metric are never invented: an explicit 'no evidence' line is printed instead."""
    path = a.model or str(Path(a.model_dir) / f"v18_brain_{a.cat_side}.json")
    blob = json.load(open(path))
    nodes = blob["nodes"]
    cols = {c: i for i, c in enumerate(blob["_meta"]["cs_cols"])}
    sym_t = {}
    if a.sym_side:
        if cat_side_of(a.sym_side) != a.cat_side:
            print(f"refused: {a.sym_side} is not in {a.cat_side} (four cat_sides stay apart)")
            return 2
        sym_t = {nodes[r[0]]: (r[1], r[2]) for r in (blob.get("sym") or {}).get(a.sym_side, [])}
    rows = [r for r in blob["cs"] if nodes[r[0]].startswith(("R:", "F:"))]
    tp = sum(r[cols["wpos"]] for r in rows if nodes[r[0]].startswith(a.kind + ":"))
    tn = sum(r[cols["wn"]] for r in rows if nodes[r[0]].startswith(a.kind + ":"))
    p0 = (tp + 0.5) / (tn + 1.0)
    out = []
    mk = {"tim": "d_tim", "trades": "d_trades", "dd": "d_dd", "sharpe": "d_sharpe", "wr": "d_wr"}
    for r in rows:
        node = nodes[r[0]]
        if not node.startswith(a.kind + ":") or r[cols["n_sym"]] < a.min_n_sym:
            continue
        wpos, wn = r[cols["wpos"]], r[cols["wn"]]
        p = (wpos + M_SHRINK * p0) / (wn + M_SHRINK)
        if node in sym_t:
            sp, sn = sym_t[node]
            p = (sp + M_SHRINK * p) / (sn + M_SHRINK)
        eg = r[cols["sum_pos"]] / r[cols["pos"]] if r[cols["pos"]] else 0.0
        if a.metric == "gain":
            v = p * eg
        elif a.metric == "forward":
            if not r[cols["fwd_n"]]:
                continue
            v = (r[cols["fwd_pos"]] + 0.5) / (r[cols["fwd_n"]] + 1.0) * p
        else:
            v = r[cols[mk[a.metric]]]
            if v is None:
                continue
        out.append((v, node, p, eg, r))
    if not out:
        print(f"no evidence: cat_side={a.cat_side} metric={a.metric} kind={a.kind} (n=0)")
        return 0
    out.sort(key=lambda t: t[0], reverse=(a.metric != "dd"))
    for v, node, p, eg, r in out[: a.top]:
        print(f"{v:+10.4f}  {node:70s} p_pos={p:.3f} E[d|pos]={eg:.2f} n={r[cols['n']]} n_sym={r[cols['n_sym']]} pos={r[cols['pos']]} d_tim={r[cols['d_tim']]} "
              f"d_trades={r[cols['d_trades']]} d_dd={r[cols['d_dd']]} d_sharpe={r[cols['d_sharpe']]} d_wr={r[cols['d_wr']]} fwd={r[cols['fwd_pos']]}/{r[cols['fwd_n']]} "
              f"live_cooc={r[cols['live_pos']]}/{r[cols['live_n']]}")
    return 0


# ------------------------------------------------------------------------------------------------ runtime (pilot) + compare

class PilotBrainV18:
    """Runtime twin for ONE board (the pilot imports this behind V18_BRAIN=1). Loads data/brain/v18_brain_<CAT_SIDE>.json
    (V18_BRAIN_DIR overrides the dir). decide() is pure apart from the in-board EMA/sym evidence that observe() updates.
    Contract: decide(sheet, row_key, filter_key=None, round_id, naked_d=None) -> {keep, p, p_pos, n, u, why}; the floor
    (>= 1/1000) is a guarantee; the caller books p with every eval or skip it logs (delta-log field `brain_p`)."""

    def __init__(self, path, sym_side, floor_per_1000=None, p_ref_r=None, p_ref_f=None):
        blob = json.load(open(path))
        cs = cat_side_of(sym_side)
        if blob["_meta"].get("cat_side") != cs:
            raise ValueError(f"model {path} is {blob['_meta'].get('cat_side')}, board is {cs}: cat_sides never mix")
        self.sym_side, self.cs = sym_side, cs
        self.meta = blob["_meta"]
        self.w = blob["heads"]
        self.T = Tables(blob=blob, sym_side=sym_side)
        self.bk = Buckets(blob.get("buckets") or {})
        self.floor = max(HARD_MIN_FLOOR, float(floor_per_1000 if floor_per_1000 is not None else os.environ.get("V18_FLOOR_PER_1000", self.meta.get("floor_per_1000", DEFAULT_FLOOR))))
        self.p_ref = {"R": float(p_ref_r if p_ref_r is not None else os.environ.get("V18_BRAIN_PREF_R", self.meta.get("p_ref", {}).get("R", DEFAULT_PREF_R))),
                      "F": float(p_ref_f if p_ref_f is not None else os.environ.get("V18_BRAIN_PREF_F", self.meta.get("p_ref", {}).get("F", DEFAULT_PREF_F)))}
        self.ema = collections.defaultdict(lambda: 0.02)
        e0 = (blob.get("ema") or {}).get(sym_side)
        if e0:
            self.ema[sym_side] = float(e0)
        self.ctx = {"tier": 0.0, "bh": 0.0, "regime": "UNK"}
        self.booked = collections.Counter()

    def observe_ctx(self, tier=None, bh=None):
        self.ctx = {"tier": tier_code(tier), "bh": bh_code(bh), "regime": regime_of(bh)}

    def _x(self, kind, sheet, row_key, hdr, naked_d=None):
        return featurize(self.T, self.bk, kind, self.sym_side, sheet, row_key, hdr, naked_d, self.ctx["regime"], self.ctx["tier"], self.ctx["bh"],
                         self.ema[self.sym_side], self.ema[(self.sym_side, sheet)])

    def decide(self, sheet, row_key, filter_key=None, round_id="", naked_d=None):
        if filter_key is None:
            x, cn = self._x("R", sheet, row_key, None)
            p = sig(sum(a * b for a, b in zip(self.w["R"], x)))
            pe = p_eval(p, self.p_ref["R"], self.floor, cn)
            u = draw(self.sym_side, sheet, row_key, round_id)
            why = "V18_BRAIN row"
        else:
            if naked_d is not None and naked_d > EPS:
                pe, p, cn, u, why = 1.0, 1.0, 0, 0.0, "V18_BRAIN revive (naked > 0)"
            else:
                x, cn = self._x("F0", sheet, row_key, filter_key)
                p = sig(sum(a * b for a, b in zip(self.w["F0"], x)))
                pe = p_eval(p, self.p_ref["F"], self.floor, cn)
                u = draw(self.sym_side, sheet, row_key + "|" + str(filter_key), round_id)
                why = "V18_BRAIN cell"
        keep = u < pe
        self.booked["keep" if keep else "skip"] += 1
        return {"keep": keep, "p": pe, "p_pos": p, "n": cn, "u": u, "why": f"{why} p_pos={p:.4f} n={cn:g} p_eval={pe:.4f}"}

    def observe(self, sheet, row_key, delta, alpha=0.02):
        y = 1.0 if (delta is not None and delta > EPS) else 0.0
        for k in (self.sym_side, (self.sym_side, sheet)):
            self.ema[k] = min(0.98, max(0.002, (1 - alpha) * self.ema[k] + alpha * y))
        if delta is not None:
            a = "R:" + row_key
            sp, sn = self.T.sym_t.get(a, (0.0, 0.0))
            self.T.sym_t[a] = [sp + y, sn + 1.0]


def load_pilot_brain(sym_side, env=None):
    """V18_BRAIN=1 + data/brain/v18_brain_<CAT_SIDE>.json (V18_BRAIN_DIR) -> PilotBrainV18, else None (fail-open: compute all)."""
    env = env if env is not None else os.environ
    if str(env.get("V18_BRAIN", "0")).strip() not in ("1", "true", "True", "on"):
        return None
    cs = cat_side_of(sym_side)
    if cs is None:
        return None
    p = Path(env.get("V18_BRAIN_DIR") or (ROOT / "data" / "brain")) / f"v18_brain_{cs}.json"
    if not p.exists():
        return None
    try:
        return PilotBrainV18(p, sym_side)
    except Exception:
        return None


def compare_models(champ_path, chal_path, sorted_events_path, days=3.0, budget_frac=0.5, min_pos=10):
    """Champion vs challenger on the last `days` of real fresh evals (one sorted event file): both frozen models score every
    eval of their own cat_side with a neutral board context; at EQUAL eval-seconds the positives kept are compared.
    Verdict only — never replaces a file."""
    champ, chal = json.load(open(champ_path)), json.load(open(chal_path))
    cs = champ["_meta"].get("cat_side")
    if chal["_meta"].get("cat_side") != cs:
        return {"winner": "error", "promote": False, "error": "champion and challenger are different cat_sides"}
    cut = (datetime.now(timezone.utc).timestamp() - days * 86400.0)
    cut_iso = iso_of(cut)
    Ta, Tb = Tables(blob=champ), Tables(blob=chal)
    ba, bb = Buckets(champ.get("buckets") or {}), Buckets(chal.get("buckets") or {})
    sa, sbb = [], []
    with open(sorted_events_path) as fh:
        for line in fh:
            ts, _, js = line.partition("\t")
            if ts < cut_iso:
                continue
            ev = json.loads(js)
            if ev.get("e") != "row" or cat_side_of(ev.get("s")) != cs:
                continue
            g = ev.get("g") or "UNK"
            items = []
            nk = ev.get("n")
            if nk is not None and nk[-1] and nk[0] is not None:
                items.append(("R", None, nk[0] if nk[1] else None, nk[2]))
            for c in ev.get("y") or []:
                items.append(("F0", c[0], c[1] if c[2] else None, c[3]))
            for kind, hdr, d, secs in items:
                if d is None:
                    continue
                y = 1.0 if d > EPS else 0.0
                xa, _ = featurize(Ta, ba, kind, ev["s"], ev["sh"], ev["k"], hdr, None, g, 0.0, 0.0, 0.02, 0.02)
                xb, _ = featurize(Tb, bb, kind, ev["s"], ev["sh"], ev["k"], hdr, None, g, 0.0, 0.0, 0.02, 0.02)
                h = "R" if kind == "R" else "F0"
                sa.append((sig(sum(p * q for p, q in zip(champ["heads"][h], xa))), secs or 0.0, y))
                sbb.append((sig(sum(p * q for p, q in zip(chal["heads"][h], xb))), secs or 0.0, y))
    pos = sum(s[2] for s in sa)
    verdict = {"winner": "insufficient", "promote": False, "cat_side": cs, "cut": cut_iso, "n": len(sa), "pos": int(pos)}
    if not sa or pos < min_pos:
        verdict["note"] = f"only {len(sa)} evals / {int(pos)} positives since {cut_iso}"
        return verdict
    budget = budget_frac * sum((s[1] if s[1] > 0 else 1.0) for s in sa)

    def cap(sc):
        got = spent = 0.0
        for p, secs, y in sorted(sc, key=lambda r: -r[0]):
            if spent >= budget:
                break
            unit = secs if secs > 0 else 1.0
            take = min(1.0, (budget - spent) / unit)
            got += take * y
            spent += take * unit
        return got

    ca, cb = cap(sa), cap(sbb)
    verdict.update({"budget_secs": round(budget, 1), "champ_pos_kept": round(ca, 2), "chal_pos_kept": round(cb, 2)})
    verdict["winner"] = "challenger" if cb > ca else ("champion" if ca > cb else "tie")
    verdict["promote"] = cb > ca
    return verdict


def cmd_compare(a):
    srt = a.events
    if not srt:
        tmp = Path(a.tmp_dir or (Path(a.spill_dir[0]) / "tmp"))
        srt = tmp / "events_sorted_compare.tsv"
        sorted_events(a.spill_dir, tmp, srt)
    print(json.dumps(compare_models(a.champ, a.chal, srt, a.days, a.budget, a.min_pos), indent=1))
    return 0


# ------------------------------------------------------------------------------------------------ main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    x = sub.add_parser("extract")
    x.add_argument("--source", required=True, choices=("logs", "rebuilt", "history", "ledgers", "native", "progress", "xlsx"))
    x.add_argument("--from", dest="source_from", action="append")
    x.add_argument("--host", default="mac")
    x.add_argument("--spill-dir", required=True)
    x.add_argument("--ctx-map", help="ctxmap_<host>.json written by --source rebuilt (bh/tier for logs without BOARD_CTX)")
    x.add_argument("--sets", help="ledgers: replay_sets.jsonl.gz (sym_side -> replayed set)")
    x.add_argument("--full", action="store_true", help="logs: ignore watermarks and re-read every file")
    x.add_argument("--max-delta-keys", type=int, default=64)
    x.add_argument("--covered-by", action="append", help="progress/xlsx: glob of delta logs (*.jsonl BOARD_CTX) or brain spills (spill_*.tsv[.gz] board events) whose boards are already absorbed")
    x.add_argument("--covered-by-ctx", help="progress/xlsx: JSON list of [sym_side, npz_last_bar_ts | ISO board ts] already absorbed")
    x.add_argument("--progress-index", action="append", help="xlsx: progress_index_<host>.json written by --source progress")
    t = sub.add_parser("train")
    t.add_argument("--spill-dir", action="append", required=True)
    t.add_argument("--tmp-dir")
    t.add_argument("--cut", required=True, help="ISO ts: prequential capture / forward holdout are scored at and after it")
    t.add_argument("--state")
    t.add_argument("--out-dir")
    t.add_argument("--report")
    t.add_argument("--template-cache", default=str(ROOT / "data" / "learn" / "_cache"))
    t.add_argument("--floor", type=float, default=DEFAULT_FLOOR)
    t.add_argument("--p-ref-r", type=float, default=DEFAULT_PREF_R)
    t.add_argument("--p-ref-f", type=float, default=DEFAULT_PREF_F)
    t.add_argument("--keep-sorted", action="store_true")
    e = sub.add_parser("export")
    e.add_argument("--state", required=True)
    e.add_argument("--out-dir", default=str(ROOT / "data" / "brain"))
    e.add_argument("--floor", type=float, default=DEFAULT_FLOOR)
    e.add_argument("--p-ref-r", type=float, default=DEFAULT_PREF_R)
    e.add_argument("--p-ref-f", type=float, default=DEFAULT_PREF_F)
    s = sub.add_parser("suggest")
    s.add_argument("--cat-side", required=True, choices=CAT_SIDES)
    s.add_argument("--metric", default="gain", choices=("gain", "dd", "tim", "trades", "sharpe", "wr", "forward"))
    s.add_argument("--kind", default="R", choices=("R", "F"))
    s.add_argument("--sym-side")
    s.add_argument("--model")
    s.add_argument("--model-dir", default=str(ROOT / "data" / "brain"))
    s.add_argument("--min-n-sym", type=int, default=3)
    s.add_argument("--top", type=int, default=25)
    xp = sub.add_parser("explain")
    xp.add_argument("--cat-side", required=True, choices=CAT_SIDES)
    xp.add_argument("--switch")
    xp.add_argument("--family")
    xp.add_argument("--state", required=True)
    xp.add_argument("--top", type=int, default=20)
    c = sub.add_parser("compare")
    c.add_argument("--champ", required=True)
    c.add_argument("--chal", required=True)
    c.add_argument("--events", help="an already sorted events file")
    c.add_argument("--spill-dir", action="append")
    c.add_argument("--tmp-dir")
    c.add_argument("--days", type=float, default=3.0)
    c.add_argument("--budget", type=float, default=0.5)
    c.add_argument("--min-pos", type=int, default=10)
    r = sub.add_parser("report")
    r.add_argument("--state", required=True)
    r.add_argument("--floor", type=float, default=DEFAULT_FLOOR)
    a = ap.parse_args(argv)
    if a.cmd == "explain" and not (a.switch or a.family):
        ap.error("explain needs --switch K or --family F")
    if a.cmd == "compare" and not (a.events or a.spill_dir):
        ap.error("compare needs --events or --spill-dir")
    return {"extract": cmd_extract, "train": cmd_train, "export": cmd_export, "suggest": cmd_suggest, "explain": cmd_explain, "compare": cmd_compare,
            "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
