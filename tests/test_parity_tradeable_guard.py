"""TRADEABLE_KEYS guard: poison detection + last-good restore (USER 2026-10-10 no-touch order)."""
import json
from tools.forward_parity import live_guardian as lg


class _NoRedis:
    def run(self, *a, **k):
        class R:
            stdout = ""
        return R()


def _mkroot(tmp_path, monkeypatch, keys, open_pos=None):
    monkeypatch.setattr(lg, "ROOT", tmp_path)
    monkeypatch.setattr(lg.subprocess, "run", _NoRedis().run)
    (tmp_path / "tradeable_keys.json").write_text(json.dumps(keys))
    for acct, poss in (open_pos or {}).items():
        (tmp_path / acct).mkdir(exist_ok=True)
        (tmp_path / acct / "long_positions.json").write_text(json.dumps({k: {"positionAmt": v} for k, v in poss.items()}))
        (tmp_path / acct / "short_positions.json").write_text(json.dumps({}))
    g = lg.Guardian(dry=False)
    g.alert = lambda *a, **k: g.alerts.append(a[0])
    return g


def test_healthy_file_stores_last_good(tmp_path, monkeypatch):
    keys = [f"men:SYM{i}_LONG" for i in range(60)]
    g = _mkroot(tmp_path, monkeypatch, keys, {"men": {"men:SYM1_LONG": 5}})
    out = g.check_tradeable_keys()
    assert out["poison"] is False
    assert len(g.st["tradeable_last_good"]) == 60


def test_zeroed_account_with_open_restores(tmp_path, monkeypatch):
    good = [f"men:SYM{i}_LONG" for i in range(60)] + [f"fin:A{i}_LONG" for i in range(60)]
    g = _mkroot(tmp_path, monkeypatch, good, {"men": {"men:SYM1_LONG": 5}})
    assert g.check_tradeable_keys()["poison"] is False
    bad = [f"fin:A{i}_LONG" for i in range(60)]
    (tmp_path / "tradeable_keys.json").write_text(json.dumps(bad))
    monkeypatch.setattr(lg, "broker_open_keys", lambda acct, ttl_s=300.0: {"men:SYM1_LONG"})
    out = g.check_tradeable_keys()
    assert out["poison"] is True
    assert out["restored"] is True
    assert "TRADEABLE_KEYS_POISONED" in g.alerts
    assert len(json.loads((tmp_path / "tradeable_keys.json").read_text())) == 120


def test_missing_open_key_flags_poison(tmp_path, monkeypatch):
    keys = [f"men:SYM{i}_LONG" for i in range(60)]
    g = _mkroot(tmp_path, monkeypatch, keys, {"men": {"men:OTHER_LONG": 5}})
    monkeypatch.setattr(lg, "broker_open_keys", lambda acct, ttl_s=300.0: {"men:OTHER_LONG"})
    out = g.check_tradeable_keys()
    assert out["poison"] is True


def test_stale_tracker_missing_key_silent(tmp_path, monkeypatch):
    keys = [f"men:SYM{i}_LONG" for i in range(60)]
    g = _mkroot(tmp_path, monkeypatch, keys, {"men": {"men:STALE_SHORT": 5}})
    monkeypatch.setattr(lg, "broker_open_keys", lambda acct, ttl_s=300.0: set())
    out = g.check_tradeable_keys()
    assert out["poison"] is False


def test_broker_unverified_silent(tmp_path, monkeypatch):
    keys = [f"men:SYM{i}_LONG" for i in range(60)]
    g = _mkroot(tmp_path, monkeypatch, keys, {"men": {"men:OTHER_LONG": 5}})
    monkeypatch.setattr(lg, "broker_open_keys", lambda acct, ttl_s=300.0: None)
    out = g.check_tradeable_keys()
    assert out["poison"] is False
    assert out["broker_unverified"] == ["men"]


def test_collapsed_total_flags_poison(tmp_path, monkeypatch):
    g = _mkroot(tmp_path, monkeypatch, ["men:A_LONG", "fin:B_SHORT"])
    out = g.check_tradeable_keys()
    assert out["poison"] is True
