#!/usr/bin/env python3
"""Reconstruct broker position lifecycles from Binance fills and order records.

This is an evidence inventory, not a strategy evaluator. It never converts fees
between assets and never infers entry switches from current configuration.
"""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path


EPS = 1e-10


def _num(value, default=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _iso_ms(value):
    from datetime import datetime, timezone

    try:
        return datetime.fromtimestamp(float(value) / 1000.0, timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _is_opening(fill, side):
    action = str(fill.get("side") or "").upper()
    if action not in {"BUY", "SELL"} or side not in {"LONG", "SHORT"}:
        return None
    return (action == "BUY") == (side == "LONG")


def _fixed_stop(order):
    if not order:
        return False, None
    types = [str(order.get(key) or "").upper() for key in ("origType", "type")]
    cid = str(order.get("clientOrderId") or "").upper()
    stop_types = [typ for typ in types if typ.startswith("STOP")]
    if stop_types:
        return True, "order_type:" + "/".join(stop_types)
    if cid.startswith("VDS"):
        return True, "client_order_id_prefix:vds"
    if cid.startswith("EZMSTOP"):
        return True, "client_order_id_prefix:EZMSTOP"
    return False, None


def _explicit_entry_evidence(order, fill):
    """Return only actual order/fill metadata that names the entry decision."""
    keys = ("entrySwitch", "entry_switch", "switch", "decisionReason", "decision_reason", "reason")
    for source_name, source in (("order", order or {}), ("fill", fill)):
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return {"value": value, "source": source_name + "." + key}
    return None


def _order_maps(account):
    result = {}
    for symbol, rows in (account.get("orders") or {}).items():
        for order in rows or []:
            try:
                result[(str(symbol), str(int(order["orderId"]))) ] = order
            except (KeyError, TypeError, ValueError):
                continue
    return result


def _position_map(account):
    result = {}
    rows = account.get("positions")
    if rows is None:
        return None
    for row in rows:
        symbol = str(row.get("symbol") or "")
        side = str(row.get("positionSide") or "").upper()
        amount = _num(row.get("positionAmt"))
        if symbol and side in {"LONG", "SHORT"} and amount is not None:
            result[(symbol, side)] = abs(amount)
    return result


def _fill_sort_key(fill):
    return (_num(fill.get("time"), 0.0), _num(fill.get("id"), 0.0), _num(fill.get("orderId"), 0.0))


def reconstruct_account(account_id, account, window_start_ms=None, window_end_ms=None):
    orders = _order_maps(account)
    end_positions = _position_map(account)
    fills_by_key = collections.defaultdict(list)
    malformed_fills = []
    for symbol, rows in (account.get("trades") or {}).items():
        for fill in rows or []:
            pside = str(fill.get("positionSide") or "").upper()
            qty = _num(fill.get("qty"))
            if not symbol or pside not in {"LONG", "SHORT"} or qty is None or qty <= 0:
                malformed_fills.append({"symbol": symbol, "trade_id": fill.get("id"), "why": "missing symbol/positionSide or positive qty"})
                continue
            fills_by_key[(str(symbol), pside)].append(fill)

    keys = set(fills_by_key)
    if end_positions is not None:
        keys.update(key for key, qty in end_positions.items() if qty > EPS)
    lifecycles = []
    conservation = collections.defaultdict(lambda: {"raw_realized_pnl": 0.0, "fill_count": 0, "lifecycle_realized_pnl": 0.0})

    for symbol, side in sorted(keys):
        row = conservation[(symbol, side)]
        fills = sorted(fills_by_key.get((symbol, side), []), key=_fill_sort_key)
        end_qty = end_positions.get((symbol, side), 0.0) if end_positions is not None else None
        signed_net = 0.0
        for fill in fills:
            is_opening = _is_opening(fill, side)
            if is_opening is None:
                malformed_fills.append({"symbol": symbol, "trade_id": fill.get("id"), "why": "unknown fill action"})
                continue
            signed_net += _num(fill.get("qty"), 0.0) * (1.0 if is_opening else -1.0)
        start_qty = max(0.0, end_qty - signed_net) if end_qty is not None else None
        start_qty_valid = start_qty is not None and (end_qty - signed_net) >= -EPS
        start_unexplained = (end_qty - signed_net) if end_qty is not None else None
        pos = start_qty if start_qty_valid else 0.0
        cur = None
        lifecycle_index = 0

        def new_lifecycle(carried=False, opening_qty=None, opening_method=None):
            nonlocal lifecycle_index
            lifecycle_index += 1
            return {
                "account_id": str(account_id), "symbol": symbol, "position_side": side,
                "lifecycle_index": lifecycle_index, "status": "open", "carried_in": bool(carried),
                "opening_qty_estimate": opening_qty, "opening_qty_method": opening_method,
                "opening_qty_actual_fills": 0.0, "ending_qty_inferred": None,
                "ending_qty_observed": None, "ending_qty_reconciliation_delta": None,
                "entry_decision_evidence": None, "entry_switch_attribution": "unknown",
                "events": [], "issues": [], "_qty": opening_qty or 0.0, "_avg": None,
            }

        def record_close(life):
            life["ending_qty_inferred"] = life["_qty"]
            life["status"] = "completed" if life["_qty"] <= EPS and not life["issues"] else ("incomplete" if life["issues"] else "open")
            life["events"] = sorted(life["events"], key=lambda event: (event.get("timestamp_ms", 0), str(event.get("trade_id") or "")))
            life.pop("_qty", None)
            life.pop("_avg", None)
            lifecycles.append(life)

        if start_qty_valid and pos > EPS:
            cur = new_lifecycle(True, pos, "ending_position_minus_signed_window_flows")
            cur["events"].append({
                "timestamp_ms": int(window_start_ms) if window_start_ms is not None else None,
                "timestamp": _iso_ms(window_start_ms), "event_type": "carried_in_opening_inventory",
                "qty": pos, "price": None, "realized_pnl_raw": 0.0, "realized_pnl_asset": None,
                "commission_amount": 0.0, "commission_asset": None, "order_id": None,
                "fixed_stop_order_detected": False, "entry_switch_attribution": "unknown",
                "evidence": "opening price and entry time precede or are absent from this fill window",
            })
        elif end_qty is not None and not start_qty_valid:
            # Keep the inconsistency visible; never invent a carried quantity to make flows fit.
            issue = {"kind": "opening_inventory_conservation_failed", "calculated_start_qty": start_unexplained, "end_qty": end_qty, "signed_window_net_qty": signed_net}
            malformed_fills.append({"symbol": symbol, "position_side": side, "why": "ending position is smaller than net window openings"})
        else:
            issue = None

        for fill in fills:
            qty = _num(fill.get("qty"), 0.0)
            price = _num(fill.get("price"))
            opening = _is_opening(fill, side)
            if opening is None:
                continue
            key = (symbol, str(int(_num(fill.get("orderId"), -1))))
            order = orders.get(key)
            stop_detected, stop_basis = _fixed_stop(order)
            order_id = fill.get("orderId")
            trade_id = fill.get("id")
            realized = _num(fill.get("realizedPnl"), 0.0)
            fee = _num(fill.get("commission"), 0.0)
            fee_asset = fill.get("commissionAsset")
            asset = fill.get("marginAsset")
            row = conservation[(symbol, side)]
            row["raw_realized_pnl"] += realized
            row["fill_count"] += 1

            if opening:
                if cur is None:
                    cur = new_lifecycle(False, 0.0, "observed_entry_fills")
                before = cur["_qty"]
                avg_before = cur["_avg"]
                if price is not None and before <= EPS:
                    cur["_avg"] = price
                elif price is not None and avg_before is not None:
                    cur["_avg"] = (before * avg_before + qty * price) / (before + qty)
                cur["_qty"] += qty
                cur["opening_qty_actual_fills"] += qty
                cur["opening_qty_estimate"] = cur["_qty"] if not cur["carried_in"] else cur["opening_qty_estimate"]
                event_type = "open" if before <= EPS and not cur["carried_in"] else "augment"
                evidence = _explicit_entry_evidence(order, fill) if event_type == "open" else None
                if evidence:
                    cur["entry_decision_evidence"] = evidence
                    cur["entry_switch_attribution"] = evidence["value"]
            else:
                if cur is None:
                    cur = new_lifecycle(True, None, "unknown_due_to_missing_opening_evidence")
                    cur["issues"].append({"kind": "reduction_without_observed_or_reconciled_opening_inventory"})
                before = cur["_qty"]
                avg_before = cur["_avg"]
                if qty > before + EPS:
                    cur["issues"].append({"kind": "reduction_exceeds_reconstructed_qty", "fill_qty": qty, "available_qty": before, "trade_id": trade_id})
                cur["_qty"] = max(0.0, before - qty)
                event_type = "close" if cur["_qty"] <= EPS and before > EPS else "reduce"

            cur["events"].append({
                "timestamp_ms": int(_num(fill.get("time"), 0.0)), "timestamp": _iso_ms(fill.get("time")),
                "event_type": event_type, "trade_id": trade_id, "qty": qty, "price": price,
                "realized_pnl_raw": realized, "realized_pnl_asset": asset,
                "commission_amount": fee, "commission_asset": fee_asset,
                "order_id": order_id, "fixed_stop_order_detected": bool(stop_detected),
                "fixed_stop_detection_basis": stop_basis, "order_orig_type": (order or {}).get("origType"),
                "order_type": (order or {}).get("type"),
                "client_order_id": (order or {}).get("clientOrderId"),
                "entry_switch_attribution": (cur.get("entry_switch_attribution") if opening else "unknown"),
                "current_qty_after": cur["_qty"], "weighted_average_entry_price_after": cur["_avg"],
                "weighted_average_entry_price_before": avg_before,
            })
            if not opening:
                cur["events"][-1]["fixed_stop_order_detected"] = bool(stop_detected)
                cur["events"][-1]["fixed_stop_detection_basis"] = stop_basis
            if not opening and cur["_qty"] <= EPS:
                record_close(cur)
                cur = None

        if cur is not None:
            cur["ending_qty_inferred"] = cur["_qty"]
            cur["ending_qty_observed"] = end_qty
            if end_qty is not None:
                cur["ending_qty_reconciliation_delta"] = end_qty - cur["_qty"]
                if abs(cur["ending_qty_reconciliation_delta"]) > max(EPS, 1e-8 * max(1.0, end_qty)):
                    cur["issues"].append({"kind": "ending_position_mismatch", "observed": end_qty, "inferred": cur["_qty"]})
            if not start_qty_valid and end_qty is not None and not cur["carried_in"]:
                cur["issues"].append(issue)
            cur["status"] = "incomplete" if cur["issues"] else "open"
            record_close(cur)

        # Include the endpoint in per-lifecycle conservation and derive final open qty state.
        for life in [x for x in lifecycles if x["account_id"] == str(account_id) and x["symbol"] == symbol and x["position_side"] == side]:
            life_pnl = sum(_num(event.get("realized_pnl_raw"), 0.0) for event in life["events"])
            conservation[(symbol, side)]["lifecycle_realized_pnl"] += life_pnl

    grouped = {}
    for (symbol, side), row in sorted(conservation.items()):
        sym = grouped.setdefault(symbol, {"symbol": symbol, "position_sides": {}, "commission_by_asset": {}, "fixed_stop_exit_fill_count": 0,
                                         "fixed_stop_exit_fill_loss_count": 0, "fixed_stop_exit_fill_realized_pnl_raw_by_asset": {},
                                         "completed_lifecycles_with_fixed_stop_exit_count": 0,
                                         "fixed_stop_completed_loss_lifecycle_count": 0,
                                         "total_lifecycle_count": 0, "completed_count": 0, "open_count": 0, "incomplete_count": 0,
                                         "wins_by_raw_realized_pnl": 0, "losses_by_raw_realized_pnl": 0, "flat_by_raw_realized_pnl": 0,
                                         "realized_pnl_raw_by_asset": {}})
        members = [life for life in lifecycles if life["symbol"] == symbol and life["position_side"] == side]
        result = {"raw_realized_pnl": row["raw_realized_pnl"], "fill_count": row["fill_count"], "lifecycle_realized_pnl": row["lifecycle_realized_pnl"],
                  "realized_pnl_conservation_delta": row["lifecycle_realized_pnl"] - row["raw_realized_pnl"], "lifecycle_count": len(members),
                  "completed_count": sum(life["status"] == "completed" for life in members), "open_count": sum(life["status"] == "open" for life in members),
                  "incomplete_count": sum(life["status"] == "incomplete" for life in members),
                  "wins_by_raw_realized_pnl": 0, "losses_by_raw_realized_pnl": 0, "flat_by_raw_realized_pnl": 0,
                  "fixed_stop_exit_fill_count": 0, "fixed_stop_exit_fill_loss_count": 0,
                  "fixed_stop_exit_fill_realized_pnl_raw_by_asset": {}, "commission_by_asset": {},
                  "realized_pnl_raw_by_asset": {}}
        sym["position_sides"][side] = result
        sym["total_lifecycle_count"] += result["lifecycle_count"]
        sym["completed_count"] += result["completed_count"]
        sym["open_count"] += result["open_count"]
        sym["incomplete_count"] += result["incomplete_count"]
        for life in members:
            pnl = sum(_num(ev.get("realized_pnl_raw"), 0.0) for ev in life["events"])
            fixed_stop_exit_events = [ev for ev in life["events"] if ev.get("fixed_stop_order_detected") and ev.get("event_type") in {"reduce", "close"}]
            if life["status"] == "completed":
                win = pnl > EPS
                loss = pnl < -EPS
                flat = abs(pnl) <= EPS
                sym["wins_by_raw_realized_pnl"] += win
                sym["losses_by_raw_realized_pnl"] += loss
                sym["flat_by_raw_realized_pnl"] += flat
                result["wins_by_raw_realized_pnl"] += win
                result["losses_by_raw_realized_pnl"] += loss
                result["flat_by_raw_realized_pnl"] += flat
                if fixed_stop_exit_events:
                    sym["completed_lifecycles_with_fixed_stop_exit_count"] += 1
                    sym["fixed_stop_completed_loss_lifecycle_count"] += pnl < -EPS
        for life in members:
            for ev in life["events"]:
                fee_asset = ev.get("commission_asset")
                if fee_asset and ev.get("commission_amount") is not None:
                    amount = ev["commission_amount"]
                    sym["commission_by_asset"][fee_asset] = sym["commission_by_asset"].get(fee_asset, 0.0) + amount
                    result["commission_by_asset"][fee_asset] = result["commission_by_asset"].get(fee_asset, 0.0) + amount
                pnl_asset = ev.get("realized_pnl_asset")
                if pnl_asset and ev.get("realized_pnl_raw") is not None:
                    amount = ev["realized_pnl_raw"]
                    sym["realized_pnl_raw_by_asset"][pnl_asset] = sym["realized_pnl_raw_by_asset"].get(pnl_asset, 0.0) + amount
                    result["realized_pnl_raw_by_asset"][pnl_asset] = result["realized_pnl_raw_by_asset"].get(pnl_asset, 0.0) + amount
                if ev.get("fixed_stop_order_detected") and ev.get("event_type") in {"reduce", "close"}:
                    sym["fixed_stop_exit_fill_count"] += 1
                    result["fixed_stop_exit_fill_count"] += 1
                    loss_fill = _num(ev.get("realized_pnl_raw"), 0.0) < -EPS
                    sym["fixed_stop_exit_fill_loss_count"] += loss_fill
                    result["fixed_stop_exit_fill_loss_count"] += loss_fill
                    asset = ev.get("realized_pnl_asset")
                    if asset:
                        values = sym["fixed_stop_exit_fill_realized_pnl_raw_by_asset"]
                        values[asset] = values.get(asset, 0.0) + _num(ev.get("realized_pnl_raw"), 0.0)
                        side_values = result["fixed_stop_exit_fill_realized_pnl_raw_by_asset"]
                        side_values[asset] = side_values.get(asset, 0.0) + _num(ev.get("realized_pnl_raw"), 0.0)

    return {
        "schema_version": 1,
        "account_id": str(account_id),
        "coverage": {"window_start_ms": window_start_ms, "window_start_utc": _iso_ms(window_start_ms),
                     "window_end_ms": window_end_ms, "window_end_utc": _iso_ms(window_end_ms),
                     "fill_count_input": sum(len(v or []) for v in (account.get("trades") or {}).values()),
                     "order_count_input": sum(len(v or []) for v in (account.get("orders") or {}).values()),
                     "position_snapshot_available": end_positions is not None},
        "lifecycle_counts": {"total": len(lifecycles), "completed": sum(x["status"] == "completed" for x in lifecycles),
                             "open": sum(x["status"] == "open" for x in lifecycles), "incomplete": sum(x["status"] == "incomplete" for x in lifecycles)},
        "per_symbol": grouped,
        "lifecycles": lifecycles,
        "malformed_or_unreconciled_records": malformed_fills,
        "account_commission_by_asset": dict(_sum_commissions(lifecycles)),
        "notes": ["No commission conversion was performed.", "Entry-switch attribution is unknown unless the actual fill/order record contains explicit decision evidence.",
                  "Fixed-stop labels require a matching raw order type beginning STOP or client order ID prefix vds/EZMSTOP.",
                  "Raw realized PnL and commission totals are kept asset-separated; no net-after-fee amount is synthesized."],
    }


def _sum_commissions(lifecycles):
    result = collections.defaultdict(float)
    for life in lifecycles:
        for event in life["events"]:
            asset = event.get("commission_asset")
            amount = _num(event.get("commission_amount"))
            if asset and amount is not None:
                result[asset] += amount
    return result


def build_inventory(document, source_path="<memory>", source_sha256=None):
    start = document.get("start_ms")
    end = document.get("end_ms")
    accounts = document.get("accounts") or {}
    result = {}
    for account_id, account in sorted(accounts.items()):
        inventory = reconstruct_account(account_id, account, start, end)
        inventory["source"] = {"path": str(source_path), "sha256": source_sha256,
                               "generated_ms": document.get("generated_ms"), "window_start_ms": start, "window_end_ms": end}
        result[str(account_id)] = inventory
    return result


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    input_path = Path(args.input)
    document = json.loads(input_path.read_text())
    inventories = build_inventory(document, str(input_path), sha256_file(input_path))
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for account_id, inventory in inventories.items():
        (out_dir / (account_id + ".json")).write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
    manifest = {"schema_version": 1, "source": {"path": str(input_path), "sha256": sha256_file(input_path)},
                "accounts": {acct: {"file": acct + ".json", "lifecycle_counts": inv["lifecycle_counts"],
                                    "symbol_count": len(inv["per_symbol"])} for acct, inv in inventories.items()}}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
