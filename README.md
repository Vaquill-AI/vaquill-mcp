# vaquill-mcp

MCP server for [Vaquill](https://www.vaquill.ai) legal research API. Covers US federal and 50-state law (USC, CFR, state legislation, CourtListener case law). Ask AI-powered legal questions, search statutes, and ground answers in primary sources, all from your AI tools.

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

### General

| Tool | Description | Credits |
|------|-------------|---------|
| `ask_legal_question` | AI-powered legal Q&A across USC, CFR, 50-state law, and CourtListener case law. Standard (fast) or deep (multi-hop) modes. | 15 - 30 |
| `get_pricing` | Get current API credit pricing (no auth required). | Free |

### US law (USC + CFR)

| Tool | Description | Credits |
|------|-------------|---------|
| `search_us_statutes` | Semantic search across the United States Code (USC) and Code of Federal Regulations (CFR). Filter by `corpusType` and `titleNumber`. | 4 |
| `get_us_statute_section` | Metadata for a specific USC/CFR section by `act_id` (citation, title hierarchy, links). | 2 |
| `get_us_statute_section_text` | Full HTML + plain text of a USC or CFR section. | 6 |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VAQUILL_API_KEY` | Yes | - | API key (`vq_key_...`) from [vaquill.ai](https://www.vaquill.ai) |
| `VAQUILL_BASE_URL` | No | `https://api.vaquill.ai` | API base URL |
| `VAQUILL_TIMEOUT` | No | `120` | Request timeout in seconds |

## Example Usage

Once configured, you can ask your AI assistant things like:

- "What does 17 CFR 240.10b-5 say about insider trading?"
- "Find USC sections on equal protection under the Fourteenth Amendment"
- "Summarize FRCP Rule 12(b)(6) and recent SDNY case law applying it" (uses deep mode)
- "What are the federal penalties for wire fraud under 18 USC 1343?"

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

This package is a thin MCP wrapper around the [Vaquill Developer API](https://www.vaquill.ai/docs/api-reference/). At startup, it fetches the OpenAPI spec from the live API and auto-generates MCP tools using [FastMCP](https://github.com/jlowin/fastmcp). Tool names and descriptions are customized for optimal LLM performance.

Because the spec is fetched at startup (not bundled), tools automatically reflect any API changes without a package update.

## Credits & Pricing

API calls consume credits. The credit costs in the tables above are current at
the time of writing; the `get_pricing` tool and each tool's own description
always reflect the live price, so treat those as authoritative if they differ.

1 credit = $0.01 USD

## License

MIT
