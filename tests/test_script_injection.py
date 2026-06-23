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


def test_build_connect_script_reads_credentials_from_env_not_literals():
    '''connect() must read credentials via os.environ[...] lookups, never as literal arguments.

    Credentials are never embedded in the generated script text -- see
    test_credential_handling.py for the regression coverage proving the
    plaintext password never reaches the temporary script file on disk.
    '''
    script = wlst_mcp._build_connect_script()
    tree = ast.parse(script, mode="exec")
    connect_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "connect"
    ]
    assert connect_calls, "No call to connect() found in generated script"
    for node in connect_calls:
        assert len(node.args) == 3
        for arg in node.args:
            assert isinstance(arg, ast.Subscript), "connect() arguments must be os.environ[...] lookups"


def test_build_connect_script_references_expected_env_var_names():
    script = wlst_mcp._build_connect_script()
    assert wlst_mcp._ENV_USERNAME in script
    assert wlst_mcp._ENV_PASSWORD in script
    assert wlst_mcp._ENV_ADMIN_URL in script
