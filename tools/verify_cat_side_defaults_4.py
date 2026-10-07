#!/usr/bin/env python3
"""verify_cat_side_defaults_4 — GUARD: every scalar non-secret field of the venue config (config.Config / config_tradier.TradierConfig)
and QuickConfig must have an explicit entry in ALL FOUR cat_side maps of data/cat_side_defaults_4.json (USER 2026-09-30: four settings
per switch, ALWAYS). Fields whose live value and QuickConfig value disagree are listed in _meta.conflicts_quick_vs_live (curated
decision, never auto-filled — BIBLE §17.3) and reported as WARN. Also flags stale snapshots: a non-template key whose stored value
no longer equals the current config value (someone edited config.py; the file shadows it) -> --fix rebuilds (stage-only: live values
of template keys are kept, promotions stay staged).
  python tools/verify_cat_side_defaults_4.py [--fix]      exit 0 = OK, 1 = missing/stale keys
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import build_cat_side_defaults_4 as B  # noqa: E402
import cat_side_defaults as CSD  # noqa: E402


def main():
    fix = "--fix" in sys.argv
    data = json.loads(CSD.PATH.read_text())
    meta = data.get("_meta", {})
    bad = 0
    for cs in CSD.CAT_SIDES:
        stocks = cs.startswith("STOCKS")
        _typed, live, quick = B.venue_values(stocks)
        have = data.get(cs) or {}
        conf = set((meta.get("conflicts_quick_vs_live") or {}).get(cs) or [])
        missing, stale = [], []
        for k in sorted(set(live) | set(quick)):
            lv, qv = live.get(k, B._MISSING_V), quick.get(k, B._MISSING_V)
            ref = lv if lv is not B._MISSING_V else qv
            if B.SECRET_RE.search(k) or not B._scalar(ref) or k in conf:
                continue
            if k not in have:
                missing.append(k)
            elif lv is not B._MISSING_V and not B._same(have[k], lv) and k not in set((meta.get("staged_not_live") or {}).get(cs) or []) and k not in (meta.get("template_keys", {}).get(cs) or []):
                stale.append(k)
        print(f"[{cs}] entries={len(have)} missing={len(missing)} stale_vs_config={len(stale)} conflicts(curated)={len(conf)}")
        if missing or stale:
            bad += 1
            print("   missing:", missing[:8], "stale:", stale[:8])
    if bad and fix:
        print("[fix] rebuilding (stage-only)")
        subprocess.run([sys.executable, str(ROOT / "tools" / "build_cat_side_defaults_4.py"), "--stage-only"], check=True)
        return 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
