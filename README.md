# vaquill-mcp

MCP server for [Vaquill](https://www.vaquill.ai) legal research API. Covers US federal and 50-state primary law: USC, CFR, state statutes and regulations, state and US constitutions, court rules, the Federal Register, executive orders, and agency guidance. Search primary law, resolve statutory citations, browse the hierarchy, and ground answers in official sources, all from your AI tools.

## Quick Start

### Prerequisites

Sign up at [vaquill.ai](https://www.vaquill.ai) to get your API key.

### Claude.ai (Web)

No installation needed. Add as a remote MCP server in Claude.ai Settings > Integrations:

**Option A: Simple URL (API key in path)**

```
https://mcp.vaquill.ai/s/vq_key_your_key_here
```

**Option B: Bearer token (recommended)**

```
URL:   https://mcp.vaquill.ai/s/_
Token: vq_key_your_key_here
```

Available on Claude Pro, Max, Team, and Enterprise plans.

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

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

### Claude Code

**Remote (no install):**

```bash
claude mcp add-json vaquill '{"type":"http","url":"https://mcp.vaquill.ai/s/_","headers":{"Authorization":"Bearer vq_key_your_key_here"}}'
```

**Local (via uvx):**

```bash
claude mcp add vaquill -- uvx vaquill-mcp
# Then set the env var in your shell: export VAQUILL_API_KEY=vq_key_...
```

Or add to `.claude/settings.json`:

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

### Cursor

Add to Cursor Settings > MCP Servers:

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

Add to `.vscode/settings.json`:

```json
{
  "mcp": {
    "servers": {
      "vaquill": {
        "command": "uvx",
        "args": ["vaquill-mcp"],
        "env": {
          "VAQUILL_API_KEY": "vq_key_your_key_here"
        }
      }
    }
  }
}
```

### Windsurf

Add to `~/.windsurf/settings.json`:

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
| `resolve_statute_citation` | Resolve a Bluebook citation (e.g. `42 U.S.C. § 1983`) straight to its section. |
| `list_statute_divisions` | Browse the statutory hierarchy one level at a time. |
| `list_statutes_coverage` | Self-describing coverage matrix: which corpora exist in which jurisdiction. |

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
