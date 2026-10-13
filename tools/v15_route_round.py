#!/usr/bin/env python3
"""V15 ROUTE ROUND — the enlarged PINPOINT round (USER 2026-10-13: "the worst_first round needs to be abbreviated and the
pinpoint round needs to be enlarged … improved BY A LOT … smart routes: filter ablation first, then testing all the entry
and exit paths that did not have a chance in previous rounds because filters had disabled them").

Evidence that shaped it (finished boards KMNOUSDT_SHORT / MELANIAUSDT_SHORT, mined 2026-10-13):
  * the main row-by-row pass spent ~97% of all evals and ~99% of them returned 0 or negative; 916/1004 switch names were
    exactly 0 on every row on both boards (non-binding: their entries/exits never fire under the running filters);
  * STRIP all filters: KMNO 12.19 -> -14.32, MELANIA 6.67 -> 0.22 — the filters carry the edge, and they MASK paths:
    on the stripped base EXIT_VELOCITY_WT_TFS moved +6.77, COOLDOWN_BARS=0 +3.57 (0 or negative on the filtered set);
  * the post stages found the biggest single lifts with ~3% of the evals; FINAL_FILTER_RECHECK found 19 valid
    positives on MELANIA and chained only one.

So the round runs ROUTES, each one a batch of independent real evals (parallel in the pilot's fork pool) followed by a
batched greedy chain (identical acceptance rules to the main pass — valid, trade floor, TIM/trades guards, promotion
blocks — every promotion is a fresh real eval of the exact set, never a summed delta, §14.2/§19):

  S0 KNOWN   — replay this cat_side's proven moves from the route knowledge (self-improvement, cheap, first);
  S1 ABLATE  — leave-one-out every ACTIVE filter (TF != OFF / gate == True): measured d_gain/d_trades/d_tim per filter;
               drops that help are chained;
  S2 UNMASK  — the ALL-OFF stripped base; every entry/exit/reentry/augment/reduce path row (not already running) is
               measured on it: paths that bind there but were 0 in the main pass were MASKED by filters;
  S3 GRAFT   — top unmasked paths go back onto the CURRENT set, alone and paired with each of their likeliest blockers
               turned off (filters ranked by how many trades their removal added in S1);
  S4 REBUILD — (budget permitting) stripped base + unmasked paths greedy, then filter options greedy: a whole
               alternative route; adopted only if it beats the current set on a fresh eval;
  S5 GRID    — every effective switch of the final set re-tuned: all template candidates + x0.5..x2 numeric grid,
               TF ladders, bool flips;
  S6 ROWLESS — wired live+vec switches that have NO template row (switch-bible COVERAGE, e.g. the invented SHORT
               first-lower-high ENTRY_LH_TRIGGER_*): never swept before, so the invention loop's code never ran;
  S7 PAIRS   — near-miss singles from every step paired (the greedy blind spot).

Knowledge (persistent, cross-campaign, per cat_side, flock-merged — the endgame knowledge lived in the per-campaign
progress dir and reset every round): data/route_knowledge/{CAT_SIDE}.json + ledger_{CAT_SIDE}.jsonl. Negative results
are recorded too (no survivorship bias). S0 replays moves with pos>=2 & mean>0; every list is EV-ordered so a binding
budget banks the best first; nothing is ever skipped because of knowledge alone (order only, plus S0 extra candidates).

Pure module: no engine import. The pilot supplies eval_batch/accept/promote callbacks (tests/test_v15_route_round.py).
"""
from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TF_OPTIONS = ("OFF", "15m", "1h", "4h", "D")
_TF_SET = {"OFF", "15M", "1H", "4H", "D", "3M", "5M", "30M", "2H", "W"}
PATH_TABS = {
    "ENTRY_REVERSAL_BOUNCE": "entry", "ENTRY_BREAKOUT_CHANNEL": "entry", "ENTRY_CONFIRMATION_GATES": "filter",
    "EXIT_STRUCTURAL": "exit", "EXIT_VELOCITY": "exit", "REENTRY_WINDOWED": "reentry", "REENTRY_ADAPTIVE": "reentry",
    "AUGMENT_TREND": "augment", "AUGMENT_RISK_SIZING": "augment", "REDUCE_PROFIT_LOCK": "reduce",
    "REDUCE_SIGNAL_RATER": "reduce", "GLOBAL_RISK_GATES": "filter", "STDEV_SLOPE_SIZING": "sizing",
}
PATH_CLASSES = ("entry", "exit", "reentry", "augment", "reduce")
_FILTER_TOKENS = ("_FILTER", "FILTER_", "GATE", "VETO", "BLOCK", "_KILL", "REQUIRE", "CONFIRM", "_GUARD", "NOLOSS", "COOLDOWN")
# Never swept as rowless candidates: explicit user orders / fishnet doctrine (§90) / sacred masters / no vec data.
ROWLESS_DENY_PREFIXES = ("DELTA_", "DG_", "MAX_ORDER_VALUE", "MAX_AUGMENTS", "GOLDEN_RULE_", "PENNY_STOCK_", "GAP_MOC_", "RECENT_REDUCTION_GUARD", "USE_1M_3M_", "ABLATION_")
ROWLESS_DENY_KEYS = {"MIN_GAIN", "MIN_GAIN_TO_BUY_AGGRESSIVELY", "MODE", "VENUE", "IS_TRADIER", "SIMPLE_PRICE_GT0_ENABLED"}
# Never ablated (sacred / mandate / identity keys): the route round may tune them in S5 but never strips them.
ABLATE_DENY_PREFIXES = ("GOLDEN_RULE_", "ENTRY_DC_TF", "ULTIMATE_DC_4H", "MAX_AUGMENT", "MAX_ORDER_VALUE", "ABLATION_", "FIXED_STOP")


