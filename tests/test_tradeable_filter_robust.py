"""Single-bad-read robustness for the crypto tradeable filter (USER 2026-10-10:
one bad read must never zero crypto filtering — 300 sym_side/day depends on it).

load_account_syms() in per_sym_crypto_profiler.py + per_sym_real_profiler.py:
  1. one corrupt per_sym entry is skipped, the rest of the filter stands;
  2. one unreadable source file -> the other side carries the filter;
  3. BOTH sources unreadable -> fail OPEN (test the file universe, loud warning),
     never an empty universe;
  4. normal filtering still drops non-tradeable syms (no compute waste).
"""
import json

import per_sym_crypto_profiler as cp
import per_sym_real_profiler as rp


def _stage(root, tk=None, ps=None, long_syms=None, short_syms=None, corrupt_tk=False, corrupt_ps=False, corrupt_long=False):
    (root / "data" / "hourly_reconfig").mkdir(parents=True, exist_ok=True)
    (root / "symbols_ang_long.json").write_text("{{{BAD" if corrupt_long else json.dumps(long_syms or ["AAAUSDT", "BBBUSDT", "CCCUSDT"]))
    (root / "symbols_ang_short.json").write_text(json.dumps(short_syms or ["DDDUSDT"]))
    if corrupt_tk:
        (root / "tradeable_keys.json").write_text("{nope")
    elif tk is not None:
        (root / "tradeable_keys.json").write_text(json.dumps(tk))
    if corrupt_ps:
        (root / "data" / "hourly_reconfig" / "per_sym_active_config.json").write_text("[bad")
    elif ps is not None:
        (root / "data" / "hourly_reconfig" / "per_sym_active_config.json").write_text(json.dumps(ps))


PS_GOOD = {"BBBUSDT_LONG": {"trades": 50}, "DDDUSDT_SHORT": {"trades": 40}}
PS_ONE_BAD = {"BBBUSDT_LONG": {"trades": 50}, "DDDUSDT_SHORT": {"trades": 40}, "ZZZUSDT_LONG": {"trades": "CORRUPT"}}
TK = ["ang:AAAUSDT_LONG"]


def test_crypto_normal_filter(tmp_path, monkeypatch, capsys):
    _stage(tmp_path, tk=TK, ps=PS_GOOD)
    monkeypatch.setattr(cp, "ROOT", tmp_path)
    got = cp.load_account_syms("ang")
    assert "AAAUSDT" in got and "BBBUSDT" in got and "DDDUSDT" in got
    assert "CCCUSDT" not in got  # non-tradeable still dropped


def test_crypto_one_bad_entry_keeps_filter(tmp_path, monkeypatch, capsys):
    _stage(tmp_path, tk=TK, ps=PS_ONE_BAD)
    monkeypatch.setattr(cp, "ROOT", tmp_path)
    got = cp.load_account_syms("ang")
    assert "BBBUSDT" in got and "DDDUSDT" in got and "AAAUSDT" in got
    assert "CCCUSDT" not in got
    assert "corrupt entries" in capsys.readouterr().out


def test_crypto_one_bad_file_other_side_carries(tmp_path, monkeypatch, capsys):
    _stage(tmp_path, tk=TK, ps=PS_GOOD, corrupt_tk=True)
    monkeypatch.setattr(cp, "ROOT", tmp_path)
    got = cp.load_account_syms("ang")
    assert "BBBUSDT" in got and "DDDUSDT" in got  # per_sym side stands
    assert "CCCUSDT" not in got


def test_crypto_both_dead_fails_open(tmp_path, monkeypatch, capsys):
    _stage(tmp_path, corrupt_tk=True, corrupt_ps=True)
    monkeypatch.setattr(cp, "ROOT", tmp_path)
    got = cp.load_account_syms("ang")
    assert got, "fail-open must never return an empty universe"
    assert set(got) == {"AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"}
    assert "filter OPEN" in capsys.readouterr().out


def test_crypto_missing_files_fail_open(tmp_path, monkeypatch, capsys):
    _stage(tmp_path)  # no tk, no ps at all
    monkeypatch.setattr(cp, "ROOT", tmp_path)
    got = cp.load_account_syms("ang")
    assert len(got) == 4


def _stage_npz(root, monkeypatch):
    npz = root / "npz"
    npz.mkdir(exist_ok=True)
    for s in ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"):
        (npz / f"{s}.npz").write_bytes(b"fake")
    monkeypatch.setattr(rp, "NPZ_DIR", npz)


def test_real_one_bad_entry_keeps_filter(tmp_path, monkeypatch, capsys):
    _stage(tmp_path, tk=TK, ps=PS_ONE_BAD)
    _stage_npz(tmp_path, monkeypatch)
    monkeypatch.setattr(rp, "ROOT", tmp_path)
    got = rp.load_account_syms("ang")
    assert "BBBUSDT" in got and "DDDUSDT" in got and "AAAUSDT" in got
    assert "CCCUSDT" not in got


def test_real_both_dead_fails_open(tmp_path, monkeypatch, capsys):
    _stage(tmp_path, corrupt_tk=True, corrupt_ps=True)
    _stage_npz(tmp_path, monkeypatch)
    monkeypatch.setattr(rp, "ROOT", tmp_path)
    got = rp.load_account_syms("ang")
    assert len(got) == 4


def test_real_corrupt_symfile_does_not_crash(tmp_path, monkeypatch, capsys):
    _stage(tmp_path, tk=TK, ps=PS_GOOD, corrupt_long=True)
    _stage_npz(tmp_path, monkeypatch)
    monkeypatch.setattr(rp, "ROOT", tmp_path)
    got = rp.load_account_syms("ang")  # must not raise; short file still loads
    assert "DDDUSDT" in got
    assert "parse error" in capsys.readouterr().out
