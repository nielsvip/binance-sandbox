"""MAX_AUGMENTS_PER_POSITION is deleted — USER 2026-10-15 "delete the switch completely".

No augment-count cap exists on any surface: not in config.py / config_tradier.py / QuickConfig, not in the live
decision path (ez_manage / tradier_manage), not in the scalar verifier (backtest_v12_engine), not in the vector twin,
not in the cat_side layer, and not in any live per-sym set. Replaces tests/test_max_augments_parity.py, which
asserted the opposite. MAX_ORDER_VALUE stays — it is still a live switch (6x START_POSITION_SIZE today).
"""
import dataclasses
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KEY = "MAX_AUGMENTS_PER_POSITION"
LIVE_SOURCES = ["ez_manage.py", "tradier_manage.py", "config.py", "config_tradier.py",
                "v12_quick_engine.py", "backtest_v12_engine.py",
                "vec_decisions/ported_stateful_augment.py"]


def test_no_config_field_on_any_surface():
    from config import Config
    from config_tradier import TradierConfig
    import v12_quick_engine as V
    assert not hasattr(Config(), KEY)
    assert not hasattr(TradierConfig(), KEY)
    assert KEY not in {f.name for f in dataclasses.fields(V.QuickConfig)}


@pytest.mark.parametrize("name", LIVE_SOURCES)
def test_no_executable_reference_in_live_sources(name):
    """A comment or docstring may record the deletion; no executable token may carry the key."""
    import io
    import tokenize
    src = (ROOT / name).read_text()
    hits = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            continue
        if tok.type == tokenize.STRING and tok.line.strip().startswith(('"""', "'''", 'r"""', "r'''")):
            continue  # docstring
        if KEY in tok.string:
            hits.append(f"{name}:{tok.start[0]} {tok.line.strip()[:110]}")
    assert not hits, f"{KEY} is still read in code: {hits[:5]}"


def test_no_block_reason_remains():
    for name in ("ez_manage.py", "backtest_v12_engine.py", "tradier_manage.py"):
        assert "BLOCKED_MAX_AUGMENTS" not in (ROOT / name).read_text(), name


def test_absent_from_the_cat_side_layer():
    import per_sym_settings as ps
    for cs in ps.CAT_SIDES:
        assert KEY not in ps.defaults(cs), cs


def test_absent_from_every_live_per_sym_set():
    import json
    import sqlite3
    db = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
    if not db.exists():
        pytest.skip("no live store on this host")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30.0)
    try:
        rows = con.execute("SELECT sym_side, overrides_json, full_config_json, defaults_snapshot_json "
                           "FROM per_sym_active").fetchall()
    finally:
        con.close()
    offenders = [r[0] for r in rows if any(c and KEY in c for c in r[1:])]
    assert not offenders, f"{len(offenders)} live rows still carry {KEY}: {offenders[:8]}"


REENTRY_DEAD = {"REENTRY_TIER2_MAX_MINUTES_TRADIER", "REENTRY_TIER2_MAX_MINUTES"}
REENTRY_LIVE_SIBLINGS = {"REENTRY_TIER2_MIN_MINUTES", "REENTRY_TIER2_PRICE_PCT",
                         "REENTRY_TIER2_SIZE_MULT", "REENTRY_TIER2_MIN_MINUTES_TRADIER",
                         "REENTRY_TIER2_PRICE_PCT_TRADIER", "REENTRY_TIER2_SIZE_MULT_TRADIER"}
REENTRY_SEED_FILES = ["data/cat_side_defaults_4.json", "data/per_sym_settings.json",
                      "data/cat_side_promotions.json", "data/per_sym_full_sets.json"]


def _reentry_hits(obj):
    found = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in REENTRY_DEAD:
                    found.append(k)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(obj)
    return found


@pytest.mark.parametrize("rel", REENTRY_SEED_FILES)
def test_reentry_dead_keys_absent_from_seeding_files(rel):
    import json
    doc = json.loads((ROOT / rel).read_text())
    hits = _reentry_hits(doc)
    assert not hits, f"{rel} still seeds dead keys: {sorted(set(hits))}"


