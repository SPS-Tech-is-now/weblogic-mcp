# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An MCP (Model Context Protocol) server, implemented as a single Python file (`src/wlst_mcp.py`), that exposes Oracle WebLogic Server administration as MCP tools. It works by generating WLST (WebLogic Scripting Tool / Jython) script fragments at runtime, writing them to a temp file, and shelling out to the `wlst.sh`/`wlst.cmd` executable to run them.

## Setup and running

```bash
pip install -r requirements.txt
```

Run directly (used as the MCP server entry point, normally launched by an MCP client, not by hand):

```bash
python src/wlst_mcp.py
```

There is no build step or lint config. There is a small pytest suite under `tests/` (install with `pip install -r requirements-dev.txt`, run with `python -m pytest`) that tests script generation in isolation — it monkeypatches `_execute_wlst_script` to capture the generated WLST/Jython source instead of running it, then asserts on that source via the `ast` module (e.g. that user-supplied values can't break out of their string literal). It does not exercise WLST itself. For anything beyond script generation, validate changes by exercising the tool through an MCP client (e.g. Claude Code's `.mcp.json`/`~/.claude.json` config, see README.md) against a real or test WebLogic domain.

## Configuration (env vars)

- `WEBLOGIC_HOME` — WebLogic install dir; used to derive the WLST executable path (`$WEBLOGIC_HOME/oracle_common/common/bin/wlst.sh|.cmd`).
- `WLST_PATH` — overrides the WLST executable path directly (used only if `WEBLOGIC_HOME` is unset).
- `WLST_TIMEOUT` — default script timeout in seconds (default 120).
- `WLST_SHUTDOWN_TIMEOUT` — default timeout for stop/restart operations (default 300).
- `WLST_ADMIN_URL`, `WLST_USERNAME`, `WLST_PASSWORD` — default connection credentials, used when a tool call doesn't supply its own.
- `WLST_ALLOW_EXECUTE_SCRIPT` — must be set (`true`/`1`/`yes`) for `wlst_execute_script` to be registered as an MCP tool at all (checked once, at import time, via `_EXECUTE_SCRIPT_ENABLED`). That tool runs arbitrary caller-supplied Jython with no sandboxing, so it's opt-in; see `tests/test_execute_script_guardrails.py`.

## Architecture

Everything lives in `src/wlst_mcp.py`, organized top-to-bottom into four sections (see the `# ====` banner comments):

1. **Pydantic input models** — one `BaseModel` per tool (e.g. `ConnectionInput`, `DeployInput`, `ServerOperationInput`). All use `extra='forbid'` and `str_strip_whitespace=True`. Each model that needs connection info defines `get_admin_url()`/`get_username()`/`get_password()` helper methods that fall back to the `WLST_*` env-var defaults when the field is `None` — this fallback pattern is duplicated across every model rather than shared via inheritance, so when adding a new tool, copy the pattern rather than trying to refactor it away mid-change.

2. **Utility functions** (`_execute_wlst_script`, `_build_connect_script`, `_build_disconnect_script`, `_handle_error`, `_parse_json_output`, `_jython_str_literal`) — the core execution engine:
   - `_execute_wlst_script` writes the script string to a `NamedTemporaryFile`, runs it via `asyncio.create_subprocess_exec(wlst_path, script_path, ...)` with a timeout, captures stdout/stderr, and deletes the temp file.
   - `_build_connect_script`/`_build_disconnect_script` produce the `connect(...)`/`disconnect()` WLST fragments injected at the top/bottom of nearly every generated script, wrapped in try/except that prints a `CONNECTION_ERROR: ...` sentinel line on failure. `_build_connect_script()` takes no arguments — it emits a fixed `connect(os.environ['WLST_MCP_USERNAME'], ...)` snippet; the actual admin_url/username/password values are passed separately into `_execute_wlst_script(script, timeout, admin_url=..., username=..., password=...)`, which injects them into the WLST subprocess's environment (`_ENV_ADMIN_URL`/`_ENV_USERNAME`/`_ENV_PASSWORD`). This keeps credentials out of the script text entirely, so they're never written to the temporary script file on disk — see `tests/test_credential_handling.py`. When adding a tool that connects, call `_build_connect_script()` with no args and pass `admin_url=params.get_admin_url(), username=params.get_username(), password=params.get_password()` to its `_execute_wlst_script(...)` call.
   - `_handle_error` inspects stdout for that sentinel to produce a friendly error message.
   - `_jython_str_literal(value)` (`repr(str(value))`) renders a value as a safely-escaped Jython/Python string literal. Every free-text parameter embedded into a generated script (server/app names, targets, paths, credentials) must go through this — see "Adding a new tool" below.

