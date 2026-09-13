"""
memory_facts.py — memory that changes when your life changes.

The old store was a list of strings with a fuzzy duplicate check. That handles
a restatement ("my meeting is at 5" → "at 6" overlap heavily, so the second
replaced the first) and fails everything else:

    "my internship runs until 10 June"  →  "my internship ended"

share almost no tokens, so both survived and Helio believed two contradictory
things at once. Nothing ever expired either — a fact true in June was still
being asserted in September.

Three mechanisms fix that, and each earns its place:

*Records, not strings.* A fact now carries when it was learned, when it stops
being true, what it replaced, and whether it is still active. History is kept
because an update can be wrong, and a memory system you can't rewind is one
you stop trusting.

*Embeddings decide what a new fact is about.* This is where retrieval genuinely
helps the memory layer, not just documents: cosine similarity puts "internship
ended" next to "internship runs until June" where token overlap does not. The
same MiniLM already loaded for documents is reused, so this costs no extra
model.

*A model arbitrates the middle.* High similarity is a clear replacement, low is
a clear separate fact, and in between similarity alone cannot tell "I like tea"
/ "I like coffee" (both true) from "meeting at 5" / "meeting at 6" (one
replaces the other). Only that band asks the model, so the common cases stay
free.
"""
import json
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "memory"

# Cosine bands for deciding what a newly stated fact does to what's stored.
SUPERSEDE_AT = 0.80     # same subject, new value — replace outright
ARBITRATE_AT = 0.58     # close enough to be worth one model call
RECALL_FLOOR = 0.28     # below this a memory isn't relevant to the question

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_EXPIRED = "expired"


# ── storage ──────────────────────────────────────────────────────────────────

def _path(category):
    return DATA_DIR / f"{category}.json"


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _as_record(entry):
    """
    Accept either shape. Existing installs hold plain strings; wrapping them on
    read means nobody has to migrate a file by hand and nothing is lost if an
    older build writes to the same store.
    """
    if isinstance(entry, dict):
        entry.setdefault("id", uuid.uuid4().hex[:6])
        entry.setdefault("status", STATUS_ACTIVE)
        entry.setdefault("created_ts", entry.get("updated_ts") or time.time())
        return entry
    return {
        "id": uuid.uuid4().hex[:6],
        "text": str(entry),
        "status": STATUS_ACTIVE,
        "created_ts": time.time(),
        "updated_ts": time.time(),
        "valid_until": None,
        "history": [],
    }


def load(category) -> list:
    _ensure_dir()
    path = _path(category)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [_as_record(e) for e in data]


