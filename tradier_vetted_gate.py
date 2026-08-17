
# 2026-08-10 VETTED GATE: only backtest_v8_engine passing sym/sides may open live (manual overrules)
# Checked via data/hourly_reconfig/trb/vetted_allowlist_20260810.json + active_config gated flags
def _is_vetted_live_allowed(symbol, side):
    try:
        import json, pathlib
        allow=pathlib.Path("data/hourly_reconfig/trb/vetted_allowlist_20260810.json")
        if allow.exists():
            j=json.loads(allow.read_text())
            vetted=j.get("vetted_keys",[])
            if f"{symbol}_{side}" in vetted:
                return True
        # also check active_config gated flag
        cfg_path=pathlib.Path("data/hourly_reconfig/trb/active_config.json")
        if cfg_path.exists():
            cfg=json.loads(cfg_path.read_text())
            entry=cfg.get(f"{symbol}_{side}",{})
            over=entry.get("overrides",{})
            if over.get("_gated_by_vetted_rule_20260810"):
                return False
        return False
    except:
        return False
