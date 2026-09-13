"""
rag_tool.py — asking questions of your own documents.

search_files finds a file by its name. This finds the answer inside it, and
cites which document and which part it came from, so a claim can be checked
rather than trusted.

The answer is generated strictly from retrieved chunks. If nothing clears the
similarity floor, the tool says so instead of letting the model fill the gap
from its own weights — a confident invented answer about your own contract is
worse than no answer.
"""
from tools.tool_registry import tool
from memory import vector_store as store


def _format_sources(hits):
    seen, lines = [], []
    for hit in hits:
        marker = f"{hit['name']} (part {hit['chunk'] + 1} of {hit['total']})"
        if marker in seen:
            continue
        seen.append(marker)
        lines.append(f"  - {marker}")
    return "\n".join(lines)


@tool(
    name="study_file",
    description=(
        "Read a document into Helio's searchable memory so its contents can be "
        "questioned later, not just its filename. "
        "Use when the user says 'study this', 'learn this file', 'index my notes', "
        "or wants Helio to actually know what's inside a document. "
        "'path' is the file path. This is different from index_file, which only "
        "stores a short summary."
    ),
    parameters={"path": "string"},
    required=["path"],
)
def handle_study_file(action_data):
    path = str(action_data.get("path", "") or "").strip().strip('"')
    if not path:
        return "Which file should I study?"

    count, message = store.index_document(path)
    if not count:
        return message
    return (f"{message} You can now ask me questions about it and I'll quote "
            "the relevant part back.")


@tool(
    name="ask_documents",
    description=(
        "Answer a question using the contents of documents Helio has studied. "
        "Use whenever the user asks about something that would be inside their own "
        "files — 'what did the contract say about payment terms', 'what were the "
        "action items in my notes', 'find where it mentions the deadline'. "
        "'question' is what they want to know. Optional 'path' narrows it to one "
        "document."
    ),
    parameters={"question": "string", "path": "string"},
    required=["question"],
)
def handle_ask_documents(action_data):
    question = str(action_data.get("question", "") or "").strip()
    path = str(action_data.get("path", "") or "").strip() or None

    if not question:
        return "What would you like to know from your documents?"

    if store.chunk_count() == 0:
        return ("I haven't studied any documents yet. Say 'study <file>' or use "
                "the STUDY action in the Files planet, and then ask me again.")

    hits = store.search(question, top_k=5, path=path)
    if not hits:
        where = f" in {path}" if path else ""
        return (f"I couldn't find anything about that{where} in the documents "
                "I've studied.")

    # Only the retrieved text goes to the model. Nothing else is in scope.
    passages = "\n\n".join(
        f"[{i + 1}] From {h['name']}, part {h['chunk'] + 1}:\n{h['text']}"
        for i, h in enumerate(hits))

    prompt = (
        "Answer the question using ONLY the passages below, which come from the "
        "user's own documents.\n\n"
        "Rules:\n"
        "- If the passages do not contain the answer, say 'That isn't in the "
        "documents I've studied.' Do not use outside knowledge.\n"
        "- Quote or closely paraphrase the relevant wording.\n"
        "- Be direct. Two or three sentences unless more is genuinely needed.\n"
        "- Plain text only.\n\n"
        f"Passages:\n{passages}\n\n"
        f"Question: {question}\n"
        "Answer:"
    )

    try:
        from core.llm import generate
        answer = generate(prompt, role="chat") or ""
    except Exception as e:
        answer = f"LLM Error: {e}"

    if answer.startswith("LLM Error:"):
        # The retrieval still worked — hand back what was found rather than
        # nothing, since the passages are the valuable half.
        top = hits[0]
        return ("I found the relevant passage but couldn't summarise it "
                f"(the model is unreachable). From {top['name']}:\n\n{top['text'][:600]}")

    return f"{answer}\n\nFrom:\n{_format_sources(hits)}"


@tool(
    name="list_studied_documents",
    description=(
        "List the documents Helio has read into searchable memory, with how many "
        "pieces each was split into. Use for 'what documents have you studied', "
        "'what files do you know'."
    ),
    parameters={},
    required=[],
)
def handle_list_studied(action_data):
    docs = store.indexed_documents()
    if not docs:
        return "I haven't studied any documents yet."

    lines = [f"  {i + 1}. {d['name']} — {d['chunks']} chunks"
             for i, d in enumerate(docs)]
    total = store.chunk_count()
    return ("I've studied {} document{} ({} searchable pieces):\n{}".format(
        len(docs), "" if len(docs) == 1 else "s", total, "\n".join(lines)))


@tool(
    name="forget_studied_document",
    description=(
        "Remove a document from Helio's searchable memory. Only forgets what Helio "
        "learned — never touches the file itself. 'path' is the file path or part "
        "of its name."
    ),
    parameters={"path": "string"},
    required=["path"],
)
def handle_forget_studied(action_data):
    wanted = str(action_data.get("path", "") or "").strip().lower()
    if not wanted:
        return "Which document should I forget?"

    match = None
    for doc in store.indexed_documents():
        if wanted in doc["path"].lower() or wanted in doc["name"].lower():
            match = doc
            break
    if match is None:
        return f"I haven't studied anything matching '{wanted}'."

    removed = store.forget_document(match["path"])
    return f"Forgot {match['name']} — {removed} chunks removed. The file is untouched."
