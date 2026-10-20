"""USER LAW 2026-10-20: coverage metric, catch-up hysteresis, throughput line, V18_CATCHUP launch flag."""

import datetime
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from tools import v15_fleet_scheduler as S

NOW = datetime.datetime(2026, 10, 20, 12, 0, tzinfo=datetime.timezone.utc).timestamp()


def _iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(ss, age_h, lag_min, host="niels", ts_last=None):
    last = ts_last if ts_last is not None else NOW - age_h * 3600 - 3600
    return [ss, _iso(NOW - age_h * 3600), host, last, (last + lag_min * 60) * 1e9, None]


def test_coverage_counts_fresh_board_on_fresh_npz_only():
    allowed = ["AAAUSDT_LONG", "AAAUSDT_SHORT", "BBBUSDT_LONG", "CCCUSDT_LONG"]
    rows = [_row("AAAUSDT_LONG", 2, 7), _row("AAAUSDT_SHORT", 2, 45), _row("BBBUSDT_LONG", 30, 5)]
    c = S._coverage(allowed, rows, NOW)
    assert c["n"] == 4 and c["covered"] == 1 and c["coverage"] == 0.25
    assert c["published_24h"] == 2
    pc = c["per_cat"]["CRYPTO_LONG"]
    assert pc["n"] == 3 and pc["covered"] == 1 and pc["never_or_old"] == 2
    assert c["per_cat"]["CRYPTO_SHORT"]["published_but_npz_stale"] == 1


def test_newest_manifest_per_side_wins():
    rows = [_row("AAAUSDT_LONG", 30, 5), _row("AAAUSDT_LONG", 1, 5)]
    assert S._coverage(["AAAUSDT_LONG"], rows, NOW)["covered"] == 1


def test_manifest_npz_age_field_wins():
    r = _row("AAAUSDT_LONG", 1, 90)
    r[5] = 3.0
    assert S._coverage(["AAAUSDT_LONG"], [r], NOW)["covered"] == 1


def test_empty_universe_is_full_coverage():
    assert S._coverage([], [], NOW)["coverage"] == 1.0


def test_hysteresis_does_not_flap():
    m = False
    seq = []
    for cov in (0.9, 0.79, 0.85, 0.94, 0.95, 0.9, 0.7):
        m = S._select_catchup_mode(m, cov)
        seq.append(m)
    assert seq == [False, True, True, True, False, False, True]


def test_throughput_per_host_and_both_sides():
    rows = [_row("AAAUSDT_LONG", 0.2, 5), _row("AAAUSDT_SHORT", 0.5, 5, host="s2"), _row("BBBUSDT_LONG", 0.5, 5), _row("CCCUSDT_LONG", 3, 5)]
    t = S._throughput(rows, NOW)
    assert t["published_per_host"] == {"s1": 2, "s2": 1}
    assert t["symbols_both_sides"] == 1 and t["target_per_host"] == 12


def test_side_cmd_carries_flag_only_in_catchup_mode():
    host = {"name": "s1", "root": "~/binance-sandbox", "template_dir": "SPREADSHEETS"}
    S._CATCHUP_MODE[0] = False
    assert "V18_CATCHUP=1" not in S.side_cmd(host, "AAA", "LONG", "30D", "/tmp/p", 1, 3)
    S._CATCHUP_MODE[0] = True
    try:
        assert "V18_CATCHUP=1" in S.side_cmd(host, "AAA", "LONG", "30D", "/tmp/p", 1, 3)
    finally:
        S._CATCHUP_MODE[0] = False
