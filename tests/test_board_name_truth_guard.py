import json
import os
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import board_name_truth_guard as G  # noqa: E402

OLD = time.time() - 7200


def _page(trades, events, hdr_gain, hdr_peak):
    head = f'<div class="metrics"><b>X</b> gain {hdr_gain:.2f}% BH 1.00% | trades {len(trades)} | peak ${hdr_peak:.0f} score 1.0</div>'
    return f"<html><body>{head}<script>\nconst TRADES = {json.dumps(trades)};\nconst EVENTS = {json.dumps(events)};\n</script></body></html>"


def _ledger():
    # two trades of $200 notional each, sequential: pnl 10 + 15 (MTM row of 99 must be excluded) -> G = 25/200 = 12.5%
    trades = [
        {"type": "CLOSE", "ts": 2.0, "pnl_dollars": 10.0, "deployed": 200.0, "bar_entry": 1, "bar_exit": 2, "price": 2.05, "qty": 100.0},
        {"type": "CLOSE", "ts": 4.0, "pnl_dollars": 15.0, "deployed": 200.0, "bar_entry": 3, "bar_exit": 4, "price": 2.10, "qty": 100.0},
        {"type": "CLOSE", "ts": 6.0, "pnl_dollars": 99.0, "deployed": 200.0, "bar_entry": 5, "bar_exit": 6, "exit_reason": "FINAL_MTM window end", "unrealised": True},
    ]
    events = [{"type": "OPEN", "ts": 1.0, "price": 2.0, "qty": 100.0}, {"type": "OPEN", "ts": 3.0, "price": 2.0, "qty": 100.0}, {"type": "OPEN", "ts": 5.0, "price": 2.0, "qty": 100.0}]
    return trades, events


def _board(d: Path, stem: str, hdr_gain=12.5, hdr_peak=200.0):
    cell = d / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
    cell.mkdir(parents=True, exist_ok=True)
    tr, ev = _ledger()
    (cell / f"{stem}_30d_matrix.html").write_text(_page(tr, ev, hdr_gain, hdr_peak))
    (cell / f"{stem}_30d_matrix.xlsx").write_bytes(b"PK fake xlsx")
    (cell / f"{stem}_30d_matrix_manifest.json").write_text(json.dumps({"final_name": f"{stem}_30d_matrix.xlsx", "metrics": {"gain_pct": 466.7}}))
    for p in cell.iterdir():
        os.utime(p, (OLD, OLD))
    return cell


def _run(root, dry=False, min_age=600):
    G.LOG = Path(root) / "guard.log"
    return G.Guard([root], dry, min_age, Path(root) / "state.json").run()


def test_token_format():
    assert G.fmt_token(12.376) == "12p38"
    assert G.fmt_token(-3.149) == "m3p15"
    assert G.fmt_token(-0.001) == "0p00"


def test_page_truth_excludes_mtm_and_matches_header(tmp_path):
    cell = _board(tmp_path, "AAAUSDT_LONG_bh1p00_gain12p50_t2")
    t = G.page_truth(cell / "AAAUSDT_LONG_bh1p00_gain12p50_t2_30d_matrix.html")
    assert t["status"] == "OK" and abs(t["G"] - 12.5) < 1e-9 and t["mtm_rows_excluded"] == 1 and abs(t["peak"] - 200.0) < 1e-9


def test_correct_name_is_untouched(tmp_path):
    _board(tmp_path, "AAAUSDT_LONG_bh1p00_gain12p50_t2")
    res = _run(tmp_path)
    assert res["actions"] == [] and res["summary"]["ok"] == 1


def test_false_name_renamed_with_backup_and_manifest(tmp_path):
    cell = _board(tmp_path, "AAAUSDT_LONG_bh1p00_gain466p_t2")
    res = _run(tmp_path)
    names = sorted(p.name for p in cell.iterdir())
    assert names == ["AAAUSDT_LONG_bh1p00_gain12p50_t2_30d_matrix.html", "AAAUSDT_LONG_bh1p00_gain12p50_t2_30d_matrix.xlsx", "AAAUSDT_LONG_bh1p00_gain12p50_t2_30d_matrix_manifest.json"]
    man = json.loads((cell / "AAAUSDT_LONG_bh1p00_gain12p50_t2_30d_matrix_manifest.json").read_text())
    assert abs(man["name_truth"]["G"] - 12.5) < 1e-9 and man["name_truth"]["sum_pnl_dollars"] == 25.0 and man["name_truth"]["peak"] == 200.0
    assert man["final_name"] == "AAAUSDT_LONG_bh1p00_gain12p50_t2_30d_matrix.xlsx" and man["nolies_correction"]["claimed_gain_pct"] == 466.7
    tars = list((tmp_path / "backups" / "board_name_truth").glob("*.tar"))
    assert len(tars) == 1 and len(tarfile.open(tars[0]).getnames()) == 3
    assert len([a for a in res["actions"] if a["action"] == "RENAME"]) == 3
    assert _run(tmp_path)["actions"] == []  # idempotent


