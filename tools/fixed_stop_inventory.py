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
import re
import sys
from pathlib import Path


EPS = 1e-10
_CANONICAL_SWITCH_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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


def _explicit_entry_evidence(order, fill, venue):
    """Accept an explicit switch field or map a recorded reason through the verified static registry."""
    for source_name, source in (("order", order or {}), ("fill", fill)):
        for key in ("entrySwitch", "entry_switch"):
            value = source.get(key)
            if value not in (None, ""):
                switch = str(value)
                if switch not in {"NONE", "MODE", "VECTOR_DIRECT"} and not switch.startswith("UNATTRIBUTED:") and _CANONICAL_SWITCH_RE.fullmatch(switch):
                    return {"value": switch, "switch": switch, "source": source_name + "." + key, "confidence": "explicit_switch_field"}
        for key in ("decisionReason", "decision_reason", "reason"):
            value = source.get(key)
            if value in (None, ""):
                continue
            from vec_decisions import history_prov
            mapping = history_prov._resolve_spec(str(value), venue) or {}
            switch = str(mapping.get("switch") or "")
            if switch and switch not in {"NONE", "MODE", "VECTOR_DIRECT"} and not switch.startswith("UNATTRIBUTED:") and _CANONICAL_SWITCH_RE.fullmatch(switch):
                return {"value": switch, "switch": switch, "recorded_reason": value,
                        "mapping_source": "vec_decisions.history_prov._resolve_spec", "mapping_evidence": mapping.get("ev"),
                        "source": source_name + "." + key, "confidence": "mapped_recorded_entry_reason"}
    return None


def _row_order_refs(row):
    refs = []
    if row.get("order_id") not in (None, ""):
        refs.append((str(row["order_id"]), "order_id"))
    if row.get("echo_of_order_id") not in (None, ""):
        refs.append((str(row["echo_of_order_id"]), "echo_of_order_id"))
    for order_id in row.get("order_ids") or []:
        if order_id not in (None, ""):
            refs.append((str(order_id), "order_ids"))
    return refs


def _load_provenance_records(account_id, symbol_sides, history_root, decisions_root):
    """Load exact order-ID-linked records; archives are included for history ledgers."""
    result = collections.defaultdict(list)
    sources = (("history", Path(history_root) / str(account_id)), ("decisions", Path(decisions_root) / str(account_id)))
    for symbol, side in sorted(symbol_sides):
        ledger_name = symbol + "_" + side + ".jsonl"
        for source_name, account_dir in sources:
            live_path = account_dir / ledger_name
            if not live_path.exists():
                continue
            try:
                if source_name == "history":
                    from vec_decisions import history_archive
                    rows = history_archive.iter_rows(live_path, include_offsite=False)
                else:
                    rows = (json.loads(line) for line in live_path.read_text(errors="replace").splitlines() if line.strip())
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    prov = row.get("prov") if isinstance(row.get("prov"), dict) else {}
                    for order_id, relationship in _row_order_refs(row):
                        result[order_id].append({
                            "source": source_name, "source_path": str(live_path), "order_id_relationship": relationship,
                            "recorded_type": row.get("type"), "recorded_ts": row.get("ts") or row.get("timestamp"),
                            "recorded_reason": row.get("reason"), "recorded_provenance": prov,
                            "ledger_key": row.get("ledger_key"), "client_order_id": row.get("client_order_id"),
                            "decision_provenance": row.get("decision_provenance"), "venue": row.get("venue"),
                        })
            except (OSError, ValueError, TypeError):
                continue
    return result


def _verified_recorded_switch(records):
    """Accept an exact order-linked VEC_EXACT row or a content-address-verified consumed-state record."""
    for record in records:
        verified_state = record.get("verified_decision_state") or {}
        if (record.get("source") == "history" and record.get("order_id_relationship") == "order_id"
                and str(record.get("recorded_type") or "").upper() in {"OPEN", "REENTRY"}
                and verified_state.get("entry_switch")):
            return {"switch": verified_state["entry_switch"], "source": record.get("source"),
                    "order_id_relationship": record.get("order_id_relationship"), "recorded_ts": record.get("recorded_ts"),
                    "source_path": record.get("source_path"), "recorded_reason": record.get("recorded_reason"),
                    "verified_decision_state": verified_state, "confidence": "order_id_linked_content_address_verified_decision_state"}
        prov = record.get("recorded_provenance") or {}
        switch = str(prov.get("switch") or "")
        event_type = str(record.get("recorded_type") or "").upper()
        if (record.get("order_id_relationship") == "order_id" and prov.get("src") == "VEC_EXACT" and event_type in {"OPEN", "REENTRY"}
                and switch not in {"", "NONE", "VECTOR_DIRECT"}
                and switch != "MODE" and not switch.startswith("UNATTRIBUTED:") and _CANONICAL_SWITCH_RE.fullmatch(switch)):
            return {"switch": switch, "source": record.get("source"), "order_id_relationship": record.get("order_id_relationship"),
                    "recorded_ts": record.get("recorded_ts"), "source_path": record.get("source_path"),
                    "reason": record.get("recorded_reason"), "provenance": prov}
    return None