def save(category, records):
    _ensure_dir()
    try:
        with _path(category).open("w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def active(category) -> list:
    return [r for r in load(category) if r.get("status") == STATUS_ACTIVE]


# ── temporal bounds ──────────────────────────────────────────────────────────

# "until 10 June", "till the 3rd", "ends on Friday", "through August"
_UNTIL = re.compile(
    r"\b(?:until|till|til|up to|through|ends?\s+(?:on|in)?|finishes?\s+(?:on|in)?|"
    r"expires?\s+(?:on|in)?|due)\s+(.{3,40}?)(?:[.,;]|$)", re.I)

# "for the next 3 months", "for two weeks"
_FOR_SPAN = re.compile(
    r"\bfor\s+(?:the\s+next\s+)?(\d+|a|an|two|three|four|five|six)\s+"
    r"(day|days|week|weeks|month|months|year|years)\b", re.I)

_SPAN_WORDS = {"a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_SPAN_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


def parse_validity(text, now=None):
    """
    When does this fact stop being true?

    Returns (valid_until_timestamp, matched_phrase) or (None, ""). Only
    explicit bounds count — guessing an expiry for an unbounded statement would
    quietly delete things the user still believes.
    """
    now = now or datetime.now()
    text = text or ""

    match = _FOR_SPAN.search(text)
    if match:
        count = match.group(1).lower()
        count = _SPAN_WORDS.get(count, None) or (int(count) if count.isdigit() else None)
        unit = match.group(2).lower().rstrip("s")
        if count and unit in _SPAN_DAYS:
            end = now + timedelta(days=count * _SPAN_DAYS[unit])
            return end.timestamp(), match.group(0)

    match = _UNTIL.search(text)
    if match:
        phrase = match.group(1).strip()
        try:
            from tools.time_ops.when_parser import parse_datetime
            parsed = parse_datetime(phrase, now=now, default_hour=23)
        except Exception:
            parsed = None
        if parsed:
            return parsed[0].timestamp(), match.group(0)

    return None, ""


def _past_tense(text, phrase):
    """
    Rewrite a lapsed fact so it reads as history rather than a live claim.

    "My internship runs until 10 June" becoming "My internship ended on
    10 June" matters: leaving the original wording would have Helio still
    asserting a present-tense thing that stopped being true.
    """
    cleaned = re.sub(re.escape(phrase), "", text, flags=re.I).strip(" ,.;")
    cleaned = re.sub(r"\s{2,}", " ", cleaned)

    swaps = [
        (r"\b(runs|goes|lasts|continues)\b", "ran"),
        (r"\bis\s+ongoing\b", "has ended"),
        (r"\b(am|is|are)\s+(doing|working|studying|interning)\b", "was \\2"),
        (r"\b(have|has)\b", "had"),
        (r"\bis\b", "was"),
        (r"\bare\b", "were"),
    ]
    for pattern, replacement in swaps:
        if re.search(pattern, cleaned, re.I):
            cleaned = re.sub(pattern, replacement, cleaned, count=1, flags=re.I)
            break

    return cleaned


# ── similarity ───────────────────────────────────────────────────────────────

def _embed(texts):
    """Reuse the document model rather than loading a second one."""
    from memory.vector_store import _embed as embed
    return embed(list(texts))


def _similarities(text, records):
    """Cosine of `text` against each record. Falls back to fuzzy if unavailable."""
    if not records:
        return []
    try:
        import numpy as np
        vectors = _embed([r["text"] for r in records])
        query = _embed([text])[0]
        return list(np.asarray(vectors @ query, dtype=float))
    except Exception:
        try:
            from rapidfuzz import fuzz
            return [fuzz.token_set_ratio(text, r["text"]) / 100.0 for r in records]
        except ImportError:
            return [0.0] * len(records)


def _arbitrate(new_text, old_text):
    """
    Does the new statement replace the old one, or sit beside it?

    Asked only in the ambiguous band. Biased toward SEPARATE: wrongly keeping
    two facts is a cluttered memory, wrongly replacing one silently destroys
    something the user told Helio.
    """
    prompt = (
        "A user previously told their assistant one thing, and has now said "
        "another. Decide whether the new statement REPLACES the old one "
        "(same subject, changed value or now finished) or whether both remain "
        "true side by side.\n\n"
        f"Previously: {old_text}\n"
        f"Now: {new_text}\n\n"
        "Answer with exactly one word:\n"
        "REPLACES — if the new statement makes the old one no longer true.\n"
        "SEPARATE — if both can be true at once, or you are unsure."
    )
    try:
        from core.llm import generate
        verdict = (generate(prompt, role="memory") or "").strip().upper()
    except Exception:
        return False
    return verdict.startswith("REPLACES")


# ── the write path ───────────────────────────────────────────────────────────

def remember(text, category, now=None, use_model=True):
    """
    Store a fact, replacing whatever it makes untrue.

    Returns (record, replaced_records).
    """
    text = (text or "").strip()
    if not text:
        return None, []

    now = now or datetime.now()
    records = load(category)
    live = [r for r in records if r.get("status") == STATUS_ACTIVE]

    # Exact restatement — nothing to do but touch it.
    for record in live:
        if record["text"].strip().lower() == text.lower():
            record["updated_ts"] = time.time()
            save(category, records)
            return record, []

    replaced = []
    if live:
        scores = _similarities(text, live)
        ranked = sorted(zip(scores, live), key=lambda pair: pair[0], reverse=True)
        for score, candidate in ranked[:3]:
            if score >= SUPERSEDE_AT:
                replaced.append(candidate)
            elif score >= ARBITRATE_AT and use_model:
                if _arbitrate(text, candidate["text"]):
                    replaced.append(candidate)
            # Only the top candidate is worth arbitrating; below that the
            # subject has drifted too far to be the same fact.
            break

    valid_until, phrase = parse_validity(text, now)

    record = {
        "id": uuid.uuid4().hex[:6],
        "text": text,
        "status": STATUS_ACTIVE,
        "created_ts": time.time(),
        "updated_ts": time.time(),
        "valid_until": valid_until,
        "validity_phrase": phrase,
        "history": [],
    }

    for old in replaced:
        old["status"] = STATUS_SUPERSEDED
        old["superseded_by"] = record["id"]
        old["superseded_ts"] = time.time()
        record["history"].append({"text": old["text"], "until": time.time()})

    records.append(record)
    save(category, records)
    return record, replaced


def retire_contradicted(text, categories=("facts",), threshold=0.72, now=None):
    """
    Retire remembered facts that something stored elsewhere has just made stale.

    The stores can contradict each other. Telling Helio "my meeting is at 5"
    saves a fact; later saying "my meeting is at 6" creates a *schedule event*,
    which is the right home for it — but the 5pm fact stays behind and is still
    fed to every prompt. This is the same supersede check, applied across the
    boundary: whatever wrote the new truth calls it with what it stored.

    Returns the records retired.
    """
    text = (text or "").strip()
    if not text:
        return []

    retired = []
    for category in categories:
        records = load(category)
        live = [r for r in records if r.get("status") == STATUS_ACTIVE]
        if not live:
            continue

        scores = _similarities(text, live)
        touched = False
        for score, record in zip(scores, live):
            if score < threshold:
                continue
            record["status"] = STATUS_SUPERSEDED
            record["superseded_ts"] = time.time()
            record["superseded_note"] = f"replaced by: {text[:120]}"
            retired.append(record)
            touched = True

        if touched:
            save(category, records)
    return retired


def forget(category, needle):
    """Delete a fact outright. Returns the removed records."""
    records = load(category)
    needle = (needle or "").strip().lower()
    if not needle:
        return []

    keep, removed = [], []
    for record in records:
        if needle in record["text"].lower():
            removed.append(record)
        else:
            keep.append(record)

    if not removed:
        # Fall back to the closest match rather than failing on a paraphrase.
        live = [r for r in records if r.get("status") == STATUS_ACTIVE]
        if live:
            scores = _similarities(needle, live)
            best = max(zip(scores, live), key=lambda pair: pair[0])
            if best[0] >= 0.62:
                removed = [best[1]]
                keep = [r for r in records if r is not best[1]]

    if removed:
        save(category, keep)
    return removed


def sweep_expired(category, now=None):
    """
    Retire facts whose stated end date has passed, rewriting them as history.

    Returns the records that changed. Called on a slow timer, so a fact that
    lapses overnight is already past-tense by morning.
    """
    now = now or datetime.now()
    cutoff = now.timestamp()
    records = load(category)
    changed = []

    for record in records:
        if record.get("status") != STATUS_ACTIVE:
            continue
        until = record.get("valid_until")
        if not until or until > cutoff:
            continue

        original = record["text"]
        record["status"] = STATUS_EXPIRED
        record["expired_ts"] = time.time()

        rewritten = _past_tense(original, record.get("validity_phrase", ""))
        ended = datetime.fromtimestamp(until).strftime("%d %B")
        replacement = {
            "id": uuid.uuid4().hex[:6],
            "text": f"{rewritten} (ended {ended})",
            "status": STATUS_ACTIVE,
            "created_ts": time.time(),
            "updated_ts": time.time(),
            "valid_until": None,
            "history": [{"text": original, "until": until}],
            "from_expiry_of": record["id"],
        }
        records.append(replacement)
        changed.append(replacement)

    if changed:
        save(category, records)
    return changed


# ── the read path ────────────────────────────────────────────────────────────

def recall(query, category, top_k=8, floor=RECALL_FLOOR):
    """
    The memories relevant to a question, best first.

    The old implementation returned *everything* when there were fewer than 30
    memories, which is not retrieval — it's a dump that pushes the actual
    question further from the model's attention.
    """
    live = active(category)
    if not live:
        return []
    if not query:
        return live[:top_k]

    scores = _similarities(query, live)
    ranked = sorted(zip(scores, live), key=lambda pair: pair[0], reverse=True)
    hits = [record for score, record in ranked if score >= floor]
    return hits[:top_k] or [ranked[0][1]]


def history_of(category, needle):
    """What a fact used to say. The reason keeping history is worth the bytes."""
    needle = (needle or "").strip().lower()
    out = []
    for record in load(category):
        if needle and needle not in record["text"].lower():
            continue
        for past in record.get("history", []):
            out.append({
                "now": record["text"],
                "was": past.get("text", ""),
                "changed": past.get("until"),
                "status": record.get("status"),
            })
    return out
