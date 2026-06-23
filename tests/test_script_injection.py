'''Regression tests for WLST/Jython script-injection hardening.

These tests verify that values coming from MCP tool callers (server names,
app names, credentials, etc.) are embedded into generated WLST scripts as
inert string literals, not as executable code, even when they contain
quotes, backslashes, or attempted breakout sequences.
'''
import ast

import pytest

import wlst_mcp


MALICIOUS_PAYLOADS = [
    "normal-value",
    "O'Brien",
    'has "double" quotes',
    "back\\slash",
    "line\nbreak",
    "'); import os; os.system('echo pwned'); ('",
    "trailing-quote'",
    "unicode-ñ-value",
]


@pytest.mark.parametrize("value", MALICIOUS_PAYLOADS)
def test_jython_str_literal_round_trips(value):
    '''The escaped literal must evaluate back to the exact original string.'''
    literal = wlst_mcp._jython_str_literal(value)
    assert ast.literal_eval(literal) == value


@pytest.mark.parametrize("value", MALICIOUS_PAYLOADS)
def test_jython_str_literal_is_single_valid_expression(value):
    '''The escaped literal must compile as exactly one expression (no breakout).'''
    literal = wlst_mcp._jython_str_literal(value)
    tree = ast.parse(literal, mode="eval")
    assert isinstance(tree.body, ast.Constant)


def _find_call_args(script: str, func_name: str):
    '''Parse a generated script and return the literal argument values of the first call to func_name.'''
    tree = ast.parse(script, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == func_name:
            return [ast.literal_eval(arg) for arg in node.args]
    raise AssertionError(f"No call to {func_name}() found in generated script")


@pytest.mark.parametrize("value", MALICIOUS_PAYLOADS)
def test_build_connect_script_escapes_username(value):
    script = wlst_mcp._build_connect_script("t3://localhost:7001", value, "pw")
    args = _find_call_args(script, "connect")
    assert args[0] == value


@pytest.mark.parametrize("value", MALICIOUS_PAYLOADS)
def test_build_connect_script_escapes_password(value):
    script = wlst_mcp._build_connect_script("t3://localhost:7001", "user", value)
    args = _find_call_args(script, "connect")
    assert args[1] == value


@pytest.mark.parametrize("value", MALICIOUS_PAYLOADS)
def test_build_connect_script_escapes_admin_url(value):
    script = wlst_mcp._build_connect_script(value, "user", "pw")
    args = _find_call_args(script, "connect")
    assert args[2] == value
