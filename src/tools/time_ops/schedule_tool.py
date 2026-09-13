"""
Schedule tools — the calendar Helio actually acts on.

These are the voice surface of memory/schedule_store. The interesting one is
suggest_activity: it reads the free block you're standing in, what you've
already missed today, what's coming, and the assemblies you've built in SETUP,
then proposes things that fit the time you actually have. accept_suggestion
turns a proposal into a real entry, which is the loop that makes this feel like
an assistant rather than a calendar.
"""
import time
from datetime import datetime, timedelta

from tools.tool_registry import tool
from tools.time_ops.when_parser import parse_datetime
from memory import schedule_store as store

# Proposals from the last suggest_activity call, so "do the second one" works.
_PENDING_SUGGESTIONS = []

_KIND_WORDS = {
    "meeting": ("meeting", "call", "standup", "interview", "sync", "1:1", "review"),
    "birthday": ("birthday", "anniversary", "bday"),
    "workout": ("gym", "workout", "run", "yoga", "training", "exercise", "walk"),
    "study": ("study", "revise", "read", "course", "homework", "assignment", "class"),
    "personal": ("lunch", "dinner", "coffee", "date", "party", "family", "friend"),
    "break": ("break", "rest", "nap"),
}

_RECURRENCE_WORDS = (
    ("weekdays", ("every weekday", "weekdays", "every working day", "mon to fri")),
    ("daily", ("every day", "daily", "each day", "everyday")),
    ("weekly", ("every week", "weekly", "each week", "every monday", "every tuesday",
                "every wednesday", "every thursday", "every friday",
                "every saturday", "every sunday")),
    ("monthly", ("every month", "monthly")),
    ("yearly", ("every year", "yearly", "annually", "birthday", "anniversary")),
)


def _infer_kind(text):
    lowered = (text or "").lower()
    for kind, words in _KIND_WORDS.items():
        if any(word in lowered for word in words):
            return kind
    return store.DEFAULT_KIND


def _infer_recurrence(text):
    lowered = (text or "").lower()
    for recurrence, words in _RECURRENCE_WORDS:
        if any(word in lowered for word in words):
            return recurrence
    return "none"


def _clean_title(text):
    """Strip the scheduling scaffolding so the title is just the thing."""
    title = (text or "").strip().strip('"').strip()
    for prefix in ("that ", "i have ", "i've got ", "there's ", "a ", "an ", "my "):
        if title.lower().startswith(prefix):
            title = title[len(prefix):].strip()
            break
    return title


def _when(occurrence, now=None):
    """'today at 3:00 pm', 'tomorrow at 9:00 am', 'Fri 11 Sep at 3:00 pm'."""
    now = now or datetime.now()
    start = occurrence["start"]
    clock = start.strftime("%I:%M %p").lstrip("0").lower()
    days = (start.date() - now.date()).days
    if days == 0:
        stamp = "today"
    elif days == 1:
        stamp = "tomorrow"
    elif 0 < days < 7:
        stamp = start.strftime("%A")
    else:
        stamp = start.strftime("%a %d %b")
    if occurrence.get("duration_min", 0) <= 0:
        return stamp
    return f"{stamp} at {clock}"


def _render(occurrences, now=None, numbered=True):
    lines = []
    for index, o in enumerate(occurrences, 1):
        mark = {"done": "✓", "skipped": "–"}.get(o["status"], "·")
        head = f"  {index}. " if numbered else "  "
        lines.append(f"{head}{mark} {o['title']} — {_when(o, now)}")
    return "\n".join(lines)


# ── writing ──────────────────────────────────────────────────────────────────

