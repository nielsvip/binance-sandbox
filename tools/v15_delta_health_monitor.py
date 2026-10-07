#!/usr/bin/env python3
"""v15_delta_health_monitor — NO-LIES watchdog over the live sweep (USER 2026-09-29: monitor every cell
fill, flag any 0 or repeated delta immediately). Runs on the Mac via cron (*/10). For each server it reads
the newest fresh-run progress JSONs (~/v15_run1_20260929/progress) and flags, per sym_side:
  * ZERO-DELTA: every switch delta ~0 (dead NPZ / no-op stubs / DATA_ERROR) — see /tmp/sweep_{SS}.log
  * REPEATED-DELTA: one non-zero delta value repeated across many unrelated switches (the fabricated
    v12_quick_engine distinctness trap — memory v12_quick_engine_synthetic_distinctness) — NEVER promote.
Writes data/reports/delta_health_{YYYYMMDD}.md (append) so the operator (or the next agent) sees offenders.
Read-only; never edits sheets.
"""
import collections, datetime, json, subprocess, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOSTS = {"s1": "s1-int", "s5": "s5", "s2": "s2-fresh-int"}
ISO = "~/v15_run1_20260929/progress"
REMOTE = r'''
import json,glob,os,collections
rows=[]
files=sorted(glob.glob(os.path.expanduser("%s/*_v14_progress.json")),key=os.path.getmtime)[-40:]
for f in files:
    try: d=json.load(open(f))
    except Exception: continue
    ss=os.path.basename(f).replace("_v14_progress.json","")
    done=d.get("done",{})
    deltas=[v.get("delta") for v in done.values() if isinstance(v,dict) and isinstance(v.get("delta"),(int,float))]
    if not deltas: continue
    nz=[round(x,6) for x in deltas if abs(x)>1e-9]
    zero = len(nz)==0
    rep=""
    if nz:
        c=collections.Counter(nz); val,cnt=c.most_common(1)[0]
        if cnt>=8 and cnt>=0.5*len(nz): rep=f"{val} x{cnt}/{len(nz)}"
    if zero or rep: rows.append((ss,len(deltas),"ZERO" if zero else "REPEAT "+rep))
print(json.dumps(rows))
''' % ISO


def main():
    day = datetime.date.today().strftime("%Y%m%d")
    out = ROOT / "data" / "reports" / f"delta_health_{day}.md"
    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ")
    lines = [f"\n## {stamp}"]
    for tag, host in HOSTS.items():
        try:
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes",
                                "-o", "StrictHostKeyChecking=accept-new", host,
                                f"cd ~/binance-sandbox && python3 -c {json.dumps(REMOTE)}"],
                               capture_output=True, text=True, timeout=60)
            data = json.loads([l for l in r.stdout.splitlines() if l.strip().startswith("[")][-1]) if r.stdout.strip() else []
        except Exception as e:
            lines.append(f"- {tag}: monitor error {str(e)[:80]}")
            continue
        if not data:
            lines.append(f"- {tag}: OK (no 0/repeated-delta offenders in last 40 sheets)")
        else:
            lines.append(f"- {tag}: {len(data)} OFFENDERS")
            for ss, n, why in data[:25]:
                lines.append(f"    - {ss} ({n} switches): {why}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
