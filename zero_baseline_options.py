import math
import datetime


def evaluate_stock_zero_baseline_option(symside, prepared, overrides, baseline, rows, evaluate):
    if not str(symside).endswith(("_LONG", "_SHORT")) or prepared.get("mode") != "tradier" or int(baseline.get("trades") or 0) != 0:
        return None
    switch = "ENABLE_TR_SWING_ENTRIES"
    if overrides.get(switch) is True:
        return None
    option = next(((tab, row_switch, candidate) for tab, row_switch, candidate in rows if tab == "GLOBAL_RISK_GATES" and row_switch == switch and candidate is True), None)
    if option is None:
        return None
    result = evaluate(prepared, {**overrides, switch: True}) or {}
    gain = result.get("gain_pct")
    base_gain = baseline.get("gain_pct")
    if gain is None or base_gain is None or not math.isfinite(float(gain)) or not math.isfinite(float(base_gain)):
        return None
    if not result.get("valid") or int(result.get("trades") or 0) <= 0 or float(gain) <= max(0.0, float(base_gain)):
        return None
    net_dollars = result.get("net_gain_dollars")
    if net_dollars is None or not math.isfinite(float(net_dollars)) or float(net_dollars) <= 0.0:
        return None
    return {"sheet": option[0], "switch": switch, "candidate": True, "delta": float(gain) - float(base_gain), "result": result, "baseline": dict(baseline), "baseline_overrides": dict(overrides), "eval_ts": datetime.datetime.now(datetime.timezone.utc).isoformat()}


def promote_measured_option(workbook, progress, overrides, cumulative_gain, option):
    if option is None or overrides != option["baseline_overrides"] or float(cumulative_gain) != float(option["baseline"]["gain_pct"]) or overrides.get(option["switch"]) is True:
        return None
    worksheet = workbook[option["sheet"]]
    headers = {str(cell.value).strip().upper(): cell.column for cell in worksheet[2] if cell.value is not None}
    columns = {name: headers[name] for name in ("SWITCH", "DEFAULT", "OVERRIDE", "BASELINE", "HUSTLE_DELTA", "VECTOR_DELTA")}
    target = next((row for row in range(3, worksheet.max_row + 1) if worksheet.cell(row, columns["SWITCH"]).value == option["switch"] and worksheet.cell(row, columns["DEFAULT"]).value is True), None)
    if target is None:
        return None
    key = f'ZERO_BASELINE_OPTION!{target}:{option["switch"]}=True'
    if key in progress.get("done", {}):
        return None
    before = float(cumulative_gain)
    after = float(option["result"]["gain_pct"])
    delta = after - before
    worksheet.cell(target, columns["OVERRIDE"]).value = option["switch"] + "=True"
    worksheet.cell(target, columns["BASELINE"]).value = before
    worksheet.cell(target, columns["HUSTLE_DELTA"]).value = delta
    worksheet.cell(target, columns["VECTOR_DELTA"]).value = delta
    updated = {**overrides, option["switch"]: True}
    progress.setdefault("done", {})[key] = {"delta": delta, "promoted": True, "promoted_how": "zero_baseline_option", "ts": option["eval_ts"], "vec_gain": after, "trades": option["result"]["trades"], "yellows": {}, "cumulative_before": before, "cumulative_after": after, "complete": True, "tab": option["sheet"], "row": target, "switch": option["switch"], "cand": "True", "parts": [option["switch"] + "=True"]}
    progress["cumulative_overrides"] = updated
    progress["cumulative_gain"] = after
    progress["zero_baseline_option"] = {"done_key": key, "original_baseline_gain": before, "candidate_gain": after, "delta": delta, "net_gain_dollars": option["result"]["net_gain_dollars"]}
    return updated, after
