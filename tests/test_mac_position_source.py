from copy import deepcopy

from tools.mac_position_source import resolve_account_positions


NOW = 1_800_000_000.0
HASH = "a" * 64


def receipt(account, side, api_at=NOW - 2, file_at=NOW - 1, complete=True):
    return {"account": account, "venue": "crypto", "side": side, "complete": complete,
            "source": "ez_positions_service", "writer": "ez_positions_service:service:123",
            "file_sha256": HASH, "file_epoch": file_at, "api_confirmed_at": api_at}


def snapshot(account="ang", rows=None):
    return {"source": "mac", "venue": "crypto", "epoch": NOW - 1,
            "accounts": {account: rows or {}},
            "book_receipts": [receipt(account, "long"), receipt(account, "short")]}


def test_fresh_complete_mac_source_is_authoritative_and_broker_confirms_cost_fields():
    doc = snapshot(rows={"BTCUSDT_LONG": {"qty": 2.0, "source_file": "ang/long_positions.json", "last_updated": NOW - 2}})
    broker = {"BTCUSDT_LONG": {"qty": 2.0, "entry": 100.0, "break_even": 101.0, "price": 110.0}}
    result = resolve_account_positions(doc, broker, "ang", "crypto", now=NOW)
    assert result["ok"] is True
    assert result["positions"]["BTCUSDT_LONG"]["qty"] == 2.0
    assert result["positions"]["BTCUSDT_LONG"]["position_source"] == "mac_api_confirmed_rest_reconciled"
    assert result["positions"]["BTCUSDT_LONG"]["entry"] == 100.0
    assert result["provenance"]["broker_confirmation"] == "matched_quantities"


def test_stale_unrequested_account_does_not_block_requested_account():
    doc = snapshot(rows={"BTCUSDT_LONG": {"qty": 2.0, "source_file": "ang/long_positions.json"}})
    doc["book_receipts"].extend([receipt("tra", "long", api_at=NOW - 50_000, file_at=NOW - 50_000),
                                 receipt("tra", "short", api_at=NOW - 50_000, file_at=NOW - 50_000)])
    result = resolve_account_positions(doc, {"BTCUSDT_LONG": {"qty": 2.0}}, "ang", "crypto", now=NOW)
    assert result["ok"] is True


def test_missing_side_receipt_cannot_be_interpreted_as_flat():
    doc = snapshot()
    doc["book_receipts"].pop()
    result = resolve_account_positions(doc, {}, "ang", "crypto", now=NOW)
    assert result["ok"] is False and "short source receipt missing" in result["why"]


def test_complete_fresh_empty_books_prove_flat_only_against_flat_broker():
    result = resolve_account_positions(snapshot(), {}, "ang", "crypto", now=NOW)
    assert result["ok"] is True and result["positions"] == {}
    mismatch = resolve_account_positions(snapshot(), {"BTCUSDT_LONG": {"qty": 1}}, "ang", "crypto", now=NOW)
    assert mismatch["ok"] is False and "position set mismatch" in mismatch["why"]


def test_incomplete_stale_mismatched_and_invalid_books_fail_closed():
    doc = snapshot(rows={"BTCUSDT_LONG": {"qty": 2.0}})
    cases = []
    incomplete = deepcopy(doc)
    incomplete["book_receipts"][0]["complete"] = False
    cases.append((incomplete, "long source book is not complete"))
    stale = deepcopy(doc)
    stale["book_receipts"][1]["api_confirmed_at"] = NOW - 901
    cases.append((stale, "short source is stale"))
    bad_qty = deepcopy(doc)
    bad_qty["accounts"]["ang"]["BTCUSDT_LONG"]["qty"] = float("nan")
    cases.append((bad_qty, "quantity invalid"))
    mismatch = deepcopy(doc)
    cases.append((mismatch, "position set mismatch"))
    for candidate, expected in cases:
        result = resolve_account_positions(candidate, {"ETHUSDT_LONG": {"qty": 2}}, "ang", "crypto", now=NOW)
        assert result["ok"] is False and expected in result["why"]


def test_transport_receipt_must_bind_snapshot_and_destination_hash():
    doc = snapshot()
    digest = __import__("hashlib").sha256(__import__("json").dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()).hexdigest()
    receipt_doc = {"ok": True, "venue": "crypto", "snapshot_sha256": digest, "copy_sha256": digest,
                   "push_completed_at": NOW}
    good = resolve_account_positions(doc, {}, "ang", "crypto", now=NOW, transport_receipt=receipt_doc)
    assert good["ok"] is True and good["provenance"]["transport"]["snapshot_sha256"] == digest
    receipt_doc["copy_sha256"] = "b" * 64
    bad = resolve_account_positions(doc, {}, "ang", "crypto", now=NOW, transport_receipt=receipt_doc)
    assert bad["ok"] is False and "transport receipt does not bind" in bad["why"]
