import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import v18_experience as X  # noqa: E402

CRYPTO_OLD = X._iso_epoch("2026-10-10T00:00:00Z")
CRYPTO_NEW = X._iso_epoch("2026-10-15T00:00:00Z")
STOCKS_MID = X._iso_epoch("2026-10-16T00:00:00Z")
STOCKS_NEW = X._iso_epoch("2026-10-18T00:00:00Z")


def _native_result(sym, gain, ov):
    return json.dumps({"symside": sym, "overrides": ov, "window_days": 30, "offset_days": 0, "bh_pct": 5.0, "bars": 100,
                       "gain_pct": gain, "trades": 7, "tim_pct": 50.0, "max_dd_pct": 1.0, "pool_sharpe": 0.1, "wr_pct": 60.0,
                       "valid": True, "invalid_reason": "", "behavior_fingerprint": "abc", "ledger": None})


@pytest.fixture
def native_db(tmp_path):
    p = tmp_path / "eval_cache.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE evals (key TEXT PRIMARY KEY, result TEXT NOT NULL, created REAL, hits INT DEFAULT 0)")
    base = {"A": 1, "B": True, "C": "x"}
    rows = [("a" * 40, _native_result("BTCUSDC_LONG", 3.5, base), CRYPTO_OLD),
            ("b" * 40, _native_result("BTCUSDC_SHORT", -1.0, dict(base, A=2)), CRYPTO_NEW),
            ("c" * 40, _native_result("AAPL_LONG", 2.0, base), STOCKS_MID)]
    c.executemany("INSERT INTO evals (key, result, created) VALUES (?,?,?)", rows)
    c.commit()
    c.close()
    return p


