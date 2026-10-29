#!/usr/bin/env python3
"""build_cat_side_defaults_4 — build data/per_sym_settings.json: the FOUR default sets (CRYPTO_LONG/CRYPTO_SHORT/STOCKS_LONG/
STOCKS_SHORT) read from the TEMPLATE_{cat_side}.xlsx defaults (USER 2026-09-30; consumed via cat_side_defaults.py).

Per template: every non-grey (tab, switch) group's is_default=YES / bold row (v15_pilot.template_bold_defaults, fail-closed on
0/2 defaults) + every yellow filter's bold "FILTER=opt" header. Values are typed like the venue field (config.Config for crypto,
config_tradier.TradierConfig for stocks, else QuickConfig with apply_tradier_defaults() for stocks). Only keys that exist in
the venue config or QuickConfig are kept. Report: per cat_side, how many values differ from today's single global value.
Refuses to write when any template violates the one-default rule. Run after every tools/v15_avg_delta_apply.py --apply.
"""
import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

_MISSING_V = object()
STAGE_ONLY = "--stage-only" in sys.argv
# OPERATOR LAW 2026-10-27, verbatim: "NO config or live trading has ANY default that is NOT overridden by
# TEMPLATE_ switch default or override. TEMPLATE is the ONLY authority any default that exists in any trading
# or config script can ONLY exist because NO switch exists for it just yet."
# v15_pilot.template_bold_defaults DISCARDS a bold whenever it differs from the config value (UNTRUSTED_BOLD) —
# i.e. in the only case where the bold decides anything. That runs the authority backwards, so this builder
# reinstates every discarded scalar bold. A container-valued config field (dict/list/set) stays: a scalar cell
# cannot express it, so it is a switch that does not exist yet, not a default the sheet chose.
BOLD_WINS = os.environ.get("CSD4_BOLD_WINS", "1") != "0"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import v15_pilot as P  # noqa: E402
import cat_side_defaults as CSD  # noqa: E402

TEMPLATES = {"CRYPTO_LONG": "TEMPLATE_CRYPTO_LONG.xlsx", "CRYPTO_SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx", "STOCKS_LONG": "TEMPLATE_STOCKS_LONG.xlsx", "STOCKS_SHORT": "TEMPLATE_STOCKS_SHORT.xlsx"}
PROBE = {"CRYPTO_LONG": "BTCUSDC_LONG", "CRYPTO_SHORT": "BTCUSDC_SHORT", "STOCKS_LONG": "AAPL_LONG", "STOCKS_SHORT": "AAPL_SHORT"}


SECRET_RE = re.compile(r"API_KEY|SECRET|TOKEN|PASSWORD|PASSPHRASE|ACCOUNT_ID|PRIVATE", re.I)


def _scalar(v):
    return v is None or isinstance(v, (bool, int, float, str))


def _same(a, b):
    if a == b:
        return True
    try:
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool) and float(a) == float(b)
    except Exception:
        return False


def fill_every_field(kept, live_vals, quick_vals):
    """USER 2026-09-30: ALWAYS four settings per switch. Every scalar non-secret field of the venue config / QuickConfig that the
    template does not carry gets an explicit entry (= its current value) so each cat_side map holds ALL fields. A field whose
    live value and QuickConfig value DISAGREE is NOT filled (filling live's value would override the sweep engine's own value,
    e.g. commission 0.0 vs 0.08 — BIBLE §17.3): those are listed in _meta.conflicts_quick_vs_live for a curated decision."""
    filled, conflicts, skipped = [], [], []
    for k in sorted(set(live_vals) | set(quick_vals)):
        if k in kept:
            continue
        lv, qv = live_vals.get(k, _MISSING_V), quick_vals.get(k, _MISSING_V)
        ref = lv if lv is not _MISSING_V else qv
        if SECRET_RE.search(k) or not _scalar(ref):
            skipped.append(k)
            continue
        if lv is not _MISSING_V and qv is not _MISSING_V and _scalar(qv) and not _same(lv, qv):
            conflicts.append(k)
            continue
        kept[k] = ref
        filled.append(k)
    return filled, conflicts, skipped



