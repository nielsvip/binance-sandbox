"""AUTOPSY-INERT row pruning helpers (USER 2026-10-08): rotating 1/N sample + effective-switch ingest from the start base."""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _helpers():
    src = (ROOT / "v15_pilot.py").read_text()
    m = re.search(r"\ndef inert_row_sampled\(.*?\n(?=\ndef _zero_enabled)", src, re.S)
    assert m, "helpers missing from v15_pilot.py"
    ns = {"json": json, "os": __import__("os")}
    exec(m.group(0), ns)
    return ns["inert_row_sampled"], ns["inert_switches_from_start"]


def test_rotating_sample_covers_every_row_once_per_cycle():
    sampled, _ = _helpers()
    every = 25
    for rr in range(3, 300):
        hits = [seq for seq in range(every) if sampled(rr, "ENTRY_REVERSAL_BOUNCE", seq, every)]
        assert len(hits) == 1, (rr, hits)
    # per round about 1/every of the rows are full
    n = sum(1 for rr in range(3, 1003) if sampled(rr, "EXIT_VELOCITY", 7, every))
    assert 30 <= n <= 50, n
    assert sampled(10, "X", 3, 1) and sampled(11, "X", 3, 0)


def test_effective_switches_from_base(tmp_path):
    _, from_start = _helpers()
    p = tmp_path / "b.json"
    p.write_text(json.dumps({"final": {"overrides": {"A": 1}}, "autopsy": {"effective_switches": ["EXIT_VELOCITY_WT_MIN_TFS", "MOM3_FILTER_TF=4h"]}}))
    assert from_start(str(p)) == {"EXIT_VELOCITY_WT_MIN_TFS", "MOM3_FILTER_TF"}
    p.write_text(json.dumps({"final": {"overrides": {"A": 1}}}))
    assert from_start(str(p)) == set()  # no autopsy knowledge -> no pruning
    assert from_start("") == set() and from_start("/nonexistent") == set()


def test_pilot_wiring_present():
    src = (ROOT / "v15_pilot.py").read_text()
    assert src.count("AUTOPSY-INERT-REVIVED") >= 2
    assert '"inert_filters": st.get("inert_filters") or []' in src
    assert "V15_INERT_SAMPLE_EVERY" in src and "V15_INERT_PRUNE" in src
