"""v15_stalled_prune — finalized sym_sides lose rescue copies; sheets backstop needs age+proof."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import v15_stalled_prune as P


def test_attribution_progress():
    assert P.symside_of_progress("AMZN_LONG_v14_progress.json") == "AMZN_LONG"
    assert P.symside_of_progress("A_SHORT.json") == "A_SHORT"
    assert P.symside_of_progress("X.jsonl") is None
    assert P.symside_of_progress("1INCHUSDT_LONG_jump.jsonl") == "1INCHUSDT_LONG"
    assert P.symside_of_progress("endgame_knowledge.json") is None
    assert P.symside_of_progress("endgame_ledger.jsonl") is None
    assert P.symside_of_progress("notes.txt") is None


def test_attribution_sheets():
    assert P.symside_of_sheet("AMZN_LONG_30d_matrix.xlsx") == "AMZN_LONG"
    assert P.symside_of_sheet("GALAUSDT_LONG_bh34p28_gain52p20_30d_matrix.html") == "GALAUSDT_LONG"
    assert P.symside_of_sheet("AMZN_LONG_30d_matrix.xlsx.stale_20261008183446") == "AMZN_LONG"
    assert P.symside_of_sheet("XLE_LONG_30d_matrix_jump.xlsx.bak") == "XLE_LONG"
    assert P.symside_of_sheet("RSRUSDT_SHORT_30d_matrix.tmp.3515088.1790209397599539902") == "RSRUSDT_SHORT"
    assert P.symside_of_sheet("LONGUSDT_LONG_30d_matrix.xlsx") == "LONGUSDT_LONG"
    assert P.symside_of_sheet("ALONG_LONG_30d_matrix.xlsx") == "ALONG_LONG"
    assert P.symside_of_sheet("OBSOLETE_SNDK_E_FAKE_20260913.txt") is None
    assert P.symside_of_sheet("1000FLOKIUSDT_LONG_bh15p84_gain37p_t156_30d_matrix_manifest.json") == "1000FLOKIUSDT_LONG"


def test_attribution_logs():
    assert P.symside_of_log("sweep_AMZN_LONG_30D.log") == "AMZN_LONG"
    assert P.symside_of_log("sweep_A_LONG_30D.log") == "A_LONG"
    assert P.symside_of_log("random.log") is None


def test_strip_suffixes():
    assert P.strip_suffixes("A_LONG_30d_matrix.xlsx.stale_20261008183446") == "A_LONG_30d_matrix.xlsx"
    assert P.strip_suffixes("A_LONG_30d_matrix.xlsx.bak") == "A_LONG_30d_matrix.xlsx"
    assert P.strip_suffixes("A_LONG_30d_matrix.tmp.1.2") == "A_LONG_30d_matrix"
    assert P.strip_suffixes("A_LONG_30d_matrix.xlsx") == "A_LONG_30d_matrix.xlsx"


def test_final_gain_grep(tmp_path):
    yes = tmp_path / "a.json"
    yes.write_text('{"symside": "A_LONG", "done": {}, "final_gain": 8.1}')
    assert P.file_has_final_gain(yes) is True
    neg = tmp_path / "b.json"
    neg.write_text('{"final_gain": -2.5}')
    assert P.file_has_final_gain(neg) is True
    no = tmp_path / "c.json"
    no.write_text('{"symside": "A_LONG", "done": {}}')
    assert P.file_has_final_gain(no) is False
    assert P.file_has_final_gain(tmp_path / "missing.json") is False


def test_final_gain_chunk_boundary(tmp_path):
    target = tmp_path / "big.json"
    key = b'"final_gain": 1.5'
    target.write_bytes(b"x" * 100 + key + b"y" * 100)
    assert P.file_has_final_gain(target, chunk_size=7) is True
    plain = tmp_path / "plain.json"
    plain.write_bytes(b"z" * 100)
    assert P.file_has_final_gain(plain, chunk_size=7) is False


def _write_progress(path, finalized):
    payload = {"symside": path.stem, "done": {}}
    if finalized:
        payload["final_gain"] = 3.3
    path.write_text(json.dumps(payload))


def test_finalized_cache_invalidates_on_rewrite(tmp_path):
    progress_dir = tmp_path / "progress"
    progress_dir.mkdir()
    target = progress_dir / "A_LONG_v14_progress.json"
    _write_progress(target, False)
    cache_path = tmp_path / "cache.json"
    finalized, cache = P.load_finalized([progress_dir], progress_dir, cache_path)
    assert finalized == set()
    _write_progress(target, True)
    finalized, _ = P.load_finalized([progress_dir], progress_dir, cache_path)
    assert finalized == {"A_LONG"}


def test_finalized_current_round_override(tmp_path):
    current = tmp_path / "run29" / "progress"
    old = tmp_path / "run28" / "progress"
    current.mkdir(parents=True)
    old.mkdir(parents=True)
    _write_progress(old / "A_LONG_v14_progress.json", True)
    _write_progress(current / "A_LONG_v14_progress.json", False)
    _write_progress(old / "B_SHORT_v14_progress.json", True)
    finalized, _ = P.load_finalized([current, old], current, tmp_path / "cache.json")
    assert "A_LONG" not in finalized
    assert "B_SHORT" in finalized


def _layout(root):
    live_pd = root / "live" / "progress"
    live_sheets = root / "live" / "sheets"
    live_logs = root / "live" / "logs"
    live_pd.mkdir(parents=True)
    live_sheets.mkdir(parents=True)
    live_logs.mkdir(parents=True)
    dump = root / "stalled_s1_202610082210"
    (dump / "progress").mkdir(parents=True)
    (dump / "sheets").mkdir(parents=True)
    (dump / "logs").mkdir(parents=True)
    return live_pd, live_sheets, live_logs, dump


def _prune(dump, finalized, live_pd, live_sheets, live_logs, progress_dirs, old_enough=False, dry_run=False):
    index = P.live_sheet_index(live_sheets)
    return P.prune_dump(dump, finalized, live_pd, live_sheets, live_logs, index, progress_dirs, old_enough, dry_run)


def test_prune_dump_finalized_only(tmp_path):
    live_pd, live_sheets, live_logs, dump = _layout(tmp_path)
    _write_progress(live_pd / "A_LONG_v14_progress.json", True)
    _write_progress(live_pd / "B_SHORT_v14_progress.json", False)
    (live_sheets / "A_LONG_30d_matrix.xlsx").write_text("live")
    (live_logs / "sweep_A_LONG_30D.log").write_text("live")
    (dump / "progress" / "A_LONG_v14_progress.json").write_text("{}")
    (dump / "progress" / "B_SHORT_v14_progress.json").write_text("{}")
    (dump / "sheets" / "A_LONG_30d_matrix.xlsx").write_text("x")
    (dump / "sheets" / "B_SHORT_30d_matrix.xlsx").write_text("x")
    (dump / "logs" / "sweep_A_LONG_30D.log").write_text("x")
    (dump / "logs" / "sweep_B_SHORT_30D.log").write_text("x")
    removed_files, removed_bytes, backstop = _prune(dump, {"A_LONG"}, live_pd, live_sheets, live_logs, [live_pd])
    assert (removed_files, backstop) == (3, 0)
    assert removed_bytes > 0
    assert not (dump / "progress" / "A_LONG_v14_progress.json").exists()
    assert (dump / "progress" / "B_SHORT_v14_progress.json").exists()
    assert not (dump / "sheets" / "A_LONG_30d_matrix.xlsx").exists()
    assert (dump / "sheets" / "B_SHORT_30d_matrix.xlsx").exists()
    assert not (dump / "logs" / "sweep_A_LONG_30D.log").exists()
    assert (dump / "logs" / "sweep_B_SHORT_30D.log").exists()


def test_prune_dump_requires_live_counterpart(tmp_path):
    live_pd, live_sheets, live_logs, dump = _layout(tmp_path)
    (dump / "progress" / "A_LONG_v14_progress.json").write_text("{}")
    (dump / "sheets" / "A_LONG_30d_matrix.xlsx").write_text("x")
    (dump / "logs" / "sweep_A_LONG_30D.log").write_text("x")
    assert _prune(dump, {"A_LONG"}, live_pd, live_sheets, live_logs, [live_pd])[0] == 0
    assert (dump / "progress" / "A_LONG_v14_progress.json").exists()
    assert (dump / "sheets" / "A_LONG_30d_matrix.xlsx").exists()
    assert (dump / "logs" / "sweep_A_LONG_30D.log").exists()


def test_prune_dump_unattributed_supersedes(tmp_path):
    live_pd, live_sheets, live_logs, dump = _layout(tmp_path)
    old_copy = dump / "progress" / "endgame_knowledge.json"
    old_copy.write_text("{}")
    live_newer = live_pd / "endgame_knowledge.json"
    live_newer.write_text('{"v": 2, "pad": "...."}')
    assert _prune(dump, set(), live_pd, live_sheets, live_logs, [live_pd])[0] == 1
    assert not old_copy.exists()


def test_prune_dump_unattributed_without_live_is_kept(tmp_path):
    live_pd, live_sheets, live_logs, dump = _layout(tmp_path)
    orphan = dump / "progress" / "endgame_knowledge.json"
    orphan.write_text("{}")
    assert _prune(dump, set(), live_pd, live_sheets, live_logs, [live_pd])[0] == 0
    assert orphan.exists()


def test_prune_dump_dry_run_deletes_nothing(tmp_path):
    live_pd, live_sheets, live_logs, dump = _layout(tmp_path)
    (live_sheets / "A_LONG_30d_matrix.xlsx").write_text("live")
    target = dump / "sheets" / "A_LONG_30d_matrix.xlsx"
    target.write_text("x")
    removed_files, _, _ = _prune(dump, {"A_LONG"}, live_pd, live_sheets, live_logs, [live_pd], dry_run=True)
    assert removed_files == 1
    assert target.exists()


def test_prune_dump_backstop_old_verified_sheets_only(tmp_path):
    live_pd, live_sheets, live_logs, dump = _layout(tmp_path)
    (live_sheets / "OLD_LONG_30d_matrix.xlsx").write_text("live")
    (dump / "sheets" / "OLD_LONG_30d_matrix.xlsx").write_text("x")
    (dump / "sheets" / "GONE_SHORT_30d_matrix.xlsx").write_text("x")
    (dump / "progress" / "OLD_LONG_v14_progress.json").write_text("{}")
    removed_files, _, backstop = _prune(dump, set(), live_pd, live_sheets, live_logs, [live_pd], old_enough=True)
    assert removed_files == 1
    assert backstop == 1
    assert not (dump / "sheets" / "OLD_LONG_30d_matrix.xlsx").exists()
    assert (dump / "sheets" / "GONE_SHORT_30d_matrix.xlsx").exists()
    assert (dump / "progress" / "OLD_LONG_v14_progress.json").exists()


def test_prune_dump_backstop_tmp_expires(tmp_path):
    live_pd, live_sheets, live_logs, dump = _layout(tmp_path)
    stale_tmp = dump / "sheets" / "OLD_LONG_30d_matrix.tmp.3515088.1790209397599539902"
    stale_tmp.write_text("x")
    removed_files, _, backstop = _prune(dump, set(), live_pd, live_sheets, live_logs, [live_pd], old_enough=True)
    assert (removed_files, backstop) == (1, 1)
    assert not stale_tmp.exists()


def test_prune_dump_backstop_needs_age(tmp_path):
    live_pd, live_sheets, live_logs, dump = _layout(tmp_path)
    (live_sheets / "OLD_LONG_30d_matrix.xlsx").write_text("live")
    target = dump / "sheets" / "OLD_LONG_30d_matrix.xlsx"
    target.write_text("x")
    assert _prune(dump, set(), live_pd, live_sheets, live_logs, [live_pd], old_enough=False)[0] == 0
    assert target.exists()


def test_dump_status_terminal_rules(tmp_path):
    live_pd, _, _, dump = _layout(tmp_path)
    (dump / "progress").mkdir(exist_ok=True)
    assert P.dump_status(dump, [live_pd])[0] == 0
    (dump / "progress" / "A_LONG_v14_progress.json").write_text("{}")
    _write_progress(live_pd / "A_LONG_v14_progress.json", False)
    left, terminal = P.dump_status(dump, [live_pd])
    assert (left, terminal) == (1, True)
    (dump / "progress" / "MISSING_SHORT_v14_progress.json").write_text("{}")
    assert P.dump_status(dump, [live_pd])[1] is False


def test_main_end_to_end(tmp_path, monkeypatch, capsys):
    live_pd, live_sheets, live_logs, dump = _layout(tmp_path)
    _write_progress(live_pd / "A_LONG_v14_progress.json", True)
    (live_sheets / "A_LONG_30d_matrix.xlsx").write_text("live")
    (dump / "sheets" / "A_LONG_30d_matrix.xlsx").write_text("x")
    pointer = tmp_path / "v15_current_progress_dir.txt"
    pointer.write_text(str(live_pd))
    monkeypatch.setattr(P, "HOME", tmp_path)
    monkeypatch.setattr(P, "POINTER", pointer)
    monkeypatch.setattr(P, "SHEETS_DIR", live_sheets)
    monkeypatch.setattr(P, "LIVE_LOGS", live_logs)
    monkeypatch.setattr(P, "CACHE_PATH", tmp_path / "cache.json")
    old_mtime = (dump / "sheets" / "A_LONG_30d_matrix.xlsx").stat().st_mtime - 7200
    os.utime(dump, (old_mtime, old_mtime))
    assert P.main(["prog", "--min-age-min", "60"]) == 0
    assert not dump.exists()
    out = capsys.readouterr().out
    assert "TOTAL: 1 files" in out
