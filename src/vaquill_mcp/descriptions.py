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
    "list_statutes_laws": (
        "Catalog of the distinct bodies of law available (a state's statutes, a state's "
        "regulations, the US Code, the US Constitution, federal court rules, and so on), each "
        "with its corpusType and section count. Use to discover the corpusType values that "
        "filter search_us_statutes."
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
    "search_legal_cases": (
        "Boolean keyword search across 8M+ US federal and state court opinions. Supports AND, "
        "OR, NOT and quoted phrases; filter by court and year range. Returns paginated results "
        "with citation, court, date, relevance score and a snippet. Present the top results "
        "only and do not emphasize the total count. Use pageSize 10 for a conversational "
        "answer, 20+ for an exhaustive list."
    ),
    "quick_search": (
        "Fast, compact US case law search returning the top few results with just the "
        "essentials: title, citation, court, year and a short excerpt. Same boolean syntax as "
        "search_legal_cases but flatter and cheaper. Best when you need a quick orientation "
        "rather than full results."
    ),
    "resolve_citation": (
        "Resolve a US case citation ('603 U.S. 369', '410 U.S. 113') to its canonical record, "
        "returning the case details and cluster id. Returns found=false rather than an error "
        "when the citation cannot be resolved. Use before lookup_case or get_citation_network, "
        "both of which want the resolved id."
    ),
    "lookup_case": (
        "Full details for one US case: opinion text, the panel of judges, its citation list "
        "and disposition. Use after resolve_citation when you need to read or quote the "
        "opinion itself rather than just confirm it exists."
    ),
    "get_citation_network": (
        "Citation graph around a US case: both who cites it and what it relies on, with "
        "per-node citation counts and treatment hints. Depth 1 is the direct neighbourhood; "
        "depth 2 reaches citing-of-citing and costs substantially more. Use to judge whether a "
        "case is still good law and how influential it has been."
    ),
    # ------------------------------------------------------------------
    # Meta
    # ------------------------------------------------------------------
    "get_pricing": (
        "Current API credit pricing: per-endpoint credit costs and the credit-to-currency "
        "conversion rate (1 credit = $0.01 USD). Free, and no authentication required. Use to "
        "check what a call will cost before making it."
    ),
}
