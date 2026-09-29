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

SCOPE: both jurisdictions. US (18 published tools: statutes, regulations,
constitutions, court rules and agency guidance) and India (22 published tools:
Central and State Acts plus regulator instruments). Both counts include the
`search` / `fetch` aliases, which are titled here but described in aliases.py.
The law-change alert tools (boards and watches) were removed from the MCP
catalogue on 2026-09-29 and have no entries here. India was retired on
2026-08-20 and RESTORED on 2026-09-01, so an instruction to strip "Indian"
descriptions is obsolete: the IN entries below are live and shipping.

Every tool in BOTH catalogues must have an entry in ``TOOL_DESCRIPTIONS`` and in
``TOOL_TITLES``. ``tests/test_derived_catalogue.py`` asserts that in both
directions, so a missing entry and an orphaned one each fail.
"""

TOOL_DESCRIPTIONS: dict[str, str] = {
    # --- Added 2026-09-19 -------------------------------------------------
    # Sixteen tools that the API had been publishing for weeks with no curated
    # entry. They surfaced when the committed OpenAPI fixtures were refreshed:
    # the September India redesign took that document from 6 paths to 19, and
    # `/us/statutes/count` and the two credit-balance routes landed on the US
    # one, while the fixtures here still described the old shape. Until this,
    # every one of them shipped its raw multi-paragraph OpenAPI description
    # into every agent's context on every turn.
    "count_statute_sections": (
        "Exact section count for a jurisdiction, corpus, code, title, chapter "
        "or part. Use when sizing a job before walking divisions and fetching "
        "sections. This counts a scope, not search matches. There is no query "
        "parameter, because search ranks within a bounded window and cannot "
        "give an exact total of matching sections. A zero count may indicate "
        "a wrong filter. "
    ),
    "get_act_section": (
        "Metadata for one provision, including its citation, position in the "
        "act, structural features and references. Use when identifying or "
        "inspecting a provision without fetching its wording. Text is not "
        "included. Use get_act_section_body to read it. wordCount covers all "
        "passages, and actsReferenced normalizes whitespace before "
        "deduplication so publisher line breaks do not create duplicate acts. "
    ),
    "get_act_section_body": (
        "The publisher's full text for one provision, taken directly from the "
        "act document without synthesized search headers. Use when reading, "
        "quoting or citing the provision's wording rather than its metadata. "
        "Amendments are not applied to this text. Check get_section_history "
        "for recorded changes before treating the wording as current law. "
    ),
    "get_act_status": (
        "An act's standing in three separate fields, publisherStatus for "
        "India Code's label, servedStatus for API availability and "
        "repealClaim for the publisher's sourced repeal record. Use when "
        "assessing whether an act remains law or why it is unavailable. These "
        "are not one verdict. A repealClaim does not verify that the "
        "repealing instrument was brought into force. "
    ),
    "get_act_structure": (
        "An act's table of contents, with chapters and parts where the "
        "publisher supplies them and provisions listed beneath. Use when "
        "locating a section or planning a traversal of the act. hasHierarchy "
        "is false for most acts, where nodes is a flat section list rather "
        "than an error. Section numbers sort numerically with alphabetic "
        "suffixes attached. "
    ),
    "get_coverage": (
        "India corpus counts for acts and provisions, broken down by "
        "jurisdiction, regulator and status, plus coverage depth. Use when "
        "checking whether the corpus supports a question before searching or "
        "interpreting an empty result. The depth block distinguishes text "
        "coverage from much thinner parsed amendment coverage. "
        "actsClaimingAmendmentsWithoutEvents marks missing records, not an "
        "absence of amendments. "
    ),
    "get_credit_balance": (
        "The credits this API key can spend now, calculated from live credit "
        "buckets under the same expiry rules used for billing. Use for "
        "pre-flight checks or low-balance alerts. creditsRemaining is a "
        "spendable balance, not a cached estimate. Any valid API key works "
        "without a particular scope, and normal rate limits still apply. "
    ),
    "get_india_credit_balance": (
        "The account-wide spendable credit balance shared by the India and "
        "United States APIs. Use for pre-flight checks or low-balance alerts "
        "from an India workflow. This returns the same balance as "
        "get_credit_balance, not a separate India allowance. Check bySource "
        "before assuming credits will last. Subscription credits expire at "
        "the period end. "
    ),
    "get_pricing_in": (
        "Credit-to-price conversion and per-endpoint costs for the India "
        "legislation API, billed in US dollars. Use when estimating a "
        "workflow or comparing endpoint charges. Set region to US for United "
        "States primary-law pricing instead. Both surfaces use the same API "
        "key and account-wide credit balance. No authentication is required. "
    ),
    "get_section_history": (
        "Recorded amendments to one provision, including the changing Act and "
        "section, effective dates and replaced wording where published. Use "
        "when tracing changes or checking whether published text reflects "
        "later amendments. An empty history can mean no changes or no parsed "
        "records, as coverage explains. Read appliedStatus on every event. "
        "Amendments are not applied to served text. "
    ),
    "india_act_cited_by": (
        "Paged inbound citations from provisions elsewhere in the corpus that "
        "name an act. Use when finding provisions that mention the act, "
        "rather than following its outbound references. Matching uses the "
        "act's title, not its identifier, so title variants and line breaks "
        "can be missed. Read matchBasis and treat results as a floor. total "
        "counts distinct citing provisions across all pages. "
    ),
    "india_act_definitions": (
        "Extracted defined terms and their defining provisions, plus a "
        "separate definitionSections list. Use when a question turns on an "
        "act's own meaning of a term. Extraction covers only a minority of "
        "acts, so an empty result does not mean the act defines nothing. "
        "definitionSections identifies provisions to read even without "
        "extracted terms. terms is paged, but definitionSections is not. "
    ),
    "india_act_subordinate": (
        "Paged rules, regulations, notifications and orders the publisher "
        "records as made under an act. Use when looking beyond the parent "
        "statute to its subordinate instruments. Matching uses the parent's "
        "title, not parent_act_id, which is an India Code identifier that "
        "does not address this corpus. Check matchBasis. total counts "
        "distinct instruments across all pages, while returned counts rows on "
        "this page. "
    ),
    "india_section_references": (
        "Outbound citations from a provision to other enactments and sections "
        "of its own act. Use when following references outwards, not finding "
        "citations to the act. actId identifies served targets, while "
        "resolved false marks coverage gaps rather than parse failures. "
        "Results are not paged. totalActs and totalSections give full counts, "
        "so check truncated before treating returned references as complete. "
    ),
    "resolve_india_citation": (
        "The provision named by an Indian legal citation, accepting common "
        "abbreviations and flexible element order. Use when a user provides a "
        "citation rather than a research topic, such as s.302 IPC, O. 39 R. 1 "
        "CPC or Art. 21 of the Constitution. Subsection and clause references "
        "are supported. Malformed citations are rejected rather than reported "
        "as not found. "
    ),
    "resolve_india_citations_batch": (
        "Provision resolutions for a list of Indian legal citations, using "
        "the same resolver as resolve_india_citation. Use when several "
        "citations need resolving without individual calls. Accepts up to 50 "
        "distinct citations and 500 entries before deduplication. One "
        "citation failing does not invalidate the others. Unknown request "
        "fields are rejected rather than ignored. "
    ),
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
# on search and a resolution constraint on resolve_statute_citation, and one
# shared entry would be wrong on one of them.
PARAM_DESCRIPTIONS_BY_TOOL: dict[tuple[str, str], str] = {
    # --- Added 2026-09-19 -------------------------------------------------
    # Twelve parameters the API had been publishing with descriptions of 262
    # to 513 characters and no curated entry, riding in every agent's context on
    # every turn. Each keeps the one fact a CALLER cannot guess and drops the
    # background; the API reference is the right place for the long version.
    ("count_statute_sections", "excludeRepealed"): (
        "Only sections with an affirmatively dead status are excluded. "
        "Sections with no recorded status are kept. US Code uses a stored "
        "annual edition, so sections repealed after that edition closed are "
        "still counted."
    ),
    ("get_act_amendments", "type"): (
        "Action classes are returned in nominal form, but filters accept "
        "both nominal and stored verbal spellings. For example, "
        "`substitution` and `substituted` are both accepted."
    ),
    ("get_corresponding_provisions", "act_code"): (
        "`ipc` and `bns` return the same mapping, as do `crpc` and `bnss`. "
        "Case-insensitive. `iea` and `bsa` are accepted but return 404 "
        "until their mapping is available."
    ),
    ("get_pricing", "region"): (
        "Defaults to the jurisdiction of the documentation you are reading. "
        "If omitted, returns pricing only for that jurisdiction's "
        "endpoints, not prices across jurisdictions."
    ),
    ("get_pricing_in", "region"): (
        "Defaults to the jurisdiction of the documentation you are reading. "
        "If omitted, returns pricing only for that jurisdiction's "
        "endpoints, not prices across jurisdictions."
    ),
    ("get_sections_batch", "includeBody"): (
        "Adds the ordinary body price for each row that returns text. "
        "Unresolved text returns `body: null` with no body charge. Use "
        "`creditsConsumed` for the total rather than calculating from the "
        "number of IDs."
    ),
    ("list_acts", "department"): (
        "Accepts both regulator slugs such as `sebi` and `rbi` and "
        "free-text state department names such as `Law Department`. Values "
        "are not limited to a fixed enum or a uniform slug format."
    ),
    ("list_statute_divisions", "excludeRepealed"): (
        "Only sections with an affirmatively dead status, including "
        "repealed, superseded or renumbered, are excluded. Sections with no "
        "recorded status are kept, not treated as repealed."
    ),
    ("resolve_statute_citations_batch", "corpusType"): (
        "Restricts every citation to one corpus: `STATE`, `REGULATION`, "
        "`STATE_RULES`, `CONSTITUTION`, `STATE_CONSTITUTION`. `STATE` "
        "covers federal and state statutes, and the two constitution values "
        "are synonyms here, with `state` distinguishing the jurisdiction."
    ),
    ("search_acts", "department"): (
        "Accepts both regulator slugs such as `sebi` and `rbi` and "
        "free-text state department names such as `Law Department`. Values "
        "are not limited to a fixed enum or a uniform slug format."
    ),
    ("search_acts", "legalSubject"): (
        "Accepts one value or a list, matched against subject "
        "classifications assigned at ingest."
    ),
    ("search_acts", "matchType"): (
        "One of `any`, `all`, `phrase`. Filters ranked candidates rather "
        "than re-querying the index, so `phrase` matches only within the "
        "top candidates and not every corpus match. Narrow with structured "
        "filters first when you need exhaustive phrase results. Defaults to "
        "`any`."
    ),
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
        "section's text twice, so `plain` or `html` roughly halves the "
        "payload at the same price. `content` and `operative` return only "
        "the operative text, about 1 KB instead of 30 KB on a long section, "
        "but ONLY United States Code sections can be split: on any other "
        "corpus they return no text, so check for null."
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
        "Restrict to one corpus, or several as a list. Federal: `USC`, "
        "`USC_ANNUAL` (published past editions), `CFR`, `CFR_ANNUAL` (same, "
        "superseded by construction), `CONSTITUTION`, `FEDERAL_RULES`, "
        "`FEDERAL_REGISTER`, `FEDERAL_REGISTER_NOTICE` (a curated slice, "
        "not the whole series), `EXECUTIVE_ACTION`, `AGENCY_GUIDANCE`, "
        "`SENTENCING_GUIDELINES`, `US_TAX_TREATY`, `SESSION_LAW` (Statutes "
        "at Large, as enacted), `STATUTE_COMPILATION` (an act as amended "
        "through a stated later law), `AGENCY_ADJUDICATION` (DOJ Office of "
        "Legal Counsel opinions and MSPB decisions). Pair with `state`: "
        "`STATE`, `REGULATION`, `STATE_RULES`, `STATE_CONSTITUTION`, "
        "`STATE_AGENCY_GUIDANCE`, `STATE_AG_OPINION`. Omit for all."
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
    # --- US: sizing and account ---------------------------------------------
    "count_statute_sections": "Count Sections in a Scope",
    "get_credit_balance": "Credit Balance",
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
    "get_coverage": "India Corpus Coverage",
    "get_pricing_in": "India Credit Pricing",
    "get_india_credit_balance": "Credit Balance",
    "get_act_structure": "Act Table of Contents",
    "get_act_section": "Act Section Metadata",
    "get_act_section_body": "Act Section Text",
    "get_act_status": "Act Status and Repeal Record",
    "get_section_history": "Section Amendment History",
    "india_section_references": "Citations From a Section",
    "india_act_cited_by": "Acts Citing This One",
    "india_act_definitions": "Defined Terms in an Act",
    "india_act_subordinate": "Rules Made Under an Act",
    "resolve_india_citation": "Resolve Indian Citation",
    "resolve_india_citations_batch": "Resolve Indian Citations (Batch)",
}
