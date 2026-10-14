"""per_trade_ledger — distinct-trade view of data/per_trade_returns (USER 2026-10-14).

The raw ledger is history and is never rewritten. Before 2026-10-14 the live managers logged a row per close ATTEMPT
(retry storms: LTCUSDC_SHORT men 661 rows = 1 lifecycle, MANAUSDT_SHORT 708 rows of $3 dust). This module turns raw rows
into distinct confirmed trades, and every loss guard / learner reads through it:

  lifecycle  = row["lifecycle"] (new confirmed rows) else account|SYMBOL|SIDE|~opened, opened = ts - hold_minutes
               (identical for every retry of one position; rows of one sym_side whose opened estimates lie within
               LIFECYCLE_TOL_S are one lifecycle). hold_minutes 0 (legacy rows without opened_at): bursts with gaps
               < BURST_GAP_S are one lifecycle.
  distinct   = new rows: one per idem_key. legacy rows: one per (lifecycle, qty) — repeated identical-qty attempts
               collapse to the LAST attempt (last exit price, last pnl).
  final      = the latest distinct row of each lifecycle (the close the loss guards count).
  dust       = qty * exit_price < DUST_USD (default $5) — never counted as a win or a loss.

CLI (backfill a clean derived ledger, history untouched):
    python per_trade_ledger.py --days 30            -> data/per_trade_returns_clean/{acct}_{day}.jsonl + SUMMARY.json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent
LIFECYCLE_TOL_S = 180.0
BURST_GAP_S = 900.0
DUST_USD = 5.0
VENUE_ACCOUNTS = {"ez": ("ang", "fin", "men", "flz", "inf"), "tradier": ("tra", "trb", "trc")}


def _f(x, d=0.0) -> float:
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def _epoch(ts) -> Optional[float]:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except Exception:
        return None


def notional(row: dict) -> float:
    n = _f(row.get("notional_usd"), 0.0)
    if n > 0:
        return n
    return abs(_f(row.get("qty"))) * abs(_f(row.get("exit_price")) or _f(row.get("entry_price")))


def is_dust(row: dict, min_usd: float = DUST_USD) -> bool:
    n = notional(row)
    return 0.0 < n < float(min_usd)  # unknown notional (qty/price missing) is never treated as dust


def _ss(row: dict) -> str:
    return f"{str(row.get('symbol') or '').upper()}_{str(row.get('side') or '').upper()}"


def assign_lifecycles(rows: Iterable[dict]) -> List[dict]:
    """Pure: returns copies of rows with _epoch, _opened, _life set (legacy rows clustered per account+sym_side)."""
    out = []
    groups: Dict[tuple, List[dict]] = defaultdict(list)
    for r in rows:
        e = _epoch(r.get("ts"))
        if e is None:
            continue
        x = dict(r)
        x["_epoch"] = e
        h = _f(x.get("hold_minutes"), 0.0)
        x["_opened"] = e - h * 60.0 if h > 0 else None
        if x.get("lifecycle"):
            x["_life"] = str(x["lifecycle"])
            out.append(x)
        else:
            groups[(str(x.get("account") or ""), _ss(x))].append(x)
    for (acct, ss), g in groups.items():
        with_open = sorted([x for x in g if x["_opened"] is not None], key=lambda x: x["_opened"])
        cur, anchor = None, None
        for x in with_open:
            if cur is None or x["_opened"] - anchor > LIFECYCLE_TOL_S:
                anchor = x["_opened"]
                cur = f"{acct}|{ss}|~{datetime.fromtimestamp(anchor, timezone.utc).strftime('%Y-%m-%dT%H:%M')}"
            x["_life"] = cur
            out.append(x)
        no_open = sorted([x for x in g if x["_opened"] is None], key=lambda x: x["_epoch"])
        cur, last = None, None
        for x in no_open:
            if cur is None or x["_epoch"] - last > BURST_GAP_S:
                cur = f"{acct}|{ss}|burst{datetime.fromtimestamp(x['_epoch'], timezone.utc).strftime('%Y-%m-%dT%H:%M')}"
            last = x["_epoch"]
            x["_life"] = cur
            out.append(x)
    out.sort(key=lambda x: x["_epoch"])
    return out


def distinct_rows(rows: Iterable[dict]) -> List[dict]:
    """Pure: distinct trades (see module doc). Each output row carries lifecycle, n_raw (attempt rows it absorbed),
    lifecycle_final (latest distinct row of its lifecycle), dust, notional_usd."""
    rs = assign_lifecycles(rows)
    keep: Dict[tuple, dict] = {}
    count: Dict[tuple, int] = defaultdict(int)
    order: List[tuple] = []
    for x in rs:
        if x.get("idem_key"):
            k = ("idem", str(x.get("account")), str(x["idem_key"]))
        else:
            k = ("legacy", x["_life"], round(abs(_f(x.get("qty"))), 8))
        if k not in keep:
            order.append(k)
        keep[k] = x
        count[k] += 1
    out = []
    last_by_life: Dict[str, float] = {}
    for k in order:
        x = keep[k]
        last_by_life[x["_life"]] = max(last_by_life.get(x["_life"], 0.0), x["_epoch"])
    for k in order:
        x = dict(keep[k])
        x["lifecycle"] = x.get("lifecycle") or x["_life"]
        x["n_raw"] = count[k]
        x["lifecycle_final"] = x["_epoch"] >= last_by_life[x["_life"]]
        x["notional_usd"] = round(notional(x), 4)
        x["dust"] = is_dust(x)
        if k[0] == "legacy":
            x["derived"] = "legacy_lifecycle_collapse"
        out.append(x)
    out.sort(key=lambda x: x["_epoch"])
    return out


def strip(row: dict) -> dict:
    return {k: v for k, v in row.items() if not k.startswith("_")}


def load_raw(root: Path = ROOT, accounts: Optional[Iterable[str]] = None, days: int = 30, now: Optional[float] = None) -> List[dict]:
    d = Path(root) / "data" / "per_trade_returns"
    today = datetime.fromtimestamp(now, timezone.utc) if now else datetime.now(timezone.utc)
    accts = set(accounts) if accounts else None
    rows = []
    for i in range(int(days) + 1):
        day = (today - timedelta(days=i)).strftime("%Y%m%d")
        for p in d.glob(f"*_{day}.jsonl"):
            acct = p.name.rsplit("_", 1)[0]
            if accts is not None and acct not in accts:
                continue
            try:
                with open(p, errors="ignore") as fh:
                    for ln in fh:
                        try:
                            e = json.loads(ln)
                        except Exception:
                            continue
                        e.setdefault("account", acct)
                        rows.append(e)
            except Exception:
                continue
    return rows


def load_distinct(root: Path = ROOT, accounts: Optional[Iterable[str]] = None, days: int = 30, now: Optional[float] = None) -> List[dict]:
    return distinct_rows(load_raw(root, accounts, days, now))


def final_closes(rows: Iterable[dict], min_usd: float = DUST_USD) -> List[dict]:
    """Distinct lifecycle-final, non-dust rows (input: distinct_rows output). What a loss guard counts."""
    return [r for r in rows if r.get("lifecycle_final") and not is_dust(r, min_usd)]


def build_clean(root: Path = ROOT, days: int = 30, out_dir: Optional[Path] = None, now: Optional[float] = None) -> dict:
    root = Path(root)
    out_dir = Path(out_dir) if out_dir else root / "data" / "per_trade_returns_clean"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = load_raw(root, None, days, now)
    dist = distinct_rows(raw)
    by_file: Dict[str, List[dict]] = defaultdict(list)
    for r in dist:
        by_file[f"{r.get('account')}_{datetime.fromtimestamp(r['_epoch'], timezone.utc).strftime('%Y%m%d')}.jsonl"].append(strip(r))
    for name, rs in by_file.items():
        tmp = out_dir / (name + ".tmp")
        with open(tmp, "w") as fh:
            for r in rs:
                fh.write(json.dumps(r, separators=(",", ":"), default=str) + "\n")
        tmp.replace(out_dir / name)
    per_ss: Dict[str, dict] = defaultdict(lambda: {"raw_rows": 0, "distinct_rows": 0, "lifecycles": 0, "final_non_dust": 0, "dust_rows": 0})
    for r in raw:
        per_ss[f"{r.get('account')}:{_ss(r)}"]["raw_rows"] += 1
    lifes = defaultdict(set)
    for r in dist:
        k = f"{r.get('account')}:{_ss(r)}"
        per_ss[k]["distinct_rows"] += 1
        lifes[k].add(r["lifecycle"])
        if r["dust"]:
            per_ss[k]["dust_rows"] += 1
        if r["lifecycle_final"] and not r["dust"]:
            per_ss[k]["final_non_dust"] += 1
    for k, s in lifes.items():
        per_ss[k]["lifecycles"] = len(s)
    summary = {"built_at": datetime.now(timezone.utc).isoformat(), "days": days, "raw_rows": len(raw), "distinct_rows": len(dist), "lifecycles": len({r["lifecycle"] for r in dist}), "final_non_dust_closes": len(final_closes(dist)), "rule": "per_trade_ledger.distinct_rows (lifecycle = ts - hold_minutes, dust < $%g)" % DUST_USD, "top_inflated": sorted(({"key": k, **v} for k, v in per_ss.items()), key=lambda x: -x["raw_rows"] + x["distinct_rows"])[:40]}
    (out_dir / "SUMMARY.json").write_text(json.dumps(summary, indent=1, default=str))
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    s = build_clean(Path(a.root), a.days, Path(a.out) if a.out else None)
    print(json.dumps({k: s[k] for k in ("raw_rows", "distinct_rows", "lifecycles", "final_non_dust_closes")}, indent=1))
    for t in s["top_inflated"][:12]:
        print(f"  {t['key']:<28} raw={t['raw_rows']:<5} distinct={t['distinct_rows']:<4} lifecycles={t['lifecycles']:<3} final_non_dust={t['final_non_dust']:<3} dust_rows={t['dust_rows']}")


if __name__ == "__main__":
    main()
