"""
situation.py — what is true right now.

Every planet keeps its own store: facts and preferences in semantic_memory,
assemblies in the workflow store, commitments in schedule_store, countdowns in
the reminder store, documents in file knowledge. Until now none of that reached
the model — the chat prompt carried the conversation and nothing else, which is
why Helio could confidently describe reminders that didn't exist.

This module is the single place those stores are read together and rendered as
a compact block the model can be grounded on. It is also, in practice, what
connects the planets: a routine built in SETUP is visible when you talk in
CHAT, a birthday added by voice shows up when you ask what's coming, and a
preference stored in MEMORY colours every answer.

Deliberately small: every section is capped, and the whole thing is skipped
rather than half-built if a store is unavailable, because a prompt that grows
with the user's data eventually costs more than it helps.
"""
from datetime import datetime

# Caps, so a busy week can't crowd out the actual question.
MAX_FACTS = 8
MAX_SCHEDULE = 6
MAX_REMINDERS = 4
MAX_ASSEMBLIES = 6
MAX_DOCUMENTS = 4
MAX_OBSERVED = 5


def _safe(fn, default):
    """Any store may be missing or mid-write; never take the prompt down with it."""
    try:
        return fn()
    except Exception:
        return default


def _clock(moment):
    return moment.strftime("%I:%M %p").lstrip("0").lower()


def _get_day_phase(now):
    hour = now.hour
    if 4 <= hour < 12:
        return "Morning", "Start of day / morning work session"
    elif 12 <= hour < 17:
        return "Afternoon", "Midday / afternoon session"
    elif 17 <= hour < 21:
        return "Evening", "Post-work / evening relaxation & dinner"
    else:
        return "Late Night / Night", "Night-time / late hours, past typical working schedule"


def _temporal_block(now):
    phase_name, phase_desc = _get_day_phase(now)
    time_str = now.strftime("%I:%M %p").lstrip("0")
    date_str = now.strftime("%A, %B %d, %Y")
    return [
        f"- Current Time: {time_str} ({now.strftime('%H:%M')} 24h format)",
        f"- Phase of Day: {phase_name} ({phase_desc})",
        f"- Day of the Week: {now.strftime('%A')}",
        f"- Calendar Date: {date_str}",
    ]


def _facts_block():
    from memory.semantic_memory import get_memories

    lines = []
    for category in ("facts", "preferences"):
        entries = _safe(lambda c=category: get_memories(c), [])
        for entry in entries[:MAX_FACTS]:
            text = entry.get("fact") if isinstance(entry, dict) else str(entry)
            if text:
                lines.append(f"- {text}")
    return lines[:MAX_FACTS]


