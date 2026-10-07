"""test_order_pulse — order-pulse parsing (Mac) + post-order threshold (S1 supervisor).

Live-contract pins: crypto marker + ts format copied from real rotated logs
(ez_manage_ang.log.3 / ez_manage_men.log.2); tradier marker shape from the
`if order_id:` success branch (tradier_manage.py ~28670, zero live samples on
2026-10-06 because no real stock order was placed that day).
"""
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from mac_order_pulse import RE_CRYPTO_EXEC, RE_CRYPTO_SENT, RE_STOCK_ANY, RE_STOCK_SENT, LedgerTailer, LogTailer, collect_pulse, ledger_best_one_shot, match_crypto, match_stock, parse_ez_ts, parse_ledger_ts, parse_tradier_ts  # noqa: E402
from s1_position_failover import decide, in_order_window, last_order_age_s, load_pulse  # noqa: E402

NOW = datetime.now().astimezone()
LIVE_CRYPTO_1 = "04 10:07:00 - WARNING - [ang] \U0001f477 [ang:BTCUSDT_LONG] MARKET_SENT qty=0.013 orderId=1153957610780 status=NEW avgPrice=?"
LIVE_CRYPTO_2 = "03 22:44:43 - WARNING - [men] \U0001f477 [men:STXUSDT_LONG] MARKET_SENT qty=25.0 orderId=8633329060 status=NEW avgPrice=?"
TRADIER_SENT = "10-06 15:42:11 - [TRADE] SENT: \u2705 AAPL  OPEN    BUY  | Qty: 10   @ $232.10   | Status: ok | ID: 987654"
TRADIER_ATTEMPT_NONE = "10-06 20:43:01 - [TRADE] AR    OPEN    SELL | Qty: 48   @ $35.00   | Status: SUBMITTED  | order_id: None | Reason: GAP_FILL_SELL gap=4.76% | Gain: +0.00% $+0.0 | Entry: $0.00 PosVal: $0 |"


def test_crypto_marker_accepts_live_samples():
    assert RE_CRYPTO_SENT.search(LIVE_CRYPTO_1).group(1) == "1153957610780"
    assert RE_CRYPTO_SENT.search(LIVE_CRYPTO_2).group(1) == "8633329060"


def test_crypto_exec_trace_counts_as_activity():
    line = "06 23:46:05 - INFO - [men] [EXEC_TRACE] men:LTCUSDC_SHORT: STEP1_LOCK action=OPEN is_aug=True is_red=False"
    assert RE_CRYPTO_EXEC.search(line) is not None
    assert match_crypto(line) is True


def test_crypto_marker_rejects_unknown_id():
    assert RE_CRYPTO_SENT.search("MARKET_SENT qty=1.0 orderId=? status=NEW") is None
    assert match_crypto("06 23:46:05 - x MARKET_SENT qty=1.0 orderId=? status=NEW") is False


def test_stock_sent_extracts_id_and_any_counts_attempts():
    assert RE_STOCK_SENT.search(TRADIER_SENT).group(1) == "987654"
    assert RE_STOCK_ANY.search(TRADIER_SENT) is not None
    assert match_stock(TRADIER_ATTEMPT_NONE) is True
    assert match_stock("10-06 20:35:22 - [TRADE] AR OPEN SELL | Status: TIMEOUT_STALL_SKIP | order_id: N/A |") is True
    assert match_stock("10-06 20:35:22 - MONITOR_QUEUE: Total=11") is False


def test_ledger_ts_parses_newest():
    blob = '{"ts":"2026-10-06T23:01:00+00:00","type":"OPEN"}\n{"ts":"2026-10-06T23:47:09.273940+00:00","type":"AUGMENT","qty":1}\n'
    ts = parse_ledger_ts(blob)
    assert ts is not None and abs(ts - datetime(2026, 10, 6, 23, 47, 9).replace(tzinfo=NOW.tzinfo).timestamp()) < 2
    assert parse_ledger_ts("no ts here\n") is None


def test_ledger_tailer_incremental(tmp_path):
    acct_dir = tmp_path / "men"
    acct_dir.mkdir()
    (acct_dir / "A_LONG.jsonl").write_text('{"ts":"2026-10-06T10:00:00+00:00","type":"OPEN"}\n')
    tailer = LedgerTailer("men", history_dir=tmp_path)
    first = tailer.poll(NOW)
    assert first is not None
    assert tailer.poll(NOW) == first
    with open(acct_dir / "A_LONG.jsonl", "a") as fh:
        fh.write('{"ts":"2026-10-06T11:00:00+00:00","type":"AUGMENT"}\n')
    assert tailer.poll(NOW) > first


def test_ledger_one_shot_picks_newest_file(tmp_path):
    acct_dir = tmp_path / "trb"
    acct_dir.mkdir()
    (acct_dir / "A_LONG.jsonl").write_text('{"ts":"2026-10-06T10:00:00+00:00","type":"OPEN"}\n')
    (acct_dir / "B_LONG.jsonl").write_text('{"ts":"2026-10-06T15:19:16+00:00","type":"OPEN"}\n')
    ts = ledger_best_one_shot("trb", history_dir=tmp_path)
    assert ts is not None and ts > datetime(2026, 10, 6, 15, 0, 0).replace(tzinfo=NOW.tzinfo).timestamp()
    assert ledger_best_one_shot("nope", history_dir=tmp_path) is None