@tool(
    name="add_event",
    description=(
        "Put something on the user's schedule: a meeting, a birthday, a workout, "
        "a class, an appointment, anything with a time. "
        "Use this whenever the user mentions a commitment — 'I have a meeting at 3', "
        "'my friend's birthday is on March 12', 'gym at 7 every weekday', "
        "'remind me about the dentist on Friday'. "
        "'what' is the thing itself (e.g. 'dentist appointment', \"Arjun's birthday\"). "
        "'when' is the time phrase exactly as the user said it "
        "(e.g. 'tomorrow at 3pm', 'March 12', 'next Friday at 9'). "
        "'repeats' is one of none/daily/weekdays/weekly/monthly/yearly — birthdays "
        "are yearly. 'duration' is in minutes if the user said how long."
    ),
    parameters={
        "what": "string", "when": "string", "kind": "string",
        "duration": "integer", "repeats": "string",
    },
    required=["what", "when"],
)
def handle_add_event(action_data):
    title = _clean_title(str(action_data.get("what", "") or ""))
    when_text = str(action_data.get("when", "") or "").strip()

    if not title:
        return "What should I put on the schedule?"

    parsed = parse_datetime(when_text) if when_text else None
    if not parsed and title:
        parsed = parse_datetime(title)
    if not parsed:
        return (f"When is '{title}'? Say something like 'tomorrow at 3pm' "
                "or 'on March 12'.")

    start, label, _had_time = parsed
    context = f"{when_text} {title}"

    kind = str(action_data.get("kind", "") or "").strip().lower()
    if kind not in store.KINDS:
        kind = _infer_kind(context)

    recurrence = str(action_data.get("repeats", "") or "").strip().lower()
    if recurrence not in store.RECURRENCES:
        recurrence = "none"
    if recurrence == "none":
        recurrence = _infer_recurrence(context)
    if kind == "birthday":
        recurrence = "yearly"

    duration = action_data.get("duration")
    try:
        duration = int(duration) if duration else None
    except (TypeError, ValueError):
        duration = None

    store.add_event(title, start.timestamp(), kind=kind,
                    duration_min=duration, recurrence=recurrence)

    # Putting this on the schedule may have just made a remembered fact stale.
    # "My meeting is at 5pm" was saved as a memory; scheduling it for 6pm is
    # the new truth, and leaving the old one active means two stores disagree
    # and both reach the prompt.
    superseded = []
    try:
        from memory import memory_facts
        superseded = memory_facts.retire_contradicted(f"{title} {label}")
    except Exception:
        pass

    repeat_note = "" if recurrence == "none" else f", repeating {recurrence}"
    message = f"Added '{title}' — {label}{repeat_note}."
    if superseded:
        message += (" I've dropped what I had before: "
                    + superseded[0]["text"] + ".")
    return message


