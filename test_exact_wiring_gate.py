import sqlite3

from tools import exact_wiring_gate as gate


def test_two_binding_values_identical_to_baseline_are_red_reconnect(
    monkeypatch
):
    monkeypatch.setattr(
        gate,
        "read_site_class",
        lambda _param: {"class": "PER_KEY_READ", "hits": []},
    )
    monkeypatch.setattr(
        gate,
        "master_state",
        lambda _param, *_args: {"master": "X_ENABLED", "enabled": True, "reason": "test"},
    )
    monkeypatch.setattr(
        gate,
        "range_binding",
        lambda _param, _values, _npz: {"status": "BINDING_OBSERVED_DOMAIN"},
    )
    rows = [
        {
            "value": 10.0,
            "value_json": "10.0",
            "fingerprint": "2:baseline",
            "inert": True,
        },
        {
            "value": 20.0,
            "value_json": "20.0",
            "fingerprint": "2:baseline",
            "inert": True,
        },
    ]
    result = gate.classify_param("WT_THRESHOLD", rows, "2:baseline", None)
    assert result["verdict"] == "RED_RECONNECT"
    assert result["skip_remaining_exact"] is True


def test_disabled_master_is_not_mislabeled_as_broken_wiring(monkeypatch):
    monkeypatch.setattr(
        gate,
        "read_site_class",
        lambda _param: {"class": "PER_KEY_READ", "hits": []},
    )
    monkeypatch.setattr(
        gate,
        "master_state",
        lambda _param, *_args: {
            "master": "FEATURE_ENABLED",
            "enabled": False,
            "reason": "test",
        },
    )
    rows = [
        {
            "value": 10.0,
            "value_json": "10.0",
            "fingerprint": "2:baseline",
            "inert": True,
        }
    ]
    result = gate.classify_param("FEATURE_THRESHOLD", rows, "2:baseline", None)
    assert result["verdict"] == "RED_GATED_OFF"


def test_current_contract_query_excludes_stale_and_opposite_side():
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE key_baseline (
          mode TEXT,campaign TEXT,symbol TEXT,side TEXT,contract_fingerprint TEXT,
          validation_status TEXT,trades_fingerprint TEXT
        );
        CREATE TABLE param_cells (
          mode TEXT,campaign TEXT,symbol TEXT,side TEXT,tier TEXT,
          contract_fingerprint TEXT,validation_status TEXT,param TEXT,
          value_json TEXT,value_num REAL,trades_fingerprint TEXT,inert INTEGER
        );
        """
    )
    con.execute(
        "INSERT INTO key_baseline VALUES (?,?,?,?,?,?,?)",
        ("tradier", "c", "MU", "LONG", "current", "PASS", "base"),
    )
    rows = [
        ("tradier", "c", "MU", "LONG", "ENGINE", "current", "PASS", "P", "1", 1, "a", 0),
        ("tradier", "c", "MU", "LONG", "ENGINE", "stale", "PASS", "P", "2", 2, "b", 0),
        ("tradier", "c", "MU", "SHORT", "ENGINE", "current", "PASS", "P", "3", 3, "c", 0),
        ("tradier", "c", "MU", "LONG", "VEC", "current", "PASS", "P", "4", 4, "d", 0),
    ]
    con.executemany("INSERT INTO param_cells VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    baseline, grouped = gate.rows_for_key(
        con,
        symbol="MU",
        side="LONG",
        campaign="c",
        contract_fingerprint="current",
    )
    assert baseline == "base"
    assert [row["value"] for row in grouped["P"]] == [1.0]
