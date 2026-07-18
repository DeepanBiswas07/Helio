import json
from pathlib import Path
from typing import Optional
from rapidfuzz import process, fuzz

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "memory"

# ── Category definitions ──────────────────────────────────────────────────────
CATEGORIES = {
    "facts":       "Factual personal information about the user (name, age, location, job, etc.).",
    "preferences": "User preferences, likes/dislikes, and style choices.",
    "workflows":   "Repeated tasks, habits, or multi-step sequences the user performs.",
    "execution":   "Short-term execution history, last commands, and runtime context.",
}

DEFAULT_CATEGORY = "facts"
DEDUP_THRESHOLD = 82  # % similarity above which a fact is considered a duplicate

# Keywords that hint at which category a fact belongs to
_CATEGORY_HINTS = {
    "preferences": [
        "prefer", "like", "love", "hate", "dislike", "always use", "favourite",
        "favorite", "rather", "instead", "enjoy", "want", "wish", "don't like",
    ],
    "workflows": [
        "usually", "always open", "every time", "first", "then", "after that",
        "workflow", "routine", "sequence", "start by", "habit", "step",
    ],
    "execution": [
        "last ran", "executed", "ran", "result was", "output", "command", "tool",
        "just did", "recently", "session",
    ],
}


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _category_path(category: str) -> Path:
    return DATA_DIR / f"{category}.json"


