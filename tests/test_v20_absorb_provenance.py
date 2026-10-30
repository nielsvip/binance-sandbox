import json
from types import SimpleNamespace

import pytest

from tools import v20_absorb_cycle as cycle


def test_provenance_uses_observed_event_time_and_normalizes_offsets():
    result = cycle.corpus_provenance({"per_cat_side": {"CRYPTO_LONG": {"last_ts": "2026-10-22T20:00:00Z"}, "STOCKS_LONG": {"last_ts": "2026-10-22T22:00:00+01:00"}, "STOCKS_SHORT": {"last_ts": "", "counters": {}}}})
    assert result["global_corpus_last_ts"] == "2026-10-22T21:00:00.000000Z"
    assert result["corpus_last_ts_by_cat_side"]["CRYPTO_LONG"] == "2026-10-22T20:00:00.000000Z"


@pytest.mark.parametrize("report", [{}, {"per_cat_side": {"CRYPTO_LONG": {"last_ts": "0000-00-00T00:00:00Z"}}}, {"per_cat_side": {"CRYPTO_LONG": {"last_ts": "2026-10-22T20:00:00"}}}, {"per_cat_side": {"CRYPTO_LONG": {"counters": {"rows": 4}}}}])
def test_unknown_event_time_refuses_provenance(report):
    with pytest.raises(ValueError):
        cycle.corpus_provenance(report)


@pytest.mark.parametrize("report,expected", [({}, False), ({"per_cat_side": {"CRYPTO_LONG": {"last_ts": "2026-10-22T20:00:00Z"}}}, True)])
def test_train_manifest_requires_report_provenance(monkeypatch, report, expected):
    calls = []
    def fake_sh(host, command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(report) if command.startswith("cat ") else "", stderr="")
    monkeypatch.setattr(cycle, "sh", fake_sh)
    monkeypatch.setattr(cycle, "free_mb", lambda host: cycle.TRAIN_MIN_FREE_MB + 1)
    monkeypatch.setattr(cycle, "log", lambda *args, **kwargs: None)
    result = cycle.train("s2", ["s1", "s2"])
    assert result["ok"] is expected
    writes = [command for command in calls if "MANIFEST_EOF" in command]
    assert bool(writes) is expected
    if expected:
        assert '"global_corpus_last_ts": "2026-10-22T20:00:00.000000Z"' in writes[0]
    else:
        assert result["step"] == "provenance"