def test_header_mismatch_quarantines(tmp_path):
    cell = _board(tmp_path, "BBBUSDT_SHORT_bhm1p00_gain61p_t2", hdr_gain=61.5)  # header counted the MTM row: (25+99)/200
    res = _run(tmp_path)
    q = cell / G.QDIR
    assert sorted(p.name for p in q.iterdir()) == ["BBBUSDT_SHORT_bhm1p00_gain12p50_t2_30d_matrix.html", "BBBUSDT_SHORT_bhm1p00_gain12p50_t2_30d_matrix.xlsx", "BBBUSDT_SHORT_bhm1p00_gain12p50_t2_30d_matrix_manifest.json"]
    assert not any(p.is_file() for p in cell.iterdir()) and len(res["alerts"]) == 1
    assert _run(tmp_path)["actions"] == []


def test_resurrected_old_name_deleted_and_root_copy_renamed(tmp_path):
    cell = _board(tmp_path, "CCCUSDT_LONG_bh1p00_gain466p_t2")
    _run(tmp_path)
    root_copy = tmp_path / "SPREADSHEETS" / "CCCUSDT_LONG_bh1p00_gain466p_t2_30d_matrix.xlsx"
    root_copy.write_bytes(b"PK copy")
    resurrected = cell / "CCCUSDT_LONG_bh1p00_gain466p_t2_30d_matrix.xlsx"
    resurrected.write_bytes(b"PK fake xlsx")
    for p in (root_copy, resurrected):
        os.utime(p, (OLD, OLD))
    _run(tmp_path)
    assert not resurrected.exists() and not root_copy.exists()
    assert (tmp_path / "SPREADSHEETS" / "CCCUSDT_LONG_bh1p00_gain12p50_t2_30d_matrix.xlsx").exists()
    assert (cell / "CCCUSDT_LONG_bh1p00_gain12p50_t2_30d_matrix.xlsx").exists()


def test_dry_run_changes_nothing(tmp_path):
    cell = _board(tmp_path, "DDDUSDT_LONG_bh1p00_gain466p_t2")
    before = sorted(p.name for p in cell.iterdir())
    res = _run(tmp_path, dry=True)
    assert len(res["actions"]) == 3 and sorted(p.name for p in cell.iterdir()) == before and not (tmp_path / "backups").exists()


def test_young_and_tmp_files_left_alone(tmp_path):
    cell = _board(tmp_path, "EEEUSDT_LONG_bh1p00_gain466p_t2")
    os.utime(cell / "EEEUSDT_LONG_bh1p00_gain466p_t2_30d_matrix.html", None)
    (cell / "EEEUSDT_LONG_bh1p00_gain466p_t2_30d_matrix.fz.tmp.xlsx").write_bytes(b"x")
    res = _run(tmp_path)
    assert res["actions"] == [] and res["summary"]["young_skipped"] == 1


def test_peer_map_renames_copy_whose_page_lives_on_another_host(tmp_path):
    cell = tmp_path / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
    cell.mkdir(parents=True)
    orphan = cell / "FFFUSDT_LONG_bh1p00_gain466p_t2_30d_matrix.xlsx"
    orphan.write_bytes(b"PK")
    os.utime(orphan, (OLD, OLD))
    peers = tmp_path / "board_name_truth_peers"
    peers.mkdir()
    (peers / "s1.json").write_text(json.dumps({"map": {"FFFUSDT_LONG_bh1p00_gain466p_t2": {"new": "FFFUSDT_LONG_bh1p00_gain12p50_t2", "G": 12.5}}, "quarantined": {}}))
    res = _run(tmp_path)
    assert not orphan.exists() and (cell / "FFFUSDT_LONG_bh1p00_gain12p50_t2_30d_matrix.xlsx").exists() and res["summary"]["peer_maps"] == 1
    exported = json.loads((tmp_path / "board_name_truth_map.json").read_text())
    assert "FFFUSDT_LONG_bh1p00_gain466p_t2" not in exported["map"]


def test_cost_basis_peak_and_price_peak_pages():
    rows = [{"type": "OPEN", "ts": 1.0, "price": 1.0, "qty": 100.0}, {"type": "AUGMENT", "ts": 2.0, "price": 2.0, "qty": 100.0}, {"type": "CLOSE", "ts": 3.0, "pnl_dollars": 30.0, "bar_entry": 1, "bar_exit": 3}]
    pk = G.event_peaks(rows)
    assert abs(pk["cost"] - 300.0) < 1e-9 and abs(pk["price"] - 400.0) < 1e-9


def test_rerendered_consistent_page_is_unquarantined(tmp_path):
    cell = _board(tmp_path, "GGGUSDT_SHORT_bhm1p00_gain61p_t2", hdr_gain=61.5)
    _run(tmp_path)
    q = cell / G.QDIR
    tr, ev = _ledger()
    (q / "GGGUSDT_SHORT_bhm1p00_gain12p50_t2_30d_matrix.html").write_text(_page(tr, ev, 12.5, 200.0))
    os.utime(q / "GGGUSDT_SHORT_bhm1p00_gain12p50_t2_30d_matrix.html", (OLD, OLD))
    res = _run(tmp_path)
    assert res["summary"]["unquarantined_stems"] == 1 and not any(q.iterdir())
    assert sorted(p.name for p in cell.iterdir() if p.is_file()) == ["GGGUSDT_SHORT_bhm1p00_gain12p50_t2_30d_matrix.html", "GGGUSDT_SHORT_bhm1p00_gain12p50_t2_30d_matrix.xlsx", "GGGUSDT_SHORT_bhm1p00_gain12p50_t2_30d_matrix_manifest.json"]
    assert _run(tmp_path)["actions"] == []
