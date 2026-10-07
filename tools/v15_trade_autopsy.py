"""v15_trade_autopsy — relate EVERY bad trade to the switch that would have avoided, filtered, expedited, delayed or augmented
it (USER 2026-10-06: "relate every losing trade or exit before top to a switch that could have avoided or filtered the
entry or expedited the exit or augmented when it should").

Pure logic (unit-tested in tests/test_v15_trade_autopsy.py); the runner `tools/v15_trade_autopsy_run.py` feeds it real
engine ledgers (evaluate_prepared_sanitized(..., include_ledger=True)) on the 30D slice in RAM.

1. classify(base trades, price path): LOSER (pnl<0), PREMATURE_EXIT (after the exit price kept going our way by
   >= max(PREMATURE_MIN_PCT, realised gain) within POST_BARS), GIVEBACK (in-trade max favourable excursion − realised >=
   GIVEBACK_MIN_PCT), MISSED_AUGMENT (winner with MFE >= AUG_MFE_PCT and no augment), plus MISSED_MOVES while flat
   (a >= MISSED_MOVE_PCT move in the side's direction within POST_BARS that no trade covered).
2. attribute(base, candidate): trades are keyed by entry bar (same engine + same data → identical bars). Per base trade:
   FILTERED (entry gone; value = −pnl$), EXIT_EARLIER / EXIT_LATER (value = Δpnl$), RESIZED (same bars, Δpnl$ from
   augment/reduce), and ADDED candidate trades (value = their pnl$; inside a missed move = CAPTURED_MOVE).
3. fixes per bad trade (switches whose value on that trade > 0, ranked) + scorecard per switch (losers fixed, $saved,
   winners hurt, $hurt, net$ and the real Δgain of the candidate set) + greedy surgical combination (to be verified by a
   real eval — the attribution is a guide, the engine is the judge).
4. missing_functions: for bad trades no switch fixes, single-feature threshold rules on the NPZ indicator values at the
   entry (or exit) bar that separate them from good trades → concrete new filter / exit-confirmation suggestions,
   flagged MISSING when no existing candidate of the same indicator family fixes those trades.
"""
from __future__ import annotations

from collections import defaultdict

PREMATURE_MIN_PCT = 1.0
GIVEBACK_MIN_PCT = 1.5
AUG_MFE_PCT = 2.5
MISSED_MOVE_PCT = 3.0
POST_BARS = 32  # 15m bars = 8 h
FEATURE_PREFIXES = ("wt1_", "wt2_", "wt_velocity_", "dc_position_", "rsi", "adx", "k_", "d_", "bb_pct", "stoch", "mfi", "atr_pct", "stdev_edge_", "stdev_slope_", "ema_dist", "trend_")


def realised(ledger: list) -> list:
    """Realised trade rows (with pnl_dollars) incl. flattening REDUCEs; augment events attached by bar."""
    rows = [t for t in ledger or [] if isinstance(t, dict) and "pnl_dollars" in t and not t.get("seeded_prewindow")]
    augs = [int(e.get("bar")) for e in ledger or [] if isinstance(e, dict) and e.get("type") == "AUGMENT" and e.get("bar") is not None]
    out = []
    for t in rows:
        be, bx = int(t.get("bar_entry", -1)), int(t.get("bar_exit", -1))
        out.append({"be": be, "bx": bx, "pnl": float(t.get("pnl_dollars") or 0.0), "pnl_pct": float(t.get("pnl_pct") or 0.0),
                    "entry_reason": str(t.get("entry_reason") or ""), "exit_reason": str(t.get("exit_reason") or t.get("reason") or ""),
                    "entry_price": float(t.get("entry_price") or 0.0), "exit_price": float(t.get("exit_price") or t.get("price") or 0.0),
                    "n_aug": sum(1 for a in augs if be <= a <= bx), "type": t.get("type")})
    return out


def _fav(p0: float, p1: float, is_long: bool) -> float:
    return ((p1 - p0) / p0 * 100.0) * (1 if is_long else -1) if p0 else 0.0


def classify(trades: list, close, is_long: bool) -> list:
    """Adds 'cls' (list of labels), 'mfe_pct' (in-trade), 'post_pct' (best favourable move within POST_BARS after exit)."""
    n = len(close)
    for t in trades:
        cls = []
        be, bx = t["be"], t["bx"]
        p_in = t["entry_price"] or (close[be] if 0 <= be < n else 0.0)
        seg = close[max(0, be):min(n, bx + 1)] if 0 <= be < n else []
        mfe = max((_fav(p_in, p, is_long) for p in seg), default=0.0)
        p_out = t["exit_price"] or (close[bx] if 0 <= bx < n else 0.0)
        post = close[min(n, bx + 1):min(n, bx + 1 + POST_BARS)] if 0 <= bx < n else []
        post_fav = max((_fav(p_out, p, is_long) for p in post), default=0.0)
        t["mfe_pct"], t["post_pct"] = round(mfe, 4), round(post_fav, 4)
        if t["pnl"] < 0:
            cls.append("LOSER")
        if post_fav >= max(PREMATURE_MIN_PCT, t["pnl_pct"]):
            cls.append("PREMATURE_EXIT")
        if mfe - t["pnl_pct"] >= GIVEBACK_MIN_PCT:
            cls.append("GIVEBACK")
        if t["pnl"] > 0 and mfe >= AUG_MFE_PCT and t["n_aug"] == 0:
            cls.append("MISSED_AUGMENT")
        t["cls"] = cls
    return trades


