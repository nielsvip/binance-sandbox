from datetime import date, datetime, timezone

from tools.audit_recent_tradier_trade_universe import (
    build_audit,
    is_option_symbol,
    side_from_quantity,
    utc_calendar_window,
)


def test_utc_calendar_window_is_exactly_thirty_calendar_dates():
    start, end = utc_calendar_window(date(2026, 7, 29), 30)
    assert start == datetime(2026, 6, 30, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 30, tzinfo=timezone.utc)
    assert (end.date() - start.date()).days == 30


def test_quantity_is_the_side_contract_and_options_fail_closed():
    assert side_from_quantity(5) == "LONG"
    assert side_from_quantity("-2") == "SHORT"
    assert side_from_quantity(0) is None
    assert is_option_symbol("MU260821C00150000")
    assert not is_option_symbol("MU")


def test_build_audit_keeps_long_short_separate_and_excludes_options(tmp_path):
    start, end = utc_calendar_window(date(2026, 7, 29), 30)

    def fetcher(account, _start, _end):
        assert account == "trb"
        return {
            "history": [
                {
                    "date": "2026-07-15T12:00:00Z",
                    "symbol": "MU",
                    "trade_type": "equity",
                }
            ],
            "history_pages": [{"page": 1, "row_count": 1}],
            "gainloss": [
                {
                    "symbol": "MU",
                    "quantity": 10,
                    "close_date": "2026-07-10T12:00:00Z",
                },
                {
                    "symbol": "MU",
                    "quantity": -4,
                    "close_date": "2026-07-11T12:00:00Z",
                },
                {
                    "symbol": "MU260821C00150000",
                    "quantity": 1,
                    "close_date": "2026-07-12T12:00:00Z",
                },
            ],
            "gainloss_payload_sha256": "gainloss-sha",
            "positions": [
                {
                    "symbol": "TTD",
                    "quantity": -2,
                    "date_acquired": "2026-07-20T12:00:00Z",
                }
            ],
            "positions_payload_sha256": "positions-sha",
        }

    audit = build_audit(
        root=tmp_path,
        accounts=("trb",),
        start=start,
        end=end,
        fetcher=fetcher,
    )
    keys = {row["position_key"] for row in audit["cohort"]}
    assert keys == {"MU_LONG", "MU_SHORT", "TTD_SHORT"}
    assert audit["long_key_count"] == 1
    assert audit["short_key_count"] == 2
    assert audit["cohort_symbol_count"] == 2
