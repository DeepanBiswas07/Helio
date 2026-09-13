"""
Watches the schedule and speaks up before things happen.

Same shape as ReminderService, and for the same reason: the tools that write
the schedule run on AgentThread, but Kokoro lives on the GUI thread, so
something on this side has to notice and announce.

Three things it does that a plain reminder can't:
  - leads an event rather than firing on it ("your standup starts in 10 minutes")
  - opens the day once, the first time you're at the machine after DAY_BRIEF_HOUR
  - notices a commitment you've walked past and says so once, rather than
    letting it slide silently
"""
import time
from datetime import datetime, date

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

try:
    from memory import schedule_store as store
except ImportError:   # src/ not on the path — degrade quietly, don't crash the UI
    store = None

TICK_MS = 20_000          # the schedule moves in minutes, not seconds
LEAD_MINUTES = 10         # how far ahead of an event to speak
DAY_BRIEF_HOUR = 8        # earliest hour the morning brief may fire
MISS_GRACE_MINUTES = 15   # how long past its end before something counts as missed


class ScheduleService(QObject):
    """Emits announce(text) on the GUI thread when the schedule needs a voice."""

    announce = pyqtSignal(str)
    store_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_mtime = -1.0
        self._briefed_on = None       # date the morning brief was given
        self._missed_said = set()     # (event id, day key) already mentioned

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    def start(self):
        if store is None:
            print("[Schedule] Store unavailable — schedule announcements disabled.")
            return
        self._timer.start()
        today = self.today()
        if today:
            print(f"[Schedule] {len(today)} item(s) on today's schedule.")
        # Give the day's brief shortly after boot rather than on the first tick,
        # so opening Helio in the morning greets you with the day.
        QTimer.singleShot(6000, self._maybe_brief)

    def stop(self):
        self._timer.stop()

    # ── reads for the UI ──────────────────────────────────────────────────
    def today(self):
        if store is None:
            return []
        try:
            return store.day_agenda()
        except Exception:
            return []

    def next_up(self):
        if store is None:
            return None
        try:
            ahead = store.upcoming(limit=1)
            return ahead[0] if ahead else None
        except Exception:
            return None

    # ── the tick ──────────────────────────────────────────────────────────
    def _tick(self):
        if store is None:
            return
        try:
            mtime = store.schedule_mtime()
        except Exception:
            return

        if mtime != self._last_mtime:
            self._last_mtime = mtime
            self.store_changed.emit()

        now = datetime.now()
        self._maybe_brief(now)
        self._announce_upcoming(now)
        self._announce_missed(now)

    def _announce_upcoming(self, now=None):
        now = now or datetime.now()
        try:
            due = store.due_announcements(now, LEAD_MINUTES)
        except Exception as e:
            print(f"[Schedule] Could not read the schedule: {e}")
            return

        for occurrence in due:
            minutes = int((occurrence["start"] - now).total_seconds() // 60)
            title = occurrence["title"]

            if occurrence["duration_min"] <= 0:
                # An all-day marker (a birthday) is news, not a countdown.
                text = f"Today is {title}."
            elif minutes <= 0:
                text = f"{title} starts now."
            elif minutes == 1:
                text = f"{title} starts in a minute."
            else:
                text = f"{title} starts in {minutes} minutes."

            store.mark_announced(occurrence["id"], occurrence["start"].date())
            print(f"[Schedule] Announcing: {text}")
            self.announce.emit(text)

    def _announce_missed(self, now=None):
        """
        Mention something walked past exactly once — and offer the way out,
        since a schedule that only nags is worse than no schedule.
        """
        now = now or datetime.now()
        try:
            missed = store.missed_today(now)
        except Exception:
            return

        for occurrence in missed:
            key = (occurrence["id"], occurrence["day_key"])
            if key in self._missed_said:
                continue
            gone = (now - occurrence["end"]).total_seconds() / 60
            if gone < MISS_GRACE_MINUTES or gone > 120:
                continue
            self._missed_said.add(key)
            self.announce.emit(
                f"You've gone past {occurrence['title']}. "
                "Say 'what can I do' and I'll find you a slot."
            )

    def _maybe_brief(self, now=None):
        """One opening summary a day, the first time you're here after 8am."""
        if store is None:
            return
        now = now or datetime.now()
        if now.hour < DAY_BRIEF_HOUR or self._briefed_on == now.date():
            return
        self._briefed_on = now.date()

        try:
            items = [o for o in store.day_agenda(now.date())
                     if o["status"] == "pending" and o["end"] >= now]
        except Exception:
            return
        if not items:
            return

        first = items[0]
        clock = first["start"].strftime("%I:%M %p").lstrip("0").lower()
        count = len(items)
        birthdays = [o["title"] for o in items if o["kind"] == "birthday"]

        text = ("You have {} thing{} left today. First up, {} at {}."
                .format(count, "" if count == 1 else "s", first["title"], clock))
        if birthdays:
            text += " Also, today is " + " and ".join(birthdays) + "."
        self.announce.emit(text)