def _load_verified_decision_state_records(provenance_by_order, decision_state_root, venue):
    root = Path(decision_state_root)
    root_resolved = root.resolve()
    for records in provenance_by_order.values():
        for record in records:
            proof = record.get("decision_provenance")
            if not isinstance(proof, dict) or not proof.get("state_sha256") or not proof.get("state_file"):
                continue
            try:
                path = (root / str(proof["state_file"])).resolve()
                if not path.is_relative_to(root_resolved) or not path.is_file():
                    continue
                state = json.loads(path.read_text())
                canonical = json.dumps(state, sort_keys=True, separators=(",", ":"), default=str).encode()
                digest = hashlib.sha256(canonical).hexdigest()
                if digest != str(proof["state_sha256"]):
                    continue
                fields_match = all(proof.get(key) in (None, state.get(state_key)) for key, state_key in (
                    ("settings_sha256", "settings_sha256"), ("engine_ast_sha256", "engine_ast_sha256"),
                    ("npz_tail", "npz_tail"), ("state_ts", "ts"), ("settings_updated_at", "settings_updated_at")))
                if not fields_match:
                    continue
                ev_type = str(record.get("recorded_type") or "").upper()
                event_rows = list(state.get("events_this_bar") or []) + list(state.get("acts_last_bar") or [])
                reasons = {str(event.get("reason")) for event in event_rows
                           if str(event.get("type") or "").upper() in {"OPEN", "REENTRY"}
                           and event.get("reason") not in (None, "")}
                target = state.get("target_state") if isinstance(state.get("target_state"), dict) else {}
                state_bar = _num(state.get("bar_ts"))
                target_entry_ts = _num(target.get("entry_ts"))
                if target.get("entry_reason") and target_entry_ts is not None and state_bar is not None and abs(target_entry_ts - state_bar) <= 1.0:
                    reasons.add(str(target["entry_reason"]))
                if len(reasons) != 1 or ev_type not in {"OPEN", "REENTRY"}:
                    continue
                reason = next(iter(reasons))
                from vec_decisions import history_prov
                mapping = history_prov._resolve_spec(reason, venue) or {}
                switch = str(mapping.get("switch") or "")
                if not switch or switch in {"NONE", "MODE", "VECTOR_DIRECT"} or switch.startswith("UNATTRIBUTED:") or not _CANONICAL_SWITCH_RE.fullmatch(switch):
                    continue
                record["verified_decision_state"] = {
                    "verified": True, "state_sha256": digest, "state_file": str(proof["state_file"]),
                    "state_path": str(path), "entry_reason": reason, "entry_switch": switch,
                    "mapping_source": "vec_decisions.history_prov._resolve_spec", "mapping_evidence": mapping.get("ev"),
                    "settings_sha256": state.get("settings_sha256"), "engine_ast_sha256": state.get("engine_ast_sha256"),
                    "npz_tail": state.get("npz_tail"), "state_ts": state.get("ts"),
                    "state_content_address_verified": True, "decision_provenance_fields_match_state": True,
                }
            except (OSError, ValueError, TypeError, KeyError):
                continue


def _ledger_anchor(record, account_id, symbol, side):
    parts = str(record.get("ledger_key") or "").split("|")
    if len(parts) != 4 or parts[0] != str(account_id) or parts[1] != symbol + "_" + side:
        return None
    try:
        bar_ts = int(float(parts[2]))
    except (TypeError, ValueError):
        return None
    action = parts[3].upper()
    if action not in {"OPEN", "REENTRY"}:
        return None
    return symbol + "_" + side, bar_ts, action


