#!/usr/bin/env python3
"""v17_conviction — DAILY per-sym_side CONVICTION multiplier (USER 2026-10-14: "it needs to get bolder with entry/reentry quantities
on proven winners (in backtest and live history) so we can dedicate more resources to the proven gainers (whether winners or losers)").

READ-ONLY on every input. Writes ONLY data/conviction/* and the conviction columns of data/persym_size_tiers.json (the existing
PERF_TIER_SIZING path consumed by perf_tier_sizing.get_mult -> ez_manage _psym_sps / OPEN_CEIL_SPS / execute_trade_action OPEN boost
and tradier_manage _qta_entry_sizing). No orders, no config, no engine evals. No Sharpe is computed or written anywhere.

Evidence per sym_side ("gainer" = makes money on ITS side: long winners AND short-side gainers on falling symbols):
  BACKTEST  fresh 30D re-measure of the active final set on the latest NPZ (go-live report evidence; STALE_NPZ -> active-row fallback
            at 0.75 weight), gain vs bh, trades >= 30 floor, TIM in [20,80], DD <= 30, consistency over the last 5 distinct valid boards
            (per_sym_history), 365D (data/confirmed_365d.json, else go-live final_365d): g365 > 0 confirms, g365 <= 0 = overfit (no boost).
  LIVE      full closes from data/per_trade_returns (all accounts of the venue, net of fees, crypto gain from the exit-reason g<x>% as
            stale_set_halt.ledger_row_to_close does), 30d + 7d, shrunk toward 0 with a K=10-trade prior (few trades -> neutral), USD-
            weighted sign check (size-weighted loser cannot be "proven"), median MAE from data/learn/live_leg_*.json.
Verdicts -> mult_target (combined with the tier by combine_entry; the managers never see a parallel file):
  PROVEN      B > 0.15, L > 0.10 (notional-weighted, shrunk), >= 5 effective live closes, live USD >= 0, equal-weight mean net > 0, no 365D overfit -> 1 + (cap-1) * strength, cap = OPEN_CEIL_SPS_MAX_MULT
              (6.0) when 365D-confirmed, UNCONFIRMED_CAP (2.0) otherwise; final = max(tier, target) unless the tier itself is a demotion.
  DISPROVEN   >= 5 live closes and shrunk live score < -0.15 -> min(tier, 0.5)
  TRIGGER     stale-set halt / 2-loss escalation (v15_365_priority) / vigilance block / own last-2-closes-both-losers -> min(tier, 1.0)
  REPROVING   a trigger seen before and not yet re-proven (2 net-winning closes after it cleared) -> min(tier, 1.0)
  NEUTRAL     everything else -> tier unchanged
Live order-time clamp (immediate, no restart): perf_tier_sizing.get_mult caps any > 1 multiplier at 1.0 while a trigger is active.
Kill switch: touch data/conviction/DISABLED (or env V17_CONVICTION_DISABLED=1) -> tiers revert to tier_mult at the next read.
Board gain% is scale-free (v12_quick_engine gain = total_pnl / peak_capital) -> a constant per-sym multiplier does not change any
board number; conviction is a LIVE allocation layer only.

  python tools/v17_conviction.py [--date YYYYMMDD] [--dry-run] [--no-tiers]
"""
import argparse
import datetime as dt
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("V17_CONVICTION_ROOT") or Path(__file__).resolve().parents[1])
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
OUT_DIR = ROOT / "data" / "conviction"
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "BUSD", "FDUSD")
VENUE_ACCOUNTS = {"ez": ("ang", "fin", "men", "flz", "inf"), "tradier": ("tra", "trb", "trc")}
DEFAULT_FEE_PCT = {"ez": 0.04, "tradier": 0.0}
K_SHRINK = 10.0
MIN_LIVE_N = 5
BT_TRADE_FLOOR = 30
UNCONFIRMED_CAP = 2.0
DISPROVEN_MULT = 0.5
FRESH_HOURS = 48.0
DEMOTE_TIERS = ("MIN", "NEG30_POS365", "BOOK_NEG", "UNLISTED_MIN", "NOSTORE_MIN", "RANKED_NEG365_SMALL")
DUST_USD = {"ez": 5.0, "tradier": 0.0}  # Binance futures minimum notional; below it a "close" is dust churn, not a sized trade


def venue_of(ss):
    return "ez" if str(ss).rsplit("_", 1)[0].upper().endswith(CRYPTO_SUFFIX) else "tradier"


def _f(x, d=None):
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


_CFG = {}


