"""MCP resources: reference data and corpus knowledge, not another tool call.

WHY THIS FILE EXISTS
====================

MCP has three primitives -- tools, resources and prompts -- and until now this
server published 25 tools, 0 resources and 0 prompts. That ratio IS the
definition of a generated wrapper: `OpenAPIProvider` turns each endpoint into a
tool, mechanically, and nothing else was ever added. FastMCP's own author makes
the point bluntly ("Stop Converting Your REST APIs to MCP"), and the worked
example everyone cites goes the other way: 24 REST endpoints become 8 tools, 3
resources and 2 prompts.

Resources are the right home for two things a tool is a poor fit for.

**Reference data.** `coverage`, `pricing` and the India filter vocabulary are
not actions. They are stable, free, and the thing an agent should consult
BEFORE deciding what to do. As tools they cost a call and a turn; as resources
a client can pull them into context once and keep them.

**Knowledge the API cannot return.** `vaquill://guide` is not backed by an
endpoint at all. It is what we know about our own corpus that no response
field carries: how an act_id is shaped and why hand-building one 404s, why
`goodLawStatus: unknown` is not the same as "repealed", why an empty change
list is not "never amended". Every one of those has produced a confidently
wrong answer. Encoding them here is the difference between a server that
exposes an API and one that knows the domain it serves.
"""

from __future__ import annotations

from typing import Any

import httpx2
from fastmcp import FastMCP

# The reference endpoints, per jurisdiction. All three are priced FREE, which is
# why they are safe to expose as resources: a client that eagerly reads every
# resource cannot run up a bill.
_REFERENCE: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    "US": (
        (
            "vaquill://us/coverage",
            "/api/v1/us/statutes/coverage",
            "US corpus coverage",
            (
                "Every corpusType we hold and its per-jurisdiction section counts. "
                "Consult before answering a jurisdiction question, so a gap is "
                "reported as a gap rather than searched for and missed. Free."
            ),
        ),
        (
            "vaquill://pricing",
            "/api/v1/api-credits/pricing",
            "API credit pricing",
            (
                "Per-endpoint credit costs and the credit-to-currency rate "
                "(1 credit = $0.01 USD). Free, no authentication."
            ),
        ),
    ),
    "IN": (
        (
            "vaquill://in/filters",
            "/api/v1/in/acts/filters",
            "India filter vocabulary",
            (
                "Every category, state, department and status the Indian acts "
                "corpus actually holds, with counts. Read before filtering, so a "
                "query uses a value that exists instead of returning empty because "
                "the spelling was wrong. Free."
            ),
        ),
        (
            "vaquill://pricing",
            "/api/v1/api-credits/pricing",
            "API credit pricing",
            (
                "Per-endpoint credit costs and the credit-to-currency rate "
                "(1 credit = $0.01 USD). Free, no authentication."
            ),
        ),
    ),
}

