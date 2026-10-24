"""Loop-stall fixes (2026-10-24 USER unlock+fix; SentimentMonitor cadence kept).

The event loop averaged 10.5 s blocks (3,400+ stall dumps): the top on-loop
offenders were sync Manager IPC in IndicatorsBridge._maintain_connection_loop
and the per-2 s data-dir glob+sort in SnapshotLoader. SentimentMonitor was
measured innocent (already off-loop via to_thread, 0.06 s parse) so its 5 s
cadence — which feeds per-scan velocity math — is intentionally unchanged.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ez_manage as em  # noqa: E402


def test_reconnect_delay_backs_off_to_cap():
    assert em._reconnect_delay_s(0) == 1.0
    assert em._reconnect_delay_s(1) == 2.0
    assert em._reconnect_delay_s(2) == 4.0
    assert em._reconnect_delay_s(5) == 30.0
    assert em._reconnect_delay_s(99) == 30.0
    assert em._reconnect_delay_s("junk") == 1.0
    assert em._reconnect_delay_s(-3) == 1.0


def _loader(tmp_path):
    return em.SnapshotLoader(SimpleNamespace(), tmp_path)


def test_pick_target_file_newest_name_and_static_fallback(tmp_path):
    loader = _loader(tmp_path)
    assert loader._pick_target_file() is None
    (tmp_path / "market_data_20240101.json").write_text("{}")
    (tmp_path / "market_data_20240202.json").write_text("{}")
    pick = loader._pick_target_file()
    assert pick is not None and pick.name == "market_data_20240202.json"
    (tmp_path / "market_data_20240303.json").write_text("{}")
    assert loader._pick_target_file().name == "market_data_20240303.json"
    for p in tmp_path.glob("market_data_*.json"):
        p.unlink()
    (tmp_path / "latest_market_data.json").write_text("{}")
    assert loader._pick_target_file().name == "latest_market_data.json"


def test_pick_target_file_skips_glob_when_dir_unchanged(tmp_path, monkeypatch):
    loader = _loader(tmp_path)
    (tmp_path / "market_data_20240101.json").write_text("{}")
    assert loader._pick_target_file().name == "market_data_20240101.json"
    calls = []
    orig_glob = Path.glob

    def counting_glob(self, *a, **k):
        calls.append(1)
        return orig_glob(self, *a, **k)

    monkeypatch.setattr(Path, "glob", counting_glob)
    assert loader._pick_target_file().name == "market_data_20240101.json"
    assert calls == []
