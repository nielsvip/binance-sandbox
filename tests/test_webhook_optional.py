"""2026-10-04 USER: FINANDY BUST — webhook creds no longer required.

AccountConfig in service + quick must construct with Binance API_KEY/SECRET
only; Binance keys remain mandatory (ValueError when missing).
"""
import os

import pytest


def _scrub_webhook(monkeypatch):
    for v in [k for k in os.environ if "WEBHOOK" in k]:
        monkeypatch.delenv(v, raising=False)


def test_service_account_config_no_webhook_ok(monkeypatch):
    monkeypatch.setenv("ang_API_KEY", "fake")
    monkeypatch.setenv("ang_API_SECRET", "fake")
    _scrub_webhook(monkeypatch)
    from ez_positions_service import AccountConfig

    cfg = AccountConfig(prefix="ang")
    assert bool(cfg.api_key)
    assert cfg.webhook_url is None


def test_quick_account_config_no_webhook_ok(monkeypatch):
    monkeypatch.setenv("ang_API_KEY", "fake")
    monkeypatch.setenv("ang_API_SECRET", "fake")
    _scrub_webhook(monkeypatch)
    from ez_positions_quick import AccountConfig

    cfg = AccountConfig(prefix="ang")
    assert bool(cfg.api_secret)
    assert cfg.webhook_secret is None


def test_service_account_config_still_requires_binance_keys(monkeypatch):
    monkeypatch.delenv("ang_API_KEY", raising=False)
    monkeypatch.delenv("ANG_API_KEY", raising=False)
    monkeypatch.setenv("ang_API_SECRET", "fake")
    _scrub_webhook(monkeypatch)
    from ez_positions_service import AccountConfig

    with pytest.raises(ValueError, match="ang_API_KEY"):
        AccountConfig(prefix="ang")
