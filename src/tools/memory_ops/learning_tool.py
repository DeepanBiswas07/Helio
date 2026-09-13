"""
learning_tool.py — reading back, and correcting, what Helio worked out on its own.

An assistant that quietly builds a picture of you and never shows it is not
memory, it's surveillance. These tools exist so the inferences are askable,
arguable and deletable by voice — the same way you'd correct a person who had
got the wrong idea about you.
"""
from datetime import datetime

from tools.tool_registry import tool
from memory import observed_memory as observed
from memory import observation_log as log
from memory import pattern_miner


def _age(record):
    seen = record.get("last_seen", 0)
    if not seen:
        return ""
    days = (datetime.now() - datetime.fromtimestamp(seen)).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago"


@tool(
    name="what_have_you_learned",
    description=(
        "Report the patterns Helio has noticed about the user on its own — habits, "
        "routines, preferences it picked up rather than being told. "
        "Use for 'what have you learned about me', 'what have you noticed', "
        "'what do you know about my habits', 'what patterns have you seen'."
    ),
    parameters={},
    required=[],
)
def handle_what_have_you_learned(action_data):
    records = observed.list_observations()

    if not records:
        state = pattern_miner.readiness()
        if not state["ready"]:
            return ("I haven't noticed anything yet — I need more to go on. "
                    "I've seen {} interactions over {} days; I start looking for "
                    "patterns at {} over {} days.".format(
                        state["entries"], state["days"],
                        state["needed_entries"], state["needed_days"]))
        return "I've been watching but nothing has repeated often enough to count yet."

    lines = []
    for index, record in enumerate(records, 1):
        confidence = int(record.get("confidence", 0) * 100)
        lines.append("  {}. {}  ({}% sure — {}, last {})".format(
            index, record["fact"], confidence,
            record.get("evidence", "no detail"), _age(record)))

    return ("Here's what I've worked out on my own. Tell me if any of it is "
            "wrong and I'll drop it.\n" + "\n".join(lines))


@tool(
    name="forget_observation",
    description=(
        "Tell Helio one of its own inferences is wrong, so it stops believing it "
        "and won't re-learn it. "
        "Use for 'that's not true', 'forget that you noticed X', 'I don't do that', "
        "'stop thinking I X'. "
        "'which' is the number from the list or part of what it said."
    ),
    parameters={"which": "string"},
    required=["which"],
)
def handle_forget_observation(action_data):
    which = str(action_data.get("which", "") or "").strip().lower()
    records = observed.list_observations()

    if not records:
        # No inferences to drop — but "forget that I work at Acme" arrives here
        # too, and that's a stated fact. Try that store before giving up, or an
        # empty inference store would swallow every deletion request.
        if which:
            try:
                from memory.semantic_memory import forget_memory
                removed = forget_memory(which)
                if removed:
                    return "Forgotten: " + removed[0]["text"]
            except Exception:
                pass
        return "I haven't noticed anything about you yet, and I couldn't find that in what you've told me."

    if which in ("all", "everything"):
        count = 0
        for record in records:
            if observed.dismiss(record["id"]):
                count += 1
        return f"Dropped all {count}. I won't re-learn them."

    target = None
    digits = "".join(c for c in which if c.isdigit())
    if digits:
        index = int(digits) - 1
        if 0 <= index < len(records):
            target = records[index]

    if target is None and which:
        for record in records:
            if which in record["fact"].lower():
                target = record
                break
    if target is None and which:
        try:
            from rapidfuzz import process, fuzz
            texts = {r["fact"]: r for r in records}
            matches = process.extract(which, texts.keys(),
                                      scorer=fuzz.token_set_ratio, limit=1)
            if matches and matches[0][1] >= 55:
                target = texts[matches[0][0]]
        except ImportError:
            pass

    if target is None:
        # "forget that I work at Acme" and "forget that I open Chrome each
        # morning" are the same sentence to a router, but one is a fact the
        # user stated and the other is something Helio inferred. Rather than
        # make the router guess, try the other store before giving up.
        try:
            from memory.semantic_memory import forget_memory
            removed = forget_memory(which)
            if removed:
                return "Forgotten: " + removed[0]["text"]
        except Exception:
            pass
        return f"I couldn't tell which one you meant by '{which}'."

    observed.dismiss(target["id"])
    return f"Dropped it — I won't assume that again. ({target['fact']})"


@tool(
    name="confirm_observation",
    description=(
        "Agree with something Helio noticed, promoting it from a guess into "
        "proper memory. Use for 'yes that's right', 'that's true', "
        "'you're right about X'. 'which' is the number or part of what it said."
    ),
    parameters={"which": "string"},
    required=["which"],
)
def handle_confirm_observation(action_data):
    which = str(action_data.get("which", "") or "").strip().lower()
    records = observed.list_observations()
    if not records:
        return "I haven't noticed anything to confirm."

    target = None
    digits = "".join(c for c in which if c.isdigit())
    if digits:
        index = int(digits) - 1
        if 0 <= index < len(records):
            target = records[index]
    if target is None:
        for record in records:
            if which and which in record["fact"].lower():
                target = record
                break

    if target is None:
        return f"Which one? I couldn't match '{which}'."

    observed.confirm(target["id"])
    return f"Noted as fact rather than a guess: {target['fact']}"


@tool(
    name="forget_my_activity",
    description=(
        "Erase the behaviour log Helio uses to spot patterns — every record of "
        "what was asked and when. Use for 'forget what I've been doing', "
        "'clear my activity', 'stop tracking me', 'delete my history'. "
        "Does not touch facts the user explicitly asked Helio to remember."
    ),
    parameters={},
    required=[],
)
def handle_forget_my_activity(action_data):
    entries = log.clear()
    dropped = observed.clear_all()
    return ("Cleared {} activity records and {} things I'd inferred. What you "
            "explicitly told me to remember is untouched.".format(entries, dropped))
