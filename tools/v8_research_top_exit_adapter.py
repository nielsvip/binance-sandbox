#!/usr/bin/env python3
"""Backtest-only schedule adapter for E02 + E10/E11 faithful-engine replay.

This module cannot run live.  It accepts only an explicit research spec whose
event schedule was produced by vec_top_exit_campaign.py, validates provenance
and the alternating position lifecycle, and supplies exact next-open actions to
backtest_v8_engine.py.

It intentionally does not decide historical signals again.  Phase 1 of parity
is narrower: prove that the faithful engine can execute the independently
audited E02/E10/E11 schedule with the same timestamps, side, prices, quantities,
and trade accounting.  A separate independent signal implementation is needed
before any matrix promotion.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SPEC_KIND = "V8_RESEARCH_E02_E10_E11_REPLAY"
SPEC_VERSION = 1
REASON_PREFIX = "V8_RESEARCH_TOP_EXIT_REPLAY"


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ReplayAction:
    fill_ts: int
    event_type: str
    action: str
    order_side: str
    position_side: str
    fill_price: float
    quantity: float | None
    full_close: bool
    reason: str
    source_event: dict[str, Any]


class ResearchReplayError(RuntimeError):
    pass


class TopExitReplayAdapter:
    """Validated, fail-closed action schedule for one symbol and one side."""

    def __init__(self, spec_path: str | Path):
        self.spec_path = Path(spec_path).resolve()
        self.spec = json.loads(self.spec_path.read_text())
        self._validate_spec()
        self.symbol = str(self.spec["symbol"]).upper()
        self.position_side = str(self.spec["side"]).upper()
        self.account = str(self.spec["account"])
        self.seed_notional_usd = float(self.spec["seed_notional_usd"])
        schedule_path = Path(self.spec["event_schedule"])
        if not schedule_path.is_absolute():
            schedule_path = (self.spec_path.parent / schedule_path).resolve()
        self.schedule_path = schedule_path
        actual_schedule_sha = sha256_file(self.schedule_path)
        expected_schedule_sha = str(self.spec["expected_schedule_sha256"]).lower()
        if actual_schedule_sha.lower() != expected_schedule_sha:
            raise ResearchReplayError(
                "event schedule hash mismatch: "
                f"expected={expected_schedule_sha}, actual={actual_schedule_sha}"
            )
        self.events = self._load_events()
        self.actions = self._build_actions()
        self._by_ts: dict[int, list[ReplayAction]] = {}
        for action in self.actions:
            self._by_ts.setdefault(action.fill_ts, []).append(action)
        self.executed: list[dict[str, Any]] = []
        self.refused: list[dict[str, Any]] = []
        self._runtime_equity_usd = self.seed_notional_usd
        self._runtime_open: dict[str, float] | None = None
        self._planned_quantities: dict[tuple[int, str], float] = {}

    def _validate_spec(self) -> None:
        if self.spec.get("kind") != SPEC_KIND:
            raise ResearchReplayError(f"wrong spec kind: {self.spec.get('kind')!r}")
        if int(self.spec.get("version", 0)) != SPEC_VERSION:
            raise ResearchReplayError(f"unsupported spec version: {self.spec.get('version')!r}")
        required = {
            "symbol",
            "side",
            "account",
            "event_schedule",
            "npz_path",
            "expected_npz_sha256",
            "expected_schedule_sha256",
            "seed_notional_usd",
            "commission_round_trip_pct",
            "expected_gain_pct",
            "expected_tim_rth_pct",
            "slippage_bps_one_way",
            "source_artifact",
        }
        missing = sorted(required - set(self.spec))
        if missing:
            raise ResearchReplayError(f"missing spec fields: {missing}")
        if str(self.spec["side"]).upper() not in {"LONG", "SHORT"}:
            raise ResearchReplayError("side must be LONG or SHORT")
        if float(self.spec["seed_notional_usd"]) <= 0:
            raise ResearchReplayError("seed_notional_usd must be positive")
        if bool(self.spec.get("promotion_allowed", False)):
            raise ResearchReplayError("research replay spec cannot allow promotion")

    def _load_events(self) -> list[dict[str, Any]]:
        if not self.schedule_path.exists():
            raise ResearchReplayError(f"event schedule missing: {self.schedule_path}")
        opener = gzip.open if self.schedule_path.suffix == ".gz" else open
        with opener(self.schedule_path, "rt") as fh:
            events = [json.loads(line) for line in fh if line.strip()]
        if not events:
            raise ResearchReplayError("empty event schedule")
        return events

    def validate_npz(self, npz_path: str | Path) -> str:
        actual = sha256_file(npz_path)
        expected = str(self.spec["expected_npz_sha256"]).lower()
        if actual.lower() != expected:
            raise ResearchReplayError(
                f"NPZ hash mismatch: expected={expected}, actual={actual}, path={npz_path}"
            )
        # Independently bind every scheduled fill to the loaded source bar.
        # Real campaign events carry RTH-relative indices; compact unit fixtures
        # without them use adjacent rows in the supplied timestamp array.
        import numpy as np

        with np.load(npz_path, allow_pickle=False) as data:
            timestamps = np.asarray(data["timestamps"], dtype=np.int64)
            ts_to_index = {int(ts): i for i, ts in enumerate(timestamps)}
            open_key = "open_5m" if "open_5m" in data.files else "open"
            close_key = "close_5m" if "close_5m" in data.files else "close"
            opens = np.asarray(data[open_key], dtype=np.float64)
            closes = np.asarray(data[close_key], dtype=np.float64)
            for action in self.actions:
                if action.fill_ts not in ts_to_index:
                    raise ResearchReplayError(
                        f"fill timestamp absent from loaded NPZ: {action.fill_ts}"
                    )
                fill_i = ts_to_index[action.fill_ts]
                event = action.source_event
                if action.event_type in {"EXIT", "REENTRY"}:
                    signal_ts = int(event.get("signal_ts", 0))
                    if signal_ts not in ts_to_index:
                        raise ResearchReplayError(
                            f"signal timestamp absent from loaded NPZ: {signal_ts}"
                        )
                    signal_i = ts_to_index[signal_ts]
                    if "fill_index" in event and "signal_index" in event:
                        if int(event["fill_index"]) != int(event["signal_index"]) + 1:
                            raise ResearchReplayError(
                                "signal must fill at exact next RTH index"
                            )
                    elif fill_i != signal_i + 1:
                        raise ResearchReplayError(
                            "signal must be on row immediately before next fill"
                        )
                expected_fill = self.expected_fill_from_loaded_bar(
                    action,
                    raw_open=float(opens[fill_i]),
                    raw_close=float(closes[fill_i]),
                )
                if abs(expected_fill - action.fill_price) > max(
                    1e-9, abs(action.fill_price) * 1e-10
                ):
                    raise ResearchReplayError(
                        "scheduled fill price does not match loaded NPZ bar open/close: "
                        f"ts={action.fill_ts} scheduled={action.fill_price} "
                        f"loaded={expected_fill}"
                    )
        return actual

    def validate_runtime(
        self,
        *,
        account: str,
        symbols: list[str],
        mode: str,
        seed_positions_file: str = "",
        round_trip_cost_pct: float,
    ) -> None:
        if mode != "tradier":
            raise ResearchReplayError("top-exit replay is tradier-backtest only")
        if account != self.account:
            raise ResearchReplayError(
                f"account mismatch: spec={self.account}, runtime={account}"
            )
        if [s.upper() for s in symbols] != [self.symbol]:
            raise ResearchReplayError(
                f"replay requires exactly [{self.symbol}], got {symbols}"
            )
        if seed_positions_file:
            raise ResearchReplayError("seeded live positions are forbidden in research replay")
        expected_cost = float(self.spec["commission_round_trip_pct"])
        if abs(float(round_trip_cost_pct) - expected_cost) > 1e-12:
            raise ResearchReplayError(
                f"round-trip cost mismatch: spec={expected_cost}, runtime={round_trip_cost_pct}"
            )

    def _build_actions(self) -> list[ReplayAction]:
        actions: list[ReplayAction] = []
        open_state = False
        active_qty: float | None = None
        equity_before_entry = 1.0
        last_ts = -1
        for event in self.events:
            event_type = str(event.get("type", "")).upper()
            if event_type not in {"ENTRY", "EXIT", "REENTRY", "MTM_FINAL"}:
                raise ResearchReplayError(f"unsupported event type: {event_type!r}")
            fill_ts = int(event.get("fill_ts", 0))
            fill_px = float(event.get("fill_px", 0))
            if fill_ts <= 0 or fill_px <= 0:
                raise ResearchReplayError(f"invalid fill event: {event}")
            if fill_ts <= last_ts:
                raise ResearchReplayError("event fill timestamps must be strictly increasing")
            last_ts = fill_ts
            if event_type in {"ENTRY", "REENTRY"}:
                if open_state:
                    raise ResearchReplayError(f"{event_type} while already open")
                if event_type == "REENTRY":
                    latency = int(event.get("latency_bars", -1))
                    if latency != 1:
                        raise ResearchReplayError(f"REENTRY latency must be one bar: {event}")
                    if (
                        "fill_index" in event
                        and "signal_index" in event
                        and int(event["fill_index"]) != int(event["signal_index"]) + 1
                    ):
                        raise ResearchReplayError(
                            f"REENTRY must fill at exact next RTH index: {event}"
                        )
                    if int(event.get("fill_ts", 0)) <= int(event.get("signal_ts", 0)):
                        raise ResearchReplayError(
                            f"REENTRY fill_ts must be after signal_ts: {event}"
                        )
                active_qty = self.seed_notional_usd * equity_before_entry / fill_px
                if not (active_qty > 0):
                    raise ResearchReplayError("computed non-positive entry quantity")
                order_side = "BUY" if self.position_side == "LONG" else "SELL"
                action_name = "OPEN" if event_type == "ENTRY" else "REENTRY"
                reason_leaf = (
                    "SEED"
                    if event_type == "ENTRY"
                    else str(event.get("reason", "UNKNOWN_REENTRY"))
                )
                actions.append(
                    ReplayAction(
                        fill_ts=fill_ts,
                        event_type=event_type,
                        action=action_name,
                        order_side=order_side,
                        position_side=self.position_side,
                        fill_price=fill_px,
                        quantity=active_qty,
                        full_close=False,
                        reason=f"{REASON_PREFIX}__{reason_leaf}",
                        source_event=event,
                    )
                )
                open_state = True
            else:
                if not open_state or not active_qty:
                    raise ResearchReplayError(f"{event_type} while flat")
                latency = int(event.get("latency_bars", -1))
                if event_type == "EXIT" and latency != 1:
                    raise ResearchReplayError(f"EXIT latency must be one bar: {event}")
                if (
                    event_type == "EXIT"
                    and "fill_index" in event
                    and "signal_index" in event
                    and int(event["fill_index"]) != int(event["signal_index"]) + 1
                ):
                    raise ResearchReplayError(
                        f"EXIT must fill at exact next RTH index: {event}"
                    )
                if event_type == "EXIT" and int(event.get("fill_ts", 0)) <= int(
                    event.get("signal_ts", 0)
                ):
                    raise ResearchReplayError(
                        f"EXIT fill_ts must be after signal_ts: {event}"
                    )
                if event_type == "MTM_FINAL" and latency != 0:
                    raise ResearchReplayError(f"MTM_FINAL latency must be zero: {event}")
                actions.append(
                    ReplayAction(
                        fill_ts=fill_ts,
                        event_type=event_type,
                        action="CLOSE",
                        order_side="SELL" if self.position_side == "LONG" else "BUY",
                        position_side=self.position_side,
                        fill_price=fill_px,
                        quantity=None,  # engine must read the full active position
                        full_close=True,
                        reason=(
                            f"{REASON_PREFIX}__E02_DONCHIAN"
                            if event_type == "EXIT"
                            else f"{REASON_PREFIX}__END_OF_SAMPLE_MTM"
                        ),
                        source_event=event,
                    )
                )
                equity_before_entry = float(event.get("equity_after_fill", 0))
                if not (equity_before_entry > 0):
                    raise ResearchReplayError("EXIT missing positive equity_after_fill")
                active_qty = None
                open_state = False
        if open_state:
            raise ResearchReplayError(
                "schedule ends with an open position; explicit MTM replay is not implemented"
            )
        expected = self.spec.get("expected_counts") or {}
        actual = {
            "entries": sum(a.event_type == "ENTRY" for a in actions),
            "exits": sum(a.event_type == "EXIT" for a in actions),
            "reentries": sum(a.event_type == "REENTRY" for a in actions),
            "mtm_final": sum(a.event_type == "MTM_FINAL" for a in actions),
            "e10_reclaims": sum("E10_RECLAIM" in a.reason for a in actions),
            "e11_lower": sum("E11_LOWER_PRICE" in a.reason for a in actions),
        }
        for key, value in expected.items():
            if key in actual and int(value) != int(actual[key]):
                raise ResearchReplayError(
                    f"event count mismatch for {key}: expected={value}, actual={actual[key]}"
                )
        return actions

    def expected_fill_from_loaded_bar(
        self,
        action: ReplayAction,
        *,
        raw_open: float,
        raw_close: float,
    ) -> float:
        """Apply declared slippage to the bar actually loaded by the engine."""
        slip = float(self.spec["slippage_bps_one_way"]) / 10_000.0
        raw = raw_close if action.event_type == "MTM_FINAL" else raw_open
        if not (raw > 0):
            raise ResearchReplayError(
                f"loaded bar has no positive {'close' if action.event_type == 'MTM_FINAL' else 'open'}"
            )
        is_buy = action.order_side == "BUY"
        return raw * (1.0 + slip if is_buy else 1.0 - slip)

    def quantity_for_action(
        self,
        action: ReplayAction,
        *,
        actual_fill_price: float,
        current_position_qty: float = 0.0,
    ) -> float:
        """Ack-driven sizing from faithful-engine realized equity."""
        if action.full_close:
            qty = float(current_position_qty)
        else:
            if self._runtime_open is not None:
                raise ResearchReplayError("entry requested while runtime ledger is open")
            qty = self._runtime_equity_usd / float(actual_fill_price)
        if not (qty > 0):
            raise ResearchReplayError(f"non-positive runtime quantity for {action}")
        self._planned_quantities[(action.fill_ts, action.event_type)] = qty
        return qty

    def actions_at(self, ts: int) -> list[ReplayAction]:
        return list(self._by_ts.get(int(ts), ()))

    def record_result(
        self,
        action: ReplayAction,
        *,
        result: str,
        actual_quantity: float,
        actual_price: float,
    ) -> None:
        row = {
            "fill_ts": action.fill_ts,
            "event_type": action.event_type,
            "action": action.action,
            "reason": action.reason,
            "expected_price": action.fill_price,
            "actual_price": actual_price,
            "expected_quantity": self._planned_quantities.get(
                (action.fill_ts, action.event_type), action.quantity
            ),
            "actual_quantity": actual_quantity,
            "result": result,
        }
        if result != "SUCCESS":
            self.refused.append(row)
        else:
            self.executed.append(row)
            if action.full_close:
                if self._runtime_open is None:
                    raise ResearchReplayError("runtime close ack while ledger is flat")
                entry_px = self._runtime_open["price"]
                entry_qty = self._runtime_open["quantity"]
                if abs(actual_quantity - entry_qty) > max(1e-9, entry_qty * 1e-10):
                    raise ResearchReplayError(
                        f"runtime close quantity mismatch: open={entry_qty}, close={actual_quantity}"
                    )
                gross = (
                    (actual_price - entry_px) * entry_qty
                    if self.position_side == "LONG"
                    else (entry_px - actual_price) * entry_qty
                )
                fee = (
                    float(self.spec["commission_round_trip_pct"])
                    / 100.0
                    * entry_px
                    * entry_qty
                )
                self._runtime_equity_usd += gross - fee
                self._runtime_open = None
            else:
                if self._runtime_open is not None:
                    raise ResearchReplayError("runtime entry ack while ledger is open")
                self._runtime_open = {
                    "price": float(actual_price),
                    "quantity": float(actual_quantity),
                }

    def final_audit(self, actual_tim_rth_pct: float | None = None) -> dict[str, Any]:
        scheduled = len(self.actions)
        executed = len(self.executed)
        missing = sorted(
            set((a.fill_ts, a.event_type) for a in self.actions)
            - set((r["fill_ts"], r["event_type"]) for r in self.executed)
        )
        price_mismatch = [
            row
            for row in self.executed
            if abs(float(row["actual_price"]) - float(row["expected_price"])) > 1e-9
        ]
        quantity_mismatch = [
            row
            for row in self.executed
            if row["expected_quantity"] is not None
            and abs(float(row["actual_quantity"]) - float(row["expected_quantity"]))
            > max(1e-9, abs(float(row["expected_quantity"])) * 1e-10)
        ]
        tim = None
        if actual_tim_rth_pct is not None:
            expected_tim = float(self.spec["expected_tim_rth_pct"])
            tim = {
                "expected_rth_pct": expected_tim,
                "actual_rth_pct": float(actual_tim_rth_pct),
                "delta_pp": float(actual_tim_rth_pct) - expected_tim,
                "status": (
                    "PASS"
                    if abs(float(actual_tim_rth_pct) - expected_tim) <= 0.05
                    else "FAIL"
                ),
            }
        status = (
            "PASS"
            if (
                executed == scheduled
                and not self.refused
                and not missing
                and not price_mismatch
                and not quantity_mismatch
                and self._runtime_open is None
                and (tim is None or tim["status"] == "PASS")
            )
            else "FAIL"
        )
        return {
            "status": status,
            "spec": str(self.spec_path),
            "source_artifact": self.spec["source_artifact"],
            "symbol": self.symbol,
            "side": self.position_side,
            "scheduled": scheduled,
            "executed": executed,
            "refused": self.refused,
            "missing": missing,
            "price_mismatch": price_mismatch,
            "quantity_mismatch": quantity_mismatch,
            "runtime_flat_at_end": self._runtime_open is None,
            "tim": tim,
            "signal_parity": False,
            "promotion_allowed": False,
        }

    def tim_audit(self) -> dict[str, Any]:
        held_bars = 0
        entry_index: int | None = None
        for event in self.events:
            kind = str(event["type"]).upper()
            if kind in {"ENTRY", "REENTRY"}:
                entry_index = int(event["fill_index"])
            elif kind in {"EXIT", "MTM_FINAL"}:
                if entry_index is None:
                    raise ResearchReplayError("TIM audit close while flat")
                fill_index = int(event["fill_index"])
                held_bars += fill_index - entry_index + int(kind == "MTM_FINAL")
                entry_index = None
        if entry_index is not None:
            raise ResearchReplayError("TIM audit ended open")
        rth_rows = int(
            self.spec.get("expected_rth_rows")
            or max(int(event.get("fill_index", 0)) for event in self.events) + 1
        )
        actual = 100.0 * held_bars / rth_rows
        expected = float(self.spec["expected_tim_rth_pct"])
        delta_pp = actual - expected
        return {
            "status": "PASS" if abs(delta_pp) <= 0.05 else "FAIL",
            "held_bars": held_bars,
            "rth_rows": rth_rows,
            "expected_tim_rth_pct": expected,
            "actual_tim_rth_pct": actual,
            "delta_pp": delta_pp,
        }

    def accounting_audit(self, executed_trades: list[dict[str, Any]]) -> dict[str, Any]:
        """Compare faithful-engine closed PnL with the frozen vector equity."""
        closes = [
            row
            for row in executed_trades
            if str(row.get("reason", "")).startswith(REASON_PREFIX)
            and row.get("pnl_dollars") is not None
        ]
        total_pnl = sum(float(row.get("pnl_dollars", 0) or 0) for row in closes)
        actual_gain_pct = 100.0 * total_pnl / self.seed_notional_usd
        expected_gain_pct = float(self.spec["expected_gain_pct"])
        delta_bp = 100.0 * (actual_gain_pct - expected_gain_pct)
        expected_real = int((self.spec.get("expected_counts") or {}).get("exits", 0))
        expected_mtm = int((self.spec.get("expected_counts") or {}).get("mtm_final", 0))
        actual_real = sum(
            str(row.get("reason", "")).endswith("__E02_DONCHIAN") for row in closes
        )
        actual_mtm = sum(
            str(row.get("reason", "")).endswith("__END_OF_SAMPLE_MTM") for row in closes
        )
        status = (
            "PASS"
            if (
                abs(delta_bp) <= float(self.spec.get("accounting_tolerance_bp", 1.0))
                and actual_real == expected_real
                and actual_mtm == expected_mtm
            )
            else "FAIL"
        )
        return {
            "status": status,
            "expected_gain_pct": expected_gain_pct,
            "actual_gain_pct": actual_gain_pct,
            "delta_bp": delta_bp,
            "total_pnl_dollars": total_pnl,
            "seed_notional_usd": self.seed_notional_usd,
            "expected_real_closes": expected_real,
            "actual_real_closes": actual_real,
            "expected_mtm_closes": expected_mtm,
            "actual_mtm_closes": actual_mtm,
            "promotion_allowed": False,
        }


def build_spec_from_artifact(
    artifact_dir: str | Path,
    *,
    account: str = "trb",
    seed_notional_usd: float = 2000.0,
) -> dict[str, Any]:
    """Build (but do not write) a strict replay spec from a campaign artifact."""
    artifact = Path(artifact_dir).resolve()
    manifest = json.loads((artifact / "run_manifest.json").read_text())
    queue = json.loads((artifact / "faithful_engine_replay_queue.json").read_text())
    candidate = queue["candidate"]
    if not candidate.get("policy_compliant"):
        raise ResearchReplayError("artifact queue candidate is not policy compliant")
    return {
        "kind": SPEC_KIND,
        "version": SPEC_VERSION,
        "promotion_allowed": False,
        "source_artifact": str(artifact),
        "symbol": manifest["symbol"],
        "side": manifest["side"],
        "account": account,
        "event_schedule": str(artifact / "top_candidate_events.jsonl.gz"),
        "npz_path": manifest["npz_path"],
        "expected_npz_sha256": manifest["npz_sha256"],
        "expected_schedule_sha256": sha256_file(
            artifact / "top_candidate_events.jsonl.gz"
        ),
        "seed_notional_usd": seed_notional_usd,
        "commission_round_trip_pct": (
            2.0
            * float(
                manifest.get(
                    "commission_bps_one_way",
                    manifest.get("cost_bps_one_way", 0.0),
                )
            )
            / 100.0
        ),
        "expected_gain_pct": candidate["gain_pct"],
        "expected_tim_rth_pct": candidate["tim_rth_pct"],
        "expected_rth_rows": manifest["rth_rows"],
        "slippage_bps_one_way": manifest["slippage_bps_one_way"],
        "accounting_tolerance_bp": 1.0,
        "slippage_is_embedded_in_fill_price": True,
        "candidate": {
            "strategy": candidate["strategy"],
            "exit_params": candidate["exit_params"],
            "reentry": candidate["reentry"],
        },
        "expected_counts": {
            "entries": 1,
            "exits": candidate["technical_exits"],
            "reentries": candidate["reentries"],
            "mtm_final": int(candidate["round_trips"] > candidate["technical_exits"]),
            "e10_reclaims": candidate["reclaim_reentries"],
            "e11_lower": candidate["lower_reentries"],
        },
    }
