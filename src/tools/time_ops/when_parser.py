"""
Natural-language time parsing for reminders and timers.

Deliberately regex-only. A dedicated library (dateparser, parsedatetime) would
pull in a large dependency to handle phrasings nobody speaks to a desktop
assistant, and Helio ships with no date library today. This covers the forms
people actually say out loud, and returns None on anything else so the caller
can ask instead of firing at a wrong time.
"""
import re
from datetime import datetime, timedelta

_WORD_NUMBERS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
    "forty five": 45, "fortyfive": 45, "forty-five": 45, "fifty": 50, "sixty": 60,
    "ninety": 90, "couple": 2, "few": 3, "half": 0.5,
}

_UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400,
}

_UNIT_LABEL = {1: "second", 60: "minute", 3600: "hour", 86400: "day"}

_NUM_PATTERN = r"(\d+(?:\.\d+)?|" + "|".join(
    sorted((re.escape(w) for w in _WORD_NUMBERS), key=len, reverse=True)
) + r")"
_UNIT_PATTERN = "|".join(sorted(_UNIT_SECONDS, key=len, reverse=True))


def _to_number(token: str):
    token = token.strip().lower()
    try:
        return float(token)
    except ValueError:
        return _WORD_NUMBERS.get(token)


def _plural(value: float, noun: str) -> str:
    shown = int(value) if float(value).is_integer() else value
    return f"{shown} {noun}" + ("" if shown == 1 else "s")


def _relative(text: str, now: datetime):
    """'in 20 minutes', 'in an hour and a half', 'for 5 mins', '20 minutes'."""
    # "half an hour" / "an hour and a half" need their own pass — the generic
    # number+unit rule would read "half" as the count and drop the "and a half".
    if re.search(r"\bhalf an hour\b", text):
        return now + timedelta(minutes=30), "30 minutes"
    m = re.search(r"\b(?:an?|1|one)\s+hour\s+and\s+a\s+half\b", text)
    if m:
        return now + timedelta(minutes=90), "1 hour 30 minutes"

    matches = list(
        re.finditer(rf"\b{_NUM_PATTERN}\s*(?:of\s+)?({_UNIT_PATTERN})\b", text)
    )
    if not matches:
        return None

    total = 0.0
    parts = []
    for m in matches:
        count = _to_number(m.group(1))
        if count is None:
            continue
        unit_seconds = _UNIT_SECONDS[m.group(2)]
        total += count * unit_seconds
        parts.append(_plural(count, _UNIT_LABEL[unit_seconds]))

    if total <= 0:
        return None
    return now + timedelta(seconds=total), " ".join(parts)


def _absolute(text: str, now: datetime):
    """'at 5', 'at 5pm', 'at 17:30', '9:30', 'tomorrow at 9am', 'tonight at 8'."""
    m = re.search(
        r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b", text
    )
    if not m:
        return None

    meridiem = (m.group(3) or "").replace(".", "")
    # A bare number with no ':' and no am/pm isn't a time — "remind me 5" is
    # ambiguous enough that guessing 5 o'clock would be worse than asking.
    if not m.group(2) and not meridiem and not re.search(r"\bat\s+\d", text):
        return None

    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    if hour > 23 or minute > 59:
        return None

    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif not meridiem and hour < 8:
        # "at 5" almost always means this afternoon, not 5 in the morning.
        hour += 12

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if re.search(r"\btomorrow\b", text):
        target += timedelta(days=1)
    elif target <= now:
        target += timedelta(days=1)

    try:
        label = target.strftime("%I:%M %p").lstrip("0").lower()
    except ValueError:
        label = f"{hour:02d}:{minute:02d}"
    if target.date() != now.date():
        label += " tomorrow"
    return target, label


def parse_when(text: str, now: datetime = None):
    """
    Parse a spoken time expression.

    Returns (datetime, human_label) or None when the phrase carries no usable
    time — the caller should ask rather than assume.
    """
    if not text:
        return None

    now = now or datetime.now()
    cleaned = str(text).strip().lower()

    result = _relative(cleaned, now)
    if result:
        return result

    return _absolute(cleaned, now)


# ── calendar dates ───────────────────────────────────────────────────────────
# parse_when covers durations and clock times, which is all a timer needs. A
# schedule also needs actual dates — "on March 12", "next Monday", "the 14th" —
# so those live here and reuse the clock-time pass above for the time of day.

_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}

_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6,
}

_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))
_WEEKDAY_PATTERN = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))

