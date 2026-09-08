"""LLM-optimized tool descriptions for Vaquill MCP tools.

Each description tells the LLM WHEN to use the tool and WHAT it returns.
Kept concise (under 500 characters) for efficient context usage.

These override the verbose OpenAPI descriptions, which are multi-paragraph
markdown with tables, paging contracts and worked examples -- far too long for
a tool description.

Credit costs are intentionally NOT written here. They are injected at server
startup from the live API (`GET /api/v1/api-credits/pricing/all`) by
``server.py`` so the numbers can never drift from ``CREDIT_PRICING`` in the
backend. See ``server.py`` (``_pricing_endpoint_for_route`` + ``_format_cost``).

SCOPE: US only. The India corpus (`/ask`, `/acts/*`) was retired with the
India-market exit, and `/research/*` + `/citations/*` now serve US case law
only and reject `countryCode=IN` with a 400. Descriptions that still said
"Indian" were rewritten on 2026-08-20; do not reintroduce them.
"""

TOOL_DESCRIPTIONS: dict[str, str] = {
    # ------------------------------------------------------------------
    # US statutes, regulations, constitutions and court rules
    # ------------------------------------------------------------------
    "search_us_statutes": (
        "Semantic + keyword search across US primary law: the United States Code (USC), the "
        "Code of Federal Regulations (CFR), and all 50 states' statutes, regulations, "
        "constitutions and court rules. Use for any 'what does the law say' question. Filter "
        "by corpusType and titleNumber. Returns sections with citation, hierarchy and official "
        "source links. The returned act_id (e.g. 'USC_T42_C21_S1983') feeds every other "
        "statute tool -- do not hand-build one, they usually 404."
    ),
    "get_us_statute_section": (
        "Metadata for one US statute, regulation or rule section by act_id: citation, title "
        "hierarchy, breadcrumb, amendment history, and links to HTML, PDF and XML. Does NOT "
        "include the section text -- use get_us_statute_section_text for that. Good for "
        "confirming you have the right section before paying for its full body."
    ),
    "get_us_statute_section_text": (
        "The full text of a US statute, regulation or rule section by act_id. Returns styled "
        "HTML (with cross-references and paragraph numbering as officially published) and "
        "plain text. Use when you need the actual statutory language to quote, draft against, "
        "or analyze rather than just cite."
    ),
    "get_sections_batch": (
        "Metadata for up to 50 sections in one call, by a list of act_ids. Same fields as "
        "get_us_statute_section. Use instead of looping that tool when you already hold "
        "several act_ids, for example every result of one search. Sections that are not found "
        "are skipped and not charged."
    ),
    "get_section_neighbors": (
        "The sections immediately before and after a given section within its own chapter or "
        "code, in statutory order. Use to read a provision in context, to find a definitions "
        "or penalties sibling, or to check whether the operative language continues into the "
        "next section."
    ),
    "get_section_cited_by": (
        "The USC and CFR sections whose text cross-references a given section: the inverse of "
        "the crossReferences already returned on a section lookup. Use to find where a "
        "definition or requirement is actually invoked, or to gauge how load-bearing a "
        "provision is across the code."
    ),
    "get_section_definitions": (
        "The term definitions that govern a section, parsed from its chapter's definitions "
        "section. Use whenever a provision turns on a term of art ('covered entity', "
        "'security', 'employer') and you need the statute's own definition rather than the "
        "ordinary meaning."
    ),
    "get_section_cross_state": (
        "Provisions in OTHER states that address the same subject as a given state statute "
        "section, ranked by similarity. State statutes only. Use for fifty-state surveys, "
        "multi-jurisdiction compliance, or to check whether a client's home-state rule is "
        "typical or an outlier."
    ),
    "get_section_changes": (
        "What our refreshes have observed changing on one section over time: when it was "
        "added, amended or removed, newest first. This is our capture history, not the "
        "publisher's -- an empty list means we recorded no change, never that the section was "
        "never amended. For the publisher's own history, read amendmentHistory on the section."
    ),
    "resolve_statute_citation": (
        "Resolve a Bluebook citation string ('42 U.S.C. 1983', '16 C.F.R. 444.1', "
        "'Cal. Code Regs. tit. 22, 76227') to the exact section, confirmed, with an official "
        "source link and the act_id. Use this whenever the user gives you a citation rather "
        "than a question -- it is far more reliable than searching for the citation text."
    ),
    "list_statute_divisions": (
        "Walk the statutory hierarchy: list the child divisions (titles, chapters, parts, or "
        "sections) under any level, in statutory order. Use to browse a code structurally when "
        "you do not yet know the section number, or to enumerate everything under a chapter."
    ),
    "list_statutes_coverage": (
        "Self-describing coverage matrix: every corpusType we hold and its per-jurisdiction "
        "section counts. Use before answering a jurisdiction question to check whether we "
        "actually cover that state and that body of law, so you can say so instead of "
        "searching a corpus that does not exist."
    ),
    # ------------------------------------------------------------------
    # Law-change alerts (boards and watches)
    # ------------------------------------------------------------------
    "list_boards": (
        "List every watchable board: a tracked corpus source such as the Federal Register, the "
        "CFR, or one state's statutes, with its refresh cadence. A board is identified by "
        "corpusType plus state (state is null for federal). Use to discover what can be "
        "subscribed to before calling create_watch."
    ),
    "create_watch": (
        "Subscribe to a board so a change to that source notifies you. Delivery is by webhook "
        "(HMAC-SHA256 signed) or email, fired when the source's existing refresh finds real "
        "changes -- nothing is crawled on your behalf and there is no real-time trigger. "
        "channel is immutable once set; to change it, delete and recreate."
    ),
    "list_watches": (
        "List the board watches you own, with their board, channel, destination, active state "
        "and the outcome of the last delivery attempt. Use to find a watchId for the other "
        "watch tools."
    ),
    "update_watch": (
        "Change a watch's destination, signing secret, outbound auth, or active state. Set "
        "isActive false to pause notifications while keeping the watch's config and history. "
        "The channel itself cannot be changed."
    ),
    "delete_watch": (
        "Delete a watch you own. Immediate and permanent, and its delivery history goes with "
        "it. To stop notifications without losing the config or history, prefer update_watch "
        "with isActive false."
    ),
    "test_watch": (
        "Send a synthetic notification to a watch's destination to verify signing, outbound "
        "auth and reachability before relying on it. Test deliveries are deliberately not "
        "persisted, so they never appear in list_watch_deliveries and never move the watch's "
        "last-notified timestamp. Rate limited by a short cooldown."
    ),
    "list_watch_changes": (
        "What a watched source added, amended or removed: section identifier, citation, title "
        "and detection time, newest first. Metadata only, never section text. Covers the "
        "board's whole captured history, not just since you subscribed. Safe to poll: it "
        "writes nothing and cannot suppress or double-fire a delivery. Page with sinceId and "
        "the returned cursor."
    ),
    "get_watch_change_diff": (
        "The full text of a changed section BEFORE and AFTER one specific change, as whole "
        "documents ready to diff. Only meaningful where hasDiff is true on the change list. A "
        "missing side is not an error: hasBefore and hasAfter say which text is present, so "
        "render 'diff unavailable' rather than treating null as a failure. Unlike the rest of "
        "the law-change tools this one returns section text, so check its cost before looping "
        "over a change list."
    ),
    "list_watch_deliveries": (
        "Per-attempt delivery log for one watch: status code, error and attempt number, "
        "retained 90 days. Webhook watches only -- an email-only watch always returns an empty "
        "list, because email sends are not logged per attempt. Use to debug a webhook that is "
        "not arriving."
    ),
    # ------------------------------------------------------------------
    # US case law (CourtListener-backed). Hidden from the public docs, still
    # live, and used by the hosted remote server in remote.py.
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------
    "get_pricing": (
        "Current API credit pricing: per-endpoint credit costs and the credit-to-currency "
        "conversion rate (1 credit = $0.01 USD). Free, and no authentication required. Use to "
        "check what a call will cost before making it."
    ),
    # --- India: Acts & Legislation (VAQUILL_JURISDICTION=IN) -----------------
    # Published only by the India OpenAPI document, so a US deployment never
    # sees them. Kept in this shared file because descriptions.py is the
    # vocabulary BOTH jurisdictions key on, and splitting it per jurisdiction
    # would give the drift it exists to prevent somewhere new to hide.
    "search_acts": (
        "Search Indian legislation down to the individual section: Central and State "
        "Acts plus the instruments of the principal regulators (SEBI, RBI, MCA, IRDAI, "
        "TRAI, DGFT). Use for any 'what does Indian law say' question. Supports boolean "
        "and phrase queries; filters by category, state, year and status. Returns "
        "sections with title, chapter and a sourceUrl pointing at the publisher's own "
        "document. The returned actId (e.g. 'IND_central_2065') feeds every acts tool."
    ),
    "list_acts": (
        "Browse and filter enactments rather than searching their text: by jurisdiction "
        "(central or a state), issuing regulator, year and status. Use when the user "
        "wants to know WHAT exists in an area before asking what it says, or to confirm "
        "an Act's exact title before citing it."
    ),
    "list_act_filters": (
        "Self-describing filter vocabulary: every category, state, department and status "
        "the acts corpus actually holds, with counts. Call this before filtering, so a "
        "query uses a value that exists instead of returning empty because the spelling "
        "was wrong."
    ),
    "get_act_text": (
        "Source links for one enactment: the plain-text, PDF and HTML renderings, plus "
        "how many sections it holds. Use when the user wants to read or cite the Act "
        "itself rather than a matched section."
    ),
    "get_corresponding_provisions": (
        "Map a repealed Indian criminal code to the 2023 code that replaced it, section "
        "by section: IPC to BNS and CrPC to BNSS, in force from 1 July 2024. Pass either "
        "side ('ipc' or 'bns' both work). Use whenever a source, a pleading or the user "
        "cites an old section number, so you answer under the provision actually in force "
        "rather than the repealed one. 'iea'/'bsa' return 404 until that mapping lands."
    ),
    "get_act_amendments": (
        "The amendment history recorded against one enactment: substitutions, insertions "
        "and omissions, each with the amending Act and its effective date "
        "(e.g. 'Subs. by Act 22 of 2023, s. 44 (w.e.f. 13-11-2025)'). Use to check "
        "whether a provision still reads as enacted before relying on its text. An empty "
        "list means no amendment was recorded, NOT that the Act was never amended."
    ),
    # --- US: batch citation resolution ---------------------------------------
    "resolve_statute_citations_batch": (
        "Resolve up to 50 Bluebook citations in one call, returning the same confirmed "
        "section, official source link and act_id as the single-citation tool. Use when "
        "a document or answer cites several provisions: one call is far cheaper in both "
        "credits and latency than looping the single-citation tool."
    ),
}