def venue_values(stocks: bool) -> dict:
    import v12_quick_engine as V
    qc = V.QuickConfig()
    if stocks:
        qc.apply_tradier_defaults()
    out = {k: getattr(qc, k) for k in dir(qc) if k.isupper() and not k.startswith("_")}
    if stocks:
        import config_tradier as CT
        live = CT.TradierConfig()
    else:
        import config as C
        live = C.Config()
    live_vals = {}
    for k in dir(live):
        if k.isupper() and not k.startswith("_"):
            try:
                live_vals[k] = getattr(live, k)
            except Exception:
                pass
    out.update(live_vals)
    quick_vals = {k: getattr(qc, k) for k in dir(qc) if k.isupper() and not k.startswith("_")}
    return out, live_vals, quick_vals


def grey_switches(path: Path) -> set:
    """Switch names whose column-A row group is GREY = retired, excluded from calculation. The pilot skips them, so
    neither their bold nor a yellow header of theirs is an authority: the config value stays (operator 2026-10-27 on
    DAYTRADE_DC_TARGET_TF / TECHNICAL_DC_TARGET_TF: "staying grey")."""
    import openpyxl
    wb = openpyxl.load_workbook(str(path))
    out = set()
    try:
        for tab in P.SWITCH_SHEETS:
            if tab not in wb.sheetnames:
                continue
            ws = wb[tab]
            for r in range(3, ws.max_row + 1):
                a = ws.cell(row=r, column=1).value
                if a in (None, ""):
                    continue
                f = ws.cell(row=r, column=1).font
                if f is not None and f.color is not None and str(getattr(f.color, "rgb", "") or "").upper() in P.GREY_SKIP_RGB:
                    out.add(str(a).strip())
        return out
    finally:
        wb.close()


def filter_header_defaults(path: Path) -> dict:
    import openpyxl
    wb = openpyxl.load_workbook(str(path), read_only=False)
    out = {}
    for tab in P.SWITCH_SHEETS:
        if tab not in wb.sheetnames:
            continue
        ws = wb[tab]
        for c in range(1, ws.max_column + 1):
            h = ws.cell(row=2, column=c)
            if isinstance(h.value, str) and "=" in h.value and h.font is not None and h.font.b:
                f, o = h.value.split("=", 1)
                out.setdefault(f.strip(), o.strip())
    wb.close()
    return out


