"""
Watches the reminder store and announces what comes due.

Tools run on AgentThread; HelioTTS lives on the GUI thread. So set_reminder only
writes to disk, and this service — which lives on the GUI thread — is what turns
a due reminder into speech.

Polling a file beats holding a QTimer per reminder: reminders persist across
restarts, so a countdown living only in memory would be lost on every relaunch.
The tick is nearly free because the file is only re-read when its mtime moves.
"""
import time

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

try:
    from memory.semantic_memory import (
        pop_due_reminders,
        list_reminders,
        reminders_mtime,
    )
except ImportError:  # src/ not on the path — degrade quietly, don't crash the UI
    pop_due_reminders = None
    list_reminders = None
    reminders_mtime = None

TICK_MS = 1000


class ReminderService(QObject):
    """Emits reminder_due(message) on the GUI thread when a reminder comes due."""

    reminder_due = pyqtSignal(str)
    store_changed = pyqtSignal()   # a reminder was added, fired or cancelled

    def __init__(self, parent=None):
        super().__init__(parent)
        self._last_mtime = -1.0
        self._next_due = None

        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    def start(self):
        if pop_due_reminders is None:
            print("[Reminders] Store unavailable — reminder announcements disabled.")
            return
        self._timer.start()
        pending = self.pending()
        if pending:
            print(f"[Reminders] {len(pending)} reminder(s) restored from disk.")

    def stop(self):
        self._timer.stop()

    def pending(self) -> list:
        """Pending reminders, soonest first. Safe to call from the GUI thread."""
        if list_reminders is None:
            return []
        try:
            return list_reminders()
        except Exception:
            return []

    def _tick(self):
        try:
            mtime = reminders_mtime()
        except Exception:
            return

        # Re-read the store only when something has written to it. Between
        # writes the tick costs a single stat() plus a float comparison.
        if mtime != self._last_mtime:
            self._last_mtime = mtime
            self._refresh_next_due()
            self.store_changed.emit()

        if self._next_due is None or time.time() < self._next_due:
            return

        try:
            due = pop_due_reminders()
        except Exception as e:
            print(f"[Reminders] Could not read the reminder store: {e}")
            self._next_due = None
            return

        self._last_mtime = reminders_mtime()
        self._refresh_next_due()
        self.store_changed.emit()

        for record in due:
            message = (record.get("message") or "").strip()
            if message:
                print(f"[Reminders] Due: {message}")
                self.reminder_due.emit(message)

    def _refresh_next_due(self):
        """Cache the soonest pending due time so idle ticks touch no I/O."""
        pending = self.pending()
        self._next_due = pending[0].get("due_ts") if pending else None
