"""
observed_memory.py — things Helio worked out about you, kept apart from things
you told it.

The separation is the whole point. semantic_memory holds statements you chose
to store; this holds inferences Helio drew on its own. Mixing them would make
a wrong guess indistinguishable from a fact you stated, and there would be no
honest way to show you what it had decided about you.

Every record therefore carries its evidence, how many times it has been seen,
and where it came from — so an inference can be read, argued with, dismissed,
or promoted into real memory once you agree with it.

Stored in data/memory/observed.json:

  [
    {
      "id": "8f21c0",
      "fact": "You usually open VS Code and Chrome together on weekday mornings.",
      "kind": "habit",
      "evidence": "seen on 4 separate days, most recently Fri 05 Sep",
      "times_seen": 4,
      "confidence": 0.72,
      "source": "app-pairing",
      "first_seen": 1770000000.0,
      "last_seen": 1770400000.0,
      "status": "open"
    }
  ]
"""
import json
import time
import uuid
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "memory"
OBSERVED_PATH = DATA_DIR / "observed.json"

KINDS = ("habit", "preference", "routine", "avoidance", "interest", "rhythm")

# Below this an inference is a coincidence, not a pattern. Nothing weaker is
# stored at all — a memory full of maybes is worse than an empty one.
MIN_CONFIDENCE = 0.45


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load() -> list:
    _ensure_dir()
    if not OBSERVED_PATH.exists():
        return []
    try:
        with OBSERVED_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(records: list):
    _ensure_dir()
    try:
        with OBSERVED_PATH.open("w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def observed_mtime() -> float:
    try:
        return OBSERVED_PATH.stat().st_mtime
    except OSError:
        return 0.0


def note(fact, kind="habit", evidence="", confidence=0.5, source="", key=None):
    """
    Record an inference, or strengthen one already held.

    `key` is a stable identifier for the *pattern*, not the sentence — so
    seeing the same habit again next week bumps its count and confidence
    instead of writing a near-duplicate line.

    Returns the record, or None if it was too weak to keep or the user has
    already dismissed it.
    """
    fact = (fact or "").strip()
    if not fact or confidence < MIN_CONFIDENCE:
        return None

    key = key or fact.lower()[:80]
    records = _load()

    for record in records:
        if record.get("key") != key:
            continue
        # Dismissed means the user disagreed. Re-noticing the same thing must
        # not resurrect it, or "no" doesn't mean anything.
        if record.get("status") == "dismissed":
            return None
        record["times_seen"] = record.get("times_seen", 1) + 1
        record["last_seen"] = time.time()
        record["fact"] = fact
        if evidence:
            record["evidence"] = evidence
        # Repeated observation raises confidence, with a ceiling — an inference
        # never becomes a certainty just by being seen again.
        record["confidence"] = min(0.95, max(record.get("confidence", 0.5),
                                             confidence) + 0.05)
        _save(records)
        return record

    now = time.time()
    record = {
        "id": uuid.uuid4().hex[:6],
        "key": key,
        "fact": fact,
        "kind": kind if kind in KINDS else "habit",
        "evidence": evidence,
        "times_seen": 1,
        "confidence": round(float(confidence), 2),
        "source": source,
        "first_seen": now,
        "last_seen": now,
        "status": "open",
    }
    records.append(record)
    _save(records)
    return record


def list_observations(include_dismissed=False, min_confidence=0.0) -> list:
    """Everything inferred, strongest first."""
    records = _load()
    if not include_dismissed:
        records = [r for r in records if r.get("status") != "dismissed"]
    records = [r for r in records if r.get("confidence", 0) >= min_confidence]
    return sorted(records,
                  key=lambda r: (r.get("confidence", 0), r.get("times_seen", 0)),
                  reverse=True)


def get(observation_id):
    for record in _load():
        if record.get("id") == observation_id:
            return record
    return None


def dismiss(observation_id) -> bool:
    """Mark an inference wrong. It stays on file so it isn't re-learned."""
    records = _load()
    for record in records:
        if record.get("id") == observation_id:
            record["status"] = "dismissed"
            record["dismissed_ts"] = time.time()
            _save(records)
            return True
    return False


def forget(observation_id) -> bool:
    """Delete outright. Unlike dismiss, this allows it to be noticed again."""
    records = _load()
    remaining = [r for r in records if r.get("id") != observation_id]
    if len(remaining) == len(records):
        return False
    _save(remaining)
    return True


def confirm(observation_id):
    """
    Agree with an inference: promote it into real memory and mark it settled.

    Once you've confirmed it, it stops being a guess — so it moves into
    semantic_memory where the rest of what you told Helio lives.
    """
    record = get(observation_id)
    if not record:
        return None

    try:
        from memory.semantic_memory import save_memory
        category = "preferences" if record["kind"] in ("preference", "avoidance") \
            else "workflows"
        save_memory(record["fact"], category)
    except Exception:
        pass

    records = _load()
    for candidate in records:
        if candidate.get("id") == observation_id:
            candidate["status"] = "confirmed"
            candidate["confidence"] = 1.0
            candidate["confirmed_ts"] = time.time()
            _save(records)
            return candidate
    return None


def clear_all() -> int:
    records = _load()
    _save([])
    return len(records)