def test_ez_ts_parses_live_format():
    day_now = datetime.now().astimezone()
    line = day_now.strftime("%d %H:%M:%S") + " - INFO - [ang] x"
    ts = parse_ez_ts(line, day_now)
    assert ts is not None and abs(ts - time.time()) < 120


def test_ez_ts_month_boundary_resolves_past():
    oct1 = datetime(NOW.year, 10, 1, 8, 0, 0).astimezone()
    ts = parse_ez_ts("30 23:00:00 - WARNING - [ang] MARKET_SENT qty=1 orderId=5 status=NEW", oct1)
    assert ts is not None and ts < oct1.timestamp() and ts > oct1.timestamp() - 86400 * 3


def test_tradier_ts_parses_live_format():
    ts = parse_tradier_ts(TRADIER_SENT, NOW)
    assert ts is not None and abs(ts - time.time()) < 86400 * 400


def test_january_rollover_prefers_past():
    dec_now = datetime(NOW.year, 1, 2, 12, 0, 0).astimezone()
    ts = parse_tradier_ts("12-30 10:00:00 - x", dec_now)
    assert ts is not None and ts < dec_now.timestamp()


def test_order_window_bounds():
    assert in_order_window(0.0) is True
    assert in_order_window(179.9) is True
    assert in_order_window(180.0) is False
    assert in_order_window(None) is False


def test_pulse_age_none_when_stale_or_missing():
    now = 1000000.0
    assert last_order_age_s(None, now) is None
    assert last_order_age_s({"epoch": now - 61, "last_order_epoch": now - 1}, now) is None
    assert last_order_age_s({"epoch": now - 5, "last_order_epoch": None}, now) is None
    assert last_order_age_s({"epoch": now - 5, "last_order_epoch": now - 30}, now) == 30.0


def test_load_pulse_newest_wins(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text('{"epoch": 100.0, "last_order_epoch": 90.0}')
    b.write_text('{"epoch": 200.0, "last_order_epoch": 190.0}')
    assert load_pulse((a, b))["epoch"] == 200.0
    assert load_pulse((b, a))["epoch"] == 200.0
    (tmp_path / "missing.json")
    assert load_pulse((tmp_path / "missing.json",)) is None


def test_decide_post_order_grace_then_fire():
    ages = {"ang": 4.0, "inf": 1.0, "flz": None, "men": 1.0, "fin": 1.0}
    s, _, _, b = decide(True, ages, set(), {}, order_age=10.0, breach_streaks={})
    assert s == [] and b["ang"] == 1
    s, _, _, b = decide(True, ages, set(), {}, order_age=11.0, breach_streaks=b)
    assert s == ["ang"]


def test_decide_post_order_fresh_positions_hold():
    ages = {a: 1.0 for a in ("ang", "inf", "flz", "men", "fin")}
    s, t, _, _ = decide(True, ages, set(), {}, order_age=5.0, breach_streaks={})
    assert (s, t) == ([], [])


def test_decide_quiet_60s_holds_post_order_4s_fires():
    ages = {"ang": 60.0, "inf": 4.0, "flz": None, "men": 1.0, "fin": 1.0}
    s, _, _, _ = decide(True, ages, set(), {}, order_age=None, breach_streaks={})
    assert s == []
    _, _, _, b = decide(True, ages, set(), {}, order_age=30.0, breach_streaks={})
    s, _, _, _ = decide(True, ages, set(), {}, order_age=31.0, breach_streaks=b)
    assert sorted(s) == ["ang", "inf"]


def test_collect_pulse_shape():
    payload = collect_pulse()
    assert set(payload) == {"epoch", "last_order_epoch", "last_order_age_s", "per_account"}


def test_tailer_incremental_and_rotation(tmp_path):
    log = tmp_path / "ez.log"
    log.write_text("06 10:00:00 - WARNING - [ang] noise\n")
    tailer = LogTailer(log, match_crypto, parse_ez_ts)
    now = datetime.now().astimezone()
    assert tailer.poll(now) is None
    with open(log, "a") as fh:
        fh.write("06 10:05:00 - WARNING - [ang] \U0001f477 [ang:X_LONG] MARKET_SENT qty=1 orderId=42 status=NEW\n")
    found = tailer.poll(now)
    assert found is not None
    assert tailer.poll(now) == found
    log.write_text("06 10:06:00 - WARNING - [ang] rotated, no orders\n")
    assert tailer.poll(now) == found


def test_tailer_finds_mid_file_marker(tmp_path):
    log = tmp_path / "big.log"
    with open(log, "w") as fh:
        fh.write("06 09:00:00 - x\n" * 5000)
        fh.write("06 10:07:00 - WARNING - [ang] \U0001f477 [ang:BTCUSDT_LONG] MARKET_SENT qty=0.013 orderId=1153957610780 status=NEW avgPrice=?\n")
        fh.write("06 10:08:00 - y\n" * 5000)
    tailer = LogTailer(log, match_crypto, parse_ez_ts)
    assert tailer.poll(datetime.now().astimezone()) is not None