def knowledge_dir() -> Path:
    return Path(os.environ.get("V15_KNOWLEDGE_DIR") or (ROOT / "data" / "route_knowledge"))


def canon(v) -> str:
    """cross-run identity of a value (2.0 == 2, TRUE == True, off == OFF) — same spirit as the pilot's _canon_val."""
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f.is_integer() else repr(round(f, 8))
    s = str(v).strip()
    if s.lower() in ("true", "false"):
        return "True" if s.lower() == "true" else "False"
    if s.upper() == "OFF":
        return "OFF"
    try:
        f = float(s)
        return str(int(f)) if f.is_integer() else repr(round(f, 8))
    except Exception:
        return s


def same(a, b) -> bool:
    return canon(a) == canon(b)


def move_key(delta: dict) -> str:
    """canonical label of an override delta: 'K1=v1+K2=v2' sorted — the knowledge identity of a move."""
    return "+".join(f"{k}={canon(v)}" for k, v in sorted((delta or {}).items()))


def is_tf_value(v) -> bool:
    return isinstance(v, str) and v.strip().upper() in _TF_SET


def classify_key(key: str, tab: str | None = None, filter_names=()) -> str:
    """filter | entry | exit | reentry | augment | reduce | sizing | other. Names that the template uses as yellow /
    orange FILTER columns are filters whatever their tab; then the explicit filter tokens; then the tab; then the name."""
    k = str(key).strip().upper()
    if k in {str(x).strip().upper() for x in (filter_names or ())}:
        return "filter"
    if k.endswith("_FILTER_TF") or any(t in k for t in _FILTER_TOKENS):
        return "filter"
    if tab and PATH_TABS.get(str(tab)) and PATH_TABS[str(tab)] != "filter":
        return PATH_TABS[str(tab)]
    if tab and PATH_TABS.get(str(tab)) == "filter":
        return "filter"
    if "REENTRY" in k or "RE_ENTRY" in k:
        return "reentry"
    if "AUGMENT" in k or k.startswith("ADD_"):
        return "augment"
    if "REDUCE" in k or "PARTIAL" in k or "PROFIT_LOCK" in k:
        return "reduce"
    if "EXIT" in k or "STOP" in k or "_TP" in k or "TRAIL" in k or "TARGET" in k:
        return "exit"
    if "ENTRY" in k or "BOUNCE" in k or "BREAKOUT" in k or "TRIGGER" in k:
        return "entry"
    if "SIZ" in k or "MULT" in k:
        return "sizing"
    return "other"


_ABLATE_TOKENS = ("_FILTER", "FILTER_", "GATE", "VETO", "BLOCK", "_KILL", "REQUIRE", "CONFIRM", "_GUARD", "NOLOSS")
_ABLATE_DENY_TOKENS = ("PARITY", "EXECUTE_NOW", "ENFORCE", "PRIMARY_TF", "TF_HTF", "_TFS", "MANDATORY", "BYPASS", "APPLY_TO_")


def is_ablatable_name(key: str) -> bool:
    """only genuine filters are stripped: the name says filter/gate/veto/block/kill/require/confirm/guard, and it is not
    an infrastructure flag (parity/execute_now/enforce), a TF selector (ENTRY_PRIMARY_TF, TF_HTF1, *_TFS lists), a
    mandatory path or a bypass (turning a bypass 'off' ADDS a filter)."""
    k = str(key).strip().upper()
    if any(k.startswith(p) for p in ABLATE_DENY_PREFIXES) or any(t in k for t in _ABLATE_DENY_TOKENS):
        return False
    return any(t in k for t in _ABLATE_TOKENS)


def off_value(key: str, value, options=None):
    """the 'filter not applied' value for an ACTIVE filter, else None (not ablatable / already off).
    TF filters -> 'OFF' (only when the template offers OFF for this key, if options are known); boolean gates -> False.
    Numeric thresholds have no honest 'off' -> None."""
    if not is_ablatable_name(key):
        return None
    if isinstance(value, bool):
        return False if value else None
    if isinstance(value, str):
        s = value.strip()
        if s.lower() == "true":
            return False
        if s.lower() == "false":
            return None
        if is_tf_value(s) and s.upper() != "OFF":
            if options is not None and not any(str(o).strip().upper() == "OFF" for o in options):
                return None
            return "OFF"
    return None


def active_filters(running: dict, defaults: dict, filter_names, options=None) -> dict:
    """{filter_key: off_value} for every filter whose EFFECTIVE value (running override, else default) is active."""
    out = {}
    for k in sorted(set(filter_names or ())):
        if k not in defaults and k not in (running or {}):
            continue
        v = (running or {}).get(k, (defaults or {}).get(k))
        ov = off_value(k, v, None if options is None else (options or {}).get(k, []))
        if ov is not None:
            out[k] = ov
    return out