def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_category(category: str) -> list:
    _ensure_data_dir()
    path = _category_path(category)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_category(category: str, entries: list):
    _ensure_data_dir()
    try:
        with _category_path(category).open("w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


# ── Category classification ───────────────────────────────────────────────────

def classify_category(fact: str) -> str:
    """Guess which category a fact belongs to based on keyword hints."""
    lower = fact.lower()
    for category, hints in _CATEGORY_HINTS.items():
        if any(hint in lower for hint in hints):
            return category
    return DEFAULT_CATEGORY


# ── Public API ────────────────────────────────────────────────────────────────

def _find_duplicate(fact: str, entries: list):
    """
    Returns the index of an existing entry that is semantically close enough
    to 'fact' to be considered a duplicate, or -1 if none found.
    """
    if not entries:
        return -1

    results = process.extract(
        fact,
        entries,
        scorer=fuzz.token_set_ratio,
        limit=1,
    )
    if results:
        _match, score, idx = results[0]
        if score >= DEDUP_THRESHOLD:
            return idx
    return -1


def save_memory(fact: str, category: str = None):
    """
    Save a fact to the appropriate memory category.
    If a fuzzy-similar fact already exists it is replaced (updated) rather
    than duplicated. If category is not given it is auto-classified.
    """
    if not fact or not isinstance(fact, str):
        return

    category = category if category in CATEGORIES else classify_category(fact)
    entries = _load_category(category)

    # Exact match — already stored, nothing to do
    if fact in entries:
        return

    # Fuzzy match — update existing entry instead of appending
    dup_idx = _find_duplicate(fact, entries)
    if dup_idx >= 0:
        entries[dup_idx] = fact
    else:
        entries.append(fact)

    _save_category(category, entries)


def get_memories(category: str = None) -> list:
    """
    Return all memories for a specific category, or ALL memories flattened
    if category is None.
    """
    if category:
        return _load_category(category)

    all_memories = []
    for cat in CATEGORIES:
        all_memories.extend(_load_category(cat))
    return all_memories


def retrieve_memories(query: str = None, top_k: int = 30, category: str = None) -> list:
    """
    Retrieve the most relevant memories for a query.
    Searches a specific category or all categories if none given.
    """
    memories = get_memories(category)
    if not memories:
        return []

    if len(memories) <= top_k:
        return memories

    # Fuzzy fallback for large stores
    results = process.extract(
        query or "",
        memories,
        scorer=fuzz.token_set_ratio,
        limit=top_k,
    )
    return [match for match, score, _ in results if score >= 20]


# ── Conversation Memory ───────────────────────────────────────────────────────
# Stores summaries of important past discussions.
# Schema: list of { "title": str, "summary": str, "timestamp": str }

_CONVERSATIONS_PATH = DATA_DIR / "conversations.json"


def _load_conversations() -> list:
    _ensure_data_dir()
    if not _CONVERSATIONS_PATH.exists():
        return []
    try:
        with _CONVERSATIONS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_conversations(convs: list):
    _ensure_data_dir()
    try:
        with _CONVERSATIONS_PATH.open("w", encoding="utf-8") as f:
            json.dump(convs, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def save_conversation(title: str, summary: str):
    """
    Pin an important conversation summary to long-term memory.
    title  — short label (e.g. 'AI internship discussion')
    summary — 1-3 sentence recap of what was discussed
    """
    if not title or not summary:
        return

    from datetime import datetime
    convs = _load_conversations()

    # Avoid near-duplicate titles
    existing_titles = [c.get("title", "") for c in convs]
    results = process.extract(
        title, existing_titles, scorer=fuzz.token_set_ratio, limit=1
    ) if existing_titles else []

    entry = {
        "title": title.strip(),
        "summary": summary.strip(),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    if results and results[0][1] >= 85:
        # Update existing entry with same topic
        idx = results[0][2]
        convs[idx] = entry
    else:
        convs.append(entry)

    # Keep only the last 100 conversations
    _save_conversations(convs[-100:])


def get_recent_conversations(n: int = 5) -> list:
    """Return the N most recent conversation summaries."""
    return _load_conversations()[-n:]


def search_conversations(query: str, top_k: int = 5) -> list:
    """Return conversation entries whose title or summary matches the query."""
    convs = _load_conversations()
    if not convs:
        return []

    # Search over combined title+summary text
    searchable = [f"{c.get('title', '')} {c.get('summary', '')}" for c in convs]
    results = process.extract(
        query, searchable, scorer=fuzz.token_set_ratio, limit=top_k
    )
    return [convs[idx] for _, score, idx in results if score >= 25]



# ── File Knowledge Memory ─────────────────────────────────────────────────────
# Stores indexed summaries of files the user has read or asked about.
# Schema: { path: { "filename", "summary", "topics": [], "indexed_at" } }

_FILE_KNOWLEDGE_PATH = DATA_DIR / "file_knowledge.json"


def _load_file_knowledge() -> dict:
    _ensure_data_dir()
    if not _FILE_KNOWLEDGE_PATH.exists():
        return {}
    try:
        with _FILE_KNOWLEDGE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_file_knowledge(store: dict):
    _ensure_data_dir()
    try:
        with _FILE_KNOWLEDGE_PATH.open("w", encoding="utf-8") as f:
            json.dump(store, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def index_file_knowledge(path: str, summary: str, topics: list = None):
    """
    Index a file by its path, storing a summary and optional topic tags.
    Calling again with the same path overwrites the previous entry.
    """
    if not path or not summary:
        return

    from datetime import datetime
    import os

    store = _load_file_knowledge()
    store[path] = {
        "filename": os.path.basename(path),
        "path": path,
        "summary": summary.strip(),
        "topics": [t.strip() for t in (topics or []) if t.strip()],
        "indexed_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _save_file_knowledge(store)


def get_file_knowledge(path: str) -> Optional[dict]:
    """Return the indexed knowledge for a specific file path."""
    return _load_file_knowledge().get(path)


def search_file_knowledge(query: str, top_k: int = 5) -> list:
    """
    Search indexed files by filename, summary, or topics.
    Returns a list of matching file knowledge dicts.
    """
    store = _load_file_knowledge()
    if not store:
        return []

    entries = list(store.values())
    # Build searchable text: filename + topics + summary snippet
    searchable = [
        f"{e.get('filename', '')} {' '.join(e.get('topics', []))} {e.get('summary', '')[:200]}"
        for e in entries
    ]

    results = process.extract(
        query, searchable, scorer=fuzz.token_set_ratio, limit=top_k
    )
    return [entries[idx] for _, score, idx in results if score >= 30]


def list_indexed_files() -> list:
    """Return all indexed file entries."""
    return list(_load_file_knowledge().values())


def forget_file_knowledge(path: str) -> bool:
    """Remove a file from the knowledge index. Returns True if it existed."""
    store = _load_file_knowledge()
    if path in store:
        del store[path]
        _save_file_knowledge(store)
        return True
    return False


# ── Workflow Memory ───────────────────────────────────────────────────────────


# Workflows are stored as a dict in data/memory/workflows.json:
# {
#   "work setup": {
#     "steps": ["open vscode", "open terminal", "run server"],
#     "purpose": "faster task execution"
#   }
# }

_WORKFLOWS_PATH = DATA_DIR / "workflows.json"


def _load_workflows() -> dict:
    _ensure_data_dir()
    if not _WORKFLOWS_PATH.exists():
        return {}
    try:
        with _WORKFLOWS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_workflows(workflows: dict):
    _ensure_data_dir()
    try:
        with _WORKFLOWS_PATH.open("w", encoding="utf-8") as f:
            json.dump(workflows, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def save_workflow(name: str, steps: list, purpose: str = ""):
    """
    Save or overwrite a named workflow.
    steps: list of action strings e.g. ["open vscode", "open terminal"]
    purpose: optional description of why this workflow exists
    """
    if not name or not steps:
        return

    workflows = _load_workflows()
    workflows[name.lower().strip()] = {
        "steps": [s.strip() for s in steps if isinstance(s, str) and s.strip()],
        "purpose": purpose.strip(),
    }
    _save_workflows(workflows)


def get_workflow(name: str) -> Optional[dict]:
    """Return a workflow dict {steps, purpose} by name, or None if not found."""
    workflows = _load_workflows()
    return workflows.get(name.lower().strip())


def list_workflows() -> dict:
    """Return all stored workflows."""
    return _load_workflows()


def delete_workflow(name: str) -> bool:
    """Delete a workflow by name. Returns True if it existed."""
    workflows = _load_workflows()
    key = name.lower().strip()
    if key in workflows:
        del workflows[key]
        _save_workflows(workflows)
        return True
    return False


def migrate_legacy(legacy_path: Path = None):

    """
    One-time migration: read the old flat semantic_memory.json and distribute
    its entries into the new per-category files, then rename the old file.
    """
    if legacy_path is None:
        legacy_path = Path(__file__).resolve().parents[2] / "data" / "semantic_memory.json"

    if not legacy_path.exists():
        return

    try:
        with legacy_path.open("r", encoding="utf-8") as f:
            old_entries = json.load(f)
    except (OSError, json.JSONDecodeError):
        return

    if not isinstance(old_entries, list):
        return

    for fact in old_entries:
        if isinstance(fact, str) and fact.strip():
            save_memory(fact)

    # Rename to signal migration is done
    legacy_path.rename(legacy_path.with_suffix(".json.migrated"))


# Run migration on first import so nothing is ever lost
migrate_legacy()
