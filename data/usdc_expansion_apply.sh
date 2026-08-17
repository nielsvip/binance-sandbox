#!/usr/bin/env bash
# usdc_expansion_apply.sh — APPENDS USDC pairs to tradeable_keys + symbols files.
# Generated 20260426. Per CLAUDE.md: APPENDS ONLY. No position dict touched.
# Source proposal: data/usdc_expansion_proposal_20260426.md

set -euo pipefail

ROOT="/Users/niels/Documents/binance"
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="${ROOT}/backups/usdc_expansion_${TS}"

mkdir -p "${BACKUP_DIR}"

echo "=========================================="
echo "USDC EXPANSION APPLY — ${TS}"
echo "Backup dir: ${BACKUP_DIR}"
echo "=========================================="

# 1) BACKUP all 8 files
for f in tradeable_keys.json symbols.json \
         symbols_fin.json symbols_flz.json symbols_men.json \
         symbols_ang_long.json symbols_ang_short.json \
         symbols_inf_long.json symbols_inf_short.json; do
  if [[ -f "${ROOT}/${f}" ]]; then
    cp "${ROOT}/${f}" "${BACKUP_DIR}/${f}"
    echo "BACKED UP: ${f}"
  else
    echo "WARN: missing ${f} — skip backup"
  fi
done

# 2) APPEND via Python (atomic load → mutate → dump). Skips duplicates.
"/opt/anaconda3/envs/binance_env/bin/python" - <<'PYEOF'
import json, os, sys

ROOT = "/Users/niels/Documents/binance"

# Per-account proposed pairs (derived from data/usdc_expansion_proposal_20260426.md)
PROPOSAL = {
    "fin": ["ETHUSDC","SOLUSDC","XRPUSDC","ORDIUSDC","SUIUSDC","PENGUUSDC","LINKUSDC",
            "ENAUSDC","FILUSDC","LTCUSDC","ARBUSDC","BIOUSDC","IPUSDC","NEARUSDC","TIAUSDC"],
    "ang": ["ETHUSDC","SOLUSDC","XRPUSDC","ORDIUSDC","1000PEPEUSDC","SUIUSDC","LINKUSDC",
            "FILUSDC","LTCUSDC","BIOUSDC","IPUSDC","NEARUSDC","TIAUSDC","HBARUSDC","1000SHIBUSDC"],
    "inf": ["ORDIUSDC","LINKUSDC","BIOUSDC","IPUSDC","WLFIUSDC","PNUTUSDC","NEOUSDC","KAITOUSDC"],
    "flz": ["XRPUSDC","DOGEUSDC","ORDIUSDC","SUIUSDC","PENGUUSDC"],
    "men": ["ETHUSDC","SOLUSDC","XRPUSDC","ORDIUSDC","PENGUUSDC"],
}

def load(p):
    with open(p) as f:
        return json.load(f)

def save(p, obj):
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, p)

stats = {}

# --- tradeable_keys.json (LONG + SHORT for each pair) ---
tk_path = os.path.join(ROOT, "tradeable_keys.json")
tk = load(tk_path)
tk_set = set(tk)
added_tk = 0
for acct, pairs in PROPOSAL.items():
    for sym in pairs:
        for side in ("LONG", "SHORT"):
            key = f"{acct}:{sym}_{side}"
            if key not in tk_set:
                tk.append(key)
                tk_set.add(key)
                added_tk += 1
save(tk_path, tk)
stats["tradeable_keys.json"] = f"+{added_tk} keys (total now {len(tk)})"

# --- symbols.json (global, distinct pairs only) ---
s_path = os.path.join(ROOT, "symbols.json")
s = load(s_path)
s_set = set(s)
added_s = 0
distinct_pairs = set()
for pairs in PROPOSAL.values():
    distinct_pairs.update(pairs)
for sym in sorted(distinct_pairs):
    if sym not in s_set:
        s.append(sym)
        s_set.add(sym)
        added_s += 1
save(s_path, s)
stats["symbols.json"] = f"+{added_s} pairs (total now {len(s)})"

# --- per-account symbols files ---
def append_pairs(rel, pairs):
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        print(f"WARN: missing {rel} — skip")
        return
    arr = load(p)
    if not isinstance(arr, list):
        print(f"WARN: {rel} is not a list — skip")
        return
    seen = set(arr)
    added = 0
    for sym in pairs:
        if sym not in seen:
            arr.append(sym)
            seen.add(sym)
            added += 1
    save(p, arr)
    stats[rel] = f"+{added} pairs (total now {len(arr)})"

append_pairs("symbols_fin.json", PROPOSAL["fin"])
append_pairs("symbols_flz.json", PROPOSAL["flz"])
append_pairs("symbols_men.json", PROPOSAL["men"])
# ang & inf split LONG/SHORT files (each pair gets BOTH sides on the allowlist)
append_pairs("symbols_ang_long.json", PROPOSAL["ang"])
append_pairs("symbols_ang_short.json", PROPOSAL["ang"])
append_pairs("symbols_inf_long.json", PROPOSAL["inf"])
append_pairs("symbols_inf_short.json", PROPOSAL["inf"])

print()
print("=== APPLY SUMMARY ===")
for k, v in stats.items():
    print(f"  {k}: {v}")

# Klines flag
KLINES_MISSING = ["BIOUSDC", "WLFIUSDC"]
print()
print("=== KLINES MISSING (fetch BEFORE backtests) ===")
for k in KLINES_MISSING:
    print(f"  - {k}: missing 15m/3m/D in klines_cache/")
PYEOF

# 3) VALIDATE — parse all 8 files + grep one canonical added key
echo
echo "=== VALIDATION ==="
"/opt/anaconda3/envs/binance_env/bin/python" -c "
import json
for f in ['tradeable_keys.json','symbols.json','symbols_fin.json','symbols_flz.json',
          'symbols_men.json','symbols_ang_long.json','symbols_ang_short.json',
          'symbols_inf_long.json','symbols_inf_short.json']:
    with open(f'/Users/niels/Documents/binance/{f}') as h:
        d = json.load(h)
    print(f'OK PARSE: {f} ({len(d)} entries)')
"

echo
echo "Sample new keys present in tradeable_keys.json:"
for K in "fin:ETHUSDC_LONG" "ang:1000PEPEUSDC_SHORT" "inf:KAITOUSDC_LONG" "flz:DOGEUSDC_SHORT" "men:PENGUUSDC_LONG"; do
  if grep -q "\"${K}\"" "${ROOT}/tradeable_keys.json"; then
    echo "  FOUND: ${K}"
  else
    echo "  MISSING: ${K}"
  fi
done

echo
echo "=========================================="
echo "DONE. To revert: bash data/usdc_expansion_revert.sh ${TS}"
echo "=========================================="