def _load_forward_anchor_records(required_anchors, intents_root, intents_archive_root, forward_state_root):
    from datetime import datetime, timezone

    result = {}
    intent_roots = (("bar_intent_archive", Path(intents_archive_root)), ("bar_intent_current", Path(intents_root)))
    for sym_side, bar_ts, action in required_anchors:
        dt = datetime.fromtimestamp(float(bar_ts), timezone.utc)
        stem = dt.strftime("%Y%m%dT%H%M") + "_" + sym_side + ".json"
        intents = []
        for source, root in intent_roots:
            for path in root.glob(stem + "*"):
                try:
                    data = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                if data.get("ss") != sym_side or _num(data.get("bar_ts")) is None or int(float(data["bar_ts"])) != bar_ts:
                    continue
                target = data.get("target_state") if isinstance(data.get("target_state"), dict) else {}
                intents.append({"source": source, "source_path": str(path), "sha256": sha256_file(path),
                                "bar_ts": bar_ts, "action": action, "entry_reason": target.get("entry_reason"),
                                "entry_ts": target.get("entry_ts"), "snapshot_mode": data.get("mode"),
                                "intent_actions": data.get("intents") or []})
        result[(sym_side, bar_ts, action)] = {"intents": intents, "forward_states": []}

    required_by_sym = collections.defaultdict(set)
    for sym_side, bar_ts, action in required_anchors:
        required_by_sym[sym_side].add(bar_ts)
    root = Path(forward_state_root)
    if root.exists():
        for run_dir in root.iterdir():
            if not run_dir.is_dir():
                continue
            for sym_side, bars in required_by_sym.items():
                path = run_dir / (sym_side + ".json")
                if not path.is_file():
                    continue
                try:
                    data = json.loads(path.read_text())
                except (OSError, ValueError):
                    continue
                acts_bar = _num(data.get("acts_bar_ts"))
                if acts_bar is None or int(acts_bar) not in bars:
                    continue
                bar_ts = int(acts_bar)
                file_hash = sha256_file(path)
                for action in ("OPEN", "REENTRY"):
                    bucket = result.get((sym_side, bar_ts, action))
                    if bucket is None:
                        continue
                    for event in data.get("acts_last_bar") or []:
                        if str(event.get("type") or "").upper() != action:
                            continue
                        bucket["forward_states"].append({
                            "source": "forward_state_expired", "source_path": str(path), "sha256": file_hash,
                            "snapshot_ts": data.get("ts"), "acts_bar_ts": bar_ts, "action": action,
                            "entry_reason": event.get("reason"), "settings_sha256": data.get("settings_sha256"),
                            "engine_ast_sha256": data.get("engine_ast_sha256"), "input_sha256": data.get("input_sha256"),
                            "template_md5": data.get("template_md5"), "settings_updated_at": data.get("settings_updated_at"),
                        })
    return result


def _forward_switch_candidate(records, forward_anchor_records, account_id, symbol, side, venue):
    from vec_decisions import history_prov

    candidates = []
    for record in records:
        if record.get("source") != "history" or record.get("order_id_relationship") != "order_id":
            continue
        anchor = _ledger_anchor(record, account_id, symbol, side)
        if not anchor:
            continue
        evidence = (forward_anchor_records or {}).get(anchor, {})
        intents = evidence.get("intents") or []
        states = evidence.get("forward_states") or []
        intent_reasons = {str(item.get("entry_reason")) for item in intents if item.get("entry_reason") not in (None, "")}
        intent_times = {_num(item.get("entry_ts")) for item in intents if _num(item.get("entry_ts")) is not None}
        conflict = len(intent_reasons) > 1 or len(intent_times) > 1
        reason = next(iter(intent_reasons)) if len(intent_reasons) == 1 else None
        matching_states = [state for state in states if reason and state.get("entry_reason") == reason]
        if not reason and not intents:
            state_reasons = {str(state.get("entry_reason")) for state in states if state.get("entry_reason") not in (None, "")}
            reason = next(iter(state_reasons)) if len(state_reasons) == 1 else None
        if conflict:
            status = "conflicting_bar_intent_versions"
        elif reason is None:
            status = "bar_anchor_found_without_entry_reason" if intents or states else "no_exact_bar_decision_snapshot"
        else:
            mapping = history_prov._resolve_spec(reason, venue) or {}
            switch = str(mapping.get("switch") or "")
            switch_is_mapped = bool(switch and switch not in {"NONE", "MODE", "VECTOR_DIRECT"} and not switch.startswith("UNATTRIBUTED:") and _CANONICAL_SWITCH_RE.fullmatch(switch))
            exact_entry_time = any(abs(value - anchor[1]) <= 1.0 for value in intent_times)
            archived_intents = [item for item in intents if item.get("source") == "bar_intent_archive"]
            if not switch_is_mapped:
                status = "reason_not_in_verified_switch_mapping"
            elif matching_states and any(state.get("settings_sha256") and state.get("engine_ast_sha256") for state in matching_states):
                status = "hash_bound_forward_action_candidate"
            elif archived_intents and exact_entry_time:
                status = "archived_bar_intent_reason_candidate_missing_forward_hash_record"
            elif intents and exact_entry_time:
                status = "current_bar_intent_reason_candidate_unarchived"
            else:
                status = "intent_entry_time_or_generation_unverified"
            candidates.append({"switch": switch if switch_is_mapped else None, "status": status,
                               "confidence": "candidate_unverified_order_generation" if switch_is_mapped else "unattributed",
                               "reason": reason, "mapping_source": "vec_decisions.history_prov._resolve_spec",
                               "mapping_evidence": mapping.get("ev"), "position_key": anchor[0], "bar_ts": anchor[1],
                               "action": anchor[2], "entry_ts_matches_bar": exact_entry_time,
                               "intent_artifacts": intents, "matching_forward_state_records": matching_states,
                               "available_forward_state_records": states,
                               "execution_generation_hash_join": "unavailable_in_broker_history_and_order_record"})
    mapped = [candidate for candidate in candidates if candidate.get("switch")]
    if mapped:
        mapped.sort(key=lambda candidate: (candidate["status"] != "hash_bound_forward_action_candidate", candidate["status"]))
        return mapped[0]
    return candidates[0] if candidates else None


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


