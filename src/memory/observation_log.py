"""
observation_log.py — what Helio has actually seen you do.

Noticing patterns requires a record of behaviour, and Helio never kept one:
chat_memory holds a short rolling conversation, the schedule holds what you
planned, but nothing held "you asked this, at this time, and it did that".
Without that there is nothing to find a pattern in.

Three constraints shape this file:

*Bounded.* A ring of the last MAX_ENTRIES interactions. A log that grows
forever on someone's laptop is a liability, not a feature.

*Cheap.* Appending happens on every single request, so it must never be the
slow part. One small JSON write, no model, no network.

*Erasable.* clear() exists and is reachable from the assistant by voice,
because a behaviour log you cannot delete is surveillance rather than memory.
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "memory"
LOG_PATH = DATA_DIR / "observations.json"

# Roughly a few weeks of ordinary use. Enough for weekday patterns to show up,
# small enough that the whole file loads in milliseconds.
MAX_ENTRIES = 2000

# Queries shorter than this carry no signal ("yes", "2", "next") and would
# swamp the repeat-topic detector.
MIN_QUERY_CHARS = 8


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load() -> list:
    _ensure_dir()
    if not LOG_PATH.exists():
        return []
    try:
        with LOG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(entries: list):
    _ensure_dir()
    try:
        with LOG_PATH.open("w", encoding="utf-8") as f:
            json.dump(entries[-MAX_ENTRIES:], f, ensure_ascii=False)
    except OSError:
        pass


def record(query, task_class=None, action=None, ok=True, detail=None):
    """
    Note one interaction. Called on every request, so it stays deliberately dumb.

    `action` is the tool that ran, when one did — that's what makes app-launch
    and habit patterns findable later.
    """
    text = (query or "").strip()
    if len(text) < MIN_QUERY_CHARS and not action:
        return None

    now = datetime.now()
    entry = {
        "ts": time.time(),
        "hour": now.hour,
        "weekday": now.weekday(),
        "query": text[:200],
        "task": task_class or "",
        "action": action or "",
        "ok": bool(ok),
    }
    if detail:
        entry["detail"] = str(detail)[:120]

    entries = _load()
    entries.append(entry)
    _save(entries)
    return entry


def recent(hours=None, days=None, limit=None) -> list:
    """Entries within a window, oldest first."""
    entries = _load()
    if hours or days:
        cutoff = time.time() - ((hours or 0) * 3600 + (days or 0) * 86400)
        entries = [e for e in entries if e.get("ts", 0) >= cutoff]
    return entries[-limit:] if limit else entries


def all_entries() -> list:
    return _load()


def count() -> int:
    return len(_load())


def span_days() -> float:
    """How long the log actually covers. A pattern needs history to be real."""
    entries = _load()
    if len(entries) < 2:
        return 0.0
    return (entries[-1].get("ts", 0) - entries[0].get("ts", 0)) / 86400.0


def clear() -> int:
    """Delete the behaviour log. Returns how many entries went."""
    entries = _load()
    _save([])
    try:
        if LOG_PATH.exists():
            LOG_PATH.unlink()
    except OSError:
        pass
    return len(entries)


def log_mtime() -> float:
    try:
        return LOG_PATH.stat().st_mtime
    except OSError:
        return 0.0
