# Changelog

All notable changes to `vaquill-mcp` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] - 2026-11-26
### Changed
- **India primary law has its own host: `https://in-mcp.vaquill.ai/mcp`.** It supports sign-in with a Vaquill account as well as a bearer API key, which the old path could not. 40 tools: Central and State Acts, Supreme Court and High Court judgments, and tribunal records.
- **The old India address is gone.** `https://mcp.vaquill.ai/in/mcp` and `https://mcp.vaquill.ai/in/s/{key}` stopped working on 2026-11-26. Remove a connector saved with either and add `https://in-mcp.vaquill.ai/mcp`.
- **Indian regulatory publications are only on `https://reg-mcp.vaquill.ai/mcp`.** The India primary law server no longer carries regulatory tools.
- **The MCP Registry listing names the three hosts**: `mcp.vaquill.ai` (US), `in-mcp.vaquill.ai` (India primary law) and `reg-mcp.vaquill.ai` (India regulatory).

### Added
- **Account tools on the regulatory server.** `get_india_credit_balance` and `get_pricing_in`, so you can check your balance and a workflow's price without switching connections. The regulatory server now has 7 tools.
- **`vaquill-mcp-origin`**, one entry point that serves any hosted origin from a registry, for self-hosting.

### Fixed
- **A hosted image refuses to start on an in-memory OAuth store.** The Docker image sets `VAQUILL_REQUIRE_DURABLE_OAUTH=1`, so a deployment with sign-in on but no Redis store fails at boot instead of signing every connected user out on its next restart.

## [0.6.0] - 2026-11-25
### Added
- **US case law tools.** The US server now publishes 16 case law tools (cases, opinions, citations, courts, judges, dockets, oral arguments).
- **New corpus types on `search_us_statutes`:** `JURY_INSTRUCTION`, `ETHICS_OPINION`, `STATE_EXECUTIVE_ORDER` and `MUNICIPAL_CODE`. They may return no results until loaded.
- **`fetch` returns Markdown** with tables and images where the publisher printed them, instead of a plain-text body with figure placeholders. Same price.

### Changed
- **Each jurisdiction sees only its own prices.** The US server reads US prices from `/api/v1/api-credits/pricing`, and the India server reads India prices from `/api/v1/in/api-credits/pricing`. No `region` parameter is sent any more. The India `vaquill://pricing` resource now shows India prices; before, it showed United States prices.
- **`act_id` is described per jurisdiction.** India tools now show an India act id example (`IND_central_20062`) instead of a United States Code section id.
- The US server describes itself as case law, statutes and regulations, and the generic `search` tool says it covers statutes and regulations, pointing court decisions to `search_us_cases`.
- The Docker image installs from `uv.lock`, so an unrelated edit can no longer upgrade production dependencies.

### Removed
- `get_us_docket_entries`, together with the API route it called. The US server now has 52 tools.

### Fixed
- OAuth sign-ins from Gemini Enterprise and Harvey are classified as their own client families.
- Prompt arguments read the same on every Python version. On Python 3.10 (and with older fastmcp releases) each text argument of a prompt carried a boilerplate "Provide a value matching the following JSON schema" note that newer installs did not show.

## [0.5.0] - 2026-11-10

### Added

- **A hosted server for the Indian regulatory corpus, on its own origin.**
  `vaquill-mcp-regulatory` (`vaquill_mcp.regulatory_main`) serves the five
  regulatory tools at the root of `https://reg-mcp.vaquill.ai`: `/mcp` for
  a bearer key or OAuth, `/s/{api_key}` for clients that cannot send a header.

  It is a subdomain and not a path on `mcp.vaquill.ai` because OAuth discovery is
  served from the origin root: an app under a prefix advertises the wrong
  `resource` URL, which is why India legislation is still bearer-only. It is a
  separate process from `remote_main` so the live US and India connectors, and
  the tool-count tests that protect them, are untouched.

- **One Dockerfile builds both hosted servers.** `--build-arg
  SERVER=vaquill-mcp-regulatory` selects the new one; the default is the
  previous hard-coded `vaquill-mcp-remote`, so an unset argument builds what it
  always did.

