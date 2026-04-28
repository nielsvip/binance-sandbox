#!/usr/bin/env python3
"""Auto-discover the INDICATOR_TO_SWITCH map by parsing v8_quick_engine.py.

For each `getattr(cfg, 'XXX', ...)` in the engine, find which indicator fields
appear in nearby code (within ~10 lines) — that's the switch's gating relationship.
Cross-reference against `_safe(npz, 'YYY', n, ...)` reads.

Outputs:
  - data/indicator_switch_map.json — {indicator_field: [{switch, line_no, context_snippet}, ...]}
  - prints a markdown summary

The current hand-curated map in auto_suggest_loop.py is small (~20 fields). This builds
a much bigger one from code, which auto_suggest_loop.py can load to expand its action surface.

Usage:
  python3 build_indicator_switch_map.py
  python3 build_indicator_switch_map.py --merge   # merge into auto_suggest_loop.py's map
"""
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path("/Users/niels/Documents/binance")
ENGINE_PATH = ROOT / "v8_quick_engine.py"
OUT_PATH = ROOT / "data" / "indicator_switch_map.json"
MARKDOWN_PATH = ROOT / "data" / "indicator_switch_map.md"

# Indicator-naming conventions used in NPZ
INDICATOR_PREFIXES = (
    "stoch_k_", "stoch_d_", "stoch_crossover_", "stoch_crossunder_",
    "wt1_", "wt2_", "wt_velocity_", "wt_acceleration_", "wt_zscore_",
    "wt_momentum_state_",
    "dc_high_", "dc_low_", "dc_basis_", "dc_width_", "dc_pos_",
    "ema_", "sma_",
    "bb_upper_", "bb_lower_", "bb_pct_b_",
    "kc_upper_", "kc_lower_",
    "atr_", "adx_", "mfi_", "rsi_",
    "macd_", "macd_signal_",
    "ha_",
    "funding_rate_", "oi_", "oi_change_",
    "div_reg_bull_", "div_reg_bear_", "div_hid_bull_", "div_hid_bear_",
    "squeeze_", "relative_volume_",
    "0market_sentiment_", "0final_score_", "0sentiment_",
    "0dc_", "0ranking_", "0is_",
)


