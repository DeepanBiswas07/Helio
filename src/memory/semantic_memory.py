import json
import time
import uuid
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


def save_memory(fact: str, category: str = None, use_model: bool = True):
    """
    Save a fact, retiring whatever it makes untrue.

    Storage moved to memory_facts, which keeps records rather than strings so a
    fact can carry an expiry and a history. The old fuzzy duplicate check only
    caught restatements that shared most of their words; "my internship ended"
    shares almost nothing with "my internship runs until 10 June", so both used
    to survive and Helio believed two contradictory things.

    Returns (record, replaced) for callers that want to report the change.
    """
    if not fact or not isinstance(fact, str):
        return None, []

    from memory import memory_facts

    category = category if category in CATEGORIES else classify_category(fact)
    return memory_facts.remember(fact, category, use_model=use_model)


def save_memory_text(fact: str, category: str = None):
    """Old signature, for callers that only care that it was stored."""
    save_memory(fact, category)


def get_memory_records(category: str = None) -> list:
    """Full records — text plus expiry, history and status."""
    from memory import memory_facts

    if category:
        return memory_facts.active(category)
    out = []
    for cat in CATEGORIES:
        out.extend(memory_facts.active(cat))
    return out


def forget_memory(needle: str, category: str = None):
    """Delete a stored fact. Returns the records removed."""
    from memory import memory_facts

    categories = [category] if category in CATEGORIES else list(CATEGORIES)
    removed = []
    for cat in categories:
        removed.extend(memory_facts.forget(cat, needle))
    return removed


def sweep_expired_memories():
    """Retire facts whose stated end date has passed. Returns what changed."""
    from memory import memory_facts

    changed = []
    for cat in CATEGORIES:
        changed.extend(memory_facts.sweep_expired(cat))
    return changed


def memory_history(needle: str = "", category: str = None) -> list:
    """What a fact used to say, and when it changed."""
    from memory import memory_facts

    categories = [category] if category in CATEGORIES else list(CATEGORIES)
    out = []
    for cat in categories:
        out.extend(memory_facts.history_of(cat, needle))
    return out


def get_memories(category: str = None) -> list:
    """
    Active memories as plain strings — the shape every existing caller expects.

    Superseded and expired records are filtered out here, which is the whole
    point: a fact that stopped being true should not reach a prompt.
    """
    return [r["text"] for r in get_memory_records(category)]


def retrieve_memories(query: str = None, top_k: int = 8, category: str = None) -> list:
    """
    The memories relevant to a question, best first.

    This used to return *everything* whenever the store held fewer than top_k
    entries, which is not retrieval — it's a dump, and it pushes the actual
    question further from the model's attention. It now ranks semantically, so
    "what do I do for work" reaches "the user is interning at Acme" without
    sharing a single word with it.
    """
    from memory import memory_facts

    categories = [category] if category in CATEGORIES else list(CATEGORIES)
    hits = []
    for cat in categories:
        hits.extend(memory_facts.recall(query, cat, top_k=top_k))

    if not query:
        return [r["text"] for r in hits][:top_k]

    scores = memory_facts._similarities(query, hits) if hits else []
    ranked = sorted(zip(scores, hits), key=lambda pair: pair[0], reverse=True)
    return [record["text"] for _score, record in ranked][:top_k]


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

    Run history (created / last_run / run_count) is preserved across edits.
    """
    if not name or not steps:
        return

    workflows = _load_workflows()
    key = name.lower().strip()
    existing = workflows.get(key) or {}

    workflows[key] = {
        "steps": [s.strip() for s in steps if isinstance(s, str) and s.strip()],
        "purpose": purpose.strip(),
        "created": existing.get("created") or time.time(),
        "last_run": existing.get("last_run"),
        "run_count": existing.get("run_count", 0),
    }
    _save_workflows(workflows)


def touch_workflow(name: str):
    """Record that a workflow just ran. Safe on workflows saved before metadata existed."""
    workflows = _load_workflows()
    key = name.lower().strip()
    entry = workflows.get(key)
    if not entry:
        return

    entry["last_run"] = time.time()
    entry["run_count"] = entry.get("run_count", 0) + 1
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


# ── Reminders ─────────────────────────────────────────────────────────────────
# Reminders are stored as a list in data/memory/reminders.json:
# [
#   {
#     "id": "3f2a1c",
#     "message": "check the oven",
#     "due_ts": 1770000000.0,
#     "created_ts": 1769999000.0
#   }
# ]
# They live on disk rather than in memory so a pending reminder survives a
# restart — the UI's ReminderService re-reads this file on every tick.

_REMINDERS_PATH = DATA_DIR / "reminders.json"


def _load_reminders() -> list:
    _ensure_data_dir()
    if not _REMINDERS_PATH.exists():
        return []
    try:
        with _REMINDERS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save_reminders(reminders: list):
    _ensure_data_dir()
    try:
        with _REMINDERS_PATH.open("w", encoding="utf-8") as f:
            json.dump(reminders, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def add_reminder(message: str, due_ts: float) -> dict:
    """Store a reminder due at an absolute epoch timestamp. Returns the record."""
    record = {
        "id": uuid.uuid4().hex[:6],
        "message": (message or "").strip(),
        "due_ts": float(due_ts),
        "created_ts": time.time(),
    }
    reminders = _load_reminders()
    reminders.append(record)
    reminders.sort(key=lambda r: r.get("due_ts", 0))
    _save_reminders(reminders)
    return record


def list_reminders() -> list:
    """All pending reminders, soonest first."""
    return sorted(_load_reminders(), key=lambda r: r.get("due_ts", 0))


def remove_reminder(reminder_id: str) -> bool:
    """Delete one reminder by id. Returns True if it existed."""
    reminders = _load_reminders()
    remaining = [r for r in reminders if r.get("id") != reminder_id]
    if len(remaining) == len(reminders):
        return False
    _save_reminders(remaining)
    return True


def pop_due_reminders(now: Optional[float] = None) -> list:
    """
    Return every reminder due at or before `now` and remove them from the store
    in one pass, so a reminder can never fire twice even if two ticks overlap.
    """
    now = time.time() if now is None else now
    reminders = _load_reminders()
    due = [r for r in reminders if r.get("due_ts", 0) <= now]
    if due:
        pending = [r for r in reminders if r.get("due_ts", 0) > now]
        _save_reminders(pending)
    return due


def reminders_mtime() -> float:
    """Modification time of the reminder store, or 0 if it doesn't exist yet."""
    try:
        return _REMINDERS_PATH.stat().st_mtime
    except OSError:
        return 0.0
