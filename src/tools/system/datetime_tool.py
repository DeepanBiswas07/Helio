"""
datetime_tool.py — Gives Helio awareness of the current date and time.
Auto-discovered by tool_registry.auto_discover().
"""
from datetime import datetime
from tools.tool_registry import tool


@tool(
    name="get_datetime",
    description=(
        "Get the current local date, time, or day of the week. "
        "Specify 'query' parameter with 'time', 'date', or 'day' depending on what is asked."
    ),
    parameters={"query": "string"},
    required=[]
)
def handle_get_datetime(action_data):
    now = datetime.now()
    q = str(action_data.get("query", "")).lower().strip()
    
    # Check what specifically the user is asking for
    is_time = False
    is_date = False
    is_day = False
    
    if "time" in q or "clock" in q or "hour" in q:
        is_time = True
    elif "date" in q or "month" in q or "year" in q or "today" in q:
        is_date = True
    elif "day" in q or "week" in q or "weekday" in q:
        is_day = True
    else:
        # If the query is completely empty or general, return all details
        is_time = True
        is_date = True
        is_day = True

    # 1. Time only (Premium hearable voice style, e.g. "It's 3:25 PM")
    if is_time and not is_date and not is_day:
        return f"It's {now.strftime('%I:%M %p')}"
        
    # 2. Date only (e.g. "May 21, 2026")
    if is_date and not is_time and not is_day:
        return now.strftime("%B %d, %Y")
        
    # 3. Day only (e.g. "Thursday")
    if is_day and not is_time and not is_date:
        return now.strftime("%A")
        
    # 4. Default / All Details
    return (
        f"{now.strftime('%A, %B %d %Y')} — "
        f"{now.strftime('%I:%M %p')}"
    )

