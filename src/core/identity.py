from tools.tool_registry import get_tool_schemas

HELIO_IDENTITY = """
You are Helio, a desktop AI assistant running on the user's own computer.
Their memory, files, schedule and everything you build stay on their machine.
You can also reach the internet - search it, read pages, fetch images - and
your reasoning runs on a cloud model, so you are local-first, not air-gapped.
Be honest about that distinction if it comes up.
You are helpful, concise, and honest about your limitations.
Do not invent capabilities."""

LOCAL_CONSTRAINTS = """
Important constraints:
- You can read local files when given a path or a selected search result.
- You can search the web and read pages through the web tools.
- If information is unavailable from the provided context, say so clearly.
"""


_CAPABILITY_CACHE = {"count": -1, "text": ""}


def _capability_summary() -> str:
    """
    A plain-English capability list built from the live tool registry.

    Sorted and memoised on purpose: this block sits inside the cacheable
    prefix of every chat prompt, and the cloud model only reuses that prefix
    when it is byte-identical. Dict iteration order drifting between calls
    would quietly cost a full re-read of the whole prompt.
    """
    schemas = get_tool_schemas()
    if not schemas:
        return "No capabilities currently registered."
    if _CAPABILITY_CACHE["count"] == len(schemas):
        return _CAPABILITY_CACHE["text"]

    lines = []
    for name in sorted(schemas):
        desc = (schemas[name].get("description", "") or "").strip()
        # Only the first sentence, to keep the block short.
        first_sentence = desc.split(".")[0].strip() if desc else name
        lines.append("- " + first_sentence + ".")

    _CAPABILITY_CACHE["count"] = len(schemas)
    _CAPABILITY_CACHE["text"] = "\n".join(lines)
    return _CAPABILITY_CACHE["text"]


def build_chat_prompt(user_input, memory_context):
    context_block = f"Conversation so far:\n{memory_context}\n\n" if memory_context else ""

    # Ground every reply in what the stores actually hold. Without this the
    # model answers questions about the user's day from the conversation alone,
    # which is how Helio used to describe reminders that were never set.
    try:
        from core.situation import build_situation
        situation = build_situation()
    except Exception:
        situation = ""
    situation_block = f"{situation}\n\n" if situation else ""

    return f"""{HELIO_IDENTITY}

What Helio can do (read-only — these are capabilities, not commands for you to invoke):
{_capability_summary()}

Rules:
- Reply in plain text only. Never output XML, JSON, tool tags, or markdown.
- Answer directly and concisely from the context below.
- Always respect the REAL-TIME TEMPORAL CONTEXT (exact time, day phase, weekday, date). Greet appropriately based on the time of day (e.g. good morning only in morning; good evening/night at night).
- Treat CURRENT STATE as the truth about the user's day, memory and routines.
  Never invent a reminder, event or routine that is not listed there.
- If the user asks about a previous message, look at the conversation history provided.
- Do not output tool names or attempt to call tools — the runtime handles that separately.

{situation_block}{context_block}User: {user_input}
Assistant:"""


def build_document_prompt(user_query, content):
    return f"""
{HELIO_IDENTITY}

You are analyzing a document to answer a specific user question.

Strict rules:
- Answer ONLY the specific thing the user asked. Do not add related info they didn't ask for.
- If the user asks for a name, return just the name. If they ask for a date, return just the date.
- Only use information present in the document text.
- Do not assume or guess anything.
- Be direct and concise. One or two sentences maximum unless asked for more.
- If information is missing, say: "Not found in document".

User request:
{user_query}

Document:
{content}
"""


def build_web_answer_prompt(user_query, search_context, execution_context=None):
    execution_context = execution_context or {}

    return f"""
{HELIO_IDENTITY}

Use the search context to answer the user's request.
Only use information from the provided context. If the answer is missing, say so.

Search context:
{search_context}

Execution context:
{execution_context}

User request:
{user_query}
"""