# ---------------------------------------------------------------------------
# Per-PARAMETER descriptions
# ---------------------------------------------------------------------------
# Same job as TOOL_DESCRIPTIONS one level down, and a much bigger lever than it.
# Measured on the published documents 2026-09-02: the US catalogue is 51,854
# bytes of tool definition, of which tool descriptions are 12.8% and INPUT
# SCHEMAS are 86.0%. Two thirds of that schema mass is parameter prose inherited
# verbatim from the OpenAPI, where it exists to generate the public API
# reference and is right to be long. A tool definition is resident in the
# model's working memory on every turn, so the MCP layer is the wrong place to
# pay for it.
#
# WHAT A REWRITE MUST KEEP. Everything a caller needs to get the call right and
# to read the answer right:
#   - the operative meaning of the parameter,
#   - any constraint that turns into a 4xx (pairings, mutual exclusions),
#   - any caveat where the obvious reading is WRONG. `excludeRepealed` not
#     promising the remainder is good law, and `changedSince` being observation
#     rather than effect, both stay: this is a legal API and those two are the
#     difference between a correct answer and a confidently wrong one.
#
# WHAT IT DROPS. Prose the machine-readable schema already carries (a gloss of
# every `enum` value), API-design rationale, and worked paging examples.
#
# Only `search_us_statutes.source` had 3,529 characters spent restating 46 enum
# values that sit in the schema three lines below it.
#
# The mechanism, and why this is hand-written rather than truncated
# mechanically, is in schema_slim.py. Both directions are guarded in
# tests/test_schema_slim.py: an entry naming a parameter no document publishes
# fails, and an uncurated parameter over the budget fails.

