from tools.tool_registry import tool
from memory.semantic_memory import save_conversation, get_recent_conversations, search_conversations


@tool(
    name="pin_conversation",
    description=(
        "Save a summary of an important conversation or discussion to long-term memory. "
        "Use this when the user says something like 'remember this conversation', "
        "'save this discussion', or 'this is important'. "
        "'title' is a short topic label (e.g. 'career goals discussion'). "
        "'summary' is a 1-3 sentence recap of what was discussed."
    ),
    parameters={
        "title": "string",
        "summary": "string",
    },
    required=["title", "summary"]
)
def handle_pin_conversation(action_data):
    title = action_data.get("title", "").strip()
    summary = action_data.get("summary", "").strip()

    if not title:
        return "Please provide a title for this conversation."
    if not summary:
        return "Please provide a summary of what was discussed."

    save_conversation(title, summary)
    return f"Pinned to conversation memory: '{title}'"


@tool(
    name="recall_conversations",
    description=(
        "Look up PAST conversations that were explicitly pinned to long-term memory. "
        "Use this ONLY when the user asks about a prior session they saved, e.g. "
        "'do you remember when we discussed X', or 'recall our conversation about Y'. "
        "Do NOT use this for questions about the current session like 'what did I just say', "
        "'what was my last message', or 'what did we talk about just now' — those are answered "
        "from live conversation context, not this tool. "
        "Optionally provide a 'query' to search for a specific topic."
    ),
    parameters={"query": "string"},
    required=[]
)
def handle_recall_conversations(action_data):
    query = action_data.get("query", "").strip()

    if query:
        results = search_conversations(query)
    else:
        results = get_recent_conversations(n=5)

    if not results:
        return "No relevant conversations found in memory."

    lines = []
    for c in results:
        ts = c.get("timestamp", "")
        title = c.get("title", "Untitled")
        summary = c.get("summary", "")
        ts_str = f" [{ts}]" if ts else ""
        lines.append(f"• {title}{ts_str}\n  {summary}")

    return "Past Conversations:\n\n" + "\n\n".join(lines)
