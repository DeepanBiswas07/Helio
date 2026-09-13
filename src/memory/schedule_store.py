"""
schedule_store.py — Helio's calendar.

Kept separate from semantic_memory because a schedule is not a fact store: it
has recurrence, occurrences, completion per day, and a window query. Mixing
that into the flat key/value memory would make both harder to reason about.

An *event* is what you told Helio ("gym at 7 every weekday"). An *occurrence*
is one instance of it on one day. Recurrence is expanded on read rather than
written out ahead of time, so a birthday costs one record forever and editing
the event fixes every future instance at once.

Stored in data/memory/schedule.json:

  [
    {
      "id": "a1b2c3",
      "title": "gym",
      "kind": "workout",
      "start_ts": 1770000000.0,
      "duration_min": 60,
      "recurrence": "weekdays",
      "notes": "",
      "created_ts": 1769000000.0,
      "done_dates": ["2026-09-07"],
      "skipped_dates": [],
      "announced": ["2026-09-07"]
    }
  ]
"""
import json
import time
import uuid
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "memory"
_SCHEDULE_PATH = DATA_DIR / "schedule.json"

# What an entry *is*, which drives its colour and how Helio talks about it.
KINDS = ("meeting", "birthday", "workout", "task", "study", "personal", "break")
DEFAULT_KIND = "task"

RECURRENCES = ("none", "daily", "weekdays", "weekly", "monthly", "yearly")

# A day runs from here to here when looking for free time. Suggesting the gym
# at 3am is worse than suggesting nothing.
DAY_START_HOUR = 7
DAY_END_HOUR = 23

DEFAULT_DURATION = {
    "meeting": 60, "birthday": 0, "workout": 60,
    "task": 30, "study": 60, "personal": 30, "break": 15,
}


def _ensure_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load() -> list:
    _ensure_dir()
    if not _SCHEDULE_PATH.exists():
        return []
    try:
        with _SCHEDULE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(events: list):
    _ensure_dir()
    try:
        with _SCHEDULE_PATH.open("w", encoding="utf-8") as f:
            json.dump(events, f, indent=2, ensure_ascii=False)
    except OSError:
        pass


def schedule_mtime() -> float:
    """Modification time of the store, or 0. Lets the UI poll for free."""
    try:
        return _SCHEDULE_PATH.stat().st_mtime
    except OSError:
        return 0.0


def _key(moment) -> str:
    """The per-day key used for done / skipped / announced bookkeeping."""
    if isinstance(moment, datetime):
        moment = moment.date()
    return moment.isoformat()


# ── writing ──────────────────────────────────────────────────────────────────

def add_event(title, start_ts, kind=DEFAULT_KIND, duration_min=None,
              recurrence="none", notes="") -> dict:
    """Store an event. Returns the record."""
    kind = kind if kind in KINDS else DEFAULT_KIND
    recurrence = recurrence if recurrence in RECURRENCES else "none"
    if duration_min is None:
        duration_min = DEFAULT_DURATION.get(kind, 30)

    record = {
        "id": uuid.uuid4().hex[:6],
        "title": (title or "").strip(),
        "kind": kind,
        "start_ts": float(start_ts),
        "duration_min": int(duration_min),
        "recurrence": recurrence,
        "notes": (notes or "").strip(),
        "created_ts": time.time(),
        "done_dates": [],
        "skipped_dates": [],
        "announced": [],
    }
    events = _load()
    events.append(record)
    events.sort(key=lambda e: e.get("start_ts", 0))
    _save(events)
    return record


def list_events() -> list:
    """Every stored event, soonest first. Raw records, not occurrences."""
    return sorted(_load(), key=lambda e: e.get("start_ts", 0))


def get_event(event_id) -> Optional[dict]:
    for event in _load():
        if event.get("id") == event_id:
            return event
    return None


def update_event(event_id, **fields) -> bool:
    events = _load()
    for event in events:
        if event.get("id") == event_id:
            event.update({k: v for k, v in fields.items() if v is not None})
            _save(events)
            return True
    return False


def remove_event(event_id) -> bool:
    events = _load()
    remaining = [e for e in events if e.get("id") != event_id]
    if len(remaining) == len(events):
        return False
    _save(remaining)
    return True


def _flag_day(event_id, field, day) -> bool:
    """Add a day key to one of the per-occurrence lists."""
    events = _load()
    for event in events:
        if event.get("id") == event_id:
            days = event.setdefault(field, [])
            key = _key(day)
            if key not in days:
                days.append(key)
                _save(events)
            return True
    return False


def mark_done(event_id, day=None) -> bool:
    return _flag_day(event_id, "done_dates", day or date.today())


def mark_skipped(event_id, day=None) -> bool:
    return _flag_day(event_id, "skipped_dates", day or date.today())


def mark_announced(event_id, day=None) -> bool:
    return _flag_day(event_id, "announced", day or date.today())


def clear_done(event_id, day=None) -> bool:
    events = _load()
    key = _key(day or date.today())
    for event in events:
        if event.get("id") == event_id:
            for field in ("done_dates", "skipped_dates"):
                if key in event.get(field, []):
                    event[field].remove(key)
            _save(events)
            return True
    return False


# ── recurrence ───────────────────────────────────────────────────────────────

