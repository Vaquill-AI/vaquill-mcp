"""MCP prompts: the workflows, not the endpoints.

A tool says what CAN be called. A prompt says how a real question gets answered
with these tools, in what order, and which of the answers are traps. That is
knowledge this server holds and the API cannot express, and publishing it is
most of what separates a designed MCP server from a generated one.

Each prompt below exists because the naive tool sequence produces a confidently
wrong answer:

* `good_law_check` -- because `excludeRepealed` and `actStatus` look like they
  answer "is this still law" and do not. `goodLawStatus: unknown` means
  unchecked, not current, and the distinction decides whether an answer is
  safe to rely on.
* `whats_changed` -- because change capture is OBSERVATION. An empty result
  reads as "nothing was amended" and means "we recorded nothing".
* `fifty_state_survey` -- because a state we do not hold and a state with no
  such law return the same empty set unless coverage is checked first.
* `cite_check` -- because the batch route exists and looping the single one is
  the same price with fifty times the latency.
* `old_code_citation` (India) -- because IPC/CrPC section numbers are still
  everywhere, and answering under a repealed provision is the single most
  likely error on that corpus.

They are additive: prompts are listed through `prompts/list` and invoked
deliberately, so none of this rides in the per-turn tool budget.
"""

from __future__ import annotations

from fastmcp import FastMCP


def _register_us(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="good_law_check",
        description=(
            "Determine whether a US statute, regulation or rule is still in "
            "force, and say honestly how well that is established."
        ),
    )
    def good_law_check(citation: str) -> str:
        return f"""\
Determine whether **{citation}** is still good law, using the Vaquill US corpus.

1. `resolve_statute_citation` with the citation to get the `act_id`. Prefer this
   over searching for the citation text.
2. `get_us_statute_section` on that `act_id`. Read THREE fields, which answer
   different questions and are routinely conflated:
   - `actStatus`: what the publisher says (`in_force`, `repealed`, ...).
   - `goodLawStatus`: our verdict. `good_law` = checked. `unknown` = we hold no
     trustworthy repeal signal for this jurisdiction.
   - `amendmentHistory`: the publisher's own record of amendments.
3. `get_section_changes` on the same `act_id` for what our refreshes OBSERVED.

Then report, in this order:
- whether it is in force, and **on whose authority** (publisher status vs our check);
- if `goodLawStatus` is `unknown`, say plainly that we have not verified it
  rather than implying it is current;
- the most recent amendment, distinguishing the publisher's effective date from
  our observation date;
- if the change list is empty, say we recorded no change. Do NOT say it was
  never amended: capture began after the corpus did and sweeps at 24 months.

Do not assert a provision is current on the strength of `excludeRepealed` alone.
That filter removes what we know is dead; it does not verify the remainder."""

    @mcp.prompt(
        name="fifty_state_survey",
        description=(
            "Compare how multiple US states treat one issue, without mistaking "
            "a coverage gap for an absence of law."
        ),
    )
    def fifty_state_survey(topic: str, states: str = "") -> str:
        scope = f"the states: {states}" if states else "all states we cover"
        return f"""\
Survey how **{topic}** is treated across {scope}.

1. Read the `vaquill://us/coverage` resource FIRST, or call
   `list_statutes_coverage`. Establish which jurisdictions we actually hold for
   the relevant `corpusType` before searching.
2. `search_us_statutes` with `corpusType: "STATE"` and a `state` LIST, which is
   one call rather than one per jurisdiction. Add `excludeRepealed: true`.
3. For a representative hit, `get_section_cross_state` to find the sibling
   provisions in other states, ranked by similarity.
4. `get_sections_batch` (up to 50 ids per call) for the details you need.

When reporting:
- group states by rule, and name the outliers;
- **separate "no such provision" from "not in our corpus"**. They return the
  same empty result and mean opposite things. Any state absent from step 1 is
  the second case and must be labelled as unknown, not as permissive;
- give the citation and `act_id` for every state you assert a rule for."""

    @mcp.prompt(
        name="whats_changed",
        description=(
            "Find what changed in a body of US law since a date, with the "
            "observation-vs-effect caveat stated correctly."
        ),
    )
    def whats_changed(since: str, corpus: str = "", state: str = "") -> str:
        narrowing = ", ".join(
            part
            for part in (
                f'corpusType: "{corpus}"' if corpus else "",
                f'state: "{state}"' if state else "",
            )
            if part
        )
        hint = (
            f" Narrow with {narrowing}."
            if narrowing
            else (
                " Narrow with `corpusType` and `state`: an unnarrowed window can match "
                "too many sections and is rejected with a 422."
            )
        )
        return f"""\
Report what changed since **{since}**.

1. `search_us_statutes` with `changedSince: "{since}"` (format `YYYY-MM-DD`).{hint}
2. For each hit worth reporting, `get_section_changes` on its `act_id` for the
   per-section history, newest first.
3. Where a diff matters and you have a board watch, `get_watch_change_diff`
   returns the before/after text.

State the caveat explicitly in your answer, because the obvious reading is wrong:
these are dates we OBSERVED a difference between the publisher and the copy we
held. They are an upper bound on when the change took effect, not the effective
date. Coverage is bounded by when capture began for that source, not by the age
of the law, and events are swept at 24 months.

**An empty result means no captured change in the window. It does not mean
nothing was amended.**"""

    @mcp.prompt(
        name="cite_check",
        description=(
            "Resolve and verify every US citation in a passage, in one batch "
            "rather than one call each."
        ),
    )
    def cite_check(text: str) -> str:
        return f"""\
Verify every US legal citation in the passage below.

1. Extract the citations verbatim.
2. `resolve_statute_citations_batch` with up to 50 at once. It costs the same
   per citation as resolving them one at a time and takes one round trip instead
   of fifty. Duplicates collapse and order is preserved.
3. For any that fail to resolve, retry the single `resolve_statute_citation`
   with `state` or `corpusType` set. Some forms are genuinely ambiguous:
   `8 CCR 1206-2` is Colorado and `22 CCR 76227` is California.
4. `get_sections_batch` on the resolved `act_id`s to confirm each section says
   what the passage claims.

Report per citation: resolved or not, the official source link, and whether the
passage characterises it correctly. Flag any that resolved to a section whose
`goodLawStatus` is not `good_law`, since a correctly-formatted citation to a
repealed provision is the failure this check exists to catch.

---
{text}"""


