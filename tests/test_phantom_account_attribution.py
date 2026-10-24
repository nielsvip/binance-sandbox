"""Phantom key_* attribution (2026-10-24 USER: BROKEN POSITIONS MANAGEMENT spam).

Raw (unwrapped) python-binance clients order through the class guard, which
names them key_<sha1(API_KEY)[:10]>. In a STRICT single-account process that
phantom identity compares a fossil key_*/ positions file (no writer anywhere)
against the live broker -> an unhealable BROKEN alarm on every order, plus a
second dedupe ledger for one real account. Measured live: ang/flz/fin each
grew a stable key_* twin (1:1 process mapping), e.g. flz orders landing in
key_6317ff02b7 while flz/* files stayed writer-kept.

Single-registered-account processes must attribute the unknown key to that
account; multi-account processes keep the legacy fingerprint.
"""
import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("TRADIER_API_LOG_DIR", "/tmp/tradier_api_test_logs")
sys.path.insert(0, str(ROOT))
import order_dedupe_guard as odg  # noqa: E402


def _fp(key: str) -> str:
    return "key_" + hashlib.sha1(key.encode()).hexdigest()[:10]


@pytest.fixture
def isolated_map(monkeypatch):
    saved = dict(odg._BINANCE_KEY_ACCOUNTS)
    odg._BINANCE_KEY_ACCOUNTS.clear()
    monkeypatch.setenv("ODG_SINGLE_ACCOUNT_ATTRIBUTION", "1")
    yield odg._BINANCE_KEY_ACCOUNTS
    odg._BINANCE_KEY_ACCOUNTS.clear()
    odg._BINANCE_KEY_ACCOUNTS.update(saved)


def test_single_registered_account_claims_unknown_key(isolated_map):
    isolated_map["LIVEKEY_FLZ"] = "flz"
    raw = SimpleNamespace(API_KEY="LIVEKEY_FLZ_HELPER")
    assert odg._binance_account_for(raw) == "flz"
    assert isolated_map["LIVEKEY_FLZ_HELPER"] == "flz"


def test_multi_registered_accounts_keep_fingerprint(isolated_map):
    isolated_map["LIVEKEY_FLZ"] = "flz"
    isolated_map["LIVEKEY_ANG"] = "ang"
    raw = SimpleNamespace(API_KEY="LIVEKEY_FLZ_HELPER")
    assert odg._binance_account_for(raw) == _fp("LIVEKEY_FLZ_HELPER")
    assert "LIVEKEY_FLZ_HELPER" not in isolated_map


def test_kill_switch_restores_fingerprint(isolated_map, monkeypatch):
    monkeypatch.setenv("ODG_SINGLE_ACCOUNT_ATTRIBUTION", "0")
    isolated_map["LIVEKEY_FLZ"] = "flz"
    raw = SimpleNamespace(API_KEY="LIVEKEY_FLZ_HELPER")
    assert odg._binance_account_for(raw) == _fp("LIVEKEY_FLZ_HELPER")


def test_empty_key_never_claimed(isolated_map):
    isolated_map["LIVEKEY_FLZ"] = "flz"
    assert odg._binance_account_for(SimpleNamespace(API_KEY="")) == _fp("")
    assert "" not in isolated_map


def test_wrapped_client_identity_wins(isolated_map):
    isolated_map["LIVEKEY_FLZ"] = "flz"
    wrapped = SimpleNamespace(_odg_account="flz", API_KEY="ANYTHING_ELSE")
    assert odg._binance_account_for(wrapped) == "flz"


def test_known_key_still_resolves(isolated_map):
    isolated_map["LIVEKEY_FLZ"] = "flz"
    raw = SimpleNamespace(API_KEY="LIVEKEY_FLZ")
    assert odg._binance_account_for(raw) == "flz"
