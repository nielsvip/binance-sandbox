"""Every Binance Client() must carry a socket timeout (USER 2026-10-10 latency root cause).

Without requests_params={"timeout": ...}, python-binance uses requests with NO
timeout: a stalled socket hangs the to_thread worker forever, leaking one thread
per event. Enough leaks exhaust the default executor and dispatch tasks park
4-50+ min (the ang STEP-span wedge). A timeout converts those into <=10s
exceptions handled by the existing BLOCK/UNCONFIRMED paths.
"""
import ast
import pathlib

FILES = ["ez_manage.py", "ez_positions_service.py"]


def _binance_client_calls(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name != "Client":
            continue
        keys = {kw.arg for kw in node.keywords if kw.arg}
        if "api_key" in keys and "api_secret" in keys:
            yield node


def _timeout_of(call):
    for kw in call.keywords:
        if kw.arg == "requests_params" and isinstance(kw.value, ast.Dict):
            for k, v in zip(kw.value.keys, kw.value.values):
                if isinstance(k, ast.Constant) and k.value == "timeout" and isinstance(v, ast.Constant):
                    return v.value
    return None


def test_all_binance_clients_have_socket_timeout():
    root = pathlib.Path(__file__).resolve().parent.parent
    for fname in FILES:
        tree = ast.parse((root / fname).read_text(), filename=fname)
        calls = list(_binance_client_calls(tree))
        assert calls, f"{fname}: no Binance Client() found — test is blind"
        for call in calls:
            timeout = _timeout_of(call)
            assert isinstance(timeout, (int, float)) and timeout > 0, (
                f"{fname}:{call.lineno}: Binance Client() without requests_params timeout "
                f"— hung sockets leak executor threads and park dispatches"
            )