def is_indicator_token(tok):
    return any(tok.startswith(p) for p in INDICATOR_PREFIXES)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merge", action="store_true", help="merge into auto_suggest_loop.INDICATOR_TO_SWITCH")
    ap.add_argument("--context", type=int, default=10, help="lines around switch to scan for indicators")
    args = ap.parse_args()

    text = ENGINE_PATH.read_text()
    lines = text.splitlines()
    n = len(lines)

    # Find all `getattr(cfg, 'NAME'` and `cfg.NAME` references.
    # Filter to legitimate switches by suffix — drops noise like cfg.LTF, cfg.MODE, etc.
    switch_re = re.compile(r"""getattr\(\s*cfg\s*,\s*['"]([A-Z][A-Z0-9_]{2,})['"]""")
    switch_dot_re = re.compile(r"""\bcfg\.([A-Z][A-Z0-9_]{2,})\b""")
    SWITCH_SUFFIXES = ("_ENABLED", "_DISABLED", "_PCT", "_BPS", "_MIN_TFS", "_MAX_TFS",
                       "_MIN_INDS", "_MIN_BARS", "_MAX_BARS", "_BARS", "_THRESHOLD",
                       "_BUFFER", "_FLOOR", "_CEILING", "_GATE", "_REQUIRE",
                       "_LEVERAGE", "_NOTIONAL", "_FRAC", "_MULT", "_BOOST",
                       "_LOOKBACK", "_WINDOW", "_PROXIMITY", "_USE_FIB",
                       "_USE_ROUND", "_USE_WT_DC", "_DEDICATED", "_SYMBOLS",
                       "_TFS", "_TF", "_HARD_LOSS_USD_PER_TRADE", "_RISK_PATH",
                       "_FIB", "_ROUND", "_BOUNCE", "_MODE", "_VALUE", "_PCT_PER_TRADE",
                       "_MIN_TFS_THRESHOLD")
    SWITCH_BLACKLIST = {"LTF", "MODE", "BASE_PATH", "DATA_DIR", "HOME"}
    def is_real_switch(name):
        if name in SWITCH_BLACKLIST: return False
        # Either a known suffix OR contains a known sub-token
        if any(name.endswith(s) for s in SWITCH_SUFFIXES): return True
        if any(tok in name for tok in ("_RZ_", "_BTC_", "_HEDGE_", "_ENTRY", "_EXIT",
                                        "_AUGMENT", "_REENTRY", "_DIVERGENCE",
                                        "_FUNDING", "_OI", "_PARTIAL_PROFIT",
                                        "_NOLOSS", "_STOP_LOSS", "_BREAKOUT",
                                        "_FOLLOW_THROUGH", "_TECH_EXIT", "_ACCEL_RAMP")):
            return True
        return False

    # Find indicator field accesses: _safe(npz, 'field', ...) and npz['field']
    field_safe_re = re.compile(r"""_safe\([^,]+,\s*f?['"]([a-zA-Z0-9_]+)['"]""")
    field_npz_re = re.compile(r"""npz\[\s*f?['"]([a-zA-Z0-9_]+)['"]\s*\]""")
    field_files_re = re.compile(r"""['"]([a-zA-Z0-9_]+)['"]\s+in\s+npz\.files""")

    # Per-line cache of switches and indicator fields
    line_switches = defaultdict(set)
    line_fields = defaultdict(set)
    for i, line in enumerate(lines):
        for m in switch_re.finditer(line):
            if is_real_switch(m.group(1)):
                line_switches[i].add(m.group(1))
        for m in switch_dot_re.finditer(line):
            if is_real_switch(m.group(1)):
                line_switches[i].add(m.group(1))
        for rgx in (field_safe_re, field_npz_re, field_files_re):
            for m in rgx.finditer(line):
                tok = m.group(1)
                if is_indicator_token(tok):
                    line_fields[i].add(tok)

    # For each switch reference, look for indicator fields in ±context lines
    pairs = defaultdict(lambda: defaultdict(int))   # field → switch → count
    snippets = defaultdict(list)                   # (field, switch) → [line_no]
    for i, switches in line_switches.items():
        if not switches:
            continue
        lo = max(0, i - args.context)
        hi = min(n, i + args.context + 1)
        nearby_fields = set()
        for j in range(lo, hi):
            nearby_fields |= line_fields.get(j, set())
        if not nearby_fields:
            continue
        for sw in switches:
            for f in nearby_fields:
                pairs[f][sw] += 1
                snippets[(f, sw)].append(i + 1)  # 1-indexed line numbers

    # Build output map: for each indicator, list switches sorted by co-occurrence count
    out = {}
    for f, sw_counts in pairs.items():
        ranked = sorted(sw_counts.items(), key=lambda kv: -kv[1])
        out[f] = [
            {"switch": sw, "co_occurrence_count": cnt,
             "engine_lines": snippets[(f, sw)][:5]}
            for sw, cnt in ranked
        ]

    # Save JSON
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2))

    # Markdown summary
    md = ["# Indicator → Config-Switch Map  *(auto-discovered)*", ""]
    md.append(f"Source: `{ENGINE_PATH.name}`  ·  context window: ±{args.context} lines")
    md.append(f"Indicators with ≥1 switch correlation: **{len(out)}**")
    md.append("")
    md.append("| Indicator field | Strongest switch (count) | Other switches |")
    md.append("|---|---|---|")
    for f in sorted(out.keys()):
        ranked = out[f]
        if not ranked:
            continue
        primary = f"`{ranked[0]['switch']}` ({ranked[0]['co_occurrence_count']})"
        others = ", ".join(f"`{r['switch']}`({r['co_occurrence_count']})" for r in ranked[1:5])
        md.append(f"| `{f}` | {primary} | {others} |")
    MARKDOWN_PATH.write_text("\n".join(md))

    print(f"Discovered {len(out)} indicator → switch mappings")
    print(f"  JSON: {OUT_PATH}")
    print(f"  MD:   {MARKDOWN_PATH}")
    print()
    print("Top 15 strongest correlations:")
    flat = []
    for f, rows in out.items():
        if rows:
            flat.append((f, rows[0]["switch"], rows[0]["co_occurrence_count"]))
    flat.sort(key=lambda x: -x[2])
    for f, sw, cnt in flat[:15]:
        print(f"  {f:<30s} → {sw:<35s} ({cnt} co-occurrences)")

    if args.merge:
        # Merge into auto_suggest_loop's INDICATOR_TO_SWITCH at runtime
        # Write a sidecar JSON that auto_suggest_loop loads if present
        sidecar = ROOT / "data" / "indicator_switch_map_merged.json"
        merged = {}
        # Load existing hardcoded map
        try:
            from auto_suggest_loop import INDICATOR_TO_SWITCH as HARD
            for f, info in HARD.items():
                merged[f] = info
        except Exception:
            pass
        # Add discovered (only fields not already mapped)
        for f, rows in out.items():
            if f in merged: continue
            if not rows: continue
            sw = rows[0]["switch"]
            # Default values if we can't infer: use True/False booleans for *_ENABLED, ints for *_TFS / *_BARS / *_INDS
            if sw.endswith("_ENABLED"):
                merged[f] = {"switch": sw, "tighten_value": True, "loosen_value": False, "default": True, "auto": True}
            elif sw.endswith(("_MIN_TFS", "_MIN_INDS", "_MAX_BARS", "_BARS")):
                merged[f] = {"switch": sw, "tighten_value": 5, "loosen_value": 1, "default": 2, "auto": True}
            elif sw.endswith(("_PCT", "_PROXIMITY_PCT", "_THRESHOLD")):
                merged[f] = {"switch": sw, "tighten_value": 0.3, "loosen_value": 1.0, "default": 0.5, "auto": True}
            else:
                # skip — unknown shape
                continue
        sidecar.write_text(json.dumps(merged, indent=2))
        print(f"Merged map: {len(merged)} entries → {sidecar}")
        print("auto_suggest_loop will load this if --use-discovered is passed.")


if __name__ == "__main__":
    main()
