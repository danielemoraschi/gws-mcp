# gws-mcp

MCP server that exposes the [Google Workspace CLI](https://github.com/nicholasgasior/gws) (`gws`) to Claude Desktop — giving Claude full access to **19 Google Workspace services** via 5 generic tools.

## Why?

Claude's built-in Google connectors cover **3 services** (Gmail, Calendar, Drive) with limited write access. This MCP server wraps the full `gws` CLI, unlocking everything:

| Capability | Native Connectors | gws-mcp |
|---|---|---|
| Services | 3 | **19** (Gmail, Calendar, Drive, Sheets, Docs, Slides, Tasks, People, Chat, Classroom, Forms, Keep, Meet, Admin Reports, Events, Model Armor, Workflow) |
| Send emails | No (drafts only) | **Yes** |
| Edit Sheets cells | No | **Yes** |
| Edit Docs/Slides | No | **Yes** |
| Tasks, Chat, Forms, Classroom | No | **Yes** |
| Schema introspection | No | **Yes** |
| Dry-run validation | No | **Yes** |
| Plan tier required | Pro+ | **Any** (self-hosted) |

## Prerequisites

- **Python 3.10+**
- **`mcp` package**: `pip install "mcp>=1.0.0"`
- **`gws` CLI** installed and authenticated: `npm install -g @nicholasgasior/gws`
- **Node.js** (required by `gws`)

Authenticate `gws` before first use:
```bash
gws auth login
```

## Quickstart

1. **Clone this repo:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/gws-mcp.git
   ```

2. **Install the MCP dependency:**
   ```bash
   pip install "mcp>=1.0.0"
   ```

3. **Configure Claude Desktop** — add to `claude_desktop_config.json`:
   ```jsonc
   {
     "mcpServers": {
       "gws": {
         "command": "python3",
         "args": ["/path/to/gws-mcp/server.py"],
         "env": {
           "HOME": "/Users/YOUR_USERNAME"
         }
       }
     }
   }
   ```

4. **Restart Claude Desktop.** The `gws` tools should appear in the tools menu.

### nvm users

Claude Desktop doesn't inherit nvm's PATH. Set explicit paths via env vars:

```jsonc
{
  "mcpServers": {
    "gws": {
      "command": "python3",
      "args": ["/path/to/gws-mcp/server.py"],
      "env": {
        "HOME": "/Users/YOUR_USERNAME",
        "GWS_NODE_PATH": "/Users/YOUR_USERNAME/.nvm/versions/node/v22.0.0/bin/node",
        "GWS_BIN_PATH": "/Users/YOUR_USERNAME/.nvm/versions/node/v22.0.0/bin/gws"
      }
    }
  }
}
```

## Tools

The server exposes 5 tools. Claude is instructed to follow a discovery workflow:

| # | Tool | Purpose |
|---|------|---------|
| 1 | `gws_services` | List all 19 available services with descriptions |
| 2 | `gws_help` | Get help for any command or subcommand |
| 3 | `gws_schema` | Get full API schema for a method (parameters, types, required fields) |
| 4 | `gws_dry_run` | Validate a command without executing (shows resolved URL, HTTP method, body) |
| 5 | `gws_run` | Execute a command and return parsed JSON |

**Discovery workflow:** `gws_services` → `gws_help` → `gws_schema` → `gws_dry_run` → `gws_run`

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `GWS_NODE_PATH` | `which node` | Path to Node.js binary |
| `GWS_BIN_PATH` | `which gws` | Path to gws binary |

The server fails fast at startup with a clear error if either binary cannot be found.

## Security

- **No shell execution** — uses `asyncio.create_subprocess_exec` with args as a list
- **Service allowlist** — service names validated before execution
- **Blocked flags** — `--upload` and `--output` are rejected (no local file writes via MCP)
- **Timeout** — 60-second timeout per command
- **Output cap** — responses truncated at 100 KB
- **Local auth** — uses ambient OAuth tokens from `~/.config/gws/`, no credentials in config

## License

MIT