def grid_values(cur, template_vals=(), is_int: bool = False, mults=(0.5, 0.75, 0.9, 1.1, 1.25, 1.5, 2.0)) -> list:
    """candidate values around the current value: every template candidate + a multiplicative grid; TF -> the ladder;
    bool -> the flip. Never the current value, deduped, stable order (template first)."""
    out = []
    def _add(x):
        if not same(x, cur) and all(not same(x, y) for y in out):
            out.append(x)
    if isinstance(cur, bool):
        _add(not cur)
        return out
    if is_tf_value(cur):
        for t in list(template_vals) + list(TF_OPTIONS):
            if is_tf_value(t):
                _add(t)
        return out
    for t in template_vals:
        _add(t)
    if isinstance(cur, (int, float)) and float(cur) != 0.0:
        for m in mults:
            g = float(cur) * m
            g = int(round(g)) if is_int else round(g, 6)
            if is_int and g == 0 and int(cur) != 0:
                continue
            _add(g)
    return out


def rowless_keys(verify_path=None, deny_prefixes=ROWLESS_DENY_PREFIXES, deny_keys=ROWLESS_DENY_KEYS) -> list:
    """switch-bible COVERAGE: switches 'wired live+vec … but in no template' — the code the self-improvement loops
    produced (e.g. ENTRY_LH_TRIGGER_*) that no sweep could ever reach. Deny-listed: user orders / fishnets / sacred."""
    p = Path(verify_path or os.environ.get("V15_ROWLESS_VERIFY_JSON") or (ROOT / "data" / "reports" / "switch_bible_verify_latest.json"))
    try:
        j = json.loads(p.read_text())
        cov = ((j.get("checks") or {}).get("COVERAGE")) or []
    except Exception:
        return []
    out = []
    for s in cov:
        k = str(s).split(":", 1)[0].strip()
        if not k or k in deny_keys or any(k.startswith(d) for d in deny_prefixes):
            continue
        if "wired live+vec" not in str(s):
            continue
        out.append(k)
    return sorted(set(out))


def rowless_candidates(keys, defaults: dict, running: dict, validate=None) -> list:
    """[(key, value)] to try per rowless key: bool flip, TF ladder, x0.5/x2 numeric. validate(key, value)->bool filters."""
    out = []
    for k in keys:
        if k not in (defaults or {}) and k not in (running or {}):
            continue
        cur = (running or {}).get(k, (defaults or {}).get(k))
        if isinstance(cur, bool):
            vals = [not cur]
        elif is_tf_value(cur):
            vals = [t for t in TF_OPTIONS if not same(t, cur)]
        elif isinstance(cur, (int, float)) and float(cur) != 0.0:
            is_int = isinstance(cur, int)
            vals = [int(round(cur * 0.5)) if is_int else round(cur * 0.5, 6), int(round(cur * 2)) if is_int else round(cur * 2, 6)]
            vals = [v for v in dict.fromkeys(vals) if not same(v, cur)]
        else:
            vals = []
        for v in vals:
            if validate is None or validate(k, v):
                out.append((k, v))
    return out


def _gain(res):
    try:
        g = (res or {}).get("gain_pct")
        return None if g is None else float(g)
    except Exception:
        return None


def batched_greedy(cands, base_ov: dict, base_gain: float, eval_batch, accept, left, max_rounds: int = 50, lazy_margin: float = 0.5, on_promote=None, how: str = "route", max_batch: int = 400) -> dict:
    """Steepest-ascent greedy over independent candidate moves, one PARALLEL batch per round.
    cands: [{"label", "delta": {key: value}, "key": switch for block checks, ...}].
    Round: every remaining candidate is applied to the CURRENT set and evaluated in one batch; the best ACCEPTED positive
    (real fresh gain - current gain > 1e-9, accept(res, err, d, cand) True) is promoted; the next round re-measures the
    remaining candidates vs the grown set. lazy_margin: after a promotion only candidates whose last delta was
    > -lazy_margin stay in the pool (a candidate 0.5pp under water rarely flips — documented heuristic, logged).
    Every promoted number is the measured gain of the exact set (no sums). Returns
    {"ov", "gain", "promotions", "evals", "rounds", "last": {label: (delta, trades, tim, binding)}}."""
    cur_ov, cur_gain = dict(base_ov), float(base_gain)
    pool = [c for c in cands if c.get("delta")]
    promos, last, n_ev, rounds = [], {}, 0, 0
    while pool and rounds < max_rounds and left():
        rounds += 1
        items, idx = [], []
        for c in pool:
            if all(same(cur_ov.get(k, "__absent__"), v) for k, v in c["delta"].items()):
                continue  # identity: already running
            ov = dict(cur_ov)
            ov.update(c["delta"])
            items.append((f"{how}:{c['label']}", ov))
            idx.append(c)
            if len(items) >= max_batch:
                break
        if not items:
            break
        results = eval_batch(items)
        n_ev += len(items)
        scored = []
        for c, (lab, ov), (res, err) in zip(idx, items, results):
            g = _gain(res)
            d = None if (err or g is None) else g - cur_gain
            last[c["label"]] = (d, (res or {}).get("trades"), (res or {}).get("tim_pct"), (res or {}).get("behavior_fingerprint"))
            if d is not None and d > 1e-9 and accept(res, err, d, c):
                scored.append((d, c, ov, res))
        if not scored:
            break
        scored.sort(key=lambda t: -t[0])
        d, c, ov, res = scored[0]
        before = cur_gain
        cur_ov, cur_gain = dict(ov), float(_gain(res))
        rec = {"how": how, "label": c["label"], "delta": round(d, 6), "before": round(before, 6), "after": round(cur_gain, 6), "trades": (res or {}).get("trades"), "tim": (res or {}).get("tim_pct"), "dd": (res or {}).get("max_dd_pct"), "move": dict(c["delta"])}
        promos.append(rec)
        if on_promote is not None:
            on_promote(cur_ov, res, d, how, c["label"], c)
        pool = [x for x in pool if x is not c and (last.get(x["label"], (None,))[0] is None or last[x["label"]][0] > -float(lazy_margin))]
    return {"ov": cur_ov, "gain": cur_gain, "promotions": promos, "evals": n_ev, "rounds": rounds, "last": last}


