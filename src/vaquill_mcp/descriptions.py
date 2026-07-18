"""LLM-optimized tool descriptions for Vaquill MCP tools.

Each description tells the LLM WHEN to use the tool and WHAT it returns.
Kept concise (50-100 words) for efficient context usage.

These override the verbose OpenAPI descriptions which contain multi-paragraph
markdown with tables, code examples, and SSE documentation — far too long
for an LLM tool description.

Credit costs are intentionally NOT written here. They are injected at server
startup from the live API (`GET /api/v1/api-credits/pricing/all`) by
``server.py`` so the numbers can never drift from ``CREDIT_PRICING`` in the
backend. See ``server.py`` (``_TOOL_COST_ENDPOINTS`` + ``_format_cost``).
"""

TOOL_DESCRIPTIONS: dict[str, str] = {
    "ask_legal_question": (
        "AI-generated legal answer grounded in primary sources. countryCode='US' (default) "
        "covers USC, CFR, 50-state law, and CourtListener case law. countryCode='IN' covers "
        "31M+ Indian judgments + 23K+ acts. US-only sourcesFilter: 'all' | 'statutes_only' "
        "| 'cases_only'. Modes: 'standard' or 'deep' (multi-hop, more thorough). "
        "Pass chatHistory for follow-ups. Returns answer with numbered citations."
    ),
    "search_legal_cases": (
        "Boolean keyword search of the **Indian** corpus. Supports AND, OR, NOT and quoted "
        "phrases. Filter by courtType (supreme_court, high_court), courtName, year range. "
        "Returns paginated results with text, citation, court, relevance score, snippet, PDF. "
        "Present only the top results, do NOT emphasize total count. Use pageSize 10 for "
        "conversational answers, 20 for exhaustive lists. For US case law, use "
        "ask_legal_question with countryCode='US'."
    ),
    "quick_search": (
        "Fast compact **Indian** legal case search returning top 3-5 results with just the "
        "essentials: title, citation, court, year, summary excerpt, and PDF link. Same "
        "boolean query syntax as search_legal_cases but returns fewer, flatter results. "
        "Best when you need a quick overview rather than detailed results."
    ),
    "resolve_citation": (
        "Resolve any Indian legal citation format to its canonical case record. Accepts "
        "SCC, AIR, SCR, MANU, SCALE, INSC formats (e.g., '(2019) 11 SCC 706' or "
        "'AIR 1976 SC 1207'). Returns case details and all known citation aliases/formats. "
        "Returns found=false (not an error) when citation cannot be resolved."
    ),
    "search_cases_by_citation": (
        "Search for legal cases by citation text or case name. Use when you know part of "
        "a case name (e.g., 'Maneka Gandhi') or a partial citation. Filter by court code "
        "(SC, DEL, BOM, MAD, etc.), year range, and validity status (GOOD_LAW, OVERRULED, "
        "DISTINGUISHED, etc.). Returns up to 50 matching cases with metadata."
    ),
    "lookup_case": (
        "Get full details for a specific case by its citation. Returns comprehensive case "
        "metadata, all known citation aliases, and citation treatment statistics showing "
        "how many times the case was followed, distinguished, overruled, approved, or "
        "referred. Use after resolve_citation or search_cases_by_citation for deep case "
        "analysis."
    ),
    "get_citation_network": (
        "Traverse the citation network around a case. Returns nodes (cases) and edges "
        "(citing relationships) with treatment types (followed, distinguished, overruled). "
        "Specify direction: 'outbound' (cases this cites), 'inbound' (cases citing this), "
        "or 'both'. Set depth (1-3 hops) and limit (1-100 nodes). Useful for understanding "
        "a case's legal influence."
    ),
    "get_pricing": (
        "Get current API credit pricing. Returns per-endpoint credit costs and "
        "credit-to-currency conversion rates (1 credit = $0.01 USD). "
        "No authentication required. Use to check costs before making API calls."
    ),
    "search_legislation": (
        "Search 23,000+ Indian acts, regulations, and legislation using semantic search. "
        "Find specific statutory provisions, definitions, penalties, and procedures. "
        "Filter by category (central, state, regulatory), state, department (SEBI, RBI, "
        "TRAI, etc.), and year range. Returns relevant act sections with text excerpts, "
        "section numbers, provision type, and PDF links. "
        "Use for questions like 'What is the penalty for insider trading under SEBI Act?' "
        "or 'Definition of goods under GST Act'."
    ),
    "get_act_text": (
        "Get URLs for the full text, PDF, and HTML versions of a specific Indian act. "
        "Pass the act_id (e.g., 'IND_central_2187' for Indian Contract Act). "
        "Returns R2 CDN URLs — fetch the text/PDF content directly from those URLs."
    ),
    "get_amendments": (
        "Get the complete amendment history for an Indian act. Returns all footnotes "
        "showing substitutions, insertions, omissions, and notes made by amending acts. "
        "Filter by section number or amendment type. Each footnote shows the amending "
        "act name and original text (if available). Use to trace how a statute evolved."
    ),
    "list_legislation": (
        "Browse 23,000+ Indian acts, regulations, and legislation. Use to **discover act_id "
        "values** for get_act_text and get_amendments. Filter by category (central, state, "
        "regulatory, repealed, spent), state slug, department (sebi, rbi, etc.), year range, "
        "and status (in_force, repealed, spent). Sort by year_desc, year_asc, title_asc, "
        "title_desc, or popular."
    ),
    "search_us_statutes": (
        "Semantic search across the **United States Code (USC)** and **Code of Federal "
        "Regulations (CFR)**. Use for federal statutory and regulatory questions: SEC "
        "(Title 17), FDA (Title 21), civil rights (Title 42), tax (Title 26), etc. Filter "
        "by corpusType ('USC' | 'CFR') and titleNumber. Returns sections with citation, "
        "title hierarchy, HTML/PDF/XML links. The returned act_id (e.g. 'USC_T42_C21_S1983') "
        "feeds get_us_statute_section_text for full text."
    ),
    "get_us_statute_section": (
        "Get metadata for a specific US statute or regulation section by act_id (e.g. "
        "'USC_T42_C21_S1983'). The act_id comes from search_us_statutes results or "
        "ask_legal_question sources. Returns citation, title hierarchy, breadcrumb, and "
        "links to HTML, PDF, and XML formats. Use before get_us_statute_section_text to "
        "preview a section."
    ),
    "get_us_statute_section_text": (
        "Get the **full text** of a US statute or regulation section (USC or CFR) by "
        "act_id. Returns both styled HTML (with cross-references and paragraph numbering "
        "as published in the official code) and plain text. Use when you need the actual "
        "statutory language for quotation, drafting, or analysis."
    ),
}
