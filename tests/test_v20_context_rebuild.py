import json
from pathlib import Path
import sqlite3

from tools import v20_context_rebuild as rebuild
from tools import v18_linknet as linknet


def fixture(tmp_path):
    initial = {"SW": False, "FILTER": "OFF", "DEP": 0}
    promoted = {"SW": True, "FILTER": "1h", "DEP": 2}
    progress = {"symside": "BTCUSDT_LONG", "initial_overrides": initial, "done": {"EXIT_STRUCTURAL!3:SW=True": {"promoted": True, "is_running": False, "promoted_filters": ["FILTER=1h"], "dep_forced": {"promoted": {"DEP": 2}}}}}
    lines = [{"label": "BOARD_CTX", "sym_side": "BTCUSDT_LONG", "ts": "2026-10-27T00:00:00Z", "set_sha256": rebuild.planner.set_sha256(initial), "baseline_gain": 5.0, "window_days": 30},
             {"label": "naked", "sym_side": "BTCUSDT_LONG", "ts": "2026-10-27T00:01:00Z", "switch": "SW", "cand": "True", "sheet": "EXIT_STRUCTURAL", "row": 3, "gain_pct": 6.0, "cum_before": 5.0, "valid": True},
             {"label": "ROW_DONE", "sym_side": "BTCUSDT_LONG", "ts": "2026-10-27T00:02:00Z", "switch": "SW", "cand": "True", "sheet": "EXIT_STRUCTURAL", "row": 3, "promoted": True, "cum_after": 6.0, "set_sha256": rebuild.planner.set_sha256(promoted)},
             {"label": "naked", "sym_side": "BTCUSDT_LONG", "ts": "2026-10-27T00:03:00Z", "switch": "OTHER", "cand": "False", "sheet": "EXIT_STRUCTURAL", "row": 4, "gain_pct": 6.0, "cum_before": 6.0, "valid": True}]
    raw, source = tmp_path / "raw.jsonl", tmp_path / "progress.json"
    raw.write_text("".join(json.dumps(line) + "\n" for line in lines))
    source.write_text(json.dumps(progress))
    return raw, source, progress, lines


def test_exact_yellow_and_dependency_reconstruction(tmp_path):
    raw, progress, _, _ = fixture(tmp_path)
    rows, diagnostics = [], []
    report = rebuild.reconstruct(raw, progress, "fixture", lambda *row: rows.append(row), diagnostics.append)
    assert report["counters"]["verified_eval_rows"] == 2
    assert rows[0][1] == {"SW": False, "FILTER": "OFF", "DEP": 0}
    assert rows[1][1] == {"SW": True, "FILTER": "1h", "DEP": 2}
    assert rows[1][3]["chain_sha_verified"] is True
    assert not diagnostics


def test_missing_companion_never_becomes_partial_known_context(tmp_path):
    raw, path, progress, _ = fixture(tmp_path)
    progress["done"]["EXIT_STRUCTURAL!3:SW=True"]["promoted_filters"] = []
    path.write_text(json.dumps(progress))
    rows, diagnostics = [], []
    report = rebuild.reconstruct(raw, path, "fixture", lambda *row: rows.append(row), diagnostics.append)
    assert len(rows) == 1
    assert report["counters"]["post_transition_sha_mismatch"] == 1
    assert report["counters"]["eval_without_verified_chain"] == 1


def test_final_set_not_substituted_for_initial(tmp_path):
    raw, path, progress, _ = fixture(tmp_path)
    progress.pop("initial_overrides")
    progress["cumulative_overrides"] = {"SW": True, "FILTER": "1h", "DEP": 2}
    path.write_text(json.dumps(progress))
    rows = []
    rebuild.reconstruct(raw, path, "fixture", lambda *row: rows.append(row), lambda record: None)
    assert len(rows) == 1  # only the explicitly logged matching post-row hash can anchor the final content
    assert rows[0][0]["cum_before"] == 6.0


def test_official_store_and_strict_join_preserve_proof(tmp_path):
    raw, progress, _, _ = fixture(tmp_path)
    store = rebuild.experience.Store(tmp_path / "store", apply=True)
    rebuild.reconstruct(raw, progress, "fixture", lambda *row: rebuild.write_eval(store, *row), lambda record: None)
    store.close()
    shard = next((tmp_path / "store").glob("experience_CRYPTO_LONG_*.db"))
    with sqlite3.connect(shard) as connection:
        rows = connection.execute("SELECT gain,cum_before,extra FROM evals ORDER BY ts").fetchall()
        assert [row[:2] for row in rows] == [(6.0, 5.0), (6.0, 6.0)]
        assert all(json.loads(row[2])["chain_sha_verified"] for row in rows)
        connection.execute("UPDATE evals SET extra='{}' WHERE cum_before=5")
    class Encoder:
        ignored_values = __import__("collections").Counter()
        def diff(self, value): return value
        def feats(self, value): return tuple(sorted(value))
    original = linknet.FT.ContextEncoder
    try:
        linknet.FT.ContextEncoder = lambda context: Encoder()
        join = linknet.build_ctx_join("CRYPTO_LONG", tmp_path / "store", fctx=object(), native=False, verified_log_only=True, log=lambda *args: None)
    finally:
        linknet.FT.ContextEncoder = original
    assert join["report"]["source_reports"][0]["eligible_log_rows"] == 1
    assert ("BTCUSDT_LONG", 5.0) not in join["cbmap"]
    assert ("BTCUSDT_LONG", 6.0) in join["cbmap"]
