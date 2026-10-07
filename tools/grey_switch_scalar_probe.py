"""Probe the grey-rewire live hooks inside backtest_v12_engine (REAL tradier_manage/ez_manage.process_position).

Wraps vec_decisions.dc_channel_exits functions with counters (written to $PROBE_OUT on every call),
optionally forces config keys from $PROBE_CFG onto every config.Config instance (the crypto
V8_OVERRIDE_FILE loader drops keys outside the curated allowlist), then runs $BT_ENGINE as __main__.
Driven by tools/grey_switch_scalar_ab.sh. Run with PYTHONHASHSEED=0 V8_HASHSEED_LOCKED=1 so the
engine does not re-exec itself (which would drop the probe)."""
import collections, json, runpy, sys, os
sys.path.insert(0, os.getcwd())
import vec_decisions.dc_channel_exits as X
STATS = collections.Counter()
KEYS = {}
_orig_live = X.wt_lower_cross_live
_orig_dt = X.daytrade_dc_exit
_orig_res = X.resolve_daytrade_dc
def _flush():
    json.dump(dict(STATS), open(os.environ.get("PROBE_OUT", "/tmp/probe.json"), "w"), indent=1)
def wl(ind, tf, px, is_long):
    STATS['wlc_calls'] += 1
    if STATS['wlc_calls'] == 5:
        json.dump(sorted((ind or {}).keys()), open(os.environ.get("PROBE_OUT") + ".keys", "w"))
    for k in (f"wt1_{tf}", f"wt2_{tf}", f"wt1_{tf}_prev", f"wt2_{tf}_prev", f"_completed_wt1_{tf}_prev", f"_completed_wt2_{tf}_prev", "close_15m_prev", f"wt_cross_bear_{tf}", f"wt_cross_bull_{tf}"):
        if (ind or {}).get(k) not in (None, 0, 0.0):
            STATS['has_' + k] += 1
    r = _orig_live(ind, tf, px, is_long)
    if r:
        STATS['wlc_fire'] += 1
    _flush()
    return r
def dt(px, is_long, s, t, level):
    STATS['dt_calls'] += 1
    r = _orig_dt(px, is_long, s, t, level)
    if r[0]:
        STATS['dt_fire:' + r[1].split(' ')[0]] += 1
    for sp in s + t:
        if level(sp['field_long'] if is_long else sp['field_short']):
            STATS['lvl_' + sp['tag']] += 1
    _flush()
    return r
def res(get):
    a, b = _orig_res(get)
    if a or b:
        STATS['resolve_nonempty'] += 1
    return a, b
_orig_tf = X.wt_lower_cross_tf
def wtf(raw):
    STATS['tf_raw:' + str(raw)] += 1
    _flush()
    return _orig_tf(raw)
X.wt_lower_cross_tf = wtf
X.wt_lower_cross_live = wl
X.daytrade_dc_exit = dt
X.resolve_daytrade_dc = res
if os.environ.get("PROBE_CFG"):
    # crypto backtest filters V8_OVERRIDE_FILE through a curated allowlist: set the probe keys the
    # same 4 ways the engine's loader does (module attr, class attr, dataclass default, instances).
    import config as _C
    _PCFG = json.load(open(os.environ["PROBE_CFG"]))
    _orig_init = _C.Config.__init__
    def _init(self, *a, **k):
        _orig_init(self, *a, **k)
        for _kk, _vv in _PCFG.items():
            object.__setattr__(self, _kk, _vv)
    _C.Config.__init__ = _init
    for _inst in list(getattr(_C.Config, "_INSTANCES", [])):
        for _kk, _vv in _PCFG.items():
            object.__setattr__(_inst, _kk, _vv)
    for _k, _v in _PCFG.items():
        setattr(_C, _k, _v); setattr(_C.Config, _k, _v)
        if _k in _C.Config.__dataclass_fields__:
            _C.Config.__dataclass_fields__[_k].default = _v
out = os.environ.get("PROBE_OUT", "/tmp/probe.json")
sys.argv = ["backtest_v12_engine.py"] + sys.argv[1:]
try:
    runpy.run_path(os.environ.get("BT_ENGINE","backtest_v12_engine.py"), run_name="__main__")
except SystemExit:
    pass
finally:
    json.dump(dict(STATS), open(out, "w"), indent=1)
    print("PROBE", dict(STATS))
