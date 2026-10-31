import hashlib
import json

import pytest

from tools import account_execution_state as state


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _broker_row(qty=2.0, account_opened="2026-10-30T12:00:00Z"):
    return {
        "qty": qty,
        "entry": 100.0 if qty else None,
        "break_even": 100.1 if qty else None,
        "price": 101.0,
        "position_source": "mac_snapshot_broker_verified",
        "mac_qty": qty,
        "mac_age_s": 2.0,
        "mac": {
            "entry_price": 100.0,
            "opened_at": account_opened,
            "source_file": "ang/long_positions.json",
            "source_file_epoch": 1799999999.0,
        },
    }


def _snapshot(qty=2.0, account="ang", **kwargs):
    args = {
        "broker_confirmed": True,
        "broker_source": "binance_positionRisk",
        "observed_at": 1800000000,
        "bar_ts": 1799999100,
        "settings_sha256": _sha("settings"),
        "filter_sha256": _sha("filters"),
        "state_sha256": _sha("forward-state"),
        "now": 1800000001,
    }
    args.update(kwargs)
    position = args.pop("position", _broker_row(qty))
    return state.build_snapshot(account, "ABCUSDT_LONG", position, **args)


def test_snapshot_keeps_broker_quantity_and_validates_mac_lifecycle(tmp_path):
    snap = _snapshot()
    assert snap["qty"] == 2.0
    assert snap["entry_price"] == 100.0
    assert snap["lifecycle_status"] == "verified_mac_lifecycle"
    assert snap["lifecycle_id"]
    saved = state.persist_snapshot(tmp_path, snap)
    path = tmp_path / saved["snapshot_path"]
    assert json.loads(path.read_text()) == snap
    assert (
        json.loads((path.parent / "latest.json").read_text())["snapshot_sha256"]
        == snap["snapshot_sha256"]
    )
    assert state.load_latest_snapshot(tmp_path, "ang", "ABCUSDT_LONG") == snap

    mismatched = _broker_row()
    mismatched["mac_qty"] = 1.0
    bad = _snapshot(position=mismatched)
    assert bad["qty"] == 2.0
    assert bad["lifecycle_id"] is None
    assert bad["lifecycle_status"] == "Mac quantity does not match broker quantity"

    augmented = _broker_row()
    augmented["entry"] = 105.0
    augmented["mac"]["entry_price"] = 105.0
    augmented_snapshot = _snapshot(position=augmented)
    assert augmented_snapshot["lifecycle_id"] == snap["lifecycle_id"]

    stale_mac = _broker_row()
    stale_mac["mac"]["source_file_epoch"] = 1799990000
    stale = _snapshot(position=stale_mac)
    assert stale["qty"] == 2.0
    assert stale["lifecycle_id"] is None
    assert stale["lifecycle_status"] == "Mac source file is stale or future-dated"


def test_snapshot_requires_fresh_confirmed_broker_position_and_hashes():
    with pytest.raises(ValueError, match="broker-confirmed"):
        state.build_snapshot(
            "ang",
            "ABCUSDT_LONG",
            _broker_row(),
            broker_confirmed=False,
            broker_source="",
            observed_at=1800000000,
            bar_ts=1799999100,
            settings_sha256=_sha("s"),
            filter_sha256=_sha("f"),
            state_sha256=_sha("x"),
            now=1800000001,
        )
    with pytest.raises(ValueError, match="stale"):
        _snapshot(now=1800000100, max_age_s=10)
    with pytest.raises(ValueError, match="SHA-256"):
        state.build_snapshot(
            "ang",
            "ABCUSDT_LONG",
            _broker_row(),
            broker_confirmed=True,
            broker_source="binance_positionRisk",
            observed_at=1800000000,
            bar_ts=1799999100,
            settings_sha256="bad",
            filter_sha256=_sha("f"),
            state_sha256=_sha("x"),
            now=1800000001,
        )


