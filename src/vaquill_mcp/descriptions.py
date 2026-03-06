"""LLM-optimized tool descriptions for Vaquill MCP tools.

Each description tells the LLM WHEN to use the tool, WHAT it returns,
and the credit cost. Kept concise (50-100 words) for efficient context usage.

These override the verbose OpenAPI descriptions which contain multi-paragraph
markdown with tables, code examples, and SSE documentation — far too long
for an LLM tool description.
"""

TOOL_DESCRIPTIONS: dict[str, str] = {
    "ask_legal_question": (
        "Ask a legal question and get an AI-generated answer grounded in 13M+ Indian "
        "court judgments. Use 'standard' mode (fast, 0.5 credits) for factual questions "
        "or 'deep' mode (thorough multi-hop analysis, 2.0 credits) for complex comparisons. "
        "Pass chatHistory for follow-up questions. Returns answer text with numbered source "
        "citations including case name, court, year, relevance score, excerpt, and PDF link."
    ),
    "search_legal_cases": (
        "Search the legal corpus using boolean keyword queries. Supports AND, OR, NOT "
        "operators and quoted phrases (e.g., '\"beyond reasonable doubt\" AND murder'). "
        "Filter by court type, court name, year range, and country code. Returns paginated "
        "results with content text, citation, court, relevance score, highlighted snippet, "
        "and PDF URL. Cost: 1 credit per search."
    ),
    "quick_search": (
        "Fast compact legal search returning top 3-5 results with just the essentials: "
        "title, citation, court, year, summary excerpt, and PDF link. Same boolean query "
        "syntax as search_legal_cases but returns fewer, flatter results. Best when you "
        "need a quick overview rather than detailed results. Cost: 0.1 credits."
    ),
    "resolve_citation": (
        "Resolve any Indian legal citation format to its canonical case record. Accepts "
        "SCC, AIR, SCR, MANU, SCALE, INSC formats (e.g., '(2019) 11 SCC 706' or "
        "'AIR 1976 SC 1207'). Returns case details and all known citation aliases/formats. "
        "Returns found=false (not an error) when citation cannot be resolved. Cost: 0.1 credits."
    ),
    "search_cases_by_citation": (
        "Search for legal cases by citation text or case name. Use when you know part of "
        "a case name (e.g., 'Maneka Gandhi') or a partial citation. Filter by court code "
        "(SC, DEL, BOM, MAD, etc.), year range, and validity status (GOOD_LAW, OVERRULED, "
        "DISTINGUISHED, etc.). Returns up to 50 matching cases with metadata. Cost: 0.1 credits."
    ),
    "lookup_case": (
        "Get full details for a specific case by its citation. Returns comprehensive case "
        "metadata, all known citation aliases, and citation treatment statistics showing "
        "how many times the case was followed, distinguished, overruled, approved, or "
        "referred. Use after resolve_citation or search_cases_by_citation for deep case "
        "analysis. Cost: 0.1 credits."
    ),
    "get_citation_network": (
        "Traverse the citation network around a case. Returns nodes (cases) and edges "
        "(citing relationships) with treatment types (followed, distinguished, overruled). "
        "Specify direction: 'outbound' (cases this cites), 'inbound' (cases citing this), "
        "or 'both'. Set depth (1-3 hops) and limit (1-100 nodes). Useful for understanding "
        "a case's legal influence. Cost: 0.2 credits."
    ),
    "get_pricing": (
        "Get current API credit pricing. Returns per-endpoint credit costs and "
        "credit-to-currency conversion rates (1 credit = $0.10 USD = 10 INR). "
        "No authentication required. Use to check costs before making API calls."
    ),
}
