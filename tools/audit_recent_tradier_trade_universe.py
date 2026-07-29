#!/usr/bin/env python3
"""Read-only audit of stock symbol/sides actually traded at Tradier.

The broker account-history endpoint is authoritative.  Local ``/history``
ledgers and the current symbol registries are reported only as reconciliation
evidence; neither can manufacture an executed trade.

The default window is the current UTC calendar day plus the previous 29
calendar days: ``[today-29d 00:00Z, tomorrow 00:00Z)``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACCOUNTS = ("tra", "trb", "trc")
ENTRY_ACTIONS = {
    "buy": "LONG",
    "buy_to_open": "LONG",
    "sell_short": "SHORT",
    "sell_to_open": "SHORT",
}
EXIT_ACTIONS = {
    "sell": "LONG",
    "sell_to_close": "LONG",
    "buy_to_cover": "SHORT",
    "buy_to_close": "SHORT",
}


def utc_calendar_window(as_of: date, days: int) -> tuple[datetime, datetime]:
    if days < 1:
        raise ValueError("days must be >= 1")
    start = datetime.combine(as_of - timedelta(days=days - 1), time(), timezone.utc)
    end = datetime.combine(as_of + timedelta(days=1), time(), timezone.utc)
    return start, end


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_action(value: Any) -> str:
    return (
        str(value or "")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )


def is_option_symbol(symbol: str) -> bool:
    """Recognize OCC option symbols while retaining ordinary stock tickers."""
    compact = symbol.replace(" ", "")
    if len(compact) < 15:
        return False
    tail = compact[-15:]
    return (
        tail[:6].isdigit()
        and tail[6:7] in {"C", "P"}
        and tail[7:].isdigit()
    )


def classify_trade(row: dict[str, Any]) -> tuple[str, str, str] | None:
    """Return ``(symbol, side, action)`` for an equity execution.

    Account-history payloads have used both top-level trade fields and nested
    ``trade`` payloads.  Unknown actions fail closed instead of guessing an
    inverse side.
    """
    body = row.get("trade") if isinstance(row.get("trade"), dict) else row
    symbol = str(
        body.get("symbol")
        or body.get("underlying")
        or row.get("symbol")
        or ""
    ).strip().upper()
    action = normalize_action(
        body.get("side")
        or body.get("action")
        or body.get("transaction_type")
        or row.get("side")
        or row.get("action")
    )
    security = normalize_action(
        body.get("security_type")
        or body.get("class")
        or body.get("type")
        or row.get("security_type")
    )
    if not symbol or is_option_symbol(symbol) or security in {
        "option",
        "options",
        "index_option",
    }:
        return None
    if action in ENTRY_ACTIONS:
        return symbol, ENTRY_ACTIONS[action], action
    if action in EXIT_ACTIONS:
        return symbol, EXIT_ACTIONS[action], action
    return None


def event_ts(row: dict[str, Any]) -> datetime | None:
    body = row.get("trade") if isinstance(row.get("trade"), dict) else row
    for key in (
        "date",
        "transaction_date",
        "trade_date",
        "executed_at",
        "created_at",
    ):
        parsed = parse_ts(body.get(key) or row.get(key))
        if parsed is not None:
            return parsed
    return None


def flatten_history(payload: Any) -> list[dict[str, Any]]:
    """Flatten the documented history/event wrapper defensively."""
    if not isinstance(payload, dict):
        return []
    node: Any = payload.get("history", payload)
    if isinstance(node, dict):
        node = node.get("event", node.get("events", node.get("trade", [])))
    if isinstance(node, dict):
        node = [node]
    if not isinstance(node, list):
        return []
    return [row for row in node if isinstance(row, dict)]


def load_encrypted_env(root: Path) -> None:
    encrypted = root / ".env.gpg"
    result = subprocess.run(
        ["gpg", "--batch", "--yes", "--decrypt", str(encrypted)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("gpg environment decrypt failed")
    try:
        from dotenv import dotenv_values
    except ImportError as exc:
        raise RuntimeError("python-dotenv is required") from exc
    for key, value in dotenv_values(stream=StringIO(result.stdout)).items():
        if value and str(value).strip():
            os.environ[str(key)] = str(value).strip()


def broker_get(
    account: str,
    endpoint: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    api_key = os.environ.get(f"TRADIER_API_KEY_{account.upper()}")
    account_id = os.environ.get(f"TRADIER_ACCOUNT_ID_{account.upper()}")
    if not api_key or not account_id:
        raise RuntimeError(f"{account}: missing broker credentials")
    base = (
        "https://sandbox.tradier.com/v1"
        if account == "trc"
        else "https://api.tradier.com/v1"
    )
    query = urllib.parse.urlencode(params)
    url = f"{base}/accounts/{account_id}/{endpoint}"
    if query:
        url += f"?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"{account}: broker history HTTP {exc.code}: {detail}"
        ) from exc


def fetch_history(
    account: str,
    start: datetime,
    end: datetime,
    *,
    page_limit: int = 100,
    max_pages: int = 100,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch paginated trade history, returning rows and page receipts."""
    rows: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        payload = broker_get(
            account,
            "history",
            {
                "type": "trade",
                "start": start.date().isoformat(),
                "end": (end - timedelta(microseconds=1)).date().isoformat(),
                "page": page,
                "limit": page_limit,
            },
        )
        batch = flatten_history(payload)
        signature = hashlib.sha256(
            json.dumps(batch, sort_keys=True, default=str).encode()
        ).hexdigest()
        receipts.append(
            {
                "page": page,
                "row_count": len(batch),
                "payload_sha256": signature,
            }
        )
        if not batch or signature in seen:
            break
        seen.add(signature)
        rows.extend(batch)
        if len(batch) < page_limit:
            break
    return rows, receipts


