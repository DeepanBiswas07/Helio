"""
Reminders and timers.

These tools only *record* a reminder. Speaking it is the UI's job — tools run on
the agent's background thread and Kokoro lives on the GUI thread, so
ui/services/reminder_service.py polls the store and announces what comes due.
"""
import time
from datetime import datetime

from tools.tool_registry import tool
from tools.time_ops.when_parser import parse_when
from memory.semantic_memory import (
    add_reminder,
    list_reminders,
    remove_reminder,
)

_FILLER_PREFIXES = (
    "me to ", "me that ", "me ", "to ", "that ", "about ",
)


def _clean_message(message: str) -> str:
    """Strip the leading scraps of 'remind me to ...' that survive extraction."""
    text = (message or "").strip().strip('"').strip()
    lowered = text.lower()
    for prefix in _FILLER_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return text


def _relative_label(due_ts: float, now: float = None) -> str:
    """'in 12 minutes', 'in 2 hours', 'at 9:00 am tomorrow'."""
    now = time.time() if now is None else now
    delta = due_ts - now

    if delta < 0:
        return "overdue"
    if delta < 60:
        return f"in {int(delta)} seconds"
    if delta < 3600:
        minutes = int(round(delta / 60))
        return f"in {minutes} minute" + ("" if minutes == 1 else "s")
    if delta < 8 * 3600:
        hours = delta / 3600
        shown = int(hours) if float(hours).is_integer() else round(hours, 1)
        return f"in {shown} hour" + ("" if shown == 1 else "s")

    # Past a working day's distance, "in 23.7 hours" is useless — say the clock
    # time instead, and name the day if it isn't today.
    stamp = datetime.fromtimestamp(due_ts)
    clock = stamp.strftime("%I:%M %p").lstrip("0").lower()
    if stamp.date() == datetime.fromtimestamp(now).date():
        return f"at {clock}"
    return f"at {clock} on {stamp.strftime('%A')}"


@tool(
    name="set_reminder",
    description=(
        "Set a reminder or timer that Helio will announce out loud when it comes due. "
        "Use this whenever the user says 'remind me...', 'set a timer for...', "
        "'wake me in...', or asks to be told something later. "
        "'when' is the raw time phrase exactly as the user said it "
        "(e.g. 'in 20 minutes', 'at 5pm', 'tomorrow at 9am'). "
        "'message' is what to remind them about (e.g. 'check the oven'). "
        "For a bare timer with no subject, leave 'message' empty."
    ),
    parameters={"when": "string", "message": "string"},
    required=["when"],
)
def handle_set_reminder(action_data):
    when_text = str(action_data.get("when", "") or "").strip()
    message = _clean_message(str(action_data.get("message", "") or ""))

    # The time phrase sometimes arrives buried in the message instead, and
    # vice versa — try both rather than failing on a routing wobble.
    parsed = parse_when(when_text) if when_text else None
    if not parsed and message:
        parsed = parse_when(message)

    if not parsed:
        return "When should I remind you? Say something like 'in 20 minutes' or 'at 5pm'."

    due, label = parsed

    # parse_when returns either a duration ("20 minutes") or a clock time
    # ("5:00 pm") — they need different prepositions to read naturally.
    is_clock_time = ":" in label
    lead = f"at {label}" if is_clock_time else f"in {label}"

    if not message:
        # "5 minutes" -> "your 5 minute timer is up"
        message = f"your {label.rstrip('s') if not is_clock_time else label} timer is up"

    add_reminder(message, due.timestamp())
    return f"Got it — I'll remind you {lead}: {message}"


@tool(
    name="list_reminders",
    description=(
        "List every reminder and timer currently pending. "
        "Use this when the user asks 'what reminders do I have', 'any timers running', "
        "or 'what am I supposed to do later'."
    ),
    parameters={},
    required=[],
)
def handle_list_reminders(action_data):
    reminders = list_reminders()
    if not reminders:
        return "You have no reminders set."

    now = time.time()
    lines = [
        f"  {i + 1}. {r.get('message', '(no message)')} — {_relative_label(r.get('due_ts', 0), now)}"
        for i, r in enumerate(reminders)
    ]
    count = len(reminders)
    header = f"You have {count} reminder" + ("" if count == 1 else "s") + " pending:"
    return header + "\n" + "\n".join(lines)


@tool(
    name="cancel_reminder",
    description=(
        "Cancel a pending reminder or timer. "
        "'which' identifies it — either its position in the list ('the first one', '2') "
        "or part of its text ('the oven one'). "
        "Use 'all' to clear every reminder."
    ),
    parameters={"which": "string"},
    required=["which"],
)
def handle_cancel_reminder(action_data):
    which = str(action_data.get("which", "") or "").strip().lower()
    reminders = list_reminders()

    if not reminders:
        return "You have no reminders to cancel."

    # "cancel my reminder" with nothing to go on: obvious when there's only one,
    # ambiguous otherwise — and clearing everything would be the wrong guess.
    if not which:
        if len(reminders) == 1:
            remove_reminder(reminders[0].get("id"))
            return f"Cancelled: {reminders[0].get('message', 'that reminder')}"
        return (
            f"You have {len(reminders)} reminders — which one should I cancel? "
            "Name it, or say 'cancel all'."
        )

    if which in ("all", "everything", "every one", "them all", "every"):
        for r in reminders:
            remove_reminder(r.get("id"))
        count = len(reminders)
        return f"Cleared all {count} reminder" + ("" if count == 1 else "s") + "."

    target = None

    # Positional: "2", "the first one", "last"
    digits = "".join(c for c in which if c.isdigit())
    ordinals = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4}
    if digits:
        index = int(digits) - 1
        if 0 <= index < len(reminders):
            target = reminders[index]
    elif "last" in which:
        target = reminders[-1]
    else:
        for word, index in ordinals.items():
            if word in which and index < len(reminders):
                target = reminders[index]
                break

    # Otherwise match on the reminder text, the same fuzzy approach
    # handle_run_workflow uses for workflow names.
    if target is None and which:
        from rapidfuzz import process, fuzz
        texts = {r.get("message", ""): r for r in reminders}
        matches = process.extract(which, texts.keys(), scorer=fuzz.token_set_ratio, limit=1)
        if matches and matches[0][1] >= 60:
            target = texts[matches[0][0]]

    if target is None:
        return f"I couldn't find a reminder matching '{which}'. Ask me to list your reminders."

    remove_reminder(target.get("id"))
    return f"Cancelled: {target.get('message', 'that reminder')}"
