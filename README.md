# vaquill-mcp

MCP server for [Vaquill](https://www.vaquill.ai) legal research API. Covers US federal and 50-state primary law: USC, CFR, state statutes and regulations, state and US constitutions, court rules, the Federal Register, executive orders, and agency guidance. Search primary law, resolve statutory citations, browse the hierarchy, and ground answers in official sources, all from your AI tools.

[![Discord](https://img.shields.io/badge/Discord-Join%20the%20community-5865F2?logo=discord&logoColor=white)](https://discord.gg/GQtnwxf8nQ)

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

For **Indian legislation**, add `--jurisdiction IN`. One process serves one
corpus, so run two entries if you want both:

```json
{
  "mcpServers": {
    "vaquill": {
      "command": "uvx",
      "args": ["vaquill-mcp"],
      "env": { "VAQUILL_API_KEY": "vq_key_your_key_here" }
    },
    "vaquill-india": {
      "command": "uvx",
      "args": ["vaquill-mcp", "--jurisdiction", "IN"],
      "env": { "VAQUILL_API_KEY": "vq_key_your_key_here" }
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
| `search` | Generic one-string corpus search returning `{id, title, url}`. |
| `fetch` | Generic one-string retrieval returning `{id, title, text, url, metadata}`. |

`search` and `fetch` exist because ChatGPT's deep research and company-knowledge
connectors match a corpus server by those exact names and their single-string
signature, and refuse to work without them. They are thin wrappers over the
typed tools above, which any client that can call them should prefer: the typed
ones filter by jurisdiction, corpus, date and status, and these two do not.
`fetch` is deliberately lenient about its `id`, accepting an act_id, a citation
URL, a bare path, or a Bluebook citation such as `42 U.S.C. 1983`.

### Resources and prompts

This server is not only tools. An MCP server generated from an OpenAPI document
publishes one tool per endpoint and nothing else; these two primitives are where
the knowledge that is not in the API lives, and neither costs anything in the
per-turn tool budget.

**Resources** (read, don't call):

| Resource | Contents |
|----------|----------|
| `vaquill://guide` | How to use the corpus correctly: identifier rules, what "still good law" actually means here, what an empty change list does and does not tell you, and where the cost is. Not backed by any endpoint. |
| `vaquill://us/coverage` | Coverage matrix: every corpusType and its per-jurisdiction counts. Free. |
| `vaquill://in/filters` | The filter vocabulary the India corpus actually holds. Free. |
| `vaquill://pricing` | Live credit pricing. Free. |

**Prompts** (workflows, with the traps built in):

| Prompt | What it encodes |
|--------|-----------------|
| `good_law_check(citation)` | `actStatus` vs `goodLawStatus` vs `amendmentHistory`, and why `unknown` means unchecked rather than current. |
| `fifty_state_survey(topic, states)` | Check coverage first, so "no such law" and "not in our corpus" are never conflated. |
| `whats_changed(since, corpus, state)` | Change capture is observation, not effect; an empty result is not "nothing was amended". |
| `cite_check(text)` | Batch-resolve rather than looping, then verify the passage's claims against each section. |
| `old_code_citation(citation)` | India: map IPC/CrPC to the BNS/BNSS provision in force since 1 July 2024 before answering. |
| `indian_provision_check(question)` | India: read the filter vocabulary first, and state the amendment caveat correctly. |

### Indian legislation

A **separate endpoint on the same host**. Central and State Acts plus the
instruments of the principal regulators (SEBI, RBI, MCA, IRDAI, TRAI, DGFT):
22,265 enactments and 1,098,577 individually addressable sections.

```
https://mcp.vaquill.ai/in/s/vq_key_your_key_here
```

Same API key, same credit balance as the US endpoint.

| Tool | Description |
|------|-------------|
| `search_acts` | Search Indian legislation down to the section. |
| `list_acts` | Browse and filter enactments by jurisdiction, regulator, year, status. |
| `list_act_filters` | The filter vocabulary the corpus actually holds, with counts. |
| `get_act_text` | Source links (text, PDF, HTML) for one enactment. |
| `get_act_amendments` | Amendment history: what changed, by which Act, effective when. |
| `search` / `fetch` | The same generic pair described above, over Indian legislation. |
| `get_corresponding_provisions` | Map a repealed criminal code to its 2023 replacement (IPC to BNS, CrPC to BNSS). |

**One endpoint serves one jurisdiction, deliberately.** The US and India
OpenAPI documents are disjoint, and each app derives its entire tool set from
one of them, so `/s/` cannot expose an Indian tool and `/in/s/` cannot expose a
US one. The mount path selects an app; it does not filter one, so there is no
per-request check to get wrong. That also keeps an integrator's context window
to the jurisdiction they actually work in.

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VAQUILL_API_KEY` | Yes | - | API key (`vq_key_...`) from [vaquill.ai](https://www.vaquill.ai) |
| `VAQUILL_BASE_URL` | No | `https://api.vaquill.ai` | API base URL |
| `VAQUILL_TIMEOUT` | No | `120` | Request timeout in seconds |
| `VAQUILL_JURISDICTION` | No | `US` | **stdio server only.** `US` or `IN`. Selects the OpenAPI document, and therefore the whole tool set. The hosted server ignores it and serves both jurisdictions on separate paths. |

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

### Tests

```bash
uv sync --extra dev --extra remote
uv run pytest                                    # 306 tests
uv run pytest -W error::DeprecationWarning       # what CI runs
```

CI (`.github/workflows/test.yml`) runs two jobs, and they are deliberately not
redundant. `test` runs the suite against the **locked** environment on Python
3.10-3.13. `wheel` builds the package, installs it into a fresh venv **without
the lockfile**, and runs the same suite there. The second reproduces what a
customer gets from `uvx vaquill-mcp`: a lockfile does not constrain anyone
installing the published wheel, so a dependency shipping a breaking major shows
up in that job and nowhere else.

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

## Community

Questions, ideas, or want to contribute? Join the Vaquill community on [Discord](https://discord.gg/GQtnwxf8nQ).