def _schedule_block(now):
    from memory import schedule_store as store

    lines = []
    today = _safe(lambda: store.day_agenda(now.date()), [])
    for occurrence in today[:MAX_SCHEDULE]:
        mark = {"done": "done", "skipped": "skipped"}.get(occurrence["status"], "")
        when = ("all day" if occurrence["duration_min"] <= 0
                else _clock(occurrence["start"]))
        suffix = f" ({mark})" if mark else ""
        lines.append(f"- {when}: {occurrence['title']}{suffix}")

    ahead = _safe(lambda: store.upcoming(now, within_days=7, limit=3), [])
    ahead = [o for o in ahead if o["start"].date() != now.date()]
    for occurrence in ahead:
        lines.append("- {}: {}".format(
            occurrence["start"].strftime("%a %d %b"), occurrence["title"]))

    free = _safe(lambda: store.current_free_block(now), None)
    if free:
        minutes = int((free[1] - now).total_seconds() // 60)
        lines.append(f"- free right now for about {minutes} minutes")

    missed = _safe(lambda: store.missed_today(now), [])
    if missed:
        lines.append("- walked past today: " +
                     ", ".join(o["title"] for o in missed[:3]))
    return lines


def _reminders_block(now):
    from memory.semantic_memory import list_reminders

    lines = []
    for record in _safe(list_reminders, [])[:MAX_REMINDERS]:
        due = datetime.fromtimestamp(record.get("due_ts", 0))
        minutes = int((due - now).total_seconds() // 60)
        when = f"in {minutes} min" if 0 <= minutes < 180 else _clock(due)
        lines.append(f"- {record.get('message', '')} ({when})")
    return lines


def _assemblies_block():
    from memory.semantic_memory import list_workflows

    lines = []
    for name, data in list(_safe(list_workflows, {}).items())[:MAX_ASSEMBLIES]:
        steps = data.get("steps", [])
        purpose = data.get("purpose", "")
        detail = f" — {purpose}" if purpose else ""
        lines.append(f"- '{name}' ({len(steps)} steps){detail}")
    return lines


def _documents_block():
    from memory.semantic_memory import list_indexed_files

    lines = []
    for record in _safe(list_indexed_files, [])[:MAX_DOCUMENTS]:
        name = record.get("filename") or record.get("path", "")
        summary = (record.get("summary") or "").strip().replace("\n", " ")
        if len(summary) > 90:
            summary = summary[:87] + "…"
        lines.append(f"- {name}: {summary}" if summary else f"- {name}")
    return lines


def _observed_block():
    """
    Inferences Helio drew on its own, kept clearly separate from stated facts.

    The label matters: the model must not present a guess as something the
    user told it, or an inference becomes indistinguishable from a fact and
    there is no way for the user to catch a wrong one.
    """
    from memory.observed_memory import list_observations

    lines = []
    for record in _safe(lambda: list_observations(min_confidence=0.55), [])[:MAX_OBSERVED]:
        confidence = int(record.get("confidence", 0) * 100)
        lines.append("- {} (inferred, {}% confident)".format(
            record.get("fact", ""), confidence))
    return lines


def _made_block():
    """
    The last few things Helio built, as lines.

    "Put the sourdough picture in the sourdough website" only means something
    if both can be resolved to a file. Without this the planner could see that
    artefacts existed but never which one was meant, so asked to combine two
    of them it listed the inventory instead of doing the job.
    """
    from pathlib import Path as _Path

    lines = []
    try:
        from tools.system.forge_tool import list_forge_history
        for record in list_forge_history()[:6]:
            path = record.get("file_path", "")
            name = _Path(path).name if path else ""
            lines.append("- {} [{}]{}".format(
                record.get("title", "untitled"),
                record.get("type", "build"),
                f" -> {name}" if name else ""))
    except Exception:
        pass

    try:
        from tools.system.forge_tool import _picture_shelf
        for picture in _picture_shelf()[:5]:
            lines.append(f"- picture: {picture.name}")
    except Exception:
        pass

    return lines


def _surface_block():
    """
    What is open on the workshop right now.

    Without this, "the picture on screen" is a phrase Helio cannot resolve —
    it can put things on the surface but has no idea what is there.
    """
    from pathlib import Path as _Path

    lines = []
    try:
        from tools.system import workshop_tool
        if not workshop_tool.is_open():
            return []
        words = {"image": "picture", "web": "page", "code": "source",
                 "chart": "chart", "document": "document", "text": "note"}
        for item in workshop_tool.on_surface():
            what = words.get(item.get("kind"), item.get("kind", "thing"))
            name = _Path(str(item.get("path", ""))).name
            lines.append("- {}: {}{}".format(
                what, item.get("title", "untitled"),
                f"  ({name})" if name else ""))
    except Exception:
        return []
    return lines


def build_situation(now=None, include=None):
    """
    Render the live state as a compact block, or "" when nothing is known.

    `include` optionally narrows which sections are read — the router doesn't
    need the document list, and a fast path shouldn't pay for stores it won't
    use.
    """
    now = now or datetime.now()
    wanted = set(include or
                 ("time", "facts", "schedule", "reminders", "assemblies",
                  "documents", "observed", "made", "surface"))
    sections = []

    if "time" in wanted:
        sections.append(("REAL-TIME TEMPORAL CONTEXT (Time of day, weekday & date)",
                         _temporal_block(now)))
    if "facts" in wanted:
        sections.append(("What you know about the user", _safe(_facts_block, [])))
    if "schedule" in wanted:
        sections.append(("Their schedule", _safe(lambda: _schedule_block(now), [])))
    if "reminders" in wanted:
        sections.append(("Pending reminders", _safe(lambda: _reminders_block(now), [])))
    if "assemblies" in wanted:
        sections.append(("Routines they've built (you can run these by name)",
                         _safe(_assemblies_block, [])))
    if "documents" in wanted:
        sections.append(("Documents you've read", _safe(_documents_block, [])))
    if "observed" in wanted:
        sections.append((
            "Patterns you have NOTICED, not been told — treat these as guesses, "
            "say so if you use one, and never state them as fact",
            _safe(_observed_block, [])))
    if "surface" in wanted:
        sections.append((
            "Open on the WORKSHOP right now — this is what the user means by "
            "'on screen', 'that picture', 'the one you opened'",
            _safe(_surface_block, [])))
    if "made" in wanted:
        sections.append((
            "Things you have MADE recently, newest first — when the user says "
            "'the page', 'that picture' or 'the site you built', this is what "
            "they mean; use the exact filename",
            _safe(_made_block, [])))

    rendered = []
    for title, lines in sections:
        if lines:
            rendered.append(f"{title}:\n" + "\n".join(lines))

    if not rendered:
        return ""

    return (
        "CURRENT STATE — this is real, read from Helio's own stores. "
        "Answer from it, and never invent entries that aren't listed here. "
        "If something isn't in this block, say you don't have it.\n\n"
        + "\n\n".join(rendered)
    )
