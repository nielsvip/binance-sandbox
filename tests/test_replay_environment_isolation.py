import ast
import os
from pathlib import Path

import pytest

def native_function(name):
    tree = ast.parse(Path("utils.py").read_text())
    node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    scope = {"os": os, "ENV_LOADED": False, "_hostname_cache": None}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "utils.py", "exec"), scope)
    return scope[name], scope

def test_gpg_disabled_before_any_io_or_logger_import(monkeypatch):
    function, scope = native_function("load_environment_from_gpg")
    monkeypatch.setenv("EZ_SKIP_ENV_GPG", "1")
    assert function() is False
    assert scope["ENV_LOADED"] is False

@pytest.mark.parametrize("environment,expected", [
    ("server", {"local": ("localhost", 6379)}),
    ("gateway", {"local": ("localhost", 6379), "server": ("10.0.0.3", 6379)}),
    ("macbook", {"local": ("localhost", 6379), "gateway": ("localhost", 6380), "server": ("localhost", 6381)}),
])
def test_explicit_environment_never_discovers_network(monkeypatch, environment, expected):
    import socket
    function, scope = native_function("_get_env_internal")
    monkeypatch.setenv("EZ_ENVIRONMENT", environment.upper())
    def forbidden(*args, **kwargs):
        raise AssertionError("Explicit environment must not probe host/network")
    for name in ("gethostname", "gethostbyname_ex", "socket"):
        monkeypatch.setattr(socket, name, forbidden)
    assert function() == {"env": environment, "redis_connections": expected}
    assert scope["_hostname_cache"] is None
