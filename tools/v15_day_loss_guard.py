#!/usr/bin/env python3
"""v15_day_loss_guard — same-day containment for tail losing days (Mac, where the live trades are).

Scope: the 5 Binance crypto accounts (ang, fin, flz, inf, men). Stocks (tra/trb/trc) are NEVER touched.

Day-loss metric (honest, from a real sample of data/per_trade_returns/ang_*.jsonl):
  fields present per trade: pnl_pct, fees_pct, exit_price, qty. entry_price is ALWAYS 0.0,
  so entry notional is unusable. The primary metric is therefore QUOTE-NOTIONAL-WEIGHTED net USD:
      net_pct  = pnl_pct - fees_pct            (same formula as tools/v15_loss_streak_watch.net_of
                                                and tools/v15_causal_learner.ingest_live)
      trade_usd = net_pct / 100 * exit_price * qty
      day_net_usd(acct) = sum(trade_usd over the UTC day)
  Fallback (stated, never silent): trades with exit_price<=0 or qty<=0 cannot be weighted;
  they accumulate into day_net_pts (sum of net_pct points) and are counted in n_fallback.
  The points threshold is evaluated only as a secondary tripwire and the output always says
  which metric fired (metric: USD_WEIGHTED or POINTS_FALLBACK).

Threshold (env V15_DAY_HALT_NET, default -50.0 USD net of fees per account per UTC day):
  worst observed UTC-day net over 2026-10-10..14 across all 5 accounts was men 2026-10-13
  at -$18.58; the rest of the losing days sat between -$0.37 and -$11.84. -50.0 is ~2.7x the
  worst observed day, so the halt fires only on genuine tail days, never on normal variance.
  Secondary points tripwire (env V15_DAY_HALT_NET_PTS, default -200.0): worst observed points
  day was men 2026-10-10 at -168.86 pts on $4.83 of real loss (points are distorted by dust
  notionals), so -200 is ~1.2x worst-observed and deliberately coarse — USD is the real gate.

On breach this tool writes data/live_halt_<acct>.json {halt, reason, at, day, day_net_usd,
threshold} AND escalates the halted account's losing sym_sides into data/v15_365_priority.json
using the SAME entry shape as tools/v15_loss_streak_watch (state/streak/applied_at/
winning_tag/trades_since_apply/losers/note), merged with — never overwriting — existing entries.
On a non-breach day it removes a stale halt file for that account (self-clearing at day rollover).

PROHIBITED and absent by design: this tool NEVER closes, reduces, sizes, or blocks anything
itself (no loss-closes on live, ever). Exits stay with the technical exits. The actual
OPEN/AUGMENT skip lives in the manager wire, which is PATCH-ONLY in
tools/patches/PENDING_UNLOCK_day_halt_wire.md until the user says 'unlock'.

HONESTY LAW: nothing here promises 'no losing day'. A halt only stops NEW exposure after a
tail loss is already booked; gap risk and adverse drift on open positions are NOT contained.

Usage:
  python3 tools/v15_day_loss_guard.py [--day YYYY-MM-DD] [--accounts ang,fin] [--dry-run]
"""

import argparse
import datetime
import glob
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RETURNS = ROOT / "data" / "per_trade_returns"
DB = os.environ.get("PER_SYM_STORE_DB") or str(ROOT / "data" / "hourly_reconfig" / "per_sym_store.db")
PRIORITY = ROOT / "data" / "v15_365_priority.json"

CRYPTO_ACCOUNTS = ["ang", "fin", "flz", "inf", "men"]

DEFAULT_HALT_NET_USD = -50.0
DEFAULT_HALT_NET_PTS = -200.0


def utc_today() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def halt_threshold_usd() -> float:
    try:
        return float(os.environ.get("V15_DAY_HALT_NET", str(DEFAULT_HALT_NET_USD)))
    except (TypeError, ValueError):
        return DEFAULT_HALT_NET_USD


def halt_threshold_pts() -> float:
    try:
        return float(os.environ.get("V15_DAY_HALT_NET_PTS", str(DEFAULT_HALT_NET_PTS)))
    except (TypeError, ValueError):
        return DEFAULT_HALT_NET_PTS


