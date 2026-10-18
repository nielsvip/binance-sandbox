#!/usr/bin/env python3
"""discrepancy_report — the daily verdict: what is wrong in LIVE, what is wrong in VECTOR, and what ignoring the vector
would have gained (USER 2026-10-18).

Scores every unscored row of data/discrepancy/discrepancy.db against REAL exchange 15m closes (klines_cache/<SYM>_15m.json,
the same store the live twin builds on) and writes the daily report. No simulation of trading logic anywhere: a
counterfactual here is only "price then vs price later, side-signed, minus round-trip cost" — stated as such on every line.

HOW EACH CLASS IS SCORED
  LIVE_ONLY_FILL (who_wanted=LIVE)   live acted without the vector. AUGMENT -> forward return over --horizon bars from the
                                     fill price (what the extra exposure earned); REDUCE -> the opposite sign (what leaving
                                     the position on would have earned instead). cf_usd is on the row's own notional.
  LIVE_ONLY_FILL (EXCHANGE_STOP)     the protective stop filling; counted, not scored as a live decision.
  GATE_REFUSED                       live wanted it, the parity gate refused: forward return from the refusal price = what
                                     following live instead of the vector would have paid or earned.
  PROMISE_LATE                       same decision, later bar: cost = (price at delivery - price at the promised bar close),
                                     side-signed, on the promised notional.
  BAR_MISMATCH                       the retrace already measured both sides: cf_usd = live net $ - the vector's own return
                                     on live's notional (positive = live's timing was worth more).
  PROMISE_NOT_DELIVERED              the vector's act never happened: forward return from the bar close = what it would have
                                     been worth, side-signed by the action (CLOSE/REDUCE -> avoided loss shows positive).
  EXEC_SKIPPED_INFLIGHT, SWEEP_TIMEOUT   counted only; no order existed, so no price can be attributed without inventing one.

Usage: tools/discrepancy_report.py [--days 1] [--horizon 16] [--out data/reports/discrepancy] [--rescore]
"""
from __future__ import annotations

import argparse
import bisect
import collections
import datetime as dt
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import discrepancy_ledger as DL  # noqa: E402

BAR_S = 900.0
COST_PCT = {"crypto": 0.10, "stocks": 0.02}
_KL = {}


def closes(symbol: str):
    if symbol in _KL:
        return _KL[symbol]
    out = ([], [])
    for d in ("klines_cache", "klines_cache_tradier", "klines_cache_gateway"):
        p = ROOT / d / f"{symbol}_15m.json"
        if not p.exists():
            continue
        try:
            rows = json.load(open(p))
            ts = [dt.datetime.fromisoformat(str(r["timestamp"]).replace("Z", "+00:00")).timestamp() for r in rows]
            px = [float(r["close"]) for r in rows]
            if len(ts) > len(out[0]):
                out = (ts, px)
        except Exception:
            continue
    _KL[symbol] = out
    return out


def close_at(symbol: str, ts: float):
    t, p = closes(symbol)
    if not t:
        return None
    i = bisect.bisect_right(t, float(ts)) - 1
    return p[i] if 0 <= i < len(p) else None


def close_after(symbol: str, ts: float, bars: int):
    t, p = closes(symbol)
    if not t:
        return None
    i = bisect.bisect_right(t, float(ts)) - 1
    j = i + int(bars)
    if i < 0 or j >= len(p):
        return None
    return p[j]


def signed(pct: float, side: str) -> float:
    return -pct if str(side).upper().endswith("SHORT") else pct


