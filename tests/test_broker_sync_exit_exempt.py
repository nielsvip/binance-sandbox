"""BROKER_SYNC_DEMAND must never strand exits (2026-10-02 fin:ETHUSDT_LONG bled
-13% ROE with every CLOSE refused 17:35-22:30 UTC: the manager's own frozen
positions_last_sync shadowed the fresh service ts, and CLOSE had no exemption)."""
from datetime import datetime, timedelta, timezone

import ez_manage as em


def test_exit_actions_recognized():
    assert em._broker_sync_is_exit("CLOSE") is True
    assert em._broker_sync_is_exit("REDUCE") is True
    assert em._broker_sync_is_exit("close") is True
    assert em._broker_sync_is_exit("OPEN") is False
    assert em._broker_sync_is_exit("AUGMENT") is False
    assert em._broker_sync_is_exit(None) is False


def test_freshest_ts_wins_over_frozen_manager_ts():
    now = datetime.now(timezone.utc)
    frozen_mgr = now - timedelta(seconds=17000)
    fresh_svc = now - timedelta(seconds=12)
    assert em._broker_sync_freshest_ts(frozen_mgr, None, fresh_svc) == fresh_svc
    assert em._broker_sync_freshest_ts(None, None, None) is None
    assert em._broker_sync_freshest_ts(frozen_mgr, None, None) == frozen_mgr


def test_freshest_ts_accepts_naive():
    now = datetime.now(timezone.utc)
    naive = (now - timedelta(seconds=5)).replace(tzinfo=None)
    got = em._broker_sync_freshest_ts(naive)
    assert got is not None and got.tzinfo is not None


def test_execute_now_source_exempts_exits_from_stale_refusal():
    import pathlib

    src = pathlib.Path("ez_manage.py").read_text()
    assert "if _age > 90 and not _is_exit_bs:" in src
    assert "exits never blocked" in src
