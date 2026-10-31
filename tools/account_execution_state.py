"""Durable account-scoped broker position snapshots and fill-confirmed tier state."""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _number(value, name, allow_zero=True):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite numeric") from exc
    if not math.isfinite(number) or (number < 0 if allow_zero else number <= 0):
        raise ValueError(
            f"{name} must be finite and {'nonnegative' if allow_zero else 'positive'}"
        )
    return number


def _epoch(value, name):
    try:
        stamp = float(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            stamp = parsed.timestamp()
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be a finite epoch or ISO timestamp") from exc
    if not math.isfinite(stamp) or stamp < 0:
        raise ValueError(f"{name} must be a finite nonnegative timestamp")
    return stamp / 1000.0 if stamp > 1e11 else stamp


def _identity(account, sym_side):
    acct = str(account or "").strip().lower()
    ss = str(sym_side or "").strip().upper()
    if not acct or ":" in acct or "/" in acct or "_" in acct:
        raise ValueError("account must be a nonempty account key")
    if "_" not in ss or ss.rsplit("_", 1)[-1] not in ("LONG", "SHORT"):
        raise ValueError("sym_side must end in _LONG or _SHORT")
    return acct, ss


def _required_hash(value, name):
    value = str(value or "").lower()
    if not _SHA_RE.fullmatch(value):
        raise ValueError(f"{name} must be a 64-character SHA-256")
    return value


def _canonical_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(
                payload, stream, sort_keys=True, separators=(",", ":"), default=str
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _write_immutable(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temp, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError(f"immutable artifact collision at {path}")
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _validated_mac_lifecycle(position, now, max_age_s, qty, entry):
    if position.get("position_source") != "mac_snapshot_broker_verified":
        return None, "Mac lifecycle annotation is not broker-verified"
    mac = position.get("mac")
    if not isinstance(mac, dict):
        return None, "Mac lifecycle annotation is absent"
    try:
        age = _number(position.get("mac_age_s"), "mac_age_s")
        mac_qty = _number(position.get("mac_qty"), "mac_qty")
        mac_entry = _number(mac.get("entry_price"), "mac.entry_price", allow_zero=False)
        opened_at = _epoch(mac.get("opened_at"), "mac.opened_at")
    except ValueError as exc:
        return None, str(exc)
    source_epoch = position["mac"].get("source_file_epoch")
    if source_epoch in (None, "") or not position["mac"].get("source_file"):
        return None, "Mac source file provenance is missing"
    try:
        source_epoch = _epoch(source_epoch, "mac.source_file_epoch")
        if position["mac"].get("last_updated"):
            source_epoch = min(
                source_epoch,
                _epoch(position["mac"]["last_updated"], "mac.last_updated"),
            )
    except ValueError as exc:
        return None, str(exc)
    if now - source_epoch > max_age_s or source_epoch > now:
        return None, "Mac source file is stale or future-dated"
    if age > max_age_s or age < 0 or opened_at > now:
        return None, "Mac lifecycle annotation is stale or future-dated"
    if abs(mac_qty - qty) > max(1e-8, qty * 1e-6):
        return None, "Mac quantity does not match broker quantity"
    if entry is None or abs(mac_entry - entry) > max(1e-10, entry * 1e-6):
        return None, "Mac entry price does not match broker entry price"
    lifecycle_seed = f"{opened_at:.6f}"
    return hashlib.sha256(lifecycle_seed.encode()).hexdigest(), "verified_mac_lifecycle"


def build_snapshot(
    account,
    sym_side,
    broker_position,
    *,
    broker_confirmed,
    broker_source,
    observed_at,
    bar_ts,
    settings_sha256,
    filter_sha256,
    state_sha256,
    now=None,
    max_age_s=30.0,
    mac_max_age_s=900.0,
):
    """Build a broker-authoritative snapshot; accept Mac lifecycle fields only when annotation validates."""
    acct, ss = _identity(account, sym_side)
    if broker_confirmed is not True or not str(broker_source or "").strip():
        raise ValueError("a broker-confirmed position source is required")
    if not isinstance(broker_position, dict):
        raise ValueError("broker_position must be a mapping")
    observed = _epoch(observed_at, "observed_at")
    current = _epoch(time.time() if now is None else now, "now")
    age = current - observed
    if age < 0 or age > _number(max_age_s, "max_age_s"):
        raise ValueError("broker position observation is stale or future-dated")
    bar = _epoch(bar_ts, "bar_ts")
    if bar > current:
        raise ValueError("decision bar timestamp is future-dated")
    qty = _number(broker_position.get("qty"), "broker qty")
    entry_raw = broker_position.get("entry")
    entry = (
        None
        if entry_raw in (None, "") or qty == 0
        else _number(entry_raw, "broker entry", allow_zero=False)
    )
    break_even_raw = broker_position.get("break_even")
    break_even = (
        None
        if break_even_raw in (None, "") or qty == 0
        else _number(break_even_raw, "broker break_even", allow_zero=False)
    )
    if qty > 0 and entry is None:
        raise ValueError("held broker position requires an observed entry price")
    settings_hash = _required_hash(settings_sha256, "settings_sha256")
    filter_hash = _required_hash(filter_sha256, "filter_sha256")
    state_hash = _required_hash(state_sha256, "state_sha256")
    lifecycle_id, lifecycle_status = (None, "flat position")
    if qty > 0:
        lifecycle_id, lifecycle_status = _validated_mac_lifecycle(
            broker_position,
            current,
            _number(mac_max_age_s, "mac_max_age_s"),
            qty,
            entry,
        )
        if lifecycle_id:
            lifecycle_id = hashlib.sha256(
                f"{acct}|{ss}|{lifecycle_id}".encode()
            ).hexdigest()
    broker_facts = {
        "account": acct,
        "sym_side": ss,
        "broker_source": str(broker_source),
        "observed_at": observed,
        "qty": qty,
        "entry_price": entry,
        "break_even_price": break_even,
    }
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        **broker_facts,
        "bar_ts": bar,
        "position_age_s": round(age, 6),
        "lifecycle_id": lifecycle_id,
        "lifecycle_status": lifecycle_status,
        "settings_sha256": settings_hash,
        "filter_sha256": filter_hash,
        "state_sha256": state_hash,
        "broker_facts_sha256": _canonical_hash(broker_facts),
        "mac_lifecycle": None,
        "mac_annotation_sha256": None,
    }
    if lifecycle_id:
        snapshot["mac_lifecycle"] = {
            "opened_at": _epoch(broker_position["mac"]["opened_at"], "mac.opened_at"),
            "qty": broker_position["mac_qty"],
            "age_s": broker_position["mac_age_s"],
            "fields": dict(broker_position["mac"]),
            "source": "mac_snapshot_broker_verified",
        }
        snapshot["mac_annotation_sha256"] = _canonical_hash(
            {
                "position_source": broker_position.get("position_source"),
                "mac_qty": broker_position.get("mac_qty"),
                "mac_age_s": broker_position.get("mac_age_s"),
                "mac": broker_position["mac"],
            }
        )
    snapshot["snapshot_sha256"] = _canonical_hash(snapshot)
    return snapshot


def persist_snapshot(root, snapshot):
    """Persist an immutable content-addressed snapshot and atomically update its latest pointer."""
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != SCHEMA_VERSION
    ):
        raise ValueError("unsupported execution snapshot")
    acct, ss = _identity(snapshot.get("account"), snapshot.get("sym_side"))
    if snapshot.get("snapshot_sha256") != _canonical_hash(
        {k: v for k, v in snapshot.items() if k != "snapshot_sha256"}
    ):
        raise ValueError("snapshot hash does not match its contents")
    base = Path(root) / "snapshots" / acct / ss
    rel = Path("snapshots") / acct / ss / f"{snapshot['snapshot_sha256']}.json"
    _write_immutable(Path(root) / rel, snapshot)
    base.mkdir(parents=True, exist_ok=True)
    with (base / "latest.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            try:
                latest = load_latest_snapshot(root, acct, ss)
            except FileNotFoundError:
                latest = None
            if latest is None or _epoch(snapshot.get("observed_at"), "observed_at") > _epoch(
                latest.get("observed_at"), "latest observed_at"
            ):
                _atomic_json(
                    base / "latest.json",
                    {"snapshot_sha256": snapshot["snapshot_sha256"], "snapshot_path": str(rel)},
                )
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return {"snapshot_sha256": snapshot["snapshot_sha256"], "snapshot_path": str(rel)}


def load_latest_snapshot(root, account, sym_side):
    acct, ss = _identity(account, sym_side)
    base = Path(root) / "snapshots" / acct / ss
    pointer = json.loads((base / "latest.json").read_text())
    rel = Path(pointer.get("snapshot_path") or "")
    if (
        rel.is_absolute()
        or rel.parts[:3] != ("snapshots", acct, ss)
        or ".." in rel.parts
    ):
        raise ValueError("latest snapshot pointer escapes its account scope")
    snapshot = json.loads((Path(root) / rel).read_text())
    if (snapshot.get("account"), snapshot.get("sym_side")) != (acct, ss):
        raise ValueError("latest snapshot identity mismatch")
    if snapshot.get("snapshot_sha256") != pointer.get("snapshot_sha256"):
        raise ValueError("latest snapshot pointer hash mismatch")
    expected = _canonical_hash(
        {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    )
    if expected != snapshot.get("snapshot_sha256"):
        raise ValueError("latest snapshot content hash mismatch")
    return snapshot


def _journal_path(root, account, sym_side, lifecycle_id):
    acct, ss = _identity(account, sym_side)
    lifecycle = str(lifecycle_id or "")
    if not _SHA_RE.fullmatch(lifecycle):
        raise ValueError("verified lifecycle_id is required")
    return Path(root) / "journals" / acct / ss / f"{lifecycle}.json"


def _read_journal(path, account, sym_side, lifecycle_id):
    try:
        doc = json.loads(path.read_text())
    except FileNotFoundError:
        return {
            "schema_version": SCHEMA_VERSION,
            "account": account,
            "sym_side": sym_side,
            "lifecycle_id": lifecycle_id,
            "executed_tiers": {},
            "family_order": {},
            "intents": {},
            "fill_ids": {},
            "events": [],
        }
    if (doc.get("account"), doc.get("sym_side"), doc.get("lifecycle_id")) != (
        account,
        sym_side,
        lifecycle_id,
    ):
        raise ValueError("journal identity mismatch")
    return doc


def _mutate_journal(root, account, sym_side, lifecycle_id, callback):
    path = _journal_path(root, account, sym_side, lifecycle_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        doc = _read_journal(
            path, str(account).lower(), str(sym_side).upper(), lifecycle_id
        )
        result = callback(doc)
        _atomic_json(path, doc)
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        return result, doc


def record_tier_intent(
    root,
    snapshot,
    *,
    intent_id,
    bar_ts,
    tier_before,
    tier_after,
    action,
    requested_qty,
    family,
    revision=0,
    sequence=0,
    now=None,
):
    """Create/revise a family-specific pending tier transition without advancing its cursor."""
    if not isinstance(snapshot, dict) or snapshot.get("lifecycle_id") is None:
        raise ValueError("tier transitions require a verified actual lifecycle")
    expected_snapshot_hash = _canonical_hash(
        {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    )
    if snapshot.get("snapshot_sha256") != expected_snapshot_hash:
        raise ValueError("tier transition snapshot hash does not match its contents")
    acct, ss = _identity(snapshot.get("account"), snapshot.get("sym_side"))
    lifecycle = snapshot["lifecycle_id"]
    iid = str(intent_id or "").strip()
    if not iid:
        raise ValueError("intent_id is required")
    before = None if tier_before is None else int(tier_before)
    after = int(tier_after)
    family = str(family or "").strip().upper()
    if not family:
        raise ValueError("tier family is required")
    revision = int(revision)
    sequence = int(sequence)
    if revision < 0 or sequence < 0:
        raise ValueError("revision and sequence must be nonnegative integers")
    if before is not None and before < 0 or after < 0:
        raise ValueError("tier values must be nonnegative integers")
    requested = _number(requested_qty, "requested_qty", allow_zero=False)
    timestamp = _epoch(time.time() if now is None else now, "now")
    intent = {
        "intent_id": iid,
        "family": family,
        "bar_ts": _epoch(bar_ts, "bar_ts"),
        "sequence": sequence,
        "tier_before": before,
        "tier_after": after,
        "action": str(action or "").upper(),
        "requested_qty": requested,
        "revision": revision,
        "snapshot_sha256": _required_hash(
            snapshot.get("snapshot_sha256"), "snapshot_sha256"
        ),
        "settings_sha256": snapshot["settings_sha256"],
        "filter_sha256": snapshot["filter_sha256"],
        "state_sha256": snapshot["state_sha256"],
        "broker_facts_sha256": snapshot["broker_facts_sha256"],
        "mac_annotation_sha256": snapshot["mac_annotation_sha256"],
        "confirmed_fills_qty": 0.0,
        "status": "PENDING",
        "created_at": timestamp,
        "attempts": [],
        "fills": [],
        "revisions": [],
    }
    if intent["action"] not in ("OPEN", "AUGMENT", "REDUCE", "CLOSE"):
        raise ValueError("unsupported tier action")

    revision_record = {
        "revision": revision,
        "requested_qty": requested,
        "snapshot_sha256": intent["snapshot_sha256"],
        "settings_sha256": intent["settings_sha256"],
        "filter_sha256": intent["filter_sha256"],
        "state_sha256": intent["state_sha256"],
        "broker_facts_sha256": intent["broker_facts_sha256"],
        "mac_annotation_sha256": intent["mac_annotation_sha256"],
        "created_at": timestamp,
    }

    def apply(doc):
        family_state = doc["executed_tiers"].get(
            family, {"tier": None, "bar_ts": 0.0, "sequence": -1}
        )
        existing = doc["intents"].get(iid)
        if existing:
            compare = {
                key: existing.get(key)
                for key in (
                    "intent_id",
                    "family",
                    "bar_ts",
                    "sequence",
                    "tier_before",
                    "tier_after",
                    "action",
                )
            }
            wanted = {key: intent[key] for key in compare}
            if compare != wanted:
                raise ValueError(
                    "intent_id already exists with different transition data"
                )
            current_revision = int(existing["revision"])
            if revision == current_revision:
                saved_revision = existing["revisions"][-1]
                if any(
                    saved_revision.get(key) != revision_record.get(key)
                    for key in revision_record
                    if key != "created_at"
                ):
                    raise ValueError(
                        "revision already exists with different sizing or provenance"
                    )
                return {"created": False, "revised": False, "intent": existing}
            if revision != current_revision + 1:
                raise ValueError(
                    "resized intent revision must increment by exactly one"
                )
            if existing["status"] in ("FILLED", "OVERFILLED"):
                raise ValueError("completed tier intent cannot be resized")
            existing["revision"] = revision
            existing["requested_qty"] = requested
            existing["snapshot_sha256"] = intent["snapshot_sha256"]
            existing["settings_sha256"] = intent["settings_sha256"]
            existing["filter_sha256"] = intent["filter_sha256"]
            existing["state_sha256"] = intent["state_sha256"]
            existing["broker_facts_sha256"] = intent["broker_facts_sha256"]
            existing["mac_annotation_sha256"] = intent["mac_annotation_sha256"]
            existing["revisions"].append(revision_record)
            complete, advanced = _settle_intent(doc, existing)
            doc["events"].append(
                {
                    "event": "INTENT_RESIZED",
                    "intent_id": iid,
                    "revision": revision,
                    "requested_qty": requested,
                    "at": timestamp,
                    "tier_advanced": advanced,
                }
            )
            return {
                "created": False,
                "revised": True,
                "intent": existing,
                "complete": complete,
            }
        if family_state.get("tier") != before:
            raise ValueError(
                f"tier_before {before} does not match confirmed {family} tier {family_state.get('tier')}"
            )
        prior_order = doc["family_order"].get(family, [0.0, -1])
        order = [intent["bar_ts"], sequence]
        if tuple(order) < tuple(prior_order):
            raise ValueError("tier intent bar is older than the family journal")
        if tuple(order) == tuple(prior_order):
            raise ValueError("family bar/sequence already has an intent")
        if revision != 0:
            raise ValueError("a new intent must begin at revision 0")
        doc["family_order"][family] = order
        intent["revisions"].append(revision_record)
        doc["intents"][iid] = intent
        doc["events"].append(
            {
                "event": "INTENT_PENDING",
                "intent_id": iid,
                "at": timestamp,
                "bar_ts": intent["bar_ts"],
            }
        )
        return {"created": True, "revised": False, "intent": intent}

    result, _ = _mutate_journal(root, acct, ss, lifecycle, apply)
    return result


def record_attempt(
    root,
    account,
    sym_side,
    lifecycle_id,
    intent_id,
    outcome,
    *,
    revision=None,
    at=None,
    detail=None,
    now=None,
):
    """Record submit/reject/unfilled observations without changing executed tier or discarding pending work."""
    result_status = str(outcome or "").upper()
    if result_status not in (
        "SUBMITTED",
        "REJECTED",
        "UNFILLED",
        "MISSING",
        "PARTIALLY_FILLED",
    ):
        raise ValueError("outcome must describe a non-fill attempt")
    timestamp = _epoch(time.time() if at is None else at, "at")
    current = _epoch(time.time() if now is None else now, "now")
    if timestamp > current:
        raise ValueError("attempt timestamp is future-dated")

    def apply(doc):
        intent = doc["intents"].get(str(intent_id))
        if not intent or intent.get("status") in ("FILLED", "OVERFILLED"):
            raise ValueError("pending tier intent not found")
        revision_id = intent["revision"] if revision is None else int(revision)
        if revision_id < 0 or revision_id > intent["revision"]:
            raise ValueError("attempt revision is not present")
        attempt = {
            "revision": revision_id,
            "outcome": result_status,
            "at": timestamp,
            "detail": detail,
        }
        if attempt not in intent["attempts"]:
            intent["attempts"].append(attempt)
            doc["events"].append(
                {
                    "event": result_status,
                    "intent_id": str(intent_id),
                    "at": timestamp,
                    "detail": detail,
                }
            )
        intent["status"] = "PENDING"
        return intent

    _, doc = _mutate_journal(root, account, sym_side, lifecycle_id, apply)
    return doc


def record_confirmed_fill(
    root,
    account,
    sym_side,
    lifecycle_id,
    intent_id,
    *,
    fill_id,
    filled_qty,
    fill_source,
    confirmed,
    filled_at,
    revision=None,
    now=None,
):
    """Add an idempotent broker fill; advance the tier only once its requested quantity is confirmed."""
    if confirmed is not True or not str(fill_source or "").strip():
        raise ValueError("broker-confirmed fill source is required")
    fid = str(fill_id or "").strip()
    if not fid:
        raise ValueError("fill_id is required")
    qty = _number(filled_qty, "filled_qty", allow_zero=False)
    timestamp = _epoch(filled_at, "filled_at")
    current = _epoch(time.time() if now is None else now, "now")
    if timestamp > current:
        raise ValueError("fill timestamp is future-dated")

    def apply(doc):
        prior = doc["fill_ids"].get(fid)
        intent = doc["intents"].get(str(intent_id))
        if not intent:
            raise ValueError("tier intent not found")
        revision_id = intent["revision"] if revision is None else int(revision)
        if revision_id < 0 or revision_id > intent["revision"]:
            raise ValueError("fill revision is not present")
        fill = {
            "fill_id": fid,
            "intent_id": str(intent_id),
            "revision": revision_id,
            "qty": qty,
            "source": str(fill_source),
            "filled_at": timestamp,
        }
        if prior is not None:
            if prior != fill:
                raise ValueError("fill_id already recorded with different data")
            return {
                "duplicate": True,
                "intent": intent,
                "executed_tier": doc["executed_tiers"]
                .get(intent["family"], {})
                .get("tier"),
            }
        cumulative = float(intent.get("confirmed_fills_qty") or 0.0) + qty
        requested = float(intent["requested_qty"])
        intent["confirmed_fills_qty"] = cumulative
        intent["fills"].append(fill)
        doc["fill_ids"][fid] = fill
        complete, advanced = _settle_intent(doc, intent)
        doc["events"].append(
            {
                "event": "FILL_CONFIRMED",
                **fill,
                "cumulative_qty": intent["confirmed_fills_qty"],
                "requested_qty": requested,
                "tier_advanced": advanced,
                "overfilled": intent["status"] == "OVERFILLED",
            }
        )
        return {
            "duplicate": False,
            "intent": intent,
            "executed_tier": doc["executed_tiers"]
            .get(intent["family"], {})
            .get("tier"),
            "executed_tiers": doc["executed_tiers"],
            "tier_advanced": advanced,
        }

    result, _ = _mutate_journal(root, account, sym_side, lifecycle_id, apply)
    return result


def _settle_intent(doc, intent):
    requested = float(intent["requested_qty"])
    filled = float(intent.get("confirmed_fills_qty") or 0.0)
    tolerance = max(1e-10, requested * 1e-8)
    complete = filled >= requested - tolerance
    intent["status"] = (
        "OVERFILLED"
        if filled > requested + tolerance
        else "FILLED" if complete else "PENDING"
    )
    if not complete:
        return False, False
    family = intent["family"]
    cursor = doc["executed_tiers"].get(
        family, {"tier": None, "bar_ts": 0.0, "sequence": -1}
    )
    incoming = (float(intent["bar_ts"]), int(intent.get("sequence") or 0))
    current = (float(cursor.get("bar_ts") or 0.0), int(cursor.get("sequence", -1)))
    if incoming < current:
        return True, False
    doc["executed_tiers"][family] = {
        "tier": intent["tier_after"],
        "bar_ts": incoming[0],
        "sequence": incoming[1],
        "intent_id": intent["intent_id"],
    }
    return True, True


def load_lifecycle_state(root, account, sym_side, lifecycle_id):
    path = _journal_path(root, account, sym_side, lifecycle_id)
    return _read_journal(
        path, str(account).lower(), str(sym_side).upper(), lifecycle_id
    )
