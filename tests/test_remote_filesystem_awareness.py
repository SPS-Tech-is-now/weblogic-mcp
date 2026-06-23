'''Regression tests for surfacing a clear warning when the domain home is not
locally accessible to the WLST process.

wlst_analyze_logs and wlst_diagnose_application read raw log/source files from
disk using paths derived from cmo.getRootDirectory(). That call returns the
domain home as known to the Admin Server, but the os.path.exists()/open()
calls run wherever the WLST subprocess actually executes -- which is only the
same machine if the MCP server happens to run on the Admin Server's host (or
has the domain directory mounted at the same path). When it isn't, every
os.path.exists() check silently returns False, producing a misleadingly
"clean" report instead of an explanation.

Since the affected logic runs inside the generated Jython script (not in this
Python process), these tests check two things:
1. The generated script actually computes and reports whether the domain home
   is accessible (a static check on the script text -- we can't execute real
   Jython here).
2. Given that signal comes back as False, the Markdown renderer (which *is*
   plain Python) surfaces a clear, prominent warning instead of silently
   reporting zero errors/issues found.
'''
import asyncio
import json

import wlst_mcp


def _make_execute_stub(stdout_extra_line):
    async def fake_execute(script, timeout=None, **credentials):
        return {
            "success": True,
            "returncode": 0,
            "stdout": stdout_extra_line,
            "stderr": "",
            "error": None,
        }
    return fake_execute


def test_analyze_logs_script_computes_domain_home_accessibility():
    params = wlst_mcp.ServerLogsInput(server_name="server1")
    captured = {}

    async def fake_execute(script, timeout=None, **credentials):
        captured["script"] = script
        return {"success": False, "returncode": 1, "stdout": "", "stderr": "", "error": "mocked"}

    import wlst_mcp as mod

    orig = mod._execute_wlst_script
    mod._execute_wlst_script = fake_execute
    try:
        asyncio.run(mod.wlst_analyze_logs(params))
    finally:
        mod._execute_wlst_script = orig

    assert "domain_home_accessible" in captured["script"]
    assert "os.path.isdir" in captured["script"]


def test_analyze_logs_warns_when_domain_home_not_accessible(monkeypatch):
    analysis = {
        "server_name": "server1",
        "days_analyzed": 1,
        "domain_home": "/u01/oracle/domains/mydomain",
        "domain_home_accessible": False,
        "server_info": {},
        "errors": [],
        "warnings": [],
        "restart_events": [],
        "nodemanager_events": [],
        "time_range": {"from": "2026-01-01", "to": "2026-01-02"},
        "summary": {
            "total_errors": 0,
            "total_warnings": 0,
            "total_restart_events": 0,
            "total_nm_events": 0,
            "has_oom_errors": False,
            "has_jvm_crashes": False,
            "probable_restart_reasons": ["No clear restart reason found in analyzed logs"],
        },
    }
    stdout = "LOGS_JSON:" + json.dumps(analysis)
    monkeypatch.setattr(wlst_mcp, "_execute_wlst_script", _make_execute_stub(stdout))

    params = wlst_mcp.ServerLogsInput(server_name="server1")
    result = asyncio.run(wlst_mcp.wlst_analyze_logs(params))

    assert "not accessible" in result.lower()
    assert "/u01/oracle/domains/mydomain" in result


def test_diagnose_application_script_computes_domain_home_accessibility():
    params = wlst_mcp.AppDiagnosticInput(app_name="myapp")
    captured = {}

    async def fake_execute(script, timeout=None, **credentials):
        captured["script"] = script
        return {"success": False, "returncode": 1, "stdout": "", "stderr": "", "error": "mocked"}

    orig = wlst_mcp._execute_wlst_script
    wlst_mcp._execute_wlst_script = fake_execute
    try:
        asyncio.run(wlst_mcp.wlst_diagnose_application(params))
    finally:
        wlst_mcp._execute_wlst_script = orig

    assert "domain_home_accessible" in captured["script"]
    assert "os.path.isdir" in captured["script"]


def test_diagnose_application_does_not_claim_source_missing_when_inaccessible(monkeypatch):
    diagnostics = {
        "domain_home": "/u01/oracle/domains/mydomain",
        "domain_home_accessible": False,
        "apps_analyzed": [
            {
                "name": "myapp",
                "issues": ["FILESYSTEM_NOT_ACCESSIBLE"],
                "probable_causes": ["Domain home not accessible from this host; cannot verify the source file."],
                "suggestions": ["Run the MCP server on the Admin Server host, or mount the domain directory."],
                "log_errors": [],
                "target_states": [{"target": "server1", "state": "STATE_FAILED"}],
                "intended_state": "STATE_ACTIVE",
                "config": {"sourcePath": "myapp.war"},
                "source_exists": None,
            }
        ],
        "summary": {"total_analyzed": 1, "total_failed": 1, "total_issues_found": 1},
    }
    stdout = "DIAG_JSON:" + json.dumps(diagnostics)
    monkeypatch.setattr(wlst_mcp, "_execute_wlst_script", _make_execute_stub(stdout))

    params = wlst_mcp.AppDiagnosticInput(app_name="myapp")
    result = asyncio.run(wlst_mcp.wlst_diagnose_application(params))

    assert "SOURCE_FILE_MISSING" not in result
    assert "not accessible" in result.lower()