def halt_path(account: str, root=None) -> Path:
    return Path(root or ROOT) / "data" / f"live_halt_{str(account).lower()}.json"


def halt_active(account: str, root=None, today: str = None):
    """Predicate the manager wire calls. Returns (active: bool, reason: str).

    Active ONLY when data/live_halt_<acct>.json exists, has halt=true, and its day field
    equals today's UTC date. Fail-open: ANY read/parse problem returns (False, ...)."""
    try:
        day = today or utc_today()
        try:
            doc = json.loads(halt_path(account, root).read_text())
        except FileNotFoundError:
            return False, "no halt file"
        except Exception as exc:
            return False, f"fail-open: unreadable halt file ({exc})"
        if not isinstance(doc, dict) or not doc.get("halt"):
            return False, "halt flag not set"
        if str(doc.get("day") or "") != day:
            return False, f"halt day {doc.get('day')} != today {day}"
        return True, str(doc.get("reason") or "day-loss halt active")
    except Exception as exc:
        return False, f"fail-open: {exc}"


def net_of(trade) -> float:
    try:
        return float(trade.get("pnl_pct") or 0.0) - float(trade.get("fees_pct") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def trade_usd(trade):
    """Quote-notional-weighted net USD, or None when exit_price/qty are unusable (fallback case)."""
    try:
        px = float(trade.get("exit_price") or 0.0)
        qty = float(trade.get("qty") or 0.0)
    except (TypeError, ValueError):
        return None
    if px <= 0 or qty <= 0:
        return None
    return net_of(trade) / 100.0 * px * qty


def day_trades(account: str, day: str, root=None):
    """All closed trades for one account whose ts falls on the UTC `day` (YYYY-MM-DD)."""
    out = []
    pat = str(Path(root or ROOT) / "data" / "per_trade_returns" / f"{str(account).lower()}_*.jsonl")
    for fp in glob.glob(pat):
        try:
            fh = open(fp)
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    t = json.loads(line)
                except Exception:
                    continue
                if str(t.get("account") or "").lower() != str(account).lower():
                    continue
                if str(t.get("ts") or "")[:10] != day:
                    continue
                out.append(t)
    out.sort(key=lambda t: str(t.get("ts") or ""))
    return out


def day_total(trades):
    """(day_net_usd, day_net_pts_fallback, n, n_fallback). Pure."""
    usd, pts, nfb = 0.0, 0.0, 0
    for t in trades:
        u = trade_usd(t)
        if u is None:
            pts += net_of(t)
            nfb += 1
        else:
            usd += u
    return usd, pts, len(trades), nfb


def applied_map(db_path=DB):
    """{sym_side: (updated_at_iso, winning_tag)} for live settings. Read-only; {} on any error."""
    out = {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=20.0)
        for ss, ua, tag in con.execute("SELECT sym_side, updated_at, winning_tag FROM per_sym_active"):
            out[str(ss)] = (str(ua or ""), str(tag or ""))
        con.close()
    except Exception:
        return {}
    return out


def check_account(account: str, day: str, thusd: float, thpts: float, root=None):
    """Evaluate one account. Pure except reads. Returns a result dict (never writes)."""
    trades = day_trades(account, day, root)
    dusd, dpts, n, nfb = day_total(trades)
    by_ss = {}
    for t in trades:
        sym, side = t.get("symbol"), str(t.get("side") or "").upper()
        if not sym or side not in ("LONG", "SHORT"):
            continue
        by_ss.setdefault(f"{sym}_{side}", []).append(t)
    ss_net = {ss: sum((trade_usd(t) if trade_usd(t) is not None else 0.0) for t in ts) for ss, ts in by_ss.items()}
    breach_usd = dusd < thusd
    breach_pts = nfb > 0 and dpts < thpts
    metric = "USD_WEIGHTED" if breach_usd or nfb == 0 else ("POINTS_FALLBACK" if breach_pts else "USD_WEIGHTED")
    return {
        "account": str(account).lower(), "day": day, "n_trades": n, "n_fallback": nfb,
        "day_net_usd": round(dusd, 2), "day_net_pts": round(dpts, 2),
        "threshold_usd": thusd, "threshold_pts": thpts,
        "breach": bool(breach_usd or breach_pts), "metric": metric,
        "reason": (f"UTC-day net {dusd:+.2f} USD (< {thusd:+.2f})" if breach_usd else
                   f"fallback points net {dpts:+.2f} (< {thpts:+.2f}) over {nfb} unweightable trades") if (breach_usd or breach_pts) else "",
        "sym_sides": sorted(by_ss), "losing_sym_sides": sorted(ss for ss, v in ss_net.items() if v < 0),
        "_by_ss": by_ss,
    }


def write_halt(account: str, reason: str, day: str, day_net_usd: float, threshold: float, root=None) -> Path:
    p = halt_path(account, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    doc = {"halt": True, "reason": reason, "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "day": day, "account": str(account).lower(), "day_net_usd": day_net_usd, "threshold_usd": threshold,
           "by": "tools/v15_day_loss_guard.py"}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True))
    tmp.replace(p)
    return p


