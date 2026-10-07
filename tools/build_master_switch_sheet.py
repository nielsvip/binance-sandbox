#!/usr/bin/env python3
"""
build_master_switch_sheet — the switch AUTO-FILLER registry (rebuilt 2026-09-28; the original was
lost, its s1 cron */30 failed since Aug 20 and MASTER_switches.csv froze).

One row per switch, its hookup status across every layer an agent must wire when deploying a new
switch:
  template   — which cat_side TEMPLATE_*.xlsx tabs carry rows for it (the sweep can test it)
  quickcfg   — field on v12_quick_engine.QuickConfig (the vector engine can receive it)
  vec_read   — v12_quick_engine.py or vec_decisions/* actually READS cfg.<NAME> (real vec path)
  cfg_crypto / cfg_stocks — default exists in config.Config / config_tradier.TradierConfig
  ez_read / tradier_read  — ez_manage.py / tradier_manage.py reference the name (live path)
  status     — WIRED (vec+live), LIVE_ONLY, VEC_ONLY, DEAD (template row but no vec and no live),
               or UNLISTED (in code but no template row → the sweep never tests it)

Outputs (ROOT-relative, safe on Mac and s1):
  SPREADSHEETS/MASTER_switches.csv          — full registry
  SPREADSHEETS/SWITCHES_NOT_CONNECTED.csv   — template rows with no vec and no live read
  data/v15_wired_switches.json              — refreshed wired list (vec_read minus DEAD_VEC),
                                              previous file backed up beside it with a timestamp
"""
import csv
import dataclasses
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

TEMPLATES = {cat: ROOT / "SPREADSHEETS" / f"TEMPLATE_{cat}.xlsx" for cat in ("STOCKS_LONG", "STOCKS_SHORT", "CRYPTO_LONG", "CRYPTO_SHORT")}
SWITCH_SHEETS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")


def template_switches() -> dict:
    import openpyxl
    where: dict = {}
    for cat, path in TEMPLATES.items():
        if not path.exists():
            continue
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for sheet in SWITCH_SHEETS:
            if sheet not in wb.sheetnames:
                continue
            for row in wb[sheet].iter_rows(min_row=3, max_col=1, values_only=True):
                name = row[0]
                if isinstance(name, str) and NAME_RE.match(name.strip()):
                    where.setdefault(name.strip(), set()).add(f"{cat}:{sheet}")
        wb.close()
    return where


def cfg_reads(paths, pattern) -> set:
    found = set()
    rx = re.compile(pattern)
    for p in paths:
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        found |= set(rx.findall(text))
    return found


def main():
    tpl = template_switches()
    import v12_quick_engine as V
    quickcfg = {f.name for f in dataclasses.fields(V.QuickConfig)}
    engine_files = [ROOT / "v12_quick_engine.py"] + sorted((ROOT / "vec_decisions").glob("*.py"))
    vec_read = cfg_reads(engine_files, r"cfg\.([A-Z][A-Z0-9_]{2,})") | cfg_reads(engine_files, r"getattr\(cfg,\s*[\"']([A-Z][A-Z0-9_]{2,})[\"']")
    import config as C
    import config_tradier as CT
    cfg_crypto = {k for k in dir(C.Config) if NAME_RE.match(k)}
    cfg_stocks = {k for k in dir(CT.TradierConfig) if NAME_RE.match(k)}
    live_ez = cfg_reads([ROOT / "ez_manage.py"], r"\b([A-Z][A-Z0-9_]{4,})\b")
    live_tr = cfg_reads([ROOT / "tradier_manage.py"], r"\b([A-Z][A-Z0-9_]{4,})\b")
    dead_vec, live_only = set(), set()
    try:
        pilot_src = (ROOT / "v15_pilot.py").read_text(errors="ignore")
        for var, target in (("DEAD_VEC_SWITCHES", dead_vec), ("LIVE_ONLY_SWITCHES", live_only)):
            m = re.search(var + r"\s*=\s*frozenset\(\{(.*?)\}\)", pilot_src, re.S)
            if m:
                target |= set(re.findall(r"[\"']([A-Z][A-Z0-9_]{2,})[\"']", m.group(1)))
    except Exception as e:
        print(f"[warn] pilot lists unreadable: {e}")
    names = sorted(set(tpl) | quickcfg | (vec_read & (cfg_crypto | cfg_stocks)))
    rows, not_connected, wired = [], [], []
    for n in names:
        in_tpl = ";".join(sorted(tpl.get(n, [])))
        vec = n in vec_read and n not in dead_vec
        live = (n in live_ez) or (n in live_tr)
        if n in live_only:
            status = "LIVE_ONLY"
        elif vec and live:
            status = "WIRED"
        elif vec:
            status = "VEC_ONLY"
        elif live:
            status = "LIVE_NO_VEC"
        else:
            status = "DEAD"
        if not in_tpl and status in ("WIRED", "VEC_ONLY", "LIVE_NO_VEC"):
            status += "+UNLISTED"
        rows.append({"switch": n, "status": status, "template": in_tpl, "quickcfg": n in quickcfg, "vec_read": n in vec_read, "dead_vec_listed": n in dead_vec, "cfg_crypto": n in cfg_crypto, "cfg_stocks": n in cfg_stocks, "ez_read": n in live_ez, "tradier_read": n in live_tr})
        if in_tpl and status == "DEAD":
            not_connected.append({"switch": n, "template": in_tpl})
        if in_tpl and vec:
            wired.append(n)
    out = ROOT / "SPREADSHEETS" / "MASTER_switches.csv"
    with out.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    out2 = ROOT / "SPREADSHEETS" / "SWITCHES_NOT_CONNECTED.csv"
    with out2.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["switch", "template"])
        w.writeheader()
        w.writerows(not_connected)
    wired_path = ROOT / "data" / "v15_wired_switches.json"
    if wired_path.exists():
        stamp = time.strftime("%Y%m%d%H%M")
        wired_path.rename(wired_path.with_name(f"v15_wired_switches.{stamp}.bak.json"))
    wired_path.write_text(json.dumps(sorted(wired), indent=0))
    print(f"[master-switch] {len(rows)} switches | wired {len(wired)} | template-dead {len(not_connected)} | quickcfg {len(quickcfg)} | vec_read {len(vec_read & set(names))} -> {out.name}, {out2.name}, {wired_path.name}")


if __name__ == "__main__":
    main()
