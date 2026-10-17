#!/bin/bash
# env_parity_report.sh — USER 2026-10-17: "compare the binance folder venv with the conda one all versions of all pip
# installs make sure we can point everything to conda".
#
# Answers one question per host: if every launcher were pointed at the conda live env, would anything be LOST?
# Three classes, only the first can block:
#   MISSING  — installed in the venv, absent from conda. A blocker ONLY if code actually imports it; the report says.
#   OLDER    — conda has an older version than the venv. Not a blocker, but it is a downgrade; listed so it is a
#              decision and not an accident. The operator's standing preference is the newest reliable numpy.
#   NEWER    — conda is ahead. Listed for completeness.
# Read-only: installs nothing, changes nothing.
#
#   bash tools/env_parity_report.sh            # both hosts
#   bash tools/env_parity_report.sh s1-pub     # one host
set -u
HOSTS=${*:-"s1-pub s2"}
SSH="ssh -o BatchMode=yes -o ConnectTimeout=20 -o StrictHostKeyChecking=accept-new"
CONDA=/home/niels/.conda/envs/binance_env_mac/bin/python

for H in $HOSTS; do
    echo "################ $H ################"
    $SSH "$H" "CONDA=$CONDA; "'
for E in "binance/.venv:/home/niels/binance/.venv/bin/python" "binance-sandbox/.venv:/home/niels/binance-sandbox/.venv/bin/python"; do
  LABEL=${E%%:*}; P=${E#*:}
  [ -x "$P" ] || { echo "=== $LABEL: not present on this host ==="; continue; }
  [ -x "$CONDA" ] || { echo "=== $LABEL: conda env MISSING — cannot point here ==="; continue; }
  "$P" -m pip list --format=json > /tmp/_ep_venv.json 2>/dev/null
  "$CONDA" -m pip list --format=json > /tmp/_ep_conda.json 2>/dev/null
  "$CONDA" - "$LABEL" <<PYEOF
import json, subprocess, sys
label = sys.argv[1]
norm = lambda n: n.lower().replace("_", "-")
venv  = {norm(p["name"]): p["version"] for p in json.load(open("/tmp/_ep_venv.json"))}
conda = {norm(p["name"]): p["version"] for p in json.load(open("/tmp/_ep_conda.json"))}
def ver(s):
    out = []
    for part in s.replace("-", ".").split("."):
        out.append(int(part) if part.isdigit() else part)
    return out
missing, older, newer, same = [], [], [], 0
for k, v in sorted(venv.items()):
    if k not in conda: missing.append((k, v)); continue
    c = conda[k]
    if c == v: same += 1
    else:
        try: older.append((k, v, c)) if ver(c) < ver(v) else newer.append((k, v, c))
        except Exception: older.append((k, v, c))
print(f"=== {label}  ({len(venv)} pkgs)  vs conda binance_env_mac ({len(conda)} pkgs) ===")
print(f"  identical: {same}   MISSING from conda: {len(missing)}   conda OLDER: {len(older)}   conda NEWER: {len(newer)}")
if missing:
    print("  MISSING (blocker only if imported):")
    for k, v in missing:
        # is it actually used anywhere outside the venvs?
        mod = k.replace("-", "_")
        r = subprocess.run(["grep","-rl","-e",f"import {mod}","-e",f"from {mod}","/home/niels/binance-sandbox","--include=*.py"], capture_output=True, text=True)
        hits = [l for l in r.stdout.splitlines() if "/.venv/" not in l]
        tag = " -> BLOCKER" if hits else " -> unused, safe"
        print(f"    {k:<16} venv={v:<12} imported by {len(hits)} file(s){tag}")
for title, rows in (("conda OLDER (downgrade if pointed)", older), ("conda NEWER", newer)):
    if rows:
        print(f"  {title}:")
        for k, v, c in rows: print(f"    {k:<20} venv={v:<14} conda={c}")
blockers = 0
for k, v in missing:
    mod = k.replace("-", "_")
    r = subprocess.run(["grep","-rl","-e",f"import {mod}","-e",f"from {mod}","/home/niels/binance-sandbox","--include=*.py"], capture_output=True, text=True)
    if [l for l in r.stdout.splitlines() if "/.venv/" not in l]: blockers += 1
verdict = "CAN point everything to conda (no blocker)" if blockers == 0 else f"{blockers} BLOCKER(S) — install into conda first"
print(f"  VERDICT: {verdict}")
PYEOF
done'
done
