"""
pattern_miner.py — finding the patterns worth remembering.

Most of this is deliberately deterministic. Counting how often two applications
open together, or how often a scheduled thing gets skipped, needs arithmetic
rather than a language model, and arithmetic doesn't hallucinate a habit you
don't have. The model is used for exactly one job the counting can't do:
reading conversation for preferences you stated in passing and never asked to
be saved.

The rules that keep this honest:

*Evidence before belief.* Every detector has a minimum number of separate days
or occurrences. Two coincidences are not a pattern, and a memory full of
maybes is worse than an empty one.

*Nothing sensitive is inferred.* The detectors work on which tool ran and when.
The one model pass is scoped to preferences about working with Helio, and is
told to skip anything personal, medical, financial or about other people.

*Everything is attributable.* Each inference carries the evidence that produced
it, so you can read why Helio believes it and disagree.
"""
import re
import time
from collections import Counter, defaultdict
from datetime import datetime

from memory import observation_log as log
from memory import observed_memory as observed

# Nothing is inferred at all until there is a few days of behaviour to look at.
MIN_LOG_ENTRIES = 25
MIN_LOG_DAYS = 2.0

# Two apps count as "opened together" within this many minutes.
PAIR_WINDOW_MINUTES = 12
MIN_PAIR_DAYS = 3           # on this many separate days before it's a habit

MIN_HABIT_OCCURRENCES = 4   # for a time-of-day rhythm
MIN_REPEAT_TOPIC = 3        # same question asked this often
MIN_SKIPS = 3               # scheduled thing skipped/missed this often

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")


def _day_key(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _part_of_day(hour):
    if hour < 12:
        return "mornings"
    if hour < 17:
        return "afternoons"
    if hour < 22:
        return "evenings"
    return "late at night"


def _ready(entries):
    return (len(entries) >= MIN_LOG_ENTRIES
            and log.span_days() >= MIN_LOG_DAYS)


# ── detectors ────────────────────────────────────────────────────────────────

def _app_pairs(entries):
    """Applications opened close together, on several separate days."""
    launches = [e for e in entries if e.get("action") == "open_app"]
    if len(launches) < 4:
        return []

    def app_of(entry):
        text = (entry.get("detail") or entry.get("query") or "").lower()
        match = re.search(r"\b(?:open|launch|start)\s+(.+)$", text)
        name = (match.group(1) if match else text).strip()
        return re.sub(r"[^a-z0-9 .+-]", "", name)[:28].strip()

    pairs = defaultdict(set)     # (a, b) -> {day, day}
    for i, first in enumerate(launches):
        for second in launches[i + 1:]:
            gap = second["ts"] - first["ts"]
            if gap > PAIR_WINDOW_MINUTES * 60:
                break
            a, b = app_of(first), app_of(second)
            if not a or not b or a == b:
                continue
            pairs[tuple(sorted((a, b)))].add(_day_key(first["ts"]))

    found = []
    for (a, b), days in pairs.items():
        if len(days) < MIN_PAIR_DAYS:
            continue
        confidence = min(0.9, 0.45 + 0.1 * len(days))
        found.append({
            "fact": f"You usually open {a} and {b} together.",
            "kind": "routine",
            "evidence": "seen on {} separate days, most recently {}".format(
                len(days), max(days)),
            "confidence": confidence,
            "source": "app-pairing",
            "key": f"pair:{a}|{b}",
            "suggest_assembly": [f"open {a}", f"open {b}"],
        })
    return found


def _time_habits(entries):
    """A tool used repeatedly around the same part of the day."""
    buckets = defaultdict(lambda: defaultdict(set))
    for entry in entries:
        action = entry.get("action")
        if not action or action in ("get_datetime",):
            continue
        buckets[action][_part_of_day(entry.get("hour", 12))].add(
            _day_key(entry["ts"]))

    readable = {
        "list_schedule": "check your schedule",
        "get_system_info": "check how the machine is doing",
        "search_and_read": "look things up",
        "ask_documents": "ask about your documents",
        "describe_screen": "have me read your screen",
        "set_reminder": "set reminders",
        "run_workflow": "run a saved routine",
        "search_files": "search for files",
    }

    found = []
    for action, parts in buckets.items():
        phrase = readable.get(action)
        if not phrase:
            continue

        # Only the strongest part of the day, not every bucket that cleared the
        # bar. Activity at 16:00 and 17:00 straddles the afternoon/evening line
        # and would otherwise produce two near-identical memories for one habit.
        part, days = max(parts.items(), key=lambda kv: len(kv[1]))
        if len(days) < MIN_HABIT_OCCURRENCES:
            continue

        found.append({
            "fact": f"You tend to {phrase} in the {part}.",
            "kind": "rhythm",
            "evidence": f"{len(days)} separate days",
            "confidence": min(0.85, 0.45 + 0.08 * len(days)),
            "source": "time-of-day",
            # Keyed on the action alone, so if the habit drifts to a different
            # part of the day it updates in place instead of forking.
            "key": f"habit:{action}",
        })
    return found


def _repeat_topics(entries):
    """The same question, asked again and again — worth remembering as an interest."""
    def normalise(text):
        text = re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())
        words = [w for w in text.split()
                 if len(w) > 3 and w not in
                 ("what", "when", "where", "which", "about", "there", "your",
                  "have", "does", "with", "that", "this", "from", "tell")]
        return " ".join(sorted(set(words))[:4])

    counts = Counter()
    examples = {}
    for entry in entries:
        query = entry.get("query", "")
        if len(query) < 15:
            continue
        signature = normalise(query)
        if len(signature.split()) < 2:
            continue
        counts[signature] += 1
        examples.setdefault(signature, query)

    found = []
    for signature, hits in counts.items():
        if hits < MIN_REPEAT_TOPIC:
            continue
        found.append({
            "fact": 'You come back to this often: "{}".'.format(
                examples[signature][:90]),
            "kind": "interest",
            "evidence": f"asked {hits} times",
            "confidence": min(0.8, 0.45 + 0.08 * hits),
            "source": "repeat-topic",
            "key": f"topic:{signature}",
        })
    return found