def _cfg_val(key, default, venue="ez"):
    try:
        if venue not in _CFG:
            if venue == "ez":
                import config as _c
                _CFG[venue] = _c.Config()
            else:
                import config_tradier as _ct
                _CFG[venue] = _ct.TradierConfig()
        return getattr(_CFG[venue], key, default)
    except Exception:
        return default


def conv_cap():
    return float(_f(_cfg_val("OPEN_CEIL_SPS_MAX_MULT", 6.0), 6.0) or 6.0)


def disabled(root=None):
    r = Path(root) if root else ROOT
    return os.environ.get("V17_CONVICTION_DISABLED") == "1" or (r / "data" / "conviction" / "DISABLED").exists()


# ─────────────────────────────── pure scoring ───────────────────────────────
def backtest_score(ev, active, hist, g365, dd365):
    """Pure. ev = fresh go-live evidence (dict, may be invalid/None), active = per_sym_active light row, hist = [board rows newest first],
    g365/dd365 = 365D numbers or None. Returns {B, g30, bh, trades, tim, dd, src, confirmed365, overfit365, pos_frac, n_boards, notes}."""
    notes = []
    src, w = None, 1.0
    g = bh = n = tim = dd = None
    if isinstance(ev, dict) and ev.get("valid") and _f(ev.get("gain_pct")) is not None and int(ev.get("trades") or 0) > 0:
        g, bh, n, tim, dd, src = _f(ev.get("gain_pct")), _f(ev.get("bh_pct")), int(ev.get("trades") or 0), _f(ev.get("tim_pct")), _f(ev.get("max_dd_pct")), "fresh_remeasure"
    elif isinstance(active, dict) and _f(active.get("gain")) is not None:
        g, bh, n, tim, dd, src, w = _f(active.get("gain")), _f(active.get("bh")), int(active.get("trades") or 0), _f(active.get("tim")), _f(active.get("dd")), "active_row", 0.75
        if isinstance(ev, dict) and ev.get("invalid_reason"):
            notes.append(str(ev.get("invalid_reason"))[:40])
    out = {"B": 0.0, "g30": g, "bh": bh, "trades": n, "tim": tim, "dd": dd, "src": src, "confirmed365": False, "overfit365": False, "pos_frac": None, "n_boards": 0, "notes": notes}
    if g is None:
        out["notes"].append("no_board")
        return out
    b = 0.6 * math.tanh(g / 10.0) + (0.4 * math.tanh((g - bh) / 10.0) if bh is not None else 0.0)
    if b > 0:
        if n is not None and n < BT_TRADE_FLOOR:
            b *= max(0.0, n) / BT_TRADE_FLOOR
            notes.append(f"trades<{BT_TRADE_FLOOR}")
        if tim is not None and not (20.0 <= tim <= 80.0):
            b *= 0.7
            notes.append("TIM_out")
        if dd is not None and dd > 30.0:
            b *= 0.5
            notes.append("DD>30")
    boards = [h for h in (hist or []) if _f(h.get("gain")) is not None][:5]
    out["n_boards"] = len(boards)
    if len(boards) >= 2:
        pf = sum(1 for h in boards if _f(h.get("gain")) > 0) / len(boards)
        out["pos_frac"] = round(pf, 3)
        if b > 0:
            b *= 0.4 + 0.6 * pf
    if g365 is not None:
        if g365 > 0:
            out["confirmed365"] = True
            if b > 0:
                b = min(1.0, b + 0.1)
        else:
            out["overfit365"] = True
            b = min(b, 0.0)
            notes.append("365D<=0")
    if dd365 is not None and dd365 > 30.0 and b > 0:
        b *= 0.5
        notes.append("DD365>30")
    out["B"] = round(max(-1.0, min(1.0, b * w)), 4)
    return out


def shrunk_mean(values, k=K_SHRINK):
    """Pure: mean pulled toward 0 with a k-trade neutral prior (n small -> ~0)."""
    n = len(values)
    if n == 0:
        return 0.0
    return sum(values) / n * n / (n + k)


def weighted_shrunk(closes, k=K_SHRINK):
    """Pure: notional-weighted mean net % shrunk toward 0 by the Kish effective sample size n_eff = (sum w)^2 / sum w^2.
    Dust churn (hundreds of $3 closes) cannot outvote a few real-size trades; equal weights when no notional is known."""
    if not closes:
        return 0.0, 0.0
    w = [float(c.get("notional") or 0.0) for c in closes]
    if sum(w) <= 0:
        w = [1.0] * len(closes)
    sw, sw2 = sum(w), sum(x * x for x in w)
    neff = sw * sw / sw2 if sw2 > 0 else 0.0
    wm = sum(x * c["net_pct"] for x, c in zip(w, closes)) / sw
    return wm * neff / (neff + k), neff