# Keyed by (tool_name, parameter_name). Preferred, because the same name means
# different things on different tools: `corpusType` is a 15-value corpus filter
# on search, a board selector on create_watch, and a resolution constraint on
# resolve_statute_citation, and one shared entry would be wrong on two of them.
PARAM_DESCRIPTIONS_BY_TOOL: dict[tuple[str, str], str] = {
    # Three parameters the API added in early September 2026 that had no curated
    # entry, found 2026-09-03 when the OpenAPI fixtures were regenerated: their
    # inherited prose was 865 + 656 + 1,147 = 2,668 characters riding in every
    # agent's context on every turn. The API reference is the right place for the
    # long version; each one below keeps the fact a CALLER cannot guess and drops
    # the rest.
    ("get_us_statute_section_text", "asOf"): (
        "Text as it stood on this date (`YYYY-MM-DD`), same cost. A "
        "RECONSTRUCTION from observed changes, not an archive: read the response's "
        "`asOf.isBounded` before citing it, since false means the answer is "
        "limited by when capture began, not by the law. A section we cannot "
        'rebuild returns `source: "unavailable"` and is refunded.'
    ),
    ("get_us_statute_section_text", "format"): (
        "Which representations to return. The default `both` carries a long "
        "section's text twice, so `plain` or `html` roughly halves the payload at "
        "the same price. `content` returns only the operative text, about 1 KB "
        "instead of 30 KB on a long section, but ONLY United States Code sections "
        "can be split: on any other corpus it returns no text, so check for null."
    ),
    ("search_us_statutes", "includeBody"): (
        "Return each hit's full text inline on `body`, instead of one "
        "`/section/{actId}/body` call per hit. Buys latency, not a discount: the "
        "4-credit search PLUS 6 credits for every row that returns text, so ten "
        "rows is 64. ⚠️ It multiplies with `limit`; 50 rows is 304 credits in one "
        "call. Rows with no text are not charged, so read `creditsConsumed`. "
        "Prefer this over a bigger `excerptChars`: an excerpt is windowed around "
        "the match and can start mid-section, so it is not safe to quote."
    ),
    # --- search_us_statutes: 19,242 bytes, 38% of the whole US catalogue -----
    ("search_us_statutes", "source"): (
        "The named body of law within a `corpusType` that folds several together: "
        "`FEDERAL_RULES` into `frcp`/`fre`/`sct`, `CFR` into `far`/`dfars`, "
        "`AGENCY_GUIDANCE` into ~34 agency sources, `AGENCY_ADJUDICATION` into "
        "`olc_opinion`/`mspb_precedential`/`mspb_nonprecedential`. Every result "
        "carries its own `source`, so a hit's value can be passed straight back."
    ),
    # 🔴 This sentence RESTATES the `enum` sitting beside it, which is the one
    # thing `schema_slim` never touches. It is written out anyway because the
    # bare tokens do not say which ones need `state`, and that pairing is the
    # single most common 422. The cost is that it goes stale silently: it sat
    # missing `AGENCY_ADJUDICATION` and `STATUTE_COMPILATION` after both landed
    # on 2026-09-03, so an agent reading the description would never pass either
    # even though the enum accepted them. `test_schema_slim.py::
    # test_a_description_that_lists_enum_values_lists_all_of_them` now fails on
    # exactly that, so the next token cannot drift the same way.
    ("search_us_statutes", "corpusType"): (
        "Restrict to one corpus, or several as a list. Federal: `USC`, `CFR`, "
        "`CONSTITUTION`, `FEDERAL_RULES`, `FEDERAL_REGISTER`, `EXECUTIVE_ACTION`, "
        "`AGENCY_GUIDANCE`, `SENTENCING_GUIDELINES`, `US_TAX_TREATY`, `SESSION_LAW` "
        "(Statutes at Large, as enacted), `STATUTE_COMPILATION` (an act as amended "
        "through a stated later law), `AGENCY_ADJUDICATION` (federal administrative "
        "adjudication: DOJ Office of Legal Counsel opinions and MSPB decisions). "
        "Pair with `state`: `STATE`, `REGULATION`, `STATE_RULES`, "
        "`STATE_CONSTITUTION`, `STATE_AGENCY_GUIDANCE`. Omit for all."
    ),
    ("search_us_statutes", "changedSince"): (
        "Only sections we OBSERVED changing on or after this date (`YYYY-MM-DD`). "
        "Observed, not effective: the date we saw it, an upper bound on when it took "
        "effect. Capture began long after the corpus did and events sweep at 24 "
        "months, so empty means no captured change, never that nothing was amended."
    ),
    ("search_us_statutes", "excludeRepealed"): (
        "Drop sections whose own status says they are not operative (repealed, "
        "renumbered, transferred, expired, superseded, omitted, and the rest). "
        "Removes what we KNOW is dead; it does not promise the remainder is good "
        "law. Read `goodLawStatus` per result to tell them apart: `good_law` is "
        "checked, `unknown` is unchecked."
    ),
    ("search_us_statutes", "actStatus"): (
        "Positively scope to raw publisher statuses: `repealed` for dead law only, "
        "`in_force` for sections affirmatively marked current. The inverse of "
        "`excludeRepealed`, and what a compliance diff asking what was LOST needs. "
        "Combining a dead status with `excludeRepealed: true` is rejected 422."
    ),
    ("search_us_statutes", "state"): (
        "Jurisdiction. A 2-letter code for one of the 52 supported US jurisdictions "
        "(50 states + DC + PR), or `federal` for USC / CFR / Constitution / federal "
        "rules. Pass a list to search several at once. Case-insensitive. Omit to "
        "search every jurisdiction."
    ),
    ("search_us_statutes", "matchType"): (
        "`any` (default) is hybrid semantic + keyword ranking, for natural-language "
        "questions. `all` requires every query term; `phrase` matches an exact "
        "phrase, for a defined term. To pull up one section, pass its citation as "
        "the query and it resolves to that section at rank 1."
    ),
    ("search_us_statutes", "code"): (
        "Restrict to specific state statutory codes, e.g. `tx_pe` for the Texas Penal "
        "Code. Values are the `actId`s from list_statute_divisions. List allowed, "
        "across states. The only way to scope below a whole jurisdiction: `state=tx` "
        "alone searches all ~15 Texas codes."
    ),
    ("search_us_statutes", "fields"): (
        'Return only these result fields, e.g. `["title", "excerpt"]`. A result '
        "carries 40+ fields, most null on any given row. `actId` and `citation` are "
        "always included. Unknown names are rejected 422. Omit for the full object."
    ),
    ("search_us_statutes", "yearFrom"): (
        "Only sections last amended in or after this year; pair with `yearTo` for a "
        "window. Tracks the publisher's own amendment credit, not when we rebuilt "
        "the corpus. About a fifth of sections carry no credit and are excluded once "
        "either bound is set."
    ),
    ("search_us_statutes", "yearTo"): (
        "Only sections last amended in or before this year. This filters the LAST "
        "amendment, so a section amended in 2025 is excluded by `yearTo=2024` even "
        "though it existed in 2024. A currency filter, not point-in-time retrieval."
    ),
    ("search_us_statutes", "chapter"): (
        "One or more chapters within a title or code, e.g. `21` for USC Title 42 "
        "Chapter 21. Pass a hit's `parent.chapter` back to search its neighbors. "
        "Chapter numbers repeat across titles, so pair with `titleNumber` (USC) or "
        "`code` (state); an unpaired chapter is rejected."
    ),
    ("search_us_statutes", "part"): (
        "One or more parts within a title, e.g. `240` for 17 C.F.R. Part 240. The CFR "
        "counterpart to `chapter`: pass a hit's `parent.part` back. Pair with "
        "`titleNumber`; an unpaired part is rejected."
    ),
    ("search_us_statutes", "offset"): (
        "How many results to skip, for paging. Every page of a query is cut from one "
        "ranking, so results never repeat or go missing between pages. The deepest "
        "reachable result is `offset` + `limit`; check `hasMore`."
    ),
    # --- batch inputs --------------------------------------------------------
    ("get_sections_batch", "actIds"): (
        "Section identifiers from a prior search, up to 50 per call. Duplicates are "
        "collapsed and order is preserved. DO NOT BUILD THESE FROM A CITATION: the "
        "chapter/article segments exist only in the data, so assembled ids miss. To "
        "start from a citation, use resolve_statute_citation."
    ),
    ("resolve_statute_citations_batch", "citations"): (
        "Bluebook citation strings, up to 50 per call. Duplicates are collapsed and "
        "order preserved, so `results` lines up with the de-duplicated input. Priced "
        "PER CITATION at the single-resolve rate: batching is a round-trip and "
        "latency win, not a discount."
    ),
    # --- resolve: constraints, not hints -------------------------------------
    ("resolve_statute_citation", "state"): (
        "Optional 2-letter jurisdiction to resolve WITHIN, e.g. `tx`. Some citation "
        "forms are shared: `8 CCR 1206-2` is Colorado and `22 CCR 76227` is "
        "California. A constraint, not a hint: a citation naming a different "
        "jurisdiction returns `resolved: false` rather than being forced into this one."
    ),
    ("resolve_statute_citation", "corpusType"): (
        "Optional corpus to resolve WITHIN: `STATE`, `REGULATION`, `STATE_RULES`, "
        "`CONSTITUTION`, `STATE_CONSTITUTION`. Narrows a citation whose form several "
        "corpora share; like `state`, a citation belonging to another corpus resolves "
        "to nothing instead."
    ),
    # --- law-change alerts ---------------------------------------------------
    # `scope` runs past the budget on purpose. Three mutually exclusive forms,
    # and the wrong one creates a watch that can never fire. See
    # `schema_slim.uncurated_overruns`.
    ("create_watch", "scope"): (
        "Narrow the alert to one citation instead of a whole source. Three mutually "
        'exclusive forms. Hierarchy prefix: `{"title": "21", "part": "314"}`, where '
        "every level set must match and `title` is required whenever a narrower level "
        'is set. Exact section: `{"actId": "CFR_T21_P314_S314_50"}`, validated at '
        "create time and the only form EVERY source accepts, including flat ones. "
        'Named source: `{"source": "fdic_fil"}`, accepted on `agency_guidance`, '
        "`agency_manuals` and `cfr` only. Omit to watch the whole source."
    ),
    ("create_watch", "corpusType"): (
        "Board's corpus_type (e.g. `state`, `state_regulation`, `federal_register`, "
        "`agency_guidance`), matched case-insensitively so `USC` / `CFR` work too. "
        "Call list_boards for the authoritative list: this is a growing set, not a "
        "fixed enum, and not every corpus is a watchable board."
    ),
    ("create_watch", "state"): (
        "Board's state, 2-letter and case-insensitive. For a federal board (USC, "
        "eCFR, the Federal Register) pass `federal` or omit entirely; the two are "
        "equivalent. Must otherwise match the `state` list_boards returned for this "
        "corpusType."
    ),
    ("create_watch", "webhookSecret"): (
        "Optional signing secret, stored encrypted and never returned. Every delivery "
        "then carries `X-Vaquill-Signature: sha256=<hex>`, an HMAC-SHA256 of the raw "
        "request body bytes keyed with this secret. Verify over the raw body before "
        "parsing JSON, constant-time."
    ),
    # The published document gives `scope` the SAME text on create and update,
    # and that text ends "omit entirely to watch the whole source". True of the
    # POST; false of the PATCH, where omitting a field leaves it unchanged.
    # Curating them separately fixes an inaccuracy rather than just shortening
    # one, which is why this is not a single shared entry.
    ("update_watch", "scope"): (
        "Replace the alert's narrowing. Three mutually exclusive forms. Hierarchy "
        'prefix: `{"title": "21", "part": "314"}`, where every level set must '
        "match and `title` is required whenever a narrower level is set. Exact "
        'section: `{"actId": "CFR_T21_P314_S314_50"}`, the only form EVERY source '
        'accepts. Named source: `{"source": "fdic_fil"}`, on `agency_guidance`, '
        "`agency_manuals` and `cfr` only. Omitting the field leaves the current "
        "scope unchanged."
    ),
    ("update_watch", "webhookAuth"): (
        "Replace the outbound credential config, sent as a WHOLE object rather than "
        "field by field: a scheme without a credential is not a partial edit, it is a "
        'broken config. `{"scheme": "none"}` removes auth. Keeping the scheme and '
        "omitting `secret` retains the stored credential."
    ),
    ("list_watch_changes", "beforeId"): (
        "Return only changes with an `id` below this: the cursor for walking BACK "
        "through history, where `sinceId` walks forward into new ones. Pass the "
        "smallest `id` on your last page."
    ),
}

