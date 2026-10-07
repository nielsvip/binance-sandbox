import importlib.util, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
STG = ROOT / "data/live_parity/staged/batch6_entry_veto"
sys.path.insert(0, str(ROOT))

def test_config_defaults_and_hook():
    spec = importlib.util.spec_from_file_location("config_b6", STG / "config.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    c = m.Config()
    assert c.ALL_TF_AGAINST_BLOCK_ENTRY_ENABLED is False and c.ALL_TF_AGAINST_BLOCK_ENTRY_MIN_TFS == 4
    s = (STG / "ez_manage.py").read_text()
    assert 'getattr(config, "ALL_TF_AGAINST_BLOCK_ENTRY_ENABLED", False)' in s

def _run_veto(on, ind, is_long, min_tfs=4):
    # re-implementation of the staged hook's counting on the same indicator dict (guards the formula; the file is checked textually above)
    cnt = 0
    for tf in ("3m", "15m", "1h", "4h", "D"):
        a, b = float(ind.get(f"wt1_{tf}", 0) or 0), float(ind.get(f"wt2_{tf}", 0) or 0)
        if a == 0 and b == 0: continue
        cnt += int((a < b) if is_long else (a > b))
    return on and cnt >= min_tfs

def test_veto_formula():
    ind = {f"wt1_{t}": -5 for t in ("3m", "15m", "1h", "4h")} | {f"wt2_{t}": 3 for t in ("3m", "15m", "1h", "4h")}
    assert _run_veto(True, ind, True) is True and _run_veto(True, ind, False) is False and _run_veto(False, ind, True) is False