# ── knowledge (persistent, per cat_side, flock-merged) ──

@contextlib.contextmanager
def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lk = open(str(path) + ".lock", "a+")
    try:
        try:
            import fcntl
            fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lk.close()


def load_knowledge(cat_side: str, kdir=None) -> dict:
    p = Path(kdir or knowledge_dir()) / f"{cat_side}.json"
    try:
        j = json.loads(p.read_text())
        return j if isinstance(j, dict) else {}
    except Exception:
        return {}


def _stat_add(tab: dict, key: str, d, extra: dict | None = None):
    if d is None:
        return
    s = tab.setdefault(key, {"n": 0, "pos": 0, "neg": 0, "sum": 0.0, "max": None, "promoted": 0, "syms": []})
    s["n"] += 1
    s["sum"] = round(float(s["sum"]) + float(d), 6)
    if d > 1e-9:
        s["pos"] += 1
    elif d < -1e-9:
        s["neg"] += 1
    s["max"] = float(d) if s["max"] is None else max(float(s["max"]), float(d))
    for k, v in (extra or {}).items():
        if k == "promoted" and v:
            s["promoted"] += 1
        elif k == "sym" and v and v not in s["syms"]:
            s["syms"] = (s["syms"] + [v])[-40:]


def merge_board(know: dict, board: dict) -> dict:
    """fold ONE board's route record into the cat_side knowledge (pure; tested). board = {"symside", "moves": {move_key:
    {"step", "delta", "promoted"}}, "unmask": {path_key: delta_on_stripped}, "masked_by": {path_key: [filter keys]},
    "lift", "entry_gain", "final_gain"}. Negatives are recorded (no survivorship bias)."""
    know = dict(know or {})
    know.setdefault("moves", {})
    know.setdefault("unmask", {})
    know.setdefault("masked_by", {})
    wb = know.setdefault("boards", {"n": 0, "sum_lift": 0.0, "pos_lift": 0, "symsides": []})
    ss = board.get("symside")
    for mk, m in (board.get("moves") or {}).items():
        _stat_add(know["moves"], mk, m.get("delta"), {"promoted": m.get("promoted"), "sym": ss})
        know["moves"][mk]["step"] = m.get("step") or know["moves"][mk].get("step")
    for pk, d in (board.get("unmask") or {}).items():
        _stat_add(know["unmask"], pk, d, {"sym": ss})
    for pk, fl in (board.get("masked_by") or {}).items():
        cur = know["masked_by"].setdefault(pk, {})
        for f in fl or []:
            cur[f] = int(cur.get(f, 0)) + 1
    lift = board.get("lift")
    if lift is not None:
        wb["n"] += 1
        wb["sum_lift"] = round(float(wb["sum_lift"]) + float(lift), 6)
        if float(lift) > 1e-9:
            wb["pos_lift"] += 1
        if ss and ss not in wb["symsides"]:
            wb["symsides"] = (wb["symsides"] + [ss])[-500:]
    know["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return know


def save_board(cat_side: str, board: dict, kdir=None) -> dict:
    """append the board to the ledger and merge it into the knowledge under an exclusive lock (re-read inside the lock:
    concurrent pilots never lose each other's merges). Idempotent per (symside, key). Never raises."""
    d = Path(kdir or knowledge_dir())
    kp = d / f"{cat_side}.json"
    lp = d / f"ledger_{cat_side}.jsonl"
    try:
        with _locked(kp):
            rk = f"{board.get('symside')}|{board.get('key')}"
            if lp.exists():
                with open(lp) as fh:
                    for ln in fh:
                        if rk in ln[:400]:
                            return load_knowledge(cat_side, d)
            with open(lp, "a") as fh:
                fh.write(json.dumps({"rk": rk, **board}, default=str) + "\n")
            know = merge_board(load_knowledge(cat_side, d), board)
            tmp = kp.with_suffix(f".tmp{os.getpid()}")
            tmp.write_text(json.dumps(know, indent=1, default=str))
            os.replace(tmp, kp)
            return know
    except Exception as e:
        print(f"[ROUTE-KNOW-warn] save {cat_side}: {e}", flush=True)
        return {}


def rebuild_from_ledgers(paths, kdir=None) -> dict:
    """fleet merge: rebuild every cat_side knowledge from ledger files (S1 pulls ledgers from all hosts). Deduped by rk."""
    by_cat, seen = {}, set()
    for p in paths:
        cat = Path(p).name[len("ledger_"):-len(".jsonl")] if Path(p).name.startswith("ledger_") else None
        if not cat:
            continue
        try:
            for ln in open(p):
                try:
                    rec = json.loads(ln)
                except Exception:
                    continue
                rk = rec.get("rk")
                if rk in seen:
                    continue
                seen.add(rk)
                by_cat[cat] = merge_board(by_cat.get(cat, {}), rec)
        except Exception:
            continue
    d = Path(kdir or knowledge_dir())
    d.mkdir(parents=True, exist_ok=True)
    for cat, know in by_cat.items():
        (d / f"{cat}.json").write_text(json.dumps(know, indent=1, default=str))
    return {c: (k.get("boards") or {}).get("n", 0) for c, k in by_cat.items()}


def ev(stat) -> float:
    try:
        return float(stat["sum"]) / max(1, int(stat["n"]))
    except Exception:
        return 0.0


def known_moves(know: dict, min_pos: int = 2, top: int = 40) -> list:
    """moves proven on this cat_side (pos >= min_pos syms, mean > 0), best EV first — S0 replays them."""
    out = []
    for mk, s in (know.get("moves") or {}).items():
        if int(s.get("pos", 0)) >= min_pos and ev(s) > 0:
            out.append((ev(s), mk))
    out.sort(key=lambda t: -t[0])
    return [mk for _, mk in out[:top]]


def parse_move(mk: str, defaults: dict, parse=None) -> dict:
    """'K1=v1+K2=v2' -> {K1: parsed v1, ...}; parse(value_str, default) supplied by the pilot (_parse_opt_value)."""
    out = {}
    for part in str(mk).split("+"):
        if "=" not in part:
            return {}
        k, _, v = part.partition("=")
        k = k.strip()
        out[k] = parse(v.strip(), (defaults or {}).get(k)) if parse else v.strip()
    return out


def ev_order(labels_moves, know: dict) -> list:
    """stable EV ordering of candidate dicts by knowledge (unknown = 0, keeps input order among equals)."""
    mv = know.get("moves") or {}
    return sorted(labels_moves, key=lambda c: -ev(mv.get(move_key(c.get("delta") or {}), {"sum": 0, "n": 1})))


# ── the round ──

def run_route_round(ctx: dict) -> dict:
    """Orchestrates S0..S7 against ctx (all callbacks supplied by the pilot; see module doc). ctx keys:
      symside, cat_side, tier, running (dict), gain (float), defaults (dict), filter_names (set),
      path_rows [(tab, key, value)], options {key: [values]}, rowless [(key, value)], effective_keys [key],
      eval_batch(items)->[(res, err)], accept(res, err, d, cand)->bool, promote(ov, res, d, how, label, cand),
      validate(key, value)->bool, left()->bool, frac_left()->float, know (dict), log(msg), top_k (int).
    Returns the round record (steps, promotions, ablation, unmask tables, board knowledge record)."""
    log = ctx.get("log") or (lambda m: None)
    defaults = ctx.get("defaults") or {}
    know = ctx.get("know") or {}
    top_k = int(ctx.get("top_k") or 24)
    validate = ctx.get("validate") or (lambda k, v: True)
    state = {"ov": dict(ctx["running"]), "gain": float(ctx["gain"])}
    out = {"entry_gain": state["gain"], "steps": {}, "promotions": [], "ablation": [], "unmask": [], "evals": 0}
    board = {"symside": ctx.get("symside"), "moves": {}, "unmask": {}, "masked_by": {}}

    def _promote(ov, res, d, how, label, cand):
        state["ov"], state["gain"] = dict(ov), float(_gain(res))
        ctx["promote"](ov, res, d, how, label, cand)

    def _greedy(cands, how, **kw):
        r = batched_greedy(cands, state["ov"], state["gain"], ctx["eval_batch"], ctx["accept"], ctx["left"], on_promote=_promote, how=how, **kw)
        out["evals"] += r["evals"]
        out["promotions"].extend(r["promotions"])
        prom = {p["label"] for p in r["promotions"]}
        for c in cands:
            lv = r["last"].get(c["label"])
            if lv and lv[0] is not None:
                board["moves"][move_key(c["delta"])] = {"step": how, "delta": lv[0], "promoted": c["label"] in prom}
        out["steps"][how] = {"cands": len(cands), "evals": r["evals"], "rounds": r["rounds"], "promoted": len(r["promotions"])}
        log(f"[ROUTE] {ctx.get('symside')} {how}: {len(cands)} cands, {r['evals']} evals, {len(r['promotions'])} promoted -> {state['gain']:.4f}")
        return r

    def _cand(key, val, label=None, extra=None):
        c = {"label": label or f"{key}={canon(val)}", "delta": {key: val}, "key": key}
        if extra:
            c.update(extra)
        return c

    # S0 KNOWN — replay proven moves of this cat_side
    if ctx["left"]():
        s0 = []
        for mk in known_moves(know, min_pos=int(ctx.get("known_min_pos") or 2)):
            mv = parse_move(mk, defaults, ctx.get("parse"))
            if mv and all(validate(k, v) for k, v in mv.items()):
                s0.append({"label": "KNOWN:" + mk, "delta": mv, "key": sorted(mv)[0]})
        if s0:
            _greedy(s0, "S0_KNOWN", max_rounds=8)
    # S1 ABLATE — leave-one-out every active filter (measurement + chained helpful drops)
    filt = active_filters(state["ov"], defaults, ctx.get("filter_names") or (), ctx.get("options"))
    filt = {k: v for k, v in filt.items() if validate(k, v)}
    abl = {}
    if filt and ctx["left"]():
        items = []
        for k, offv in filt.items():
            ov = dict(state["ov"])
            ov[k] = offv
            items.append((f"S1_ABLATE:{k}={canon(offv)}", ov))
        all_off = dict(state["ov"])
        all_off.update(filt)
        items.append(("S1_ALL_OFF", all_off))
        base_res = ctx["eval_batch"](items)
        out["evals"] += len(items)
        cur_tr = ctx.get("chain_trades")
        for (lab, _ov), (res, err), k in zip(items, base_res, list(filt) + ["__ALL__"]):
            g = _gain(res)
            d = None if (err or g is None) else g - state["gain"]
            row = {"filter": k, "off": canon(filt.get(k, "ALL")), "delta": d, "trades": (res or {}).get("trades"), "tim": (res or {}).get("tim_pct"), "valid": (res or {}).get("valid"), "d_trades": (None if cur_tr is None or (res or {}).get("trades") is None else int((res or {}).get("trades")) - int(cur_tr))}
            abl[k] = (row, res, err)
            out["ablation"].append(row)
            if k != "__ALL__" and d is not None:
                board["moves"][move_key({k: filt[k]})] = {"step": "S1_ABLATE", "delta": d, "promoted": False}
        drops = [_cand(k, filt[k], f"DROP:{k}") for k, (row, _r, _e) in abl.items() if k != "__ALL__" and row["delta"] is not None and row["delta"] > 1e-9]
        drops.sort(key=lambda c: -abl[c["key"]][0]["delta"])
        if drops:
            _greedy(drops, "S1_ABLATE", max_rounds=len(drops))
        else:
            out["steps"]["S1_ABLATE"] = {"cands": 0, "evals": len(items), "rounds": 1, "promoted": 0}
    # S2 UNMASK — paths on the ALL-OFF stripped base
    stripped = None
    unmasked = []
    if ctx["left"]():
        filt_now = active_filters(state["ov"], defaults, ctx.get("filter_names") or (), ctx.get("options"))
        filt_now = {k: v for k, v in filt_now.items() if validate(k, v)}
        stripped = dict(state["ov"])
        stripped.update(filt_now)
        sres = ctx["eval_batch"]([("S2_STRIPPED_BASE", stripped)])
        out["evals"] += 1
        s_res, s_err = sres[0]
        s_gain = _gain(s_res)
        s_fp = (s_res or {}).get("behavior_fingerprint")
        out["stripped"] = {"gain": s_gain, "trades": (s_res or {}).get("trades"), "tim": (s_res or {}).get("tim_pct"), "filters_off": sorted(filt_now)}
        if s_gain is not None:
            paths, seen = [], set()
            for tab, key, val in ctx.get("path_rows") or []:
                cls = classify_key(key, tab, ctx.get("filter_names") or ())
                if cls not in PATH_CLASSES or (key, canon(val)) in seen or same(stripped.get(key, defaults.get(key)), val) or not validate(key, val):
                    continue
                seen.add((key, canon(val)))
                paths.append((tab, key, val, cls))
            um = know.get("unmask") or {}
            paths.sort(key=lambda p: -ev(um.get(f"{p[1]}={canon(p[2])}", {"sum": 0, "n": 1})))
            max_paths = int(ctx.get("max_paths") or 1500)
            paths = paths[:max_paths]
            items = []
            for tab, key, val, cls in paths:
                ov = dict(stripped)
                ov[key] = val
                items.append((f"S2_UNMASK:{key}={canon(val)}", ov))
            res_l = ctx["eval_batch"](items) if items else []
            out["evals"] += len(items)
            for (tab, key, val, cls), (res, err) in zip(paths, res_l):
                g = _gain(res)
                if err or g is None:
                    continue
                d = g - s_gain
                binding = (res or {}).get("behavior_fingerprint") != s_fp if s_fp else abs(d) > 1e-9
                board["unmask"][f"{key}={canon(val)}"] = d
                if d > 1e-9 and binding and bool((res or {}).get("valid")):
                    unmasked.append({"tab": tab, "key": key, "val": val, "cls": cls, "d_stripped": d, "trades": (res or {}).get("trades")})
            unmasked.sort(key=lambda u: -u["d_stripped"])
            out["unmask"] = [{k: (canon(v) if k == "val" else v) for k, v in u.items()} for u in unmasked[:200]]
            out["steps"]["S2_UNMASK"] = {"paths": len(paths), "evals": len(items) + 1, "unmasked": len(unmasked), "stripped_gain": s_gain}
            log(f"[ROUTE] {ctx.get('symside')} S2_UNMASK: stripped base {s_gain:.4f} ({len(filt_now)} filters off), {len(paths)} paths, {len(unmasked)} unmasked")
    # S3 GRAFT — unmasked paths onto the CURRENT set, alone and with each likely blocker off
    if unmasked and ctx["left"]():
        blockers = sorted([k for k in abl if k != "__ALL__"], key=lambda k: -(abl[k][0].get("d_trades") or 0))
        blockers = blockers[: int(ctx.get("max_blockers") or 6)]
        mb = know.get("masked_by") or {}
        cands = []
        for u in unmasked[:top_k]:
            pk = f"{u['key']}={canon(u['val'])}"
            cands.append(_cand(u["key"], u["val"], f"GRAFT:{pk}", {"path": pk}))
            bl = sorted(blockers, key=lambda f: -int((mb.get(pk) or {}).get(f, 0)))
            for f in bl:
                offv = filt.get(f) if f in filt else off_value(f, state["ov"].get(f, defaults.get(f)), (ctx.get("options") or {}).get(f))
                if offv is None or f == u["key"]:
                    continue
                cands.append({"label": f"GRAFT:{pk}-{f}", "delta": {u["key"]: u["val"], f: offv}, "key": u["key"], "path": pk, "blocker": f})
        r = _greedy(cands, "S3_GRAFT", max_rounds=top_k)
        for p in r["promotions"]:
            c = next((x for x in cands if x["label"] == p["label"]), None)
            if c and c.get("blocker"):
                board["masked_by"].setdefault(c["path"], []).append(c["blocker"])
    # S4 REBUILD — an alternative route from the stripped base (WINNER/MEDIOCRE, budget permitting)
    if stripped is not None and unmasked and ctx["left"]() and ctx.get("tier") in ("WINNER", "MEDIOCRE") and float(ctx.get("frac_left", lambda: 1.0)()) > 0.45:
        cands = [_cand(u["key"], u["val"], f"REB_PATH:{u['key']}={canon(u['val'])}") for u in unmasked[:top_k]]
        sres = ctx["eval_batch"]([("S4_BASE", stripped)])
        out["evals"] += 1
        sg = _gain(sres[0][0])
        if sg is not None:
            ok_any = lambda res, err, d, c: (not err) and bool((res or {}).get("valid"))
            r1 = batched_greedy(cands, stripped, sg, ctx["eval_batch"], ok_any, ctx["left"], max_rounds=top_k, how="S4_REB_PATH")
            fc = []
            for fk in sorted(ctx.get("filter_names") or ()):
                for v in (ctx.get("options") or {}).get(fk, []):
                    if validate(fk, v) and not same(r1["ov"].get(fk, defaults.get(fk)), v):
                        fc.append(_cand(fk, v, f"REB_FILT:{fk}={canon(v)}"))
            fc = ev_order(fc, know)[: int(ctx.get("max_rebuild_filters") or 400)]
            r2 = batched_greedy(fc, r1["ov"], r1["gain"], ctx["eval_batch"], ok_any, ctx["left"], max_rounds=12, how="S4_REB_FILT")
            out["evals"] += r1["evals"] + r2["evals"]
            out["rebuild"] = {"stripped": sg, "after_paths": r1["gain"], "after_filters": r2["gain"], "current": state["gain"], "paths": [p["label"] for p in r1["promotions"]], "filters": [p["label"] for p in r2["promotions"]]}
            if r2["gain"] - state["gain"] > 1e-9:
                fin = ctx["eval_batch"]([("S4_ADOPT", r2["ov"])])
                out["evals"] += 1
                res, err = fin[0]
                g = _gain(res)
                d = None if (err or g is None) else g - state["gain"]
                c = {"label": "REBUILD", "delta": {}, "key": "REBUILD"}
                if d is not None and d > 1e-9 and ctx["accept"](res, err, d, c):
                    rec = {"how": "S4_REBUILD", "label": "REBUILD", "delta": round(d, 6), "before": round(state["gain"], 6), "after": round(g, 6), "trades": (res or {}).get("trades"), "tim": (res or {}).get("tim_pct"), "dd": (res or {}).get("max_dd_pct"), "move": {"paths": out["rebuild"]["paths"], "filters": out["rebuild"]["filters"]}}
                    _promote(r2["ov"], res, d, "S4_REBUILD", "REBUILD", c)
                    out["promotions"].append(rec)
                    out["rebuild"]["adopted"] = True
            log(f"[ROUTE] {ctx.get('symside')} S4_REBUILD: stripped {sg:.4f} -> paths {r1['gain']:.4f} -> filters {r2['gain']:.4f} vs current {state['gain']:.4f} adopted={bool((out.get('rebuild') or {}).get('adopted'))}")
    # S5 GRID — pinpoint every effective switch of the final set
    if ctx["left"]():
        cands = []
        for k in ctx.get("effective_keys") or []:
            cur = state["ov"].get(k, defaults.get(k))
            tv = (ctx.get("options") or {}).get(k, [])
            is_int = isinstance(defaults.get(k), int) and not isinstance(defaults.get(k), bool)
            for v in grid_values(cur, tv, is_int=is_int):
                if validate(k, v):
                    cands.append(_cand(k, v, f"GRID:{k}={canon(v)}"))
        if cands:
            _greedy(ev_order(cands, know), "S5_GRID", max_rounds=top_k)
    # S6 ROWLESS — wired switches without a template row (the invention loop's code)
    if ctx["left"]() and ctx.get("rowless"):
        cands = [_cand(k, v, f"ROWLESS:{k}={canon(v)}") for k, v in ctx["rowless"] if validate(k, v)]
        if cands:
            _greedy(ev_order(cands, know), "S6_ROWLESS", max_rounds=8)
    # S7 PAIRS — near-miss singles paired
    if ctx["left"]():
        near = []
        for mk, m in board["moves"].items():
            d = m.get("delta")
            if d is not None and -0.5 < d <= 1e-9 and not m.get("promoted") and m.get("step") in ("S3_GRAFT", "S5_GRID", "S6_ROWLESS", "S1_ABLATE", "S0_KNOWN"):
                near.append((d, mk))
        near.sort(key=lambda t: -t[0])
        singles = [parse_move(mk, defaults, ctx.get("parse")) for _, mk in near[: int(ctx.get("pair_singles") or 10)]]
        singles = [s for s in singles if s]
        cands = []
        for i in range(len(singles)):
            for j in range(i + 1, len(singles)):
                if set(singles[i]) & set(singles[j]):
                    continue
                dl = dict(singles[i])
                dl.update(singles[j])
                cands.append({"label": "PAIR:" + move_key(dl), "delta": dl, "key": sorted(singles[i])[0]})
        if cands:
            _greedy(cands, "S7_PAIRS", max_rounds=4)
    out["final_gain"] = state["gain"]
    out["final_ov"] = state["ov"]
    out["lift"] = state["gain"] - out["entry_gain"]
    board.update({"lift": out["lift"], "entry_gain": out["entry_gain"], "final_gain": state["gain"], "tier": ctx.get("tier")})
    out["board"] = board
    return out


def _cli():
    import argparse
    import glob
    ap = argparse.ArgumentParser(description="route knowledge maintenance (merge fleet ledgers, report)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("merge", help="rebuild knowledge from ledger_*.jsonl files (fleet pull)")
    m.add_argument("--ledgers", nargs="+", default=None)
    m.add_argument("--out", default=None)
    r = sub.add_parser("report", help="markdown report: proven moves, unmask evidence, rowless switch-add plan")
    r.add_argument("--out", default=None)
    a = ap.parse_args()
    kd = Path(a.out) if getattr(a, "out", None) and a.cmd == "merge" else knowledge_dir()
    if a.cmd == "merge":
        paths = a.ledgers or glob.glob(str(knowledge_dir() / "ledger_*.jsonl")) + glob.glob(str(knowledge_dir() / "hosts" / "*" / "ledger_*.jsonl"))
        print(json.dumps(rebuild_from_ledgers(paths, kd)))
        return
    lines = ["# ROUTE KNOWLEDGE REPORT", "", f"generated {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} from `{kd}`", ""]
    rowless_hits = []
    for p in sorted(kd.glob("*.json")):
        cat = p.stem
        k = load_knowledge(cat, kd)
        wb = k.get("boards") or {}
        n = int(wb.get("n", 0))
        lines += [f"## {cat}", "", f"boards {n}, mean lift {float(wb.get('sum_lift', 0)) / max(1, n):+.3f}pp, lift>0 on {wb.get('pos_lift', 0)}/{n}", "", "| move | n | pos | neg | mean | promoted |", "|---|---|---|---|---|---|"]
        mv = sorted((k.get("moves") or {}).items(), key=lambda t: -ev(t[1]))
        for mk, s in mv[:40]:
            lines.append(f"| `{mk}` | {s['n']} | {s['pos']} | {s['neg']} | {ev(s):+.3f} | {s['promoted']} |")
        lines += ["", "Top unmasked paths (delta on the filter-stripped base):", "", "| path | n | pos | mean |", "|---|---|---|---|"]
        for pk, s in sorted((k.get("unmask") or {}).items(), key=lambda t: -ev(t[1]))[:25]:
            lines.append(f"| `{pk}` | {s['n']} | {s['pos']} | {ev(s):+.3f} |")
        for mk, s in mv:
            if s.get("step") == "S6_ROWLESS" and int(s.get("pos", 0)) >= 2 and ev(s) > 0:
                rowless_hits.append((cat, mk, s))
        lines.append("")
    lines += ["## SWITCH-ADD PLAN (rowless switches with positive evidence — run tools/v15_switch_add.py on S1 per SWITCH_ADD_GUIDE.md)", ""]
    for cat, mk, s in rowless_hits or []:
        lines.append(f"- {cat}: `{mk}` pos {s['pos']}/{s['n']} mean {ev(s):+.3f}")
    if not rowless_hits:
        lines.append("- none yet (needs pos >= 2 sym_sides)")
    txt = "\n".join(lines) + "\n"
    if a.out:
        Path(a.out).write_text(txt)
    print(txt)


if __name__ == "__main__":
    _cli()
