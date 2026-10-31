"""Review-only current-domain and wiring-era exploration guard; no pilot hook or defaults change."""
import copy


def filter_current_cells(proposals, category, template_index, runtime_root=None):
    from tools.switch_types import coerce
    venue = "tradier" if category.startswith("STOCKS") else "crypto"
    allowed = {(str(switch).strip(), str(candidate).strip(), str(header).strip()) for block in template_index.get("tabs", {}).values() for row, switch, candidate, default in block["rows"] for header in block["yellows"]}
    accepted, rejected = [], []
    for original in proposals:
        row = dict(original)
        switch, candidate = row.get("switch"), row.get("cand")
        field, option = row.get("filter"), row.get("opt")
        reason = None
        if not switch or not field or candidate is None or option is None or str(row.get("cat_side") or category) != category:
            reason = "missing_identity_or_wrong_category"
        else:
            valid_switch, _, switch_reason = coerce(switch, candidate, venue, strict_unknown=True, root=runtime_root)
            valid_filter, _, filter_reason = coerce(field, option, venue, strict_unknown=True, root=runtime_root)
            if not valid_switch or not valid_filter:
                reason = "unknown_or_invalid_current_field: " + str(switch_reason if not valid_switch else filter_reason)
            elif (str(switch).strip(), str(candidate).strip(), f"{str(field).strip()}={str(option).strip()}") not in allowed:
                reason = "outside_exact_current_template"
        if reason:
            rejected.append({"proposal": row, "reason": reason})
        else:
            accepted.append(row)
    return {"accepted": accepted, "rejected": rejected, "scope": "current runtime field/type plus exact current template; no default inference or field installation"}


def protected_headers(switch, headers, wiring_registry, evidence_by_cell):
    result = set()
    for header in headers:
        evidence = (evidence_by_cell or {}).get(header) or {}
        epochs = evidence.get("wiring_sha256_by_field") or {}
        for field in (switch, str(header).partition("=")[0]):
            current = (wiring_registry or {}).get(field) or {}
            if current.get("new_or_changed") and (not current.get("wiring_sha256") or epochs.get(field) != current["wiring_sha256"] or str(evidence.get("reason") or "").startswith("UNWIRED_CALCULATED")):
                result.add(header)
    return result


def protect_policy_skips(info, switch, headers, wiring_registry, evidence_by_cell, category, symbol_side, tab, row_key, round_id, untried_p=0.05):
    from tools import v15_v18_planner as planner
    protected = protected_headers(switch, headers, wiring_registry, evidence_by_cell)
    result = copy.deepcopy(info)
    def keep(kind, header):
        if kind == "row":
            return bool(protected) and planner.floor_draw(category, symbol_side, tab, row_key, round_id) < untried_p
        return header in protected and planner.floor_draw(category, symbol_side, tab, row_key + "|" + str(header), round_id) < untried_p
    stats = planner.floor_apply(result, keep, untried_p)
    return {"info": result, "protected": protected, "stats": stats, "scope": "review-only existing untried floor; counts-policy skips may be rescued, structural skips never changed; not a finite-time trial guarantee"}


def decide_cells_review(headers, binding_map, tab, switch, candidate, running_set, *, wiring_registry, evidence_by_cell, binding_epochs=None, **kwargs):
    from tools import v15_v20_selector as selector
    protected = protected_headers(switch, headers, wiring_registry, evidence_by_cell)
    combined = {"keep": [], "settled": {}, "counts": {}, "protected_new_wiring": sorted(protected)}
    for header in headers:
        options = dict(kwargs)
        current_map = binding_map
        if header in protected:
            options["model_p"] = {key: value for key, value in (options.get("model_p") or {}).items() if key != header}
            options["untried_cells"] = set(options.get("untried_cells") or ()) | {header}
            options["dead_end_row"] = False
            options["predicted_noop_cells"] = set(options.get("predicted_noop_cells") or ()) - {header}
            options["exhausted_cells"] = {key: value for key, value in (options.get("exhausted_cells") or {}).items() if key != header}
            for field in (switch, str(header).partition("=")[0]):
                current = (wiring_registry or {}).get(field) or {}
                if current.get("new_or_changed") and (binding_epochs or {}).get(field) != current.get("wiring_sha256"):
                    current_map = None
        decision = selector.decide_cells([header], current_map, tab, switch, candidate, running_set, **options)
        combined["keep"].extend(decision["keep"])
        combined["settled"].update(decision["settled"])
        for key, value in decision["counts"].items():
            combined["counts"][key] = combined["counts"].get(key, 0) + value
    return combined
