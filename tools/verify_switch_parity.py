#!/usr/bin/env python3
"""Honest guard for the TEMPLATE<->config invariant, plus an opt-in live-vs-vector rerun.

INVARIANT (USER, verbatim 2026-10-20): "ANYTHING in TEMPLATE_CRYPTO_* HAS TO EXIST IN config.py
and ANY FUCKING LINE IN TEMPLATE_STOCKS_* HAS TO EXIST IN config_tradier.py".

Section 1 -- TEMPLATE key parity (default, always runs, decides the exit code).
This is the check PARITY_TESTING.md documents for this file ("PARITY GUARD OK: N TEMPLATE keys ...",
"PARITY GUARD FAILED: Missing from configs: [...]"). It reads column A of every sheet of
SPREADSHEETS/TEMPLATE_CRYPTO_{LONG,SHORT}.xlsx and TEMPLATE_STOCKS_{LONG,SHORT}.xlsx, keeps only
real UPPER_SNAKE_CASE switch identifiers (the 13 SWITCH_SHEETS tab names, legend/colour rows and
inventory banners are labels, not switches), and proves presence by IMPORTING the config and calling
hasattr on the real surfaces live reads: the instance (config.Config() / config_tradier.TradierConfig()),
the class, and the module. Never by text presence -- switch reads are built dynamically.
PRISTINE-IMPORT RULE: the config import must happen before any engine import. Importing
backtest_v12_engine triggers AUTO_VECTOR_LOAD, which setattrs the per_sym_active_config.json union onto
the config module/class/instances and makes absent keys look present (measured 2026-10-20:
REENTRY_TIER2_MAX_MINUTES flips False->True that way). The rerun imports therefore live inside
--rerun only, after section 1 has already been evaluated.

Section 2 -- honest live-vs-vector rerun (opt-in, --rerun). NEVER compares vector to vector or
hash/proxy; reruns the real live function on a frozen snapshot and compares element-wise, fail-closed.
The symbols this section needs (backtest_v12_engine.decide_for_symbol,
per_sym_vec_engine_{crypto,stocks}.vec_decide) have never existed in this repo -- the file was added
DOA in 6607501dc (Autosave 2026-10-07, 148 insertions / 0 deletions) and the unconditional import
failure made it print "PARITY GUARD FAILED: live_decide or vec_decide not importable" and exit 1 on
every run, so section 1 never got to run. It is kept, resolved dynamically against candidate names,
and still fails closed when unavailable -- it is simply no longer allowed to silence section 1.
"""
import sys, os, json, pathlib, copy, re, datetime, argparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SWITCH_SHEETS = (
    "STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL",
    "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY",
    "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND",
    "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER",
    "GLOBAL_RISK_GATES",
)
NON_SWITCH_LABELS = frozenset(SWITCH_SHEETS) | {
    "MODE", "ABLATION", "Switch", "Filter", "Sheet", "Color", "key", "tab", "#",
}
IDENT_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
TEMPLATES = {
    "CRYPTO": ("config.py", ("TEMPLATE_CRYPTO_LONG.xlsx", "TEMPLATE_CRYPTO_SHORT.xlsx")),
    "STOCKS": ("config_tradier.py", ("TEMPLATE_STOCKS_LONG.xlsx", "TEMPLATE_STOCKS_SHORT.xlsx")),
}

def is_switch_name(name):
    """A real switch is an UPPER_SNAKE_CASE identifier, not a tab name / legend row / banner."""
    if not isinstance(name, str):
        return False
    name = name.strip()
    if not name or name in NON_SWITCH_LABELS:
        return False
    if len(name) < 4 or "_" not in name:
        return False
    return bool(IDENT_RE.match(name))

