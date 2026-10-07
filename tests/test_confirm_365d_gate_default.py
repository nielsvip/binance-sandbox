"""365D gate defaults OFF until real certs exist (2026-10-02 USER order).

The file was never generated anywhere and the vector engine currently yields
1 trade/window on crypto (0/22 syms certifiable), so fail-closed = no trading
forever. Re-enable by setting CONFIRM_365D_GATE_ENABLED=True in config.py
after the engine entry-block is fixed and tools/confirm_365d.py --all has run.
"""
import ez_manage as em


def test_gate_ignored_by_default_until_configured():
    import config as cfg

    if bool(getattr(cfg, "CONFIRM_365D_GATE_ENABLED", False)):
        return
    ok, why = em._confirm_365d_allows("BTCUSDC", "LONG")
    assert ok is True
    assert "IGNORED 2026-10-02" in why