def test_partial_confirmed_fills_advance_tier_only_at_full_qty_and_are_idempotent(
    tmp_path,
):
    snap = _snapshot()
    state.persist_snapshot(tmp_path, snap)
    kwargs = {
        "intent_id": "intent-1",
        "bar_ts": snap["bar_ts"],
        "tier_before": None,
        "tier_after": 1,
        "action": "AUGMENT",
        "requested_qty": 1.0,
    }
    assert state.record_tier_intent(tmp_path, snap, **kwargs)["created"] is True
    assert state.record_tier_intent(tmp_path, snap, **kwargs)["created"] is False
    first = state.record_confirmed_fill(
        tmp_path,
        "ang",
        "ABCUSDT_LONG",
        snap["lifecycle_id"],
        "intent-1",
        fill_id="fill-1",
        filled_qty=0.4,
        fill_source="binance_userTrades",
        confirmed=True,
        filled_at=1800000002,
    )
    assert first["executed_tier"] is None
    assert first["intent"]["status"] == "PENDING"
    assert first["intent"]["snapshot_sha256"] == snap["snapshot_sha256"]
    assert first["intent"]["state_sha256"] == snap["state_sha256"]
    with pytest.raises(ValueError, match="broker-confirmed"):
        state.record_confirmed_fill(
            tmp_path,
            "ang",
            "ABCUSDT_LONG",
            snap["lifecycle_id"],
            "intent-1",
            fill_id="not-confirmed",
            filled_qty=0.6,
            fill_source="broker_order_history",
            confirmed=False,
            filled_at=1800000003,
        )
    duplicate = state.record_confirmed_fill(
        tmp_path,
        "ang",
        "ABCUSDT_LONG",
        snap["lifecycle_id"],
        "intent-1",
        fill_id="fill-1",
        filled_qty=0.4,
        fill_source="binance_userTrades",
        confirmed=True,
        filled_at=1800000002,
    )
    assert duplicate["duplicate"] is True
    final = state.record_confirmed_fill(
        tmp_path,
        "ang",
        "ABCUSDT_LONG",
        snap["lifecycle_id"],
        "intent-1",
        fill_id="fill-2",
        filled_qty=0.6,
        fill_source="binance_userTrades",
        confirmed=True,
        filled_at=1800000010,
    )
    assert final["executed_tier"] == 1
    assert final["intent"]["status"] == "FILLED"
    assert final["intent"]["confirmed_fills_qty"] == pytest.approx(1.0)
    assert (
        state.load_lifecycle_state(
            tmp_path, "ang", "ABCUSDT_LONG", snap["lifecycle_id"]
        )["executed_tier"]
        == 1
    )


def test_unfilled_rejected_and_missing_attempts_keep_pending_tier(tmp_path):
    snap = _snapshot()
    state.record_tier_intent(
        tmp_path,
        snap,
        intent_id="intent-1",
        bar_ts=snap["bar_ts"],
        tier_before=None,
        tier_after=2,
        action="REDUCE",
        requested_qty=0.5,
    )
    for outcome in ("SUBMITTED", "REJECTED", "UNFILLED", "MISSING"):
        journal = state.record_attempt(
            tmp_path,
            "ang",
            "ABCUSDT_LONG",
            snap["lifecycle_id"],
            "intent-1",
            outcome,
            at=1800000002,
        )
        assert journal["executed_tier"] is None
        assert journal["intents"]["intent-1"]["status"] == "PENDING"
    with pytest.raises(ValueError, match="still pending"):
        state.record_tier_intent(
            tmp_path,
            snap,
            intent_id="intent-2",
            bar_ts=snap["bar_ts"],
            tier_before=None,
            tier_after=3,
            action="AUGMENT",
            requested_qty=0.5,
        )


def test_account_and_lifecycle_journals_are_isolated_and_late_fill_is_accepted(
    tmp_path,
):
    ang = _snapshot(account="ang")
    fin = _snapshot(account="fin")
    assert ang["lifecycle_id"] != fin["lifecycle_id"]
    for snap in (ang, fin):
        state.record_tier_intent(
            tmp_path,
            snap,
            intent_id="same-intent",
            bar_ts=snap["bar_ts"],
            tier_before=None,
            tier_after=1,
            action="AUGMENT",
            requested_qty=0.25,
        )
    fill = state.record_confirmed_fill(
        tmp_path,
        "ang",
        "ABCUSDT_LONG",
        ang["lifecycle_id"],
        "same-intent",
        fill_id="late-fill",
        filled_qty=0.25,
        fill_source="broker_order_history",
        confirmed=True,
        filled_at=1800003600,
    )
    assert fill["executed_tier"] == 1
    assert (
        state.load_lifecycle_state(
            tmp_path, "fin", "ABCUSDT_LONG", fin["lifecycle_id"]
        )["executed_tier"]
        is None
    )


def test_conflicting_duplicate_fill_and_overfill_are_refused(tmp_path):
    snap = _snapshot()
    state.record_tier_intent(
        tmp_path,
        snap,
        intent_id="intent-1",
        bar_ts=snap["bar_ts"],
        tier_before=None,
        tier_after=1,
        action="AUGMENT",
        requested_qty=1.0,
    )
    state.record_confirmed_fill(
        tmp_path,
        "ang",
        "ABCUSDT_LONG",
        snap["lifecycle_id"],
        "intent-1",
        fill_id="f1",
        filled_qty=0.4,
        fill_source="binance",
        confirmed=True,
        filled_at=1800000002,
    )
    with pytest.raises(ValueError, match="different data"):
        state.record_confirmed_fill(
            tmp_path,
            "ang",
            "ABCUSDT_LONG",
            snap["lifecycle_id"],
            "intent-1",
            fill_id="f1",
            filled_qty=0.5,
            fill_source="binance",
            confirmed=True,
            filled_at=1800000002,
        )
    with pytest.raises(ValueError, match="exceed"):
        state.record_confirmed_fill(
            tmp_path,
            "ang",
            "ABCUSDT_LONG",
            snap["lifecycle_id"],
            "intent-1",
            fill_id="f2",
            filled_qty=0.7,
            fill_source="binance",
            confirmed=True,
            filled_at=1800000003,
        )
