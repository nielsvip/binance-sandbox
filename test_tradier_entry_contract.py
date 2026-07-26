from tradier_entry_contract import flat_key_needs_evaluation, path_switch


def test_isolated_wt_dc_routes_flat_key_to_real_process_position():
    assert flat_key_needs_evaluation(
        satoshit_ok=False,
        stdev_ok=False,
        wt_force_open_enabled=False,
        wt_dc_path_enabled=True,
    )


def test_no_entry_family_does_not_waste_exact_engine_call():
    assert not flat_key_needs_evaluation(
        satoshit_ok=False,
        stdev_ok=False,
        wt_force_open_enabled=False,
        wt_dc_path_enabled=False,
    )


def test_existing_upstream_routes_remain_additive():
    for field in ("satoshit_ok", "stdev_ok", "wt_force_open_enabled"):
        values = {
            "satoshit_ok": False,
            "stdev_ok": False,
            "wt_force_open_enabled": False,
            "wt_dc_path_enabled": False,
        }
        values[field] = True
        assert flat_key_needs_evaluation(**values)


def test_explicit_path_switch_respects_false_and_missing_default():
    class Config:
        WT_DC_EXIT_ENABLED = False

    assert not path_switch(Config(), "WT_DC_EXIT_ENABLED", True)
    assert path_switch(Config(), "MISSING_PATH", True)
