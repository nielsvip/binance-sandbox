"""vector_parity_sweep — per-sym_side live-vs-vector switch parity (USER 2026-10-21).

Live-effective chain (ez_manage._psym_get): sqlite per_sym_store overrides >
cat_side defaults > global config. Compares sqlite live rows against
per_sym_full_sets.json (which itself derives FROM the db) on the exit+hold
enabler set. NOTE the direction: this guards JSON<->DB drift + missing rows,
NOT pilot-vs-live — for that, compare the db against the CURRENT campaign's
progress cumulative_overrides (older progress files are superseded, and the
pilot only publishes on beating live, so a raw diff overstates drift).
  MISSING  sym_side has no sqlite row (live runs defaults/cat_side)
  MISMATCH sqlite live value != json value (mid-rebuild or hand edit)
  (DEFDIFF live-global vs QuickConfig defaults: audited 2026-10-21, all equal.)

Usage: python3 tools/vector_parity_sweep.py [--switch NAME] [--sym KEY]
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SWITCHES = [
    "VEL_EXIT_ENABLED",
    "VEC_VEL_EXIT_AS_TRIGGER",
    "EXIT_VELOCITY_WT_ENABLED",
    "EXIT_VELOCITY_WT_TFS",
    "EXIT_VELOCITY_WT_MIN_TFS",
    "EXIT_VELOCITY_WT_MIN_HOLD_ENABLED",
    "STRUCTURAL_EXIT_GATE_ENABLED",
    "BBKC_EXIT_ENABLED",
    "BBKC_EXIT_TF",
    "BB_RECOVERY_EXIT_ENABLED_TRADIER",
    "TECHNICAL_DC_TARGET_TF",
    "TECHNICAL_DC_STOP_TF",
    "TECHNICAL_DC_TARGET_BUFFER_PCT",
    "VEC_EXIT_SIG_IS_TRIGGER",
    "RULE_B_3M_EXIT_ENABLED",
    "MIN_HOLD_BARS",
    "MIN_HOLD_BARS_BEFORE_EXIT",
    "NOLOSS_ENABLED",
    "EXIT_TOP_FADE_FILTER_TF",
    "CANDLE_PATTERN_STOPS_FILTER_TF",
    "BASE_TF",
    "DC_PRIOR_BAR_CHANNEL",
]


def _norm(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off", "none", ""):
            return False
        return v.strip()
    return v


def main(argv):
    only_switch = argv[argv.index("--switch") + 1] if "--switch" in argv else ""
    only_sym = argv[argv.index("--sym") + 1] if "--sym" in argv else ""
    import per_sym_store as pss

    full = json.load(open(ROOT / "data" / "per_sym_full_sets.json", encoding="utf-8"))
    syms = sorted(full.keys())
    if only_sym:
        syms = [k for k in syms if k == only_sym]
    switches = [s for s in SWITCHES if not only_switch or s == only_switch]
    n_missing, n_mismatch = 0, 0
    for key in syms:
        row = full.get(key, {})
        best = row.get("full_config", row)
        live = pss.get_overrides(key)
        if live is None:
            print(f"MISSING {key}: no sqlite row")
            n_missing += 1
            continue
        for sw in switches:
            if sw not in best:
                continue
            b, l = _norm(best.get(sw)), _norm(live.get(sw, "<absent>"))
            if live.get(sw, "<absent>") == "<absent>":
                print(f"NOSWITCH {key} {sw}: pilot-best={b!r} live=<absent>")
                n_mismatch += 1
            elif b != l:
                print(f"MISMATCH {key} {sw}: pilot-best={b!r} live={l!r}")
                n_mismatch += 1
    print(f"syms={len(syms)} missing_rows={n_missing} switch_gaps={n_mismatch}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
