#!/usr/bin/env python3
"""Audit post-entry closed-bar and OHLC path evidence for recorded stop fills."""
import argparse
import bisect
import json
import math
from pathlib import Path


BAR_INTERVAL_SECONDS = {"1m": 60, "3m": 180, "15m": 900}
EPS = 1e-12


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _event_ms(event):
    value = event.get("timestamp_ms")
    if value is not None:
        return _number(value)
    stamp = event.get("timestamp")
    if not stamp:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp() * 1000
    except (TypeError, ValueError, OverflowError):
        return None


def load_npz_bars(path):
    import numpy as np

    with np.load(path, allow_pickle=False) as archive:
        required = ("timestamps", "close_15m", "high_15m", "low_15m")
        if any(name not in archive for name in required):
            return None
        ts = archive["timestamps"]
        close = archive["close_15m"]
        high = archive["high_15m"]
        low = archive["low_15m"]
        if not (len(ts) == len(close) == len(high) == len(low)):
            return None
        bars = []
        for timestamp, c, h, lo in zip(ts, close, high, low):
            values = (_number(c), _number(h), _number(lo))
            if all(value is not None for value in values):
                bars.append((int(timestamp) * 1000, values[0], values[1], values[2]))
        return {"bars": bars, "timeframe": "15m", "source_kind": "canonical_indicator_npz"}


def load_json_bars(path, timeframe):
    rows = json.loads(Path(path).read_text())
    if not isinstance(rows, list):
        return None
    bars = []
    for row in rows:
        stamp = row.get("timestamp")
        if stamp is None:
            continue
        from datetime import datetime

        try:
            timestamp_ms = int(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp() * 1000)
        except (TypeError, ValueError, OverflowError):
            continue
        values = tuple(_number(row.get(name)) for name in ("close", "high", "low"))
        if all(value is not None for value in values):
            bars.append((timestamp_ms, values[0], values[1], values[2]))
    return {"bars": bars, "timeframe": timeframe, "source_kind": "consolidated_klines_json"}


def load_symbol_bars(symbol, npz_root, fallback_root=None):
    npz_path = Path(npz_root) / (symbol + ".npz") if npz_root else None
    if npz_path and npz_path.is_file():
        loaded = load_npz_bars(npz_path)
        if loaded:
            loaded["source_path"] = str(npz_path)
            return loaded
    if fallback_root:
        root = Path(fallback_root)
        for timeframe in ("1m", "3m", "15m"):
            path = root / (symbol + "_" + timeframe + ".json")
            if path.is_file():
                loaded = load_json_bars(path, timeframe)
                if loaded:
                    loaded["source_path"] = str(path)
                    return loaded
    return None


def _gross_lifecycle_pnl(side, qty, average_price, close_price, realized):
    if not qty or average_price is None or close_price is None:
        return None
    direction = 1.0 if side == "LONG" else -1.0
    return realized + direction * qty * (close_price - average_price)