# Keyed by parameter name alone, applying wherever that name appears. Reserved
# for parameters that are genuinely THE SAME everywhere: `act_id` carries one
# identical 340-character description on seven tools, so a single entry collapses
# all seven and cannot drift between them. A tool-scoped entry above wins.
PARAM_DESCRIPTIONS: dict[str, str] = {
    "act_id": (
        "Section identifier, e.g. `USC_T42_C21_S1983` (Title 42, Chapter 21, Section "
        "1983, written 42 U.S.C. 1983). Take it from a search result rather than "
        "assembling it: the title and section are derivable from a citation but the "
        "CHAPTER is not, so hand-built ids usually 404."
    ),
}


# ---------------------------------------------------------------------------
# Tool titles
# ---------------------------------------------------------------------------

# The DISPLAY name a client shows beside each tool. FastMCP derives one from the
# tool name when this is unset, which title-cases the underscores and produces
# "Get Us Statute Section Text": correct English, wrong acronym, and the word
# "Us" reads as the pronoun. The Anthropic connector directory also REQUIRES a
# title on every tool and syncs these into the submission, so they are
# user-facing copy rather than an internal label.
#
# Written as verb-free noun phrases where the tool reads and verb-first where it
# writes, so a reader scanning a list can tell the two apart without opening the
# description. A name absent from this map still gets FastMCP's derived title,
# so an unmapped new tool degrades rather than breaks; the guard in
# `test_annotations_and_order.py` fails on an acronym mangled that way.
TOOL_TITLES: dict[str, str] = {
    # --- US: retrieval -----------------------------------------------------
    "search": "Search US Law",
    "fetch": "Fetch Document",
    "search_us_statutes": "Search US Statutes",
    "get_us_statute_section": "Statute Section Metadata",
    "get_us_statute_section_text": "Statute Section Text",
    "get_sections_batch": "Statute Sections (Batch)",
    "list_statute_divisions": "Browse Code Structure",
    "list_statutes_coverage": "Corpus Coverage",
    # --- US: citation and context ------------------------------------------
    "resolve_statute_citation": "Resolve Citation",
    "resolve_statute_citations_batch": "Resolve Citations (Batch)",
    "get_section_cited_by": "Sections Citing This One",
    "get_section_cross_state": "Same Rule in Other States",
    "get_section_definitions": "Defined Terms in Section",
    "get_section_neighbors": "Adjacent Sections",
    "get_section_changes": "Section Change History",
    # --- US: law-change alerts ---------------------------------------------
    "list_boards": "Alert Boards",
    "list_watches": "Law Change Watches",
    "list_watch_changes": "Changes on a Watch",
    "list_watch_deliveries": "Watch Deliveries",
    "get_watch_change_diff": "Change Diff",
    "create_watch": "Create Law Change Watch",
    "update_watch": "Update Law Change Watch",
    "test_watch": "Send Test Alert",
    "delete_watch": "Delete Law Change Watch",
    # --- Shared -------------------------------------------------------------
    "get_pricing": "Credit Pricing",
    # --- India --------------------------------------------------------------
    # `search` and `fetch` are registered once per jurisdiction under the SAME
    # tool name, so the map is keyed by a suffixed alias here and each India
    # registration asks for its own. Two servers, two catalogues, never both in
    # one client.
    "search_in": "Search Indian Legislation",
    "fetch_in": "Fetch Indian Enactment",
    "search_acts": "Search Indian Acts",
    "list_acts": "Browse Indian Acts",
    "list_act_filters": "Act Filter Values",
    "get_act_text": "Act Text",
    "get_act_amendments": "Act Amendment History",
    "get_corresponding_provisions": "IPC/CrPC to BNS/BNSS Mapping",
}
