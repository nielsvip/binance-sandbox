#!/usr/bin/env python3
"""Post-rsync validator: scans klines_cache_gateway for corrupt JSON files and removes them so ez_prices can rebuild cleanly."""
import json, os, sys, re
from pathlib import Path
CACHE_DIR = Path("/Users/niels/Documents/binance/klines_cache_gateway")
VERBOSE = "--verbose" in sys.argv or "-v" in sys.argv
removed = 0; ok = 0; empty = 0
def try_repair(raw: bytes) -> bool:
    """Try the same repair strategies as _load_klines_json_lenient."""
    text = raw.decode("utf-8", errors="ignore").strip()
    if not text: return False
    try: json.loads(text); return True
    except Exception: pass
    if "][" in text:
        try: json.loads(text.replace("][", ",")); return True
        except Exception: pass
    end = text.rfind(']')
    if end != -1:
        try: json.loads(text[:end+1]); return True
        except Exception: pass
    last = text.rfind('}')
    if last != -1:
        try: json.loads(text[:last+1] + "]"); return True
        except Exception: pass
    matches = re.findall(r'\{[^{}]*\}', text)
    if len(matches) >= 5: return True
    return False
for f in sorted(CACHE_DIR.glob("*.json")):
    try:
        raw = f.read_bytes()
        if not raw.strip(): empty += 1; continue
        try: json.loads(raw); ok += 1; continue
        except Exception: pass
        if try_repair(raw):
            if VERBOSE: print(f"⚠️  REPAIRABLE (kept): {f.name}")
            ok += 1
        else:
            print(f"🗑️  CORRUPT (removing): {f.name} ({len(raw)} bytes)")
            f.unlink(); removed += 1
    except Exception as e:
        print(f"❌ ERROR reading {f.name}: {e}")
print(f"\n✅ validate_klines_cache done: {ok} ok, {empty} empty, {removed} removed (will be rebuilt by ez_prices)")