def _starts_between(event, window_start: datetime, window_end: datetime):
    """
    Every start time this event has inside the window.

    Expanding on read is what makes a birthday one record instead of forty.
    """
    first = datetime.fromtimestamp(event.get("start_ts", 0))
    recurrence = event.get("recurrence", "none")

    if recurrence == "none":
        return [first] if window_start <= first < window_end else []

    starts = []
    if recurrence in ("daily", "weekdays", "weekly"):
        step = timedelta(days=7 if recurrence == "weekly" else 1)
        # Jump straight to the window instead of walking from the first
        # occurrence — a daily event created a year ago would be 365 steps.
        cursor = first
        if cursor < window_start:
            gap = (window_start - cursor).total_seconds() / step.total_seconds()
            cursor = first + step * int(gap)
        while cursor < window_end:
            if cursor >= window_start:
                if recurrence != "weekdays" or cursor.weekday() < 5:
                    starts.append(cursor)
            cursor += step
        return starts

    if recurrence == "monthly":
        cursor = first
        while cursor < window_end:
            if cursor >= window_start:
                starts.append(cursor)
            month = cursor.month + 1
            year = cursor.year + (1 if month > 12 else 0)
            month = 1 if month > 12 else month
            try:
                cursor = cursor.replace(year=year, month=month)
            except ValueError:      # e.g. the 31st in a 30-day month
                cursor = cursor.replace(year=year, month=month, day=28)
        return starts

    if recurrence == "yearly":
        for year in range(window_start.year, window_end.year + 1):
            try:
                candidate = first.replace(year=year)
            except ValueError:      # 29 Feb on a non-leap year
                candidate = first.replace(year=year, day=28)
            if window_start <= candidate < window_end:
                starts.append(candidate)
        return starts

    return []


def occurrences(window_start: datetime, window_end: datetime) -> list:
    """
    Every occurrence inside [start, end), soonest first.

    Each is a dict: id, title, kind, start (datetime), end (datetime),
    duration_min, notes, recurrence, day_key, status.
    """
    found = []
    for event in _load():
        for start in _starts_between(event, window_start, window_end):
            day_key = _key(start)
            if day_key in event.get("skipped_dates", []):
                status = "skipped"
            elif day_key in event.get("done_dates", []):
                status = "done"
            else:
                status = "pending"
            found.append({
                "id": event.get("id"),
                "title": event.get("title", ""),
                "kind": event.get("kind", DEFAULT_KIND),
                "start": start,
                "end": start + timedelta(minutes=event.get("duration_min", 30)),
                "duration_min": event.get("duration_min", 30),
                "notes": event.get("notes", ""),
                "recurrence": event.get("recurrence", "none"),
                "day_key": day_key,
                "announced": day_key in event.get("announced", []),
                "status": status,
            })
    found.sort(key=lambda o: o["start"])
    return found


def day_agenda(day=None) -> list:
    """Everything on one calendar day."""
    day = day or date.today()
    start = datetime(day.year, day.month, day.day)
    return occurrences(start, start + timedelta(days=1))


def horizon(days=7, from_day=None) -> list:
    """Everything in the next N days, starting today."""
    from_day = from_day or date.today()
    start = datetime(from_day.year, from_day.month, from_day.day)
    return occurrences(start, start + timedelta(days=days))


# ── reading the day ──────────────────────────────────────────────────────────

def free_slots(day=None, now: datetime = None, min_minutes=20) -> list:
    """
    The gaps between today's commitments, as (start, end) datetimes.

    Only counts time that hasn't already passed — a free slot this morning is
    not something Helio can suggest filling this afternoon.
    """
    day = day or date.today()
    now = now or datetime.now()

    bounds_start = datetime(day.year, day.month, day.day, DAY_START_HOUR)
    bounds_end = datetime(day.year, day.month, day.day, DAY_END_HOUR)
    if day == now.date():
        bounds_start = max(bounds_start, now)
    if bounds_start >= bounds_end:
        return []

    busy = [
        (o["start"], o["end"]) for o in day_agenda(day)
        if o["status"] != "skipped" and o["duration_min"] > 0
    ]
    busy.sort()

    slots = []
    cursor = bounds_start
    for start, end in busy:
        if start > cursor:
            slots.append((cursor, min(start, bounds_end)))
        cursor = max(cursor, end)
        if cursor >= bounds_end:
            break
    if cursor < bounds_end:
        slots.append((cursor, bounds_end))

    return [(a, b) for a, b in slots
            if (b - a).total_seconds() / 60 >= min_minutes]


def current_free_block(now: datetime = None):
    """The free slot you're standing in right now, or None if you're busy."""
    now = now or datetime.now()
    for start, end in free_slots(now.date(), now=now, min_minutes=1):
        if start <= now < end:
            return start, end
    return None


def missed_today(now: datetime = None) -> list:
    """Pending occurrences whose time has already gone by today."""
    now = now or datetime.now()
    return [o for o in day_agenda(now.date())
            if o["status"] == "pending" and o["end"] < now]


def upcoming(now: datetime = None, within_days=7, limit=None) -> list:
    """Pending occurrences still ahead, soonest first."""
    now = now or datetime.now()
    ahead = [o for o in occurrences(now, now + timedelta(days=within_days))
             if o["status"] == "pending"]
    return ahead[:limit] if limit else ahead


def due_announcements(now: datetime = None, lead_minutes=10) -> list:
    """
    Occurrences starting within the lead window that haven't been announced.

    The caller is responsible for calling mark_announced — the store won't
    assume an announcement actually reached the user.
    """
    now = now or datetime.now()
    window_end = now + timedelta(minutes=lead_minutes)
    return [
        o for o in occurrences(now - timedelta(minutes=1), window_end)
        if o["status"] == "pending" and not o["announced"]
    ]