- **Price lines on the five regulatory tools,** injected at startup from the
  public pricing matrix (region `IN`) exactly as for the derived tools, never
  typed into a description. A tool with no price row is named in a startup
  warning and published without a line.

- `server.json` lists the regulatory remote, with the API-key header optional
  because OAuth is available at its origin root.

## [0.4.2] - 2026-11-06

### Changed

- **`search_us_statutes` names the new `US_TREATY` corpus type.** The backend
  moved the Senate treaty record (treaties, resolutions of advice and consent,
  Treaty Documents) out of `US_TAX_TREATY` into its own `US_TREATY` token.
  `US_TAX_TREATY` now holds only the IRS income tax treaties and their
  Treasury technical explanations, so a call that filtered on it for Senate
  material should send `US_TREATY`, or both as a list. The curated
  `corpusType` description lists both tokens and what each holds.

  The enum itself comes from the live OpenAPI at startup, so a running server
  accepts `US_TREATY` since the backend deploy of 2026-11-06. The test
  fixture `tests/fixtures/openapi_us.json` picks it up on its next refresh.

## [0.3.0] - 2026-08-20

### Fixed

- **Every tool was shipping with no credit-cost line.** Costs are injected at
  startup from the live pricing matrix so they can never drift, but the
  tool-to-price link was a hand-keyed dict. The 2026-08 `/us` country-prefix
  migration moved every statutes price from `/statutes/search` to
  `/us/statutes/search`, the dict kept the old spelling, and a miss is silent
  by design. Combined with the India-market exit retiring most of the tools the
  dict was built around, the result was a drift-proof mechanism that had
  quietly stopped running.

  The link is now **derived** from the OpenAPI path
  (`_pricing_endpoint_for_route`), the same normalization the web console uses,
  so a tool cannot drift away from its price by being renamed or re-prefixed.

- Removed six tools from the hosted remote server that pointed at routes which
  no longer exist: `ask_legal_question` (`/ask`, unmounted),
  `search_legislation`, `get_act_text`, `get_amendments`, `list_legislation`
  (`/acts/*`, retired with the India-market exit), and
  `search_cases_by_citation` (`/citations/cases/search`, no such route).

- Tool descriptions no longer claim India coverage. `/research/*` and
  `/citations/*` have been US-only for some time and reject `countryCode=IN`
  with a 400.

### Added

- Descriptions for the fifteen tools that had none and were therefore shipping
  the raw multi-paragraph OpenAPI prose as their tool description.

- Coverage of the law-change alerts surface (`list_boards`, `create_watch`,
  `list_watches`, `update_watch`, `delete_watch`, `test_watch`,
  `list_watch_changes`, `get_watch_change_diff`, `list_watch_deliveries`) and
  the section-context tools (`get_section_neighbors`, `get_section_definitions`,
  `get_section_cited_by`, `get_section_cross_state`, `get_section_changes`,
  `get_sections_batch`, `list_statutes_laws`).

- A free endpoint now renders `Free.` rather than `Cost: 0 credits.` Most of
  the alerts surface is free, and an agent choosing between tools is better
  served by the word than by parsing a zero.

- Drift guards that would have caught the above: every tool the production
  spec publishes must have a description, no description may name a tool that
  no longer exists, and every priced route must receive an injected cost line.
  The previous guard walked only the five semantic renames, so fifteen tools
  could regress without failing anything.

- This changelog. `pyproject.toml` has linked to it since 0.1.0.

## [0.2.0] - 2026-08-08

### Changed

- US-only scope; refreshed documentation.
- Tool names are auto-derived from the OpenAPI spec rather than hand-maintained.

## [0.1.2] - 2026-07-18

### Fixed

- Credit costs are injected from the live API instead of being written into
  tool descriptions, where they had drifted to roughly half the real price.

## [0.1.1] - 2026-03-09

### Added

- Initial public release.

[0.3.0]: https://github.com/Vaquill-AI/vaquill-mcp/releases/tag/v0.3.0
[0.2.0]: https://github.com/Vaquill-AI/vaquill-mcp/releases/tag/v0.2.0
[0.1.2]: https://github.com/Vaquill-AI/vaquill-mcp/releases/tag/v0.1.2
[0.1.1]: https://github.com/Vaquill-AI/vaquill-mcp/releases/tag/v0.1.1