# "at 7", "7pm", "19:30" — the time half of a date expression.
_CLOCK = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)\b"
    r"|\bat\s+(\d{1,2})(?::(\d{2}))?\b"
    r"|\b(\d{1,2}):(\d{2})\b"
)


def _clock_of(text, default_hour=9):
    """Pull the time of day out of a date phrase. Returns (hour, minute, found)."""
    evening = bool(re.search(r"\btonight\b|\bevening\b|\bat night\b", text))
    m = _CLOCK.search(text)
    if not m:
        return default_hour, 0, False

    if m.group(1) is not None:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        meridiem = (m.group(3) or "").replace(".", "")
    elif m.group(4) is not None:
        hour, minute, meridiem = int(m.group(4)), int(m.group(5) or 0), ""
    else:
        hour, minute, meridiem = int(m.group(6)), int(m.group(7)), ""

    if hour > 23 or minute > 59:
        return default_hour, 0, False
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif not meridiem and (hour < 8 or (evening and hour < 12)):
        # "at 5" on a calendar entry means the afternoon, same as for timers —
        # and "tonight at 8" is 20:00, which the bare-hour rule alone misses.
        hour += 12
    return hour, minute, True


def _date_of(text, now):
    """Find the calendar day a phrase refers to. Returns a date, or None."""
    today = now.date()

    if re.search(r"\btoday\b|\btonight\b|\bthis (?:morning|afternoon|evening)\b", text):
        return today
    # "day after tomorrow" has to be tested before the bare "tomorrow" rule,
    # or the substring match steals it.
    if re.search(r"\bday after tomorrow\b", text):
        return today + timedelta(days=2)
    if re.search(r"\btomorrow\b|\btmrw\b", text):
        return today + timedelta(days=1)

    # "March 12", "12 March", "March 12th"
    m = re.search(rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", text)
    if not m:
        m2 = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_PATTERN})\b", text)
        if m2:
            day, month = int(m2.group(1)), _MONTHS[m2.group(2)]
            m = True
        else:
            m = None
    else:
        month, day = _MONTHS[m.group(1)], int(m.group(2))

    if m:
        year = now.year
        try:
            candidate = datetime(year, month, day).date()
        except ValueError:
            return None
        # A bare month/day that has already passed means next year — which is
        # exactly right for birthdays, the main reason this branch exists.
        if candidate < today:
            try:
                candidate = datetime(year + 1, month, day).date()
            except ValueError:
                return None
        return candidate

    # "next Monday", "on Friday", "this Saturday"
    m = re.search(rf"\b(next\s+|this\s+|on\s+|coming\s+)?({_WEEKDAY_PATTERN})\b", text)
    if m:
        target = _WEEKDAYS[m.group(2)]
        ahead = (target - today.weekday()) % 7
        if ahead == 0:
            ahead = 7                      # "on Monday" said on a Monday means next one
        if (m.group(1) or "").strip() == "next" and ahead < 7:
            ahead += 0                     # "next Friday" == the coming Friday
        return today + timedelta(days=ahead)

    # "on the 14th"
    m = re.search(r"\bon the (\d{1,2})(?:st|nd|rd|th)\b", text)
    if m:
        day = int(m.group(1))
        year, month = now.year, now.month
        try:
            candidate = datetime(year, month, day).date()
        except ValueError:
            return None
        if candidate < today:
            month += 1
            if month > 12:
                month, year = 1, year + 1
            try:
                candidate = datetime(year, month, day).date()
            except ValueError:
                return None
        return candidate

    return None


def parse_datetime(text: str, now: datetime = None, default_hour: int = 9):
    """
    Parse a scheduling phrase into an absolute moment.

    Returns (datetime, label, had_explicit_time) or None. Unlike parse_when,
    this understands calendar dates, so it can place a birthday in March or a
    meeting next Friday — and it falls back to parse_when so "in 2 hours" and
    "at 5pm" keep working through the same entry point.
    """
    if not text:
        return None

    now = now or datetime.now()
    cleaned = str(text).strip().lower()

    day = _date_of(cleaned, now)
    if day is not None:
        hour, minute, had_time = _clock_of(cleaned, default_hour)
        target = datetime(day.year, day.month, day.day, hour, minute)
        label = target.strftime("%a %d %b")
        if had_time:
            label += target.strftime(" at %I:%M %p").replace(" 0", " ").lower()
        return target, label, had_time

    fallback = parse_when(cleaned, now)
    if fallback:
        return fallback[0], fallback[1], True
    return None
