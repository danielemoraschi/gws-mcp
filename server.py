"""MCP server exposing the gws (Google Workspace CLI) to Claude Desktop."""

import asyncio
import json
import os
import shutil
import sys

from mcp.server.fastmcp import FastMCP

TIMEOUT = 60
MAX_OUTPUT = 100_000  # 100 KB


def _resolve_binary(env_var: str, binary_name: str) -> str:
    """Resolve a binary path from env var or PATH lookup."""
    path = os.environ.get(env_var) or shutil.which(binary_name)
    if not path:
        print(
            f"ERROR: '{binary_name}' not found. "
            f"Set {env_var} or add '{binary_name}' to your PATH.",
            file=sys.stderr,
        )
        sys.exit(1)
    return path


NODE_BIN = _resolve_binary("GWS_NODE_PATH", "node")
GWS_BIN = _resolve_binary("GWS_BIN_PATH", "gws")

SERVICES = {
    "drive": "Manage files, folders, and shared drives",
    "sheets": "Read and write spreadsheets",
    "gmail": "Send, read, and manage email",
    "calendar": "Manage calendars and events",
    "admin-reports": "Audit logs and usage reports",
    "reports": "Alias for admin-reports",
    "docs": "Read and write Google Docs",
    "slides": "Read and write presentations",
    "tasks": "Manage task lists and tasks",
    "people": "Manage contacts and profiles",
    "chat": "Manage Chat spaces and messages",
    "classroom": "Manage classes, rosters, and coursework",
    "forms": "Read and write Google Forms",
    "keep": "Manage Google Keep notes",
    "meet": "Manage Google Meet conferences",
    "events": "Subscribe to Google Workspace events",
    "modelarmor": "Filter user-generated content for safety",
    "workflow": "Cross-service productivity workflows",
    "wf": "Alias for workflow",
}

BLOCKED_FLAGS = {"--upload", "--output"}

mcp = FastMCP(
    "gws",
    instructions=(
        "Google Workspace CLI server. Discovery workflow: "
        "gws_services → gws_help → gws_schema → gws_dry_run → gws_run. "
        "Always use 'fields' and 'pageSize' params to limit large responses. "
        "For Gmail, use userId='me'. For Drive list, use q param to filter."
    ),
)


async def _run_gws(args: list[str], timeout: int = TIMEOUT) -> dict | str:
    """Run the gws binary safely via asyncio subprocess (no shell)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            NODE_BIN, GWS_BIN, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"error": True, "message": f"Command timed out after {timeout}s"}

    out = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT]
    err = stderr.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        for text in (err, out):
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass
        return {"error": True, "exit_code": proc.returncode, "stderr": err or out}

    out = out.strip()
    if not out:
        return {"result": "OK (empty response)"}

    try:
        return json.loads(out)
    except json.JSONDecodeError:
        pass

    # NDJSON (one JSON object per line, from --page-all)
    lines = out.split("\n")
    results = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            return out  # Not JSON at all, return raw text
    return results if results else out


@mcp.tool()
async def gws_services() -> dict:
    """List all available Google Workspace services with descriptions.

    Use this first to discover what services are available.
    """
    return {"services": SERVICES}


@mcp.tool()
async def gws_help(command: list[str] | None = None) -> str:
    """Get help for any gws command or subcommand.

    Args:
        command: Command hierarchy, e.g. ["drive"], ["drive", "files"],
                 or ["drive", "files", "list"]. Empty for top-level help.

    Returns help text showing available subcommands, methods, and options.
    """
    args = list(command or []) + ["--help"]
    result = await _run_gws(args)
    return result if isinstance(result, str) else json.dumps(result, indent=2)


@mcp.tool()
async def gws_schema(method: str) -> dict | str:
    """Get the full API schema for a method including parameters, types, and required fields.

    Args:
        method: Dotted method path, e.g. "drive.files.list" or "gmail.users.messages.get"

    Use this to understand exactly what parameters a method accepts before calling it.
    """
    result = await _run_gws(["schema", method, "--resolve-refs"])
    return result


@mcp.tool()
async def gws_dry_run(
    service: str,
    args: list[str],
    params: dict | None = None,
    json_body: dict | None = None,
) -> dict | str:
    """Validate a gws command without executing it. Shows the resolved URL, HTTP method, and body.

    Args:
        service: Service name (e.g. "drive", "gmail", "sheets")
        args: Resource and method path, e.g. ["files", "list"] or ["users", "messages", "get"]
        params: URL/query parameters as a dict, e.g. {"fileId": "abc", "fields": "name,id"}
        json_body: Request body for POST/PATCH/PUT methods
    """
    if service not in SERVICES:
        return {"error": True, "message": f"Unknown service '{service}'. Use gws_services() to list valid services."}

    cmd = [service] + list(args) + ["--dry-run", "--format", "json"]

    if params:
        cmd += ["--params", json.dumps(params)]
    if json_body:
        cmd += ["--json", json.dumps(json_body)]

    for flag in BLOCKED_FLAGS:
        if flag in cmd:
            return {"error": True, "message": f"Flag '{flag}' is not allowed through MCP."}

    return await _run_gws(cmd)


@mcp.tool()
async def gws_run(
    service: str,
    args: list[str],
    params: dict | None = None,
    json_body: dict | None = None,
    page_all: bool = False,
    page_limit: int = 10,
) -> dict | list | str:
    """Execute a gws command and return the JSON result.

    Args:
        service: Service name (e.g. "drive", "gmail", "sheets")
        args: Resource and method path, e.g. ["files", "list"] or ["users", "messages", "get"]
        params: URL/query parameters as a dict, e.g. {"fileId": "abc", "fields": "name,id"}
        json_body: Request body for POST/PATCH/PUT methods
        page_all: Auto-paginate through all pages (returns NDJSON)
        page_limit: Maximum pages to fetch when page_all is True (default 10, max 50)
    """
    if service not in SERVICES:
        return {"error": True, "message": f"Unknown service '{service}'. Use gws_services() to list valid services."}

    cmd = [service] + list(args) + ["--format", "json"]

    if params:
        cmd += ["--params", json.dumps(params)]
    if json_body:
        cmd += ["--json", json.dumps(json_body)]
    if page_all:
        cmd += ["--page-all", "--page-limit", str(min(page_limit, 50))]

    for flag in BLOCKED_FLAGS:
        if flag in cmd:
            return {"error": True, "message": f"Flag '{flag}' is not allowed through MCP."}

    return await _run_gws(cmd)


if __name__ == "__main__":
    mcp.run(transport="stdio")