def iter_ledger_events(
    root: Path,
    account: str,
    start: datetime,
    end: datetime,
) -> Iterable[tuple[str, str, dict[str, Any]]]:
    for base in (root / "data/history", root / "data/tradier/history"):
        directory = base / account
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.jsonl")):
            stem = path.stem
            if stem.endswith("_LONG"):
                symbol, side = stem[:-5], "LONG"
            elif stem.endswith("_SHORT"):
                symbol, side = stem[:-6], "SHORT"
            else:
                continue
            for line in path.read_text(errors="replace").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = parse_ts(row.get("ts"))
                if ts is not None and start <= ts < end:
                    yield symbol.upper(), side, row


def load_registry(root: Path, account: str) -> set[tuple[str, str]]:
    rows: set[tuple[str, str]] = set()
    for side in ("LONG", "SHORT"):
        path = root / f"symbols_{account}_{side.lower()}.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, list):
            rows.update(
                (str(symbol).upper(), side)
                for symbol in payload
                if isinstance(symbol, str) and symbol.strip()
            )
    return rows


def flatten_gainloss(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    node: Any = (payload.get("gainloss") or {}).get("closed_position", [])
    if isinstance(node, dict):
        node = [node]
    if not isinstance(node, list):
        return []
    return [row for row in node if isinstance(row, dict)]


def flatten_positions(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    node: Any = (payload.get("positions") or {}).get("position", [])
    if isinstance(node, dict):
        node = [node]
    if not isinstance(node, list):
        return []
    return [row for row in node if isinstance(row, dict)]


def fetch_account_evidence(
    account: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    history, history_pages = fetch_history(account, start, end)
    gainloss_payload = broker_get(
        account,
        "gainloss",
        {
            "start": start.date().isoformat(),
            "end": (end - timedelta(microseconds=1)).date().isoformat(),
            "limit": 5000,
        },
    )
    positions_payload = broker_get(account, "positions", {})
    gainloss = flatten_gainloss(gainloss_payload)
    positions = flatten_positions(positions_payload)
    return {
        "history": history,
        "history_pages": history_pages,
        "gainloss": gainloss,
        "gainloss_payload_sha256": hashlib.sha256(
            json.dumps(gainloss, sort_keys=True, default=str).encode()
        ).hexdigest(),
        "positions": positions,
        "positions_payload_sha256": hashlib.sha256(
            json.dumps(positions, sort_keys=True, default=str).encode()
        ).hexdigest(),
    }


def side_from_quantity(value: Any) -> str | None:
    try:
        quantity = float(value)
    except (TypeError, ValueError):
        return None
    if quantity > 0:
        return "LONG"
    if quantity < 0:
        return "SHORT"
    return None


def build_audit(
    *,
    root: Path,
    accounts: Iterable[str],
    start: datetime,
    end: datetime,
    fetcher=fetch_account_evidence,
) -> dict[str, Any]:
    keys: Counter[tuple[str, str]] = Counter()
    open_keys: Counter[tuple[str, str]] = Counter()
    per_account: dict[str, Any] = {}
    unknown_actions: Counter[str] = Counter()
    raw_history_total = 0
    closed_lot_total = 0
    for account in accounts:
        evidence = fetcher(account, start, end)
        rows = evidence["history"]
        gainloss = evidence["gainloss"]
        positions = evidence["positions"]
        raw_history_total += len(rows)
        closed_lot_total += len(gainloss)
        account_keys: Counter[tuple[str, str]] = Counter()
        account_open_keys: Counter[tuple[str, str]] = Counter()
        excluded_closed = 0
        for row in gainloss:
            symbol = str(row.get("symbol") or "").strip().upper()
            side = side_from_quantity(row.get("quantity"))
            close_ts = parse_ts(row.get("close_date"))
            if (
                not symbol
                or side is None
                or is_option_symbol(symbol)
                or close_ts is None
                or not (start <= close_ts < end)
            ):
                excluded_closed += 1
                continue
            account_keys[(symbol, side)] += 1
            keys[(symbol, side)] += 1
        excluded_positions = 0
        for row in positions:
            symbol = str(row.get("symbol") or "").strip().upper()
            side = side_from_quantity(row.get("quantity"))
            acquired = parse_ts(row.get("date_acquired"))
            if (
                not symbol
                or side is None
                or is_option_symbol(symbol)
                or acquired is None
                or not (start <= acquired < end)
            ):
                excluded_positions += 1
                continue
            account_open_keys[(symbol, side)] += 1
            open_keys[(symbol, side)] += 1
            if (symbol, side) not in keys:
                keys[(symbol, side)] = 0
        history_equity_symbols: Counter[str] = Counter()
        history_excluded = 0
        for row in rows:
            body = row.get("trade") if isinstance(row.get("trade"), dict) else row
            symbol = str(body.get("symbol") or "").strip().upper()
            security = normalize_action(body.get("trade_type") or body.get("security_type"))
            ts = event_ts(row)
            if (
                not symbol
                or is_option_symbol(symbol)
                or security not in {"", "equity", "stock"}
                or ts is None
                or not (start <= ts < end)
            ):
                history_excluded += 1
                continue
            history_equity_symbols[symbol] += 1
        ledger = Counter(
            (symbol, side)
            for symbol, side, _ in iter_ledger_events(root, account, start, end)
        )
        registry = load_registry(root, account)
        per_account[account] = {
            "broker_raw_rows": len(rows),
            "broker_keys": [
                {
                    "symbol": symbol,
                    "side": side,
                    "closed_lots": count,
                    "open_position_acquired_in_window": int(
                        account_open_keys[(symbol, side)]
                    ),
                }
                for symbol, side in sorted(
                    set(account_keys) | set(account_open_keys)
                )
                for count in [account_keys[(symbol, side)]]
            ],
            "closed_lot_rows": len(gainloss),
            "excluded_closed_lots": excluded_closed,
            "current_position_rows": len(positions),
            "excluded_current_positions": excluded_positions,
            "history_equity_executions_by_symbol": dict(
                sorted(history_equity_symbols.items())
            ),
            "excluded_history_rows": history_excluded,
            "pages": evidence["history_pages"],
            "gainloss_payload_sha256": evidence["gainloss_payload_sha256"],
            "positions_payload_sha256": evidence["positions_payload_sha256"],
            "local_ledger_keys": [
                {"symbol": symbol, "side": side, "events": count}
                for (symbol, side), count in sorted(ledger.items())
            ],
            "registry_key_count": len(registry),
            "broker_not_in_registry": [
                f"{symbol}_{side}"
                for symbol, side in sorted(
                    (set(account_keys) | set(account_open_keys)) - registry
                )
            ],
        }
    cohort = [
        {
            "symbol": symbol,
            "side": side,
            "position_key": f"{symbol}_{side}",
            "broker_closed_lots": count,
            "open_positions_acquired_in_window": open_keys[(symbol, side)],
        }
        for (symbol, side), count in sorted(keys.items())
    ]
    return {
        "schema_version": 1,
        "source": (
            "Tradier gainloss closed lots plus current positions; account "
            "trade history is a symbol-level execution cross-check"
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_start": start.isoformat(),
        "window_end_exclusive": end.isoformat(),
        "calendar_days": (end.date() - start.date()).days,
        "accounts": list(accounts),
        "raw_broker_history_rows": raw_history_total,
        "closed_lot_rows": closed_lot_total,
        "cohort_key_count": len(cohort),
        "cohort_symbol_count": len({row["symbol"] for row in cohort}),
        "long_key_count": sum(row["side"] == "LONG" for row in cohort),
        "short_key_count": sum(row["side"] == "SHORT" for row in cohort),
        "cohort": cohort,
        "per_account": per_account,
        "exclusions": [
            "OCC option symbols",
            "positions acquired before the frozen UTC calendar window unless closed in it",
            "closed lots outside the frozen UTC calendar window",
            "hedge/options path families; the cohort contains only equity symbol-side books",
        ],
        "side_contract": (
            "Tradier gainloss and positions quantity >0 = LONG, <0 = SHORT; "
            "no inverse-long inference"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--accounts", default=",".join(DEFAULT_ACCOUNTS))
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    as_of = args.as_of or datetime.now(timezone.utc).date()
    start, end = utc_calendar_window(as_of, args.days)
    accounts = tuple(
        account.strip().lower()
        for account in args.accounts.split(",")
        if account.strip()
    )
    load_encrypted_env(args.root)
    audit = build_audit(
        root=args.root,
        accounts=accounts,
        start=start,
        end=end,
    )
    rendered = json.dumps(audit, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temp = args.output.with_suffix(args.output.suffix + ".tmp")
        temp.write_text(rendered)
        temp.replace(args.output)
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
