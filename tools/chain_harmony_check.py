#!/usr/bin/env python3
"""chain_harmony_check — read-only proof that the pilot -> engine -> TEMPLATE -> QuickConfig -> config chain has no
duplicate or contradictory switch (USER 2026-10-16: "the v15_pilot -> v12_quick_engine -> TEMPLATE_* -> avg_delta/pos_sym
-> Quickconfig -> config(_tradier) -> live chain keeps breaking it needs to be in total harmony in this sequence. Also
duplicate contradictory fields in TEMPLATE_ or v12_quick_engine should be impossible").

Checks (exit 1 on any HARD finding):
  QC_DUP        a QuickConfig field declared twice (the later line silently re-types/re-defaults it)
  TPL_DUP_ROW   the same (switch, value) row twice in one tab of one template
  TPL_MULTI_TAB a switch with rows in more than one tab of the same template (two places to promote one knob)
  TPL_BOLD      a switch group without exactly one bold default row
  TPL_TYPE      a bold value that cannot be the QuickConfig field's type (e.g. True on an int knob, 'D' on a float)
  TPL_VS_QC     bold default != QuickConfig default (WARN: promoted cat_side bolds legitimately differ)
Usage: tools/chain_harmony_check.py [--templates DIR] [--json OUT]
"""
import argparse
import collections
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TEMPLATES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
SWITCH_SHEETS = ("STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES")


def _same(a, b):
    if isinstance(a, bool) or isinstance(b, bool) or str(a).strip().lower() in ("true", "false") or str(b).strip().lower() in ("true", "false"):
        return str(a).strip().lower() == str(b).strip().lower()
    try:
        return abs(float(a) - float(b)) < 1e-12
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _type_ok(v, qv):
    if isinstance(qv, bool):
        return isinstance(v, bool) or str(v).strip().lower() in ("true", "false")
    if isinstance(qv, (int, float)):
        if isinstance(v, bool):
            return False
        try:
            float(v)
            return True
        except (TypeError, ValueError):
            return False
    return True


def check(tdir):
    import openpyxl
    import v12_quick_engine as E

    hard, warn = [], []
    hard += [("QC_DUP", "QuickConfig", k) for k in E._quickconfig_duplicate_fields()]
    q = E.QuickConfig()
    qt = E.QuickConfig()
    qt.apply_tradier_defaults()
    fields = {f.name for f in dataclasses.fields(E.QuickConfig)}
    for t in TEMPLATES:
        p = Path(tdir) / f"TEMPLATE_{t}.xlsx"
        wb = openpyxl.load_workbook(p)
        tabs_of = collections.defaultdict(set)
        for ws in wb.worksheets:
            if ws.title not in SWITCH_SHEETS:
                continue
            groups = collections.defaultdict(list)
            for r in range(3, ws.max_row + 1):
                sw = ws.cell(r, 1).value
                if not isinstance(sw, str) or not sw.strip() or not sw.strip().replace("_", "").isalnum() or sw.strip() != sw.strip().upper():
                    continue
                groups[sw.strip()].append((r, ws.cell(r, 2).value, bool(ws.cell(r, 2).font and ws.cell(r, 2).font.b)))
            for sw, rows in groups.items():
                tabs_of[sw].add(ws.title)
                seen = []
                for r, v, b in rows:
                    if any(_same(v, s) for s in seen):
                        hard.append(("TPL_DUP_ROW", t, f"{ws.title}!{r} {sw}={v!r}"))
                    seen.append(v)
                bolds = [(r, v) for r, v, b in rows if b]
                if len(bolds) != 1:
                    warn.append(("TPL_BOLD", t, f"{ws.title} {sw} bold rows={[r for r, _ in bolds]}"))
                if sw in fields and bolds:
                    qv = getattr(qt if t.startswith("STOCKS") else q, sw)
                    bv = bolds[0][1]
                    if not _type_ok(bv, qv):
                        hard.append(("TPL_TYPE", t, f"{ws.title}!{bolds[0][0]} {sw} bold={bv!r} vs QuickConfig {type(qv).__name__}={qv!r}"))
                    elif not _same(bv, qv):
                        warn.append(("TPL_VS_QC", t, f"{sw} bold={bv!r} QuickConfig={qv!r}"))
        for sw, tabs in tabs_of.items():
            if len(tabs) > 1:
                hard.append(("TPL_MULTI_TAB", t, f"{sw} in {sorted(tabs)}"))
    return hard, warn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--templates", default=str(ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM"))
    ap.add_argument("--json")
    a = ap.parse_args()
    hard, warn = check(a.templates)
    by = collections.Counter(h[0] for h in hard)
    byw = collections.Counter(w[0] for w in warn)
    print(f"[chain-harmony] HARD {dict(by)} WARN {dict(byw)}")
    for h in hard[:60]:
        print("  HARD", *h)
    if a.json:
        Path(a.json).write_text(json.dumps({"hard": hard, "warn": warn}, indent=1, default=str))
    sys.exit(1 if hard else 0)


if __name__ == "__main__":
    main()
