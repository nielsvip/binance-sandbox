"""Resolve one account's Mac position book against an independent broker observation."""
import datetime as dt
import hashlib
import json
import math
import re
import time


_HEX256 = re.compile(r"^[0-9a-f]{64}$")
_SIDES = ("long", "short")
_SOURCE = "ez_positions_service"


def _epoch(value):
    if isinstance(value, bool):
        raise ValueError("boolean timestamp")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        result = parsed.timestamp()
    else:
        raise ValueError("missing timestamp")
    if not math.isfinite(result) or result <= 0:
        raise ValueError("invalid timestamp")
    return result


def _quantity(value):
    if isinstance(value, bool):
        raise ValueError("boolean quantity")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("invalid quantity")
    return result


def _valid_receipt(receipt, account, venue, side, now, max_age_s):
    if not isinstance(receipt, dict):
        raise ValueError(f"{side} source receipt missing")
    if receipt.get("account") != account or receipt.get("venue") != venue or str(receipt.get("side", "")).lower() != side:
        raise ValueError(f"{side} source receipt identity mismatch")
    if receipt.get("complete") is not True:
        raise ValueError(f"{side} source book is not complete")
    if receipt.get("source") != _SOURCE:
        raise ValueError(f"{side} source is not {_SOURCE}")
    writer = str(receipt.get("writer") or "")
    if _SOURCE not in writer:
        raise ValueError(f"{side} writer is not the position service")
    file_hash = receipt.get("file_sha256")
    if not isinstance(file_hash, str) or not _HEX256.fullmatch(file_hash):
        raise ValueError(f"{side} source file hash missing or invalid")
    try:
        confirmed = _epoch(receipt.get("api_confirmed_at"))
        file_epoch = _epoch(receipt.get("file_epoch"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{side} source timestamp invalid: {exc}") from exc
    confirmed_age = now - confirmed
    file_age = now - file_epoch
    if confirmed_age < -5 or file_age < -5:
        raise ValueError(f"{side} source timestamp is in the future")
    if confirmed_age > max_age_s or file_age > max_age_s:
        raise ValueError(f"{side} source is stale (API age {confirmed_age:.1f}s, file age {file_age:.1f}s)")
    if file_epoch < confirmed - 5:
        raise ValueError(f"{side} file predates its API confirmation")
    return {"side": side, "api_confirmed_at": confirmed, "file_epoch": file_epoch, "file_sha256": file_hash, "writer": writer}


def _snapshot_hash(snapshot):
    body = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()
    return hashlib.sha256(body).hexdigest()


def _transport_check(snapshot, receipt, venue, snapshot_hash):
    if receipt is None:
        return None
    if not isinstance(receipt, dict) or receipt.get("ok") is not True:
        raise ValueError("transport receipt missing or unsuccessful")
    if receipt.get("venue") != venue:
        raise ValueError("transport receipt venue mismatch")
    sent_hash = receipt.get("snapshot_sha256") or receipt.get("sha256")
    copied_hash = receipt.get("copy_sha256") or receipt.get("s1_sha256") or sent_hash
    if sent_hash != snapshot_hash or copied_hash != snapshot_hash:
        raise ValueError("transport receipt does not bind this snapshot hash")
    completed = _epoch(receipt.get("push_completed_at"))
    if completed < _epoch(snapshot.get("epoch")) - 5:
        raise ValueError("transport receipt predates the snapshot")
    return {"snapshot_sha256": snapshot_hash, "push_completed_at": completed}


def resolve_account_positions(snapshot, broker_positions, account, venue, now=None, max_age_s=900, transport_receipt=None):
    """Return an account's Mac quantities only after a complete fresh source and broker parity check.

    The required ``book_receipts`` schema is a list of two source records, one for each side. Each record
    binds account/venue/side, a complete API-confirmed service read, the exact file hash, and source timestamps.
    Missing rows mean flat only after both receipts validate. Receipt timestamps must come from their source; this
    function never stamps a book or a broker observation with the current time.
    """
    now = time.time() if now is None else float(now)
    if not math.isfinite(now) or not math.isfinite(float(max_age_s)) or float(max_age_s) <= 0:
        return {"ok": False, "why": "invalid resolver clock or freshness limit", "positions": {}, "provenance": {}}
    try:
        if not isinstance(snapshot, dict) or snapshot.get("source") != "mac" or snapshot.get("venue") != venue:
            raise ValueError("snapshot source or venue mismatch")
        snapshot_epoch = _epoch(snapshot.get("epoch"))
        snapshot_age = now - snapshot_epoch
        if snapshot_age < -5 or snapshot_age > max_age_s:
            raise ValueError(f"snapshot is stale or future-dated (age {snapshot_age:.1f}s)")
        accounts = snapshot.get("accounts")
        if not isinstance(accounts, dict):
            raise ValueError("snapshot accounts map missing")
        book = accounts.get(account)
        if book is None:
            book = {}
        if not isinstance(book, dict):
            raise ValueError("requested account book is malformed")
        receipts = snapshot.get("book_receipts")
        if not isinstance(receipts, list):
            raise ValueError("API-confirmed complete-book receipts missing")
        by_side = {}
        for item in receipts:
            if isinstance(item, dict) and item.get("account") == account:
                side = str(item.get("side") or "").lower()
                if side in _SIDES:
                    if side in by_side:
                        raise ValueError(f"duplicate {side} source receipt")
                    by_side[side] = item
        verified_receipts = [_valid_receipt(by_side.get(side), account, venue, side, now, float(max_age_s)) for side in _SIDES]
        source_positions = {}
        for symbol_side, row in book.items():
            if not isinstance(symbol_side, str) or not isinstance(row, dict):
                raise ValueError("source book contains malformed row")
            symbol, sep, side = symbol_side.rpartition("_")
            side = side.upper()
            if not sep or not symbol or side not in ("LONG", "SHORT"):
                raise ValueError(f"invalid source sym_side {symbol_side!r}")
            if row.get("source_file") is not None:
                expected_file = f"{account}/{side.lower()}_positions.json"
                if row.get("source_file") != expected_file:
                    raise ValueError(f"{symbol_side} source file identity mismatch")
            try:
                qty = _quantity(row.get("qty"))
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{symbol_side} quantity invalid: {exc}") from exc
            if qty > 0:
                source_positions[symbol_side] = {"qty": qty, "mac": dict(row), "position_source": "mac_api_confirmed_rest_reconciled"}
        if not isinstance(broker_positions, dict):
            raise ValueError("broker confirmation map missing")
        broker = {}
        for symbol_side, row in broker_positions.items():
            if not isinstance(symbol_side, str) or not isinstance(row, dict):
                raise ValueError("broker confirmation contains malformed row")
            try:
                qty = _quantity(row.get("qty"))
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"broker {symbol_side} quantity invalid: {exc}") from exc
            if qty > 0:
                broker[symbol_side] = qty
        if set(source_positions) != set(broker):
            missing_broker = sorted(set(source_positions) - set(broker))
            missing_mac = sorted(set(broker) - set(source_positions))
            raise ValueError(f"position set mismatch (Mac-only={missing_broker}, broker-only={missing_mac})")
        for symbol_side, position in source_positions.items():
            mac_qty, broker_qty = position["qty"], broker[symbol_side]
            if abs(mac_qty - broker_qty) > max(1e-8, broker_qty * 1e-6):
                raise ValueError(f"quantity mismatch for {symbol_side}: Mac={mac_qty} broker={broker_qty}")
            # Broker cost and mark fields remain independent confirmation inputs; Mac qty remains authoritative.
            for field in ("entry", "break_even", "price", "cost"):
                if broker_positions[symbol_side].get(field) is not None:
                    position[field] = broker_positions[symbol_side][field]
        transport = _transport_check(snapshot, transport_receipt, venue, _snapshot_hash(snapshot))
        return {"ok": True, "why": "fresh complete Mac books reconciled with broker", "positions": source_positions,
                "provenance": {"source": "mac_positions", "account": account, "venue": venue, "snapshot_epoch": snapshot_epoch,
                               "snapshot_age_s": snapshot_age, "snapshot_sha256": _snapshot_hash(snapshot),
                               "book_receipts": verified_receipts, "transport": transport,
                               "broker_confirmation": "matched_quantities"}}
    except (TypeError, ValueError, OverflowError) as exc:
        return {"ok": False, "why": str(exc), "positions": {}, "provenance": {"source": "mac_positions", "account": account, "venue": venue}}
