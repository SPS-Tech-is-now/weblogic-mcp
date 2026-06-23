'''Tests for wlst_create_datasource, which wires up the previously-orphaned
CreateDatasourceInput model (it existed with no tool using it).
'''
import asyncio

import wlst_mcp


def _capture(monkeypatch, params, result=None):
    captured = {}

    async def fake_execute(script, timeout=None, **kwargs):
        captured["script"] = script
        captured["kwargs"] = kwargs
        return result or {"success": True, "returncode": 0, "stdout": "CREATE_DS_SUCCESS: myDS", "stderr": "", "error": None}

    monkeypatch.setattr(wlst_mcp, "_execute_wlst_script", fake_execute)
    response = asyncio.run(wlst_mcp.wlst_create_datasource(params))
    return response, captured


def _params(**overrides):
    defaults = dict(
        ds_name="myDS",
        jndi_name="jdbc/myDS",
        db_url="jdbc:oracle:thin:@localhost:1521/orcl",
        db_driver="oracle.jdbc.OracleDriver",
        db_user="dbuser",
        db_password="super-secret-db-password",
        targets="server1,server2",
    )
    defaults.update(overrides)
    return wlst_mcp.CreateDatasourceInput(**defaults)


def test_create_datasource_script_contains_expected_wlst_commands(monkeypatch):
    _, captured = _capture(monkeypatch, _params())
    script = captured["script"]
    assert "createJDBCSystemResource" in script
    assert "activate()" in script
    assert "setUrl" in script
    assert "setDriverName" in script
    assert "JNDINames" in script


def test_create_datasource_db_password_not_embedded_but_passed_through(monkeypatch):
    _, captured = _capture(monkeypatch, _params(db_password="super-secret-db-password"))
    assert "super-secret-db-password" not in captured["script"]
    assert captured["kwargs"]["extra_env"][wlst_mcp._ENV_DB_PASSWORD] == "super-secret-db-password"


def test_create_datasource_reports_success(monkeypatch):
    response, _ = _capture(monkeypatch, _params(), result={
        "success": True, "returncode": 0, "stdout": "CREATE_DS_SUCCESS: myDS", "stderr": "", "error": None,
    })
    assert "myDS" in response
    assert "success" in response.lower()


def test_create_datasource_reports_error(monkeypatch):
    response, _ = _capture(monkeypatch, _params(), result={
        "success": True, "returncode": 0, "stdout": "CREATE_DS_ERROR: already exists", "stderr": "", "error": None,
    })
    assert "already exists" in response
