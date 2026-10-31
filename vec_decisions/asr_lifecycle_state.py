"""Causal ASR lifecycle arrays reconstructed from accepted v12 vector fills."""

import math
import numpy as np


def _finite_number(value, field):
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if not math.isfinite(out):
        raise ValueError(f"non-finite {field}: {value!r}")
    return out


def build_lifecycle_arrays(events, trades, timestamps, prices, is_long, initial_state):
    """Build pre-decision lifecycle state from accepted fills strictly before each bar.

    `events` are the v12 accepted OPEN/AUGMENT/REDUCE ledger. `trades` supply accepted
    CLOSE fills. State is explicit at the beginning of the requested prefix; no
    account/tracker defaults are inferred. Same-bar CLOSE plus other fill is rejected
    because v12 keeps those records in separate lists without a cross-list sequence.
    """
    ts = np.asarray(timestamps, dtype=np.float64)
    px = np.asarray(prices, dtype=np.float64)
    if ts.ndim != 1 or px.ndim != 1 or len(ts) != len(px):
        raise ValueError("timestamps and prices must be aligned one-dimensional arrays")
    if np.any(~np.isfinite(ts)) or np.any(np.diff(ts) < 0):
        raise ValueError("timestamps must be finite and nondecreasing")
    n = len(ts)
    state = dict(initial_state)
    required = ("position_exists",)
    missing = [key for key in required if key not in state]
    if missing:
        raise ValueError(f"initial_state missing {missing}")
    held = bool(state["position_exists"])
    if held:
        for key in ("position_amt", "avg_entry_price", "entry_ts", "opened_ts"):
            if key not in state:
                raise ValueError(f"held initial_state missing {key}")
    qty = _finite_number(state.get("position_amt", 0.0), "initial position_amt")
    avg = _finite_number(state.get("avg_entry_price", 0.0), "initial avg_entry_price")
    entry_ts = _finite_number(state.get("entry_ts", 0.0), "initial entry_ts")
    opened_ts = _finite_number(state.get("opened_ts", entry_ts), "initial opened_ts")
    last_aug_ts = _finite_number(state.get("last_augmentation_ts", 0.0), "initial last_augmentation_ts")
    last_aug_px = _finite_number(state.get("last_augmentation_price", 0.0), "initial last_augmentation_price")
    last_reduce_ts = _finite_number(state.get("last_reduction_ts", 0.0), "initial last_reduction_ts")
    last_reduce_px = _finite_number(state.get("last_reduction_price", 0.0), "initial last_reduction_price")
    last_exit_ts = _finite_number(state.get("last_exit_ts", 0.0), "initial last_exit_ts")
    last_exit_px = _finite_number(state.get("last_exit_price", 0.0), "initial last_exit_price")
    realized_pnl = _finite_number(state.get("realized_pnl", 0.0), "initial realized_pnl")
    augment_count = int(state.get("augment_count", 0))
    reduce_count = int(state.get("reduce_count", 0))
    rows = []
    for source, records in (("event", events), ("trade", trades)):
        for record in records:
            typ = str(record.get("type", "")).upper()
            if source == "event" and typ not in ("OPEN", "REENTRY", "AUGMENT", "REDUCE", "CLOSE"):
                continue
            if source == "trade" and typ != "CLOSE":
                continue
            bar_key = "bar" if source == "event" else "bar_exit"
            if bar_key not in record:
                raise ValueError(f"{source} {typ} missing {bar_key}")
            bar = int(record[bar_key])
            if bar < 0 or bar >= n:
                raise ValueError(f"{source} {typ} bar out of range: {bar}")
            price = _finite_number(record.get("price", record.get("exit_price")), f"{source} {typ} price")
            amount = _finite_number(record.get("qty", 0.0), f"{source} {typ} qty")
            if amount < 0:
                raise ValueError(f"negative accepted fill qty at bar {bar}")
            rows.append((bar, 0 if source == "event" else 1, len(rows), source, typ, price, amount, record))
    # A close and any event on the same bar has no cross-list execution ordinal.
    for bar in {row[0] for row in rows}:
        same = [row for row in rows if row[0] == bar]
        if any(row[4] == "CLOSE" for row in same) and len(same) > 1:
            raise ValueError(f"ambiguous same-bar CLOSE ordering at bar {bar}")
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    cursor = 0
    out = {key: np.zeros(n, dtype=dtype) for key, dtype in (
        ("position_exists", bool), ("position_amt", float), ("avg_entry_price", float),
        ("entry_ts", float), ("opened_ts", float), ("last_augmentation_ts", float),
        ("last_augmentation_price", float), ("last_reduction_ts", float),
        ("last_reduction_price", float), ("last_exit_ts", float), ("last_exit_price", float),
        ("realized_pnl", float), ("augment_count", np.int64), ("reduce_count", np.int64),
        ("bars_held", np.int64), ("minutes_since_augmentation", float),
        ("gain_pct", float), ("prev_gain_pct", float), ("recent_exit", bool),
    )}
    prior_gain = 0.0
    prior_gain_valid = False
    for i in range(n):
        while cursor < len(rows) and rows[cursor][0] < i:
            _, _, _, source, typ, fill_px, fill_qty, record = rows[cursor]
            fill_ts = _finite_number(record.get("ts", ts[int(record["bar"] if source == "event" else record["bar_exit"])]), f"{typ} ts")
            if typ in ("OPEN", "REENTRY"):
                if held or fill_qty <= 0 or fill_px <= 0:
                    raise ValueError(f"invalid {typ} lifecycle transition at bar {rows[cursor][0]}")
                held, qty, avg = True, fill_qty, fill_px
                entry_ts = opened_ts = fill_ts
                realized_pnl = 0.0
                augment_count = reduce_count = 0
            elif typ == "AUGMENT":
                if not held or fill_qty <= 0 or fill_px <= 0:
                    raise ValueError(f"invalid AUGMENT lifecycle transition at bar {rows[cursor][0]}")
                new_qty = qty + fill_qty
                avg = ((qty * avg) + (fill_qty * fill_px)) / new_qty
                qty = new_qty
                last_aug_ts, last_aug_px = fill_ts, fill_px
                augment_count += 1
            elif typ == "REDUCE":
                if not held or fill_qty <= 0 or fill_qty > qty:
                    raise ValueError(f"invalid REDUCE lifecycle transition at bar {rows[cursor][0]}")
                realized_pnl += ((fill_px - avg) if is_long else (avg - fill_px)) * fill_qty
                qty -= fill_qty
                last_reduce_ts, last_reduce_px = fill_ts, fill_px
                last_exit_ts, last_exit_px = fill_ts, fill_px
                reduce_count += 1
                if qty <= max(1e-12, abs(fill_qty) * 1e-12):
                    held, qty = False, 0.0
            elif typ == "CLOSE":
                if not held:
                    raise ValueError(f"invalid CLOSE lifecycle transition at bar {rows[cursor][0]}")
                closed_qty = min(qty, fill_qty if fill_qty > 0 else qty)
                realized_pnl += ((fill_px - avg) if is_long else (avg - fill_px)) * closed_qty
                last_reduce_ts, last_reduce_px = fill_ts, fill_px
                last_exit_ts, last_exit_px = fill_ts, fill_px
                reduce_count += 1
                held, qty = False, 0.0
            cursor += 1
        out["position_exists"][i] = held
        out["position_amt"][i] = qty
        out["avg_entry_price"][i] = avg if held else 0.0
        out["entry_ts"][i] = entry_ts if held else 0.0
        out["opened_ts"][i] = opened_ts if held else 0.0
        out["last_augmentation_ts"][i] = last_aug_ts
        out["last_augmentation_price"][i] = last_aug_px
        out["last_reduction_ts"][i] = last_reduce_ts
        out["last_reduction_price"][i] = last_reduce_px
        out["last_exit_ts"][i] = last_exit_ts
        out["last_exit_price"][i] = last_exit_px
        out["realized_pnl"][i] = realized_pnl
        out["augment_count"][i] = augment_count
        out["reduce_count"][i] = reduce_count
        out["bars_held"][i] = max(0, int(np.searchsorted(ts, entry_ts, side="left") and i - np.searchsorted(ts, entry_ts, side="left"))) if held else 0
        out["minutes_since_augmentation"][i] = (ts[i] - last_aug_ts) / 60.0 if last_aug_ts > 0 else np.inf
        if held and avg > 0 and np.isfinite(px[i]):
            gain = ((px[i] - avg) / avg * 100.0) if is_long else ((avg - px[i]) / avg * 100.0)
            out["gain_pct"][i] = gain
        else:
            gain = 0.0
            out["gain_pct"][i] = 0.0
        out["prev_gain_pct"][i] = prior_gain if prior_gain_valid else 0.0
        out["recent_exit"][i] = bool(last_exit_ts > 0 and ts[i] >= last_exit_ts and ts[i] - last_exit_ts < 900.0)
        prior_gain, prior_gain_valid = gain, held
    return out
