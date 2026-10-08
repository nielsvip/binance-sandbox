#!/usr/bin/env python3
"""v15_partial_selection — build a golive selection {SS: {host, path}} from finished
run28 progress files across the fleet (final_gain present = 30D done, appliable).
Latest-mtime wins across hosts. Run from Mac (uses s1-int/s2/s5/s6/s7 aliases)."""
import json
import subprocess
import sys

HOSTS = {"s1": "s1-int", "s2": "s2", "s5": "s5", "s6": "s6", "s7": "s7"}
PDIR = "/home/niels/v15_run28_20261008/progress"


def fetch(host, alias):
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=25", alias,
           "python3 -c \"import json,os,glob; "
           "out=[]; "
           "dd='%s'; "
           "[out.append((os.path.basename(f)[:-len('_v14_progress.json')], round(os.path.getmtime(f)), f)) "
           "for f in glob.glob(dd+'/*_v14_progress.json') "
           "if (lambda d: d.get('final_gain') is not None)(json.load(open(f)))]; "
           "print(json.dumps(out))\" " % PDIR]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return json.loads(r.stdout or "[]")
    except Exception as e:
        print("WARN %s: %s" % (host, e), file=sys.stderr)
        return []


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "data/avg_delta_selection_run28_20261008.json"
    sel = {}
    for host, alias in HOSTS.items():
        rows = fetch(host, alias)
        print("%s: %d finished" % (host, len(rows)))
        for ss, mt, path in rows:
            if ss not in sel or mt > sel[ss]["mtime"]:
                sel[ss] = {"host": host, "path": path, "mtime": mt}
    keep = {k: {"host": v["host"], "path": v["path"]} for k, v in sel.items()}
    json.dump(keep, open(out, "w"), indent=1)
    print("wrote %s: %d sym_sides" % (out, len(keep)))


if __name__ == "__main__":
    main()
