#!/usr/bin/env python3
"""Replay captured causal states through vector_direct_trader with an in-memory broker."""
import argparse
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(stream, row):
    stream.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _canonical_sha256(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _timestamps_sha256(timestamps):
    payload = json.dumps([int(ts) for ts in timestamps], separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def _prefix_digest(timestamps, close, index):
    digest = hashlib.sha256()
    for key, value in (("timestamps", timestamps[:index + 1]), ("close", close[:index + 1])):
        contiguous = np.ascontiguousarray(value)
        digest.update(key.encode())
        digest.update(contiguous.view(np.uint8))
    return digest.hexdigest()


def _validate_state_stream(args, arrays, active):
    raw = Path(args.state_stream).read_bytes()
    stream_sha256 = hashlib.sha256(raw).hexdigest()
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(records) < 3 or records[0].get("record") != "replay_manifest" or records[-1].get("record") != "replay_summary":
        raise ValueError("state stream is missing its leading manifest or completion summary")
    manifest = records[0]
    summary = records[-1]
    timestamps = np.asarray(arrays["timestamps"], dtype=np.int64)
    close = np.asarray(arrays["close"])
    selected_indexes = np.flatnonzero((timestamps > int(args.start_ts)) & (timestamps <= int(args.end_ts)))
    selected = timestamps[selected_indexes]
    expected_sha = _timestamps_sha256(selected)
    expected_total = int(args.expected_warmup_bars) + int(args.expected_scored_bars)
    checks = {
        "sym_side": str(manifest.get("sym_side", "")).upper() == str(active.get("sym_side", "")).upper(),
        "npz_sha256": manifest.get("input_npz_sha256") == _sha(args.npz),
        "active_set_sha256": manifest.get("active_set_file_sha256") == _sha(args.active_set),
        "active_overrides_sha256": manifest.get("active_set_overrides_sha256") == _canonical_sha256(active.get("overrides") or {}),
        "timestamp_sha256": manifest.get("selected_timestamps_sha256") == expected_sha == args.expected_timestamps_sha256,
        "bar_count": manifest.get("bar_count") == expected_total == len(selected),
        "timestamp_policy": manifest.get("timestamp_policy") == "crypto_15m_regular",
        "manifest_phase_counts": manifest.get("warmup_bar_count_expected") == args.expected_warmup_bars and manifest.get("scored_bar_count_expected") == args.expected_scored_bars,
        "summary_phase_counts": summary.get("phase_counts") == {"warmup": args.expected_warmup_bars, "scored": args.expected_scored_bars},
        "interval_start": manifest.get("interval_start_exclusive") == _iso(args.start_ts),
        "interval_end": manifest.get("interval_end_inclusive") == _iso(args.end_ts),
        "score_start": manifest.get("score_start_exclusive") == _iso(args.score_start_ts),
        "decision_clock_contract": manifest.get("npz_open_ts_to_decision_clock_seconds") == 900,
        "code_manifest_sha256": manifest.get("code_manifest_sha256") == args.code_manifest_sha,
        "summary_coverage": summary.get("coverage_ok") is True and summary.get("states_evaluated") == expected_total,
        "summary_invalid_states": not summary.get("invalid_state_counts"),
    }
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise ValueError(f"state stream provenance/coverage validation failed: {failed}")
    if str(summary.get("sym_side", "")).upper() != str(active.get("sym_side", "")).upper():
        raise ValueError("state stream summary sym_side differs from frozen active set")
    initial_rows = [row for row in records[1:-1] if row.get("record") == "initial_lifecycle"]
    if len(initial_rows) != 1 or initial_rows[0].get("ok") is not True:
        raise ValueError("state stream must contain one valid initial lifecycle snapshot")
    states = [row for row in records[1:-1] if row.get("record") == "state"]
    if len(states) != expected_total:
        raise ValueError(f"state stream has {len(states)} states, expected {expected_total}")
    expected_phases = {"warmup": int(args.expected_warmup_bars), "scored": int(args.expected_scored_bars)}
    actual_phases = {"warmup": 0, "scored": 0}
    settings_hashes = set()
    for index, (row, npz_index, expected_bar) in enumerate(zip(states, selected_indexes, selected), 1):
        bar = int(expected_bar)
        phase = "warmup" if bar <= int(args.score_start_ts) else "scored"
        if (row.get("row") != index or int(row.get("npz_open_ts", -1)) != bar
                or int(row.get("prefix_last_ts", -1)) != bar
                or int(row.get("decision_clock_ts", -1)) != bar + 900
                or row.get("phase") != phase):
            raise ValueError(f"state order/clock/phase mismatch at stream row {index}, bar {bar}")
        if row.get("prefix_sha256_timestamps_close") != _prefix_digest(timestamps, close, int(npz_index)):
            raise ValueError(f"causal prefix hash mismatch at bar {bar}")
        if row.get("ok") is not True or not (row.get("planner_state") or {}).get("ok"):
            raise ValueError(f"invalid state_for/planner state at bar {bar}: {row.get('reason')}")
        if row.get("settings_sha256") != active.get("overrides_sha256"):
            raise ValueError(f"state settings hash differs from frozen active overrides at bar {bar}")
        if float(row.get("market_price")) != float(close[npz_index]):
            raise ValueError(f"market price differs from causal NPZ close at bar {bar}")
        actual_phases[phase] += 1
        settings_hashes.add(row.get("settings_sha256"))
    if actual_phases != expected_phases or len(settings_hashes) != 1:
        raise ValueError(f"phase/settings consistency failure: {actual_phases}, settings={settings_hashes}")
    return manifest, states, summary, stream_sha256


class _ReplayTime:
    def __init__(self, clock, real_module):
        self.clock = clock
        self.real_module = real_module

    def time(self):
        return float(self.clock[0])

    def __getattr__(self, name):
        return getattr(self.real_module, name)


def _iso(timestamp):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(timestamp), timezone.utc).isoformat().replace("+00:00", "Z")


class _MockBroker:
    def __init__(self, trader, exchange, ticks, fills):
        self.trader = trader
        self.exchange = exchange
        self.ticks = ticks
        self.fills = fills
        self.position = {}
        self.price_now = 0.0
        self.bar_ts = 0
        self.dc_row = {}
        self.algos = {}
        self.next_algo = 0
        self._xi = exchange.get("symbols") or {}

    def exchange_info(self):
        return self._xi

    def is_dual(self, _account):
        return True

    def price(self, _symbol):
        return float(self.price_now)

    def positions(self, _account):
        for row in self.position.values():
            row["price"] = self.price_now
        return copy.deepcopy(self.position)

    def size(self, order, held, price):
        return self.trader_module.Binance.size(self, order, held, price)

    def _floor(self, qty, step):
        return self.trader_module.Binance._floor(qty, step)

    def send(self, account, order, qty):
        ss = str(order["ss"]).upper()
        before = float((self.position.get(ss) or {}).get("qty") or 0.0)
        row = dict(self.position.get(ss) or {})
        old_entry = float(row.get("entry") or self.price_now or 0.0)
        qty = float(qty)
        action = str(order["act"]).upper()
        if action == "OPEN":
            after, entry = qty, self.price_now
        elif action == "AUGMENT":
            after = before + qty
            entry = (before * old_entry + qty * self.price_now) / after if after > 0 else 0.0
        elif action == "REDUCE":
            after, entry = max(0.0, before - qty), old_entry
        else:
            after, entry = 0.0, old_entry
        if after > 0:
            self.position[ss] = {"qty": after, "price": self.price_now, "entry": entry, "break_even": entry, "cost": after * entry}
        else:
            self.position.pop(ss, None)
        self.fills.append({"record": "mock_fill", "acct": account, "sym_side": ss, "action": action,
                           "bar_ts": self.bar_ts, "qty": qty, "position_before": before,
                           "position_after": after, "price": self.price_now, "key": order.get("key")})
        return {"status": "FILLED", "executedQty": qty, "avgPrice": self.price_now,
                "mock_broker": True}

    def _req(self, method, path, acct=None, params=None, **_kwargs):
        params = dict(params or {})
        if path == "/fapi/v1/openAlgoOrders" and method == "GET":
            symbol = params.get("symbol")
            return [dict(row) for row in self.algos.values() if row.get("symbol") == symbol]
        if path == "/fapi/v1/algoOrder" and method == "POST":
            self.next_algo += 1
            algo_id = self.next_algo
            row = {"algoId": algo_id, "symbol": params["symbol"], "orderType": params["type"],
                   "type": params["type"], "positionSide": params.get("positionSide", "BOTH"),
                   "triggerPrice": params["triggerPrice"], "closePosition": params.get("closePosition"),
                   "workingType": params.get("workingType"), "algoStatus": "NEW", "account": acct}
            self.algos[algo_id] = row
            return {"algoId": algo_id, "algoStatus": "NEW"}
        if path == "/fapi/v1/algoOrder" and method == "DELETE":
            self.algos.pop(int(params.get("algoId") or 0), None)
            return {"code": 200}
        raise RuntimeError(f"mock broker rejected unimplemented API {method} {path}")


def replay(args):
    arrays = np.load(args.npz, allow_pickle=True)
    timestamps = np.asarray(arrays["timestamps"], dtype=np.int64)
    active_snapshot = json.loads(Path(args.active_set).read_text())
    stream_manifest, validated_states, stream_summary, validated_stream_sha = _validate_state_stream(
        args, arrays, active_snapshot
    )
    root = Path(args.run_root).resolve()
    isolation_root = Path(args.isolation_root).resolve()
    code_root = Path(args.code_root).resolve()
    npz_path = Path(args.npz).resolve()
    active_path = Path(args.active_set).resolve()
    exchange_info_path = Path(args.exchange_info).resolve()
    exchange_ticks_path = Path(args.exchange_ticks).resolve()
    code_manifest_path = Path(args.code_manifest).resolve()
    for name, path in (("run_root", root), ("code_root", code_root), ("npz", npz_path),
                       ("active_set", active_path), ("exchange_info", exchange_info_path),
                       ("exchange_ticks", exchange_ticks_path), ("code_manifest", code_manifest_path)):
        if not path.is_relative_to(isolation_root):
            raise RuntimeError(f"Replay {name} is outside the isolated candidate root: {path}")
    if code_root == root or code_root.is_relative_to(root) or root.is_relative_to(code_root):
        raise RuntimeError("Replay source and writable run output must be separate candidate paths")
    if _sha(code_manifest_path) != args.code_manifest_sha:
        raise RuntimeError("Replay source manifest SHA differs from the frozen stream manifest")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    out_dir = root / "data" / "vector_direct"
    out_dir.mkdir(parents=True, exist_ok=True)
    for source, target in ((args.exchange_info, out_dir / "exchange_info_crypto.json"),
                           (args.exchange_ticks, out_dir / "exchange_ticks_crypto.json")):
        target.write_bytes(Path(source).read_bytes())
    sys.path.insert(0, str(code_root))
    os.environ["BASE_PATH"] = str(code_root)
    os.environ["V12_BASE_PATH"] = str(code_root)
    os.environ["V12_NPZ_DIR"] = str(Path(args.npz).resolve().parent)
    os.environ["VD_MAC_POSITIONS"] = "1"
    from tools import vector_direct_trader as trader_module
    import tools.vector_direct_stops as stops_module
    from tools.vector_direct_stops import Stops

    decision_clock = [0.0]
    replay_time = _ReplayTime(decision_clock, time)
    trader_module.time = replay_time
    stops_module.time = replay_time
    trader_module.apply_venue_sizing("crypto")
    config = __import__("config").Config()
    if Path(config.BASE_PATH).resolve() != code_root:
        raise RuntimeError(f"Replay Config.BASE_PATH escapes isolated source candidate: {config.BASE_PATH}")
    for module_name, module in (("vector_direct_trader", trader_module), ("vector_direct_stops", stops_module)):
        if not Path(module.__file__).resolve().is_relative_to(code_root):
            raise RuntimeError(f"Replay imported {module_name} outside isolated source candidate: {module.__file__}")
    with exchange_info_path.open() as stream:
        exchange = json.load(stream)
    with exchange_ticks_path.open() as stream:
        ticks = json.load(stream)
    active = active_snapshot
    admission = {str(value).upper() for value in active.get("account_admission_keys", [])}
    sym_side = str(active["sym_side"]).upper()
    if f"{args.account}:{sym_side}".upper() not in admission:
        raise ValueError(f"account {args.account} is not admitted by frozen active-set snapshot")

    class _Args:
        venue = "crypto"
        dry_run = False
        force_active = False
        npz_dir = str(Path(args.npz).resolve().parent)

    trader = trader_module.Trader.__new__(trader_module.Trader)
    trader.a = _Args()
    trader.venue = "crypto"
    trader.root = root
    trader.state_dir = root / "data" / "forward_state"
    trader.out = out_dir
    trader.accounts = [args.account]
    trader.done = set()
    trader.done_ts = {}
    trader.filled_open_anchors = set()
    trader.quiet = set()
    trader.attempts = {}
    trader.halted_now = {}
    fills = []
    broker = _MockBroker(trader, exchange, ticks, fills)
    broker.trader_module = trader_module
    if type(broker) is not _MockBroker or broker.send.__func__ is not _MockBroker.send or broker._req.__func__ is not _MockBroker._req:
        raise RuntimeError("Replay broker transport is not the exact in-memory mock implementation")
    broker.out = out_dir
    trader.broker = broker
    trader.stops = Stops(trader)
    trader.stops._ticks = ticks
    _real_ledger = trader._ledger
    trader._ledger = lambda row: _real_ledger({"mock_broker_replay": True, **row})
    trader.capture_account_position = lambda *_args, **_kwargs: {"status": "MOCK_BROKER_SNAPSHOT", "mock_broker": True}

    if "dc_low_15m" not in arrays or "dc_high_15m" not in arrays:
        raise ValueError("NPZ lacks causal dc_low_15m/dc_high_15m stop inputs")
    dc_low = np.asarray(arrays["dc_low_15m"], dtype=float)
    dc_high = np.asarray(arrays["dc_high_15m"], dtype=float)
    time_to_index = {int(ts): index for index, ts in enumerate(timestamps)}
    def causal_dc15(npz_dir, symbol):
        index = time_to_index.get(broker.bar_ts)
        if index is None:
            return {}
        return {"bar_ts": broker.bar_ts, "dc_low_15m": float(dc_low[index]),
                "dc_high_15m": float(dc_high[index]), "src": "frozen_npz_bar"}
    stops_module.dc15 = causal_dc15

    manifest = {"record": "vector_consumer_manifest", "schema_version": 1, "account": args.account,
                "sym_side": sym_side, "active_set_sha256": _sha(args.active_set), "npz_sha256": _sha(args.npz),
                "state_stream_sha256": validated_stream_sha,
                "state_stream_manifest_sha256": _canonical_sha256(stream_manifest),
                "state_stream_summary": {key: stream_summary.get(key) for key in ("states_evaluated", "phase_counts", "coverage_ok")},
                "decision_clock_semantics": "decision clock is signal bar open + 900 seconds",
                "code_manifest": str(code_manifest_path), "code_manifest_sha256": _sha(code_manifest_path),
                "consumer_script_sha256": _sha(__file__),
                "vector_direct_trader_sha256": _sha(code_root / "tools" / "vector_direct_trader.py"),
                "vector_direct_stops_sha256": _sha(code_root / "tools" / "vector_direct_stops.py"),
                "exchange_info_sha256": _sha(args.exchange_info), "exchange_ticks_sha256": _sha(args.exchange_ticks),
                "start_position": "flat", "consumer": "actual Trader.send_orders + in-memory broker",
                "sizing": {"start_position_size": trader_module.START_POSITION_SIZE,
                           "max_open_notional": trader_module.MAX_OPEN_NOTIONAL,
                           "config_start_position_size": float(config.START_POSITION_SIZE),
                           "config_max_position_size": float(config.MAX_POSITION_SIZE),
                           "config_max_order_value": float(config.MAX_ORDER_VALUE),
                           "config_base_path": str(config.BASE_PATH),
                           "market_mode": getattr(config, "MARKET_MODE", None),
                           "market_mode_file": str(config.MARKET_MODE_FILE),
                           "market_mode_file_exists": Path(config.MARKET_MODE_FILE).exists()},
                "protection": "actual vector_direct_stops.Stops; causal NPZ DC15 values; in-memory algo endpoint",
                "fee_funding_model": args.fee_funding_model,
                "standing_stop_fill_model": args.standing_stop_fill_model,
                "acceptance_status": "diagnostic_only_incomplete_fee_funding_and_ohlc_stop_fill",
                "broker_transport": "exact _MockBroker.send and _MockBroker._req methods; unsupported requests raise",
                "mock_broker_transport_verified": type(broker) is _MockBroker and broker.send.__func__ is _MockBroker.send and broker._req.__func__ is _MockBroker._req,
                "isolation_root": str(isolation_root), "run_root": str(root), "source_root": str(code_root),
                "phase_score_start_ts": args.score_start_ts}
    state_rows, action_rows, invalid = 0, 0, {}
    phase_counts = {"warmup": 0, "scored": 0}
    previous_observed = {}
    previous_observed_at = 0
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_name(output_path.name + ".tmp")
    with temp_output.open("w") as target:
        _write(target, manifest)
        for record in validated_states:
            state_rows += 1
            bar = int(record["npz_open_ts"])
            phase = str(record.get("phase") or "")
            if phase not in phase_counts:
                raise ValueError(f"unknown replay phase {phase!r} at bar {bar}")
            phase_counts[phase] += 1
            broker.bar_ts = bar
            decision_clock[0] = bar + 900
            broker.price_now = float(record["market_price"])
            state = dict(record.get("planner_state") or {})
            if not state.get("ok"):
                reason = str(state.get("reason") or "unspecified")
                invalid[reason] = invalid.get(reason, 0) + 1
            positions = broker.positions(args.account)
            prev_qty = previous_observed if previous_observed_at and bar - trader_module.BAR_S <= previous_observed_at < bar + trader_module.BAR_S else {}
            orders, notes = trader_module.plan_account(
                args.account, "crypto", [sym_side], {sym_side: state}, positions, bar,
                trader.done, lambda acct, side: f"{acct}:{side}".upper() in admission,
                prev_qty=prev_qty, halted={},
            )
            if notes:
                _write(target, {"record": "planner_notes", "account": args.account,
                                "sym_side": sym_side, "bar_ts": bar, "phase": phase, "notes": notes})
            state_ref = trader_module.persist_decision_state(out_dir, state)
            for order in orders:
                order["bar"] = bar
                order["decision_provenance"] = state_ref
                side_orders = [trader_module.order_intent(order)]
                plan_doc = {"schema": "vector-direct-plan-decision-v1", "acct": args.account,
                            "ss": sym_side, "bar": bar, "state_provenance": state_ref,
                            "control": {"venue": "crypto", "bar": bar,
                                        "state_sha256": state_ref["state_sha256"],
                                        "keys_member": True, "allowed": True,
                                        "held": float((positions.get(sym_side) or {}).get("qty") or 0.0),
                                        "orders": side_orders, "notes": []},
                            "orders": side_orders}
                order["plan_decision"] = trader_module.persist_plan_decision(out_dir, plan_doc)
                action_rows += 1
            _write(target, {"record": "planner_bar", "account": args.account, "sym_side": sym_side,
                            "bar_ts": bar, "phase": phase, "orders": orders,
                            "position_before": positions.get(sym_side), "state_ok": state.get("ok")})
            trader.send_orders(args.account, orders, positions, False)
            previous_observed = {ss: float(position.get("qty") or 0.0) for ss, position in positions.items()}
            previous_observed_at = bar + trader_module.BAR_S
            _write(target, {"record": "mock_position", "account": args.account, "sym_side": sym_side,
                            "bar_ts": bar, "phase": phase, "position_after": broker.position.get(sym_side),
                            "done_key_count": len(trader.done)})
        for fill in fills:
            _write(target, fill)
        coverage_ok = (state_rows == args.expected_warmup_bars + args.expected_scored_bars
                       and phase_counts == {"warmup": args.expected_warmup_bars,
                                            "scored": args.expected_scored_bars})
        summary = {"record": "vector_consumer_summary", "account": args.account, "sym_side": sym_side,
                   "state_rows": state_rows, "planner_orders": action_rows, "mock_fills": len(fills),
                   "phase_counts": phase_counts,
                   "invalid_state_counts": invalid, "final_position": broker.position.get(sym_side),
                   "coverage_matches_expected": coverage_ok,
                   "trader_ledger": str(trader._ledger_path())}
        _write(target, summary)
    if not coverage_ok:
        raise AssertionError(f"state stream coverage mismatch: rows={state_rows}, phases={phase_counts}")
    os.replace(temp_output, output_path)
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-stream", required=True)
    parser.add_argument("--active-set", required=True)
    parser.add_argument("--npz", required=True)
    parser.add_argument("--exchange-info", required=True)
    parser.add_argument("--exchange-ticks", required=True)
    parser.add_argument("--code-root", required=True)
    parser.add_argument("--isolation-root", required=True)
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--account", required=True, choices=("ang", "inf"))
    parser.add_argument("--start-ts", type=int, required=True)
    parser.add_argument("--end-ts", type=int, required=True)
    parser.add_argument("--score-start-ts", type=int, required=True)
    parser.add_argument("--expected-timestamps-sha256", required=True)
    parser.add_argument("--expected-warmup-bars", type=int, required=True)
    parser.add_argument("--expected-scored-bars", type=int, required=True)
    parser.add_argument("--code-manifest", required=True)
    parser.add_argument("--code-manifest-sha", required=True)
    parser.add_argument("--fee-funding-model", choices=("ignored_flat_basis",), required=True)
    parser.add_argument("--standing-stop-fill-model", choices=("orders_persist_no_ohlc_trigger",), required=True)
    args = parser.parse_args()
    result = replay(args)
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
