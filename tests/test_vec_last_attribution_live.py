"""VEC_LAST labels must reflect actual native gates that could have blocked."""
import ast
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "ez_manage.py"


def test_vector_exit_does_not_claim_unchecked_lhll_predicate_was_bypassed():
    source = SOURCE.read_text()
    start = source.index("# 🚫 EXIT BLOCKER LH/LL")
    end = source.index("# 🛡️ MTF FILTER", start)
    block = source[start:end]
    assert "_vd_lhll_vec = bool(_parity_vec_order(reason))" in block
    assert 'append("EXIT_LH_LL")' not in block
    assert "and not _vd_lhll_vec" in block
    assert 'return "BLOCKED_EXIT_LH_LL_REQUIRED"' in block


def test_breaker_bypass_label_uses_enabled_path_ttl_probe_without_mutation():
    source = SOURCE.read_text()
    start = source.index("# Smart circuit breaker: diagnose losing positions")
    end = source.index("# ═══ MOMENTUM_RIDER BYPASS", start)
    block = source[start:end]
    assert "_cb_path_is_currently_disabled(" in block
    assert 'if _cb_would_block:\n                    logger.info' in block
    assert 'append("CIRCUIT_BREAKER")' in block
    assert "self.circuit_breaker._path_disabled(" not in block

    tree = ast.parse(source)
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"_cb_path_tag", "_cb_path_is_currently_disabled"}
    ]
    assert {node.name for node in selected} == {"_cb_path_tag", "_cb_path_is_currently_disabled"}
    namespace = {"time": __import__("time")}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), "exec"), namespace)
    probe = namespace["_cb_path_is_currently_disabled"]
    tag = "VEC_DRIVEN_OPEN_RECONCILE"
    breaker = SimpleNamespace(_disabled_paths={tag: 950.0})
    cfg = SimpleNamespace(CIRCUIT_BREAKER_ENABLED=True, CIRCUIT_BREAKER_PATH_DECAY_HOURS=1.0)
    assert probe(breaker, "VEC_DRIVEN_OPEN_RECONCILE_x", cfg, now=1000.0) is True
    assert probe(breaker, "VEC_DRIVEN_OPEN_RECONCILE_x", cfg, now=5000.0) is False
    cfg.CIRCUIT_BREAKER_ENABLED = False
    assert probe(breaker, "VEC_DRIVEN_OPEN_RECONCILE_x", cfg, now=1000.0) is False
    assert breaker._disabled_paths == {tag: 950.0}