def column_a_switches(xlsx_path):
    """Every column-A switch identifier in every sheet of one TEMPLATE, mapped to its sheets."""
    import openpyxl
    found = {}
    wb = openpyxl.load_workbook(str(xlsx_path), read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            for row in ws.iter_rows(min_row=1, min_col=1, max_col=1, values_only=True):
                name = row[0]
                if is_switch_name(name):
                    found.setdefault(name.strip(), set()).add(f"{xlsx_path.name}!{ws.title}")
    finally:
        wb.close()
    return found

def config_surfaces():
    """Pristine config surfaces -- imported BEFORE any engine so AUTO_VECTOR_LOAD cannot mask gaps."""
    import config as crypto_mod
    import config_tradier as stocks_mod
    return {
        "CRYPTO": (crypto_mod.Config(), crypto_mod.Config, crypto_mod),
        "STOCKS": (stocks_mod.TradierConfig(), stocks_mod.TradierConfig, stocks_mod),
    }

def key_present(name, surfaces):
    inst, klass, mod = surfaces
    return hasattr(inst, name) or hasattr(klass, name) or hasattr(mod, name)

def check_template_parity():
    """Section 1: returns (report dict, list of human-readable breach lines)."""
    surfaces = config_surfaces()
    report = {"generated": datetime.datetime.now(datetime.timezone.utc).isoformat(), "families": {}}
    breaches = []
    for family, (config_file, template_files) in TEMPLATES.items():
        switches = {}
        for template_file in template_files:
            path = ROOT / "SPREADSHEETS" / template_file
            if not path.exists():
                breaches.append(f"{family}: missing template {path}")
                continue
            for name, sheets in column_a_switches(path).items():
                switches.setdefault(name, set()).update(sheets)
        other = "STOCKS" if family == "CRYPTO" else "CRYPTO"
        missing = {}
        for name in sorted(switches):
            if key_present(name, surfaces[family]):
                continue
            missing[name] = {
                "sheets": sorted(switches[name]),
                "class": "WRONG_TEMPLATE_LEAK" if key_present(name, surfaces[other]) else "UNDEFINED_SWITCH",
                "other_config_has_it": key_present(name, surfaces[other]),
            }
        report["families"][family] = {
            "config_file": config_file,
            "templates": list(template_files),
            "switch_keys_checked": len(switches),
            "missing_count": len(missing),
            "missing": missing,
        }
        for name, info in missing.items():
            breaches.append(f"{family} -> {config_file}: {name} [{info['class']}] sheets={info['sheets']}")
    return report, breaches

def run_live_vs_vector_rerun():
    """Section 2: honest rerun. Fails closed if the real callables cannot be resolved."""
    import numpy as np
    errs = []
    for p in ["v12_quick_engine.py", "backtest_v12_engine.py", "tradier_manage.py", "ez_manage.py"]:
        if not (ROOT / p).exists():
            errs.append(f"missing live/vector file {p}")
    live_fn, live_src = _resolve("backtest_v12_engine", ("decide_for_symbol", "decide", "run_symbol"))
    vec_fn, vec_src = _resolve("per_sym_vec_engine_crypto", ("vec_decide", "decide", "sweep_variants"))
    if vec_fn is None:
        vec_fn, vec_src = _resolve("per_sym_vec_engine_stocks", ("vec_decide", "decide", "sweep_variants"))
    if live_fn is None or vec_fn is None:
        errs.append(f"live callable={live_src} vector callable={vec_src} -- no real per-bar decision entry point exists; cannot rerun live vs vector element-wise (do not fake pass)")
        return errs
    sample_switches = [
        ("WT_15M_BOUNCE_OPEN_ENABLED", True),
        ("BB_SQUEEZE_ENTRY_ENABLED", True),
        ("AUGMENT_FALLBACK_GAIN_PCT", 1.0),
        ("AUGMENT_FALLBACK_REDUCE_ENABLED", True),
    ]
    for switch, val in sample_switches:
        snap, n = _frozen_snapshot(np)
        import config as cfg_mod
        cfg = cfg_mod.Config()
        cfg.PARITY_MIN_DECISION_TF = "15m"
        cfg.PARITY_DISABLE_NON_VECTORIZABLE = True
        setattr(cfg, switch, val)
        try:
            live_res = _call(live_fn, copy.deepcopy(snap), cfg)
            vec_res = _call(vec_fn, copy.deepcopy(snap), cfg)
            live_mask = _mask(np, live_res)
            vec_mask = _mask(np, vec_res)
            if live_mask is None or vec_mask is None or live_mask.shape != vec_mask.shape:
                errs.append(f"switch {switch}: live/vector did not return comparable entry_mask -- cannot prove parity element-wise")
                continue
            mism = int((live_mask != vec_mask).sum())
            if mism:
                idx = np.where(live_mask != vec_mask)[0][:5].tolist()
                errs.append(f"switch {switch}={val}: live vs vector mask mismatch {mism}/{n} at bars {idx}")
        except Exception as exc:
            errs.append(f"switch {switch} rerun failed: {type(exc).__name__}: {exc}")
    return errs

def _resolve(module_name, candidates):
    try:
        mod = __import__(module_name)
    except Exception as exc:
        return None, f"{module_name} not importable ({type(exc).__name__}: {exc})"
    for cand in candidates:
        fn = getattr(mod, cand, None)
        if callable(fn):
            return fn, f"{module_name}.{cand}"
    return None, f"{module_name} has none of {list(candidates)}"

def _call(fn, snap, cfg):
    try:
        return fn("MU_LONG", cfg, snap)
    except TypeError:
        return fn(snap, cfg, is_long=True)

def _mask(np, res):
    if isinstance(res, dict) and "entry_mask" in res:
        return np.asarray(res["entry_mask"], dtype=bool)
    return None

def _frozen_snapshot(np):
    for cand in [ROOT / "MU.npz", ROOT / "backtest_v8/indicators/BTCUSDT.npz",
                 pathlib.Path("/home/niels/binance-sandbox/MU.npz")]:
        if cand.exists():
            try:
                z = dict(np.load(str(cand)))
                n = min(200, len(z.get("close", [])))
                snap = {k: (np.copy(v[:n]) if hasattr(v, "__len__") else v) for k, v in z.items()}
                if "high_15m" not in snap and "close" in snap:
                    snap["high_15m"] = snap["close"].copy()
                    snap["low_15m"] = snap["close"].copy()
                    snap["high_15m_prev"] = np.roll(snap["high_15m"], 1)
                    snap["low_15m_prev"] = np.roll(snap["low_15m"], 1)
                return snap, n
            except Exception:
                continue
    n = 200
    close = np.linspace(100, 110, n)
    snap = {
        "close": close.copy(),
        "high_15m": close + 1,
        "low_15m": close - 1,
        "high_15m_prev": np.roll(close + 1, 1),
        "low_15m_prev": np.roll(close - 1, 1),
        "wt1_5m": np.zeros(n),
        "wt2_5m": np.zeros(n),
        "timestamps": np.arange(n) * 900,
    }
    return snap, n

def main():
    parser = argparse.ArgumentParser(description="TEMPLATE<->config switch parity guard")
    parser.add_argument("--rerun", action="store_true", help="also attempt the honest live-vs-vector rerun (fails closed when unavailable)")
    parser.add_argument("--json", default=str(ROOT / "data/reports/switch_parity_breaches.json"), help="where to write the machine-readable breach report")
    args = parser.parse_args()
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("PARITY GUARD FAILED: openpyxl not importable -- cannot read TEMPLATE column A")
        return 1
    report, breaches = check_template_parity()
    out = pathlib.Path(args.json)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(out.suffix + ".tmp")
        with open(tmp, "w") as fh:
            json.dump(report, fh, indent=1, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, out)
    except OSError as exc:
        print(f"PARITY GUARD WARNING: could not write {out}: {exc}")
    total_keys = sum(f["switch_keys_checked"] for f in report["families"].values())
    rerun_errs = run_live_vs_vector_rerun() if args.rerun else None
    if breaches:
        print(f"PARITY GUARD FAILED: Missing from configs: {len(breaches)} total")
        for family in ("CRYPTO", "STOCKS"):
            fam = report["families"].get(family)
            if not fam:
                continue
            print(f"  {family} -> {fam['config_file']}: {fam['missing_count']} missing of {fam['switch_keys_checked']} TEMPLATE switch keys")
            for name, info in sorted(fam["missing"].items()):
                print(f"    {name}  [{info['class']}]  {info['sheets']}")
        if rerun_errs:
            print("  rerun section:")
            for e in rerun_errs:
                print("    -", e)
        print(f"  report: {out}")
        return 1
    if rerun_errs:
        print("PARITY GUARD FAILED (honest rerun, no hash, no vector-vector):")
        for e in rerun_errs:
            print(" -", e)
        return 1
    suffix = " + live-vs-vector rerun element-wise" if args.rerun else " (rerun section not requested: --rerun)"
    print(f"PARITY GUARD OK: {total_keys} TEMPLATE keys present in config.py / config_tradier.py{suffix}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
