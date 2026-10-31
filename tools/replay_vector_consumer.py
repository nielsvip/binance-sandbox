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
    root = Path(args.run_root).resolve()
    out_dir = root / "data" / "vector_direct"
    out_dir.mkdir(parents=True, exist_ok=True)
    for source, target in ((args.exchange_info, out_dir / "exchange_info_crypto.json"),
                           (args.exchange_ticks, out_dir / "exchange_ticks_crypto.json")):
        target.write_bytes(Path(source).read_bytes())
    code_root = Path(args.code_root).resolve()
    sys.path.insert(0, str(code_root))
    os.environ["V12_BASE_PATH"] = str(code_root)
    os.environ["V12_NPZ_DIR"] = str(Path(args.npz).resolve().parent)
    os.environ["VD_MAC_POSITIONS"] = "1"
    from tools import vector_direct_trader as trader_module
    from tools.vector_direct_stops import Stops

    trader_module.apply_venue_sizing("crypto")
    config = __import__("config").Config()
    with Path(args.exchange_info).open() as stream:
        exchange = json.load(stream)
    with Path(args.exchange_ticks).open() as stream:
        ticks = json.load(stream)
    active = json.loads(Path(args.active_set).read_text())
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
    broker.out = out_dir
    trader.broker = broker
    trader.stops = Stops(trader)
    trader.stops._ticks = ticks
    _real_ledger = trader._ledger
    trader._ledger = lambda row: _real_ledger({"mock_broker_replay": True, **row})
    trader.capture_account_position = lambda *_args, **_kwargs: {"status": "MOCK_BROKER_SNAPSHOT", "mock_broker": True}

    arrays = np.load(args.npz, allow_pickle=True)
    timestamps = np.asarray(arrays["timestamps"], dtype=np.int64)
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
    import tools.vector_direct_stops as stops_module
    stops_module.dc15 = causal_dc15

    manifest = {"record": "vector_consumer_manifest", "schema_version": 1, "account": args.account,
                "sym_side": sym_side, "active_set_sha256": _sha(args.active_set), "npz_sha256": _sha(args.npz),
                "state_stream_sha256": _sha(args.state_stream),
                "code_manifest_sha256": args.code_manifest_sha,
                "exchange_info_sha256": _sha(args.exchange_info), "exchange_ticks_sha256": _sha(args.exchange_ticks),
                "start_position": "flat", "consumer": "actual Trader.send_orders + in-memory broker",
                "sizing": {"start_position_size": trader_module.START_POSITION_SIZE,
                           "max_open_notional": trader_module.MAX_OPEN_NOTIONAL,
                           "config_start_position_size": float(config.START_POSITION_SIZE),
                           "config_max_position_size": float(config.MAX_POSITION_SIZE),
                           "config_max_order_value": float(config.MAX_ORDER_VALUE),
                           "market_mode": getattr(config, "MARKET_MODE", None),
                           "market_mode_file": str(config.MARKET_MODE_FILE),
                           "market_mode_file_exists": Path(config.MARKET_MODE_FILE).exists()},
                "protection": "actual vector_direct_stops.Stops; causal NPZ DC15 values; in-memory algo endpoint",
                "no_exchange_requests": True, "phase_score_start_ts": args.score_start_ts}
    state_rows, action_rows, invalid = 0, 0, {}
    phase_counts = {"warmup": 0, "scored": 0}
    previous_observed = {}
    previous_observed_at = 0
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_name(output_path.name + ".tmp")
    with Path(args.state_stream).open() as source, temp_output.open("w") as target:
        _write(target, manifest)
        for line in source:
            record = json.loads(line)
            if record.get("record") != "state":
                continue
            state_rows += 1
            bar = int(record["npz_open_ts"])
            phase = str(record.get("phase") or "")
            if phase not in phase_counts:
                raise ValueError(f"unknown replay phase {phase!r} at bar {bar}")
            phase_counts[phase] += 1
            broker.bar_ts = bar
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
    parser.add_argument("--run-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--account", required=True, choices=("ang", "inf"))
    parser.add_argument("--score-start-ts", type=int, required=True)
    parser.add_argument("--expected-warmup-bars", type=int, required=True)
    parser.add_argument("--expected-scored-bars", type=int, required=True)
    parser.add_argument("--code-manifest-sha", default="")
    args = parser.parse_args()
    result = replay(args)
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
