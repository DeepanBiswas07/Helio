"""
memory_edit_tool.py — correcting what Helio remembers, out loud.

remember_fact already replaces a fact that a new statement makes untrue, so the
common case needs no tool at all: saying "my meeting moved to 6" is enough. These
cover the cases it can't infer — deleting something outright, and asking what a
fact used to say when Helio has quietly updated it under you.
"""
from datetime import datetime

from tools.tool_registry import tool
from memory.semantic_memory import (
    forget_memory, memory_history, sweep_expired_memories, get_memory_records,
)


@tool(
    name="forget_fact",
    description=(
        "Delete something Helio remembers about the user, permanently. "
        "Use for 'forget that I...', 'delete what you know about X', "
        "'that's wrong, remove it', 'I don't work there any more — forget it'. "
        "'what' is the fact or part of it. This is for facts the user told "
        "Helio; use forget_observation for patterns Helio noticed itself."
    ),
    parameters={"what": "string"},
    required=["what"],
)
def handle_forget_fact(action_data):
    needle = str(action_data.get("what", "") or "").strip()
    if not needle:
        return "What should I forget?"

    removed = forget_memory(needle)
    if not removed:
        # Mirror of the fallback in learning_tool: the phrasing for deleting a
        # stated fact and dismissing an inference is identical, so whichever
        # door the router picked, both stores get looked in.
        try:
            from memory import observed_memory
            for record in observed_memory.list_observations():
                if needle.lower() in record["fact"].lower():
                    observed_memory.dismiss(record["id"])
                    return ("Dropped it — that was something I'd inferred, not "
                            f"something you told me. ({record['fact']})")
        except Exception:
            pass
        return f"I couldn't find anything about '{needle}' in what I remember."

    if len(removed) == 1:
        return f"Forgotten: {removed[0]['text']}"
    lines = "\n".join(f"  - {r['text']}" for r in removed)
    return f"Forgotten {len(removed)} things:\n{lines}"


@tool(
    name="what_did_you_used_to_think",
    description=(
        "Show what a remembered fact said before Helio updated it. "
        "Use for 'what did I say before', 'what was it before you changed it', "
        "'show me the old version', 'has that changed'. "
        "'what' optionally narrows it to one subject."
    ),
    parameters={"what": "string"},
    required=[],
)
def handle_memory_history(action_data):
    needle = str(action_data.get("what", "") or "").strip()
    entries = memory_history(needle)

    if not entries:
        if needle:
            return f"Nothing about '{needle}' has changed since you told me."
        return "Nothing I remember has been updated yet."

    lines = []
    for entry in entries[:8]:
        when = ""
        if entry.get("changed"):
            when = datetime.fromtimestamp(entry["changed"]).strftime(" on %d %b")
        lines.append(f"  - now: {entry['now']}\n    was: {entry['was']}{when}")
    return "Here's what changed:\n" + "\n".join(lines)


@tool(
    name="check_expired_memories",
    description=(
        "Retire anything Helio remembers whose end date has passed — an "
        "internship that finished, a trip that's over, a membership that "
        "lapsed. Use for 'is anything you know out of date', 'check your "
        "memory', 'anything expired'. Runs automatically too."
    ),
    parameters={},
    required=[],
)
def handle_check_expired(action_data):
    changed = sweep_expired_memories()

    if not changed:
        pending = [r for r in get_memory_records() if r.get("valid_until")]
        if pending:
            soonest = min(pending, key=lambda r: r["valid_until"])
            when = datetime.fromtimestamp(soonest["valid_until"]).strftime("%d %B")
            return ("Nothing has gone out of date. The next thing to lapse is "
                    f"'{soonest['text']}' on {when}.")
        return "Nothing I remember has an end date, so nothing can go stale."

    lines = "\n".join(f"  - {r['text']}" for r in changed)
    return (f"Updated {len(changed)} thing" + ("" if len(changed) == 1 else "s")
            + f" that had passed:\n{lines}")
