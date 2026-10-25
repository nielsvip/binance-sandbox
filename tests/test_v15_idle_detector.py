import json
from unittest.mock import patch

from tools import v15_idle_detector as detector


def test_idle_pins_only_the_hosts_assigned_side(tmp_path):
    hosts = tmp_path / "hosts.json"
    pins = tmp_path / "pins.json"
    state = tmp_path / "state.json"
    hosts.write_text(json.dumps({"hosts": [{"name": "s6", "ssh": ["s6"], "sides": ["SHORT"]}]}))
    pins.write_text(json.dumps({"sides": [], "hosts": []}))
    with patch.object(detector, "HOSTS_FILE", hosts), patch.object(detector, "PIN_FILE", pins), patch.object(detector, "STATE_FILE", state), patch.object(detector, "_sat", return_value=0.2), patch.object(detector, "_ssh", return_value="JPM_LONG\nJPM_SHORT\nLAC_LONG\nLAC_SHORT"):
        detector.main()
        first = json.loads(pins.read_text())["sides"]
        detector.main()
        second = json.loads(pins.read_text())["sides"]
    assert first == ["JPM_SHORT", "LAC_SHORT"]
    assert second == first
