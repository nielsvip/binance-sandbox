"""v15 DIAGNOSE+REPAIR — post-fill, non-sequential repair of a finished sym_side set (USER 2026-10-06).

Runs inside v15_pilot._spec_fill_workbook right after _final_filter_recheck, while the 30D prepared NPZ and the fork
pool are still alive. The greedy sequential fill can only climb from its baseline one row at a time; it never keeps a
negative step, so it cannot loosen a filter that blocks the entries a later switch needs, and it never adds the exits a
trending 30D window punishes (BIBLE §58). This phase:

  0. DIAGNOSE   — measure the final set (+ ledger: exit/entry reason mix, hold lengths) and name its faults
                  (TOO_FEW_TRADES, TOO_MANY_TRADES, TIM_HIGH, TIM_LOW, DD_HIGH, GAIN_NEG, BELOW_BH, LOW_WR,
                  LOSING_EXIT:<reason>, LOSING_ENTRY:<reason>).
  1. SOFTEN     — every template candidate is screened vs the current set; trade-adding softenings (filters/gates
                  off, entry/reentry/augment paths on) are applied one per round until the entry/reentry/augment
                  rows are LIVE (produce deltas) and trades reach the soften target. Gain may fall here — on purpose.
  2. ADD        — beam search over all candidates (entries, exits, reentries, reduces) by quality key.
  3. TIGHTEN    — beam search restricted to filters/gates + reverts of every change, by quality key.
  4. POLISH     — hill-climb over everything until no single change improves the key or the budget ends.
  5. VERIFY     — top distinct compliant finalists + the original set on 365D (same evaluator as the DONE gate);
                  selection key (30D compliant, 365D qualified, adjusted 30D gain, fewer changes).

Pure logic: every evaluation goes through ctx callbacks supplied by the pilot (pool eval + delta-log line per eval,
_EVAL_CACHE keyed by the full override set). Nothing here fabricates a delta — every number is a real
evaluate_prepared_sanitized / evaluate_sanitized result (BIBLE §19). Safety gates are never loosened
(BIBLE §58 final_safe tokens), ABLATION_DISABLE_*=True is never applied (§64), promotion-blocked switches are never
applied (DEAD_VEC / LIVE_ONLY / LIVE_DEAD_KEY / SIZING_FALSE_ALPHA).
"""
from __future__ import annotations

import hashlib
import json
import time
from collections import Counter, defaultdict
from tools.v15_shutdown import V15Shutdown

