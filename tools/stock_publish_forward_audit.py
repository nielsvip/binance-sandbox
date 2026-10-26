#!/usr/bin/env python3
"""Read-only replay of published stock SQL sets through a fixed Friday close."""
import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _seconds(value):
    number = float(value)
    return number / 1000.0 if number > 1e11 else number


def replay(job, cutoff):
    from tools import vec_live_executor as lane
    from tools.opt import evaluate_v12 as evaluator
    from per_sym_live_set import _apply_caps
    symbol_side = job["sym_side"]
    symbol, side = lane.split_ss(symbol_side)
    published = dt.datetime.fromisoformat(job["updated_at"].replace("Z", "+00:00")).timestamp()
    full = json.loads(job["full_config_json"])
    effective = _apply_caps(symbol_side, full)
    result = {"id": job["id"], "sym_side": symbol_side, "published_at": job["updated_at"], "set_sha256": hashlib.sha256(job["full_config_json"].encode()).hexdigest(), "set_md5_effective": lane.set_md5(effective), "board_manifest": job.get("board_manifest"), "board_tail": job.get("board_tail")}
    if published >= cutoff:
        result["status"] = "NO_FRIDAY_FORWARD_PERIOD"
        return result
    raw = lane._load_store(symbol)
    import numpy as np
    timestamps = np.asarray(raw["timestamps"], dtype="float64")
    scale = 1000.0 if float(timestamps[-1]) > 1e11 else 1.0
    available_tail = _seconds(timestamps[-1])
    if available_tail < cutoff - 1800:
        result.update(status="NPZ_MISSING_FRIDAY_CLOSE", available_tail=available_tail)
        return result
    board_tail = float(job.get("board_tail") or 0) or published
    board_raw = lane.truncate_raw(raw, board_tail)
    board_window, _ = evaluator._exact_30d_slice(board_raw, False, 30)
    anchor = _seconds(board_window["timestamps"][0])
    load_start = anchor - 60 * 86400
    limited = {key: (value[int(np.searchsorted(timestamps, load_start * scale)):] if isinstance(value, np.ndarray) and value.ndim and len(value) == len(timestamps) else value) for key, value in raw.items()}
    base = evaluator.prepare(symbol_side, 30)
    if base is None:
        result["status"] = "PREPARE_FAILED"
        return result
    lane._BASE_PREP[symbol_side] = {key: value for key, value in base.items() if key not in ("npz_prepared", "bh")}
    anchor_doc = {"anchor_ts": anchor, "load_start_ts": load_start, "mode": "tradier"}
    Friday, npz = lane.run_engine(symbol_side, effective, anchor_doc, end_ts=cutoff, raw=limited)
    if npz is None:
        result.update(status="REPLAY_FAILED", reason=Friday.get("invalid_reason"))
        return result
    ledger = Friday.get("execution_ledger") or Friday.get("ledger") or []
    events = [{"ts": _seconds(row.get("ts") or 0), "type": row.get("type"), "reason": row.get("reason"), "qty": row.get("qty"), "price": row.get("price")} for row in ledger if isinstance(row, dict) and published < _seconds(row.get("ts") or 0) <= cutoff and row.get("reason") != "FINAL_MTM"]
    end_state = lane.replay_state(ledger, cutoff)
    result.update(status="REPLAYED", anchor_ts=anchor, friday_npz_tail=_seconds(npz["timestamps"][-1]), forward_events=events, forward_event_count=len(events), friday_open=bool(end_state["qty"] > 0), friday_qty=end_state["qty"], engine_valid=Friday.get("valid"), engine_invalid_reason=Friday.get("invalid_reason"))
    if job.get("board_tail"):
        at_board, _ = lane.run_engine(symbol_side, effective, anchor_doc, end_ts=board_tail, raw=limited)
        result["board_reproduced"] = bool(at_board.get("trades") == job.get("claim_trades") and at_board.get("gain_pct") is not None and job.get("claim_gain_pct") is not None and abs(float(at_board["gain_pct"]) - float(job["claim_gain_pct"])) < 1e-4)
        result["board_trade_count_match"] = at_board.get("trades") == job.get("claim_trades")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    jobs = json.loads(Path(args.jobs).read_text())
    if args.limit:
        jobs = jobs[:args.limit]
    cutoff = dt.datetime(2026, 10, 23, 20, tzinfo=dt.timezone.utc).timestamp()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as handle:
        for index, job in enumerate(jobs, 1):
            start = time.monotonic()
            try:
                record = replay(job, cutoff)
            except Exception as error:
                record = {"id": job.get("id"), "sym_side": job.get("sym_side"), "status": "ERROR", "reason": f"{type(error).__name__}: {error}"}
            record["elapsed_s"] = round(time.monotonic() - start, 3)
            handle.write(json.dumps(record, default=str) + "\n")
            handle.flush()
            print(f"{index}/{len(jobs)} {record.get('sym_side')} {record.get('status')} board={record.get('board_reproduced')} events={record.get('forward_event_count')} {record['elapsed_s']}s", flush=True)


if __name__ == "__main__":
    main()