def audit_lifecycle(lifecycle, bar_data, cutoff_ms):
    events = sorted(lifecycle.get("events") or [], key=lambda event: (_event_ms(event) or 0, str(event.get("trade_id") or "")))
    stop_events = [event for event in events if event.get("fixed_stop_order_detected") and event.get("event_type") in {"reduce", "close"}]
    if not stop_events:
        return None
    entry_events = [event for event in events if event.get("event_type") in {"open", "augment"} and _event_ms(event) is not None]
    first_entry_ms = _event_ms(entry_events[0]) if entry_events else None
    first_stop_ms = min((_event_ms(event) for event in stop_events if _event_ms(event) is not None), default=None)
    account = str(lifecycle.get("account_id") or "")
    symbol = str(lifecycle.get("symbol") or "")
    side = str(lifecycle.get("position_side") or "").upper()
    base = {"account_id": account, "symbol": symbol, "position_side": side,
            "lifecycle_index": lifecycle.get("lifecycle_index"), "status": lifecycle.get("status"),
            "fixed_stop_event_count": len(stop_events),
            "fixed_stop_event_trade_ids": [event.get("trade_id") for event in stop_events],
            "first_entry_fill_timestamp_ms": first_entry_ms, "first_stop_fill_timestamp_ms": first_stop_ms,
            "entry_to_first_stop_seconds": (first_stop_ms - first_entry_ms) / 1000.0 if first_entry_ms is not None and first_stop_ms is not None else None,
            "first_stop_order_ids": [event.get("order_id") for event in stop_events],
            "first_stop_order_types": [event.get("order_type") for event in stop_events]}
    if side not in {"LONG", "SHORT"} or not bar_data:
        return {**base, "classification": "UNVERIFIED_NO_VALID_BAR_SOURCE", "eligible_bar_count": 0}

    bars = sorted(bar_data["bars"], key=lambda bar: bar[0])
    bar_times = [bar[0] for bar in bars]
    interval_ms = BAR_INTERVAL_SECONDS[bar_data["timeframe"]] * 1000
    eligible = []
    proven = []
    possible = []
    excluded_future_count = 0
    coverage_start = bars[0][0] if bars else None
    coverage_end = bars[-1][0] + interval_ms if bars else None
    realized = 0.0
    asset_set = set()
    unknown_realized_asset = False
    missing_average = False
    for event_index, event in enumerate(events):
        event_time = _event_ms(event)
        if event_time is None:
            continue
        raw = _number(event.get("realized_pnl_raw"))
        if raw is not None:
            realized += raw
            if abs(raw) > EPS and not event.get("realized_pnl_asset"):
                unknown_realized_asset = True
        if event.get("realized_pnl_asset") and raw is not None and abs(raw) > EPS:
            asset_set.add(event["realized_pnl_asset"])
        if event_index + 1 >= len(events):
            continue
        next_time = _event_ms(events[event_index + 1])
        qty = _number(event.get("current_qty_after"))
        avg = _number(event.get("weighted_average_entry_price_after"))
        if next_time is None or qty is None or qty <= EPS:
            continue
        if avg is None:
            missing_average = True
            continue
        left = bisect.bisect_left(bar_times, event_time)
        right = bisect.bisect_left(bar_times, next_time - interval_ms)
        for timestamp, close, high, low in bars[left:right]:
            bar_end = timestamp + interval_ms
            if bar_end >= next_time:
                continue
            if cutoff_ms is not None and bar_end > cutoff_ms:
                excluded_future_count += 1
                continue
            eligible.append(timestamp)
            asset_valid = not unknown_realized_asset and len(asset_set) <= 1
            close_pnl = _gross_lifecycle_pnl(side, qty, avg, close, realized) if asset_valid else None
            extreme = high if side == "LONG" else low
            extreme_pnl = _gross_lifecycle_pnl(side, qty, avg, extreme, realized) if asset_valid else None
            evidence = {"bar_start_ms": timestamp, "bar_end_ms": bar_end, "close_price": close,
                        "high_price": high, "low_price": low, "qty": qty, "weighted_average_entry_price": avg,
                        "realized_pnl_raw_to_bar": realized, "gross_pnl_asset": next(iter(asset_set)) if len(asset_set) == 1 else None,
                        "gross_pnl_at_close_before_commission": close_pnl,
                        "gross_pnl_at_favorable_ohlc_extreme_before_commission": extreme_pnl}
            if close_pnl is not None and close_pnl > EPS:
                proven.append(evidence)
            elif extreme_pnl is not None and extreme_pnl > EPS:
                possible.append(evidence)

    classification = "NO_CLOSED_BAR_GROSS_PROFIT_EVIDENCE"
    if proven:
        classification = "CLOSED_BAR_GROSS_PROFIT_OPPORTUNITY"
    elif possible:
        classification = "POSSIBLE_INTRABAR_ONLY_OHLC_BOUND"
    elif missing_average:
        classification = "UNVERIFIED_MISSING_WEIGHTED_ENTRY_PRICE"
    elif not eligible:
        classification = "UNVERIFIED_NO_FULL_BAR_WITHIN_LIFECYCLE"
    return {**base, "classification": classification, "bar_source_path": bar_data.get("source_path"),
            "bar_source_kind": bar_data.get("source_kind"), "bar_timeframe": bar_data.get("timeframe"),
            "bar_source_rows": len(bars), "bar_source_first_ms": coverage_start, "bar_source_end_ms": coverage_end,
            "cutoff_ms": cutoff_ms, "eligible_bar_count": len(eligible),
            "future_or_post_cutoff_excluded_bar_count": excluded_future_count,
            "first_confirmed_closed_bar_opportunity": proven[0] if proven else None,
            "first_possible_intrabar_bound": possible[0] if possible and not proven else None,
            "raw_realized_pnl_assets_seen": sorted(asset_set),
            "limitations": ["Gross mark-to-entry evidence only; commissions and slippage are not converted or deducted.",
                            "Bars containing an entry, augment, reduction, or exit event are excluded by requiring full-bar containment between events.",
                            "OHLC highs/lows bound possible intrabar paths and do not establish event chronology or an executable fill.",
                            "This audit does not attribute a missed exit to a switch or claim decision parity."]}