def score_rows(con, horizon: int, rescore: bool):
    q = "SELECT id,class,ts,bar_ts,account,sym_side,venue,action,side,who_wanted,live_price,qty,notional,live_usd,lag_s,detail_json FROM discrepancy"
    q += "" if rescore else " WHERE scored_at IS NULL"
    rows = list(con.execute(q))
    n_scored = 0
    for (rid, cls, ts, bar_ts, acct, ss, venue, action, side, who, live_px, qty, notional, live_usd, lag_s, detail) in rows:
        sym = str(ss).rsplit("_", 1)[0]
        sidev = side or ("SHORT" if str(ss).endswith("_SHORT") else "LONG")
        cost = COST_PCT.get(venue or "crypto", 0.10)
        try:
            det = json.loads(detail or "{}")
        except Exception:
            det = {}
        basis, pct, usd, verdict = "", None, None, ""
        if cls == "BAR_MISMATCH":
            vec_pct, notl = det.get("vec_pct"), notional or 0.0
            if vec_pct is not None and live_usd is not None:
                usd = float(live_usd) - float(vec_pct) / 100.0 * float(notl)
                pct = (float(live_usd) / float(notl) * 100.0 - float(vec_pct)) if notl else None
                basis = "live net $ minus the vector's own % on live's notional (retrace-measured)"
                verdict = "LIVE_TIMING_BETTER" if usd > 0 else "VECTOR_TIMING_BETTER"
        elif cls in ("LIVE_ONLY_FILL", "GATE_REFUSED") and who in ("LIVE", ""):
            px = live_px or close_at(sym, ts)
            fut = close_after(sym, ts, horizon)
            if px and fut and px > 0:
                raw = (fut - px) / px * 100.0
                drift = signed(raw, sidev)
                pct = (drift - cost) if str(action).upper() in ("OPEN", "AUGMENT") else (-drift - cost)
                usd = pct / 100.0 * float(notional or (float(qty or 0) * float(px)))
                basis = f"forward {horizon}x15m from the live price, side-signed, minus {cost:.2f}% round trip"
                verdict = "LIVE_WAS_RIGHT" if (pct or 0) > 0 else "VECTOR_WAS_RIGHT"
        elif cls == "PROMISE_LATE":
            px_bar = close_at(sym, float(bar_ts or ts) + BAR_S)
            px_del = close_at(sym, float(ts))
            if px_bar and px_del and px_bar > 0:
                raw = (px_del - px_bar) / px_bar * 100.0
                pct = signed(raw, sidev) if str(action).upper() in ("OPEN", "AUGMENT") else -signed(raw, sidev)
                usd = pct / 100.0 * float(notional or 0.0) if notional else None
                basis = "price at delivery vs price at the promised bar close, side-signed (the cost of being late)"
                verdict = "LATENESS_COST" if (pct or 0) < 0 else "LATENESS_HELPED"
        elif cls == "PROMISE_NOT_DELIVERED":
            px = close_at(sym, float(bar_ts or ts) + BAR_S)
            fut = close_after(sym, float(bar_ts or ts) + BAR_S, horizon)
            if px and fut and px > 0:
                raw = (fut - px) / px * 100.0
                drift = signed(raw, sidev)
                pct = -drift if str(action).upper() in ("CLOSE", "REDUCE") else drift
                basis = f"forward {horizon}x15m from the undelivered act's bar close, side-signed (what the act was worth)"
                verdict = "VECTOR_ACT_WAS_WORTH_IT" if (pct or 0) > 0 else "VECTOR_ACT_WAS_NOT_WORTH_IT"
        if basis:
            DL.score(con, rid, cf_basis=basis, cf_pct=(round(pct, 4) if pct is not None else None),
                     cf_usd=(round(usd, 4) if usd is not None else None), cf_horizon=horizon, verdict=verdict)
            n_scored += 1
        else:
            DL.score(con, rid, cf_basis="counted only (no order, or no price available)", verdict="NOT_PRICED", cf_horizon=horizon)
    con.commit()
    return n_scored, len(rows)


# A live-only family that has been switched OFF is HISTORY, not a current cause. These three were turned off together
# (USER 2026-10-13 "NOTHING can ever be live only"): their rows in this ledger predate the flip and must never be summed
# into a verdict about today. The switch is read from config at report time, so the table cannot drift from reality.
OFF_SWITCH = {"MARGIN_FREE_FOR": "MARGIN_FREE_CLOSE_ENABLED", "MAKER_PROFIT_EXIT": "MARGIN_FREE_CLOSE_ENABLED",
              "SUBSTITUTION": "SUBSTITUTION_CLOSE_ENABLED", "CB_HTF_EXHAUST": "CB_HTF_EXHAUST_EXIT_ENABLED"}
