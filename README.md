# vaquill-mcp

MCP server for <a href="https://www.vaquill.ai" target="_blank">Vaquill</a> legal research API. Covers **US federal + 50-state law** (USC, CFR, state legislation, CourtListener case law) and **Indian law** (31M+ judgments, 23K+ acts). Ask AI-powered legal questions, search statutes and case law, resolve citations, and traverse citation networks — all from your AI tools.

## Quick Start

### Prerequisites

Sign up at <a href="https://www.vaquill.ai" target="_blank">vaquill.ai</a> to get your API key.

### Claude.ai (Web)

No installation needed. Add as a remote MCP server in Claude.ai Settings > Integrations:

**Option A — Simple URL (API key in path):**

```
https://mcp.vaquill.ai/s/vq_key_your_key_here
```

**Option B — Bearer token (recommended):**

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

### Cross-jurisdiction

| Tool | Description | Credits |
|------|-------------|---------|
| `ask_legal_question` | AI-powered legal Q&A. `countryCode='US'` (default) covers USC + CFR + 50-state law + CourtListener case law. `countryCode='IN'` covers Indian judgments and acts. Standard (fast) or deep (multi-hop) modes. | 5 - 20 |
| `get_pricing` | Get current API credit pricing (no auth required). | Free |

### US law (USC + CFR)

| Tool | Description | Credits |
|------|-------------|---------|
| `search_us_statutes` | Semantic search across the United States Code (USC) and Code of Federal Regulations (CFR). Filter by `corpusType` and `titleNumber`. | 2 |
| `get_us_statute_section` | Metadata for a specific USC/CFR section by `act_id` (citation, title hierarchy, links). | 1 |
| `get_us_statute_section_text` | Full HTML + plain text of a USC or CFR section. | 3 |

### Indian case law

| Tool | Description | Credits |
|------|-------------|---------|
| `search_legal_cases` | Boolean keyword search of Indian Supreme Court + High Court judgments. AND/OR/NOT operators, court/year filters. | 1 - 3 |
| `quick_search` | Compact top 3-5 Indian case results with essentials only. | 1 |
| `resolve_citation` | Resolve any Indian citation format (SCC, AIR, SCR, MANU, SCALE, INSC) to canonical case record. | 1 |
| `search_cases_by_citation` | Search Indian cases by citation text or case name with filters. | 1 |
| `lookup_case` | Full Indian case details with citation treatment stats (followed, overruled, etc.). | 1 |
| `get_citation_network` | Traverse the Indian citation graph: 1-3 hops, inbound/outbound/both. | 2 |

### Indian acts & legislation

| Tool | Description | Credits |
|------|-------------|---------|
| `search_legislation` | Semantic search across 23,000+ Indian acts, regulations, state legislation. Filter by category, state, department, year. | 1 |
| `list_legislation` | Browse Indian acts to discover `act_id` values. Filter by category, state, department, status. | 1 |
| `get_act_text` | URLs for full text, PDF, and HTML versions of an Indian act. | 1 |
| `get_amendments` | Complete amendment history for an Indian act (substitutions, insertions, omissions). | 1 |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VAQUILL_API_KEY` | Yes | - | API key (`vq_key_...`) from <a href="https://www.vaquill.ai" target="_blank">vaquill.ai</a> |
| `VAQUILL_BASE_URL` | No | `https://api.vaquill.ai` | API base URL |
| `VAQUILL_TIMEOUT` | No | `120` | Request timeout in seconds |

## Example Usage

Once configured, you can ask your AI assistant things like:

**US law:**
- "What does 17 CFR 240.10b-5 say about insider trading?"
- "Find USC sections on equal protection under the Fourteenth Amendment"
- "Summarize FRCP Rule 12(b)(6) and recent SDNY case law applying it" (uses deep mode)
- "What are the federal penalties for wire fraud under 18 USC 1343?"

**Indian law:**
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

This package is a thin MCP wrapper around the <a href="https://www.vaquill.ai/docs/api-reference/" target="_blank">Vaquill Developer API</a>. At startup, it fetches the OpenAPI spec from the live API and auto-generates MCP tools using <a href="https://github.com/jlowin/fastmcp" target="_blank">FastMCP</a>. Tool names and descriptions are customized for optimal LLM performance.

Because the spec is fetched at startup (not bundled), tools automatically reflect any API changes without a package update.

## Credits & Pricing

API calls consume credits. Check current pricing at <a href="https://www.vaquill.ai/#pricing" target="_blank">vaquill.ai</a> or use the `get_pricing` tool.

1 credit = $0.10 USD = 10 INR

## License

MIT
