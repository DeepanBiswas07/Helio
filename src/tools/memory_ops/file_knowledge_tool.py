from tools.tool_registry import tool
from memory.semantic_memory import (
    index_file_knowledge,
    get_file_knowledge,
    search_file_knowledge,
    list_indexed_files,
    forget_file_knowledge,
)


@tool(
    name="index_file",
    description=(
        "Index a file's content into long-term knowledge memory with a summary and topic tags. "
        "Use this after reading a document so Helio remembers what it contains. "
        "'path' is the full file path. "
        "'summary' is a 2-4 sentence description of what the file is about. "
        "'topics' is a comma-separated list of keywords (e.g. 'resume, career, skills')."
    ),
    parameters={
        "path": "string",
        "summary": "string",
        "topics": "string",
    },
    required=["path", "summary"]
)
def handle_index_file(action_data):
    path = action_data.get("path", "").strip()
    summary = action_data.get("summary", "").strip()
    raw_topics = action_data.get("topics", "")

    if not path:
        return "Please provide the file path to index."
    if not summary:
        return "Please provide a summary of the file content."

    topics = [t.strip() for t in raw_topics.split(",") if t.strip()] if raw_topics else []

    index_file_knowledge(path, summary, topics)

    import os
    filename = os.path.basename(path)
    topic_str = f" | Topics: {', '.join(topics)}" if topics else ""
    return f"Indexed '{filename}' into file knowledge memory.{topic_str}"


@tool(
    name="recall_file_knowledge",
    description=(
        "Search Helio's file knowledge memory for documents related to a topic or query. "
        "Use this when the user asks 'do you remember that file about X', "
        "'what files do you know about Y', or 'find documents related to Z'."
    ),
    parameters={"query": "string"},
    required=["query"]
)
def handle_recall_file_knowledge(action_data):
    query = action_data.get("query", "").strip()
    if not query:
        return "Please provide a topic or query to search file knowledge."

    results = search_file_knowledge(query)
    if not results:
        return f"No indexed files found related to '{query}'."

    lines = []
    for entry in results:
        filename = entry.get("filename", "Unknown")
        path = entry.get("path", "")
        summary = entry.get("summary", "")
        topics = entry.get("topics", [])
        indexed_at = entry.get("indexed_at", "")
        topic_str = f" [{', '.join(topics)}]" if topics else ""
        date_str = f" — indexed {indexed_at}" if indexed_at else ""
        lines.append(f"• {filename}{topic_str}{date_str}\n  {summary}\n  Path: {path}")

    return "Indexed Files:\n\n" + "\n\n".join(lines)


@tool(
    name="list_indexed_files",
    description="List all files that Helio has indexed into its knowledge memory.",
    parameters={},
    required=[]
)
def handle_list_indexed_files(action_data):
    entries = list_indexed_files()
    if not entries:
        return "No files indexed yet. Ask me to read a document and I'll remember it."

    lines = []
    for entry in entries:
        filename = entry.get("filename", "Unknown")
        topics = entry.get("topics", [])
        indexed_at = entry.get("indexed_at", "")
        topic_str = f" [{', '.join(topics)}]" if topics else ""
        date_str = f" — {indexed_at}" if indexed_at else ""
        lines.append(f"• {filename}{topic_str}{date_str}")

    return f"Indexed Files ({len(entries)} total):\n\n" + "\n".join(lines)


@tool(
    name="forget_file",
    description="Remove a file from Helio's knowledge memory index by its path or filename.",
    parameters={"path": "string"},
    required=["path"]
)
def handle_forget_file(action_data):
    path = action_data.get("path", "").strip()
    if not path:
        return "Please provide the file path to remove."

    # Try exact path first, then fuzzy match on filename
    removed = forget_file_knowledge(path)
    if removed:
        import os
        return f"Removed '{os.path.basename(path)}' from file knowledge memory."

    # Fuzzy fallback: search by filename
    results = search_file_knowledge(path, top_k=1)
    if results:
        exact_path = results[0].get("path", "")
        if exact_path and forget_file_knowledge(exact_path):
            return f"Removed '{results[0].get('filename', path)}' from file knowledge memory."

    return f"No indexed file found matching '{path}'."
