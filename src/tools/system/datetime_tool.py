"""
datetime_tool.py — Gives Helio awareness of the current date and time.
Auto-discovered by tool_registry.auto_discover().
"""
from datetime import datetime
from tools.tool_registry import tool


def _get_phase(now):
    hour = now.hour
    if 4 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    else:
        return "night"


@tool(
    name="get_datetime",
    description=(
        "Get the current local date, time, day of the week, or phase of the day (morning/evening/night). "
        "Specify 'query' parameter with 'time', 'date', 'day', or 'phase'."
    ),
    parameters={"query": "string"},
    required=[]
)
def handle_get_datetime(action_data):
    now = datetime.now()
    q = str(action_data.get("query", "")).lower().strip()
    phase = _get_phase(now)
    time_str = now.strftime("%I:%M %p").lstrip("0")
    
    # 0. Greetings ("good morning", "good evening", "hello", etc.)
    if "greeting" in q:
        greeting_word = "Good morning" if phase == "morning" else (
            "Good afternoon" if phase == "afternoon" else (
                "Good evening" if phase == "evening" else "Hello"
            )
        )
        if "night" in phase:
            return f"Good evening! It's currently {time_str} on {now.strftime('%A')} night. How can I help you?"
        return f"{greeting_word}! It's {time_str} on {now.strftime('%A')}. How can I assist you?"

    # 1. Phase query ("is it morning or night", "what phase of day")
    if "phase" in q or "morning" in q or "night" in q or "evening" in q or "afternoon" in q:
        return f"It's currently {time_str}, so it is {phase} on {now.strftime('%A')}."

    # 2. Time only (e.g. "It's 3:25 PM")
    if "time" in q or "clock" in q or "hour" in q:
        return f"It's {time_str}"
        
    # 3. Date only (e.g. "September 07, 2026")
    if "date" in q or "month" in q or "year" in q or "today" in q:
        return now.strftime("%B %d, %Y")
        
    # 4. Day only (e.g. "Monday")
    if "day" in q or "week" in q or "weekday" in q:
        return now.strftime("%A")
        
    # 5. Default / All Details
    return (
        f"{now.strftime('%A, %B %d, %Y')} — "
        f"{time_str} ({phase})"
    )

