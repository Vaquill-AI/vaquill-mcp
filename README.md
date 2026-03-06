# vaquill-mcp

MCP server for <a href="https://www.vaquill.ai" target="_blank">Vaquill</a> legal research API. Search 20M+ Indian court judgments, ask AI-powered legal questions, resolve citations, and traverse citation networks — all from your AI tools.

## Quick Start

### Prerequisites

Sign up at <a href="https://www.vaquill.ai" target="_blank">vaquill.ai</a> to get your API key.

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

| Tool | Description | Credits |
|------|-------------|---------|
| `ask_legal_question` | AI-powered legal Q&A grounded in court judgments. Standard (fast) or deep (thorough) modes. | 0.5 - 2.0 |
| `search_legal_cases` | Boolean keyword search with AND/OR/NOT operators. Filter by court, year, country. | 1.0 |
| `quick_search` | Fast compact search returning top 3-5 results with essentials only. | 0.1 |
| `resolve_citation` | Resolve any citation format (SCC, AIR, SCR, MANU) to canonical case record. | 0.1 |
| `search_cases_by_citation` | Search cases by citation text or case name with filters. | 0.1 |
| `lookup_case` | Full case details with citation treatment stats (followed, overruled, etc.). | 0.1 |
| `get_citation_network` | Traverse citation graph: which cases cite/are cited by a case. 1-3 hops. | 0.2 |
| `get_pricing` | Get current API credit pricing (no auth required). | Free |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VAQUILL_API_KEY` | Yes | - | API key (`vq_key_...`) from <a href="https://www.vaquill.ai" target="_blank">vaquill.ai</a> |
| `VAQUILL_BASE_URL` | No | `https://api.vaquill.ai` | API base URL |
| `VAQUILL_TIMEOUT` | No | `120` | Request timeout in seconds |

## Example Usage

Once configured, you can ask your AI assistant things like:

- "Search for Supreme Court cases on Section 302 IPC"
- "What is the legal test for negligence in Indian tort law?"
- "Resolve the citation AIR 1978 SC 597"
- "Look up the case Maneka Gandhi vs Union of India and show treatment stats"
- "Show the citation network around ADM Jabalpur vs Shivkant Shukla"
- "Compare murder and culpable homicide under IPC" (uses deep mode)

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

This package is a thin MCP wrapper around the <a href="https://www.vaquill.ai/legal-api" target="_blank">Vaquill Developer API</a>. At startup, it fetches the OpenAPI spec from the live API and auto-generates MCP tools using <a href="https://github.com/jlowin/fastmcp" target="_blank">FastMCP</a>. Tool names and descriptions are customized for optimal LLM performance.

Because the spec is fetched at startup (not bundled), tools automatically reflect any API changes without a package update.

## Credits & Pricing

API calls consume credits. Check current pricing at <a href="https://www.vaquill.ai/#pricing" target="_blank">vaquill.ai</a> or use the `get_pricing` tool.

1 credit = $0.10 USD = 10 INR

## License

MIT