def live_stats(closes, now, mae_list=None, dust_usd=0.0):
    """Pure. closes = [{ts_epoch, net_pct, usd, notional}] full closes. Closes with known notional below dust_usd are dropped (below the
    exchange minimum = dust/phantom churn, not a sized trade). Returns 30d/7d stats + score L in [-1, 1]."""
    c30 = sorted([c for c in closes if now - c["ts_epoch"] <= 30 * 86400 and not (dust_usd and 0 < float(c.get("notional") or 0.0) < dust_usd)], key=lambda c: c["ts_epoch"])
    c7 = [c for c in c30 if now - c["ts_epoch"] <= 7 * 86400]
    r30 = [c["net_pct"] for c in c30]
    r7 = [c["net_pct"] for c in c7]
    (s30, neff30), (s7, _neff7) = weighted_shrunk(c30), weighted_shrunk(c7)
    lsc = 0.7 * math.tanh(s30 / 0.5) + 0.3 * math.tanh(s7 / 0.5)
    mae_med = statistics.median(mae_list) if mae_list else None
    if mae_med is not None and mae_med < -3.0 and lsc > 0:
        lsc *= 0.8
    usd30 = sum(c.get("usd") or 0.0 for c in c30)
    if usd30 < 0 and lsc > 0:
        lsc *= 0.5
    last2 = c30[-2:]
    return {"n30": len(r30), "neff30": round(neff30, 2), "n7": len(r7), "mean30": round(sum(r30) / len(r30), 4) if r30 else None, "mean7": round(sum(r7) / len(r7), 4) if r7 else None,
            "shr30": round(s30, 4), "shr7": round(s7, 4), "wr30": round(sum(1 for x in r30 if x > 0) / len(r30), 3) if r30 else None,
            "usd30": round(usd30, 2), "mae_med": round(mae_med, 3) if mae_med is not None else None, "L": round(max(-1.0, min(1.0, lsc)), 4),
            "last2_losers": len(last2) == 2 and all(c["net_pct"] < 0 for c in last2), "last_ts": c30[-1]["ts_epoch"] if c30 else None}


def decide(tier_mult, bt, lv, triggers, reproving, cap=6.0, unconfirmed_cap=UNCONFIRMED_CAP):
    """Pure -> (verdict, mult_target, strength)."""
    t = float(tier_mult if tier_mult is not None else 1.0)
    if triggers:
        return "TRIGGER", round(min(t, 1.0), 3), 0.0
    if reproving:
        return "REPROVING", round(min(t, 1.0), 3), 0.0
    B, L, n = bt.get("B", 0.0), lv.get("L", 0.0), lv.get("neff30", lv.get("n30", 0))
    if n >= MIN_LIVE_N and L < -0.15:
        return "DISPROVEN", round(min(t, DISPROVEN_MULT), 3), 0.0
    if B > 0.15 and L > 0.10 and n >= MIN_LIVE_N and (lv.get("usd30") or 0.0) >= 0 and (lv.get("mean30") or 0.0) > 0 and not bt.get("overfit365"):
        w = n / (n + 20.0)
        s = (B + w * L) / (1.0 + w)
        strength = max(0.0, min(1.0, (s - 0.15) / 0.55))
        c = cap if bt.get("confirmed365") else min(cap, unconfirmed_cap)
        return "PROVEN", round(max(t, 1.0 + (c - 1.0) * strength), 3), round(strength, 3)
    return "NEUTRAL", round(t, 3), 0.0


def combine_entry(tier_mult, tier_name, conv, now=None, cap=6.0, root=None):
    """Pure-ish (kill switch file only): final multiplier from the tier's own multiplier + the conviction entry. Used by BOTH writers of
    data/persym_size_tiers.json (this tool and tools/v15_persym_size_tiers.py) so the managers keep reading ONE file."""
    t = float(tier_mult if tier_mult is not None else 1.0)
    if not isinstance(conv, dict) or disabled(root):
        return t
    now = now or time.time()
    at = _f(conv.get("at_epoch"))
    if at is None or now - at > FRESH_HOURS * 3600:
        return t
    v, m = conv.get("verdict"), _f(conv.get("mult_target"), t)
    if v == "PROVEN":
        if tier_name in DEMOTE_TIERS:
            return t
        return round(min(max(t, m), cap), 3)
    if v in ("DISPROVEN", "TRIGGER", "REPROVING"):
        return round(min(t, m), 3)
    return t


