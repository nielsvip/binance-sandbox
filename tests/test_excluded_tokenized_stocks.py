import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import excluded_tokenized_stocks as X  # noqa: E402
import v15_universe as U  # noqa: E402


def _root(tmp_path, syms=("AAPLUSDT", "VUSDT", "BEUSDT")):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data" / "excluded_tokenized_stocks.json").write_text(json.dumps({"symbols": list(syms)}))
    return tmp_path


def test_real_deny_file_has_core_equities_and_not_crypto():
    d = json.loads((ROOT / "data" / "excluded_tokenized_stocks.json").read_text())
    s = set(d["symbols"])
    for k in ("AAPLUSDT", "NVDAUSDT", "TSLAUSDT", "COINUSDT", "MSTRUSDT", "SPYUSDT", "QQQUSDT", "HOODUSDT"):
        assert k in s and k in d["evidence"]
    for k in ("BTCUSDC", "ETHUSDC", "ARUSDT", "WUSDT", "SNXUSDT", "COMPUSDT", "AAPL", "NVDA"):
        assert k not in s
    assert all(v["contractType"] == "TRADIFI_PERPETUAL" for v in d["evidence"].values())


def test_is_denied_forms(tmp_path):
    r = _root(tmp_path)
    for x in ("AAPLUSDT", "aaplusdt", "AAPLUSDT_LONG", "ang:AAPLUSDT_SHORT", "VUSDT_LONG"):
        assert X.is_denied(x, r), x
    for x in ("AAPL", "AAPL_LONG", "BTCUSDC_LONG", "AVUSDT", "1000BEUSDT", "WUSDT"):
        assert not X.is_denied(x, r), x
    assert X.filter_syms(["AAPLUSDT", "AAPL", "BTCUSDC", "flz:VUSDT_LONG"], r) == ["AAPL", "BTCUSDC"]


def test_missing_deny_file_is_noop(tmp_path):
    assert X.load_denied(tmp_path) == frozenset()
    assert X.filter_syms(["AAPLUSDT"], tmp_path) == ["AAPLUSDT"]


def test_name_hits_token_bounded(tmp_path):
    r = _root(tmp_path)
    assert X.name_hits("AAPLUSDT_LONG_30d_matrix.xlsx", r) == "AAPLUSDT"
    assert X.name_hits("VUSDT.npz", r) == "VUSDT"
    assert X.name_hits("vusdt_15m.json", r) == "VUSDT"
    assert X.name_hits("AVUSDT.npz", r) is None
    assert X.name_hits("AAPL_LONG_30d_matrix.xlsx", r) is None
    assert X.name_hits("1000BEUSDT_1h.json", r) is None


def test_enforce_strips_lists_and_keys(tmp_path):
    r = _root(tmp_path)
    (r / "symbols_ang_long.json").write_text(json.dumps(["AAPLUSDT", "BTCUSDC"]))
    (r / "symbols_trb_long.json").write_text(json.dumps(["AAPLUSDT"]))  # NEVER EDITABLE -> untouched
    (r / "tradeable_keys.json").write_text(json.dumps(["ang:AAPLUSDT_LONG", "ang:BTCUSDC_LONG"]))
    (r / "data" / "v15_sched_pinned_sides.json").write_text(json.dumps({"VUSDT_LONG": 1, "ETHUSDC_LONG": 1}))
    rep = X.enforce(r, apply=True, backup_dir=tmp_path / "bk")
    assert json.loads((r / "symbols_ang_long.json").read_text()) == ["BTCUSDC"]
    assert json.loads((r / "symbols_trb_long.json").read_text()) == ["AAPLUSDT"]
    assert json.loads((r / "tradeable_keys.json").read_text()) == ["ang:BTCUSDC_LONG"]
    assert json.loads((r / "data" / "v15_sched_pinned_sides.json").read_text()) == {"ETHUSDC_LONG": 1}
    assert len(list((tmp_path / "bk").iterdir())) == 3 and len(rep) == 3
    assert X.enforce(r, apply=False) == {}


def test_archive_moves_with_manifest(tmp_path):
    r = _root(tmp_path)
    src = tmp_path / "work"
    (src / "npz").mkdir(parents=True)
    (src / "npz" / "AAPLUSDT.npz").write_bytes(b"x")
    (src / "npz" / "BTCUSDC.npz").write_bytes(b"x")
    (src / "AAPLUSDT").mkdir()
    (src / "AAPLUSDT" / "k.json").write_text("[]")
    (src / "AAPLUSDT_tool.py").write_text("")  # code never moved
    m = X.archive([src], tmp_path / "arch", apply=True, root_for_deny=r)
    assert m["count"] == 2 and not (src / "npz" / "AAPLUSDT.npz").exists() and (src / "npz" / "BTCUSDC.npz").exists()
    assert not (src / "AAPLUSDT").exists() and (src / "AAPLUSDT_tool.py").exists()
    assert json.loads((tmp_path / "arch" / "manifest.json").read_text())["count"] == 2


def test_universe_deny_beats_open_positions(tmp_path):
    r = _root(tmp_path)
    (r / "tradeable_keys.json").write_text(json.dumps(["ang:AAPLUSDT_LONG", "ang:BTCUSDC_LONG"]))
    (r / "data" / "open_position_sym_sides.json").write_text(json.dumps({"sym_sides": ["VUSDT_LONG", "ETHUSDC_SHORT"]}))
    allowed = U.tradeable_sym_sides(r)
    assert allowed == {"BTCUSDC_LONG", "ETHUSDC_SHORT"}
    u = U._deny_universe({"allowed_sym_sides": ["AAPLUSDT_LONG", "AAPL_LONG"], "crypto": ["AAPLUSDT", "BTCUSDC"], "crypto_lists": {"AAPLUSDT": ["LONG"]}}, r)
    assert u["allowed_sym_sides"] == ["AAPL_LONG"] and u["crypto"] == ["BTCUSDC"] and u["crypto_lists"] == {}


def test_scheduler_deny_helper():
    import v15_fleet_scheduler as S
    assert S._deny_tokenized(["AAPLUSDT", "BTCUSDC", "AAPL"]) == ["BTCUSDC", "AAPL"]
