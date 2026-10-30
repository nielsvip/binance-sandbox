"""fin opens nothing unless a staged 7D result authorizes the men sym_side (USER 2026-10-30).

The rule lives on two surfaces and this pins both:
  * run_with_watchdog.sh — with no staged 7D db it bans fin entries at the wire instead of unsetting VEC_LIVE_SCOPE
    and falling through to ordinary 30D entries, which was the one hole in the 2026-10-26 design.
  * order_dedupe_guard.entry_banned — the wire gate every futures_create_order passes, so the ban cannot be routed
    around by any ez_manage code path, and EXITS are never affected.
"""
import importlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def guard(banned):
    os.environ["ODG_ENTRY_BANNED_ACCOUNTS"] = banned
    import order_dedupe_guard
    return importlib.reload(order_dedupe_guard)


def test_the_env_ban_stops_fin_and_leaves_the_other_crypto_accounts_alone():
    g = guard("fin")
    assert g.entry_banned("fin") is True
    for acct in ("men", "ang", "flz", "inf"):
        assert g.entry_banned(acct) is False, f"{acct} must keep trading"


def test_tra_stays_banned_whatever_the_env_says():
    g = guard("")
    assert g.entry_banned("tra") is True, "tra is exits-only forever, not a config switch"
    assert g.entry_banned("fin") is False, "without the env fin is NOT banned — the launcher must set it"


def test_the_ban_is_entry_only_and_names_no_exit_action():
    g = guard("fin")
    source = Path(g.__file__).read_text()
    hits = [line for line in source.splitlines() if "entry_banned(" in line and "def " not in line]
    assert hits, "entry_banned must be consulted somewhere"
    for line in hits:
        assert not re.search(r"\b(close|reduce|exit)\b", line, re.I), f"the ban must never gate an exit: {line.strip()}"


def test_the_launcher_bans_fin_when_no_7d_db_is_staged():
    body = (ROOT / "run_with_watchdog.sh").read_text()
    block = body.split('if [[ "$SCRIPT" == "ez_manage.py"', 1)[1].split("log \"Starting", 1)[0]
    armed, bare = block.split("else", 1)
    assert "VEC_LIVE_SCOPE=7d" in armed
    assert "ODG_ENTRY_BANNED_ACCOUNTS" in bare and "fin" in bare, "the no-stage branch must ban fin entries"
    assert "unset VEC_LIVE_SCOPE" in bare


def test_the_launcher_still_parses():
    assert subprocess.run(["bash", "-n", str(ROOT / "run_with_watchdog.sh")], capture_output=True).returncode == 0


def test_ez_manage_gate_is_scoped_to_the_7d_lane_so_the_launcher_must_carry_the_ban():
    """Why the launcher change is required: the in-script fin gate only runs under VEC_LIVE_SCOPE=7d."""
    body = (ROOT / "ez_manage.py").read_text()
    line = next(l for l in body.splitlines() if "BLOCKED_FIN_7D_SIDE_NOT_AUTHORIZED" in l or "fin_side_authorized" in l)
    assert line
    gate = next(l for l in body.splitlines() if 'account_key == "fin"' in l and "VEC_LIVE_SCOPE" in l)
    assert 'VEC_LIVE_SCOPE", "30d"' in gate and '"7d"' in gate