# ─────────────────────────────── IO (read-only) ───────────────────────────────
def _iso_epoch(ts):
    try:
        return dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def load_ledger(root, now, days=60):
    """{ss: {"closes": [...], "rows": [...]}} — closes = full closes (net), rows = every realized row (USD replay)."""
    import stale_set_halt as _ssh
    out = {}
    d = Path(root) / "data" / "per_trade_returns"
    today = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    for venue, accts in VENUE_ACCOUNTS.items():
        for i in range(days + 1):
            day = (today - dt.timedelta(days=i)).strftime("%Y%m%d")
            for acct in accts:
                p = d / f"{acct}_{day}.jsonl"
                if not p.exists():
                    continue
                for ln in p.open(errors="ignore"):
                    try:
                        e = json.loads(ln)
                    except Exception:
                        continue
                    e.setdefault("account", acct)
                    sym, side = str(e.get("symbol") or "").upper(), str(e.get("side") or "").upper()
                    ts = _iso_epoch(e.get("ts"))
                    if not sym or side not in ("LONG", "SHORT") or ts is None or now - ts > days * 86400:
                        continue
                    ss = f"{sym}_{side}"
                    notional = abs(_f(e.get("qty"), 0.0) or 0.0) * abs(_f(e.get("exit_price"), 0.0) or _f(e.get("entry_price"), 0.0) or 0.0)
                    fee = max(_f(e.get("fees_pct"), 0.0) or 0.0, DEFAULT_FEE_PCT[venue])
                    net_row = (_f(e.get("pnl_pct"), 0.0) or 0.0) - fee
                    slot = out.setdefault(ss, {"closes": [], "rows": [], "venue": venue})
                    life = lifecycle_key(acct, ts, e.get("hold_minutes"))
                    slot["rows"].append({"ts_epoch": ts, "account": acct, "notional": notional, "net_pct": net_row, "usd": notional * net_row / 100.0, "life": life, "qty": _f(e.get("qty"), 0.0)})
                    rec = _ssh.ledger_row_to_close(e, venue)
                    if rec:
                        slot["closes"].append({"ts_epoch": ts, "account": acct, "net_pct": float(rec["net_pct"]), "usd": notional * float(rec["net_pct"]) / 100.0, "notional": notional, "life": life})
    for slot in out.values():
        slot["n_raw_closes"] = len(slot["closes"])
        slot["closes"] = dedupe_lifecycles(slot["closes"], with_qty=False)
        slot["rows"] = dedupe_lifecycles(slot["rows"], with_qty=True)
    return out


def lifecycle_key(account, ts, hold_minutes):
    """Entry moment = ts - hold_minutes, bucketed to 5 min. The live ledger re-logs the SAME close every ~minute while a close is
    retried (LTCUSDC_SHORT men: 675 rows, 2 days, hold_minutes climbing 196.8 -> 198.1 -> ...) — one lifecycle, one trade."""
    h = _f(hold_minutes)
    if h is None:
        return None
    return f"{account}|{int((ts - h * 60.0) // 300)}"


def dedupe_lifecycles(recs, with_qty):
    """Pure: keep the LAST record per (lifecycle[, qty]); records without a lifecycle key are kept as-is."""
    keep, order = {}, []
    for r in sorted(recs, key=lambda r: r["ts_epoch"]):
        if not r.get("life"):
            order.append(id(r))
            keep[id(r)] = r
            continue
        k = (r["life"], round(float(r.get("qty") or 0.0), 8)) if with_qty else r["life"]
        if k not in keep:
            order.append(k)
        keep[k] = r
    return sorted((keep[k] for k in order), key=lambda r: r["ts_epoch"])


def load_mae(root):
    out = {}
    files = sorted((Path(root) / "data" / "learn").glob("live_leg_*.json"))
    if not files:
        return out
    try:
        d = json.loads(files[-1].read_text())
        for t in d.get("trades") or []:
            if t.get("closed") and _f(t.get("mae_pct")) is not None:
                out.setdefault(str(t.get("sym_side")).upper(), []).append(float(t["mae_pct"]))
    except Exception:
        pass
    return out


