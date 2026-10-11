"""Regression: tradeable_keys retention max 2 days (PERSIST 48h), ang should not be 100."""
import json
import pathlib
import time


def test_persist_is_48h_max():
    src = pathlib.Path("config.py").read_text()
    # PERSIST should be 48.0 for 2 days max
    assert "PERSIST = 48" in src, "PERSIST should be 48.0 (2 days max)"
    # No account should exceed 48h
    for line in src.splitlines():
        if line.strip().startswith("PERSIST"):
            if "=" in line:
                val = line.split("=")[1].split("#")[0].strip()
                try:
                    f = float(val)
                    assert f <= 48.0, f"{line.strip()} exceeds 48h"
                except ValueError:
                    pass


def test_tradeable_keys_ang_not_100():
    tk = json.loads(pathlib.Path("tradeable_keys.json").read_text())
    import collections
    c = collections.Counter([k.split(":")[0] for k in tk if ":" in k])
    assert c["ang"] <= 60, f"ang tradeable_keys should be way less than 100 after 2d prune, got {c['ang']}"
    # USER 2026-10-11: ceiling reconciled to configured universe (was <220 at 144-key universe 2026-09-29). Config base = men 50*2 + fin 31*2 + flz 13*2 + ang 29+16 + inf 23+11 = 267 keys; 310 = base + persistence/winners slack. Deliberate expansion toward 300 tradeable, not prune failure.
    assert len(tk) <= 310, f"total tradeable should be <=310 (267 configured + slack), got {len(tk)}"


def test_tradeable_keys_generated_inEzPositionsService():
    src = pathlib.Path("ez_positions_service.py").read_text()
    assert "tradeable_keys" in src
    assert "universe_persistence" in src
    assert "retention_seconds" in src or "PERSIST" in src
