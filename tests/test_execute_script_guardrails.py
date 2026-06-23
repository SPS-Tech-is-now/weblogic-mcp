'''Guardrails for wlst_execute_script, the one tool that runs arbitrary
caller-supplied WLST/Jython code.

1. It must not be registered as an MCP tool unless an operator explicitly
   opts in via WLST_ALLOW_EXECUTE_SCRIPT, since it is otherwise a full
   domain/host compromise vector for any caller of the MCP server.
2. It must support a dry_run mode that returns the script that would run
   without executing it, so a caller can inspect exactly what would happen
   before committing to it.

The registration check spawns a fresh subprocess per case (rather than
importlib.reload-ing the already-imported wlst_mcp module in-process) since
tool registration happens once at module import time, driven by the
environment at that moment -- exactly mirroring how the real MCP server
process starts.
'''
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

import wlst_mcp


SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")

_CHECK_REGISTERED_SNIPPET = (
    "import sys; sys.path.insert(0, sys.argv[1]); import wlst_mcp; "
    "print('REGISTERED' if 'wlst_execute_script' in wlst_mcp.mcp._tool_manager._tools else 'NOT_REGISTERED')"
)


def _tool_registration_status(env_overrides):
    env = {**os.environ, **env_overrides}
    result = subprocess.run(
        [sys.executable, "-c", _CHECK_REGISTERED_SNIPPET, SRC_DIR],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    return result.stdout.strip()


def test_execute_script_not_registered_by_default():
    env = {"WLST_ALLOW_EXECUTE_SCRIPT": ""}
    assert _tool_registration_status(env) == "NOT_REGISTERED"


@pytest.mark.parametrize("value", ["true", "1", "yes", "TRUE"])
def test_execute_script_registered_when_opted_in(value):
    env = {"WLST_ALLOW_EXECUTE_SCRIPT": value}
    assert _tool_registration_status(env) == "REGISTERED"


def test_execute_script_dry_run_does_not_execute(monkeypatch):
    executed = {"called": False}

    async def fake_execute(*args, **kwargs):
        executed["called"] = True
        return {"success": True, "returncode": 0, "stdout": "", "stderr": "", "error": None}

    monkeypatch.setattr(wlst_mcp, "_execute_wlst_script", fake_execute)

    params = wlst_mcp.ExecuteScriptInput(script="print('hello')", dry_run=True)
    result = asyncio.run(wlst_mcp.wlst_execute_script(params))

    assert executed["called"] is False
    assert "print('hello')" in result


def test_execute_script_without_dry_run_still_executes(monkeypatch):
    executed = {"called": False}

    async def fake_execute(*args, **kwargs):
        executed["called"] = True
        return {"success": True, "returncode": 0, "stdout": "ok", "stderr": "", "error": None}

    monkeypatch.setattr(wlst_mcp, "_execute_wlst_script", fake_execute)

    params = wlst_mcp.ExecuteScriptInput(script="print('hello')", dry_run=False)
    asyncio.run(wlst_mcp.wlst_execute_script(params))

    assert executed["called"] is True