def load_golive(root):
    """Latest day's go-live reports merged: newest file first, valid evidence preferred."""
    files = sorted((Path(root) / "data" / "daily_chain").glob("persym_golive_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {}, []
    day = files[0].name.split("_")[2][:8]
    use = [p for p in files if p.name.split("_")[2][:8] == day]
    out = {}
    for p in use:
        try:
            ss_map = json.loads(p.read_text()).get("sym_sides") or {}
        except Exception:
            continue
        for ss, r in ss_map.items():
            ev = (r or {}).get("evidence") or {}
            cur = out.get(ss)
            if cur is None or (not (cur.get("evidence") or {}).get("valid") and ev.get("valid")):
                out[ss] = r
    return out, [str(p.relative_to(root)) for p in use]


def load_store(root):
    """per_sym_active light rows + last 5 distinct valid boards per sym_side (read-only sqlite)."""
    import sqlite3
    db = Path(os.environ.get("PER_SYM_STORE_DB") or (Path(root) / "data" / "hourly_reconfig" / "per_sym_store.db"))
    active, hist = {}, {}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10.0)
        for ss, g30, acc, bh, tr, dd, meta in con.execute("SELECT sym_side, gain_30d, acc_gain_pct, bh_pct, trades, max_dd_pct, meta_json FROM per_sym_active"):
            tim = None
            try:
                tim = _f((json.loads(meta) if meta and not str(meta).startswith("z:") else {}).get("tim_pct"))
            except Exception:
                pass
            active[str(ss).upper()] = {"gain": _f(g30) if _f(g30) is not None else _f(acc), "bh": _f(bh), "trades": tr, "dd": _f(dd), "tim": tim}
        seen = {}
        for ss, tag, g, bh, tr, valid in con.execute("SELECT sym_side, winning_tag, gain_pct, bh_pct, trades, valid FROM per_sym_history ORDER BY id DESC"):
            ss = str(ss).upper()
            if not valid or _f(g) is None:
                continue
            key = (str(tag), round(float(g), 3))
            s = seen.setdefault(ss, set())
            if key in s or len(s) >= 5:
                continue
            s.add(key)
            hist.setdefault(ss, []).append({"tag": tag, "gain": float(g), "bh": _f(bh), "trades": tr})
        con.close()
    except Exception as e:
        print(f"[conviction] WARNING per_sym_store read failed: {e}")
    return active, hist


def load_triggers(root):
    """{ss: [causes]} from the live drawdown guards (same files the managers write)."""
    r = Path(root) / "data"
    out = {}
    for venue in ("ez", "tradier"):
        try:
            for ss in (json.loads((r / "stale_set_halt" / f"halts_{venue}.json").read_text()).get("halts") or {}):
                out.setdefault(str(ss).upper(), []).append("STALE_SET_HALT")
        except Exception:
            pass
        try:
            for ss in (json.loads((r / f"vigilance_blocks_{venue}.json").read_text()).get("blocks") or {}):
                out.setdefault(str(ss).upper(), []).append("VIGILANCE_BLOCK")
        except Exception:
            pass
    try:
        for ss in (json.loads((r / "v15_365_priority.json").read_text()).get("sym_sides") or {}):
            out.setdefault(str(ss).upper(), []).append("2LOSS_ESCALATION")
    except Exception:
        pass
    return out


def load_365(root, golive):
    out = {}
    try:
        for ss, c in json.loads((Path(root) / "data" / "confirmed_365d.json").read_text()).items():
            out[str(ss).upper()] = (_f(c.get("gain_365d")), _f(c.get("max_dd_pct")))
    except Exception:
        pass
    for ss, r in golive.items():
        f = (r or {}).get("final_365d")
        if ss not in out and isinstance(f, dict):
            out[ss] = (_f(f.get("gain_pct", f.get("gain"))), _f(f.get("max_dd_pct", f.get("dd"))))
    return out


def sizing_base(ss):
    """(START_POSITION_SIZE $, per-order cap $) for the allocation table — same sources as the managers (per-sym store > config)."""
    venue = venue_of(ss)
    base = float(_cfg_val("START_POSITION_SIZE", 45.0 if venue == "ez" else 500.0, venue))
    mov = float(_cfg_val("MAX_ORDER_VALUE", 90.0 if venue == "ez" else 7000.0, venue))
    try:
        import per_sym_store as _pss
        ov = _pss.get_overrides(ss) or {}
        if venue == "ez" and ov.get("START_POSITION_SIZE_OVERRIDE_USD") is not None:
            base = float(ov["START_POSITION_SIZE_OVERRIDE_USD"])
        elif ov.get("START_POSITION_SIZE") is not None:
            base = float(ov["START_POSITION_SIZE"])
        if ov.get("MAX_ORDER_VALUE") is not None:
            mov = float(ov["MAX_ORDER_VALUE"])
    except Exception:
        pass
    cap = min(conv_cap() * base, mov, float(_cfg_val("OPEN_CEIL_ABSOLUTE_MAX_USD", 8000.0)) if venue == "ez" else mov)
    return base, cap


def max_position(ss, account):
    if venue_of(ss) == "tradier":
        return float(_cfg_val("MAX_POSITION_SIZE", 5000.0, "tradier"))
    if ss.startswith("BTCUSDC"):
        return float(_cfg_val("MAX_POSITION_SIZE_BTC", 2000.0))
    if account in ("men", "fin"):
        return float(_cfg_val(f"MAX_POSITION_SIZE_{account.upper()}", 200.0))
    return float(_cfg_val("MAX_POSITION_SIZE", 200.0))


# ─────────────────────────────── replay ───────────────────────────────
def replay_rows(rows, ratio_for_ts, ss):
    """Pure: realized USD vs the same trades re-sized by ratio (new mult / mult in force), clipped at MAX_POSITION_SIZE."""
    before = after = 0.0
    for r in rows:
        ratio = ratio_for_ts(r["ts_epoch"])
        n0 = r["notional"]
        n1 = n0 * ratio
        lim = max(n0, max_position(ss, r["account"]))
        n1 = min(n1, lim)
        before += r["usd"]
        after += n1 * r["net_pct"] / 100.0
    return before, after


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-tiers", action="store_true", help="do not write the conviction columns into data/persym_size_tiers.json")
    a = ap.parse_args()
    now = time.time()
    cap = conv_cap()
    tiers_path = ROOT / "data" / "persym_size_tiers.json"
    tiers_doc = json.loads(tiers_path.read_text()) if tiers_path.exists() else {"tiers": {}}
    tiers = tiers_doc.get("tiers") or {}
    golive, golive_files = load_golive(ROOT)
    active, hist = load_store(ROOT)
    r365 = load_365(ROOT, golive)
    ledger = load_ledger(ROOT, now)
    mae = load_mae(ROOT)
    trig = load_triggers(ROOT)
    dem_path = OUT_DIR / "demotions.json"
    try:
        demotions = json.loads(dem_path.read_text())
    except Exception:
        demotions = {}
    universe = set(tiers) | {ss for ss, v in ledger.items() if any(now - c["ts_epoch"] <= 30 * 86400 for c in v["closes"])}
    res = {}
    for ss in sorted(universe):
        te = tiers.get(ss) or {}
        t = _f(te.get("tier_mult"), _f(te.get("mult"), 1.0))
        g365, dd365 = r365.get(ss, (None, None))
        bt = backtest_score((golive.get(ss) or {}).get("evidence"), active.get(ss), hist.get(ss), g365, dd365)
        closes = (ledger.get(ss) or {}).get("closes") or []
        lv = live_stats(closes, now, mae.get(ss), DUST_USD.get(venue_of(ss), 0.0))
        causes = list(trig.get(ss, []))
        if lv["last2_losers"]:
            causes.append("OWN_LAST2_LOSERS")
        reproving = False
        if causes:
            d = demotions.setdefault(ss, {"since": dt.datetime.now(dt.timezone.utc).isoformat()})
            d["last_seen_epoch"], d["causes"] = now, causes
        elif ss in demotions:
            after = sorted([c for c in closes if c["ts_epoch"] > float(demotions[ss].get("last_seen_epoch") or 0)], key=lambda c: c["ts_epoch"])
            if len(after) >= 2 and all(c["net_pct"] > 0 for c in after[-2:]):
                demotions.pop(ss, None)
            else:
                reproving = True
        verdict, target, strength = decide(t, bt, lv, causes, reproving, cap)
        final = combine_entry(t, te.get("tier"), {"verdict": verdict, "mult_target": target, "at_epoch": now}, now, cap)
        res[ss] = {"venue": venue_of(ss), "tier": te.get("tier"), "tier_mult": t, "verdict": verdict, "mult_target": target, "final_mult": final, "strength": strength,
                   "causes": causes, "bt": bt, "live": lv, "g365": g365, "in_tiers": ss in tiers}
    # ── capital allocation (per-open notional, capped like execute_now OPEN_CEIL_SPS / MAX_ORDER_VALUE) ──
    alloc = {"ez": {"before": 0.0, "after": 0.0, "proven_before": 0.0, "proven_after": 0.0, "gainers_before": 0.0, "gainers_after": 0.0},
             "tradier": {"before": 0.0, "after": 0.0, "proven_before": 0.0, "proven_after": 0.0, "gainers_before": 0.0, "gainers_after": 0.0}}
    for ss, r in res.items():
        if not r["in_tiers"]:
            continue
        base, ocap = sizing_base(ss)
        b0, b1 = min(base * r["tier_mult"], ocap), min(base * r["final_mult"], ocap)
        r["open_usd_before"], r["open_usd_after"] = round(b0, 2), round(b1, 2)
        A = alloc[r["venue"]]
        A["before"] += b0
        A["after"] += b1
        if r["verdict"] == "PROVEN":
            A["proven_before"] += b0
            A["proven_after"] += b1
        if (r["bt"]["B"] > 0) and (r["live"]["usd30"] > 0):
            A["gainers_before"] += b0
            A["gainers_after"] += b1
    # ── replay of the last 30 d live ledger (REPLAY, not a forecast) ──
    rep = {"in_sample": {"ez": [0.0, 0.0], "tradier": [0.0, 0.0]}, "causal_live": {"ez": [0.0, 0.0], "tradier": [0.0, 0.0]}}
    for ss, r in res.items():
        rows = [x for x in ((ledger.get(ss) or {}).get("rows") or []) if now - x["ts_epoch"] <= 30 * 86400]
        if not rows:
            continue
        t = r["tier_mult"] or 1.0
        b, af = replay_rows(rows, lambda ts, _r=r["final_mult"] / t: _r, ss)
        rep["in_sample"][r["venue"]][0] += b
        rep["in_sample"][r["venue"]][1] += af
        closes = (ledger.get(ss) or {}).get("closes") or []
        cache = {}

        def _ratio(ts, _closes=closes, _r=r, _t=t, _cache=cache):
            day = int(ts // 86400)
            if day not in _cache:
                prior = [c for c in _closes if c["ts_epoch"] < day * 86400]
                lv_d = live_stats(prior, day * 86400.0, None, DUST_USD.get(venue_of(ss), 0.0))
                v_d, m_d, _ = decide(_t, _r["bt"], lv_d, ["OWN_LAST2_LOSERS"] if lv_d["last2_losers"] else [], False, cap)
                _cache[day] = combine_entry(_t, _r["tier"], {"verdict": v_d, "mult_target": m_d, "at_epoch": day * 86400.0}, day * 86400.0, cap) / _t
            return _cache[day]

        b, af = replay_rows(rows, _ratio, ss)
        rep["causal_live"][r["venue"]][0] += b
        rep["causal_live"][r["venue"]][1] += af
    counts = {}
    for r in res.values():
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    meta = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "at_epoch": now, "date": a.date, "cap": cap, "unconfirmed_cap": UNCONFIRMED_CAP, "k_shrink": K_SHRINK, "min_live_n": MIN_LIVE_N,
            "inputs": {"golive": golive_files, "tiers": str(tiers_path.relative_to(ROOT)), "tiers_at": (tiers_doc.get("_meta") or {}).get("at"), "store_active": len(active), "confirmed_365d": len(r365), "ledger_sym_sides": len(ledger), "triggers": len(trig)},
            "counts": counts, "allocation": alloc, "replay": rep, "consumer": "perf_tier_sizing.get_mult via data/persym_size_tiers.json (mult = combine_entry(tier_mult, conviction))",
            "kill_switch": "touch data/conviction/DISABLED (or env V17_CONVICTION_DISABLED=1)", "no_sharpe": True}
    doc = {"_meta": meta, "sym_sides": {ss: {k: v for k, v in r.items()} | {"at_epoch": now} for ss, r in res.items()}}
    md = render_md(doc)
    print(md[:6000])
    if a.dry_run:
        print("[conviction] DRY-RUN: nothing written")
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name in (f"conviction_{a.date}.json", "conviction_latest.json"):
        _atomic_json(OUT_DIR / name, doc)
    (OUT_DIR / f"proof_{a.date}.md").write_text(md)
    _atomic_json(dem_path, demotions)
    if not a.no_tiers and tiers:
        n = apply_to_tiers(tiers_path, doc)
        print(f"[conviction] persym_size_tiers.json: {n} entries carry conviction (mult = combine_entry)")
    print(f"[conviction] {len(res)} sym_sides -> {OUT_DIR / 'conviction_latest.json'}")
    return 0


def _atomic_json(p, obj):
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    json.loads(tmp.read_text())
    os.replace(tmp, p)


def apply_conviction_to_tiers(tiers, conv_doc, now=None, cap=None):
    """Pure on dicts: tier entry -> tier_mult (the tier rule's own value) + conv fields + mult = combine_entry. Idempotent."""
    cap = cap or conv_cap()
    ss_map = (conv_doc or {}).get("sym_sides") or {}
    n = 0
    for ss, te in tiers.items():
        if not isinstance(te, dict):
            continue
        t = _f(te.get("tier_mult"), _f(te.get("mult"), 1.0))
        te["tier_mult"] = t
        c = ss_map.get(ss)
        if not c:
            te["mult"] = t
            te.pop("conviction", None)
            continue
        te["conviction"] = {"verdict": c.get("verdict"), "mult_target": c.get("mult_target"), "at_epoch": c.get("at_epoch"), "strength": c.get("strength"), "causes": c.get("causes")}
        te["mult"] = combine_entry(t, te.get("tier"), te["conviction"], now, cap)
        n += 1
    return n


def apply_to_tiers(tiers_path, conv_doc):
    d = json.loads(tiers_path.read_text())
    tiers = d.get("tiers") or {}
    n = apply_conviction_to_tiers(tiers, conv_doc)
    d.setdefault("_meta", {})["conviction"] = {"at": conv_doc["_meta"]["at"], "file": "data/conviction/conviction_latest.json", "entries": n}
    bk = ROOT / "backups" / f"before_conviction_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M')}_{tiers_path.name}"
    try:
        bk.write_bytes(tiers_path.read_bytes())
    except Exception:
        pass
    _atomic_json(tiers_path, d)
    return n


def latest_conviction(root=None):
    """For tools/v15_persym_size_tiers.py: the conviction doc (or None)."""
    p = Path(root or ROOT) / "data" / "conviction" / "conviction_latest.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def render_md(doc):
    m = doc["_meta"]
    rows = sorted(doc["sym_sides"].items(), key=lambda kv: (-kv[1]["final_mult"], -(kv[1]["bt"]["B"] + kv[1]["live"]["L"])))
    L = [f"# v17 CONVICTION {m['date']} (generated {m['at']})", "", f"cap {m['cap']}x (OPEN_CEIL_SPS_MAX_MULT; 365D-confirmed only), unconfirmed cap {m['unconfirmed_cap']}x, shrink K={m['k_shrink']}, min live closes {m['min_live_n']}. Counts: {json.dumps(m['counts'])}", ""]
    hdr = "| sym_side | tier | tier_x | verdict | final_x | B | g30 | bh | bt_trades | g365 | boards+ | L | live_n30 | neff | mean30% | wr30 | usd30 | causes |"
    sep = "|" + "---|" * 18

    def line(ss, r):
        b, lv = r["bt"], r["live"]
        return f"| {ss} | {r['tier']} | {r['tier_mult']:.2f} | {r['verdict']} | {r['final_mult']:.2f} | {b['B']:+.2f} | {b['g30'] if b['g30'] is None else round(b['g30'], 2)} | {b['bh'] if b['bh'] is None else round(b['bh'], 2)} | {b['trades']} | {r['g365'] if r['g365'] is None else round(r['g365'], 2)} | {b['pos_frac']} | {lv['L']:+.2f} | {lv['n30']} | {lv.get('neff30')} | {lv['mean30']} | {lv['wr30']} | {lv['usd30']} | {','.join(r['causes'])} |"
    L += ["## TOP 30", "", hdr, sep] + [line(ss, r) for ss, r in rows[:30]]
    L += ["", "## BOTTOM 30", "", hdr, sep] + [line(ss, r) for ss, r in rows[-30:]]
    L += ["", "## Capital allocation per open (USD, capped: min(base x mult, OPEN_CEIL_SPS_MAX_MULT x base, MAX_ORDER_VALUE))", ""]
    for v, A in m["allocation"].items():
        if A["before"] > 0:
            L.append(f"- {v}: total per-open ${A['before']:.0f} -> ${A['after']:.0f}; PROVEN share {A['proven_before'] / A['before'] * 100:.1f}% -> {A['proven_after'] / max(A['after'], 1e-9) * 100:.1f}%; board+live gainers share {A['gainers_before'] / A['before'] * 100:.1f}% -> {A['gainers_after'] / max(A['after'], 1e-9) * 100:.1f}%")
    L += ["", "## REPLAY of the last 30 d live ledger (REPLAY, NOT A FORECAST)", "",
          "in_sample = today's multipliers applied to the same trades they were computed from (look-ahead, optimistic). causal_live = the live leg re-scored each day from closes BEFORE that day (backtest leg + tier = today's, still look-ahead). Notional clipped at MAX_POSITION_SIZE; every other cap ignored.", ""]
    for k, vv in m["replay"].items():
        for v, (b, af) in vv.items():
            L.append(f"- {k} {v}: realized ${b:+.2f} -> replay ${af:+.2f} (delta ${af - b:+.2f})")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    sys.exit(main())
