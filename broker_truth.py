"""Broker-verified realized PnL — the arbiter for every money number.

NO-LIES rule 6: a gain without the per-trade returns behind it is
`[UNVERIFIED]`. This module supplies the verified side. It reads only what the
exchange itself reported — `realizedPnl` and `commission` from
`data/exchange_fills/<account>/<SYMBOL>.json` — and never a reconstructed,
inferred or intended number.

Why it exists (measured 2026-10-27): the history-reconstruction path in
`utils.compute_all_stats` and the `data/per_trade_returns` ledger both claimed
the crypto book was PROFITABLE over the same window in which the broker's own
`realizedPnl` showed a loss. Two independent in-house ledgers agreeing with
each other and disagreeing with the exchange is not a tie; the exchange wins.

Commission conversion: a commission charged in BNB is converted at the live BNB
mark from `price_cache_2.json`. Using a wrong BNB price silently rescales every
fee number, so `freshness()` reports the price source too.

STALENESS IS NOT ZERO. `data/exchange_fills` is an incremental per-symbol cache
(`cached_to_ms`). A symbol whose cache lags shows fewer fills, never more, so
every figure here is a FLOOR on activity and must be published with its
coverage. `verified_window_ok()` is the gate: when coverage is poor the caller
must label the number `[UNVERIFIED]` rather than print it as truth.
"""

from __future__ import annotations

import glob
import json
import os
import time
from collections import defaultdict
from pathlib import Path

CRYPTO_ACCOUNTS = ("ang", "fin", "men", "inf", "flz")
TRADIER_ACCOUNTS = ("tra", "trb", "trc")
FRESH_MAX_AGE_H = 6.0
MIN_FRESH_SHARE = 0.60


def _base_path() -> Path:
    env = os.environ.get("BINANCE_BASE_PATH")
    if env:
        return Path(env)
    try:
        import config as _c

        p = getattr(_c, "BASE_PATH", None)
        if p:
            return Path(p)
    except Exception:
        pass
    return Path(__file__).resolve().parent


def _bnb_usd() -> tuple:
    try:
        import fee_model as _fm

        return float(_fm._bnb_usd()), "price_cache_2.json"
    except Exception:
        return 751.0, "hardcoded_fallback"


def freshness(base_path: Path = None) -> dict:
    """Coverage of the fill cache: how much of it is current enough to trust."""
    root = Path(base_path) if base_path else _base_path()
    now_ms = time.time() * 1000.0
    ages = []
    for p in glob.glob(str(root / "data" / "exchange_fills" / "*" / "*.json")):
        try:
            c = float(json.loads(Path(p).read_text()).get("cached_to_ms") or 0)
        except Exception:
            continue
        ages.append((now_ms - c) / 3600000.0)
    if not ages:
        return {"caches": 0, "fresh_share": 0.0, "ok": False, "bnb_source": _bnb_usd()[1]}
    ages.sort()
    fresh = sum(1 for a in ages if a <= FRESH_MAX_AGE_H)
    share = fresh / len(ages)
    return {
        "caches": len(ages),
        "fresh": fresh,
        "stale": len(ages) - fresh,
        "fresh_share": round(share, 4),
        "age_h_median": round(ages[len(ages) // 2], 2),
        "age_h_p90": round(ages[int(0.9 * len(ages))], 2),
        "age_h_max": round(ages[-1], 2),
        "ok": share >= MIN_FRESH_SHARE,
        "bnb_usd": _bnb_usd()[0],
        "bnb_source": _bnb_usd()[1],
        "note": "figures are a FLOOR on activity; a lagging cache shows fewer fills, never more",
    }


def verified_window_ok(base_path: Path = None) -> bool:
    return bool(freshness(base_path).get("ok"))


def realized(window_days: float = 30.0, accounts=None, base_path: Path = None) -> dict:
    """Exchange-reported realized PnL, commission and turnover.

    Returns per-account, per-symbol and totals. `net_usd = realized - fees`.
    Only CLOSING fills carry `realizedPnl`, so `closes` counts those.
    """
    root = Path(base_path) if base_path else _base_path()
    accts = tuple(accounts) if accounts else CRYPTO_ACCOUNTS
    bnb, _src = _bnb_usd()
    cutoff_ms = (time.time() - float(window_days) * 86400.0) * 1000.0
    by_acct = defaultdict(lambda: {"realized_usd": 0.0, "fees_usd": 0.0, "turnover_usd": 0.0, "fills": 0, "closes": 0})
    by_symbol = defaultdict(lambda: {"realized_usd": 0.0, "fees_usd": 0.0, "turnover_usd": 0.0, "fills": 0, "closes": 0})
    for acct in accts:
        for p in glob.glob(str(root / "data" / "exchange_fills" / acct / "*.json")):
            try:
                rows = json.loads(Path(p).read_text()).get("rows") or []
            except Exception:
                continue
            for r in rows:
                try:
                    if float(r.get("time") or 0) < cutoff_ms:
                        continue
                    pnl = float(r.get("realizedPnl") or 0.0)
                    fee = abs(float(r.get("commission") or 0.0))
                    if str(r.get("commissionAsset") or "") == "BNB":
                        fee *= bnb
                    q = abs(float(r.get("quoteQty") or 0.0))
                except (TypeError, ValueError):
                    continue
                sym = str(r.get("symbol") or "")
                for bucket in (by_acct[acct], by_symbol[sym]):
                    bucket["realized_usd"] += pnl
                    bucket["fees_usd"] += fee
                    bucket["turnover_usd"] += q
                    bucket["fills"] += 1
                    if pnl != 0.0:
                        bucket["closes"] += 1
    def _fin(d):
        out = {}
        for k, v in d.items():
            v = dict(v)
            v["net_usd"] = v["realized_usd"] - v["fees_usd"]
            t = v["turnover_usd"]
            v["net_pct_of_turnover"] = (100.0 * v["net_usd"] / t) if t else 0.0
            v["fee_pct_of_turnover"] = (100.0 * v["fees_usd"] / t) if t else 0.0
            out[k] = {kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in v.items()}
        return out
    accounts_out = _fin(by_acct)
    tot = {"realized_usd": 0.0, "fees_usd": 0.0, "turnover_usd": 0.0, "fills": 0, "closes": 0}
    for v in by_acct.values():
        for k in tot:
            tot[k] += v[k]
    tot["net_usd"] = tot["realized_usd"] - tot["fees_usd"]
    tot["net_pct_of_turnover"] = (100.0 * tot["net_usd"] / tot["turnover_usd"]) if tot["turnover_usd"] else 0.0
    tot["fee_pct_of_turnover"] = (100.0 * tot["fees_usd"] / tot["turnover_usd"]) if tot["turnover_usd"] else 0.0
    tot["fee_share_of_loss_pct"] = (100.0 * tot["fees_usd"] / abs(tot["net_usd"])) if tot["net_usd"] < 0 else None
    fr = freshness(root)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "window_days": float(window_days),
        "accounts_requested": list(accts),
        "verified": bool(fr.get("ok")),
        "label": "VERIFIED" if fr.get("ok") else "[UNVERIFIED] fill cache coverage below threshold",
        "freshness": fr,
        "totals": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in tot.items()},
        "by_account": accounts_out,
        "by_symbol": _fin(by_symbol),
    }


