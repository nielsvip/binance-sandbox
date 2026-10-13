"""per_sym > cat_side > global chain parity (USER 2026-10-13: TEMPLATE writes everything downstream).

Live may never read a config/QuickConfig literal directly for a key the
cat_side store carries: resolution is per-sym override > cat_side default
(TEMPLATE bold, delta-promoted) > single global literal. A direct read of a
diverged key runs stale values live (e.g. MAX_ORDER_VALUE 180 vs TEMPLATE 90
on CRYPTO_LONG, MAX_AUGMENTS 999999 vs 0 on SHORTS).
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


_MISSING = object()


def _expected(key, sym_side, venue, default=None):
    import per_sym_settings as pss
    try:
        import per_sym_store as store
        ov = store.get_overrides(sym_side)
        if ov is not None and key in ov:
            return ov[key]
    except Exception:
        pass
    sym, _, side = sym_side.rpartition("_")
    v = pss.get_for(key, sym, side, _MISSING, venue=venue)
    # exact chain contract: per-sym > cat_side > global fallback (grey fishnets with
    # live!=quick conflicts are absent from the store — the fallback serves live)
    return default if v is _MISSING else v


def test_chain_serves_store_over_literal_crypto():
    from ez_manage import _psym_get
    import config as C
    cfg = C.Config()
    for sym_side, key in [("BTCUSDC_LONG", "MAX_ORDER_VALUE"), ("BTCUSDC_SHORT", "MAX_AUGMENTS_PER_POSITION"), ("BTCUSDC_LONG", "MOM5_LONG_THRESHOLD"), ("BTCUSDC_SHORT", "FG_SIZING_ENABLED"), ("BTCUSDC_LONG", "EMA_DIST_SIZING_MULT")]:
        sym, _, side = sym_side.rpartition("_")
        got = _psym_get(sym, side, key, getattr(cfg, key, None))
        assert got == _expected(key, sym_side, "crypto", getattr(cfg, key, None)), (sym_side, key, got)


def test_chain_serves_store_over_literal_stocks():
    from tradier_manage import _cfg
    for sym_side, key in [("AAPL_SHORT", "PARABOLIC_PROTECTION_ENABLED"), ("AAPL_LONG", "STDEV_REJECT_EXIT_TF"), ("AAPL_SHORT", "MAX_AUGMENTS_PER_POSITION")]:
        sym, _, side = sym_side.rpartition("_")
        got = _cfg(key, None, None, sym, side)
        assert got == _expected(key, sym_side, "tradier"), (sym_side, key, got)


def test_per_sym_beats_cat_side_live():
    from ez_manage import _psym_get
    import per_sym_store as store
    ov = store.get_overrides("ETHUSDC_SHORT") or {}
    if "EMA_9_21_FILTER_ENABLED" not in ov:
        import pytest
        pytest.skip("ETHUSDC_SHORT carries no E921 override in this DB")
    assert _psym_get("ETHUSDC", "SHORT", "EMA_9_21_FILTER_ENABLED", False) == ov["EMA_9_21_FILTER_ENABLED"]


def test_service_resolver_mirrors_chain():
    from ez_positions_service import _svc_psym_get
    import config as C
    cfg = C.Config()
    for sym_side in ["BTCUSDC_LONG", "BTCUSDC_SHORT"]:
        sym, _, side = sym_side.rpartition("_")
        got = _svc_psym_get(sym, side, "MAX_ORDER_VALUE", getattr(cfg, "MAX_ORDER_VALUE", None))
        assert got == _expected("MAX_ORDER_VALUE", sym_side, "crypto", getattr(cfg, "MAX_ORDER_VALUE", None)), (sym_side, got)


def test_kg_gate_chain_wired():
    src = (ROOT / "ez_manage.py").read_text()
    gate = src[src.index("def _kindergarten_ema_gate"):src.index("def _kindergarten_ema_gate") + 3000]
    assert "_psym_get" in gate
    from ez_manage import _kindergarten_ema_gate
    ok, why = _kindergarten_ema_gate({"close": 100.0}, True, 100.0, enabled=False)
    assert (ok, why) == (True, "KG_OFF")


def test_parabolic_enabled_param():
    import inspect
    from tradier_manage import _parabolic_state
    assert "enabled" in inspect.signature(_parabolic_state).parameters
    assert _parabolic_state({}, None, enabled=False) == (False, False, False, False)


def _diverged_keys():
    import config as C, config_tradier as CT
    store = json.loads((ROOT / "data" / "per_sym_settings.json").read_text())

    def same(a, b):
        if a == b:
            return True
        try:
            return isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool) and float(a) == float(b)
        except Exception:
            return False

    div = {}
    for cs, cfg in (("CRYPTO_LONG", C.Config()), ("CRYPTO_SHORT", C.Config()), ("STOCKS_LONG", CT.TradierConfig()), ("STOCKS_SHORT", CT.TradierConfig())):
        for k, cv in (store.get(cs) or {}).items():
            if k.startswith("_"):
                continue
            lv = getattr(cfg, k, None) if hasattr(cfg, k) else "<NOLIT>"
            if lv == "<NOLIT>" or not same(cv, lv):
                div.setdefault(k, []).append(cs)
    return div


def test_no_direct_reads_of_diverged_keys():
    div = _diverged_keys()
    assert div, "store empty?"
    venue_sides = {"ez_manage.py": ("CRYPTO_LONG", "CRYPTO_SHORT"), "tradier_manage.py": ("STOCKS_LONG", "STOCKS_SHORT"), "ez_positions_service.py": ("CRYPTO_LONG", "CRYPTO_SHORT")}
    pats = [re.compile(r"getattr\(\s*config(?:_tradier)?\s*,\s*\"([A-Z][A-Z0-9_]+)\""), re.compile(r"\bconfig(?:_tradier)?\.([A-Z][A-Z0-9_]+)\b"), re.compile(r"getattr\(\s*cfg\s*,\s*\"([A-Z][A-Z0-9_]+)\"")]
    chain_mark = ("_psym_get", "_psym_cs_get", "_cfg(", "_cfg_auto", "_svc_psym_get", "get_overrides", "_ezm_default", "_ezm_cat_side_default")
    # (file, enclosing-func, key) -> reason: deliberate non-chain reads. Dead code,
    # fallback defaults feeding a chained use, or tick-global caps where per-sym is N/A.
    allow = {("ez_manage.py", "get_max_order_value", "MAX_ORDER_VALUE"): "dead, no callers",
             ("ez_manage.py", "_golden_rule_loop", "GOLDEN_RULE_COOLDOWN_S"): "fallback default for chained per-sym use",
             ("ez_manage.py", "_golden_rule_loop", "GOLDEN_RULE_MAX_FIRES_PER_TICK"): "tick-global cap, value==TEMPLATE, per-sym N/A",
             ("tradier_manage.py", "_parabolic_state", "PARABOLIC_PROTECTION_ENABLED"): "signature-compat default; all 7 live callers pass enabled="}
    dead_fns = {"_ensure_ez_all", "_ensure_tradier_all"}  # uncalled touch-blocks, config param shadows module
    hits = []
    for fp, sides in venue_sides.items():
        lines = (ROOT / fp).read_text().splitlines()
        cur_fn = "?"
        for i, ln in enumerate(lines, 1):
            m = re.match(r"(async )?def (\w+)\(", ln) or re.match(r"    (async )?def (\w+)\(", ln)
            if m:
                cur_fn = m.group(2)
            if cur_fn in dead_fns:
                continue
            s = ln.strip()
            if s.startswith("_ =") or s.startswith("_="):
                continue
            if re.search(r"_ *= *1\s*(#|$)", s) or "_ = _r" in s or re.search(r"_ *= *getattr", s):
                continue
            if re.match(r"^getattr\(config(?:_tradier)?, \"[A-Z][A-Z0-9_]+", s) and (s.rstrip().endswith(",") or (i < len(lines) and lines[i].strip().startswith("getattr(config"))):
                continue  # bare discoverability-tuple element (value discarded)
            if any(m in ln for m in chain_mark):
                continue
            if i > 1 and any(m in lines[i - 2] for m in chain_mark) and "(" in lines[i - 2] and ")" not in lines[i - 2].split("(")[-1]:
                continue
            # dead touch-blocks: `if bool/false-getattr(...):` whose body discards
            if re.match(r"if (bool|float)\(getattr\(config(?:_tradier)?, ", s):
                nxt = "".join(lines[i:i + 3])
                if "_ = 1" in nxt or "_=getattr" in nxt or "_ = _r" in nxt or "locals()" in nxt or "_ind =" in nxt or "_wt =" in nxt or "_r =" in nxt or "_f =" in nxt or "_v =" in nxt:
                    continue
            for p in pats:
                for k in p.findall(ln):
                    if k in div and any(x in div[k] for x in sides) and (fp, cur_fn, k) not in allow:
                        hits.append(f"{fp}:{i} {k}")
    assert not hits, f"live bypasses of TEMPLATE-derived values: {hits[:20]}"