INERT_AFTER_H = 48.0


def _switch_state(family: str):
    """(switch_name, is_on) for a family we know a kill switch for, else (None, None)."""
    for tok, sw in OFF_SWITCH.items():
        if tok.upper() in str(family).upper():
            try:
                from config import Config

                return sw, bool(getattr(Config(), sw, False))
            except Exception:
                return sw, None
    return None, None


def _family(reason: str) -> str:
    """The live function behind the order, via the live code's own classifier when it can name it."""
    r = str(reason or "")
    try:
        from loss_prone import fn_family

        f = fn_family(r)
        if f and f.upper() not in ("UNKNOWN", "OTHER", ""):
            return f[:44]
    except Exception:
        pass
    for tok in ("MARGIN_FREE_FOR", "Winner augment", "MAKER_PROFIT_EXIT", "HEDGE", "RATIO", "SENT_STRAT", "PRE_CLOSE"):
        if tok.upper() in r.upper():
            return tok
    return r.split("_g")[0].split(":")[0][:44] or "(empty)"


def report(con, days: float, horizon: int) -> str:
    t_from = time.time() - days * 86400.0
    day_from = time.strftime("%Y-%m-%d", time.gmtime(t_from))
    L = []
    A = L.append
    A("# Live vs vectorized — every discrepancy, and what ignoring the vector would have gained\n")
    A(f"Window from **{day_from}** ({days} d) · scored on real 15m closes, forward horizon **{horizon} bars ({horizon * 15} min)** · "
      f"ledger `data/discrepancy/discrepancy.db` · generated {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%MZ')}\n")
    A("A counterfactual here is price-vs-price, never a re-simulation: for an entry it is the forward return the extra exposure "
      "would have earned; for an exit it is the return the position would have earned had it stayed on. Round-trip cost is "
      "deducted. Rows with no order behind them (sweep timeouts, in-flight skips) are counted and never priced.\n")
    A("## By class\n")
    A("| class | who wanted it | rows | live $ realised | counterfactual $ | reading |")
    A("|---|---|---|---|---|---|")
    rows = list(con.execute(
        "SELECT class, COALESCE(who_wanted,''), COUNT(*), SUM(COALESCE(live_usd,0)), SUM(COALESCE(cf_usd,0)) FROM discrepancy"
        " WHERE day>=? GROUP BY class, who_wanted ORDER BY 3 DESC", (day_from,)))
    READ = {"GATE_REFUSED": "live wanted it, the parity gate refused",
            "LIVE_ONLY_FILL": "live traded without a vector decision",
            "PROMISE_LATE": "same decision, later bar",
            "PROMISE_NOT_DELIVERED": "the vector's act never happened",
            "EXEC_SKIPPED_INFLIGHT": "dispatch guard refused the vec act",
            "SWEEP_TIMEOUT": "the vector never got to decide (key skipped)",
            "BAR_MISMATCH": "both traded, different bar"}
    for cls, who, n, lusd, cfusd in rows:
        A(f"| {cls} | {who or '-'} | {n} | {lusd:+.2f} | {cfusd:+.2f} | {READ.get(cls, '')} |")
    A("")
    A("## What ignoring the vector would have gained (rows where live acted alone or was refused)\n")
    q = ("SELECT verdict, COUNT(*), SUM(COALESCE(cf_usd,0)), AVG(COALESCE(cf_pct,0)) FROM discrepancy"
         " WHERE day>=? AND class IN ('LIVE_ONLY_FILL','GATE_REFUSED') AND who_wanted='LIVE' GROUP BY verdict ORDER BY 2 DESC")
    A("| verdict | rows | counterfactual $ | mean % per row |")
    A("|---|---|---|---|")
    for v, n, usd, p in con.execute(q, (day_from,)):
        A(f"| {v or '-'} | {n} | {usd:+.2f} | {p:+.3f}% |")
    tot = con.execute("SELECT COUNT(*), SUM(COALESCE(cf_usd,0)) FROM discrepancy WHERE day>=? AND class IN ('LIVE_ONLY_FILL','GATE_REFUSED') AND who_wanted='LIVE'", (day_from,)).fetchone()
    A(f"\n**Net: following live instead of the vector on those {tot[0]} decisions would have been {tot[1] or 0:+.2f} $** "
      f"(forward {horizon} bars, costs in) — read the per-function table below before attributing any of it to today: a "
      f"function whose kill switch is off contributed to this sum only before it was turned off.\n")
    A("## Which live-only FUNCTION is worth keeping (the promotion question)\n")
    A("A function is only a candidate when it wins on most of its rows AND on the sum; one −25 $ tail outweighs ten +1 $ wins.\n")
    A("| live function family | rows | live right | vector right | counterfactual $ | mean % | worst row $ | last seen | state |")
    A("|---|---|---|---|---|---|---|---|---|")
    fam = collections.defaultdict(lambda: [0, 0, 0, 0.0, 0.0, 0.0, 0.0])
    for reason, v, usd, pct, ts in con.execute(
        "SELECT live_reason, verdict, COALESCE(cf_usd,0), COALESCE(cf_pct,0), ts FROM discrepancy WHERE day>=?"
        " AND class IN ('LIVE_ONLY_FILL','GATE_REFUSED') AND who_wanted='LIVE' AND cf_usd IS NOT NULL", (day_from,)):
        f = _family(reason)
        e = fam[f]
        e[0] += 1
        e[1] += 1 if v == "LIVE_WAS_RIGHT" else 0
        e[2] += 1 if v == "VECTOR_WAS_RIGHT" else 0
        e[3] += usd
        e[4] += pct
        e[5] = min(e[5], usd)
        e[6] = max(e[6], float(ts or 0))
    now_ts = time.time()
    active_sum = inert_sum = 0.0
    for f, e in sorted(fam.items(), key=lambda kv: kv[1][3]):
        sw, on = _switch_state(f)
        age_h = (now_ts - e[6]) / 3600.0 if e[6] else 1e9
        if on is False:
            state = f"INERT ({sw}=False)"
        elif age_h > INERT_AFTER_H:
            state = f"QUIET {age_h / 24.0:.1f}d"
        else:
            state = "ACTIVE"
        if state.startswith("ACTIVE"):
            active_sum += e[3]
        else:
            inert_sum += e[3]
        last = dt.datetime.fromtimestamp(e[6], dt.timezone.utc).strftime("%m-%d %H:%MZ") if e[6] else "-"
        A(f"| {f} | {e[0]} | {e[1]} | {e[2]} | {e[3]:+.2f} | {(e[4] / max(1, e[0])):+.3f}% | {e[5]:+.2f} | {last} | {state} |")
    A("")
    A(f"**Of that counterfactual, {active_sum:+.2f} $ belongs to functions that can still fire and {inert_sum:+.2f} $ to "
      f"functions already switched off or silent for more than {INERT_AFTER_H / 24:.0f} days — the second number is history "
      f"and says nothing about today.**\n")
    A("## What the vector's own timing was worth (matched trips that differed by a bar)\n")
    r = con.execute("SELECT COUNT(*), SUM(COALESCE(live_usd,0)), SUM(COALESCE(cf_usd,0)) FROM discrepancy WHERE day>=? AND class='BAR_MISMATCH'", (day_from,)).fetchone()
    A(f"- {r[0]} trips, live realised **{r[1] or 0:+.2f} $**, and **{r[2] or 0:+.2f} $** is live's timing minus the vector's own return on the same notional "
      f"(negative = the vector's bar was the better one).\n")
    A("## Per account\n")
    A("| acct | live-only | gate refused | promise late | not delivered | skipped | sweep timeouts | bar mismatch | cf $ (live-alone rows) |")
    A("|---|---|---|---|---|---|---|---|---|")
    per = collections.defaultdict(collections.Counter)
    for acct, cls, n, cf in con.execute("SELECT account, class, COUNT(*), SUM(COALESCE(cf_usd,0)) FROM discrepancy WHERE day>=? GROUP BY account, class", (day_from,)):
        per[acct][cls] = n
        if cls in ("LIVE_ONLY_FILL", "GATE_REFUSED"):
            per[acct]["cf"] += cf or 0.0
    for acct in sorted(per):
        c = per[acct]
        A(f"| {acct} | {c['LIVE_ONLY_FILL']} | {c['GATE_REFUSED']} | {c['PROMISE_LATE']} | {c['PROMISE_NOT_DELIVERED']} |"
          f" {c['EXEC_SKIPPED_INFLIGHT']} | {c['SWEEP_TIMEOUT']} | {c['BAR_MISMATCH']} | {c['cf']:+.2f} |")
    A("")
    A("## Worst 20 sym_sides by discrepancy count\n")
    A("| acct | sym_side | rows | classes | live $ | cf $ |")
    A("|---|---|---|---|---|---|")
    for acct, ss, n, cls, lusd, cfusd in con.execute(
        "SELECT account, sym_side, COUNT(*), GROUP_CONCAT(DISTINCT class), SUM(COALESCE(live_usd,0)), SUM(COALESCE(cf_usd,0))"
        " FROM discrepancy WHERE day>=? GROUP BY account, sym_side ORDER BY 3 DESC LIMIT 20", (day_from,)):
        A(f"| {acct} | {ss} | {n} | {str(cls)[:60]} | {lusd:+.2f} | {cfusd:+.2f} |")
    A("")
    A("## The individual decisions live made alone (worst and best 10 by counterfactual)\n")
    A("| day | acct | sym_side | action | live reason | cf % | cf $ | verdict |")
    A("|---|---|---|---|---|---|---|---|")
    for direction in ("DESC", "ASC"):
        for day, acct, ss, action, reason, p, usd, v in con.execute(
            f"SELECT day, account, sym_side, action, substr(live_reason,1,44), cf_pct, cf_usd, verdict FROM discrepancy"
            f" WHERE day>=? AND class IN ('LIVE_ONLY_FILL','GATE_REFUSED') AND who_wanted='LIVE' AND cf_usd IS NOT NULL"
            f" ORDER BY cf_usd {direction} LIMIT 10", (day_from,)):
            A(f"| {day} | {acct} | {ss} | {action} | {reason} | {(p or 0):+.3f}% | {(usd or 0):+.3f} | {v} |")
    A("")
    A("## Still invisible (honest gaps)\n")
    A("- **Native families switched off at the source are not in here.** The parity gate only records what reaches it "
      "(2 GATE_REFUSED rows in 7 days); a family disabled in `config.py` never evaluates, so what it *would* have traded "
      "cannot be known without running it in shadow. That is the one measurement this ledger cannot fake.")
    A("- `PROMISE_NOT_DELIVERED` includes no-ops: a vec OPEN while live already holds, or a CLOSE while already flat, needs "
      "no order but looks identical here until ez_manage records a NOOP row.")
    A("- `SWEEP_TIMEOUT` rows are collapsed to one per key per hour; the raw count was 24,757 log lines on 2026-10-18 alone.")
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=1.0)
    ap.add_argument("--horizon", type=int, default=16)
    ap.add_argument("--out", default=str(ROOT / "data" / "reports" / "discrepancy"))
    ap.add_argument("--rescore", action="store_true")
    ap.add_argument("--db", default="")
    a = ap.parse_args()
    if a.db:
        DL.DB_PATH = Path(a.db)
    con = DL.connect()
    n, tot = score_rows(con, a.horizon, a.rescore)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    md = report(con, a.days, a.horizon)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    (out / f"discrepancy_{stamp}.md").write_text(md)
    (out / "discrepancy_latest.md").write_text(md)
    print(f"[discrepancy_report] scored {n}/{tot} rows -> {out}/discrepancy_{stamp}.md")
    print(md)
    con.close()


if __name__ == "__main__":
    main()
