"""No 1/3/5m trade may execute while the master kill switch is off (USER 2026-10-15:
"AND THAT NO 1/3/5M TRADE CAN EVER BE EXECUTED UNTIL THE SWITCH IS BACK ON").

WHY THIS IS A CONTRACT TEST AND NOT A CONFIG DEFAULT: `_cfg()` (stocks) and `_psym_get()` (crypto) resolve the
per_sym layer FIRST. On 2026-10-15 the live books carried `RULE_B_5M_EXIT_ENABLED=True` on **222/222 stock and
325/325 crypto** rows, and `WT_CROSSUNDER_FINAL_ENABLED=True` on 218/222 and 322/325 — so flipping the class default
changed nothing live, and 40 of that day's exit reasons were WT_CROSSOVER/CROSSUNDER_FINAL firing on a hardcoded 5m
leg the vector never evaluates (`vec_decisions/twin_entry_ports_b.py:415` has no engine call site; the 15m NPZ has no
`wt1_5m`/`k_5m`). The block therefore lives ABOVE per_sym, inside the resolvers themselves.

The helpers are extracted and exec'd rather than imported: importing ez_manage has hung on module-level service
connects before (see the 2026-10-14 share_ind incident), and a test must not be able to wedge a live box.
"""
from __future__ import annotations

import re
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VENUES = (
    ("tradier_manage.py", "SUB15M_BLOCKED_SWITCHES", "_sub15m_blocked", "_SUB15M_LOGGED"),
    ("ez_manage.py", "SUB15M_BLOCKED_SWITCHES", "_sub15m_blocked_ez", "_SUB15M_LOGGED_EZ"),
)
# must never be blocked: 15m and higher, plus the switch that REQUIRES 15m confirmation (blocking it would loosen)
NEVER_BLOCK = (
    "WT_15M_BOUNCE_OPEN_ENABLED",
    "WT_CROSS_EXIT_ENABLED",
    "WT_LOWER_CROSS_EXIT_TF",
    "WT_CROSS_EXIT_REQUIRE_15M_CONFIRM",
    "EXIT_R1_R2_FILTER_TF",
)


def _load(fname, dict_name, fn_name, log_name):
    src = (ROOT / fname).read_text()
    d = re.search(rf"^{dict_name} = \{{(.*?)^\}}", src, re.S | re.M)
    f = re.search(rf"^def {fn_name}\(.*?(?=\n\ndef |\n\n# |\nclass )", src, re.S | re.M)
    assert d, f"{fname}: {dict_name} not found — the sub-15m block was removed or renamed"
    assert f, f"{fname}: {fn_name} not found — the sub-15m block was removed or renamed"
    ns = {
        "config": types.SimpleNamespace(LTF_SUB15M_TRADING_ENABLED=False),
        "logger": types.SimpleNamespace(critical=lambda *a, **k: None),
        log_name: set(),
    }
    exec(d.group(0) + "\n" + f.group(0), ns, ns)
    return ns[dict_name], ns[fn_name], ns


@pytest.mark.parametrize("fname,dict_name,fn_name,log_name", VENUES)
def test_every_listed_switch_is_blocked_when_master_off(fname, dict_name, fn_name, log_name):
    D, F, ns = _load(fname, dict_name, fn_name, log_name)
    assert D, f"{fname}: block list is empty"
    unblocked = [k for k in D if not F(k)]
    assert not unblocked, f"{fname}: master OFF but these are not blocked: {unblocked}"


@pytest.mark.parametrize("fname,dict_name,fn_name,log_name", VENUES)
def test_blocked_value_is_the_no_action_value(fname, dict_name, fn_name, log_name):
    D, _F, _ns = _load(fname, dict_name, fn_name, log_name)
    bad = {k: v for k, v in D.items() if v is not False}
    assert not bad, f"{fname}: a blocked sub-15m switch must be forced to False (no action), got {bad}"


@pytest.mark.parametrize("fname,dict_name,fn_name,log_name", VENUES)
def test_master_on_restores_sub15m(fname, dict_name, fn_name, log_name):
    D, F, ns = _load(fname, dict_name, fn_name, log_name)
    ns["config"].LTF_SUB15M_TRADING_ENABLED = True
    still = [k for k in D if F(k)]
    assert not still, f"{fname}: master ON must restore sub-15m, still blocked: {still}"


@pytest.mark.parametrize("fname,dict_name,fn_name,log_name", VENUES)
def test_15m_and_higher_switches_are_untouched(fname, dict_name, fn_name, log_name):
    D, F, _ns = _load(fname, dict_name, fn_name, log_name)
    for k in NEVER_BLOCK:
        assert k not in D, f"{fname}: {k} is 15m+ (or requires 15m confirmation) and must not be in the block list"
        assert F(k) is False, f"{fname}: {k} must not be blocked"


@pytest.mark.parametrize("fname,dict_name,fn_name,log_name", VENUES)
def test_fail_safe_blocks_when_config_unreadable(fname, dict_name, fn_name, log_name):
    """An unreadable config must BLOCK, never fall open — executing an unvectorised 1/3/5m trade is the harm."""
    D, F, ns = _load(fname, dict_name, fn_name, log_name)

    class Boom:
        def __getattr__(self, _n):
            raise RuntimeError("config unreadable")

    ns["config"] = Boom()
    ns[log_name] = set()
    assert F("RULE_B_5M_EXIT_ENABLED") is True, f"{fname}: must fail CLOSED when config cannot be read"


@pytest.mark.parametrize("fname,dict_name,fn_name,log_name", VENUES)
def test_block_is_checked_above_the_per_sym_lookup(fname, dict_name, fn_name, log_name):
    """Static guard: the block must be the FIRST thing the resolver does. If a per_sym read can happen first the
    block is defeated by exactly the 222/222 and 325/325 overrides that made this necessary."""
    src = (ROOT / fname).read_text()
    resolver = "_cfg(" if fname.startswith("tradier") else "_psym_get("
    m = re.search(rf"^def {re.escape(resolver)}".replace("\\(", r"\("), src, re.M)
    assert m, f"{fname}: resolver {resolver} not found"
    body = src[m.start():m.start() + 4000]
    gate = body.find(fn_name)
    per_sym = body.find("get_overrides")
    assert gate != -1, f"{fname}: resolver does not call {fn_name} — the block is not wired in"
    if per_sym != -1:
        assert gate < per_sym, f"{fname}: {fn_name} must be checked BEFORE the per_sym override lookup"