@pytest.fixture
def log_dir(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    fp = "fp0"
    base = {"gain_pct": 9.0, "trades": 4, "fp": fp, "valid": True, "window_days": 30, "tim": 50.0, "max_dd_pct": 1.0,
            "pool_sharpe": 0.1, "wr_pct": 50.0, "delta": 0.0, "secs": 0.1, "cum_before": 9.0, "err": ""}
    lines = [
        {"ts": "2026-10-19T01:00:00Z", "sym_side": "ETHUSDT_LONG", "label": "BOARD_CTX", "cat_side": "CRYPTO_LONG", "tier": "T",
         "bh": 1.0, "baseline_gain": 9.0, "baseline_trades": 4, "n_overrides": 3, "start_fp": fp, "window_days": 30, "round": "r1"},
        dict(base, ts="2026-10-19T01:00:01Z", sym_side="ETHUSDT_LONG", sheet="START", row=None, switch="BEST_START", cand="",
             label="BEST_START_SET", cached=False),
        dict(base, ts="2026-10-19T01:00:02Z", sym_side="ETHUSDT_LONG", sheet="S", row=3, switch="A", cand="2", label="naked",
             cached=False, gain_pct=9.5, fp="fp1", delta=0.5),
        dict(base, ts="2026-10-19T01:00:03Z", sym_side="ETHUSDT_LONG", sheet="S", row=3, switch="A", cand="2", label="hustle",
             cached=True),
        dict(base, ts="2026-10-19T01:00:03Z", sym_side="ETHUSDT_LONG", sheet="S", row=4, switch="B", cand="False",
             label="hustle", cached=True),
        dict(base, ts="2026-10-19T01:00:03Z", sym_side="ETHUSDT_LONG", sheet="S", row=4, switch="B", cand="False",
             label="naked", cached=True),
        {"ts": "2026-10-19T01:00:04Z", "sym_side": "ETHUSDT_LONG", "sheet": "S", "row": 4, "switch": "B", "cand": "False",
         "label": "ROW_DONE", "row_secs": 0.1, "n_evals": 2, "delta": 0.0, "promoted": False, "cum_after": 9.0},
    ]
    (d / "ETHUSDT_LONG_jump.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    prog = d / "ETHUSDT_LONG_v14_progress.json"
    prog.write_text(json.dumps({"symside": "ETHUSDT_LONG", "defaults_round": "r1", "initial_overrides": {"A": 1, "B": True, "C": "x"},
                                "cumulative_overrides": {"A": 1, "B": True, "C": "x"}, "done": {}}))
    return d


@pytest.fixture
def hist_root(tmp_path):
    r = tmp_path / "hroot"
    (r / "history" / "ang").mkdir(parents=True)
    (r / "tradier" / "history" / "tra").mkdir(parents=True)
    ev = [{"ts": "2026-10-13T10:00:00+00:00", "type": "OPEN", "qty": 1, "price": 2, "value": 2, "reason": "X |VEC_EXACT"},
          {"ts": "2026-10-15T10:00:00+00:00", "type": "REDUCE", "qty": 1, "price": 2, "value": 2, "reason": "EXIT_Y"},
          {"ts": "2026-10-15T11:00:00+00:00", "type": "CLOSE", "qty": 1, "price": 2, "value": 2, "reason": "BROKER_RECONCILED"}]
    (r / "history" / "ang" / "SOLUSDC_LONG.jsonl").write_text("\n".join(json.dumps(x) for x in ev) + "\n")
    (r / "tradier" / "history" / "tra" / "MSFT_LONG.jsonl").write_text(json.dumps(
        {"ts": "2026-10-16T15:00:00+00:00", "type": "OPEN", "qty": 1, "price": 3, "value": 3, "reason": "Z"}) + "\n")
    return r


def run(*argv):
    return X.main([str(a) for a in argv])


def count(store, cs, table, where="1"):
    n = 0
    for p in Path(store).glob(f"experience_{cs}_*.db"):
        c = sqlite3.connect(p)
        n += c.execute(f"SELECT count(*) FROM {table} WHERE {where}").fetchone()[0]
        c.close()
    return n


def test_dry_run_writes_nothing(tmp_path, native_db):
    store = tmp_path / "store"
    assert run("ingest", "--kind", "native", "--from", native_db, "--store", store) == 0
    assert not store.exists()


def test_native_ingest_prove_cat_side_separation(tmp_path, native_db):
    store = tmp_path / "store"
    run("ingest", "--kind", "native", "--from", native_db, "--store", store, "--apply")
    assert count(store, "CRYPTO_LONG", "evals") == 1
    assert count(store, "CRYPTO_SHORT", "evals") == 1
    assert count(store, "STOCKS_LONG", "evals") == 1
    assert count(store, "STOCKS_SHORT", "evals") == 0
    for p in store.glob("experience_*_*.db"):
        cs, _ = X._shard_meta_from_name(p.name)
        c = sqlite3.connect(p)
        assert c.execute("SELECT count(*) FROM evals WHERE cat_side != ?", (cs,)).fetchone()[0] == 0
    rep = tmp_path / "p.json"
    assert run("prove", "--kind", "native", "--from", native_db, "--against", store, "--report", rep) == 0
    j = json.loads(rep.read_text())
    assert j["counts"]["checked"] == 3
    assert j["missing"] == 0 and j["mismatched"] == 0 and j["reclaimable"]
    # idempotent re-ingest
    run("ingest", "--kind", "native", "--from", native_db, "--store", store, "--apply")
    assert count(store, "CRYPTO_LONG", "evals") == 1


def test_prove_detects_missing(tmp_path, native_db):
    store = tmp_path / "store"
    run("ingest", "--kind", "native", "--from", native_db, "--store", store, "--apply", "--limit", 2)
    rep = tmp_path / "p.json"
    assert run("prove", "--kind", "native", "--from", native_db, "--against", store, "--report", rep) == 3
    j = json.loads(rep.read_text())
    assert j["missing"] == 1 and not j["reclaimable"]


def test_native_era_cut(tmp_path, native_db):
    store = tmp_path / "store"
    run("ingest", "--kind", "native", "--from", native_db, "--store", store, "--apply")
    assert count(store, "CRYPTO_LONG", "evals", "era='pre_hermetic'") == 1
    assert count(store, "CRYPTO_SHORT", "evals", "era='hermetic'") == 1
    assert count(store, "STOCKS_LONG", "evals", "era='pre_hermetic'") == 1


def test_history_era_cut_and_echo_drop(tmp_path, hist_root):
    store = tmp_path / "store"
    run("ingest", "--kind", "history", "--history-root", hist_root, "--store", store, "--apply")
    assert count(store, "CRYPTO_LONG", "live_events") == 2
    assert count(store, "CRYPTO_LONG", "live_events", "era='pre_hermetic'") == 1
    assert count(store, "CRYPTO_LONG", "live_events", "era='hermetic'") == 1
    assert count(store, "CRYPTO_LONG", "live_events", "reason LIKE '%BROKER%'") == 0
    assert count(store, "STOCKS_LONG", "live_events", "era='pre_hermetic'") == 1
    rep = tmp_path / "ph.json"
    assert run("prove", "--kind", "history", "--from", hist_root, "--against", store, "--report", rep) == 0
    assert json.loads(rep.read_text())["counts"]["broker_echo_dropped_by_rule"] == 1


def test_log_identity_settlement_one_row_per_board_row(tmp_path, log_dir):
    store = tmp_path / "store"
    run("ingest", "--kind", "log", "--from", log_dir / "*.jsonl", "--host", "s1", "--progress-glob", log_dir / "*_v14_progress.json",
        "--store", store, "--apply")
    # row 3 'hustle' (cached, delta 0, same fp/gain as chain after naked? naked changed fp -> not identity)
    # row 4 hustle+naked are both identity -> exactly ONE settlement row
    assert count(store, "CRYPTO_LONG", "settlements") >= 1
    c = sqlite3.connect(next(store.glob("experience_CRYPTO_LONG_*.db")))
    rows = c.execute("SELECT row, n_lines, switch FROM settlements WHERE row=4").fetchall()
    assert rows == [(4, 2, "B")]
    assert c.execute("SELECT count(*) FROM row_done").fetchone()[0] == 1
    assert c.execute("SELECT count(*) FROM boards").fetchone()[0] == 1
    rep = tmp_path / "pl.json"
    code = run("prove", "--kind", "log", "--from", log_dir / "*.jsonl", "--host", "s1", "--against", store, "--report", rep)
    j = json.loads(rep.read_text())
    assert code == 0, j
    assert j["missing"] == 0


def test_reclaim_refuses_without_cold_copy(tmp_path, native_db):
    store = tmp_path / "store"
    run("ingest", "--kind", "native", "--from", native_db, "--store", store, "--apply")
    rep = tmp_path / "p.json"
    run("prove", "--kind", "native", "--from", native_db, "--against", store, "--report", rep)
    assert run("reclaim", "--source", native_db, "--cold", tmp_path / "nope.zst", "--proof", rep, "--apply") == 4
    assert native_db.exists()


def test_reclaim_refuses_cold_on_non_mount(tmp_path, native_db):
    store = tmp_path / "store"
    run("ingest", "--kind", "native", "--from", native_db, "--store", store, "--apply")
    rep = tmp_path / "p.json"
    run("prove", "--kind", "native", "--from", native_db, "--against", store, "--report", rep)
    zst = tmp_path / "c.zst"
    zst.write_bytes(b"x")
    (tmp_path / "c.zst.manifest.json").write_text(json.dumps({"zst_sha256": X._sha256_file(zst)}))
    assert run("reclaim", "--source", native_db, "--cold", zst, "--proof", rep, "--apply") == 4
    assert native_db.exists()


def test_reclaim_refuses_without_proof(tmp_path, native_db):
    assert run("reclaim", "--source", native_db, "--cold", tmp_path / "z.zst", "--apply") == 4
    assert native_db.exists()


def test_replay_mods_matches_rebuild_coercion():
    assert X.replay_mods("S", "False", "K", "3") == {"S": False, "K": 3}
    assert X.replay_mods("BEST_START", "", None, None) == {}
    assert X.coerce("1.5") == 1.5 and X.coerce("x") == "x"


def test_shard_refuses_unknown_cat_side(tmp_path):
    s = X.Store(tmp_path / "s", apply=True)
    with pytest.raises(ValueError):
        s.shard("MIXED", "202610")


def test_store_candidates_include_mac_boot_disk():
    assert any(c["path"].endswith("data/experience") for c in X.store_candidates())


def test_audit_clean(tmp_path, native_db):
    store = tmp_path / "store"
    run("ingest", "--kind", "native", "--from", native_db, "--store", store, "--apply")
    assert run("audit", "--store", store, "--out", tmp_path / "a.json") == 0
    j = json.loads((tmp_path / "a.json").read_text())
    assert j["totals_by_cat_side"]["CRYPTO_LONG"]["evals"] == 1


def test_trades_ingest(tmp_path):
    d = tmp_path / "tl"
    d.mkdir()
    meta = {"kind": "meta", "sym_side": "SOLUSDC_LONG", "cat_side": "CRYPTO_LONG", "eval_id": "ee1", "replayed_at": "2026-10-19T00:00:00Z",
            "gain_pct": 1.5, "trades": 1, "window_days": 30}
    tr = {"kind": "trade", "eval_id": "ee1", "trade_no": 0, "bar_entry": 1, "bar_exit": 3, "bars_held": 2, "entry_reason": "a",
          "exit_reason": "b", "entry_family": "A", "exit_family": "B", "pnl_pct": 1.0, "pnl_dollars": 2.0, "deployed": 10.0,
          "mfe_pct": 2.0, "mae_pct": -1.0, "post_exit_32b_pct": 0.5, "unrealised_mark": False}
    (d / "SOLUSDC_LONG.jsonl").write_text(json.dumps(meta) + "\n" + json.dumps(tr) + "\n")
    store = tmp_path / "store"
    for _ in range(2):
        assert run("ingest", "--kind", "trades", "--from", d / "*.jsonl", "--store", store, "--apply") == 0
    assert count(store, "CRYPTO_LONG", "trades") == 1
    assert count(store, "CRYPTO_LONG", "evals", "provenance='ledger_replay'") == 1
    assert count(store, "CRYPTO_SHORT", "trades") == 0


def test_digest_never_writes_pilot_digest_names(tmp_path, native_db):
    store = tmp_path / "store"
    run("ingest", "--kind", "native", "--from", native_db, "--store", store, "--apply")
    out = tmp_path / "learn"
    run("digest", "--store", store, "--out-dir", out, "--apply")
    names = sorted(p.name for p in out.iterdir())
    assert names and all(n.startswith("store_digest_") for n in names)