def realized_by_sym_side(window_days: float = 14.0, accounts=None, base_path: Path = None) -> dict:
    """Exchange-reported net per `SYMBOL_SIDE`, keyed off each fill's positionSide.

    This is the authority for tiering and position sizing. Every fill carries
    `positionSide` (verified 2026-10-27: 10,086 LONG / 7,886 SHORT, no blanks),
    so LONG and SHORT never have to be blended or guessed at. `win_rate` counts
    only closing fills (those with a non-zero `realizedPnl`), and `net_usd` is
    net of commission, converted from BNB at the live mark.
    """
    root = Path(base_path) if base_path else _base_path()
    accts = tuple(accounts) if accounts else CRYPTO_ACCOUNTS
    bnb, _src = _bnb_usd()
    cutoff_ms = (time.time() - float(window_days) * 86400.0) * 1000.0
    agg = defaultdict(lambda: {"realized_usd": 0.0, "fees_usd": 0.0, "turnover_usd": 0.0, "fills": 0, "closes": 0, "wins": 0, "accounts": set()})
    for acct in accts:
        for p in glob.glob(str(root / "data" / "exchange_fills" / acct / "*.json")):
            try:
                rows = json.loads(Path(p).read_text()).get("rows") or []
            except Exception:
                continue
            for r in rows:
                try:
                    if float(r.get("time") or 0) < cutoff_ms:
                        continue
                    pnl = float(r.get("realizedPnl") or 0.0)
                    fee = abs(float(r.get("commission") or 0.0))
                    if str(r.get("commissionAsset") or "") == "BNB":
                        fee *= bnb
                    q = abs(float(r.get("quoteQty") or 0.0))
                except (TypeError, ValueError):
                    continue
                side = str(r.get("positionSide") or "").upper()
                sym = str(r.get("symbol") or "")
                if side not in ("LONG", "SHORT") or not sym:
                    continue
                v = agg[f"{sym}_{side}"]
                v["realized_usd"] += pnl
                v["fees_usd"] += fee
                v["turnover_usd"] += q
                v["fills"] += 1
                v["accounts"].add(acct)
                if pnl != 0.0:
                    v["closes"] += 1
                    if pnl - 0.0 > 0:
                        v["wins"] += 1
    out = {}
    for k, v in agg.items():
        net = v["realized_usd"] - v["fees_usd"]
        closes = v["closes"]
        turn = v["turnover_usd"]
        sym, side = k.rsplit("_", 1)
        out[k] = {
            "sym_side": k,
            "symbol": sym,
            "side": side,
            "realized_usd": round(v["realized_usd"], 4),
            "fees_usd": round(v["fees_usd"], 4),
            "net_usd": round(net, 4),
            "turnover_usd": round(turn, 2),
            "fills": v["fills"],
            "closes": closes,
            "win_rate": round(v["wins"] / closes, 4) if closes else 0.0,
            # Net return per unit of capital actually put at risk. Turnover
            # counts both legs, so halve it to approximate deployed capital.
            "net_pct_of_deployed": round(200.0 * net / turn, 5) if turn else 0.0,
            "accounts": sorted(v["accounts"]),
        }
    return out


