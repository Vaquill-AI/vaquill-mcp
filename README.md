# vaquill-mcp

MCP server for [Vaquill](https://www.vaquill.ai) legal research API. Covers US federal and 50-state primary law: USC, CFR, state statutes and regulations, state and US constitutions, court rules, the Federal Register, executive orders, and agency guidance. Search primary law, resolve statutory citations, browse the hierarchy, and ground answers in official sources, all from your AI tools.

## Quick Start

### Prerequisites

Sign up at [vaquill.ai](https://www.vaquill.ai) to get your API key.

### Claude.ai (Web)

No installation needed. Add as a remote MCP server from **Customize > Connectors > Add custom connector**:

**Option A: Simple URL (API key in path)**

```
https://mcp.vaquill.ai/s/vq_key_your_key_here
```

**Option B: Bearer token (recommended)**

Open the **Request headers** section of the same dialog and add the credential there:

```
URL:            https://mcp.vaquill.ai/s/_
Header name:    Authorization
Header value:   Bearer vq_key_your_key_here
```

Claude sends the value exactly as you type it and adds no scheme of its own, so the value
field holds `Bearer vq_key_...` and **not** `Authorization: Bearer vq_key_...`.

Available on Claude Pro, Max, Team, and Enterprise plans. The Request headers section is in
beta and is enabled per account, it accepts a short allowlist of header names, and it holds at
most four. On Team and Enterprise an owner adds the connector under Organization settings first.

### Claude Desktop

Open the config file from **Settings > Developer > Edit Config**:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- Linux: `~/.config/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "vaquill": {
      "command": "uvx",
      "args": ["vaquill-mcp"],
      "env": {
        "VAQUILL_API_KEY": "vq_key_your_key_here"
      }
    }
  }
}
```

Quit Claude completely and reopen it. It does not reload the file.

### Claude Code

**Remote (no install):**

```bash
claude mcp add --transport http --scope user vaquill \
  https://mcp.vaquill.ai/s/_ \
  --header "Authorization: Bearer vq_key_your_key_here"
```

**Local (via uvx):**

```bash
claude mcp add --scope user vaquill -e VAQUILL_API_KEY=vq_key_your_key_here \
  -- uvx vaquill-mcp
```

`--scope user` registers the server for every project; the default is the current directory
only. `-e` stores the key with the registration, so it survives Claude Code being launched
from an IDE, which an `export` in your shell profile does not. Both `--header` and `-e` are
variadic, so they have to come after the server name.

### Cursor

Edit `~/.cursor/mcp.json` for every project, or `.cursor/mcp.json` for one.

**Remote:**

```json
{
  "mcpServers": {
    "vaquill": {
      "url": "https://mcp.vaquill.ai/s/_",
      "headers": {
        "Authorization": "Bearer vq_key_your_key_here"
      }
    }
  }
}
```

**Local (via uvx):**

```json
{
  "mcpServers": {
    "vaquill": {
      "command": "uvx",
      "args": ["vaquill-mcp"],
      "env": {
        "VAQUILL_API_KEY": "vq_key_your_key_here"
      }
    }
  }
}
```

### VS Code (Copilot)

Add to `.vscode/mcp.json`. For every project instead of one, run the
**MCP: Open User Configuration** command and use the same shape there.

**Remote:**

```json
{
  "inputs": [
    {
      "type": "promptString",
      "id": "vaquill-authorization",
      "description": "Authorization header value",
      "password": true
    }
  ],
  "servers": {
    "vaquill": {
      "type": "http",
      "url": "https://mcp.vaquill.ai/s/_",
      "headers": {
        "Authorization": "${input:vaquill-authorization}"
      }
    }
  }
}
```

The credential goes in a prompt rather than in the file, so the file is safe to commit. VS Code
asks for it on first start and remembers it. Restart the server after saving, or the tools will
not appear.

**Local (via uvx):**

```json
{
  "servers": {
    "vaquill": {
      "type": "stdio",
      "command": "uvx",
      "args": ["vaquill-mcp"],
      "env": {
        "VAQUILL_API_KEY": "vq_key_your_key_here"
      }
    }
  }
}
```

### Windsurf

Add to `~/.codeium/windsurf/mcp_config.json`.

**Remote:**

```json
{
  "mcpServers": {
    "vaquill": {
      "serverUrl": "https://mcp.vaquill.ai/s/_",
      "headers": {
        "Authorization": "Bearer vq_key_your_key_here"
      }
    }
  }
}
```

Windsurf uses `serverUrl` for a remote server, not `url`, and interpolates `${env:VAR}` if you
would rather read the credential from the environment.

**Local (via uvx):**

```json
{
  "mcpServers": {
    "vaquill": {
      "command": "uvx",
      "args": ["vaquill-mcp"],
      "env": {
        "VAQUILL_API_KEY": "vq_key_your_key_here"
      }
    }
  }
}
```

## Available Tools

Tools are generated from the live Vaquill API's OpenAPI spec at startup, so the
set always matches the current API. For the **authoritative, up-to-date list and
per-call credit costs**, run the free `get_pricing` tool or inspect your MCP
client's tool list. The main groups (representative tools shown):

### US statutes & regulations

USC, CFR, all 50 state codes, constitutions, state court rules, the Federal
Register, and agency guidance.

| Tool | Description |
|------|-------------|
| `search_us_statutes` | Hybrid semantic + keyword search; filter by `corpusType`, `state`, `titleNumber`, `chapter`, year, and more. |
| `get_us_statute_section` | Section metadata by `actId` (citation, hierarchy, official-source links). |
| `get_us_statute_section_text` | Full HTML + plain text of a section. |
| `get_sections_batch` | Metadata for up to 50 sections in one call. |
| `resolve_statute_citation` | Resolve a Bluebook citation (e.g. `42 U.S.C. § 1983`) straight to its section. |
| `list_statute_divisions` | Browse the statutory hierarchy one level at a time. |
| `list_statutes_coverage` | Self-describing coverage matrix: which corpora exist in which jurisdiction. |
| `list_statutes_laws` | Catalog the distinct bodies of law available, with their `corpusType` values. |

### Reading a section in context

| Tool | Description |
|------|-------------|
| `get_section_neighbors` | The sections immediately before and after, in statutory order. |
| `get_section_definitions` | The defined terms that govern a section, from its chapter's definitions section. |
| `get_section_cited_by` | Which USC/CFR sections cross-reference this one (the inverse of `crossReferences`). |
| `get_section_cross_state` | Provisions in other states addressing the same subject, ranked by similarity. |
| `get_section_changes` | What our refreshes observed changing on this section over time. |

### Law change alerts

Subscribe to a corpus source and get a webhook or email when it changes.
Subscribing, polling and inspecting deliveries are all free; only
`get_watch_change_diff` is metered, because it is the only one that returns
section text.

| Tool | Description |
|------|-------------|
| `list_boards` | The watchable sources (Federal Register, CFR, a state's statutes, ...). |
| `create_watch` | Subscribe to a board via webhook (HMAC-SHA256 signed) or email. |
| `list_watches`, `update_watch`, `delete_watch` | Manage your subscriptions. |
| `test_watch` | Send a synthetic delivery to verify signing, auth and reachability. |
| `list_watch_changes` | What changed on a watched source. Metadata only, and safe to poll. |
| `get_watch_change_diff` | Before/after text for one change, as whole documents. |
| `list_watch_deliveries` | Per-attempt webhook delivery log (90 days). |

### Utility

| Tool | Description |
|------|-------------|
| `get_pricing` | Live API credit pricing (free, no auth). |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VAQUILL_API_KEY` | Yes | - | API key (`vq_key_...`) from [vaquill.ai](https://www.vaquill.ai) |
| `VAQUILL_BASE_URL` | No | `https://api.vaquill.ai` | API base URL |
| `VAQUILL_TIMEOUT` | No | `120` | Request timeout in seconds |

## Example Usage

Once configured, you can ask your AI assistant things like:

- "What does 17 CFR 240.10b-5 say about insider trading?"
- "Resolve 42 U.S.C. § 1983 to its section and show the full text"
- "Find California statutes on tenant repair obligations"
- "Browse the Texas statutory codes, then drill into the Penal Code"

## Development

```bash
# Clone and install
git clone https://github.com/Vaquill-AI/vaquill-mcp.git
cd vaquill-mcp
uv sync --all-extras

# Run locally
VAQUILL_API_KEY=vq_key_... uv run vaquill-mcp

# Run tests
uv run pytest

# Test with FastMCP inspector
uv run fastmcp dev src/vaquill_mcp/server.py
```

## How It Works

This package is a thin MCP wrapper around the [Vaquill Developer API](https://www.vaquill.ai/docs/api-reference/). At startup, it fetches the OpenAPI spec from the live API and auto-generates MCP tools using [FastMCP](https://github.com/jlowin/fastmcp). Tool names are derived automatically from each endpoint's OpenAPI operation id, so new API endpoints show up as clean, ready-to-use tools with no package update; key descriptions are refined for optimal LLM performance.

Because the spec is fetched at startup (not bundled), tools automatically reflect any API changes without a package update.

## Credits & Pricing

API calls consume credits. The credit costs in the tables above are current at
the time of writing; the `get_pricing` tool and each tool's own description
always reflect the live price, so treat those as authoritative if they differ.

1 credit = $0.01 USD

## License

MIT
