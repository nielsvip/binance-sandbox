"""Contract tests for tools/data_locations.py — the registry that answers "where does this data live".

USER 2026-10-17: "MAKE A SYSTEM that understands WHERE THINGS ARE (klines, market data, positions info, npz
files) AND CAN NEVER FUCK UP ON SIMPLE ISSUES LIKE THIS".

These tests encode the three mistakes that produced the module, so they cannot come back silently:
  1. `failover_stage` is NOT under the base tree and IS decided by $LF_STAGE at runtime. A diagnostic that looked
     under <base>/data/live_failover/stage reported a working delivery as broken.
  2. An unknown kind must RAISE, never return a plausible-looking default.
  3. Every canonical path declared for the host we are running on must actually exist.
No ssh, no network: these run anywhere in under a second.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import data_locations as DL  # noqa: E402


def test_every_kind_declares_at_least_one_host():
    for kind, hosts in DL.REGISTRY.items():
        assert hosts, f"{kind} declares no hosts"
        for host, entries in hosts.items():
            assert entries, f"{kind}/{host} declares no paths"
            for path_t, role, _note, _env in entries:
                assert path_t and not path_t.endswith("/"), f"{kind}/{host}: bad path template {path_t!r}"
                assert role in ("canonical", "mirror", "archive", "staging", "legacy"), f"{kind}/{host}: role {role!r}"


def test_first_declared_entry_is_the_authoritative_one():
    """where() must return the FIRST declared candidate, which is the registry's ordering contract."""
    for kind, hosts in DL.REGISTRY.items():
        for host in hosts:
            first = DL.resolve(kind, host)[0]
            assert DL.where(kind, host).path == first.path, f"{kind}/{host}: where() disagrees with ordering"


def test_unknown_kind_raises_instead_of_guessing():
    with pytest.raises(DL.LocationError):
        DL.where("klines_banana", "mac")
    with pytest.raises(DL.LocationError):
        DL.resolve("not_a_kind", "mac")


def test_failover_stage_is_not_under_the_base_tree():
    """THE bug this module was written for: LF_STAGE defaults OUTSIDE LF_BASE, so <base>/.../stage is wrong."""
    loc = DL.where("failover_stage", "mac")
    assert not loc.path.startswith(DL.MAC_BASE), (
        f"failover_stage resolved inside the base tree ({loc.path}); the real default is "
        "/Users/niels/live_failover_stage and looking under the base tree is exactly the mistake this guards"
    )
    assert loc.env == "LF_STAGE"


def test_env_override_wins_for_every_kind_that_declares_one(monkeypatch):
    """A kind whose location is decided at runtime must resolve from the env FIRST, not from the default."""
    checked = 0
    for kind, hosts in DL.REGISTRY.items():
        for host, entries in hosts.items():
            envs = {e for *_rest, e in ((a, b, c, d) for a, b, c, d in entries) if e}
            for env in envs:
                monkeypatch.setenv(env, "/tmp/__loc_override__")
                got = DL.where(kind, host)
                assert got.path == "/tmp/__loc_override__", f"{kind}/{host}: ${env} did not win (got {got.path})"
                assert got.from_env is True
                monkeypatch.delenv(env, raising=False)
                checked += 1
    assert checked >= 5, f"expected several env-driven kinds, only checked {checked}"


def test_account_expansion_covers_every_live_account():
    """Positions are per-account files, not a data/positions dir — the registry's first version guessed wrong."""
    crypto = {Path(l.path).name for l in DL.resolve("positions_crypto", "mac")}
    assert crypto == set(DL.CRYPTO_ACCOUNTS), crypto
    stocks = {Path(l.path).name for l in DL.resolve("positions_stocks", "mac")}
    assert stocks == set(DL.STOCKS_ACCOUNTS), stocks


def test_npz_registry_includes_the_archive_dirs_that_held_the_deepest_copies():
    """2026-10-17: the deepest NPZ stores sat in _quarantine and .purged_npz while live was 5x shallower."""
    paths = " ".join(l.path for l in DL.resolve("npz_indicators", "s1"))
    assert "_quarantine" in paths, "quarantine must be searchable — it held 482 stores vs 332 live"
    assert ".purged_npz" in paths, "purge dirs must be searchable — they held the deepest copy of 62 symbols"


@pytest.mark.skipif(sys.platform != "darwin", reason="local-host canonical check runs on the Mac")
def test_every_canonical_mac_path_exists():
    """If this fails, either the data moved or the registry is lying. Both are bugs worth failing on."""
    missing = []
    for kind, hosts in DL.REGISTRY.items():
        for loc in DL.resolve(kind, "mac") if "mac" in hosts else []:
            if loc.role != "canonical":
                continue
            if not Path(loc.path).exists():
                missing.append(f"{kind}: {loc.path}")
    assert not missing, "canonical paths declared but absent:\n  " + "\n  ".join(missing)


def test_cli_where_and_unknown_kind_exit_codes():
    py = sys.executable
    ok = subprocess.run([py, str(ROOT / "tools" / "data_locations.py"), "--where", "npz_indicators", "--host", "s1"],
                        capture_output=True, text=True, timeout=60)
    assert ok.returncode == 0 and ok.stdout.strip().endswith("backtest_v8/indicators"), ok.stdout
    bad = subprocess.run([py, str(ROOT / "tools" / "data_locations.py"), "--where", "nope", "--host", "mac"],
                         capture_output=True, text=True, timeout=60)
    assert bad.returncode != 0, "an unknown kind must be a non-zero exit, not a silent empty answer"