def reconcile(history_stats: dict, window_days: float = 14.0, base_path: Path = None) -> dict:
    """Compare in-house per-symbol net against the exchange. Divergence is a bug.

    `history_stats` is `utils.compute_all_stats()` output. Any symbol whose
    in-house net disagrees with the exchange by more than `tol_usd` is listed:
    in-house accounting may never be published as truth while it diverges.
    """
    truth = realized(window_days, CRYPTO_ACCOUNTS, base_path)
    bysym = truth["by_symbol"]
    rows = []
    h_net = b_net = 0.0
    for key, v in (history_stats or {}).items():
        if v.get("side") not in ("LONG", "SHORT"):
            continue
        sym = v.get("symbol")
        if sym not in bysym:
            continue
        hn = float(v.get("net_usd") or 0.0)
        h_net += hn
    seen = set()
    for key, v in (history_stats or {}).items():
        if v.get("side") not in ("LONG", "SHORT"):
            continue
        sym = v.get("symbol")
        if not sym or sym in seen or sym not in bysym:
            continue
        seen.add(sym)
        bn = float(bysym[sym]["net_usd"])
        hn = sum(
            float(x.get("net_usd") or 0.0)
            for k2, x in history_stats.items()
            if x.get("symbol") == sym and x.get("side") in ("LONG", "SHORT")
        )
        b_net += bn
        rows.append({"symbol": sym, "broker_net_usd": round(bn, 4), "inhouse_net_usd": round(hn, 4), "divergence_usd": round(hn - bn, 4)})
    rows.sort(key=lambda r: -abs(r["divergence_usd"]))
    return {
        "generated_at": truth["generated_at"],
        "window_days": float(window_days),
        "verified": truth["verified"],
        "label": truth["label"],
        "broker_net_usd": round(b_net, 4),
        "inhouse_net_usd": round(h_net, 4),
        "divergence_usd": round(h_net - b_net, 4),
        "symbols_compared": len(rows),
        "worst": rows[:40],
        "verdict": "OK" if abs(h_net - b_net) <= max(5.0, 0.05 * abs(b_net)) else "IN-HOUSE ACCOUNTING DIVERGES FROM BROKER — do not publish in-house gains as truth",
    }


def write_report(window_days: float = 30.0, base_path: Path = None) -> Path:
    root = Path(base_path) if base_path else _base_path()
    doc = realized(window_days, CRYPTO_ACCOUNTS, root)
    try:
        import utils as _u

        doc["reconcile_14d"] = reconcile(_u.compute_all_stats(), 14.0, root)
    except Exception as e:
        doc["reconcile_14d"] = {"error": repr(e)}
    out = root / "data" / "reports" / "broker_truth.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
    os.replace(tmp, out)
    return out


if __name__ == "__main__":
    import sys

    wd = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    d = realized(wd)
    t = d["totals"]
    print(f"BROKER TRUTH — {d['window_days']:.0f}d — {d['label']}")
    print(f"  coverage: {d['freshness']['fresh']}/{d['freshness']['caches']} caches fresh (<{FRESH_MAX_AGE_H}h), median age {d['freshness']['age_h_median']}h, BNB ${d['freshness']['bnb_usd']} from {d['freshness']['bnb_source']}")
    print(f"  {'acct':6s}{'gross$':>11s}{'fees$':>10s}{'NET$':>11s}{'turnover$':>12s}{'closes':>8s}")
    for a, v in sorted(d["by_account"].items()):
        print(f"  {a:6s}{v['realized_usd']:11.2f}{v['fees_usd']:10.2f}{v['net_usd']:11.2f}{v['turnover_usd']:12.0f}{v['closes']:8d}")
    print(f"  {'ALL':6s}{t['realized_usd']:11.2f}{t['fees_usd']:10.2f}{t['net_usd']:11.2f}{t['turnover_usd']:12.0f}{t['closes']:8d}")
    print(f"  net {t['net_pct_of_turnover']:+.4f}% of turnover | fees {t['fee_pct_of_turnover']:.4f}% | fees are {t['fee_share_of_loss_pct'] and round(t['fee_share_of_loss_pct'])}% of the loss")
    print(write_report(wd))