def _register_in(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="old_code_citation",
        description=(
            "Answer an IPC/CrPC citation under the 2023 code that actually replaced it."
        ),
    )
    def old_code_citation(citation: str) -> str:
        return f"""\
A source cites **{citation}**, which may be from a repealed colonial code.

The 2023 codes replaced them with effect from 1 July 2024: IPC to BNS, CrPC to
BNSS. Old section numbers remain everywhere in judgments, pleadings and
commentary, so this is the most likely way to answer this corpus wrongly.

1. `get_corresponding_provisions` with the section. Either side works: pass
   `ipc` or `bns`. (`iea`/`bsa` return 404 until that mapping lands.)
2. `search_acts` for the CURRENT provision's text.
3. `get_act_amendments` on the enactment to check it still reads as enacted.

Answer under the provision **actually in force**, and give both numbers so the
reader can follow the source: "section X of the BNS (formerly section Y IPC)".
If no mapping exists, say so rather than assuming the numbering carried over."""

    @mcp.prompt(
        name="indian_provision_check",
        description=(
            "Find and verify what Indian law says on a point, with the "
            "amendment caveat stated correctly."
        ),
    )
    def indian_provision_check(question: str) -> str:
        return f"""\
Answer this question against the Vaquill India corpus: **{question}**

1. Read `vaquill://in/filters` (or call `list_act_filters`) before using any
   category, state or department filter. A plausible value that is not in the
   vocabulary returns empty, which reads like a corpus gap.
2. `search_acts` for the provision. Use `phrase` matching for a defined term.
3. `get_act_amendments` on the enactment behind your best hit, to check the
   provision still reads as enacted.
4. `get_act_text` for the publisher's own PDF/HTML links to cite.

If the question touches criminal law and any source cites IPC or CrPC section
numbers, run `get_corresponding_provisions` first and answer under the BNS/BNSS
provision in force since 1 July 2024.

State that an empty amendment list means none was RECORDED, not that the Act was
never amended. Cite the section and the publisher's `sourceUrl`."""


_REGISTRARS = {"US": _register_us, "IN": _register_in}


def register_prompts(mcp: FastMCP, jurisdiction: str) -> None:
    """Publish the workflow prompts for one jurisdiction."""
    registrar = _REGISTRARS.get(jurisdiction)
    if registrar is not None:
        registrar(mcp)
