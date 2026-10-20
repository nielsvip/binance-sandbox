#!/usr/bin/env python3
"""native_mode_gate_coupling_guard — refuse the config combination that silently halts the book.

USER 2026-10-20: "Do we need that switch that keep fucking everything up or can we just delete it".
The switch is not the problem; the COUPLING is. Three separate gates authorise an entry by asking for
something only the VEC TWIN can produce. While PARITY_VEC_EXACT_MODE=True every order came from the twin,
so each gate always passed and looked harmless. Flipping to native producers (USER 2026-10-20 "NOTHING can
live trade an npz") means a native order cannot satisfy them BY CONSTRUCTION, so each becomes a 100%
refusal that presents as a quiet market — no error, no alert, just nothing opening.

MEASURED, each one separately, on 2026-10-20:
  TRADIER_HERMETIC_ENFORCE=True   -> 199 orders refused BLOCKED_HERMETIC_NOT_VEC in the first 47 min after
                                     the open (trb 115, trc 84) against 190 OPEN EXECUTE attempts, ZERO
                                     positions. The gate wants live_twins/vec_provenance, set only by the vec
                                     dispatch sites. Its own comment predicted it: "enforcing on day one would
                                     refuse most legitimate opens and halt the book".
  STRICT_VEC_PARITY_MODE=True     -> 524 of 524 native stock OPEN proposals refused; is_vec_achievable()
                                     admitted 0 of 582,689 board entry rows and 0 of 58 live reason literals.
                                     It only ever passed because the token "VEC_EXACT" short-circuits it.
  board vintage by run label       -> 332 of 474 published sym_sides refused BOARD_TOO_OLD at 230 h while their
                                     cells were 0.4-179 h old; the run DIRECTORY label (run32_20261011) is the
                                     day the run STARTED, not the day the cells were computed.

This guard is READ-ONLY and never edits a config: it states the breach and exits 1 so a cron/chain can alert.
It does not judge whether native mode is right — it judges whether the gates agree with the mode in force.

Usage:
    tools/native_mode_gate_coupling_guard.py            # report, exit 1 on breach
    tools/native_mode_gate_coupling_guard.py --json
    tools/native_mode_gate_coupling_guard.py --quiet    # cron: print only on breach
Report: data/reports/gate_coupling/latest.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reports" / "gate_coupling"

# gate switch -> (what it requires that ONLY the twin can mint, what happens natively)
TWIN_ONLY_GATES = {
    "TRADIER_HERMETIC_ENFORCE": (
        "live_twins/vec_provenance ContextVar, set only by the vec dispatch sites",
        "every native ENTRY returns BLOCKED_HERMETIC_NOT_VEC",
    ),
    "STRICT_VEC_PARITY_MODE": (
        "a reason string matching vec_paths/vec_parity_gate's allowlist, or the literal token VEC_EXACT",
        "every native ENTRY returns BLOCKED_VEC_PARITY_* (and 60% of exits)",
    ),
}


def surfaces():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    out = {}
    for venue, mod, cls in (("crypto", "config", "Config"), ("stocks", "config_tradier", "TradierConfig")):
        try:
            out[venue] = getattr(__import__(mod), cls)()
        except Exception as exc:
            out[venue] = f"UNLOADABLE {type(exc).__name__}: {exc}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    rep = {"generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "venues": {}, "breaches": []}
    for venue, cfg in surfaces().items():
        if isinstance(cfg, str):
            rep["breaches"].append({"venue": venue, "gate": "-", "why": cfg})
            continue
        native = not bool(getattr(cfg, "PARITY_VEC_EXACT_MODE", False))
        v = {"PARITY_VEC_EXACT_MODE": bool(getattr(cfg, "PARITY_VEC_EXACT_MODE", False)),
             "native_producers": native, "gates": {}}
        for gate, (needs, effect) in TWIN_ONLY_GATES.items():
            on = getattr(cfg, gate, "<absent>")
            v["gates"][gate] = on
            if native and on is True:
                rep["breaches"].append({"venue": venue, "gate": gate, "requires": needs, "effect": effect,
                                        "why": f"{gate}=True while native producers decide — a native order cannot satisfy it"})
        rep["venues"][venue] = v
    rep["verdict"] = "OK" if not rep["breaches"] else "BREACH"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(rep, indent=1))
    if args.json:
        print(json.dumps(rep, indent=1))
    elif not (args.quiet and rep["verdict"] == "OK"):
        for venue, v in rep["venues"].items():
            mode = "NATIVE producers" if v["native_producers"] else "VEC TWIN drives"
            print(f"[coupling] {venue}: {mode} (PARITY_VEC_EXACT_MODE={v['PARITY_VEC_EXACT_MODE']})")
            for g, on in v["gates"].items():
                print(f"    {g} = {on}")
        for b in rep["breaches"]:
            print(f"[coupling] BREACH {b['venue']}.{b['gate']}: {b['why']}")
            if b.get("effect"):
                print(f"              effect: {b['effect']}")
        print(f"[coupling] {rep['verdict']} -> {OUT / 'latest.json'}")
    return 0 if rep["verdict"] == "OK" else 1


if __name__ == "__main__":
    sys.exit(main())