# Corpus knowledge that no endpoint returns. Every entry below is a mistake we
# have actually seen produce a wrong answer, which is the bar for inclusion:
# this is not a tutorial, it is a list of traps.
_GUIDE_US = """\
# Using the Vaquill US primary-law corpus

## Identifiers
`act_id` (e.g. `USC_T42_C21_S1983`, `CFR_T21_P314_S314_50`,
`STATE_TX_Cpr_C93_S93.005`) is the key to every section tool.

**Do not build one from a citation.** The title and section are derivable; the
CHAPTER segment is not, because it exists only in the data. `Tex. Property Code
93.005` is `STATE_TX_Cpr_C93_S93.005`, not `STATE_TX_C93_S93.005`. Hand-assembled
ids usually 404. Start from `search_us_statutes` or `resolve_statute_citation`.

## "Is this still good law?"
Two fields, and they answer different questions.

- `actStatus` is what the PUBLISHER says (`in_force`, `repealed`, `renumbered`...).
- `goodLawStatus` is our verdict: `good_law` means checked, `unknown` means we
  hold no trustworthy repeal signal for that jurisdiction.

`excludeRepealed: true` removes what we KNOW is dead. It does not promise the
remainder is good law: a section survives it when its status is `in_force` OR
when we have no signal at all. Always read `goodLawStatus` on the survivors
before telling a user a provision is current.

## Change history is OBSERVATION, not the publisher's record
`get_section_changes` and the `changedSince` filter report when a refresh saw
the text differ from the copy we held. That is an upper bound on the effective
date, never the effective date itself. Capture began long after the corpus did,
it is per-source, and events sweep at 24 months.

**An empty change list means we recorded no change. It never means the section
was never amended.** For the publisher's own history, read `amendmentHistory`
on the section.

## Cost discipline
`get_us_statute_section` returns metadata WITHOUT the text; the body is a
separate, dearer call. Confirm you have the right section first. When you hold
several ids, `get_sections_batch` and `resolve_statute_citations_batch` take up
to 50 in one round trip (priced per item, so it is a latency win, not a
discount). Sections that are not found are skipped and not charged.

## Coverage
Read `vaquill://us/coverage` before answering a jurisdiction question. We do not
hold every body of law for every state, and a search against a corpus we do not
have looks identical to a genuine absence of law on the point.
"""

_GUIDE_IN = """\
# Using the Vaquill India legislation corpus

## Identifiers
`actId` (e.g. `IND_central_2065`) keys every acts tool, and comes from
`search_acts` or `list_acts`. Do not construct one.

## Repealed criminal codes: answer under what is in force
The 2023 codes replaced the colonial ones with effect from 1 July 2024:
IPC to BNS, CrPC to BNSS. Sources, pleadings and users routinely still cite the
old section numbers.

`get_corresponding_provisions` maps either direction (`ipc` and `bns` both
work). **Use it whenever an old section number appears**, and answer under the
provision actually in force rather than the repealed one. `iea`/`bsa` return 404
until that mapping lands.

## Amendment history is a record, not a guarantee
`get_act_amendments` returns what we recorded against an enactment
(e.g. "Subs. by Act 22 of 2023, s. 44 (w.e.f. 13-11-2025)"). An empty list means
no amendment was RECORDED, not that the Act was never amended.

## Filter vocabulary
Read `vaquill://in/filters` before filtering. Category, state and department
values are drawn from the data; a plausible-looking spelling that is not in the
vocabulary returns an empty result that reads like a corpus gap.

## Enactments are served by reference
`get_act_text` returns the publisher's plain-text, PDF and HTML links plus a
section count, not inline prose. To read provisions, use `search_acts`, whose
results carry the section text.
"""

_GUIDES = {"US": _GUIDE_US, "IN": _GUIDE_IN}


def register_resources(
    mcp: FastMCP, client: httpx2.AsyncClient, jurisdiction: str
) -> None:
    """Publish the reference resources and the corpus guide for one jurisdiction."""
    guide = _GUIDES.get(jurisdiction)
    if guide is not None:

        @mcp.resource(
            "vaquill://guide",
            name="Corpus guide",
            description=(
                "How to use this corpus correctly: identifier rules, what "
                "'still good law' actually means here, what an empty change "
                "list does and does not tell you, and where the cost is. Read "
                "this before a first substantive query."
            ),
            mime_type="text/markdown",
        )
        def corpus_guide() -> str:
            return guide

    for uri, path, name, description in _REFERENCE.get(jurisdiction, ()):

        def _make(path: str = path):
            async def read() -> Any:
                response = await client.get(path)
                response.raise_for_status()
                return response.json()

            return read

        mcp.resource(
            uri,
            name=name,
            description=description,
            mime_type="application/json",
        )(_make())


def guide_for(jurisdiction: str) -> str | None:
    """The static guide text, exposed for tests and for the prompts module."""
    return _GUIDES.get(jurisdiction)


def reference_uris(jurisdiction: str) -> tuple[str, ...]:
    """The reference resource URIs published for a jurisdiction."""
    return tuple(uri for uri, _, _, _ in _REFERENCE.get(jurisdiction, ()))