3. **Tool implementations** — each `@mcp.tool(...)`-decorated async function follows the same shape:
   - Build a Jython/WLST script as an f-string. Any parameter value that becomes part of the script text must be passed through `_jython_str_literal(...)` first — e.g. `server_name = _jython_str_literal(params.server_name)` then `{server_name}` (no manual quotes) inside the f-string. If the value needs to sit inside a larger string (a path, a regex, a print message) rather than stand alone as a function argument, concatenate with `+` instead of interpolating it mid-literal (e.g. `cd('ServerLifeCycleRuntimes/' + {server_name})`, not `cd('ServerLifeCycleRuntimes/{params.server_name}')`) — interpolating raw text into the middle of an existing string literal is exactly how WLST/Jython script injection happens (see `tests/test_tool_injection.py`). Enum-constrained fields validated by a Pydantic `field_validator` against a fixed allowlist (`stage_mode`, `metric_type`, `log_type`, `response_format`) don't need this treatment. The one intentional exception is `wlst_execute_script`'s `script` field, which is deliberately raw user-supplied code, not data.
   - The script prints a unique sentinel line (e.g. `SERVERS_JSON:`, `DEPLOY_SUCCESS:`, `HEALTH_JSON:`) so the Python side can locate the relevant output by scanning `stdout.split('\n')` for that prefix, since `wlst.sh` also emits its own banner/noise.
   - Structured data crosses the WLST→Python boundary as a single-line `json.dumps(...)` printed after a sentinel prefix; the tool function then parses it back out and renders either Markdown (default, with emoji status indicators 🟢🟡🔴) or raw JSON depending on `response_format`.
   - Tool annotations (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`) are set per-tool and should accurately reflect the operation (e.g. `wlst_stop_server`/`wlst_undeploy`/`wlst_execute_script` are `destructiveHint: True`).

4. **Entry point** — `mcp.run()` under `if __name__ == "__main__"`, using FastMCP's default (stdio) transport.

### Adding a new tool

Follow the established pattern: define a Pydantic input model (with the `get_admin_url/username/password` trio if it needs a connection), escape every free-text field with `_jython_str_literal(...)` before it touches the script text, write a WLST script f-string with a unique sentinel + JSON output line, call `_execute_wlst_script`, parse the sentinel line back out, and produce both Markdown and JSON renderings gated on `params.response_format`. Use `_handle_error` for the connection-failure path and a tool-specific `*_ERROR:` sentinel for in-script exception handling, matching `wlst_deploy`/`wlst_start_server`/etc. Add a case to `tests/test_tool_injection.py`'s `TOOL_CASES` list for the new tool so the injection regression suite covers it.

### Notable runtime behaviors

- WLST scripts navigate the MBean tree via `cd()`/`ls(returnMap='true')`, switching between `serverConfig()` (static config) and `domainRuntime()`/`serverRuntime()` (live runtime state) as needed — config-only data (datasource URLs, JMS JNDI names) comes from `serverConfig()`, while live state (server `RUNNING`/`SHUTDOWN`, app `STATE_ACTIVE`/`STATE_FAILED`, JVM/thread metrics) comes from the runtime MBean trees.
- `wlst_analyze_logs` and `wlst_diagnose_application` don't just query MBeans — they also have WLST/Jython read raw log files (`$DOMAIN_HOME/servers/<name>/logs/*.log`, `nodemanager.log`) and regex-match against `restart_patterns`/`error_patterns`/`warning_patterns` lists to infer probable causes. `cmo.getRootDirectory()` returns the domain home as known to the *Admin Server*, but the `os.path.exists()`/`open()` calls run wherever the WLST subprocess actually executes (i.e. on this MCP server's host) — those are only the same filesystem if the MCP server happens to run on the Admin Server's host, or has the domain directory mounted at the same path. Both scripts compute `domain_home_accessible = os.path.isdir(domainHome)` up front and surface an explicit warning (instead of a silently "clean" report, or a false `SOURCE_FILE_MISSING`) when it's `False` — see `tests/test_remote_filesystem_awareness.py`.
- Credentials are passed per-call via the WLST subprocess environment, never stored and never written into the generated script text (see `_build_connect_script`/`_execute_wlst_script` above); the temp script file is deleted in a `finally` block immediately after execution.
