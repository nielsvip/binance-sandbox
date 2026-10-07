#!/usr/bin/env python3
"""def2_build_neutral_stage (DEF2 2026-10-01): rebuild the DEF2/001_neutral stage on top of ANY deployed v12_quick_engine.py (anchor based, idempotent).
usage: python tools/def2_build_neutral_stage.py <deployed_engine.py> <stage_dir>   -> <stage_dir>/{v12_quick_engine.py, vec_decisions/stocks_live_twins.py, data/vec_unwired.json}"""
import json, subprocess, sys, shutil
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
src, stage = Path(sys.argv[1]), Path(sys.argv[2])
(stage / "vec_decisions").mkdir(parents=True, exist_ok=True); (stage / "data").mkdir(exist_ok=True)
eng = stage / "v12_quick_engine.py"; shutil.copy2(src, eng)
Q = ROOT / "data/wiring/queue/DEF2/003"
for f in ("apply_def2_003_n2_007.py", "apply_def2_003_n2_011.py"):
    r = subprocess.run([sys.executable, str(Q / f), str(eng)], capture_output=True, text=True); print(f, r.stdout.strip(), r.stderr.strip()[-300:])
    if r.returncode: sys.exit(1)
s = eng.read_text()
if "STOCKS_LIVE_TWINS_ENABLED" not in s:
    a = "    STOCKS_REENTRY_LIVE_SOURCES_ENABLED: bool = True"; i = s.index(a); j = s.index("\n", i) + 1
    s = s[:j] + "    STOCKS_LIVE_TWINS_ENABLED: bool = False  # [C2 q007 -> DEF2/001 default OFF/neutral] stocks DELTA_EXIT_DC_FLOOR hold + STDEV_BREAKOUT size twin (vec_decisions/stocks_live_twins.py); True = live-faithful\n" + s[j:]
    b = "    delta_exit = (wt_against >= cfg.WT_EXIT_MIN_TFS) if getattr(cfg, 'DELTA_EXIT_ENABLED', True) else np.zeros(n, dtype=bool)\n"
    assert s.count(b) == 1
    s = s.replace(b, b + """    # [C2 q007 -> DEF2/001] stocks live DELTA_EXIT_DC_FLOOR (tradier_manage.py:19690): a valid Delta exit is HELD until price breaks the 15m Donchian boundary; gated by STOCKS_LIVE_TWINS_ENABLED (default OFF)
    if str(getattr(cfg, 'MODE', 'crypto')) == 'tradier' and bool(getattr(cfg, 'DELTA_EXIT_DC_FLOOR', False)) and bool(getattr(cfg, 'STOCKS_LIVE_TWINS_ENABLED', False)):
        try:
            import vec_decisions.stocks_live_twins as _slt
            delta_exit = delta_exit & _slt.dc_floor_confirms(npz, n, is_long, close)
        except Exception:
            pass
""")
    eng.write_text(s)
    # the sizer hunk (STDEV_BREAKOUT qty hook) of C2/007 applies cleanly; re-apply it via patch with the two failing hunks tolerated
    subprocess.run(["patch", "-p0", str(eng)], stdin=open(ROOT / "data/wiring/queue/C2/007/hook.diff"), capture_output=True, text=True)
    for rej in (stage / "v12_quick_engine.py.rej", stage / "v12_quick_engine.py.orig"):
        rej.unlink(missing_ok=True)
    s = eng.read_text().replace("bool(getattr(cfg, 'STOCKS_LIVE_TWINS_ENABLED', True))", "bool(getattr(cfg, 'STOCKS_LIVE_TWINS_ENABLED', False))")
s = s.replace("bool(getattr(cfg, 'STOCKS_LIVE_TWINS_ENABLED', True))", "bool(getattr(cfg, 'STOCKS_LIVE_TWINS_ENABLED', False))")
eng.write_text(s)
shutil.copy2(ROOT / "data/wiring/queue/C2/007/vec_decisions/stocks_live_twins.py", stage / "vec_decisions/stocks_live_twins.py")
d = json.load(open(ROOT / "data/vec_unwired.json"))
def rm(o):
    if isinstance(o, dict):
        for k in list(o):
            if k == "DELTA_EXIT_DC_FLOOR": del o[k]
            else: rm(o[k])
    elif isinstance(o, list):
        while "DELTA_EXIT_DC_FLOOR" in o: o.remove("DELTA_EXIT_DC_FLOOR")
        for x in o: rm(x)
rm(d); json.dump(d, open(stage / "data/vec_unwired.json", "w"), indent=1)
import py_compile; py_compile.compile(str(eng), doraise=True); print("stage ok", stage)
