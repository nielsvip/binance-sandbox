import sys
sys.path.insert(0, "/Users/niels/Documents/binance")
import json, pathlib
from v12_wide_engine import sweep_config_for_mode, load_npz, simulate_one_symbol
import metrics_guard

ledger_path = pathlib.Path("/Users/niels/Documents/binance/data/reports/gui_lab/next_gen_beam_per_sym.json")
j = json.loads(ledger_path.read_text())
ledger = j.get("ledger", [])
print(f"ledger {len(ledger)}")
