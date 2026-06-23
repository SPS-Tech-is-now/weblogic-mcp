'''Regression tests proving WebLogic credentials never touch the temporary
WLST script file written to disk.

`_execute_wlst_script` writes its `script` argument verbatim to a
NamedTemporaryFile that WLST then executes. Previously, `_build_connect_script`
embedded the admin password as a literal in that script text, so the plaintext
password sat on disk for the duration of every call (and could be left behind
if the process was killed before cleanup). Credentials must instead be passed
to the WLST subprocess via its environment, never written to the script file.
'''
import asyncio

import pytest

import wlst_mcp


class _FakeProcess:
    def __init__(self, returncode=0):
        self.returncode = returncode

    async def communicate(self):
        return b"", b""


def test_execute_wlst_script_passes_credentials_via_subprocess_env(monkeypatch):
    captured = {}

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeProcess()

    monkeypatch.setattr(wlst_mcp.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    asyncio.run(wlst_mcp._execute_wlst_script(
        "print('hi')",
        timeout=5,
        admin_url="t3://localhost:7001",
        username="secret-user",
        password="super-secret-password",
    ))

    env = captured["env"]
    assert env[wlst_mcp._ENV_USERNAME] == "secret-user"
    assert env[wlst_mcp._ENV_PASSWORD] == "super-secret-password"
    assert env[wlst_mcp._ENV_ADMIN_URL] == "t3://localhost:7001"


def _capture_execute_args(monkeypatch, tool_fn, params):
    captured = {}

    async def fake_execute(script, timeout=None, admin_url="", username="", password=""):
        captured["script"] = script
        captured["admin_url"] = admin_url
        captured["username"] = username
        captured["password"] = password
        return {"success": False, "returncode": 1, "stdout": "", "stderr": "", "error": "mocked"}

    monkeypatch.setattr(wlst_mcp, "_execute_wlst_script", fake_execute)
    asyncio.run(tool_fn(params))
    return captured


TOOL_CASES = [
    (wlst_mcp.wlst_test_connection, wlst_mcp.ConnectionInput, {}),
    (wlst_mcp.wlst_list_servers, wlst_mcp.ListServersInput, {}),
    (wlst_mcp.wlst_start_server, wlst_mcp.ServerOperationInput, {"server_name": "server1"}),
    (wlst_mcp.wlst_deploy, wlst_mcp.DeployInput, {"app_name": "app1", "app_path": "/apps/app1.war"}),
    (wlst_mcp.wlst_list_datasources, wlst_mcp.DatasourceInput, {}),
    (wlst_mcp.wlst_diagnose_application, wlst_mcp.AppDiagnosticInput, {}),
]


@pytest.mark.parametrize("tool_fn, model_cls, extra_kwargs", TOOL_CASES, ids=[c[0].__name__ for c in TOOL_CASES])
def test_tool_never_embeds_password_in_generated_script(tool_fn, model_cls, extra_kwargs, monkeypatch):
    params = model_cls(admin_url="t3://localhost:7001", username="alice", password="super-secret-password", **extra_kwargs)

    captured = _capture_execute_args(monkeypatch, tool_fn, params)

    assert "super-secret-password" not in captured["script"]
    assert "alice" not in captured["script"]
    # ... but the real credentials must still reach _execute_wlst_script so WLST can use them.
    assert captured["password"] == "super-secret-password"
    assert captured["username"] == "alice"
    assert captured["admin_url"] == "t3://localhost:7001"