def clear_halt(account: str, root=None) -> bool:
    try:
        halt_path(account, root).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def escalate_priority(losing_by_ss: dict, reason: str, day: str, root=None, db_path=DB):
    """Merge day-halt losers into data/v15_365_priority.json with the loss_streak_watch entry shape.

    Existing entries for other sym_sides are preserved; only the escalated sym_sides are
    added/replaced. Returns (path, [escalated sym_sides])."""
    r = Path(root or ROOT)
    p = r / "data" / "v15_365_priority.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        doc = json.loads(p.read_text())
        if not isinstance(doc, dict):
            doc = {}
    except Exception:
        doc = {}
    doc.setdefault("_meta", {})
    doc.setdefault("sym_sides", {})
    applied = applied_map(db_path)
    escalated = []
    for ss, trades in sorted(losing_by_ss.items()):
        losers = [t for t in trades if net_of(t) < 0]
        run = []
        for t in sorted(trades, key=lambda x: str(x.get("ts") or "")):
            run = run + [t] if net_of(t) < 0 else []
        anchor = applied.get(ss, ("", ""))
        doc["sym_sides"][ss] = {
            "state": "PRIORITIZE_365D", "streak": len(run), "applied_at": anchor[0], "winning_tag": anchor[1],
            "trades_since_apply": len(trades),
            "losers": [{"ts": t.get("ts"), "net_pct": round(net_of(t), 4), "reason": str(t.get("reason") or "")[:90],
                        "hold_minutes": t.get("hold_minutes"), "account": t.get("account")} for t in losers[-5:]],
            "note": f"day-loss halt {day} ({reason}) — 365D verification/optimization prioritized until new settings replace these",
        }
        escalated.append(ss)
    meta = doc["_meta"]
    meta.update({"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "day_halt_day": day, "day_halt_reason": reason, "day_halt_escalated": len(escalated),
                 "rule": "day-loss guard: halted account's losing sym_sides get 365D priority (merge; existing entries preserved)"})
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True))
    tmp.replace(p)
    return p, escalated


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=None, help="UTC day YYYY-MM-DD (default: today)")
    ap.add_argument("--accounts", default=",".join(CRYPTO_ACCOUNTS))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    day = a.day or utc_today()
    thusd, thpts = halt_threshold_usd(), halt_threshold_pts()
    summary = {"day": day, "threshold_usd": thusd, "threshold_pts": thpts, "accounts": {}}
    for acct in [x.strip().lower() for x in a.accounts.split(",") if x.strip()]:
        res = check_account(acct, day, thusd, thpts)
        by_ss = res.pop("_by_ss")
        if res["breach"] and not a.dry_run:
            write_halt(acct, res["reason"], day, res["day_net_usd"], thusd)
            losing = {ss: by_ss[ss] for ss in res["losing_sym_sides"] if ss in by_ss}
            _, res["escalated"] = escalate_priority(losing, res["reason"], day)
        elif not res["breach"] and not a.dry_run:
            res["halt_cleared"] = clear_halt(acct)
        summary["accounts"][acct] = res
    print(json.dumps(summary, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
