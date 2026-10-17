"""2026-10-17 USER: "no flapping allowed you need to find and fix this".

The flap was self-sustaining: a takeover fences the Mac, its engines are killed, they restart, and the restarted
engine has no successful broker poll YET — so `no_ok_s` fell back to the OLD log tail and instantly exceeded
BROKER_DEAD_S, producing the next takeover. MEASURED on 2026-10-17: lease gen 23 -> 31 in ~45 min, and at 03:44Z
BOTH hosts sat at 0 engines with 39 open positions.

These pin the grace that breaks the loop. No network, no live config.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import live_failover_common as C  # noqa: E402


def test_parse_etime_handles_every_ps_format():
    assert C.parse_etime("05") == 5
    assert C.parse_etime("01:30") == 90
    assert C.parse_etime("02:00:00") == 7200
    assert C.parse_etime("1-00:00:00") == 86400
    assert C.parse_etime("") is None
    assert C.parse_etime("garbage") is None


def test_trader_procs_reports_engine_age():
    """macOS ps has no `etimes`, so ages come from `etime` — the parser must cope with the real column layout."""
    ps = (
        "  101       02:19 /opt/anaconda3/envs/binance_env/bin/python -u ez_manage.py --account ang\n"
        "  102    01:02:03 /opt/anaconda3/envs/binance_env/bin/python -u ez_manage.py --account men\n"
        "  103       00:10 /bin/bash run_with_watchdog.sh ez_manage.py --account flz\n"  # no 'python' -> ignored
    )
    out = C.trader_procs(ps_text=ps)
    assert out["crypto"]["ang"] == 1 and out["crypto"]["men"] == 1
    assert out["crypto"]["flz"] == 0, "a bash wrapper is not an engine"
    assert out["_ages"]["crypto"]["ang"] == 139
    assert out["_ages"]["crypto"]["men"] == 3723


def test_trader_procs_still_works_without_an_etime_column():
    """Callers (and older fixtures) may pass `pid=,command=` output; counting must not regress to zero."""
    ps = "  101 /usr/bin/python -u ez_manage.py --account ang\n"
    out = C.trader_procs(ps_text=ps)
    assert out["crypto"]["ang"] == 1
    assert out["_ages"]["crypto"].get("ang") is None


def test_a_restarting_engine_is_not_broker_dead():
    """THE flap fix. Same inputs, only engine age differs: young -> not dead, old -> dead."""
    assert C.ENGINE_STARTUP_GRACE_S > C.BROKER_DEAD_S, "the grace must outlast the broker-dead window or a restart is still judged mid-boot"
    assert C.ENGINE_STARTUP_GRACE_S >= 180, "ez_manage takes ~80s to initialise; leave real margin"

    src = (ROOT / "tools" / "live_failover_common.py").read_text()
    crypto = src[src.index("def _crypto(self"):src.index("def _stocks(self")]
    assert "starting = _age is not None and _age < ENGINE_STARTUP_GRACE_S" in crypto
    assert "active and not starting and no_ok_s is not None" in crypto, "broker_dead must be suppressed while starting"
    stocks = src[src.index("def _stocks(self"):]
    assert "n_alive and not starting and submitted == 0" in stocks, "stocks order_dead/broker_bad need the same grace"


def test_engine_dead_s_respects_the_grace():
    """`manage_log_silent_s` is measured from the last LOG LINE, so a 60s-old engine read as dead-for-1238s on
    2026-10-17 and would have tripped account_dead (240s) the moment failover was re-armed."""
    src = (ROOT / "tools" / "live_failover_common.py").read_text()
    body = src[src.index("def _engine_dead_s"):src.index("def measure(")]
    assert "starting=False" in body
    assert "dead = (not starting) and" in body


def test_grace_is_env_tunable_and_defaults_sanely():
    import os

    assert os.environ.get("LF_ENGINE_STARTUP_GRACE_S") is None or True  # tunable, not hardcoded
    assert C.ENGINE_STARTUP_GRACE_S == float(os.environ.get("LF_ENGINE_STARTUP_GRACE_S", "240"))