def test_reentry_dead_keys_absent_from_live_store():
    import json
    import sqlite3
    db = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
    if not db.exists():
        pytest.skip("no live store on this host")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30.0)
    try:
        rows = con.execute("SELECT sym_side, overrides_json, full_config_json, defaults_snapshot_json "
                           "FROM per_sym_active").fetchall()
    finally:
        con.close()
    offenders = [r[0] for r in rows
                 if any(c and any(k in c for k in REENTRY_DEAD) for c in r[1:])]
    assert not offenders, f"{len(offenders)} live rows still carry dead reentry keys: {offenders[:8]}"


def test_golive_sanitize_drops_reentry_dead_keys():
    import tools.v15_persym_golive as golive
    co = {"REENTRY_TIER2_MAX_MINUTES_TRADIER": 30, "REENTRY_TIER2_MAX_MINUTES": 30,
          "REENTRY_TIER2_MIN_MINUTES_TRADIER": 10, "MAX_AUGMENTS_PER_POSITION": 3}
    rec = {}
    golive.sanitize(dict(co), rec)
    assert rec["overrides"].get("REENTRY_TIER2_MIN_MINUTES_TRADIER") == 10
    assert "REENTRY_TIER2_MAX_MINUTES_TRADIER" not in rec["overrides"]
    assert "REENTRY_TIER2_MAX_MINUTES" not in rec["overrides"]
    assert "MAX_AUGMENTS_PER_POSITION" not in rec["overrides"]


def test_full_sets_builder_drops_reentry_dead_keys(tmp_path):
    import json
    import sqlite3
    import tools.build_per_sym_full_sets as bpf
    db = tmp_path / "t.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE per_sym_active (sym_side TEXT, cat_side TEXT, updated_at TEXT, winning_tag TEXT, pool_sharpe REAL, trades INTEGER, max_dd_pct REAL, acc_gain_pct REAL, full_config_json TEXT)")
    con.execute("CREATE TABLE per_sym_history (sym_side TEXT, cat_side TEXT, updated_at TEXT, winning_tag TEXT, pool_sharpe REAL, trades INTEGER, max_dd_pct REAL, gain_pct REAL, full_config_json TEXT, epoch INTEGER)")
    full = {"REENTRY_TIER2_MAX_MINUTES_TRADIER": 30, "REENTRY_TIER2_MIN_MINUTES_TRADIER": 10,
            "MAX_AUGMENTS_PER_POSITION": 3, "SOME_LIVE_KEY": 1}
    con.execute("INSERT INTO per_sym_active VALUES (?,?,?,?,?,?,?,?,?)",
                ("TST_LONG", "STOCKS_LONG", "now", "t", 0.5, 10, 1.0, 2.0, json.dumps(full)))
    con.commit()
    con.close()
    out = tmp_path / "full.json"
    import sys
    sys.argv = ["bpf", "--db", str(db), "--out", str(out), "--force", "--quiet"]
    assert bpf.main() == 0
    got = json.load(open(out))["TST_LONG"]
    assert got.get("REENTRY_TIER2_MIN_MINUTES_TRADIER") == 10
    assert "REENTRY_TIER2_MAX_MINUTES_TRADIER" not in got
    assert "MAX_AUGMENTS_PER_POSITION" not in got


def test_reentry_live_siblings_survive_the_purge():
    """The purge must take exactly the dead MAX keys, never the live tier-2 family."""
    from config_tradier import TradierConfig
    cfg = TradierConfig()
    for k in REENTRY_LIVE_SIBLINGS:
        assert hasattr(cfg, k), f"live sibling purged by accident: {k}"


def test_max_order_value_is_still_a_live_switch():
    """The sibling the user kept: deleting MAX_AUGMENTS must not take MAX_ORDER_VALUE with it."""
    from config import Config
    assert hasattr(Config(), "MAX_ORDER_VALUE")
    import per_sym_store as pss
    kept = pss._mandate_sanitize({"MAX_ORDER_VALUE": 300.0, KEY: 0})
    assert kept["MAX_ORDER_VALUE"] == 300.0
    assert KEY in kept, "the strip no longer special-cases a deleted switch"
    assert pss._mandate_sanitize({"MAX_ORDER_VALUE": 0}) == {}