def missed_moves(trades: list, close, is_long: bool) -> list:
    """Flat windows where price moved >= MISSED_MOVE_PCT in the side's direction within POST_BARS (one per window start)."""
    n = len(close)
    held = [False] * n
    for t in trades:
        for i in range(max(0, t["be"]), min(n, t["bx"] + 1)):
            held[i] = True
    out, i = [], 0
    while i < n - 1:
        if held[i]:
            i += 1
            continue
        best_j, best = None, 0.0
        for j in range(i + 1, min(n, i + 1 + POST_BARS)):
            if held[j]:
                break
            f = _fav(close[i], close[j], is_long)
            if f > best:
                best, best_j = f, j
        if best >= MISSED_MOVE_PCT:
            out.append({"b0": i, "b1": best_j, "move_pct": round(best, 4)})
            i = best_j + 1
        else:
            i += 1
    return out


def attribute(base: list, cand: list, moves: list | None = None) -> dict:
    """Per base-trade effect of a candidate + added trades. Keys by entry bar."""
    cb = {t["be"]: t for t in cand}
    bb = {t["be"]: t for t in base}
    eff = {}
    for t in base:
        c = cb.get(t["be"])
        if c is None:
            eff[t["be"]] = ("FILTERED", -t["pnl"])
        elif c["bx"] != t["bx"]:
            eff[t["be"]] = ("EXIT_EARLIER" if c["bx"] < t["bx"] else "EXIT_LATER", c["pnl"] - t["pnl"])
        elif abs(c["pnl"] - t["pnl"]) > 1e-9:
            eff[t["be"]] = ("RESIZED", c["pnl"] - t["pnl"])
    added = []
    for c in cand:
        if c["be"] in bb:
            continue
        mv = next((m for m in moves or [] if m["b0"] <= c["be"] <= m["b1"]), None)
        added.append({"be": c["be"], "pnl": c["pnl"], "kind": "CAPTURED_MOVE" if mv else "ADDED"})
    return {"eff": eff, "added": added}


def scorecard(base: list, attr: dict) -> dict:
    s = {"losers_fixed": 0, "saved": 0.0, "winners_hurt": 0, "hurt": 0.0, "premature_fixed": 0, "aug_fixed": 0, "added_pnl": 0.0, "captured": 0}
    bt = {t["be"]: t for t in base}
    for be, (kind, v) in attr["eff"].items():
        t = bt[be]
        if v > 1e-9:
            if "LOSER" in t["cls"]:
                s["losers_fixed"] += 1
            if "PREMATURE_EXIT" in t["cls"] and kind == "EXIT_LATER":
                s["premature_fixed"] += 1
            if "MISSED_AUGMENT" in t["cls"] and kind == "RESIZED":
                s["aug_fixed"] += 1
            s["saved"] += v
        elif v < -1e-9:
            if t["pnl"] > 0:
                s["winners_hurt"] += 1
            s["hurt"] += -v
    for a in attr["added"]:
        s["added_pnl"] += a["pnl"]
        s["captured"] += 1 if (a["kind"] == "CAPTURED_MOVE" and a["pnl"] > 0) else 0
    s["net"] = s["saved"] - s["hurt"] + s["added_pnl"]
    return s


def fixes_per_trade(base: list, attrs: dict, top: int = 5) -> dict:
    """attrs: {cand_label: attribute()} -> {entry_bar: [(label, kind, value$)]} for bad trades."""
    out = defaultdict(list)
    bad = {t["be"] for t in base if t["cls"]}
    for lab, a in attrs.items():
        for be, (kind, v) in a["eff"].items():
            if be in bad and v > 1e-9:
                out[be].append((lab, kind, round(v, 4)))
    return {be: sorted(v, key=lambda x: -x[2])[:top] for be, v in out.items()}


def surgical_combo(cards: dict, attrs: dict, max_k: int = 8) -> list:
    """Greedy: pick switches with the best net on trades not yet claimed by an earlier pick (no double counting)."""
    claimed, picks = set(), []
    for _ in range(max_k):
        best = None
        for lab, a in attrs.items():
            if lab in picks:
                continue
            net = sum(v for be, (k, v) in a["eff"].items() if be not in claimed) + sum(x["pnl"] for x in a["added"])
            if net > 1e-9 and (best is None or net > best[1]):
                best = (lab, net)
        if best is None:
            break
        picks.append(best[0])
        claimed |= {be for be, (k, v) in attrs[best[0]]["eff"].items() if v > 1e-9}
    return picks


def _feature_keys(npz: dict, n: int) -> list:
    keys = []
    for k, v in npz.items():
        if not isinstance(k, str) or not k.lower().startswith(FEATURE_PREFIXES):
            continue
        try:
            if len(v) == n:
                keys.append(k)
        except Exception:
            continue
    return sorted(keys)