def audit_inventory(inventory, npz_root, fallback_root=None, cutoff_ms=None):
    if inventory.get("schema_version") != 1 or not isinstance(inventory.get("lifecycles"), list):
        raise ValueError("expected fixed_stop_inventory schema_version 1 with lifecycles[]")
    inventory_source = inventory.get("source") or {}
    if cutoff_ms is None:
        cutoff_ms = _number((inventory.get("coverage") or {}).get("window_end_ms") or inventory_source.get("window_end_ms"))
    cache = {}
    one_minute_cache = {}
    rows = []
    bar_sources = {}
    one_minute_sources = {}
    for lifecycle in inventory["lifecycles"]:
        if not any(event.get("fixed_stop_order_detected") for event in lifecycle.get("events", [])):
            continue
        symbol = str(lifecycle.get("symbol") or "")
        if symbol not in cache:
            cache[symbol] = load_symbol_bars(symbol, npz_root, fallback_root)
            bar_source = cache[symbol]
            if bar_source:
                interval_ms = BAR_INTERVAL_SECONDS[bar_source["timeframe"]] * 1000
                source_bars = bar_source["bars"]
                post_cutoff = [bar for bar in source_bars if cutoff_ms is not None and bar[0] + interval_ms > cutoff_ms]
                bar_sources[symbol] = {"source_path": bar_source.get("source_path"), "source_kind": bar_source.get("source_kind"),
                                       "timeframe": bar_source.get("timeframe"), "source_row_count": len(source_bars),
                                       "rows_usable_by_cutoff_count": len(source_bars) - len(post_cutoff),
                                       "rows_after_cutoff_or_crossing_cutoff_excluded_count": len(post_cutoff),
                                       "first_excluded_bar_start_ms": post_cutoff[0][0] if post_cutoff else None,
                                       "last_excluded_bar_start_ms": post_cutoff[-1][0] if post_cutoff else None}
            else:
                bar_sources[symbol] = {"source_path": None, "source_kind": None, "timeframe": None,
                                       "source_row_count": 0, "rows_usable_by_cutoff_count": 0,
                                       "rows_after_cutoff_or_crossing_cutoff_excluded_count": None,
                                       "first_excluded_bar_start_ms": None, "last_excluded_bar_start_ms": None}
        primary = audit_lifecycle(lifecycle, cache[symbol], cutoff_ms)
        one_minute = None
        if cache[symbol] and cache[symbol].get("timeframe") == "15m" and fallback_root:
            if symbol not in one_minute_cache:
                path = Path(fallback_root) / (symbol + "_1m.json")
                one_minute_cache[symbol] = load_json_bars(path, "1m") if path.is_file() else None
                if one_minute_cache[symbol]:
                    one_minute_cache[symbol]["source_path"] = str(path)
            source_1m = one_minute_cache[symbol]
            if source_1m:
                interval_ms = BAR_INTERVAL_SECONDS["1m"] * 1000
                source_bars = source_1m["bars"]
                post_cutoff = [bar for bar in source_bars if cutoff_ms is not None and bar[0] + interval_ms > cutoff_ms]
                one_minute_sources[symbol] = {"source_path": source_1m.get("source_path"), "source_kind": source_1m.get("source_kind"),
                                              "timeframe": "1m", "source_row_count": len(source_bars),
                                              "source_first_bar_start_ms": source_bars[0][0] if source_bars else None,
                                              "source_end_ms": source_bars[-1][0] + interval_ms if source_bars else None,
                                              "rows_usable_by_cutoff_count": len(source_bars) - len(post_cutoff),
                                              "rows_after_cutoff_or_crossing_cutoff_excluded_count": len(post_cutoff),
                                              "first_excluded_bar_start_ms": post_cutoff[0][0] if post_cutoff else None,
                                              "last_excluded_bar_start_ms": post_cutoff[-1][0] if post_cutoff else None}
                one_minute = audit_lifecycle(lifecycle, source_1m, cutoff_ms)
        result = primary
        if result:
            if primary and one_minute:
                chosen = one_minute if _evidence_rank(one_minute) > _evidence_rank(primary) else primary
                result = {**chosen, "primary_15m_classification": primary["classification"],
                          "one_minute_refinement_classification": one_minute["classification"],
                          "one_minute_refinement_has_eligible_bars": one_minute["eligible_bar_count"] > 0}
            rows.append(result)
    counts = {}
    for row in rows:
        key = row["classification"]
        counts[key] = counts.get(key, 0) + 1
    excluded_source_rows = sum(row["rows_after_cutoff_or_crossing_cutoff_excluded_count"] or 0 for row in bar_sources.values())
    excluded_source_rows += sum(row["rows_after_cutoff_or_crossing_cutoff_excluded_count"] or 0 for row in one_minute_sources.values())
    return {"schema_version": 1, "source_inventory": inventory_source, "audit_cutoff_ms": cutoff_ms,
            "bar_source_policy": "canonical per-symbol NPZ 15m OHLC primary; consolidated 1m OHLC refines lifecycles when a 1m file exists; if NPZ is missing use consolidated 1m, then 3m, then 15m",
            "post_cutoff_or_cutoff_crossing_source_rows_excluded_count": excluded_source_rows,
            "bar_sources_by_symbol": bar_sources, "one_minute_sources_by_symbol": one_minute_sources,
            "counts_by_classification": counts,
            "fixed_stop_lifecycle_count": len(rows), "lifecycles": rows}


def _evidence_rank(result):
    category = result.get("classification")
    if category == "CLOSED_BAR_GROSS_PROFIT_OPPORTUNITY":
        return 4
    if category == "POSSIBLE_INTRABAR_ONLY_OHLC_BOUND":
        return 3
    if category == "NO_CLOSED_BAR_GROSS_PROFIT_EVIDENCE" and result.get("eligible_bar_count", 0):
        return 2
    if result.get("eligible_bar_count", 0):
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--npz-root", required=True)
    parser.add_argument("--fallback-root")
    parser.add_argument("--cutoff-ms", type=int)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    inventory = json.loads(Path(args.inventory).read_text())
    result = audit_inventory(inventory, args.npz_root, args.fallback_root, args.cutoff_ms)
    Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"out": args.out, "fixed_stop_lifecycle_count": result["fixed_stop_lifecycle_count"],
                      "counts_by_classification": result["counts_by_classification"]}, sort_keys=True))


if __name__ == "__main__":
    main()
