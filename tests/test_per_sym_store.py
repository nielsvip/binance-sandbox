"""test_per_sym_store — SQLite primary + JSON backup stores full defaults+overrides (~5000 keys).

Every per_sym entry must carry the defaults snapshot at promotion time plus
the override delta, so TEMPLATE changes cannot drift live.  This test covers:
  - upsert stores ~3300+ keys full_config (defaults 3314 crypto / 3355 stocks)
  - get_full_config / get_overrides / get_knob parity
  - SQLite primary wins over divergent JSON (parallel run, sqlite default)
  - JSON fallback when PER_SYM_STORE_SQLITE_DISABLED=1
"""
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import per_sym_store as pss
import cat_side_defaults as csd

def test_upsert_snapshot_is_full():
    # Use an isolated DB so we don't touch the live one.
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "per_sym_store_test.db"
        old = pss.DB_PATH
        pss.DB_PATH = db
        # isolated json files too
        jdir = Path(td) / "hourly"
        jdir.mkdir(parents=True)
        cj = jdir / "per_sym_active_config.json"
        sj = jdir / "per_sym_active_config_stocks.json"
        old_cj, old_sj = pss.CRYPTO_JSON, pss.STOCKS_JSON
        pss.CRYPTO_JSON, pss.STOCKS_JSON = cj, sj
        try:
            # per_sym_store expects 4-cat defaults; pick crypto long
            cat = "CRYPTO_LONG"
            defaults = csd.defaults(cat)
            # overrides: one promoted switch
            overrides = {"WT_LOWER_CROSS_EXIT_TF": "15m", "AUGMENT_MIN_GAIN_PCT": 1.0}
            # crypto defaults have 3314 keys, full should be >= that
            assert len(defaults) >= 3000, len(defaults)
            full = dict(defaults)
            full.update(overrides)
            meta = {"winning_tag": "test", "wsharpe": 0.5, "trades": 30}
            pss.upsert("TESTUSDT_LONG", overrides, defaults, full, meta, template_md5="abc", defaults_round="round1")
            # also test stocks side
            cat2 = "STOCKS_LONG"
            d2 = csd.defaults(cat2)
            full2 = dict(d2)
            full2.update({"WT_LOWER_CROSS_EXIT_TF": "4h"})
            pss.upsert("TESTAAPL_LONG", {"WT_LOWER_CROSS_EXIT_TF": "4h"}, d2, full2, {"winning_tag": "test2"}, template_md5="def")
            # sqlite primary: each has full_config
            e1 = pss.get("TESTUSDT_LONG")
            assert e1 is not None and e1["_source"] == "sqlite"
            assert len(e1["full_config"]) == len(full)
            assert e1["full_config"]["WT_LOWER_CROSS_EXIT_TF"] == "15m"
            assert e1["defaults_snapshot"]["WT_LOWER_CROSS_EXIT_TF"] == defaults["WT_LOWER_CROSS_EXIT_TF"]
            e2 = pss.get("TESTAAPL_LONG")
            assert len(e2["full_config"]) >= 3300
            # get_full_config / get_knob
            fc = pss.get_full_config("TESTUSDT_LONG")
            assert fc["AUGMENT_MIN_GAIN_PCT"] == 1.0
            # knob that is only in defaults (not overridden) must come from full_config
            some_default_key = next(k for k in defaults if k not in overrides)
            assert pss.get_knob("TESTUSDT", "LONG", some_default_key, "MISSING") == defaults[some_default_key]
            # overridden knob
            assert pss.get_knob("TESTUSDT", "LONG", "WT_LOWER_CROSS_EXIT_TF", "MISSING") == "15m"
            # JSON backup exists and carries same full snapshot
            raw = json.loads(cj.read_text())
            assert raw["TESTUSDT_LONG"]["full_config"]["WT_LOWER_CROSS_EXIT_TF"] == "15m"
            assert len(raw["TESTUSDT_LONG"]["full_config"]) == len(full)
            # SQLite primary wins over divergent JSON: mutate JSON then read again
            raw["TESTUSDT_LONG"]["full_config"]["WT_LOWER_CROSS_EXIT_TF"] = "4h"
            cj.write_text(json.dumps(raw))
            # sqlite still returns 15m
            assert pss.get_knob("TESTUSDT", "LONG", "WT_LOWER_CROSS_EXIT_TF", "MISSING") == "15m"
            # JSON fallback when sqlite disabled
            os.environ["PER_SYM_STORE_SQLITE_DISABLED"] = "1"
            try:
                assert pss.get_knob("TESTUSDT", "LONG", "WT_LOWER_CROSS_EXIT_TF", "MISSING") == "4h"
            finally:
                os.environ.pop("PER_SYM_STORE_SQLITE_DISABLED", None)
        finally:
            pss.DB_PATH = old
            pss.CRYPTO_JSON, pss.STOCKS_JSON = old_cj, old_sj


