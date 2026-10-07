#!/usr/bin/env python3
"""orange_placement_check — ORANGE FILTER PLACEMENT evidence (MASTER_PLAN §6). For every orange filter row (A=FILTER, B=option) in every tab of the 4 FINAL templates:
 correct tab(s) = tabs whose white switches are gated by that filter per data/opportune_filter_map.json (switch -> filters) [+ SWITCH_BIBLE kind of the filter itself];
 vec path / live path = reachable vector reads / live reads of the filter name in data/SWITCH_BIBLE.json (generic *_FILTER_TF vec gating is flagged GENERIC_UNVERIFIED unless a read exists);
 placement = OK | WRONG_TAB (filter gates switches of other tab(s) only) | DISCONNECTED (no switch maps to it) ; wiring = BOTH | VEC_ONLY | LIVE_ONLY | NONE.
Output data/wiring/orange_placement.csv (tab, filter, template, n_option_rows, placement, correct_tabs, reason, vec_path, live_path, wiring). Reads the bible (run tools/build_switch_bible.py first)."""
import collections
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_switch_bible as B  # noqa: E402


def main():
    bible = B.load_json(B.DATA / "SWITCH_BIBLE.json") or B.build()
    sw = bible["switches"]
    fmap = B.load_json(B.DATA / "opportune_filter_map.json", {})
    tpl = B.read_templates()
    out = []
    for cs, t in tpl.items():
        venue = "crypto" if cs.startswith("CRYPTO") else "stocks"
        all_filters = {f for d in (fmap.get(cs) or {}).values() for fl in d.values() for f in fl}
        gates = collections.defaultdict(set)  # filter -> tabs where some switch is gated by it
        for tab, d in (fmap.get(cs) or {}).items():
            for _s, flist in d.items():
                for f in flist:
                    gates[f].add(tab)
        orange = collections.defaultdict(lambda: collections.Counter())
        for name, rows in t["rows"].items():
            for r in rows:
                if r["orange"]:
                    orange[(r["tab"], name)][0] += 1
        for (tab, name), c in sorted(orange.items()):
            s = sw.get(name)
            ctabs = sorted(gates.get(name, set()))
            if name not in all_filters and not name.endswith(("_FILTER_TF", "_FILTER_TF_REQ")):
                # orange FILL on a row whose name is a plain switch, not a filter option row: kind-based home tab from the bible
                kind = (s or {}).get("kind", "?")
                home = {"entry": "ENTRY_", "exit": "EXIT_", "reentry": "REENTRY_", "augment": "AUGMENT_", "reduce": "REDUCE_", "global": "GLOBAL_", "sizing": "STDEV_"}.get(kind, "")
                ok = bool(home) and tab.startswith(home)
                placement = "ORANGE_SWITCH_OK" if ok else "ORANGE_SWITCH_CHECK"
                reason = f"orange fill on a plain switch (kind={kind}); tab {'matches' if ok else 'does not match'} its kind"
                ctabs = [home + "*"] if home else []
            elif not ctabs:
                placement, reason = "DISCONNECTED", "no switch in any tab of this template lists it as a gating filter (opportune_filter_map)"
            elif tab in ctabs:
                placement, reason = "OK", f"gates switches of this tab ({len(ctabs)} tab(s) total)"
            else:
                placement, reason = "WRONG_TAB", f"filter gates switches of {ctabs}, none in {tab}"
            vec = (s["vec_reads"][:2] if s and s["vec_read_count"]["reachable"] else []) if s else []
            live = s["live_reads"][venue][:2] if s else []
            if not vec and name.endswith("_FILTER_TF"):
                vec = ["GENERIC_UNVERIFIED (no direct reachable read; *_FILTER_TF generic gate vec_decisions/filter_tf_gate*.py)"]
                wv = False
            else:
                wv = bool(vec)
            wl = bool(live)
            wiring = "BOTH" if wv and wl else "VEC_ONLY" if wv else "LIVE_ONLY" if wl else "NONE"
            out.append([tab, name, cs, c[0], placement, "|".join(ctabs), reason, " ; ".join(vec), " ; ".join(live), wiring])
    p = ROOT / "data" / "wiring" / "orange_placement.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tab", "filter", "template", "n_option_rows", "placement", "correct_tabs", "reason", "vec_path", "live_path", "wiring"])
        w.writerows(out)
    pc = collections.Counter(r[4] for r in out)
    wc = collections.Counter(r[9] for r in out)
    print(f"[orange] {len(out)} (tab,filter) pairs  placement={dict(pc)} wiring={dict(wc)} -> {p}")
    return out


if __name__ == "__main__":
    main()