@tool(
    name="list_schedule",
    description=(
        "Read the user's schedule back to them. "
        "Use for 'what's on today', 'what does my day look like', 'what's this week', "
        "'am I free tomorrow', 'any birthdays coming up'. "
        "'range' is today / tomorrow / week / month — default today."
    ),
    parameters={"range": "string"},
    required=[],
)
def handle_list_schedule(action_data):
    span = str(action_data.get("range", "") or "today").strip().lower()
    now = datetime.now()

    if "tomorrow" in span:
        day = (now + timedelta(days=1)).date()
        items = store.day_agenda(day)
        header = "Tomorrow"
    elif "week" in span:
        items = store.horizon(7)
        header = "This week"
    elif "month" in span:
        items = store.horizon(30)
        header = "The next 30 days"
    else:
        items = store.day_agenda(now.date())
        header = "Today"

    if not items:
        return f"{header} is clear — nothing scheduled."

    pending = [o for o in items if o["status"] == "pending"]
    body = _render(items, now)
    tail = ""
    if header == "Today" and pending:
        nxt = min(pending, key=lambda o: o["start"])
        if nxt["start"] > now:
            minutes = int((nxt["start"] - now).total_seconds() // 60)
            tail = f"\n\nNext up: {nxt['title']} in {minutes} minutes."
    return f"{header}: {len(items)} item" + ("" if len(items) == 1 else "s") + \
           f"\n{body}{tail}"


@tool(
    name="cancel_event",
    description=(
        "Remove something from the schedule permanently. "
        "'which' is part of its name (e.g. 'the dentist one') or 'all'. "
        "Use for 'cancel my 3pm', 'delete the gym thing', 'remove that meeting'."
    ),
    parameters={"which": "string"},
    required=["which"],
)
def handle_cancel_event(action_data):
    which = str(action_data.get("which", "") or "").strip().lower()
    events = store.list_events()
    if not events:
        return "Your schedule is empty."

    if which in ("all", "everything"):
        for event in events:
            store.remove_event(event["id"])
        return f"Cleared all {len(events)} scheduled items."

    target = _match_event(which, events)
    if target is None:
        return f"I couldn't find '{which}' on your schedule."

    store.remove_event(target["id"])
    return f"Removed '{target['title']}' from your schedule."


@tool(
    name="complete_event",
    description=(
        "Mark something on today's schedule as done, or as skipped. "
        "Use for 'I went to the gym', 'done with the standup', "
        "'I skipped my run today'. 'which' is part of its name; "
        "set 'skipped' to true if they missed it rather than did it."
    ),
    parameters={"which": "string", "skipped": "string"},
    required=["which"],
)
def handle_complete_event(action_data):
    which = str(action_data.get("which", "") or "").strip().lower()
    skipped = str(action_data.get("skipped", "") or "").strip().lower() in (
        "true", "yes", "1", "skipped")

    today = store.day_agenda()
    if not today:
        return "Nothing on today's schedule."

    target = _match_occurrence(which, today)
    if target is None:
        return f"I couldn't find '{which}' on today's schedule."

    if skipped:
        store.mark_skipped(target["id"], target["start"].date())
        return f"Noted — '{target['title']}' skipped today."
    store.mark_done(target["id"], target["start"].date())
    return f"Nice — '{target['title']}' marked done."


def _match_event(which, events):
    """Fuzzy-match a saved event by name, the way run_workflow matches routines."""
    if not which:
        return None
    exact = [e for e in events if which in e.get("title", "").lower()]
    if exact:
        return exact[0]
    try:
        from rapidfuzz import process, fuzz
    except ImportError:
        return None
    titles = {e.get("title", ""): e for e in events}
    matches = process.extract(which, titles.keys(), scorer=fuzz.token_set_ratio, limit=1)
    if matches and matches[0][1] >= 60:
        return titles[matches[0][0]]
    return None


def _match_occurrence(which, items):
    if not which:
        return None
    hits = [o for o in items if which in o["title"].lower()]
    if hits:
        return hits[0]
    try:
        from rapidfuzz import process, fuzz
    except ImportError:
        return None
    titles = {o["title"]: o for o in items}
    matches = process.extract(which, titles.keys(), scorer=fuzz.token_set_ratio, limit=1)
    if matches and matches[0][1] >= 60:
        return titles[matches[0][0]]
    return None


# ── the assistant part ───────────────────────────────────────────────────────

def _assemblies():
    """The routines built in SETUP — things Helio can actually run for you."""
    try:
        from memory.semantic_memory import list_workflows
        return list_workflows()
    except Exception:
        return {}


def build_suggestion_context(now=None):
    """Everything a suggestion should be grounded in. Also used by the UI."""
    now = now or datetime.now()
    block = store.current_free_block(now)
    missed = store.missed_today(now)
    ahead = store.upcoming(now, within_days=1, limit=4)

    if block:
        free_start, free_end = block
        minutes = int((free_end - now).total_seconds() // 60)
    else:
        free_start, free_end, minutes = None, None, 0

    return {
        "now": now,
        "free_until": free_end,
        "free_minutes": minutes,
        "missed": missed,
        "upcoming": ahead,
        "assemblies": _assemblies(),
    }


def _fallback_suggestions(context):
    """
    Concrete proposals without a model.

    Used when the LLM is unreachable, and as the seed the model is asked to
    improve on — so a suggestion is never an empty list.
    """
    minutes = context["free_minutes"]
    options = []

    for occurrence in context["missed"][:2]:
        options.append({
            "title": occurrence["title"],
            "minutes": max(15, occurrence["duration_min"] or 30),
            "why": "you missed it earlier today",
        })

    for name, data in list(context["assemblies"].items())[:2]:
        options.append({
            "title": f"run '{name}'",
            "minutes": 20,
            "why": data.get("purpose") or "one of your saved assemblies",
        })

    if minutes >= 45:
        options.append({"title": "a focused work block", "minutes": 45,
                        "why": "you have a clear stretch"})
    if minutes >= 20:
        options.append({"title": "a walk", "minutes": 20,
                        "why": "short enough to fit before your next thing"})
    options.append({"title": "a break", "minutes": 15, "why": "nothing is urgent"})
    options.append({"title": "plan tomorrow", "minutes": 10,
                    "why": "quick, and it makes the morning easier"})

    # Filter to what fits, but never hand back a list of one — a single option
    # isn't a suggestion, it's an instruction.
    budget = minutes if minutes > 0 else 30
    fits = [o for o in options if o["minutes"] <= budget]
    if len(fits) < 3:
        fits += [o for o in options if o not in fits]
    return fits[:3]


@tool(
    name="suggest_activity",
    description=(
        "Suggest what the user could do with the free time they have right now, "
        "based on their schedule, what they missed today, and the routines they've "
        "built. Use when the user says 'I have some time', 'what should I do now', "
        "'I skipped the gym, what can I do instead', 'any ideas', 'I'm free'. "
        "Returns a numbered list they can pick from."
    ),
    parameters={"context": "string"},
    required=[],
)
def handle_suggest_activity(action_data):
    global _PENDING_SUGGESTIONS

    extra = str(action_data.get("context", "") or "").strip()
    context = build_suggestion_context()
    now = context["now"]
    minutes = context["free_minutes"]

    if minutes <= 0:
        busy = [o for o in store.day_agenda(now.date())
                if o["start"] <= now < o["end"]]
        if busy:
            return (f"You're in the middle of {busy[0]['title']} right now — "
                    "want me to look at what's after it?")

    options = _fallback_suggestions(context)

    # Ask the model to sharpen the list, grounded in the same facts. If it
    # can't, the deterministic list still stands rather than nothing.
    try:
        from core.llm import generate
        missed_text = ", ".join(o["title"] for o in context["missed"]) or "nothing"
        next_text = ", ".join(
            f"{o['title']} at {o['start'].strftime('%I:%M %p').lstrip('0').lower()}"
            for o in context["upcoming"]) or "nothing else today"
        assembly_text = ", ".join(context["assemblies"].keys()) or "none saved"

        prompt = (
            "You are Helio, a desktop assistant, suggesting what the user could do "
            "with the free time they have right now. Be concrete and brief.\n\n"
            f"Current time: {now.strftime('%A %d %B, %I:%M %p')}\n"
            f"Free for: {minutes} minutes\n"
            f"Missed earlier today: {missed_text}\n"
            f"Later today: {next_text}\n"
            f"Saved routines Helio can run: {assembly_text}\n"
            + (f"The user also said: {extra}\n" if extra else "")
            + "\nGive exactly 3 options. One line each, in this format:\n"
            "<what to do> | <minutes> | <one short reason>\n"
            "No numbering, no extra text. Prefer things that fit the free time, "
            "and prefer picking up something they missed."
        )
        raw = generate(prompt, role="chat") or ""
        if not raw.startswith("LLM Error:"):
            parsed = []
            for line in raw.splitlines():
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3 and parts[0]:
                    try:
                        span = int("".join(c for c in parts[1] if c.isdigit()) or 30)
                    except ValueError:
                        span = 30
                    parsed.append({"title": parts[0].lstrip("-•* ").strip(),
                                   "minutes": span, "why": parts[2]})
            if parsed:
                options = parsed[:3]
    except Exception:
        pass

    _PENDING_SUGGESTIONS = options

    span_text = (f"You have about {minutes} minutes free"
                 if minutes else "You're free right now")
    lines = "\n".join(
        f"  {i}. {o['title']} ({o['minutes']} min) — {o['why']}"
        for i, o in enumerate(options, 1))
    return (f"{span_text}. Here's what I'd do:\n{lines}\n\n"
            "Say the number and I'll put it on your schedule.")


@tool(
    name="accept_suggestion",
    description=(
        "Take one of the options Helio just suggested and put it on the schedule. "
        "Use when the user picks one — 'the second one', 'number 1', 'do that', "
        "'yes the walk'. 'choice' is the number or part of the option's text."
    ),
    parameters={"choice": "string"},
    required=["choice"],
)
def handle_accept_suggestion(action_data):
    global _PENDING_SUGGESTIONS

    if not _PENDING_SUGGESTIONS:
        return "I haven't suggested anything yet — ask me what you could do."

    choice = str(action_data.get("choice", "") or "").strip().lower()
    ordinals = {"first": 0, "second": 1, "third": 2, "one": 0, "two": 1, "three": 2}

    picked = None
    digits = "".join(c for c in choice if c.isdigit())
    if digits:
        index = int(digits) - 1
        if 0 <= index < len(_PENDING_SUGGESTIONS):
            picked = _PENDING_SUGGESTIONS[index]
    if picked is None:
        for word, index in ordinals.items():
            if word in choice and index < len(_PENDING_SUGGESTIONS):
                picked = _PENDING_SUGGESTIONS[index]
                break
    if picked is None and choice:
        for option in _PENDING_SUGGESTIONS:
            if choice in option["title"].lower():
                picked = option
                break
    if picked is None and choice in ("that", "yes", "yeah", "do it", "sure"):
        picked = _PENDING_SUGGESTIONS[0]

    if picked is None:
        return "Which one? Say the number."

    start = datetime.now().replace(second=0, microsecond=0)
    store.add_event(picked["title"], start.timestamp(),
                    kind=_infer_kind(picked["title"]),
                    duration_min=picked["minutes"], recurrence="none")
    _PENDING_SUGGESTIONS = []

    end = start + timedelta(minutes=picked["minutes"])
    return (f"Done — '{picked['title']}' is on your schedule until "
            f"{end.strftime('%I:%M %p').lstrip('0').lower()}.")
