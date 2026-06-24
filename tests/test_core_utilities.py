'''Baseline unit coverage for previously-untested core logic: error
formatting, Pydantic input validation, and the sentinel-line + JSON
scanning every tool uses to pull structured data out of noisy WLST stdout.
'''
import json

import pytest
from pydantic import ValidationError

import wlst_mcp


# ---------------------------------------------------------------------------
# _handle_error
# ---------------------------------------------------------------------------

def test_handle_error_detects_connection_error_in_stdout():
    result = {
        "success": False,
        "stdout": "Initializing WebLogic Scripting Tool (WLST) ...\nCONNECTION_ERROR: weblogic.security.SecurityException: invalid credentials\n",
        "stderr": "",
        "error": None,
    }
    message = wlst_mcp._handle_error(result)
    assert "Connection failed" in message
    assert "invalid credentials" in message


def test_handle_error_falls_back_to_stderr_error_field():
    result = {"success": False, "stdout": "", "stderr": "boom", "error": "boom"}
    assert wlst_mcp._handle_error(result) == "Error: boom"


def test_handle_error_unknown_when_nothing_present():
    result = {"success": False, "stdout": "", "stderr": "", "error": None}
    assert wlst_mcp._handle_error(result) == "Error: Unknown error occurred during WLST execution"


# ---------------------------------------------------------------------------
# Pydantic validators
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scheme", ["t3", "t3s", "http", "https"])
def test_connection_input_accepts_valid_url_schemes(scheme):
    params = wlst_mcp.ConnectionInput(admin_url=f"{scheme}://localhost:7001")
    assert params.admin_url == f"{scheme}://localhost:7001"


@pytest.mark.parametrize("bad_url", ["ftp://localhost:7001", "localhost:7001", "ws://localhost:7001"])
def test_connection_input_rejects_invalid_url_schemes(bad_url):
    with pytest.raises(ValidationError):
        wlst_mcp.ConnectionInput(admin_url=bad_url)


@pytest.mark.parametrize("mode", ["stage", "nostage", "external_stage", "STAGE", "NoStage"])
def test_deploy_input_accepts_valid_stage_modes_case_insensitive(mode):
    params = wlst_mcp.DeployInput(app_name="a", app_path="/x.war", stage_mode=mode)
    assert params.stage_mode == mode.lower()


def test_deploy_input_rejects_invalid_stage_mode():
    with pytest.raises(ValidationError):
        wlst_mcp.DeployInput(app_name="a", app_path="/x.war", stage_mode="bogus")


@pytest.mark.parametrize("metric_type", ["all", "jvm", "threads", "jdbc", "jms"])
def test_server_metrics_input_accepts_valid_metric_types(metric_type):
    params = wlst_mcp.ServerMetricsInput(server_name="s1", metric_type=metric_type)
    assert params.metric_type == metric_type


def test_server_metrics_input_rejects_invalid_metric_type():
    with pytest.raises(ValidationError):
        wlst_mcp.ServerMetricsInput(server_name="s1", metric_type="bogus")


@pytest.mark.parametrize("log_type", ["all", "server", "nodemanager", "stdout"])
def test_server_logs_input_accepts_valid_log_types(log_type):
    params = wlst_mcp.ServerLogsInput(server_name="s1", log_type=log_type)
    assert params.log_type == log_type


def test_server_logs_input_rejects_invalid_log_type():
    with pytest.raises(ValidationError):
        wlst_mcp.ServerLogsInput(server_name="s1", log_type="bogus")


# ---------------------------------------------------------------------------
# Sentinel-line + JSON scanning (the pattern every list/health/metrics tool
# uses to pull its *_JSON: payload out of noisy WLST stdout)
# ---------------------------------------------------------------------------

import asyncio


def _wlst_banner(sentinel_line):
    return (
        "Initializing WebLogic Scripting Tool (WLST) ...\n"
        "\n"
        "Welcome to WebLogic Server Administration Scripting Shell\n"
        "\n"
        "Type help() for help on available commands\n"
        "\n"
        f"{sentinel_line}\n"
        "\n"
        "Disconnected from weblogic server: AdminServer\n"
        "\n"
        "Exiting WebLogic Scripting Tool.\n"
    )


def test_list_servers_extracts_json_despite_wlst_banner_noise(monkeypatch):
    payload = [{"name": "AdminServer", "state": "RUNNING"}, {"name": "ManagedServer1", "state": "SHUTDOWN"}]
    stdout = _wlst_banner("SERVERS_JSON:" + json.dumps(payload))

    async def fake_execute(*args, **kwargs):
        return {"success": True, "returncode": 0, "stdout": stdout, "stderr": "", "error": None}

    monkeypatch.setattr(wlst_mcp, "_execute_wlst_script", fake_execute)

    params = wlst_mcp.ListServersInput()
    result = asyncio.run(wlst_mcp.wlst_list_servers(params))

    assert "AdminServer" in result
    assert "🟢" in result  # RUNNING
    assert "🔴" in result  # SHUTDOWN


def test_list_servers_json_format_round_trips_payload(monkeypatch):
    payload = [{"name": "AdminServer", "state": "RUNNING"}]
    stdout = _wlst_banner("SERVERS_JSON:" + json.dumps(payload))

    async def fake_execute(*args, **kwargs):
        return {"success": True, "returncode": 0, "stdout": stdout, "stderr": "", "error": None}

    monkeypatch.setattr(wlst_mcp, "_execute_wlst_script", fake_execute)

    params = wlst_mcp.ListServersInput(response_format=wlst_mcp.ResponseFormat.JSON)
    result = asyncio.run(wlst_mcp.wlst_list_servers(params))

    parsed = json.loads(result)
    assert parsed["servers"] == payload
    assert parsed["total"] == 1