TIM_MIN, TIM_MAX, DD_MAX, FLOOR_TRADES, TARGET_TRADES = 20.0, 80.0, 30.0, 10, 30
TOO_MANY_TRADES_30D = 300
# fleet p90 trades/30D per cat_side (523 finished sheets, 2026-10-06 survey, ENCYCLOPEDIA §3); TOO_MANY also needs gain/trade < 0.10pp
P90_TRADES = {"CRYPTO_LONG": 213, "CRYPTO_SHORT": 171, "STOCKS_LONG": 188, "STOCKS_SHORT": 201}
LOW_EDGE_PP, CHURN_EDGE_PP, DD_WARN, BH_MATERIAL_PP = 0.05, 0.10, 15.0, 5.0
SHORT_HOLD_BARS = 4  # 15m bars: median hold under 1h with many trades = churn
LOW_WR_PCT = 35.0
LOSING_REASON_SHARE = 0.25
ENTRY_TABS = ("ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES")
REENTRY_TABS = ("REENTRY_WINDOWED", "REENTRY_ADAPTIVE")
AUGMENT_TABS = ("AUGMENT_TREND", "AUGMENT_RISK_SIZING")
EXIT_TABS = ("EXIT_STRUCTURAL", "EXIT_VELOCITY", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER")
GATE_TABS = ("ENTRY_CONFIRMATION_GATES", "GLOBAL_RISK_GATES")
FILTER_TOKENS = ("FILTER", "GATE", "BLOCK", "REQUIRE", "CONFIRM", "VETO", "GUARD", "MIN_", "THRESHOLD", "MAX_")
# = v15_pilot._ADAPT_FINAL_SOFTEN_EXCLUDE (BIBLE §58 final_safe) + NOLOSS: a bool safety gate is never flipped True->False
SAFETY_TOKENS = ("GUARD", "BLOCK", "HARD", "STOP", "KILL", "HEDGE", "STDEV_REJECT", "RISK", "NOLOSS", "NO_LOSS")


def metrics(res: dict | None, bh: float | None = None) -> dict:
    r = res or {}

    def _f(k):
        try:
            return float(r.get(k)) if r.get(k) is not None else None
        except Exception:
            return None
    b = _f("bh_pct")
    return {"gain": _f("gain_pct"), "trades": int(r.get("trades") or 0), "tim": _f("tim_pct"), "dd": _f("max_dd_pct"),
            "wr": _f("wr_pct"), "valid": bool(r.get("valid")), "reason": r.get("invalid_reason") or "",
            "bh": b if b is not None else bh, "fp": (r.get("behavior_fingerprint") or "")[:16]}


def compliant(m: dict) -> bool:
    return bool(m["valid"] and m["trades"] >= FLOOR_TRADES and m["tim"] is not None and TIM_MIN <= m["tim"] <= TIM_MAX
                and m["gain"] is not None and m["gain"] > 0)


def excess(m: dict) -> float:
    tim = m["tim"] if m["tim"] is not None else 100.0
    dd = m["dd"] if m["dd"] is not None else 100.0
    return max(0.0, tim - TIM_MAX) + max(0.0, dd - DD_MAX) + max(0.0, TIM_MIN - tim)


def adj_gain(m: dict) -> float:
    """30D gain with the B&H stretch bonus (never a gate, BIBLE §65) and a thin-sample penalty below 30 trades."""
    g = m["gain"] if m["gain"] is not None else -1e9
    bonus = 0.1 * max(0.0, g - m["bh"]) if m["bh"] is not None else 0.0
    return g + bonus - 0.05 * max(0, TARGET_TRADES - m["trades"])


def quality_key(m: dict, n_changes: int = 0) -> tuple:
    """Lexicographic: compliant > valid > least TIM/DD excess > reaches floor > adjusted gain > fewer changes."""
    return (compliant(m), m["valid"], -round(excess(m), 3), -max(0, FLOOR_TRADES - m["trades"]), round(adj_gain(m), 6), -n_changes)


def ledger_mix(res: dict | None) -> dict:
    led = (res or {}).get("ledger") or []
    closes = [t for t in led if isinstance(t, dict) and t.get("type") == "CLOSE"]
    out = {"closes": len(closes), "exit": {}, "entry": {}, "median_bars_held": None, "mean_pnl_pct": None}
    if not closes:
        return out
    holds = sorted(int(t.get("bars_held") or 0) for t in closes)
    out["median_bars_held"] = holds[len(holds) // 2]
    pn = [float(t.get("pnl_pct") or 0.0) for t in closes]
    out["mean_pnl_pct"] = sum(pn) / len(pn)
    for key, fld in (("exit", "exit_reason"), ("entry", "entry_reason")):
        agg = defaultdict(lambda: [0, 0.0, 0])
        for t in closes:
            reason = _reason_family(t.get(fld) or t.get("reason") or "?")
            a = agg[reason]
            a[0] += 1
            a[1] += float(t.get("pnl_pct") or 0.0)
            a[2] += 1 if float(t.get("pnl_pct") or 0.0) > 0 else 0
        out[key] = {k: {"n": v[0], "share": v[0] / len(closes), "mean_pnl_pct": v[1] / v[0], "wr_pct": 100.0 * v[2] / v[0]} for k, v in sorted(agg.items(), key=lambda x: -x[1][0])}
    return out


def _reason_family(reason: str) -> str:
    """'DAYTRADE_TARGET dc_15m_high -0.10% TARGET' -> 'DAYTRADE_TARGET'; keeps the leading token family."""
    s = str(reason).strip()
    for sep in (" ", "(", ":"):
        s = s.split(sep, 1)[0]
    return s[:48] or "?"


def diagnose(m: dict, mix: dict | None = None, cat_side: str | None = None) -> list:
    """Named faults, worst first. Every fault names the lever family that addresses it (ENCYCLOPEDIA.md §3)."""
    f = []
    mix = mix or {}
    edge = (m["gain"] / m["trades"]) if (m["gain"] is not None and m["trades"]) else None
    if not m["valid"]:
        f.append(("INVALID", m["reason"] or "?", "fix the gate that invalidates first (TIM/DD/floor)"))
    if m["trades"] < FLOOR_TRADES:
        f.append(("TOO_FEW_TRADES", f"{m['trades']}<{FLOOR_TRADES}", "SOFTEN filters/gates, open entry+reentry paths"))
    elif m["trades"] < TARGET_TRADES:
        f.append(("FEW_TRADES", f"{m['trades']}<{TARGET_TRADES}", "SOFTEN filters, add entries/reentries"))
    mh = mix.get("median_bars_held")
    p90 = P90_TRADES.get(cat_side or "", TOO_MANY_TRADES_30D)
    if (m["trades"] >= p90 and edge is not None and edge < CHURN_EDGE_PP) or m["trades"] > TOO_MANY_TRADES_30D or (mh is not None and mh < SHORT_HOLD_BARS and m["trades"] > 100):
        f.append(("TOO_MANY_TRADES", f"{m['trades']} trades (p90 {p90}) edge {edge if edge is None else round(edge, 3)}pp/trade median hold {mh} bars", "TIGHTEN entry filters; TARGET_DC_IMMEDIATE_REENTRY/HARDCODED_RALLY brakes; slower DAYTRADE_DC_TARGET_TF"))
    elif m["trades"] >= TARGET_TRADES and edge is not None and edge < LOW_EDGE_PP:
        f.append(("LOW_EDGE", f"{round(edge, 3)}pp/trade over {m['trades']} trades", "remove the smallest-|dgain| trade adders; fee drag"))
    if m["tim"] is not None and m["tim"] > TIM_MAX:
        f.append(("TIM_HIGH", f"{m['tim']:.1f}>{TIM_MAX:.0f}", "ADD exits (DC/WT/structural), reduce/profit-lock"))
    if m["tim"] is not None and m["tim"] < TIM_MIN:
        f.append(("TIM_LOW", f"{m['tim']:.1f}<{TIM_MIN:.0f}", "SOFTEN entry filters, remove premature exits, add reentry"))
    if m["dd"] is not None and m["dd"] > DD_MAX:
        f.append(("DD_HIGH", f"{m['dd']:.1f}>{DD_MAX:.0f}", "ADD loss exits/stops, reduce sizing/augments, HTF trend gates"))
    elif m["dd"] is not None and m["dd"] > DD_WARN:
        f.append(("DD_WARN", f"{m['dd']:.1f}>{DD_WARN:.0f} (365D fails mostly on DD)", "tighter DAYTRADE_DC_STOP_TF, VIGILANCE, smaller augments"))
    if m["gain"] is not None and m["gain"] <= 0:
        f.append(("GAIN_NEG", f"{m['gain']:+.2f}", "TREND/HTF entry gates, losing-exit replacement"))
    if m["gain"] is not None and m["bh"] is not None and m["gain"] < m["bh"] - BH_MATERIAL_PP:
        f.append(("BELOW_BH_MATERIAL", f"{m['gain']:+.2f}<bh {m['bh']:+.2f}", "remove premature exits/SELL_TOP, faster reentry after exit, augments on trend"))
    if m["wr"] is not None and m["trades"] >= FLOOR_TRADES and m["wr"] < LOW_WR_PCT:
        f.append(("LOW_WR", f"{m['wr']:.0f}%<{LOW_WR_PCT:.0f}%", "TIGHTEN entry confirmation, HTF direction"))
    for reason, a in (mix.get("exit") or {}).items():
        if a["share"] >= LOSING_REASON_SHARE and a["mean_pnl_pct"] < 0:
            f.append((f"LOSING_EXIT:{reason}", f"{a['share']*100:.0f}% of closes mean {a['mean_pnl_pct']:+.2f}%", "replace/disable this exit, add an earlier loss exit"))
    for reason, a in (mix.get("entry") or {}).items():
        if a["share"] >= LOSING_REASON_SHARE and a["mean_pnl_pct"] < 0:
            f.append((f"LOSING_ENTRY:{reason}", f"{a['share']*100:.0f}% of closes mean {a['mean_pnl_pct']:+.2f}%", "filter this entry path (confirmation/HTF gates)"))
    return f


# never applied by the repair (measured only): synthetic/fabrication-class switches (BIBLE §19; np.arange%2 pattern,
# v15_pilot._credible_baseline skips it too) — extend when ENCYCLOPEDIA §9 flags a new suspect
NEVER_APPLY = frozenset({"SIMPLE_PRICE_GT0_ENABLED", "STOCKS_RTH_ONLY_ENABLED"})  # RTH: stocks never trade outside market hours (USER 2026-10-06)
# structural / normalisation fields (BIBLE §31 EXCLUDE): never candidates (MODE=crypto on a stock is not a strategy lever)
STRUCTURAL = frozenset({"MODE", "BASE_TF", "START_POSITION_SIZE", "MIN_POSITION_SIZE", "MAX_ORDER_VALUE", "MAX_POSITION_SIZE", "ATR_PARITY_EQUITY_BASE_USD", "BASE_PATH"})
_STRUCT_SUFFIX = ("_PATH", "_FILE", "_DIR", "_URL", "_HOST", "_PORT", "_TOKEN", "_KEY", "_SECRET", "_CACHE")


def _structural(c: dict) -> bool:
    return any(k in STRUCTURAL or str(k).upper().endswith(_STRUCT_SUFFIX) for k in (c.get("ov") or {}))


def _is_filter(c: dict) -> bool:
    s = c["switch"].upper()
    return c.get("orange") or c["tab"] in GATE_TABS or any(t in s for t in FILTER_TOKENS)


def _is_safety(c: dict) -> bool:
    s = c["switch"].upper()
    return any(t in s for t in SAFETY_TOKENS)


def _forbidden(c: dict, state: dict | None = None, defaults: dict | None = None) -> str:
    """'' when the candidate may be APPLIED by the repair (it may always be measured)."""
    if c.get("revert"):
        return ""
    if c.get("blocked"):
        return c["blocked"]
    if any(k in NEVER_APPLY for k in (c.get("ov") or {})) or c.get("switch") in NEVER_APPLY:
        return "NEVER_APPLY: fabrication-class switch (BIBLE §19)"
    if any(t in str(c.get("switch", "")).upper() for t in ("SIZING", "SIZE_MULT", "POSITION_SIZE", "ORDER_VALUE")):
        return "SIZING_FALSE_ALPHA: notional, not edge (v15_pilot SIZING_FALSE_ALPHA)"
    for k, v in (c.get("ov") or {}).items():
        if str(k).startswith("ABLATION_DISABLE_") and v is True:
            return "ABLATION_TRUE_P0"
        if state is not None and v is False and any(t in str(k).upper() for t in SAFETY_TOKENS):
            cur = state.get(k, (defaults or {}).get(k))
            if cur is True or str(cur).strip().lower() == "true":
                return "SAFETY_LOOSEN: bool safety gate True->False (BIBLE §58 final_safe)"
    return ""


class _Search:
    def __init__(self, ctx: dict):
        self.ctx = ctx
        self.defaults = ctx["defaults"]
        self.sanitize = ctx["sanitize"]
        self.same = ctx["same_val"]
        self.bh = ctx.get("bh")
        self.deadline = float(ctx["deadline"])
        self.origin = dict(ctx["base_overrides"])
        self.memo: dict = {}
        self.n_evals = 0
        self.hall: list = []  # (key, ov, m, changes)
        self.in_hall: set = set()
        self.hall_prov: dict = dict(ctx.get("resume_hall_prov") or {})  # USER 2026-10-07: trace-only md5(set)->phase that discovered it
        self._ckpt_prov_n = 0
        self.cands = [c for c in ctx["candidates"] if c.get("ov") and not _structural(c)]
        for i, c in enumerate(self.cands):
            c["_id"] = i
        self.last: dict = {}  # cand id -> quality key of its latest single-move eval (shortlist ranking)
        self.chunk = int(ctx.get("chunk", 96))
        self.dual = False
        # USER 2026-10-07: chunk-level memo checkpoint + resume — a killed diagnose replays memo'd evals
        # (pure function of the override set) instead of recomputing. Resume maps are md5(set)->metrics.
        self._resume_memo = dict(ctx.get("resume_memo") or {})
        self._resume_m365 = dict(ctx.get("resume_m365") or {})
        self._ckpt_memo_n = 0
        self._ckpt_m365_n = 0
        if not hasattr(self, "_m365"):
            self._m365 = {}

    def _mk(self, k) -> str:
        return hashlib.md5(json.dumps(k).encode()).hexdigest()

    def _flush_ckpt(self):
        ck = self.ctx.get("checkpoint")
        if ck is None:
            return
        try:
            _mk = self._mk
            _mitems = list(self.memo.items())
            _new_memo = {_mk(k): m for k, m in _mitems[self._ckpt_memo_n:]}
            _bitems = list(self._m365.items())
            _new_m365 = {_mk(k): [m, ok] for k, (m, ok) in _bitems[self._ckpt_m365_n:]}
            _pitems = list(self.hall_prov.items())
            _new_prov = dict(_pitems[self._ckpt_prov_n:])
            self._ckpt_memo_n, self._ckpt_m365_n, self._ckpt_prov_n = len(_mitems), len(_bitems), len(_pitems)
            if _new_memo or _new_m365 or _new_prov:
                ck({"memo": _new_memo, "m365": _new_m365, "hall_prov": _new_prov, "n_evals": self.n_evals})
        except V15Shutdown:
            raise
        except Exception:
            pass

    def _shutdown_hit(self) -> bool:
        sr = self.ctx.get("shutdown_requested")
        try:
            return bool(sr and sr())
        except Exception:
            return False

    def prefetch_365(self, results: list, phase: str = ""):
        """USER 2026-10-07: the 365D dual-window scoring was SEQUENTIAL (160 cands x 1.5s = 4 min per
        screen) — batch every un-memo'd set through eval_many_365 (fork pool over the in-RAM 365D slice)
        so dual_key() below hits memo. Values identical to serial m365 (same evaluator, memoised)."""
        if not self.ctx.get("eval_365"):
            return
        missing = []
        for m, ov, c in results:
            k = self._k(self.sanitize(ov))
            if k in self._m365:
                continue
            r = self._resume_m365.get(self._mk(k))
            if r is not None:
                self._m365[k] = (r[0], bool(r[1]))
            else:
                missing.append((k, self.sanitize(ov)))
        if not missing:
            return
        batched = self.ctx.get("eval_many_365")
        if batched is None:  # non-pilot callers keep today's exact serial behavior
            for k, ov in missing:
                r, span = self.ctx["eval_365"](ov)
                ok, why = self.ctx["qualifies_365"](r, span) if r else (False, ["no result"])
                self._m365[k] = (metrics(r) if r else None, ok)
            self._flush_ckpt()
            return
        ch365 = int(self.ctx.get("chunk365", 24))
        _plog = self.ctx.get("log") or (lambda m: None)
        try: _plog(f"[DIAG-365] {phase} prefetch {len(missing)} sets ({len(results)} screened, {len(results) - len(missing)} memo)")
        except Exception: pass
        for i in range(0, len(missing), ch365):
            if self._shutdown_hit():
                self._flush_ckpt()
                raise V15Shutdown("shutdown in 365D prefetch")
            if self.left() <= 0:
                break
            part = missing[i:i + ch365]
            try:
                res = batched([(ov, k) for k, ov in part], self.deadline)
            except V15Shutdown:
                raise
            except Exception:
                res = [(None, None)] * len(part)
            for (k, ov), (r, span) in zip(part, res):
                try:
                    ok, why = self.ctx["qualifies_365"](r, span) if r else (False, ["no result"])
                    self._m365[k] = (metrics(r) if r else None, ok)
                except Exception:
                    self._m365[k] = (None, False)
        self._flush_ckpt()

    def left(self) -> float:
        return self.deadline - time.time()

    def m365(self, ov: dict):
        """memoised 365D metrics + qualified flag (None when no 365D evaluator)."""
        if not self.ctx.get("eval_365"):
            return None
        k = self._k(self.sanitize(ov))
        if k not in self._m365:
            rr = self._resume_m365.get(self._mk(k))  # USER 2026-10-07: replay, never recompute
            if rr is not None:
                self._m365[k] = (rr[0], bool(rr[1]))
            else:
                r, span = self.ctx["eval_365"](self.sanitize(ov))
                ok, why = self.ctx["qualifies_365"](r, span) if r else (False, ["no result"])
                self._m365[k] = (metrics(r) if r else None, ok)
                self._flush_ckpt()
        return self._m365[k]

    def dual_key(self, ov: dict, m30: dict) -> tuple:
        """USER 2026-10-06 (30D rally ignores stops/filters, 365D punishes): both windows judge.
        (365 qualified & 30 compliant, 365 qualified, 30 compliant, 365 valid, -365 excess, worst-window gain)."""
        r = self.m365(ov)
        if r is None:
            return quality_key(m30, len(self.changes(ov)))
        m365, q = r
        return _key365(m30, m365, q)

    def _k(self, ov: dict):
        return tuple(sorted((k, str(v)) for k, v in ov.items()))

    def changes(self, ov: dict) -> list:
        keys = set(ov) | set(self.origin)
        return sorted(k for k in keys if not self.same(ov.get(k, self.defaults.get(k)), self.origin.get(k, self.defaults.get(k))))

    def applies(self, state: dict, c: dict) -> bool:
        return not all(self.same(state.get(k, self.defaults.get(k)), v) for k, v in c["ov"].items())

    def evaluate(self, items: list, phase: str, cum_before) -> list:
        """items: [(label, ov, cand, allowed)] -> [(m|None, ov, cand)] ; one real eval per new set, memoised.
        Only sets reached through ALLOWED moves enter the hall (finalist pool)."""
        todo, out, seen = [], [], set()
        items = [(lab, self.sanitize(ov), c, a) for lab, ov, c, a in items]
        for label, ov, c, allowed in items:
            k = self._k(ov)
            if k in self.memo or k in seen:
                continue
            rm = self._resume_memo.get(self._mk(k))  # USER 2026-10-07: replay, never recompute
            if rm is not None:
                self.memo[k] = rm
                continue
            seen.add(k)
            todo.append((label, ov, c, k))
        for i in range(0, len(todo), self.chunk):  # chunked: the budget is checked between chunks, never overrun by one giant wave
            if self._shutdown_hit():
                self._flush_ckpt()
                raise V15Shutdown("shutdown in diagnose eval")
            if self.left() <= 0:
                break
            part = todo[i:i + self.chunk]
            res = self.ctx["eval_many"]([(lab, ov, c) for lab, ov, c, _ in part], phase, cum_before, self.deadline)
            for (lab, ov, c, k), (r, err) in zip(part, res):
                self.n_evals += 1
                if r is not None:
                    self.memo[k] = metrics(r, self.bh)
            self._flush_ckpt()
        for label, ov, c, allowed in items:
            k = self._k(ov)
            m = self.memo.get(k)
            if m is not None and allowed and k not in self.in_hall:
                self.in_hall.add(k)
                ch = self.changes(ov)
                self.hall.append((quality_key(m, len(ch)), ov, m, ch))
                self.hall_prov.setdefault(self._mk(k), phase)
            if m is not None and c is not None and "_id" in c:
                self.last[c["_id"]] = quality_key(m, 0)
            out.append((m, ov, c))
        return out

    def shortlist(self, pred=None, k: int = 160) -> set:
        """ids of the k most promising candidates by their latest measured single-move key (unmeasured first)."""
        ids = [c["_id"] for c in self.cands if pred is None or pred(c)]
        ids.sort(key=lambda i: (i in self.last, self.last.get(i, ())), reverse=False)
        unmeasured = [i for i in ids if i not in self.last]
        measured = sorted((i for i in ids if i in self.last), key=lambda i: self.last[i], reverse=True)
        return set(unmeasured[:k // 4] + measured[:k])

    def screen(self, state: dict, state_m: dict, phase: str, pred=None, only: set | None = None) -> list:
        items = []
        for c in self.cands:
            if pred is not None and not pred(c):
                continue
            if only is not None and c["_id"] not in only:
                continue
            if not self.applies(state, c):
                continue
            v = dict(state)
            v.update(c["ov"])
            items.append((f"{c['switch']}={c['cand']}", v, c, not _forbidden(c, state, self.defaults)))
        for k in self.changes(state):  # reverts of every change vs the original set
            v = dict(state)
            if k in self.origin:
                v[k] = self.origin[k]
            else:
                v.pop(k, None)
            items.append((f"REVERT:{k}", v, {"switch": k, "cand": "REVERT", "tab": "REVERT", "row": None, "ov": {k: self.origin.get(k, self.defaults.get(k))}, "revert": True}, True))
        out = self.evaluate(items, phase, state_m["gain"] if state_m else None)
        try: (self.ctx.get("log") or (lambda m: None))(f"[DIAG-SCREEN] {phase} cands={len(items)} evals={self.n_evals} left={self.left():.0f}s")
        except Exception: pass
        self.prefetch_365(out, phase)  # USER 2026-10-07: batch the dual-window 365D scoring (was serial)
        return out


def _delta(m: dict, base: dict) -> dict:
    def d(k):
        return (m[k] - base[k]) if (m.get(k) is not None and base.get(k) is not None) else None
    return {"gain": d("gain"), "trades": m["trades"] - base["trades"], "tim": d("tim"), "dd": d("dd")}


def _live(results: list, base_m: dict, tabs: tuple) -> tuple:
    """(#candidates in tabs that move the ledger, #candidates in tabs measured)."""
    n = live = 0
    for m, _ov, c in results:
        if c.get("revert") or c["tab"] not in tabs or m is None:
            continue
        n += 1
        if m["fp"] != base_m["fp"] or m["trades"] != base_m["trades"] or abs((m["gain"] or 0) - (base_m["gain"] or 0)) > 1e-9:
            live += 1
    return live, n


def run(ctx: dict) -> dict:
    """ctx: defaults, sanitize(ov)->ov, same_val(a,b), candidates[{tab,row,switch,cand,ov,orange,blocked}],
    base_overrides, base_res, bh, deadline (epoch), eval_many(items, phase, cum_before, deadline)->[(res,err)],
    eval_ledger(ov)->res|None, eval_365(ov)->(res|None, span_days), qualifies_365(res, span)->(ok, reasons),
    log(msg), touch(msg). Returns the report (accepted flag + best overrides)."""
    t0 = time.time()
    S = _Search(ctx)
    log = ctx.get("log") or (lambda m: None)
    touch = ctx.get("touch") or (lambda m: None)
    origin = dict(S.sanitize(dict(ctx["base_overrides"])))
    base_m = metrics(ctx["base_res"], S.bh)
    S.memo[S._k(origin)] = base_m
    S.in_hall.add(S._k(origin))
    S.hall.append((quality_key(base_m, 0), origin, base_m, []))
    rep = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0)), "n_candidates": len(S.cands), "before": base_m, "steps": [], "liveness": {}, "finalists": [], "accepted": False}
    mix0 = ledger_mix(ctx["eval_ledger"](origin) if ctx.get("eval_ledger") else None)
    rep["mix_before"] = mix0
    rep["diagnosis_before"] = diagnose(base_m, mix0, ctx.get("cat_side"))
    # USER 2026-10-06: a 30D rally never needs stops/filters, so 365D fails on DD — know the origin's 365D verdict up front
    o365 = None
    if ctx.get("eval_365"):
        touch("diag-365-origin")
        r365o, span_o = ctx["eval_365"](origin)
        q_o, why_o = ctx["qualifies_365"](r365o, span_o)
        o365 = {"m365": metrics(r365o) if r365o else None, "q365": q_o, "why365": why_o, "span": span_o}
        rep["origin_365"] = {"m365": o365["m365"], "q365": q_o, "why365": why_o}
    log(f"[DIAG] before gain={base_m['gain']} bh={base_m['bh']} tr={base_m['trades']} TIM={base_m['tim']} DD={base_m['dd']} WR={base_m['wr']} valid={base_m['valid']} faults={[x[0] for x in rep['diagnosis_before']]}")
    # ── screen 0: every candidate vs the final set = this sym_side's lever map ──
    # With a ledger evaluator (pilot fork pool) screen 0 IS the trade autopsy: every TEMPLATE row is related to the bad
    # trades it fixes (USER 2026-10-06: "relate every losing trade or exit before top to a switch …").
    _ra = ctx.get("resume_autopsy")
    if _ra and _ra.get("row_recommendations") is not None:
        rep["autopsy"] = _ra.get("summary")
        rep["row_recommendations"] = _ra.get("row_recommendations")
        rep["trade_fixes"] = _ra.get("trade_fixes")
        rep["missing_functions"] = _ra.get("missing_functions")
        log(f"[DIAG-AUTOPSY] skipped — resumed {len(rep['row_recommendations'] or [])} recommendations, no recompute")
    elif ctx.get("eval_many_ledger") and ctx.get("close") is not None:
        autopsy_screen(S, ctx, origin, base_m, rep, log)
    scr0 = S.screen(origin, base_m, "DIAG_SCREEN")
    rep["lever_map"] = [{"tab": c["tab"], "row": c.get("row"), "switch": c["switch"], "cand": str(c["cand"]), **({"d": _delta(m, base_m), "valid": m["valid"]} if m else {"d": None, "valid": None}), "blocked": _forbidden(c, origin, S.defaults)} for m, _ov, c in scr0 if not c.get("revert")]
    act_tabs = ENTRY_TABS + REENTRY_TABS + AUGMENT_TABS
    rep["liveness"]["before"] = _live(scr0, base_m, act_tabs)
    touch("diag-screen")
    # ── 0b. SURGICAL: apply the autopsy's recommended TEMPLATE rows greedily, each engine-verified ──
    state, state_m, applied = dict(origin), base_m, []
    if rep.get("row_recommendations"):
        state, state_m = surgical(S, ctx, state, state_m, rep, log, touch)
    # ── 1. SOFTEN: maximise LIVE entry/reentry/augment rows, then trades, gain secondary ──
    soft_target = min(TOO_MANY_TRADES_30D, max(60, 3 * base_m["trades"]))
    budget = S.deadline - t0
    reserve = min(max(float(ctx.get("verify_reserve_s", 120)), 0.15 * budget), 0.35 * budget)  # 365D verify + final screen
    S.dual = bool(o365 is not None and not o365["q365"] and ctx.get("dual_window", True))
    if S.dual:
        S._m365 = {S._k(origin): (o365["m365"], o365["q365"])}
        log(f"[DIAG] origin fails 365D ({'; '.join(o365['why365'])}) -> DUAL-WINDOW search (30D+365D judge every step)")
    if o365 is not None and not o365["q365"]:
        reserve = 0.25 * budget  # REPAIR_365 tail; the main search is already dual-window
    S.deadline -= reserve
    work = S.deadline - t0
    adders = {c["_id"] for m, _ov, c in scr0 if m is not None and not c.get("revert") and m["trades"] > base_m["trades"]}
    rep["n_trade_adders"] = len(adders)
    scr = scr0
    for rnd in range(int(ctx.get("soften_rounds", 8))):
        if S.left() < 0.55 * work or state_m["trades"] >= soft_target:
            break
        live_now, n_act = _live(scr, state_m, act_tabs)
        best = None
        for m, ov, c in scr:
            if m is None or c.get("revert") or _forbidden(c, state, S.defaults) or _is_safety(c):
                continue
            if not (_is_filter(c) or c["tab"] in act_tabs):
                continue
            # TIM may rise while softening (the ADD phase brings the exits); DD is risk, capped
            if m["trades"] <= state_m["trades"] or (m["dd"] or 0) > max(45.0, state_m["dd"] or 0):
                continue
            k = (m["trades"] - state_m["trades"], m["gain"] or -1e9)
            if best is None or k > best[0]:
                best = (k, m, ov, c)
        if best is None:
            break
        _, m, ov, c = best
        state, state_m = ov, m
        applied.append(c)
        adders.discard(c["_id"])
        act_ids = {x["_id"] for x in S.cands if x["tab"] in act_tabs}
        scr = S.screen(state, state_m, "SOFTEN", only=adders | (act_ids if S.left() > 0.7 * work else S.shortlist(lambda x: x["tab"] in act_tabs, 120)))
        live_after, _ = _live(scr, state_m, act_tabs)
        rep["steps"].append({"phase": "SOFTEN", "round": rnd, "applied": f"{c['switch']}={c['cand']}", "tab": c["tab"], "row": c.get("row"), **{k2: m[k2] for k2 in ("gain", "trades", "tim", "dd", "wr", "valid")}, "live": f"{live_after}/{n_act}", "evals": S.n_evals})
        log(f"[DIAG-SOFTEN] r{rnd} {c['switch']}={c['cand']} -> tr={m['trades']} gain={m['gain']} TIM={m['tim']} live {live_now}->{live_after}/{n_act}")
        touch(f"diag-soften-{rnd}")
    rep["liveness"]["after_soften"] = _live(scr, state_m, act_tabs)
    rep["softened_state_metrics"] = state_m

    # ── 2-4. ADD / TIGHTEN / POLISH: beam over quality key ──
    def beam(states: list, phase: str, pred, width: int, rounds: int, frac_stop: float, k: int = 160):
        best_key = max((S.dual_key(ov, m) if S.dual else quality_key(m, len(S.changes(ov)))) for ov, m in states)
        stale = 0
        for rnd in range(rounds):
            if S.left() < frac_stop * work:
                break
            children = []
            only = S.shortlist(pred, k)
            for ov, m in states:
                for cm, cov, c in S.screen(ov, m, phase, pred, only=only):
                    if cm is None or _forbidden(c, ov, S.defaults):
                        continue
                    children.append((quality_key(cm, len(S.changes(cov))), cov, cm, c))
            if not children:
                break
            children.sort(key=lambda x: x[0], reverse=True)
            if S.dual and children:
                top = children[:max(2 * width, 4)]
                top = [(S.dual_key(cov, cm), cov, cm, c) for _k, cov, cm, c in top]
                top.sort(key=lambda x: x[0], reverse=True)
                children = top
            nxt, seen = [], set()
            for key, cov, cm, c in children:
                fk = S._k(cov)
                if fk in seen:
                    continue
                seen.add(fk)
                nxt.append((key, cov, cm, c))
                if len(nxt) >= width:
                    break
            top = nxt[0]
            rep["steps"].append({"phase": phase, "round": rnd, "applied": f"{top[3]['switch']}={top[3]['cand']}", "tab": top[3]["tab"], "row": top[3].get("row"), **{k2: top[2][k2] for k2 in ("gain", "trades", "tim", "dd", "wr", "valid")}, "key": list(top[0]), "evals": S.n_evals})
            log(f"[DIAG-{phase}] r{rnd} best {top[3]['switch']}={top[3]['cand']} gain={top[2]['gain']} tr={top[2]['trades']} TIM={top[2]['tim']} DD={top[2]['dd']} compliant={compliant(top[2])}")
            touch(f"diag-{phase.lower()}-{rnd}")
            if top[0] > best_key:
                best_key, stale = top[0], 0
            else:
                stale += 1
                if stale >= 2:
                    break
            states = [(cov, cm) for _, cov, cm, _ in nxt]
        return states

    st = beam([(state, state_m)], "ADD", None, int(ctx.get("beam_width", 3)), int(ctx.get("add_rounds", 10)), 0.30)
    st = beam(st, "TIGHTEN", lambda c: _is_filter(c) or bool(c.get("revert")), int(ctx.get("beam_width", 3)), int(ctx.get("tighten_rounds", 8)), 0.15)
    hall_best = max(S.hall, key=lambda x: x[0])
    beam([(hall_best[1], hall_best[2])], "POLISH", None, 1, int(ctx.get("polish_rounds", 6)), 0.0, k=400)
    S.deadline += reserve  # verify + final screen may use the reserve

    # ── 5. VERIFY: distinct top compliant finalists + origin on 365D ──
    S.hall.sort(key=lambda x: x[0], reverse=True)
    fins, seenfp = [], {base_m["fp"]} if base_m["fp"] else set()
    for key, ov, m, ch in S.hall:
        if len(fins) >= int(ctx.get("n_finalists", 3)):
            break
        if not compliant(m) or not ch:
            continue
        if m["fp"] and m["fp"] in seenfp:
            continue
        seenfp.add(m["fp"])
        fins.append((key, ov, m, ch))
    if not fins and int(ctx.get("n_finalists", 3)) > 0:  # nothing compliant: the best state by quality key still competes if it beats the origin (APO_LONG proof 2026-10-06)
        for key, ov, m, ch in S.hall:
            if ch and m["valid"] and key > quality_key(base_m, 0) and (not m["fp"] or m["fp"] not in seenfp):
                fins.append((key, ov, m, ch))
                break
    pool = [((quality_key(base_m, 0)), origin, base_m, [])] + fins
    verdicts = []
    for key, ov, m, ch in pool:
        r365, span = (None, None)
        q365, why365 = False, ["not evaluated"]
        if not ch and o365 is not None:
            verdicts.append({"ov": ov, "m": m, "changes": ch, **o365})
            continue
        if ctx.get("eval_365") and S.left() > 0:
            touch("diag-365")
            r365, span = ctx["eval_365"](ov)
            q365, why365 = ctx["qualifies_365"](r365, span)
        m365 = metrics(r365) if r365 else None
        verdicts.append({"ov": ov, "m": m, "changes": ch, "m365": m365, "q365": q365, "why365": why365, "span": span})
        log(f"[DIAG-365] {'ORIGIN' if not ch else '%d changes' % len(ch)} 30D gain={m['gain']} compliant={compliant(m)} 365D gain={(m365 or {}).get('gain')} tr={(m365 or {}).get('trades')} -> {'PASS' if q365 else '; '.join(why365)}")

    if ctx.get("eval_365") and not any(v["q365"] for v in verdicts) and S.left() > 30:
        for v in repair_365(S, ctx, verdicts, rep, log, touch):
            verdicts.append(v)

    def sel_key(v):
        return (compliant(v["m"]), bool(v["q365"]), round(adj_gain(v["m"]), 6), -len(v["changes"]))
    verdicts_sorted = sorted(verdicts, key=sel_key, reverse=True)
    win, orig = verdicts_sorted[0], verdicts[0]
    min_gain = float(ctx.get("min_improve_pp", 0.5))
    status_up = (compliant(win["m"]), bool(win["q365"])) > (compliant(orig["m"]), bool(orig["q365"]))
    gain_up = (win["m"]["gain"] or -1e9) >= (orig["m"]["gain"] or -1e9) + min_gain and (compliant(win["m"]), bool(win["q365"])) >= (compliant(orig["m"]), bool(orig["q365"]))
    rep["finalists"] = [{"changes": v["changes"], "m": v["m"], "m365": v["m365"], "q365": v["q365"], "why365": v["why365"], "span": v["span"]} for v in verdicts]
    rep["accepted"] = bool(win is not orig and (status_up or gain_up))
    rep["best_overrides"] = dict(win["ov"]) if rep["accepted"] else dict(origin)
    rep["after"] = win["m"] if rep["accepted"] else base_m
    rep["changes"] = win["changes"] if rep["accepted"] else []
    rep["accept_reason"] = ("status_up" if status_up else "gain_up") if rep["accepted"] else "origin best (no finalist beats it on (30D compliant, 365D qualified, +%.2fpp))" % min_gain
    mix1 = ledger_mix(ctx["eval_ledger"](rep["best_overrides"])) if (rep["accepted"] and ctx.get("eval_ledger")) else mix0
    rep["mix_after"] = mix1
    rep["diagnosis_after"] = diagnose(rep["after"], mix1, ctx.get("cat_side"))
    # ── final lever map vs the chosen set + gap report (what no existing switch could fix) ──
    fin_scr = S.screen(rep["best_overrides"], rep["after"], "DIAG_FINAL_SCREEN", only=S.shortlist(None, 600)) if S.left() > 0 else []
    rep["liveness"]["final"] = _live(fin_scr, rep["after"], act_tabs) if fin_scr else None
    rep["gaps"] = gaps(rep["diagnosis_after"], fin_scr, rep["after"])
    rep["n_evals"] = S.n_evals
    rep["secs"] = round(time.time() - t0, 1)
    log(f"[DIAG] done {S.n_evals} evals {rep['secs']}s accepted={rep['accepted']} ({rep['accept_reason']}) gain {base_m['gain']} -> {rep['after']['gain']} faults_after={[x[0] for x in rep['diagnosis_after']]} gaps={len(rep['gaps'])}")
    return rep


def autopsy_screen(S, ctx: dict, origin: dict, base_m: dict, rep: dict, log) -> None:
    """Ledger screen of every applicable candidate vs the origin set; fills S.memo (so the normal screen 0 is free),
    rep['row_recommendations'] (per TEMPLATE row: losers/premature/augments fixed, pp saved/hurt, real Δgain) and
    rep['trade_fixes'] (per bad trade: the clean fixing rows)."""
    from tools import v15_trade_autopsy as TA
    is_long = bool(ctx.get("is_long", True))
    close = ctx["close"]
    res0 = ctx["eval_many_ledger"]([("AUTOPSY_BASE", origin, None)], "AUTOPSY_BASE", base_m["gain"], S.deadline)[0][0]
    if not res0:
        return
    base = TA.classify(res0["_rows"], close, is_long)
    moves = TA.missed_moves(base, close, is_long)
    items = []
    for c in S.cands:
        if not S.applies(origin, c) or _forbidden(c, origin, S.defaults) or any(t in c["switch"].upper() for t in ("SIZE_MULT", "SIZING", "POSITION_SIZE")):
            continue
        v = dict(origin)
        v.update(c["ov"])
        items.append((f"{c['switch']}={c['cand']}", S.sanitize(v), c))
    attrs, cards, meta = {}, {}, {}
    for i in range(0, len(items), S.chunk):
        if S._shutdown_hit():
            S._flush_ckpt()
            raise V15Shutdown("shutdown in autopsy")
        if S.left() < 10:
            break
        part = items[i:i + S.chunk]
        outs = ctx["eval_many_ledger"](part, "AUTOPSY_SCREEN", base_m["gain"], S.deadline)
        for (lab, ov, c), (r, err) in zip(part, outs):
            if not r:
                continue
            S.n_evals += 1
            m = metrics(r, S.bh)
            S.memo[S._k(ov)] = m
            S.last[c["_id"]] = quality_key(m, 0)
            a = TA.attribute(base, r["_rows"], moves)
            if not a["eff"] and not a["added"]:
                continue
            attrs[lab] = a
            cards[lab] = {**TA.scorecard(base, a), "d_gain": (m["gain"] or 0.0) - (base_m["gain"] or 0.0), "valid": m["valid"], "d_trades": m["trades"] - base_m["trades"]}
            meta[lab] = c
        S._flush_ckpt()
    recs = TA.recommendations(base, attrs, cards, meta)
    clean = {lab: a for lab, a in attrs.items() if cards[lab]["valid"] and cards[lab]["d_gain"] >= -1e-9}
    fixes = TA.fixes_per_trade(base, clean)
    nbad = {k: sum(1 for t in base if k in t["cls"]) for k in ("LOSER", "PREMATURE_EXIT", "GIVEBACK", "MISSED_AUGMENT")}
    rep["autopsy"] = {"n_trades": len(base), "bad": nbad, "fixed_clean": {k: sum(1 for t in base if k in t["cls"] and t["be"] in fixes) for k in nbad},
                      "missed_moves": len(moves), "n_screened": len(items), "n_effective": len(attrs)}
    rep["row_recommendations"] = recs[:200]
    rep["trade_fixes"] = [{"be": t["be"], "bx": t["bx"], "pnl_pp": round(t["pnl"], 4), "cls": t["cls"], "entry_reason": t["entry_reason"][:40], "exit_reason": t["exit_reason"][:40],
                           "fixes": [(l, k, v, cards[l]["d_gain"]) for l, k, v in sorted(fixes.get(t["be"], []), key=lambda x: -cards[x[0]]["d_gain"])[:4]]} for t in base if t["cls"]]
    unfixed_l = {t["be"] for t in base if "LOSER" in t["cls"] and t["be"] not in fixes}
    unfixed_p = {t["be"] for t in base if "PREMATURE_EXIT" in t["cls"] and t["be"] not in fixes}
    npz = ctx.get("npz") or {}
    rep["missing_functions"] = TA.missing_functions(base, unfixed_l, npz, len(close), "entry") + TA.missing_functions(base, unfixed_p, npz, len(close), "exit")
    log(f"[DIAG-AUTOPSY] trades={len(base)} bad={nbad} fixed_clean={rep['autopsy']['fixed_clean']} rows_effective={len(attrs)}/{len(items)} top={[(r['switch'] + '=' + r['cand'], r['real_d_gain']) for r in recs[:3]]}")
    try:  # USER 2026-10-07: persist the autopsy verdict so a relaunch skips the 10-20 min ledger re-grind (S1 reap loop)
        _ck = ctx.get("checkpoint")
        if _ck is not None:
            _ck({"autopsy": {"summary": rep.get("autopsy"), "row_recommendations": rep.get("row_recommendations"), "trade_fixes": rep.get("trade_fixes"), "missing_functions": rep.get("missing_functions"), "origin_key": hashlib.md5(json.dumps(sorted((k, str(v)) for k, v in origin.items())).encode()).hexdigest()}})
    except V15Shutdown:
        raise
    except Exception:
        pass


def surgical(S, ctx: dict, state: dict, state_m: dict, rep: dict, log, touch, max_k: int = 12) -> tuple:
    """Apply recommended rows (real Δgain > 0 first, then net_pp) cumulatively; keep each only if the quality key improves."""
    recs = [r for r in rep["row_recommendations"] if r.get("valid") and (r["real_d_gain"] > 0 or r["net_pp"] > 0)]
    by_lab = {f"{c['switch']}={c['cand']}": c for c in S.cands}
    k = 0
    for r in recs:
        if k >= max_k or S.left() < 10:
            break
        c = by_lab.get(f"{r['switch']}={r['cand']}")
        if c is None or not S.applies(state, c) or _forbidden(c, state, S.defaults):
            continue
        v = dict(state)
        v.update(c["ov"])
        m, ov, _ = S.evaluate([(f"SURGICAL:{r['switch']}={r['cand']}", v, c, True)], "SURGICAL", state_m["gain"])[0]
        if m is None:
            continue
        better = (S.dual_key(ov, m) > S.dual_key(state, state_m)) if S.dual else (quality_key(m, len(S.changes(ov))) > quality_key(state_m, len(S.changes(state))))
        if better:
            state, state_m = ov, m
            k += 1
            rep["steps"].append({"phase": "SURGICAL", "round": k, "applied": f"{r['switch']}={r['cand']}", "tab": r["tab"], "row": r.get("row"), **{k2: m[k2] for k2 in ("gain", "trades", "tim", "dd", "wr", "valid")}, "fixes": f"L{r['losers_fixed']} P{r['premature_fixed']} A{r['aug_fixed']}", "evals": S.n_evals})
            log(f"[DIAG-SURGICAL] +{r['switch']}={r['cand']} (losers {r['losers_fixed']} premature {r['premature_fixed']}) -> gain={m['gain']} tr={m['trades']} TIM={m['tim']}")
            touch(f"diag-surgical-{k}")
    return state, state_m


DD_TOKENS = ("STOP", "TRAIL", "DC_HARD", "VIGILANCE", "NEWBORN", "LOSS", "GUARD", "DD_", "DRAWDOWN", "EXIT", "HTF", "TREND", "REGIME", "VETO", "FILTER", "GATE")


def _key365(m30: dict, m365: dict | None, q365: bool) -> tuple:
    if m365 is None:
        return (False, False, compliant(m30), False, -1e9, -1e9)
    worst = min(m30["gain"] if m30["gain"] is not None else -1e9, m365["gain"] if m365["gain"] is not None else -1e9)
    return (bool(q365) and compliant(m30), bool(q365), compliant(m30), m365["valid"], -round(excess(m365), 3), round(worst, 6))


def repair_365(S, ctx: dict, verdicts: list, rep: dict, log, touch, steps: int = 5, k: int = 30) -> list:
    """REPAIR_365 (USER 2026-10-06): the 30D window (often a rally) never needed stops/filters, so 365D fails (mostly DD).
    From the best 30D state: screen stop/exit/filter/gate candidates on 30D (pool, cheap), keep those whose 30D cost is
    tolerable (valid, gain >= start - max(2pp, 50%)), evaluate the k cheapest on the 365D slice in RAM (serial), apply the
    best by (365 qualified & 30 compliant, 365 qualified, 30 compliant, 365 valid, -365 TIM/DD excess, worst-window gain).
    Returns new verdict dicts (same schema as VERIFY)."""
    def pred(c):
        return c["tab"] in EXIT_TABS or c["tab"] == "GLOBAL_RISK_GATES" or _is_filter(c) or any(t in c["switch"].upper() for t in DD_TOKENS)
    start = max(verdicts, key=lambda v: (compliant(v["m"]), adj_gain(v["m"])))
    state, m30, m365, q = dict(start["ov"]), start["m"], start.get("m365"), bool(start.get("q365"))
    cur = _key365(m30, m365, q)
    out, t_eval = [], 2.0
    for step in range(steps):
        if S.left() < 30:
            break
        tol = max(2.0, 0.5 * abs(m30["gain"] or 0.0))
        scr = S.screen(state, m30, "REPAIR365_SCREEN", pred)
        cands = [(cm, cov, c) for cm, cov, c in scr if cm is not None and not _forbidden(c, state, S.defaults) and cm["valid"] and (cm["gain"] if cm["gain"] is not None else -1e9) >= (m30["gain"] or 0.0) - tol]
        cands.sort(key=lambda x: ((x[0]["dd"] or 0.0) - (m30["dd"] or 0.0), -(x[0]["gain"] or -1e9)))
        best = None
        for cm, cov, c in cands[:max(5, min(k, int(S.left() / max(0.5, t_eval)) - 2))]:
            if S.left() < 10:
                break
            t0 = time.time()
            touch("diag-repair365")
            r, span = ctx["eval_365"](cov)
            t_eval = 0.7 * t_eval + 0.3 * (time.time() - t0)
            qq, why = ctx["qualifies_365"](r, span)
            mm = metrics(r) if r else None
            kk = _key365(cm, mm, qq)
            if kk > cur and (best is None or kk > best[0]):
                best = (kk, cov, cm, mm, qq, why, span, c)
        if best is None:
            break
        cur, state, m30, m365, q = best[0], best[1], best[2], best[3], best[4]
        c = best[7]
        v = {"ov": dict(state), "m": m30, "changes": S.changes(state), "m365": m365, "q365": q, "why365": best[5], "span": best[6]}
        out.append(v)
        rep["steps"].append({"phase": "REPAIR_365", "round": step, "applied": f"{c['switch']}={c['cand']}", "tab": c["tab"], "row": c.get("row"), **{k2: m30[k2] for k2 in ("gain", "trades", "tim", "dd", "wr", "valid")}, "gain_365": (m365 or {}).get("gain"), "dd_365": (m365 or {}).get("dd"), "q365": q, "evals": S.n_evals})
        log(f"[DIAG-REPAIR365] s{step} {c['switch']}={c['cand']} 30D gain={m30['gain']} compliant={compliant(m30)} 365D gain={(m365 or {}).get('gain')} DD={(m365 or {}).get('dd')} -> {'PASS' if q else '; '.join(best[5])}")
        if q and compliant(m30):
            break
    return out


def gaps(faults: list, fin_scr: list, base_m: dict) -> list:
    """For each remaining fault: the best single-candidate move toward fixing it. No candidate moves it = MISSING lever."""
    out = []
    want = {"TOO_FEW_TRADES": ("trades", 1), "FEW_TRADES": ("trades", 1), "TIM_LOW": ("tim", 1), "TIM_HIGH": ("tim", -1),
            "DD_HIGH": ("dd", -1), "DD_WARN": ("dd", -1), "TOO_MANY_TRADES": ("trades", -1), "LOW_EDGE": ("gain", 1), "GAIN_NEG": ("gain", 1), "BELOW_BH_MATERIAL": ("gain", 1), "LOW_WR": ("wr", 1)}
    for f in faults:
        name = f[0].split(":", 1)[0]
        if name not in want:
            continue
        k, sgn = want[name]
        best = None
        for m, _ov, c in fin_scr:
            if m is None or c.get("revert") or m.get(k) is None or base_m.get(k) is None:
                continue
            mv = sgn * (m[k] - base_m[k])
            if mv > 1e-9 and (best is None or mv > best[0]):
                best = (mv, c, m)
        if best is None:
            out.append({"fault": f[0], "metric": k, "status": "MISSING_LEVER", "note": f"no template switch/filter moves {k} {'up' if sgn > 0 else 'down'} on this sym_side — candidate for a new switch/filter"})
        else:
            mv, c, m = best
            out.append({"fault": f[0], "metric": k, "status": "LEVER_EXISTS_BUT_COSTLY", "best": f"{c['switch']}={c['cand']}", "move": round(sgn * mv, 4), "gain_at": m["gain"], "valid_at": m["valid"]})
    return out


def counter_summary(rep: dict) -> str:
    c = Counter(s["phase"] for s in rep.get("steps", []))
    return ", ".join(f"{k}:{v}" for k, v in c.items())
