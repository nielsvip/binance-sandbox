"""vector_family_monitor — parity watch until a final verdict lands (USER 2026-10-21).

The enforcer logs every check() verdict for MONITORED_FAMILIES to
data/vector_family_monitor.jsonl. This console:
  report [FAMILY]   per-family fires / allowed / killed / unique keys + last fire
  replay FAMILY     re-score historical fires twin-vs-vector on real NPZ bars
                    (EXIT velocity legs + TECHNICAL crossing; needs NPZ + boards)

Replay is evidence for upgrading a family to vectorized (exempt) or keeping it
killed — same bar-replay standard as the 2026-10-21 VEL retrace. Exit_sig
composition is a known residual for TECHNICAL (not for EXIT lane-D booking).
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_twins import vector_enforce as ve


def _rows(paths):
    out = []
    for p in paths:
        try:
            lines = Path(p).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                out.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue
    return out


def cmd_report(argv):
    fam = argv[2].upper() if len(argv) > 2 else ""
    rows = _rows([ve.MONITOR_LOG, ve.VIOLATIONS])
    fams = {}
    for d in rows:
        f = d.get("family") or ve.family_of(d.get("reason", ""))
        if fam and f != fam:
            continue
        s = fams.setdefault(
            f, {"fires": 0, "allowed": 0, "killed": 0, "keys": set(), "last": 0}
        )
        s["fires"] += 1
        v = str(d.get("verdict", ""))
        if v.startswith("KILLED") or v == "VIOLATION_FAMILY_KILLED":
            s["killed"] += 1
        elif d.get("allowed") is True or v in (
            "VEC_SWITCH_EXIT",
            "CROSS_CONFIRMED",
            "RATIO_TRIM_ALLOWED",
            "STOP_PROTECTION",
            "BOARD_CONFIRMED",
        ):
            s["allowed"] += 1
        if d.get("position_key"):
            s["keys"].add(d["position_key"])
        s["last"] = max(s["last"], float(d.get("ts", 0) or 0))
    if not fams:
        print("no monitor rows" + (f" for {fam}" if fam else ""))
        return 0
    for f, s in sorted(fams.items()):
        last = (
            time.strftime("%m-%d %H:%M", time.gmtime(s["last"])) if s["last"] else "-"
        )
        print(
            f"{f} fires={s['fires']} allowed={s['allowed']} killed={s['killed']} "
            f"keys={len(s['keys'])} last={last}"
        )
    return 0


def cmd_replay(argv):
    if len(argv) < 3:
        print("usage: replay EXIT|TECHNICAL")
        return 2
    fam = argv[2].upper()
    if fam == "EXIT":
        from tools.vector_family_replay import replay_exit_fires as run
    elif fam == "TECHNICAL":
        from tools.vector_family_replay import replay_technical_fires as run
    else:
        print("replay supports EXIT|TECHNICAL (vector-booked lanes with twin pairs)")
        return 2
    n_match, n_total = run()
    print(f"{fam}: twin==vector on {n_match}/{n_total} replayed fire bars")
    return 0 if n_match == n_total and n_total > 0 else 1


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "report"
    if cmd == "report":
        return cmd_report(argv)
    if cmd == "replay":
        return cmd_replay(argv)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
