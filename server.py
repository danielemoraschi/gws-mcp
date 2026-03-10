"""MCP server exposing the gws (Google Workspace CLI) to any MCP client."""

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


def _parse_output(text: str) -> dict | list | str:
    """Parse gws output as JSON, NDJSON, or raw text."""
    text = text.strip()
    if not text:
        return {"result": "Command completed with no output."}

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # NDJSON (one JSON object per line, from --page-all)
    results = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            results.append(json.loads(line))
        except json.JSONDecodeError:
            if results:
                results.append({"_warning": "Remaining output was not valid JSON", "raw": line})
                return results
            return text
    return results if results else text


def _make_error(message: str, **extra) -> dict:
    """Build a standardised error response."""
    return {"error": True, "message": message, **extra}


async def _run_gws(args: list[str], timeout: int = TIMEOUT) -> dict | list | str:
    """Run the gws binary safely via asyncio subprocess (no shell)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            NODE_BIN, GWS_BIN, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return _make_error(f"Failed to start gws: {exc}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return _make_error(f"Command timed out after {timeout}s")

    out = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT]
    err = stderr.decode("utf-8", errors="replace").strip()

    if proc.returncode != 0:
        for text in (err, out):
            if text:
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        parsed.setdefault("error", True)
                        parsed.setdefault("exit_code", proc.returncode)
                        return parsed
                    return _make_error(
                        "Command failed", exit_code=proc.returncode, detail=parsed,
                    )
                except json.JSONDecodeError:
                    pass
        return _make_error(err or out, exit_code=proc.returncode)

    return _parse_output(out)


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
    return await _run_gws(["schema", method, "--resolve-refs"])


def _build_service_command(
    service: str,
    args: list[str],
    params: dict | None,
    json_body: dict | None,
    extra_flags: list[str] | None = None,
) -> list[str] | dict:
    """Build a validated gws command list, or return an error dict."""
    if service not in SERVICES:
        return _make_error(f"Unknown service '{service}'. Use gws_services() to list valid services.")

    # Check user-controlled flags before appending serialized JSON values
    user_flags = [*args, *(extra_flags or [])]
    for flag in BLOCKED_FLAGS:
        if flag in user_flags:
            return _make_error(f"Flag '{flag}' is not allowed through MCP.")

    cmd = [service, *args, "--format", "json"]
    if extra_flags:
        cmd += extra_flags
    if params:
        cmd += ["--params", json.dumps(params)]
    if json_body:
        cmd += ["--json", json.dumps(json_body)]

    return cmd


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
    cmd = _build_service_command(service, args, params, json_body, ["--dry-run"])
    if isinstance(cmd, dict):
        return cmd
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
    extra = ["--page-all", "--page-limit", str(min(page_limit, 50))] if page_all else None
    cmd = _build_service_command(service, args, params, json_body, extra)
    if isinstance(cmd, dict):
        return cmd
    return await _run_gws(cmd)


if __name__ == "__main__":
    mcp.run(transport="stdio")