def reconstruct_account(account_id, account, window_start_ms=None, window_end_ms=None, provenance_records=None, forward_anchor_records=None, venue="crypto"):
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
                "entry_switch_candidate": None, "entry_switch_candidate_status": None,
                "entry_switch_candidate_evidence": None, "entry_reason_candidate": None,
                "entry_reason_candidate_status": None,
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
            order_matches = list((provenance_records or {}).get(str(order_id), []))
            realized = _num(fill.get("realizedPnl"), 0.0)
            fee = _num(fill.get("commission"), 0.0)
            fee_asset = fill.get("commissionAsset")
            asset = fill.get("marginAsset")
            row = conservation[(symbol, side)]
            row["raw_realized_pnl"] += realized
            row["fill_count"] += 1

            forward_candidate = None
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
                evidence = _explicit_entry_evidence(order, fill, venue) if event_type == "open" else None
                if event_type == "open" and evidence is None:
                    evidence = _verified_recorded_switch(order_matches)
                if event_type == "open" and evidence is None:
                    forward_candidate = _forward_switch_candidate(order_matches, forward_anchor_records, account_id, symbol, side, venue)
                if evidence:
                    cur["entry_decision_evidence"] = evidence
                    cur["entry_switch_attribution"] = evidence.get("value", evidence.get("switch", "unknown"))
                if forward_candidate:
                    cur["entry_switch_candidate"] = forward_candidate.get("switch")
                    cur["entry_switch_candidate_status"] = forward_candidate.get("status")
                    cur["entry_switch_candidate_evidence"] = forward_candidate
                    cur["entry_reason_candidate"] = forward_candidate.get("reason")
                    cur["entry_reason_candidate_status"] = forward_candidate.get("status")
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
                "entry_reason_candidate": (cur.get("entry_reason_candidate") if opening else None),
                "entry_reason_candidate_status": (cur.get("entry_reason_candidate_status") if opening else None),
                "order_id_linked_history_records": order_matches,
                "forward_anchor_switch_candidate": forward_candidate,
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
        entry_events = [event for life in members for event in life["events"] if event.get("event_type") == "open"]
        entry_sources = collections.Counter(record["source"] for event in entry_events for record in event.get("order_id_linked_history_records", []))
        entry_provenance = collections.Counter(
            str((record.get("recorded_provenance") or {}).get("src") or "unknown") + "|" +
            str((record.get("recorded_provenance") or {}).get("switch") or "unknown")
            for event in entry_events for record in event.get("order_id_linked_history_records", []))
        candidate_summary = {}
        for life in members:
            switch = life.get("entry_switch_candidate")
            if not switch:
                continue
            item = candidate_summary.setdefault(switch, {"candidate_lifecycle_count": 0, "completed_count": 0, "completed_loss_count": 0,
                                                           "fixed_stop_closed_loss_count": 0, "candidate_status_counts": {}})
            item["candidate_lifecycle_count"] += 1
            status = str(life.get("entry_switch_candidate_status") or "unknown")
            item["candidate_status_counts"][status] = item["candidate_status_counts"].get(status, 0) + 1
            pnl = sum(_num(event.get("realized_pnl_raw"), 0.0) for event in life["events"])
            stop_close = any(event.get("fixed_stop_order_detected") and event.get("event_type") in {"reduce", "close"} for event in life["events"])
            if life.get("status") == "completed":
                item["completed_count"] += 1
                if pnl < -EPS:
                    item["completed_loss_count"] += 1
                    item["fixed_stop_closed_loss_count"] += stop_close
        result = {"raw_realized_pnl": row["raw_realized_pnl"], "fill_count": row["fill_count"], "lifecycle_realized_pnl": row["lifecycle_realized_pnl"],
                  "realized_pnl_conservation_delta": row["lifecycle_realized_pnl"] - row["raw_realized_pnl"], "lifecycle_count": len(members),
                  "completed_count": sum(life["status"] == "completed" for life in members), "open_count": sum(life["status"] == "open" for life in members),
                  "incomplete_count": sum(life["status"] == "incomplete" for life in members),
                  "wins_by_raw_realized_pnl": 0, "losses_by_raw_realized_pnl": 0, "flat_by_raw_realized_pnl": 0,
                  "fixed_stop_exit_fill_count": 0, "fixed_stop_exit_fill_loss_count": 0,
                  "fixed_stop_exit_fill_realized_pnl_raw_by_asset": {}, "commission_by_asset": {},
                  "realized_pnl_raw_by_asset": {}, "entry_fill_events": len(entry_events),
                  "entry_fill_events_with_order_id_history_match": sum(bool(event.get("order_id_linked_history_records")) for event in entry_events),
                  "entry_fill_events_with_verified_switch": sum(event.get("entry_switch_attribution") != "unknown" for event in entry_events),
                  "entry_fill_events_with_unverified_switch_candidate": sum(bool((event.get("forward_anchor_switch_candidate") or {}).get("switch")) for event in entry_events),
                  "entry_order_provenance_sources": dict(entry_sources), "entry_recorded_provenance_source_switch": dict(entry_provenance)}
        result["entry_switch_candidate_lifecycles"] = candidate_summary
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

    entry_fill_events = [event for life in lifecycles for event in life["events"] if event.get("event_type") == "open"]
    linked_entry_events = [event for event in entry_fill_events if event.get("order_id_linked_history_records")]
    attributed_entry_events = [event for event in entry_fill_events if event.get("entry_switch_attribution") != "unknown"]
    source_counts = collections.Counter(record["source"] for event in entry_fill_events for record in event.get("order_id_linked_history_records", []))
    source_switch_counts = collections.Counter(
        str((record.get("recorded_provenance") or {}).get("src") or "unknown") + "|" +
        str((record.get("recorded_provenance") or {}).get("switch") or "unknown")
        for event in entry_fill_events for record in event.get("order_id_linked_history_records", []))
    candidate_events = [event for event in entry_fill_events if (event.get("forward_anchor_switch_candidate") or {}).get("switch")]
    candidate_status_counts = collections.Counter((event.get("forward_anchor_switch_candidate") or {}).get("status") for event in entry_fill_events
                                                    if event.get("forward_anchor_switch_candidate"))
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
        "entry_provenance_coverage": {"entry_fill_events": len(entry_fill_events), "entry_fill_events_with_order_id_history_match": len(linked_entry_events),
                                      "entry_fill_events_with_verified_switch": len(attributed_entry_events),
                                      "entry_fill_events_with_unverified_switch_candidate": len(candidate_events),
                                      "entry_fill_events_without_verified_switch": len(entry_fill_events) - len(attributed_entry_events),
                                      "order_linked_record_sources": dict(source_counts),
                                      "order_linked_recorded_provenance_source_switch": dict(source_switch_counts),
                                      "forward_anchor_candidate_status_counts": dict(candidate_status_counts),
                                      "unknown_reason": "No exact order-ID-linked VEC_EXACT record or content-address-verified consumed-state decision row provided a canonical switch; generic VECTOR_DIRECT and stale/unlinked contexts are not promoted."},
        "per_symbol": grouped,
        "lifecycles": lifecycles,
        "malformed_or_unreconciled_records": malformed_fills,
        "account_commission_by_asset": dict(_sum_commissions(lifecycles)),
        "notes": ["No commission conversion was performed.", "Verified entry attribution requires an exact broker-order-ID link to stored VEC_EXACT provenance or a content-address-verified consumed decision state; timestamped bar candidates remain unverified.",
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


def build_inventory(document, source_path="<memory>", source_sha256=None, history_root="data/history", decisions_root="data/decisions",
                    intents_root="data/vec_live/intents", intents_archive_root="data/vec_live/intents_archive",
                    forward_state_root="data/forward_state_expired", decision_state_root="data/vector_direct"):
    start = document.get("start_ms")
    end = document.get("end_ms")
    accounts = document.get("accounts") or {}
    result = {}
    provenance_by_account = {}
    anchors = set()
    for account_id, account in sorted(accounts.items()):
        symbol_sides = {(str(symbol), str(fill.get("positionSide") or "").upper()) for symbol, rows in (account.get("trades") or {}).items()
                        for fill in (rows or []) if str(fill.get("positionSide") or "").upper() in {"LONG", "SHORT"}}
        provenance = _load_provenance_records(account_id, symbol_sides, history_root, decisions_root)
        _load_verified_decision_state_records(provenance, decision_state_root, "stocks" if str(account_id).lower() in {"tra", "trb", "trc"} else "crypto")
        provenance_by_account[str(account_id)] = provenance
        for records in provenance.values():
            for record in records:
                if record.get("source") != "history" or record.get("order_id_relationship") != "order_id":
                    continue
                parts = str(record.get("ledger_key") or "").split("|")
                if len(parts) == 4 and parts[0] == str(account_id):
                    try:
                        action = parts[3].upper()
                        if action in {"OPEN", "REENTRY"}:
                            anchors.add((parts[1], int(float(parts[2])), action))
                    except (TypeError, ValueError):
                        continue
    forward_anchor_records = _load_forward_anchor_records(anchors, intents_root, intents_archive_root, forward_state_root)
    for account_id, account in sorted(accounts.items()):
        venue = "stocks" if str(account_id).lower() in {"tra", "trb", "trc"} else "crypto"
        inventory = reconstruct_account(account_id, account, start, end, provenance_by_account[str(account_id)], forward_anchor_records, venue)
        inventory["source"] = {"path": str(source_path), "sha256": source_sha256,
                               "generated_ms": document.get("generated_ms"), "window_start_ms": start, "window_end_ms": end}
        result[str(account_id)] = inventory
    return result


def build_neural_builder_handoff(inventories, source_path="<memory>"):
    per_account = {}
    cross_symbol = {}
    total_events = total_matched = total_attributed = 0
    total_candidates = 0
    candidate_losses = collections.defaultdict(lambda: {"completed_loss_lifecycle_count": 0, "completed_candidate_lifecycle_count": 0,
                                                        "fixed_stop_closed_loss_lifecycle_count": 0,
                                                        "candidate_status_counts": {}, "entry_reasons": {}})
    reason_candidate_losses = collections.defaultdict(lambda: {"completed_loss_lifecycle_count": 0,
                                                               "fixed_stop_closed_loss_lifecycle_count": 0,
                                                               "candidate_status_counts": {}, "mapped_switches": {}})
    proven_losses = collections.defaultdict(lambda: {"completed_loss_lifecycle_count": 0, "fixed_stop_closed_loss_lifecycle_count": 0})
    unknown_losses = collections.Counter()
    rejected_evidence = collections.defaultdict(lambda: {"completed_loss_lifecycle_count": 0, "entry_reasons": {}, "candidate_switches": {}})
    rankable_candidate_statuses = {"hash_bound_forward_action_candidate", "archived_bar_intent_reason_candidate_missing_forward_hash_record",
                                   "current_bar_intent_reason_candidate_unarchived", "reason_not_in_verified_switch_mapping"}
    for account_id, inventory in sorted(inventories.items()):
        coverage = inventory.get("entry_provenance_coverage") or {}
        total_events += coverage.get("entry_fill_events", 0)
        total_matched += coverage.get("entry_fill_events_with_order_id_history_match", 0)
        total_attributed += coverage.get("entry_fill_events_with_verified_switch", 0)
        total_candidates += coverage.get("entry_fill_events_with_unverified_switch_candidate", 0)
        per_account[account_id] = coverage
        for symbol, summary in inventory.get("per_symbol", {}).items():
            for side, side_summary in summary.get("position_sides", {}).items():
                cross_symbol[account_id + ":" + symbol + "_" + side] = {
                    "entry_fill_events": side_summary.get("entry_fill_events", 0),
                    "entry_fill_events_with_order_id_history_match": side_summary.get("entry_fill_events_with_order_id_history_match", 0),
                    "entry_fill_events_with_verified_switch": side_summary.get("entry_fill_events_with_verified_switch", 0),
                    "entry_fill_events_with_unverified_switch_candidate": side_summary.get("entry_fill_events_with_unverified_switch_candidate", 0),
                    "entry_order_provenance_sources": side_summary.get("entry_order_provenance_sources", {}),
                    "entry_recorded_provenance_source_switch": side_summary.get("entry_recorded_provenance_source_switch", {}),
                    "entry_switch_candidate_lifecycles": side_summary.get("entry_switch_candidate_lifecycles", {}),
                    "lifecycle_counts": {"total": side_summary.get("lifecycle_count", 0), "completed": side_summary.get("completed_count", 0),
                                         "open": side_summary.get("open_count", 0), "incomplete": side_summary.get("incomplete_count", 0)},
                }
        for life in inventory.get("lifecycles", []):
            if life.get("status") != "completed":
                continue
            pnl = sum(_num(event.get("realized_pnl_raw"), 0.0) for event in life.get("events", []))
            if pnl >= -EPS:
                continue
            symbol_side = life["symbol"] + "_" + life["position_side"]
            stop_loss = any(event.get("fixed_stop_order_detected") and event.get("event_type") in {"reduce", "close"} for event in life.get("events", []))
            proven_switch = life.get("entry_switch_attribution")
            candidate_switch = life.get("entry_switch_candidate")
            if proven_switch and proven_switch != "unknown":
                key = (str(account_id), symbol_side, str(proven_switch))
                proven_losses[key]["completed_loss_lifecycle_count"] += 1
                proven_losses[key]["fixed_stop_closed_loss_lifecycle_count"] += stop_loss
            elif candidate_switch and life.get("entry_switch_candidate_status") in rankable_candidate_statuses:
                key = (str(account_id), symbol_side, str(candidate_switch))
                row = candidate_losses[key]
                row["completed_loss_lifecycle_count"] += 1
                row["completed_candidate_lifecycle_count"] += 1
                row["fixed_stop_closed_loss_lifecycle_count"] += stop_loss
                status = str(life.get("entry_switch_candidate_status") or "unknown")
                row["candidate_status_counts"][status] = row["candidate_status_counts"].get(status, 0) + 1
                reason = str((life.get("entry_switch_candidate_evidence") or {}).get("reason") or "unknown")
                row["entry_reasons"][reason] = row["entry_reasons"].get(reason, 0) + 1
            elif life.get("entry_reason_candidate_status") == "intent_entry_time_or_generation_unverified":
                evidence = life.get("entry_switch_candidate_evidence") or {}
                reason = str(life.get("entry_reason_candidate") or "unknown")
                switch = str(evidence.get("switch") or "unknown")
                row = rejected_evidence[(str(account_id), symbol_side, "intent_entry_time_or_generation_unverified")]
                row["completed_loss_lifecycle_count"] += 1
                row["entry_reasons"][reason] = row["entry_reasons"].get(reason, 0) + 1
                row["candidate_switches"][switch] = row["candidate_switches"].get(switch, 0) + 1
            else:
                unknown_losses[(str(account_id), symbol_side)] += 1
            reason = life.get("entry_reason_candidate")
            reason_status = life.get("entry_reason_candidate_status")
            if reason and reason_status in rankable_candidate_statuses:
                key = (str(account_id), symbol_side, str(reason))
                row = reason_candidate_losses[key]
                row["completed_loss_lifecycle_count"] += 1
                row["fixed_stop_closed_loss_lifecycle_count"] += stop_loss
                row["candidate_status_counts"][str(reason_status)] = row["candidate_status_counts"].get(str(reason_status), 0) + 1
                if candidate_switch:
                    row["mapped_switches"][str(candidate_switch)] = row["mapped_switches"].get(str(candidate_switch), 0) + 1

    candidate_rank = [{"account_id": key[0], "sym_side": key[1], "entry_switch_candidate": key[2], **counts}
                      for key, counts in candidate_losses.items()]
    candidate_rank.sort(key=lambda item: (-item["completed_loss_lifecycle_count"], item["account_id"], item["sym_side"], item["entry_switch_candidate"]))
    reason_candidate_rank = [{"account_id": key[0], "sym_side": key[1], "entry_reason_candidate": key[2], **counts}
                             for key, counts in reason_candidate_losses.items()]
    reason_candidate_rank.sort(key=lambda item: (-item["completed_loss_lifecycle_count"], item["account_id"], item["sym_side"], item["entry_reason_candidate"]))
    proven_rank = [{"account_id": key[0], "sym_side": key[1], "entry_switch": key[2], **counts} for key, counts in proven_losses.items()]
    proven_rank.sort(key=lambda item: (-item["completed_loss_lifecycle_count"], item["account_id"], item["sym_side"], item["entry_switch"]))
    rejected_rows = [{"account_id": key[0], "sym_side": key[1], "reason": key[2], **counts}
                     for key, counts in rejected_evidence.items()]
    rejected_rows.sort(key=lambda item: (-item["completed_loss_lifecycle_count"], item["account_id"], item["sym_side"], item["reason"]))
    return {
        "schema_version": 1, "artifact_type": "neural_builder_position_provenance_handoff",
        "source_path": str(source_path), "account_count": len(inventories),
        "entry_provenance_policy": {
            "proven_switch_attribution": "An exact order-ID-linked recorded VEC_EXACT OPEN/REENTRY event with a canonical switch, or an exact order-ID-linked history row whose decision_provenance state file verifies against its content address and whose consumed OPEN/REENTRY reason maps through the verified static switch registry.",
            "unverified_candidate": "Exact broker order_id history row -> exact ledger_key account/sym_side/bar -> exact bar-named intent and/or forward acts_bar_ts; the entry reason maps through history_prov._resolve_spec. Candidates remain unverified because the broker execution row has no matching execution-generation/settings hash.",
            "rejected": ["VECTOR_DIRECT is a transport/source label, not the entry switch.",
                         "Generic reason text is not promoted to a canonical switch without a verified reason-to-switch mapping.",
                         "Unlinked or stale position/decision context is not used.",
                         "Current configuration is never consulted for historical attribution."],
        },
        "coverage": {"entry_fill_events": total_events, "entry_fill_events_with_order_id_history_match": total_matched,
                     "entry_fill_events_with_verified_switch": total_attributed,
                     "entry_fill_events_with_unverified_switch_candidate": total_candidates,
                     "entry_fill_events_without_verified_switch": total_events - total_attributed},
        "per_account": per_account, "cross_symbol": cross_symbol,
        "losing_entry_switch_rankings": {"proven": proven_rank, "exact_anchor_candidates_unverified": candidate_rank,
                                         "exact_anchor_entry_reasons_unverified": reason_candidate_rank,
                                         "completed_loss_lifecycles_without_switch": [{"account_id": key[0], "sym_side": key[1], "completed_loss_lifecycle_count": value}
                                                                                       for key, value in sorted(unknown_losses.items())]},
        "rejected_evidence": {"excluded_from_candidate_rankings": rejected_rows,
                              "policy": "Entry-time/generation mismatches and conflicting bar intent versions are retained as rejected evidence, not attributed or ranked as switch/reason candidates."},
        "fixed_stop_loss_vs_gain_stop_opportunities": {"fixed_stop_closed_loss_lifecycles_are_actual_broker_stop_fills": True,
                                                       "gain_stop_candidate_opportunities": "not included in this broker-fill inventory; evaluate separately from exact forward exit-candidate evidence, and never count as closed losses"},
        "unknowns": ["Broker fills expose the executed order ID and fill facts, not the originating vector entry switch.",
                     "A direct order history row may say VECTOR_DIRECT or a broker/position reconciliation reason; those values do not identify the underlying entry switch.",
                     "Exact bar reason and hash-bound forward state provide a useful candidate, but not proof that the live execution used that exact settings/engine generation.",
                     "A switch remains unknown unless the actual order ID joins to recorded VEC_EXACT entry provenance or a content-address-verified consumed decision state with a canonical switch."],
    }


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
    parser.add_argument("--history-root", default=str(ROOT / "data/history"))
    parser.add_argument("--decisions-root", default=str(ROOT / "data/decisions"))
    parser.add_argument("--intents-root", default=str(ROOT / "data/vec_live/intents"))
    parser.add_argument("--intents-archive-root", default=str(ROOT / "data/vec_live/intents_archive"))
    parser.add_argument("--forward-state-root", default=str(ROOT / "data/forward_state_expired"))
    parser.add_argument("--decision-state-root", default=str(ROOT / "data/vector_direct"))
    args = parser.parse_args()
    input_path = Path(args.input)
    document = json.loads(input_path.read_text())
    inventories = build_inventory(document, str(input_path), sha256_file(input_path), args.history_root, args.decisions_root,
                                  args.intents_root, args.intents_archive_root, args.forward_state_root, args.decision_state_root)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for account_id, inventory in inventories.items():
        (out_dir / (account_id + ".json")).write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n")
    manifest = {"schema_version": 1, "source": {"path": str(input_path), "sha256": sha256_file(input_path)},
                "accounts": {acct: {"file": acct + ".json", "lifecycle_counts": inv["lifecycle_counts"],
                                    "symbol_count": len(inv["per_symbol"])} for acct, inv in inventories.items()}}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    handoff = build_neural_builder_handoff(inventories, str(input_path))
    (out_dir / "neural_builder_handoff.json").write_text(json.dumps(handoff, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, sort_keys=True))


if __name__ == "__main__":
    main()
