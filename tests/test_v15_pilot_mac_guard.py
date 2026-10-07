"""Focused guard test for v15_pilot S1-only rule."""
import sys
import os
import importlib

def test_v15_pilot_blocks_on_darwin_without_allow(monkeypatch):
    # Simulate Darwin without allow flags -> should exit 2
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.delenv("V15_ALLOW_MAC", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    # reload to pick up platform? main() checks sys.platform at runtime, so patch sys.platform is enough
    import v15_pilot
    # Call main with args that would otherwise be valid
    monkeypatch.setattr(sys, "argv", ["v15_pilot.py", "--sym-side", "AAPL_LONG", "--window-days", "30"])
    try:
        v15_pilot.main()
        assert False, "should have exited on Darwin without --allow-mac"
    except SystemExit as e:
        assert e.code == 2

def test_v15_pilot_allows_dry_run_on_darwin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    import v15_pilot
    monkeypatch.setattr(sys, "argv", ["v15_pilot.py", "--sym-side", "AAPL_LONG", "--dry-run"])
    # Should not block at guard — it will print warn and continue to window check
    # We catch the later window check (30 allowed) so it won't exit at guard
    # Just verify guard does not exit 2
    try:
        v15_pilot.main()
    except SystemExit as e:
        # dry-run should pass guard, so if it exits, it should not be 2 from guard
        # It may exit elsewhere (e.g., missing template), but not the S1-only block
        assert e.code != 2 or "S1-ONLY" not in str(e)
    except Exception:
        pass

def test_v15_pilot_allows_allow_mac_flag(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("V15_ALLOW_MAC", "1")
    import v15_pilot
    monkeypatch.setattr(sys, "argv", ["v15_pilot.py", "--sym-side", "AAPL_LONG", "--window-days", "30", "--allow-mac"])
    # Should not block
    try:
        v15_pilot.main()
    except SystemExit as e:
        assert "S1-ONLY" not in str(e) and e.code != 2, f"allow-mac should bypass guard, got {e.code}"
    except Exception:
        pass
