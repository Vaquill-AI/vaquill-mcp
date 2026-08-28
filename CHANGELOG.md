# Changelog

All notable changes to `vaquill-mcp` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