def main():
    data = {"_meta": {"built_at": datetime.datetime.utcnow().isoformat() + "Z", "by": "tools/build_cat_side_defaults_4.py", "templates": {}}}
    report = {}
    bad_all = {}
    for cs, name in TEMPLATES.items():
        # 2026-10-28: the default was ROOT/"SPREADSHEETS", which is the LEGACY copy — the canonical workbooks are
        # TEMPLATE_FINAL_NORM, so every template change since the move was invisible to this builder.
        path = Path(os.environ.get("CSD4_TEMPLATE_DIR") or (ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM")) / name  # PROMO 2026-10-01: env overrides keep the live file untouched in dry-runs
        stocks = cs.startswith("STOCKS")
        typed, live_vals, quick_vals = venue_values(stocks)
        try:
            import per_sym_store as _pss2
            _promos_sql = _pss2.get_cat_side_promotions()
            promoted = set((_promos_sql.get(cs) or {}).keys()) if _promos_sql else set()
            # file fallback if SQL empty (first boot)
            if not promoted:
                _pp = Path(os.environ.get("CSD4_PROMOTIONS") or (ROOT / "data" / "cat_side_promotions.json"))
                promoted = set((json.loads(_pp.read_text()).get(cs) or {}).keys()) if _pp.exists() else set()
        except Exception:
            _pp = Path(os.environ.get("CSD4_PROMOTIONS") or (ROOT / "data" / "cat_side_promotions.json"))
            promoted = set((json.loads(_pp.read_text()).get(cs) or {}).keys()) if _pp.exists() else set()
        rows, bad = P.template_bold_defaults(path, typed, typed, promoted)
        grey = grey_switches(path)
        reinstated, container_only, empty_bold = [], [], []
        if P.UNTRUSTED_BOLD and BOLD_WINS:
            for _tab, _sw, _v, _truth in list(P.UNTRUSTED_BOLD):
                if isinstance(_truth, (dict, list, tuple, set)):
                    container_only.append(_sw)
                    continue
                if _v is None or (isinstance(_v, str) and not _v.strip()):
                    empty_bold.append(_sw)  # template defect: bold formatting on an empty cell is not a value
                    continue
                if _sw in grey:
                    continue
                rows.update(P._switch_overrides(_sw, _v))
                reinstated.append({"tab": _tab, "switch": _sw, "bold": _v, "config_was": _truth})
            print(f"[{cs}] BOLD-WINS: reinstated {len(reinstated)} bold defaults the pilot discarded for differing from config: {[r['switch'] for r in reinstated[:8]]}")
            if container_only:
                print(f"[{cs}] BOLD-WINS: {len(container_only)} left to config because the config value is a container a scalar cell cannot express: {container_only[:6]}")
            if empty_bold:
                print(f"[{cs}] BOLD-WINS-DEFECT: {len(empty_bold)} bold cells are EMPTY — not a default, config value kept, fix the sheet: {empty_bold[:6]}")
        elif P.UNTRUSTED_BOLD:
            print(f"[{cs}] {len(P.UNTRUSTED_BOLD)} placeholder bold values skipped (real value kept): {[x[1] for x in P.UNTRUSTED_BOLD[:6]]}")
        if bad:
            bad_all[cs] = bad[:10]
            continue
        vals = dict(rows)
        for f, o in filter_header_defaults(path).items():
            if f not in vals:
                _fv = P._parse_opt_value(o, typed.get(f))
                # Same inversion as UNTRUSTED_BOLD: a bold yellow header that DIFFERS from config was skipped, which is
                # the only case where it decides anything. Under BOLD_WINS the header is the authority; only a
                # container-valued config field stays, because no scalar header can express one.
                _skip = (f in grey or isinstance(typed.get(f), (dict, list, tuple, set))) if BOLD_WINS else (
                    f in typed and f not in promoted and (isinstance(typed[f], (dict, list, tuple, set)) or not P._same_default(_fv, typed[f]))
                )
                if f in typed and f not in promoted and _skip:
                    continue  # the real value stays
                vals[f] = _fv
        kept = {k: v for k, v in vals.items() if k in typed}
        filled, conflicts, skipped = fill_every_field(kept, live_vals, quick_vals)
        if cs in ("CRYPTO_LONG", "CRYPTO_SHORT"):
            # 2026-10-03 P0 (BIBLE §64 4th enforcement): template False bolds are dropped as
            # "untrusted" (differ from config truth) and refilled True from config = entry death.
            # Crypto defaults must NEVER carry ABLATION_DISABLE_*=True. Force False, log it.
            _p0 = [k for k, v in kept.items() if k.startswith("ABLATION_DISABLE") and (v is True or str(v).strip().lower() == "true")]
            for k in _p0:
                kept[k] = False
            if _p0:
                print(f"[{cs}] P0-ABLATION: forced False (was True): {_p0}")
            _abl_universe = sorted(k for k in quick_vals if k.startswith("ABLATION_DISABLE"))
            _abl_added = [k for k in _abl_universe if k not in kept]
            for k in _abl_universe:
                kept[k] = False
            conflicts[:] = [k for k in conflicts if k not in _abl_universe]
            if _abl_added:
                print(f"[{cs}] P0-ABLATION-19: emitted False for conflict-skipped: {_abl_added}")
        if cs in ("STOCKS_LONG", "STOCKS_SHORT"):
            _s2 = ["ABLATION_DISABLE_ENTRY_RANKING", "ABLATION_DISABLE_ENTRY_TECHNICAL"]
            for k in _s2:
                kept[k] = False
            print(f"[{cs}] P0-ABLATION-STOCKS2: pinned False: {_s2}")
        # OPERATOR RULING 2026-10-29 ("v12_quick_engine.py just like any config or live trading script needs to accept absolute
        # authority from TEMPLATE_* values unless there is no switch there for the cat_side"): the 2026-10-04 P0-FOSSIL pins
        # (MOM3_FILTER_TF CRYPTO_SHORT/STOCKS_SHORT, VIGILANCE_GUARD_ENABLED CRYPTO_SHORT, WT_CROSSUNDER_FINAL_ENABLED STOCKS_SHORT)
        # that kept the live value over the template bold are RETIRED. The bold is the default; a wanted value is changed IN THE SHEET.
        _fossil_retired = {"MOM3_FILTER_TF": ("CRYPTO_SHORT", "STOCKS_SHORT"), "VIGILANCE_GUARD_ENABLED": ("CRYPTO_SHORT",), "WT_CROSSUNDER_FINAL_ENABLED": ("STOCKS_SHORT",)}
        for _k, _sides in _fossil_retired.items():
            if cs in _sides and _k in live_vals and _k in kept and str(kept[_k]) != str(live_vals[_k]):
                data["_meta"].setdefault("fossil_pins_retired_20261029", {}).setdefault(cs, []).append({"key": _k, "bold": kept[_k], "live_was": live_vals[_k]})
                print(f"[{cs}] FOSSIL-RETIRED (operator ruling 2026-10-29): {_k} follows the template bold {kept[_k]!r} (live config carried {live_vals[_k]!r})")
        if STAGE_ONLY and CSD.PATH.exists():
            # USER-SAFETY 2026-09-30: live hot-reloads this file. --stage-only keeps every value that is ALREADY in the file (live
            # behaviour unchanged) and only adds the missing keys (= current config values). Promotions stay staged in the templates.
            _old = (json.loads(CSD.PATH.read_text()).get(cs) or {})
            _fset = set(filled)
            _held = [k for k in kept if k in _old and k not in _fset and str(_old[k]) != str(kept[k])]
            for k in _held:
                kept[k] = _old[k]
            data["_meta"].setdefault("staged_not_live", {})[cs] = {k: None for k in _held}
            data["_meta"]["staged_not_live"][cs] = _held
            print(f"[{cs}] STAGE-ONLY: {len(_held)} promoted/changed values held back from live: {_held[:10]}")
        data.setdefault("_meta", {}).setdefault("bold_wins_reinstated", {})[cs] = reinstated
        data["_meta"].setdefault("bold_wins_container_only", {})[cs] = container_only
        data["_meta"].setdefault("bold_wins_empty_bold_defect", {})[cs] = empty_bold
        data["_meta"].setdefault("grey_switches_excluded", {})[cs] = len(grey)
        data.setdefault("_meta", {}).setdefault("conflicts_quick_vs_live", {})[cs] = conflicts
        data["_meta"].setdefault("filled_from_config", {})[cs] = len(filled)
        data["_meta"].setdefault("skipped_non_scalar_or_secret", {})[cs] = len(skipped)
        dropped = sorted(set(vals) - set(kept))
        diff = sorted(k for k, v in kept.items() if k in live_vals and str(live_vals[k]) != str(v))
        not_in_live = sorted(k for k in kept if k not in live_vals)
        data[cs] = kept
        data["_meta"]["templates"][cs] = {"file": name, "md5": hashlib.md5(path.read_bytes()).hexdigest()}
        report[cs] = {"keys": len(kept), "dropped_not_a_config_key": dropped, "differs_from_global_config": diff, "not_in_live_config": len(not_in_live)}
        print(f"[{cs}] {len(kept)} defaults | differ from global live config: {len(diff)} {diff[:8]} | not a live-config field (engine/in-code only): {len(not_in_live)} | dropped non-keys: {dropped[:6]}")
    if bad_all:
        sys.exit(f"REFUSED: template default violations {bad_all} — fix the templates, nothing written")
    _out = Path(os.environ.get("CSD4_OUT") or CSD.PATH)
    tmp = _out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str, sort_keys=True))
    tmp.replace(_out)
    # dual-write: JSON stays as generated view, SQL primary keeps herds alive if file vanishes.
    # 2026-10-28: kv_put was NOT gated by CSD4_OUT, so a run redirected to a scratch file still published the SQL
    # row that every live reader prefers — a "dry run" went live. CSD4_OUT now means dry: redirecting the JSON
    # suppresses the SQL write unless CSD4_KV_WRITE=1 says otherwise.
    _redirected = bool(os.environ.get("CSD4_OUT")) and Path(os.environ["CSD4_OUT"]) != CSD.PATH
    if _redirected and os.environ.get("CSD4_KV_WRITE") != "1":
        print(f"[cat_side_defaults_4] CSD4_OUT redirected to {_out} → SQL primary NOT written (set CSD4_KV_WRITE=1 to publish)")
    else:
        try:
            import per_sym_store as _pss
            _pss.kv_put(_pss.KV_CAT_SIDE_DEFAULTS_4, data)
        except Exception as _e:
            print(f"[cat_side_defaults_4] kv_put failed (JSON view still written): {_e}")
    rp = ROOT / "data" / "reports" / f"cat_side_defaults_4_{datetime.datetime.now().strftime('%Y%m%d%H%M')}.json"
    rp.write_text(json.dumps(report, indent=1, default=str))
    _kv = "SQL primary SKIPPED (redirected dry run)" if (_redirected and os.environ.get("CSD4_KV_WRITE") != "1") else f"{CSD.PATH.name}→SQL"
    print(f"[written] {_out}  [report] {rp}  [kv] {_kv}")


if __name__ == "__main__":
    main()