def _schedule_avoidance():
    """Something on the schedule that keeps getting skipped or walked past."""
    try:
        from memory import schedule_store as store
    except Exception:
        return []

    found = []
    for event in store.list_events():
        skipped = len(event.get("skipped_dates", []))
        done = len(event.get("done_dates", []))
        if skipped < MIN_SKIPS or skipped <= done:
            continue
        title = event.get("title", "").strip()
        if not title:
            continue
        found.append({
            "fact": f"You often don't get to '{title}'.",
            "kind": "avoidance",
            "evidence": f"skipped {skipped} times, done {done}",
            "confidence": min(0.85, 0.5 + 0.07 * skipped),
            "source": "schedule-skips",
            "key": f"skip:{event.get('id')}",
        })
    return found


def _active_hours(entries):
    """When this person actually uses the machine — shapes when to speak."""
    if len(entries) < 40:
        return []
    hours = Counter(e.get("hour", 12) for e in entries)
    busiest = [h for h, _c in hours.most_common(4)]
    if not busiest:
        return []
    span = f"{min(busiest):02d}:00–{max(busiest) + 1:02d}:00"
    return [{
        "fact": f"You're usually at the machine between {span}.",
        "kind": "rhythm",
        "evidence": f"{len(entries)} interactions",
        "confidence": 0.6,
        "source": "active-hours",
        "key": "active-hours",
    }]


# ── the one model pass ───────────────────────────────────────────────────────

def _stated_preferences(entries):
    """
    Preferences you mentioned in passing and never asked to be saved.

    This is the only detector that needs a model: "keep it short" and "I hate
    when you explain everything twice" are preferences, but no counter finds
    them. Scoped tightly to how you want Helio to work — it is told to skip
    anything personal, medical, financial, or about other people.
    """
    queries = [e.get("query", "") for e in entries if len(e.get("query", "")) > 20]
    if len(queries) < 8:
        return []

    sample = "\n".join(f"- {q}" for q in queries[-40:])
    prompt = (
        "Below are things a user said to their desktop assistant.\n\n"
        "Find preferences about HOW THEY WANT THE ASSISTANT TO WORK that they "
        "stated in passing and never explicitly asked to be saved.\n\n"
        "Strict rules:\n"
        "- Only preferences about working with the assistant: response length, "
        "tone, what to do automatically, what to stop doing.\n"
        "- SKIP anything personal, medical, financial, or about other people.\n"
        "- SKIP anything that is a one-off instruction rather than a standing "
        "preference.\n"
        "- If you are not confident, output nothing. Silence is correct here.\n"
        "- At most 2 lines. One per line, in this exact format:\n"
        "<the preference, written as 'You prefer ...'> | <the phrase that showed it>\n"
        "- Output nothing at all if there are none.\n\n"
        f"What they said:\n{sample}\n\nPreferences:"
    )

    try:
        from core.llm import generate
        raw = generate(prompt, role="memory") or ""
    except Exception:
        return []

    if raw.startswith("LLM Error:"):
        return []

    found = []
    for line in raw.splitlines():
        if "|" not in line:
            continue
        fact, _sep, evidence = line.partition("|")
        fact = fact.strip().lstrip("-•* ").strip()
        if not fact.lower().startswith("you ") or len(fact) < 12:
            continue
        found.append({
            "fact": fact[:180],
            "kind": "preference",
            "evidence": "you said: " + evidence.strip()[:80],
            "confidence": 0.55,
            "source": "stated-in-passing",
            "key": "pref:" + re.sub(r"[^a-z]", "", fact.lower())[:40],
        })
    return found[:2]


# ── the pass ─────────────────────────────────────────────────────────────────

def mine(use_model=True, now=None):
    """
    Run every detector and record what clears its threshold.

    Returns the list of records actually written or strengthened. Safe to call
    repeatedly — note() deduplicates by pattern key, so a habit seen again
    gains confidence instead of producing a second line.
    """
    entries = log.recent(days=30)
    if not _ready(entries):
        return []

    candidates = []
    for detector in (_app_pairs, _time_habits, _repeat_topics, _active_hours):
        try:
            candidates.extend(detector(entries))
        except Exception as e:
            print(f"[Patterns] {detector.__name__} failed: {e}")

    try:
        candidates.extend(_schedule_avoidance())
    except Exception as e:
        print(f"[Patterns] schedule check failed: {e}")

    if use_model:
        try:
            candidates.extend(_stated_preferences(entries))
        except Exception as e:
            print(f"[Patterns] preference reading failed: {e}")

    written = []
    for candidate in candidates:
        record = observed.note(
            candidate["fact"], kind=candidate.get("kind", "habit"),
            evidence=candidate.get("evidence", ""),
            confidence=candidate.get("confidence", 0.5),
            source=candidate.get("source", ""), key=candidate.get("key"))
        if record:
            record = dict(record)
            if candidate.get("suggest_assembly"):
                record["suggest_assembly"] = candidate["suggest_assembly"]
            written.append(record)
    return written


def readiness():
    """How close the log is to being worth mining. Used by the UI and the tool."""
    entries = log.all_entries()
    return {
        "entries": len(entries),
        "needed_entries": MIN_LOG_ENTRIES,
        "days": round(log.span_days(), 1),
        "needed_days": MIN_LOG_DAYS,
        "ready": _ready(entries),
    }