def missing_functions(base: list, unfixed: set, npz: dict, n: int, at: str = "entry", top: int = 6) -> list:
    """Single-feature rules separating UNFIXED bad trades (at entry or exit bar) from good trades: maximise
    (bad pnl$ removed) − (good pnl$ removed). Rule text says what a new filter / exit confirmation should do."""
    bar = "be" if at == "entry" else "bx"
    bad = [t for t in base if t["be"] in unfixed]
    # good entries = every winner not in the unfixed set; good exits = trades whose exit was not premature
    good = [t for t in base if t["pnl"] > 0 and t["be"] not in unfixed] if at == "entry" else [t for t in base if "PREMATURE_EXIT" not in t["cls"] and t["be"] not in unfixed]
    if len(bad) < 2 or len(good) < 3:
        return []
    rules = []
    for k in _feature_keys(npz, n):
        arr = npz[k]
        try:
            bv = [(float(arr[t[bar]]), t) for t in bad if 0 <= t[bar] < n]
            gv = [(float(arr[t[bar]]), t) for t in good if 0 <= t[bar] < n]
        except Exception:
            continue
        vals = sorted({round(v, 6) for v, _ in bv + gv if v == v})
        if len(vals) < 2:
            continue
        step = max(1, len(vals) // 20)
        for thr in vals[::step]:
            for op in (">", "<"):
                hit = (lambda v: v > thr) if op == ">" else (lambda v: v < thr)
                rb = [t for v, t in bv if v == v and hit(v)]
                rg = [t for v, t in gv if v == v and hit(v)]
                gain = -sum(t["pnl"] for t in rb) - sum(t["pnl"] for t in rg)
                if len(rb) >= 2 and gain > 0:
                    rules.append({"feature": k, "op": op, "thr": thr, "at": at, "bad_removed": len(rb), "bad_total": len(bv),
                                  "good_removed": len(rg), "good_total": len(gv), "net_pnl": round(gain, 4)})
    rules.sort(key=lambda r: -r["net_pnl"])
    seen, out = set(), []
    for r in rules:
        fam = (family_of(r["feature"]), r["feature"].rsplit("_", 1)[-1] if "_" in r["feature"] else "")
        if r["feature"] in seen or fam in seen:
            continue
        seen.add(r["feature"])
        seen.add(fam)
        verb = "block entry" if at == "entry" else "hold (do not exit)"
        r["rule"] = f"{verb} when {r['feature']} {r['op']} {r['thr']} (removes {r['bad_removed']}/{r['bad_total']} unfixed bad, {r['good_removed']}/{r['good_total']} good; net {r['net_pnl']:+.2f}pp)"
        out.append(r)
        if len(out) >= top:
            break
    return out


def family_of(feature: str) -> str:
    f = feature.lower()
    for pre, fam in (("wt_velocity", "WT_VEL"), ("wt1", "WT"), ("wt2", "WT"), ("dc_position", "DC"), ("rsi", "RSI"), ("adx", "ADX"),
                     ("k_", "STOCH"), ("d_", "STOCH"), ("stoch", "STOCH"), ("bb", "BB"), ("mfi", "MFI"), ("atr", "ATR"), ("stdev", "STDEV"), ("ema", "EMA"), ("trend", "TREND")):
        if f.startswith(pre):
            return fam
    return f.split("_")[0].upper()


def scaled_rows(res: dict) -> list:
    """realised() rows with pnl in gain-percentage points of THIS run (gain_pct = Σpnl$/peak notional ×100)."""
    rows = realised((res or {}).get("ledger") or [])
    tot = sum(t["pnl"] for t in rows)
    g = (res or {}).get("gain_pct")
    scale = (float(g) / tot) if (g is not None and abs(tot) > 1e-12) else 0.0
    for t in rows:
        t["pnl_usd"] = t["pnl"]
        t["pnl"] = t["pnl"] * scale
    return rows


def recommendations(base: list, attrs: dict, cards: dict, cand_meta: dict) -> list:
    """One record per TEMPLATE row (tab,row,switch=cand) that changes bad trades: what it fixes, what it costs, real Δgain."""
    out = []
    for lab, s in cards.items():
        c = cand_meta[lab]
        out.append({"tab": c["tab"], "row": c.get("row"), "switch": c["switch"], "cand": str(c["cand"]), "losers_fixed": s["losers_fixed"],
                    "premature_fixed": s["premature_fixed"], "aug_fixed": s["aug_fixed"], "captured_moves": s["captured"],
                    "saved_pp": round(s["saved"], 4), "hurt_pp": round(s["hurt"], 4), "net_pp": round(s["net"], 4),
                    "real_d_gain": round(s.get("d_gain", 0.0), 4), "valid": s.get("valid"), "d_trades": s.get("d_trades")})
    out.sort(key=lambda r: (r["valid"] is True, r["real_d_gain"] > 0, r["real_d_gain"], r["net_pp"]), reverse=True)
    return out