def test_live_db_has_full_snapshot():
    # On the live DB, every entry should carry full_config ~3300 keys.
    # This guards that the v15_pilot promotion now snapshots defaults+overrides.
    # If DB missing (fresh checkout) skip.
    if not pss.DB_PATH.exists():
        return
    con = sqlite3.connect(str(pss.DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        n = con.execute("SELECT COUNT(*) FROM per_sym_active").fetchone()[0]
        assert n >= 100, f"live DB too small {n} — backfill incomplete?"
        for row in con.execute("SELECT sym_side, overrides_json, defaults_snapshot_json, full_config_json FROM per_sym_active LIMIT 5"):
            sym = row["sym_side"]
            ov = json.loads(row["overrides_json"]) if row["overrides_json"] else {}
            snap = json.loads(row["defaults_snapshot_json"]) if row["defaults_snapshot_json"] else {}
            full = json.loads(row["full_config_json"]) if row["full_config_json"] else {}
            cat = "CRYPTO_LONG" if sym.endswith(("USDT","USDC","USD1","BUSD","FDUSD")) else ("STOCKS_LONG" if sym.endswith("_LONG") else "STOCKS_SHORT")
            # crude cat from symbol/side
            if sym.endswith("_LONG") or sym.endswith("_SHORT"):
                # use stored cat_side if present, but just check sizes
                pass
            assert len(snap) >= 3000, f"{sym} snap {len(snap)}"
            assert len(full) >= 3000, f"{sym} full {len(full)}"
            # full = snap + overrides (overrides may add keys, but never smaller than snap)
            assert len(full) >= len(snap)
            # overrides subset of full
            for k, v in ov.items():
                assert full.get(k) == v, f"{sym} {k} mismatch"
    finally:
        con.close()


def test_history_retention_and_dynamic_switches():
    """30-day retention + dynamic switches/filters seamless + compact results without bloat."""
    import time
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "per_sym_store_test.db"
        old = pss.DB_PATH
        pss.DB_PATH = db
        jdir = Path(td) / "hourly"
        jdir.mkdir(parents=True)
        cj = jdir / "per_sym_active_config.json"
        sj = jdir / "per_sym_active_config_stocks.json"
        old_cj, old_sj = pss.CRYPTO_JSON, pss.STOCKS_JSON
        pss.CRYPTO_JSON, pss.STOCKS_JSON = cj, sj
        try:
            cat = "CRYPTO_LONG"
            defaults = csd.defaults(cat)
            assert len(defaults) >= 3000
            # 1) dynamic addition: new switch appears
            defaults2 = dict(defaults)
            defaults2["NEW_DYNAMIC_SWITCH_XYZ"] = "15m"
            overrides = {"WT_LOWER_CROSS_EXIT_TF": "15m", "NEW_DYNAMIC_SWITCH_XYZ": "4h"}
            full = dict(defaults2); full.update(overrides)
            pss.upsert("DYNUSDT_LONG", overrides, defaults2, full, {"winning_tag": "w1", "gain_pct": 2.5, "trades": 20, "pool_sharpe": 0.6, "max_dd_pct": 5, "tim_pct": 30, "valid": True, "baseline_gain": 1.0}, template_md5="m1", defaults_round="r1")
            e = pss.get("DYNUSDT_LONG")
            assert "NEW_DYNAMIC_SWITCH_XYZ" in e["full_config"]
            assert e["full_config"]["NEW_DYNAMIC_SWITCH_XYZ"] == "4h"
            # history stores compact results + compressed full
            hist = pss.get_history("DYNUSDT_LONG", days=30)
            assert len(hist) == 1
            assert hist[0]["gain_pct"] == 2.5
            assert hist[0]["delta_pct"] == 1.5  # 2.5-1.0
            assert hist[0]["trades"] == 20
            # full in history is decompressed transparently
            assert "NEW_DYNAMIC_SWITCH_XYZ" in hist[0]["full_config"]
            # 2) removal: next day the switch disappears from TEMPLATE
            defaults3 = dict(defaults)  # without new key
            overrides2 = {"WT_LOWER_CROSS_EXIT_TF": "4h"}
            full2 = dict(defaults3); full2.update(overrides2)
            pss.upsert("DYNUSDT_LONG", overrides2, defaults3, full2, {"gain_pct": 1.0, "trades": 15}, template_md5="m2")
            e2 = pss.get("DYNUSDT_LONG")
            assert "NEW_DYNAMIC_SWITCH_XYZ" not in e2["full_config"]
            # old history still has it for audit
            hist2 = pss.get_history("DYNUSDT_LONG", days=30)
            assert len(hist2) == 2
            # verify old entry still has the removed switch in its snapshot
            old_entry = [h for h in hist2 if h["template_md5"] == "m1"][0]
            assert "NEW_DYNAMIC_SWITCH_XYZ" in old_entry["full_config"]
            # 3) pruning: insert an old entry >35d and ensure it gets pruned
            con = sqlite3.connect(str(db))
            old_ep = time.time() - 40 * 86400
            con.execute("INSERT INTO per_sym_history(sym_side, updated_at, epoch, gain_pct, trades) VALUES (?,?,?, ?, ?)", ("DYNUSDT_LONG", "2025-08-01T00:00:00Z", old_ep, 0.5, 5))
            con.commit()
            con.close()
            pss.upsert("DYNUSDT_LONG", overrides2, defaults3, full2, {"gain_pct": 1.1, "trades": 16}, template_md5="m3")
            hist3 = pss.get_history("DYNUSDT_LONG", days=60)
            # old 40d entry should be gone (>35d pruned), recent 3 remain
            assert all((h["epoch"] or 0) >= time.time() - 36 * 86400 for h in hist3)
            # health reports retention
            h = pss.health()
            assert h["history_retention_days"] == 30
            assert h["history_n"] >= 2
        finally:
            pss.DB_PATH = old
            pss.CRYPTO_JSON, pss.STOCKS_JSON = old_cj, old_sj


def test_kv_survives_json_vanish():
    """Writers dual-write JSON→SQL; loaders SQL-primary so vanishing JSONs don't silently lose 172 promotions."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "per_sym_store_test.db"
        old = pss.DB_PATH
        pss.DB_PATH = db
        try:
            # 1) cat_side_defaults_4
            data = {"CRYPTO_LONG": {"FOO": 1}, "CRYPTO_SHORT": {}, "STOCKS_LONG": {}, "STOCKS_SHORT": {}, "_meta": {}}
            pss.kv_put(pss.KV_CAT_SIDE_DEFAULTS_4, data)
            # file missing → still returns from SQL
            assert pss.kv_get(pss.KV_CAT_SIDE_DEFAULTS_4)["CRYPTO_LONG"]["FOO"] == 1
            # cat_side_defaults loader uses same path
            import cat_side_defaults as csd2
            csd2._cache["data"] = {}
            csd2._cache["mtime"] = None
            csd2._cache["mtime_sql"] = None
            # with no file, loader should still see SQL
            old_path = csd2.PATH
            csd2.PATH = Path(td) / "missing.json"
            try:
                assert csd2.defaults("CRYPTO_LONG")["FOO"] == 1
            finally:
                csd2.PATH = old_path
                csd2._cache["data"] = {}
                csd2._cache["mtime"] = None
                csd2._cache["mtime_sql"] = None
            # 2) promotions / ledger
            promos = {"CRYPTO_LONG": {"SW": {"value": "True"}}}
            pss.kv_put(pss.KV_CAT_SIDE_PROMOTIONS, promos)
            assert pss.get_cat_side_promotions()["CRYPTO_LONG"]["SW"]["value"] == "True"
            ledger = {"CRYPTO_LONG": {"round_id": "abc", "pos_base": {"a|b": 5}}}
            pss.kv_put(pss.KV_AVG_DELTA_ROUND_LEDGER, ledger)
            assert pss.get_avg_delta_round_ledger()["CRYPTO_LONG"]["round_id"] == "abc"
            # 3) gain_pusher universes
            uni = {"entry_switches": {}, "defaults": {}}
            pss.kv_put("gain_pusher/universe_CRYPTO_LONG", uni)
            assert pss.kv_get("gain_pusher/universe_CRYPTO_LONG") == uni
            # 4) JSON becomes generated view: health shows kv_n
            h = pss.kv_health()
            assert h["kv_n"] >= 4
        finally:
            pss.DB_PATH = old
