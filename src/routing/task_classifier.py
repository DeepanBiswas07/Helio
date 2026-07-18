import re
import os
from tools.file_ops.file_search import extract_search_scope, strip_search_scope

# Task Classes
SIMPLE_ACTION = "SIMPLE_ACTION"
FILE_ACTION = "FILE_ACTION"
SEARCH_ACTION = "SEARCH_ACTION"
FILE_SEARCH_ACTION = "FILE_SEARCH_ACTION"
COMPLEX_TASK = "COMPLEX_TASK"
UNKNOWN = "UNKNOWN"

# Keywords that strongly indicate a LOCAL file search request
_FILE_SEARCH_TRIGGERS = [
    r"\bsearch\b.{0,40}\b(?:file|drive|pc|computer|disk|folder|document|my)\b",
    r"\bfind\b.{0,40}\b(?:file|drive|pc|computer|disk|folder|document|my)\b",
    r"\b[a-z]\s*drive\b",
    r"\bsearch\s+(?:for\s+)?(?:my\s+)?(?:resume|cv|document|file|pdf|report|notes|presentation)\b",
    r"\bfind\s+(?:my\s+)?(?:resume|cv|document|file|pdf|report|notes|presentation)\b",
]

# Tier 2 Heuristics: Keywords that indicate complex intent
COMPLEX_KEYWORDS = [
    "summarize", "compare", "analyze", "explain", "extract",
    "and then", "after that", "read and", "open and", "search and"
]


def classify_task(query):
    query_lower = query.lower().strip()

    # ---------------------------------------------------------
    # Tier 0: Deterministic local file search detection
    # Must happen BEFORE the COMPLEX_TASK check to avoid being
    # swallowed by the ' and ' heuristic.
    # ---------------------------------------------------------
    for pattern in _FILE_SEARCH_TRIGGERS:
        if re.search(pattern, query_lower):
            root = extract_search_scope(query_lower)
            clean = strip_search_scope(query_lower)
            # Strip common conversational filler from the search query
            clean = re.sub(
                r"\b(?:search|find|look\s+for|look|for|on\s+pc|on\s+my\s+pc|on\s+computer|on\s+the\s+pc|my|then|tell\s+me|from\s+it|from\s+the\s+file|and|it|now|college\s+name|college|name|from\s+it|get|give\s+me|show|please|can\s+you|could\s+you|in\s+the|in)\b",
                " ", clean, flags=re.IGNORECASE
            )
            clean = re.sub(r"\s+", " ", clean).strip()
            # Remove leftover single-char tokens
            clean = " ".join(w for w in clean.split() if len(w) > 1)
            if clean:
                return FILE_SEARCH_ACTION, {"query": clean, "root": root}

    # ---------------------------------------------------------
    # Tier 2: Heuristic Analysis (Check for complex workflows)
    # ---------------------------------------------------------
    if " and " in query_lower or any(kw in query_lower for kw in COMPLEX_KEYWORDS):
        return COMPLEX_TASK, None

    # ---------------------------------------------------------
    # Tier 1: Deterministic Regex Matching (Fast Path)
    # ---------------------------------------------------------
    
    # A. Specific web searches with 'in/on' keywords (must be checked before simple open)
    # 1. YouTube search (e.g. "open AI videos in YouTube" or "open AI videos on YouTube")
    match = re.match(r"^(?:open|search|search for|find)\s+(.+?)\s+(?:in|on)\s+youtube\.?$", query_lower)
    if match:
        return SEARCH_ACTION, {"action": "youtube", "query": match.group(1).strip()}

    # 2. Google / Web search (e.g. "open AI videos in Google" or "open AI videos in browser")
    match = re.match(r"^(?:open|search|search for|find)\s+(.+?)\s+(?:in|on)\s+(?:google|browser|chrome|edge|web)\.?$", query_lower)
    if match:
        return SEARCH_ACTION, {"action": "web", "query": match.group(1).strip()}

    # 1. SIMPLE_ACTION: open/launch [app name]
    # Negative lookahead ensures we don't match file requests like "open the file"
    match = re.match(r"^(?:open|launch|start)\s+(?!the file\b|my file\b|file\b)(.+)$", query_lower)
    if match:
        return SIMPLE_ACTION, {"app": match.group(1).strip()}
        
    # 2. FILE_ACTION: next / more
    if query_lower in ("next", "more"):
        return FILE_ACTION, {"action": "next"}
        
    # 3. FILE_ACTION: open / read [index]
    match = re.match(r"^(open|read)\s+(\d+)$", query_lower)
    if match:
        return FILE_ACTION, {"action": match.group(1), "index": int(match.group(2))}

    # 4. SEARCH_ACTION: youtube search
    match = re.match(r"^(?:youtube|search youtube for)\s+(.+)$", query_lower)
    if match:
        return SEARCH_ACTION, {"action": "youtube", "query": match.group(1).strip()}
        
    # 5. SEARCH_ACTION: web search
    match = re.match(r"^(?:google|search web for|search the web for)\s+(.+)$", query_lower)
    if match:
        return SEARCH_ACTION, {"action": "web", "query": match.group(1).strip()}

    # ---------------------------------------------------------
    # Tier 3: Unknown -> Fallback to Slow Path LLM
    # ---------------------------------------------------------
    return UNKNOWN, None
